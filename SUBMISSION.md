# Submission

## Candidate
- **Name:** Lakshya Upreti
- **Assignment chosen:** A — Data
- **Time spent (honest estimate):** 2 hours

## How to run

From the repository root with Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export LEDGER_KEY="$(python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution.cli ingest data/drop_1
python -m solution.cli ingest data/drop_2
python -m solution.cli report --out reports/
pytest -q
```

Keep the generated key safe and unchanged for the lifetime of the database. Full setup, key,
database-path, and report details are in [solution/README.md](solution/README.md).

## Design

The pipeline uses Python modules for schema loading, normalization, lead ingestion, event
resolution, database access, reporting, and PII protection. SQLite stores canonical leads and
typed attributes, source-file hashes, batches, row-level warnings/rejections, event attempts,
pending event candidates, and an accepted interaction ledger. A unique constraint on
`(workflow_id, phone_hmac)` merges repeated leads. A unique event ID prevents duplicate ledger
entries. The ledger references an immutable candidate and SQLite triggers reject ledger updates
and deletes.

Fields marked PII are AES-GCM encrypted. Phone identity uses HMAC-SHA256 with a separately derived
key, so matching works without storing a plaintext number or a plain phone hash. Event JSON
payloads are encrypted; event phone values are represented only by the keyed HMAC. Reports
decrypt only values needed for rendering, and expose a masked phone in the lead report.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Derive encryption and lookup keys from one externally supplied 32-byte-or-longer secret using separate HMAC labels. | Simple local setup while keeping encryption and matching keys distinct. | Separate independently provisioned secrets. |
| Reject a changed source file at an already ingested resolved path; use a new path or rebuild from a clean database for corrections. | Prevents silently changing prior results while keeping retries safe. | Replace prior file effects with compensating updates. |
| Keep unique valid event candidates pending until they can be linked; recalculate resolution after each drop. | Supports events arriving before leads or call mappings while retaining append-only accepted history. | Drop unresolved events permanently. |
| For an event that is unlinked and has an unknown disposition, count it as orphan until it links; then count it as unknown disposition. | Follows the specification's orphan-before-disposition processing order without double counting. | Count both categories at once. |
| Break equal event timestamps by lexically greatest event ID; break equal call initiation timestamps by lexically greatest call ID. | Gives stable output for tied timestamps. | Preserve ingestion order. |
| Use `invalid_<type>:<field>` for invalid required non-phone values. | The assignment specifies exact missing-required and invalid-phone codes, but not codes for other invalid required types. | Reject with a generic `invalid_required:<field>` reason. |

## Failure modes considered

- Invalid CSV values are rejected or nulled with warnings; rejection output contains no row PII.
- Invalid JSON and missing event IDs count as malformed. Duplicate event IDs count once as duplicate
  attempts. Unknown dispositions and unresolved events are reflected in their final counters.
- Each drop runs in one SQLite transaction. A failure rolls back its new files. Unchanged files
  skip on retry; changed content at an ingested path fails before commit.
- Pending events are retried after later drops; accepted ledger events are protected from update or
  deletion by database triggers.
- The implementation does not support key rotation, event correction/retraction, concurrent
  multi-process ingestion, or database migrations beyond initial schema creation. Keep database
  backups and WAL files protected, and retain the key securely.

## Testing

`pytest -q` currently reports **3 passed**. Tests cover header aliases, phone/date/decimal
normalization, blank rows and rejection numbering, warnings, duplicate merging and original batch
ownership, late event linking, orphan/unknown/duplicate/malformed event accounting, multiple
overrides, all lead buckets, timezone output, PII ciphertext, append-only ledger enforcement,
report stability, and changed-file rollback.

The full generator and CLI flow was run on seeds 42, 7, and 2026, each with isolated databases.
For each seed, both drops were ingested, reports were generated, batch/lead/quality reconciliation
checks passed, and re-ingestion produced byte-identical report files. The first ingest-plus-report
run took about 0.6 seconds per small seed in this environment. The optional large seed 42 run
ingested in 64.33 seconds and reported in 21.54 seconds: 132,903 leads and 632,602 event lines.
Ingestion plus reporting took about 85.9 seconds, below the assignment's two-minute stretch target
in this environment. A final seed 42 run with the finished code verified
all five headers, PII sample absence from SQLite, reconciliation, and byte-stable re-ingestion.
These are synthetic-data checks, not a claim of independent human verification.

## Results

For seed 42 (`small`), the reports contained 1,892 leads across six workflow/batch groups and 33
workflow/date rows. Quality accounting reported 2,057 nonblank lead rows (108 rejected, 57 merged
duplicates), 8,745 nonempty event lines (8 malformed, 237 duplicate deliveries), 462 final orphan
events, and 18 unknown-disposition events. The report reconciliation checks passed.

## If I had another day

1. Add explicit schema versioning and a documented migration process.
2. Add an operator workflow for correcting source files through a full rebuild or safe compensating
   records.
3. Add large-scale profiling and query/ingestion batching where measurements justify it.
4. Add managed key storage, key rotation, and encrypted backup/restore procedures.
5. Add more adversarial fixtures for conflicting call-to-lead mappings and malformed event shapes.
