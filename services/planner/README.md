# planner service

Cloud Run service (Python 3.12, FastAPI). Implements `contracts/planner/openapi.yaml`, reads
the Firestore documents in `contracts/firestore/`, and serves the web frontend from `static/`.

## Run locally (file store, no GCP)

```sh
cd services/planner
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
STORE_BACKEND=file FILE_STORE_DIR=tests/fixtures/store \
  uvicorn planner.main:create_app --factory --port 8080
```

A 45-minute step-free plan against the synthetic test museum:

```sh
curl -s localhost:8080/api/plan -H 'content-type: application/json' -d '{
  "museum_id": "testmuseum",
  "interests": ["armor"],
  "time_budget_minutes": 45,
  "constraints": {"step_free": true}
}'
```

## Tests

```sh
pytest
```

## Notes

- Env vars are listed under `planner` in `contracts/services.json`.
- If a plan ever fails the built-in step-free/stairs integrity check, the service answers 500
  with error code `internal_error`. That code is not in the contract's `Error.code` enum yet.
