from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SCHEMA_DIR


def normalize_header(value: str) -> str:
    return re.sub(r"[_\s]+", " ", value.strip().lower())


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple[dict[str, Any], ...]

    @property
    def reference_field(self) -> str:
        return next(field["name"] for field in self.fields if field.get("reference"))


def load_schemas(schema_dir: Path = SCHEMA_DIR) -> dict[str, WorkflowSchema]:
    result = {}
    for path in sorted(schema_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = WorkflowSchema(data["workflow_id"], tuple(data["fields"]))
        if schema.workflow_id in result:
            raise ValueError(f"Duplicate workflow schema: {schema.workflow_id}")
        result[schema.workflow_id] = schema
    return result


def load_dispositions(path: Path = SCHEMA_DIR / "dispositions.csv") -> dict[str, dict[str, dict[str, bool]]]:
    result: dict[str, dict[str, dict[str, bool]]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            result.setdefault(row["workflow_id"], {})[row["code"].strip().lower()] = {
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }
    return result
