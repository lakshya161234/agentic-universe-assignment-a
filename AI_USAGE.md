# AI Usage Disclosure

## Tools used

Codex (OpenAI).

## What I used them for

Codex inspected the supplied assignment materials and generator, designed and implemented the
Assignment A pipeline, wrote the tests, ran the CLI and generated-data validations, reviewed the
implementation for correctness and PII exposure, debugged issues, and drafted the solution and
submission documentation.

## What I wrote or decided myself

**Candidate to complete before submission:** identify the design choices you personally reviewed,
accepted, changed, or independently implemented. Do not claim ownership of work you have not
reviewed and can explain.

## Where the AI was wrong or unhelpful

The initial `pytest -q` console invocation could not import the `solution` package in this
environment. Codex diagnosed the invocation-path issue, added a pytest `pythonpath` setting, and
confirmed the same command passed afterward. **Candidate: review this account and add any other
genuine examples from your review; do not invent one.**

## How I verified AI-generated code

Codex ran `pytest -q` (3 passed), full generator/CLI/report flows on seeds 42, 7, and 2026, and the
large seed 42 flow. It checked all five report headers, batch/lead/call reconciliation, quality
counters, and byte-identical reports after re-ingestion. A database scan checked 502 source name
values and 1,953 normalized lead/event phone values for plaintext. **Candidate review is still
outstanding:** rerun or inspect the solution yourself, then record only what you personally
verified here.
