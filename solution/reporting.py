from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .database import connect
from .events import event_quality
from .normalization import IST
from .schemas import load_dispositions, load_schemas
from .security import PIIProtection


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def generate_reports(db_path: Path, out_dir: Path, security: PIIProtection) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    try:
        schemas = load_schemas()
        dispositions = load_dispositions()
        leads = db.execute("SELECT * FROM leads ORDER BY workflow_id,batch_id,id").fetchall()
        attrs: dict[int, dict[str, str]] = defaultdict(dict)
        for row in db.execute("SELECT * FROM lead_values"):
            value = row["value_text"] if row["value_cipher"] is None else security.decrypt(row["value_cipher"])
            attrs[row["lead_id"]][row["field_name"]] = value
        events = db.execute("SELECT * FROM event_ledger ORDER BY occurred_at_utc,event_id").fetchall()

        # Each call fact is derived only from accepted, linked ledger entries.
        call_events: dict[tuple[int, str], list] = defaultdict(list)
        for event in events:
            call_events[(event["lead_id"], event["call_id"])].append(event)
        calls_by_lead: dict[int, list[dict]] = defaultdict(list)
        for (lead_id, call_id), rows in call_events.items():
            initiated = [row for row in rows if row["event_type"] == "call.initiated"]
            if not initiated:
                continue
            init = max(initiated, key=lambda row: (row["occurred_at_utc"], row["event_id"]))
            overrides = [row for row in rows if row["event_type"] == "disposition.override"]
            sets = [row for row in rows if row["event_type"] == "disposition.set"]
            dispositions_for_call = overrides if overrides else sets
            effective = max(dispositions_for_call, key=lambda row: (row["occurred_at_utc"], row["event_id"])) if dispositions_for_call else None
            calls_by_lead[lead_id].append({
                "call_id": call_id,
                "init": init["occurred_at_utc"],
                "connected": any(row["event_type"] == "call.connected" for row in rows),
                "telephony_failed": any(row["event_type"] == "call.telephony_failed" for row in rows),
                "disposition": effective["disposition_code"] if effective else None,
                "workflow_id": init["workflow_id"],
            })
        for facts in calls_by_lead.values():
            facts.sort(key=lambda call: (call["init"], call["call_id"]))

        batch_counts: dict[tuple[str, str], dict[str, int]] = {
            (row["workflow_id"], row["batch_id"]): {"total_leads": 0, "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0, "in_progress_dialed": 0, "completed": 0}
            for row in db.execute("SELECT workflow_id,batch_id FROM batches")
        }
        lead_rows = []
        daily: dict[tuple[str, str], dict[str, object]] = {}
        for lead in leads:
            lead_id, workflow_id = lead["id"], lead["workflow_id"]
            schema = schemas[workflow_id]
            calls = calls_by_lead.get(lead_id, [])
            if not calls:
                bucket = "yet_to_reach_out"
            elif any(call["disposition"] and dispositions.get(workflow_id, {}).get(call["disposition"], {}).get("is_terminal", False) for call in calls):
                bucket = "completed"
            elif all(call["telephony_failed"] for call in calls):
                bucket = "in_progress_telephony_failed"
            else:
                bucket = "in_progress_dialed"
            batch_key = (workflow_id, lead["batch_id"])
            counts = batch_counts.setdefault(batch_key, {"total_leads": 0, "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0, "in_progress_dialed": 0, "completed": 0})
            counts["total_leads"] += 1
            counts[bucket] += 1

            success_calls = [call for call in calls if call["disposition"] and
                             dispositions.get(workflow_id, {}).get(call["disposition"], {}).get("is_success", False)]
            for call in calls:
                date = datetime.fromisoformat(call["init"]).astimezone(IST).date().isoformat()
                key = (workflow_id, date)
                facts = daily.setdefault(key, {"total_calls": 0, "connected_calls": 0, "success_leads": set()})
                facts["total_calls"] += 1
                facts["connected_calls"] += int(call["connected"])
                if call["disposition"] and dispositions.get(workflow_id, {}).get(call["disposition"], {}).get("is_success", False):
                    facts["success_leads"].add(lead_id)

            reference = attrs[lead_id].get(schema.reference_field, "")
            digits = ""
            phone = attrs[lead_id].get("phone")
            if phone:
                digits = phone[-4:]
            connected_calls = sum(call["connected"] for call in calls)
            last_call = calls[-1]["init"] if calls else None
            current_call = next((call for call in reversed(calls) if call["disposition"]), None)
            current = current_call["disposition"] if current_call else ""
            lead_rows.append([workflow_id, reference, lead["batch_id"], "+91******" + digits if digits else "",
                              bucket, len(calls), connected_calls,
                              datetime.fromisoformat(last_call).astimezone(IST).isoformat() if last_call else "",
                              current, "true" if success_calls else "false"])

        batch_rows = [[wf, batch, counts["total_leads"], counts["yet_to_reach_out"],
                       counts["in_progress_telephony_failed"], counts["in_progress_dialed"], counts["completed"]]
                      for (wf, batch), counts in sorted(batch_counts.items())]
        daily_rows = []
        for (workflow_id, date), values in sorted(daily.items()):
            rate = Decimal(values["connected_calls"]) / Decimal(values["total_calls"])
            daily_rows.append([workflow_id, date, values["total_calls"], values["connected_calls"],
                               f"{rate:.4f}", len(values["success_leads"])])
        lead_rows.sort(key=lambda row: (row[0], row[1]))

        rejection_rows = db.execute("SELECT workflow_id,batch_id,row_number,reason FROM rejections ORDER BY workflow_id,batch_id,row_number").fetchall()
        quality = {
            "lead_rows_total": db.execute("SELECT COUNT(*) FROM lead_row_attempts").fetchone()[0],
            "lead_rows_rejected": db.execute("SELECT COUNT(*) FROM rejections").fetchone()[0],
            "rejections_by_reason": {row["reason"]: row["n"] for row in db.execute("SELECT reason,COUNT(*) n FROM rejections GROUP BY reason ORDER BY reason")},
            "duplicate_lead_rows_merged": db.execute("SELECT COUNT(*) FROM lead_row_attempts WHERE merged_duplicate=1").fetchone()[0],
            "field_warnings": db.execute("SELECT COUNT(*) FROM field_warnings").fetchone()[0],
            **event_quality(db, dispositions),
        }
        _write_csv(out_dir / "batch_summary.csv", ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"], batch_rows)
        _write_csv(out_dir / "daily_overview.csv", ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"], daily_rows)
        _write_csv(out_dir / "lead_report.csv", ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"], lead_rows)
        _write_csv(out_dir / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"], [list(row) for row in rejection_rows])
        temp = out_dir / ".data_quality.json.tmp"
        temp.write_text(json.dumps(quality, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, out_dir / "data_quality.json")
    finally:
        db.close()
