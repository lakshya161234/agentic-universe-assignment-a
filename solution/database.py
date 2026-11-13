from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS source_files (
  path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, phone_hmac TEXT NOT NULL,
  batch_id TEXT NOT NULL, UNIQUE(workflow_id, phone_hmac)
);
CREATE INDEX IF NOT EXISTS leads_workflow_batch ON leads(workflow_id, batch_id);
CREATE TABLE IF NOT EXISTS batches (
  workflow_id TEXT NOT NULL, batch_id TEXT NOT NULL,
  PRIMARY KEY(workflow_id, batch_id)
);
CREATE TABLE IF NOT EXISTS lead_values (
  lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL, value_text TEXT, value_cipher BLOB,
  PRIMARY KEY(lead_id, field_name),
  CHECK ((value_text IS NULL) != (value_cipher IS NULL))
);
CREATE TABLE IF NOT EXISTS lead_row_attempts (
  id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, row_number INTEGER NOT NULL,
  workflow_id TEXT NOT NULL, batch_id TEXT NOT NULL, rejected_reason TEXT,
  merged_duplicate INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source_path, row_number)
);
CREATE TABLE IF NOT EXISTS field_warnings (
  source_path TEXT NOT NULL, row_number INTEGER NOT NULL, field_name TEXT NOT NULL,
  PRIMARY KEY(source_path, row_number, field_name)
);
CREATE TABLE IF NOT EXISTS rejections (
  source_path TEXT NOT NULL, row_number INTEGER NOT NULL,
  workflow_id TEXT NOT NULL, batch_id TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(source_path, row_number)
);
CREATE TABLE IF NOT EXISTS event_attempts (
  id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, line_number INTEGER NOT NULL,
  event_id TEXT, outcome TEXT NOT NULL,
  UNIQUE(source_path, line_number)
);
CREATE TABLE IF NOT EXISTS event_candidates (
  event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, workflow_id TEXT NOT NULL,
  call_id TEXT NOT NULL, occurred_at_utc TEXT, phone_hmac TEXT,
  disposition_code TEXT, payload_cipher BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS candidates_phone ON event_candidates(workflow_id, phone_hmac);
CREATE INDEX IF NOT EXISTS candidates_call ON event_candidates(workflow_id, call_id);
CREATE TABLE IF NOT EXISTS event_ledger (
  event_id TEXT PRIMARY KEY REFERENCES event_candidates(event_id),
  lead_id INTEGER NOT NULL REFERENCES leads(id), event_type TEXT NOT NULL,
  workflow_id TEXT NOT NULL, call_id TEXT NOT NULL, occurred_at_utc TEXT NOT NULL,
  disposition_code TEXT
);
CREATE INDEX IF NOT EXISTS ledger_lead ON event_ledger(lead_id, event_type);
CREATE INDEX IF NOT EXISTS ledger_call ON event_ledger(workflow_id, call_id, event_type);
CREATE TRIGGER IF NOT EXISTS event_ledger_no_update
BEFORE UPDATE ON event_ledger BEGIN
  SELECT RAISE(ABORT, 'event ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS event_ledger_no_delete
BEFORE DELETE ON event_ledger BEGIN
  SELECT RAISE(ABORT, 'event ledger is append-only');
END;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 5000")
    db.executescript(SCHEMA_SQL)
    return db
