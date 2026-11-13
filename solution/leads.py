from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .normalization import normalize_value
from .schemas import WorkflowSchema, normalize_header
from .security import PIIProtection


def ingest_lead_file(db, path: Path, workflow_id: str, batch_id: str,
                     schema: WorkflowSchema, security: PIIProtection, source_path: str) -> None:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return
        field_by_header: dict[str, str] = {}
        for field in schema.fields:
            for header in (field["name"], *field.get("aliases", [])):
                field_by_header[normalize_header(header)] = field["name"]
        positions = {}
        for index, header in enumerate(headers):
            field_name = field_by_header.get(normalize_header(header))
            if field_name and field_name not in positions:
                positions[field_name] = index

        row_number = 0
        for row in reader:
            if all(not cell.strip() for cell in row):
                continue
            row_number += 1
            values: dict[str, str | None] = {}
            warnings: list[str] = []
            rejection = None
            for field in schema.fields:
                name = field["name"]
                raw = row[positions[name]] if name in positions and positions[name] < len(row) else ""
                normalized, error = normalize_value(field, raw)
                if error:
                    if field.get("required"):
                        rejection = "invalid_phone" if error == "invalid_phone" else f"{error}:{name}"
                        break
                    warnings.append(name)
                    normalized = None
                if field.get("required") and normalized is None:
                    rejection = f"missing_required:{name}"
                    break
                values[name] = normalized

            db.execute("INSERT INTO lead_row_attempts(source_path,row_number,workflow_id,batch_id,rejected_reason) VALUES(?,?,?,?,?)",
                       (source_path, row_number, workflow_id, batch_id, rejection))
            if rejection:
                db.execute("INSERT INTO rejections VALUES(?,?,?,?,?)",
                           (source_path, row_number, workflow_id, batch_id, rejection))
                continue
            for field_name in warnings:
                db.execute("INSERT INTO field_warnings VALUES(?,?,?)", (source_path, row_number, field_name))

            phone = values["phone"]
            fingerprint = security.fingerprint(phone)
            found = db.execute("SELECT id FROM leads WHERE workflow_id=? AND phone_hmac=?",
                               (workflow_id, fingerprint)).fetchone()
            if found:
                lead_id = found["id"]
                db.execute("UPDATE lead_row_attempts SET merged_duplicate=1 WHERE source_path=? AND row_number=?",
                           (source_path, row_number))
            else:
                cursor = db.execute("INSERT INTO leads(workflow_id,phone_hmac,batch_id) VALUES(?,?,?)",
                                    (workflow_id, fingerprint, batch_id))
                lead_id = cursor.lastrowid
            for field in schema.fields:
                name = field["name"]
                value = values.get(name)
                if value is None:
                    continue
                if field.get("pii"):
                    db.execute("INSERT INTO lead_values(lead_id,field_name,value_cipher) VALUES(?,?,?) "
                               "ON CONFLICT(lead_id,field_name) DO UPDATE SET value_text=NULL,value_cipher=excluded.value_cipher",
                               (lead_id, name, security.encrypt(value)))
                else:
                    db.execute("INSERT INTO lead_values(lead_id,field_name,value_text) VALUES(?,?,?) "
                               "ON CONFLICT(lead_id,field_name) DO UPDATE SET value_cipher=NULL,value_text=excluded.value_text",
                               (lead_id, name, value))
            # Defaults apply after merging: fill only attributes still absent.
            for field in schema.fields:
                default = field.get("default")
                if default is None:
                    continue
                exists = db.execute("SELECT 1 FROM lead_values WHERE lead_id=? AND field_name=?",
                                    (lead_id, field["name"])).fetchone()
                if not exists:
                    if field.get("pii"):
                        db.execute("INSERT INTO lead_values(lead_id,field_name,value_cipher) VALUES(?,?,?)",
                                   (lead_id, field["name"], security.encrypt(str(default))))
                    else:
                        db.execute("INSERT INTO lead_values(lead_id,field_name,value_text) VALUES(?,?,?)",
                                   (lead_id, field["name"], str(default)))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
