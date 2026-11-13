import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solution.database import connect
from solution.normalization import normalize_date, normalize_decimal, normalize_phone
from solution.pipeline import ingest_drop
from solution.reporting import generate_reports
from solution.security import PIIProtection


def write_jsonl(path: Path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def event(event_id, kind, phone, call, at, code=None):
    payload = {"disposition_code": code} if code is not None else {}
    value = {"event_id": event_id, "event_type": kind, "workflow_id": "motor_renewal",
             "call_id": call, "occurred_at": at, "payload": payload}
    if phone is not None:
        value["phone"] = phone
    return value


def write_leads(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Insured Name", "Mobile No", "Policy No", "Expiry Date", "Premium", "Language", "extra column"])
        writer.writerows(rows)


def test_normalizers():
    assert normalize_phone("(+91) 98765-43210") == "+919876543210"
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("+91 9876543210") == "+919876543210"
    assert normalize_phone("5876543210") is None
    assert normalize_date("01-dEc-2026") == "2026-12-01"
    assert normalize_date("31/02/2026") is None
    assert normalize_decimal("₹1,234.565") == "1234.57"
    assert normalize_decimal("Rs. 4,000") == "4000.00"
    assert normalize_decimal("-1") is None


def test_incremental_pipeline_and_exact_reports(tmp_path):
    db_path = tmp_path / "ledger.sqlite3"
    output = tmp_path / "reports"
    security = PIIProtection(b"unit test secret key value 32 bytes!!")
    p = ["9876543210", "8765432109", "7654321098", "6543210987", "9988776655"]
    drop1 = tmp_path / "drop_1"
    write_jsonl(drop1 / "events" / "events_0001.jsonl", [
        event("a-init", "call.initiated", p[0], "a", "2026-10-01T18:50:00Z"),
        event("a-connect", "call.connected", p[0], "a", "2026-10-01T19:00:00+05:30"),
        event("a-set", "disposition.set", p[0], "a", "2026-10-01 19:01:00", "renewed"),
        event("a-override", "disposition.override", None, "a", "2026-10-01T19:02:00+05:30", "callback_requested"),
        event("a-override-2", "disposition.override", None, "a", "2026-10-01T19:03:00+05:30", "payment_link_sent"),
        event("b-init", "call.initiated", p[1], "b", "2026-10-02T10:00:00+05:30"),
        event("b-fail", "call.telephony_failed", p[1], "b", "2026-10-02T10:00:02+05:30"),
        event("b2-init", "call.initiated", p[1], "b2", "2026-10-02T11:00:00+05:30"),
        event("b2-end", "call.ended", p[1], "b2", "2026-10-02T11:01:00+05:30"),
        event("c-init", "call.initiated", p[2], "c", "2026-10-02T18:50:00Z"),
        event("c-set", "disposition.set", p[2], "c", "2026-10-02T19:10:00Z", "renewed"),
        event("bad-code", "disposition.set", p[2], "c", "2026-10-02T19:11:00Z", "not-a-code"),
        event("e-init", "call.initiated", p[4], "e", "2026-10-03T10:00:00+05:30"),
        event("e-fail", "call.telephony_failed", p[4], "e", "2026-10-03T10:00:02+05:30"),
        event("pure-orphan", "call.initiated", "9123456789", "orphan", "2026-10-02T10:00:00Z"),
    ])
    with (drop1 / "events" / "events_0001.jsonl").open("a", encoding="utf-8") as f:
        f.write("{broken json\n")
        f.write('{"event_type":"call.initiated"}\n')
    assert ingest_drop(drop1, db_path, security)["files_ingested"] == 1

    drop2 = tmp_path / "drop_2"
    rows = [
        ["Alice Example", p[0], "A-1", "31/02/2026", "N/A", "Hindi", "ignore"],
        ["Alice Updated", p[0], "A-2", "", "", "", "ignore"],
        ["Bob", p[1], "B-1", "", "", "hi", "ignore"],
        ["Carol", p[2], "C-1", "", "", "ta", "ignore"],
        ["", "", "", "", "", "", ""],
        ["Dara", p[3], "D-1", "", "", "kn", "ignore"],
        ["Eshan", p[4], "E-1", "", "", "mr", "ignore"],
        ["Rejected", "5555555555", "X-1", "", "", "en", "ignore"],
    ]
    write_leads(drop2 / "leads" / "motor_renewal" / "batch_001.csv", rows)
    # A repeated event ID is dropped even when delivered in another drop.
    write_jsonl(drop2 / "events" / "events_1001.jsonl", [
        event("a-init", "call.initiated", p[0], "a", "2026-10-01T18:50:00Z"),
    ])
    assert ingest_drop(drop2, db_path, security)["files_ingested"] == 2
    generate_reports(db_path, output, security)
    original = {p.name: p.read_bytes() for p in output.iterdir()}

    with (output / "batch_summary.csv").open(newline="", encoding="utf-8") as f:
        batches = list(csv.DictReader(f))
    assert batches == [{"workflow_id": "motor_renewal", "batch_id": "batch_001", "total_leads": "5",
                        "yet_to_reach_out": "1", "in_progress_telephony_failed": "1",
                        "in_progress_dialed": "2", "completed": "1"}]
    with (output / "lead_report.csv").open(newline="", encoding="utf-8") as f:
        report = list(csv.DictReader(f))
    alice = next(row for row in report if row["phone_masked"] == "+91******3210")
    assert alice["external_ref"] == "A-2"  # last non-null reference wins
    assert alice["batch_id"] == "batch_001"  # first batch remains owner
    assert alice["bucket"] == "in_progress_dialed"  # override replaces effective success
    assert alice["current_disposition"] == "payment_link_sent"  # latest of several overrides
    assert alice["ever_success"] == "false"
    assert alice["last_call_at"].endswith("+05:30")
    carol = next(row for row in report if row["external_ref"] == "C-1")
    assert carol["bucket"] == "completed"
    assert carol["ever_success"] == "true"
    with (output / "daily_overview.csv").open(newline="", encoding="utf-8") as f:
        daily = list(csv.DictReader(f))
    assert [row["date"] for row in daily] == ["2026-10-02", "2026-10-03"]
    assert daily[0]["total_calls"] == "3" and daily[0]["connected_calls"] == "1"
    assert daily[0]["connectivity_rate"] == "0.3333"
    assert daily[1]["success_leads"] == "1"
    assert all(row["ever_success"] in {"true", "false"} for row in report)
    assert "+919876543210" not in (output / "lead_report.csv").read_text()
    assert "Alice" not in (output / "rejections.csv").read_text()
    quality = json.loads((output / "data_quality.json").read_text())
    assert quality == {
        "lead_rows_total": 7,
        "lead_rows_rejected": 1,
        "rejections_by_reason": {"invalid_phone": 1},
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 3,
        "events_total": 18,
        "malformed_events": 2,
        "duplicate_events_dropped": 1,
        "orphan_events": 1,
        "unknown_disposition_events": 1,
    }
    with connect(db_path) as db:
        persisted = b" ".join(bytes(row[0]) for row in db.execute("SELECT value_cipher FROM lead_values WHERE value_cipher IS NOT NULL"))
        assert b"Alice" not in persisted
        assert b"9876543210" not in persisted
        assert db.execute("SELECT COUNT(*) FROM event_ledger").fetchone()[0] == 13
        assert db.execute("SELECT COUNT(*) FROM rejections WHERE row_number=7").fetchone()[0] == 1
        assert db.execute("SELECT value_text FROM lead_values WHERE field_name='preferred_language' "
                          "AND lead_id=(SELECT lead_id FROM lead_values WHERE field_name='policy_number' AND value_text='A-2')").fetchone()[0] == "en"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE event_ledger SET disposition_code='not_interested' WHERE event_id='a-set'")

    # Re-ingesting unchanged source files and regenerating reports are byte-stable.
    assert ingest_drop(drop1, db_path, security)["files_skipped"] == 1
    assert ingest_drop(drop2, db_path, security)["files_skipped"] == 2
    generate_reports(db_path, output, security)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == original


def test_changed_source_fails_without_partial_changes(tmp_path):
    db_path = tmp_path / "ledger.sqlite3"
    security = PIIProtection(b"unit test secret key value 32 bytes!!")
    drop = tmp_path / "drop"
    source = drop / "leads" / "motor_renewal" / "batch_001.csv"
    write_leads(source, [["Nina", "9876543210", "N-1", "", "", "en", ""]])
    ingest_drop(drop, db_path, security)
    with connect(db_path) as db:
        before = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    write_leads(source, [["Nina", "9876543210", "N-2", "", "", "en", ""]])
    with pytest.raises(ValueError, match="Previously ingested source changed"):
        ingest_drop(drop, db_path, security)
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == before == 1
        assert db.execute("SELECT value_text FROM lead_values WHERE field_name='policy_number'").fetchone()[0] == "N-1"
