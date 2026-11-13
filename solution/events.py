from __future__ import annotations

import json
from pathlib import Path

from .normalization import normalize_phone, parse_timestamp
from .security import PIIProtection


def ingest_event_file(db, path: Path, security: PIIProtection, source_path: str) -> None:
    with path.open(encoding="utf-8-sig") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                db.execute("INSERT INTO event_attempts(source_path,line_number,event_id,outcome) VALUES(?,?,NULL,'malformed')",
                           (source_path, line_number))
                continue
            event_id = event.get("event_id") if isinstance(event, dict) else None
            if event_id is None or not str(event_id).strip():
                db.execute("INSERT INTO event_attempts(source_path,line_number,event_id,outcome) VALUES(?,?,NULL,'malformed')",
                           (source_path, line_number))
                continue
            event_id = str(event_id)
            if db.execute("SELECT 1 FROM event_candidates WHERE event_id=?", (event_id,)).fetchone():
                db.execute("INSERT INTO event_attempts(source_path,line_number,event_id,outcome) VALUES(?,?,?,'duplicate')",
                           (source_path, line_number, event_id))
                continue

            # Only fields needed for linking and reporting are persisted. Arbitrary JSON payloads,
            # which may contain PII, are deliberately discarded.
            event_type = str(event.get("event_type") or "")
            workflow_id = str(event.get("workflow_id") or "")
            call_id = str(event.get("call_id") or "")
            timestamp = parse_timestamp(str(event.get("occurred_at") or ""))
            occurred = timestamp.isoformat(timespec="microseconds") if timestamp else None
            phone_hmac = None
            if event.get("phone") is not None:
                normalized_phone = normalize_phone(str(event["phone"]))
                if normalized_phone:
                    phone_hmac = security.fingerprint(normalized_phone)
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            code = payload.get("disposition_code") if isinstance(payload, dict) else None
            code = str(code).strip().lower() if code is not None else None
            payload_cipher = security.encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            db.execute("INSERT INTO event_candidates VALUES(?,?,?,?,?,?,?,?)",
                       (event_id, event_type, workflow_id, call_id, occurred, phone_hmac, code, payload_cipher))
            db.execute("INSERT INTO event_attempts(source_path,line_number,event_id,outcome) VALUES(?,?,?,'candidate')",
                       (source_path, line_number, event_id))


def resolve_events(db, dispositions: dict[str, dict[str, dict[str, bool]]]) -> None:
    """Append newly linkable, catalogue-valid candidates to the immutable ledger."""
    candidates = db.execute("SELECT * FROM event_candidates ORDER BY event_id").fetchall()
    leads = {(row["workflow_id"], row["phone_hmac"]): row["id"]
             for row in db.execute("SELECT id,workflow_id,phone_hmac FROM leads")}
    direct: dict[str, int] = {}
    call_leads: dict[tuple[str, str], int] = {}
    for event in candidates:
        if not event["phone_hmac"]:
            continue
        lead_id = leads.get((event["workflow_id"], event["phone_hmac"]))
        if lead_id is None:
            continue
        direct[event["event_id"]] = lead_id
        if event["call_id"]:
            call_leads.setdefault((event["workflow_id"], event["call_id"]), lead_id)

    for event in candidates:
        if db.execute("SELECT 1 FROM event_ledger WHERE event_id=?", (event["event_id"],)).fetchone():
            continue
        lead_id = direct.get(event["event_id"])
        if lead_id is None and event["event_type"] == "disposition.override":
            lead_id = call_leads.get((event["workflow_id"], event["call_id"]))
        if lead_id is None or not event["call_id"] or not event["occurred_at_utc"]:
            continue
        if event["event_type"].startswith("disposition."):
            catalogue = dispositions.get(event["workflow_id"], {})
            if not event["disposition_code"] or event["disposition_code"] not in catalogue:
                continue
        db.execute("INSERT INTO event_ledger VALUES(?,?,?,?,?,?,?)",
                   (event["event_id"], lead_id, event["event_type"], event["workflow_id"],
                    event["call_id"], event["occurred_at_utc"], event["disposition_code"]))


def event_quality(db, dispositions: dict[str, dict[str, dict[str, bool]]]) -> dict[str, int]:
    leads = {(r["workflow_id"], r["phone_hmac"]): r["id"]
             for r in db.execute("SELECT id,workflow_id,phone_hmac FROM leads")}
    candidates = db.execute("SELECT * FROM event_candidates").fetchall()
    direct = {}
    call_leads = {}
    for event in candidates:
        if event["phone_hmac"]:
            lead_id = leads.get((event["workflow_id"], event["phone_hmac"]))
            if lead_id is not None:
                direct[event["event_id"]] = lead_id
                if event["call_id"]:
                    call_leads.setdefault((event["workflow_id"], event["call_id"]), lead_id)
    orphan_count = unknown_count = 0
    for event in candidates:
        linked = direct.get(event["event_id"])
        if linked is None and event["event_type"] == "disposition.override":
            linked = call_leads.get((event["workflow_id"], event["call_id"]))
        if linked is None:
            orphan_count += 1
        elif event["event_type"].startswith("disposition."):
            if event["disposition_code"] not in dispositions.get(event["workflow_id"], {}):
                unknown_count += 1
    outcomes = {row["outcome"]: row["n"] for row in db.execute("SELECT outcome,COUNT(*) n FROM event_attempts GROUP BY outcome")}
    return {
        "events_total": sum(outcomes.values()),
        "malformed_events": outcomes.get("malformed", 0),
        "duplicate_events_dropped": outcomes.get("duplicate", 0),
        "orphan_events": orphan_count,
        "unknown_disposition_events": unknown_count,
    }
