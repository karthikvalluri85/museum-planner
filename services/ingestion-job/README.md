# ingestion-job

A Cloud Run Job (Python 3.12, no HTTP port). Each execution drains the `ingest-museum` pull subscription, re-ingests museums whose data is older than `REFRESH_AFTER_HOURS`, normalizes each museum's open API data and writes it to Firestore. It is the only writer of the `museums`, `object_chunks` and `floor_graphs` documents; the planner only reads them.

Contracts it implements or consumes are in `/contracts` (see `contracts/README.md` for the write protocol). Environment variables, defaults, IAM and failure policy: the `ingestion-job` entry of `contracts/services.json`.

Floor graphs are hand-built and bundled in `seed/floor_graphs/{museum_id}.json`. A museum without an adapter or without a seed graph is reported as `unsupported` and its messages are acknowledged.

## Run locally without GCP

```
pip install -r requirements-dev.txt
set STORE_BACKEND=file
set FILE_STORE_DIR=out
set SOURCE_MODE=fixture
set MUSEUM_ID=met
python -m ingestion.main
```

With `MUSEUM_ID` set, Pub/Sub is never touched. The last stdout line is the run report: `{"run": {"met": {"status": "ok", ...}}}`. Exit code 0 on success, 1 if any museum failed, 2 on bad configuration.

## Tests

```
pytest
```

Fully offline. Contract compliance tests validate everything the job writes against `contracts/firestore/*.schema.json`.

## Fixtures

`fixtures/` holds raw upstream payloads for `SOURCE_MODE=fixture` and the tests. See `fixtures/README.md` for their provenance.
