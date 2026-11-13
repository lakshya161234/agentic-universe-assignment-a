from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_DB
from .pipeline import ingest_drop
from .reporting import generate_reports
from .security import PIIProtection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m solution.cli")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite database (default: {DEFAULT_DB})")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="ingest a drop directory")
    ingest.add_argument("drop", type=Path)
    report = commands.add_parser("report", help="write the five contract reports")
    report.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        security = PIIProtection.from_env()
        if args.command == "ingest":
            result = ingest_drop(args.drop, args.db, security)
            print(f"Ingested {result['files_ingested']} file(s); skipped {result['files_skipped']} unchanged file(s).")
        else:
            generate_reports(args.db, args.out, security)
            print(f"Reports written to {args.out}")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
