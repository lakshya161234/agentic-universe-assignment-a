from __future__ import annotations

from pathlib import Path

from .database import connect
from .events import ingest_event_file, resolve_events
from .leads import file_hash, ingest_lead_file
from .schemas import load_dispositions, load_schemas
from .security import PIIProtection


def ingest_drop(drop_path: Path, db_path: Path, security: PIIProtection) -> dict[str, int]:
    drop_path = drop_path.resolve()
    if not drop_path.is_dir():
        raise ValueError(f"Drop directory does not exist: {drop_path}")
    schemas = load_schemas()
    dispositions = load_dispositions()
    db = connect(db_path)
    ingested = skipped = 0
    try:
        with db:
            lead_files = sorted(drop_path.glob("leads/*/*.csv"), key=lambda p: (p.name, str(p.parent)))
            event_files = sorted(drop_path.glob("events/*.jsonl"), key=lambda p: p.name)
            for path in [*lead_files, *event_files]:
                source_path = str(path.resolve())
                digest = file_hash(path)
                existing = db.execute("SELECT sha256 FROM source_files WHERE path=?", (source_path,)).fetchone()
                if existing:
                    if existing["sha256"] != digest:
                        raise ValueError(f"Previously ingested source changed: {path}. Use a new filename for corrected input.")
                    skipped += 1
                    continue
                if path in lead_files:
                    workflow_id = path.parent.name
                    if workflow_id not in schemas:
                        raise ValueError(f"No schema found for workflow {workflow_id!r} in {path}")
                    db.execute("INSERT OR IGNORE INTO batches(workflow_id,batch_id) VALUES(?,?)",
                               (workflow_id, path.stem))
                    ingest_lead_file(db, path, workflow_id, path.stem, schemas[workflow_id], security, source_path)
                else:
                    ingest_event_file(db, path, security, source_path)
                db.execute("INSERT INTO source_files(path,sha256) VALUES(?,?)", (source_path, digest))
                ingested += 1
            resolve_events(db, dispositions)
    finally:
        db.close()
    return {"files_ingested": ingested, "files_skipped": skipped}
