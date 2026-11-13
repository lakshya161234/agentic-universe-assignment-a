# Assignment A pipeline

This implementation ingests the supplied lead CSV and event JSONL drops into SQLite, then
generates the five report files in the assignment contract. It uses Python 3.11+, SQLite, and
`cryptography` for authenticated encryption. No service or network connection is required.

## Architecture

`cli.py` provides `ingest` and `report`; `pipeline.py` owns drop-level transactions and immutable
source-file tracking. `leads.py` maps schema aliases, normalizes and validates rows, merges leads,
and writes rejections/warnings. `events.py` deduplicates event IDs, stores minimized candidates,
resolves lead/call links as new drops arrive, and appends report-eligible events to the ledger.
`reporting.py` derives call facts from the ledger and writes contract-ordered CSV/JSON output.
`schemas.py` loads workflow fields and disposition catalogues from the supplied files. `security.py`
holds the local encryption and keyed matching functions.

The database separates canonical leads, typed lead values, row-level quality records, source-file
tracking, event candidates, event attempts, and the accepted event ledger. Leads have a unique
`(workflow_id, phone_hmac)` constraint. Event candidates and ledger rows have unique event IDs;
the ledger references its immutable candidate. Lead attributes and event payloads marked or
potentially containing PII are stored as AES-GCM ciphertext. The phone used for identity and event
matching is stored as HMAC-SHA256 with a derived secret key. Event payloads are encrypted as JSON;
raw event phone values and arbitrary top-level JSON are never stored. The ledger preserves the
reportable event fields and encrypted payload history.

## Setup and run

From the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export LEDGER_KEY="$(python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.cli ingest data/drop_1
python -m solution.cli ingest data/drop_2
python -m solution.cli report --out reports/
pytest -q
```

Keep the same `LEDGER_KEY` for every run that reads or writes a given database. Store it in a
secret manager or another secure location. The key is not written to the database or repository;
losing it makes encrypted values unreadable and changes phone fingerprints. Do not commit it or
put it in an unignored file. A separate database can be selected with the global option before
the command, for example `python -m solution.cli --db /tmp/ledger.sqlite3 ingest data/drop_1`.

The default database is `interaction_ledger.sqlite3` at the repository root. Generated input,
database and report paths are ignored by Git. The report command writes exactly these files under
the selected output directory: `batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`,
`rejections.csv`, and `data_quality.json`.

## Processing decisions

- Files are processed in drop invocation order; lead files are sorted by filename and workflow,
  followed by event files sorted by filename. CSV rows retain nonblank data-row numbers.
- A source file is identified by its resolved path and SHA-256 content. Re-ingesting the same
  unchanged path skips it. If a previously ingested path changes content, the transaction fails;
  place corrected content at a new path or rebuild from a clean database.
- An accepted lead's first batch remains its owner. Later valid rows update only non-null fields;
  defaults fill remaining null fields. Rejection reasons for invalid required non-phone values use
  `invalid_<type>:<field>` (for example `invalid_date:policy_expiry`).
- Unique event IDs are counted once; repeated deliveries are tracked as duplicate attempts. Valid
  event candidates are retained with encrypted payloads even while orphaned. After each drop,
  phone-linked events are retried against the current lead index, and overrides resolve through
  other phone-linked events with the same workflow and call ID. Newly linkable catalogue-valid
  candidates are appended to the ledger; prior ledger rows are never edited. Orphan quality counts
  are derived from the final resolution state. For an event that is both unlinked and has an
  unknown disposition, orphan takes precedence until it links; then it contributes to the unknown
  disposition count.
- Disposition and initiation timestamp ties are broken by lexically greatest event ID; call ties
  by lexically greatest call ID. This makes reports stable when timestamps are equal.
- Invalid nonempty optional fields are nulled and counted as one warning per field on accepted
  rows. A required field with a non-phone invalid type is rejected with `invalid_<type>:<field>`.

## Security and limitations

Phone numbers and schema fields marked PII are AES-GCM encrypted in SQLite. Phone HMACs use a
separate key derived from the supplied secret and are not plain unsalted hashes. PII is not emitted
in rejections; lead reports contain only a masked phone. Event payload JSON is encrypted, while
event phone values are normalized and represented only by their keyed HMAC for matching. The
database does not encrypt non-PII metadata or protect against a compromised runtime/key. Key
rotation and multi-user key management are outside this local take-home implementation. WAL files
and backups must be protected like the database because encrypted fields remain sensitive data.

SQLite uses transactions for each drop, so failures roll back all new files from that drop. Source
file hashes provide retry safety, and database uniqueness constraints enforce lead/event identity.
Reports are deterministic for the same database state. The pipeline is designed for the supplied
assessment scale; it does not include parallel ingestion, schema migrations beyond initial table
creation, a correction/retraction workflow, or a production scheduler. For a production service at
100k leads/day, I would add managed key storage and rotation, migration tooling, object-store source
manifests, monitored scheduled jobs with retry/backfill controls, partitioned event storage, and
load/performance monitoring while retaining idempotency keys and transaction boundaries.
