#!/usr/bin/env bash
# Offline cross-service audit: the ingestion job writes a file store from its
# bundled fixtures, the planner serves from that store, and real HTTP responses
# are checked against the contract. Proves the two deployables agree on
# /contracts without sharing any code. No GCP, no emulators, no Docker.
#
#   bash tools/integration-audit.sh
#
# Needs: Python 3.12 with both services' requirements installed, node 20+, curl.
# PYTHON overrides the interpreter (default: python).

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python}"
STORE="$ROOT/.local/audit-store"
OUT="$ROOT/.local/audit-out"
PORT="${AUDIT_PORT:-8098}"
URL="http://127.0.0.1:$PORT"
rm -rf "$STORE" "$OUT"
mkdir -p "$STORE" "$OUT"

step() { printf '\n== %s\n' "$1"; }

step "1. Contract gate"
node tools/selftest.mjs
node tools/validate-contracts.mjs
node tools/validate-contracts.mjs --graph services/ingestion-job/seed/floor_graphs/*.json

step "2. Ingest fixtures into a file store (met, aic)"
for museum in met aic; do
  (cd services/ingestion-job && STORE_BACKEND=file FILE_STORE_DIR="$STORE" SOURCE_MODE=fixture MUSEUM_ID="$museum" REFRESH_AFTER_HOURS=0 "$PY" -m ingestion.main)
done

step "3. Everything the job wrote matches the Firestore contracts"
node tools/validate-contracts.mjs --instance contracts/firestore/museum.schema.json "$STORE"/museums/*.json
node tools/validate-contracts.mjs --instance contracts/firestore/object-chunk.schema.json "$STORE"/museums/*/object_chunks/*.json
node tools/validate-contracts.mjs --graph "$STORE"/floor_graphs/*.json

step "4. Start the planner on the same store"
(cd services/planner && STORE_BACKEND=file FILE_STORE_DIR="$STORE" PORT="$PORT" "$PY" -m uvicorn planner.main:app --host 127.0.0.1 --port "$PORT" --log-level warning) &
PLANNER_PID=$!
trap 'kill "$PLANNER_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -fsS "$URL/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "$URL/healthz"; echo

step "5. Real requests, checked against the schema, invariants I1-I8, and the stored galleries"
plan() {
  local name="$1" body="$2"
  echo "$body" > "$OUT/$name.request.json"
  curl -fsS -X POST "$URL/api/plan" -H 'content-type: application/json' -d "$body" > "$OUT/$name.response.json"
  node tools/check-plan.mjs "$OUT/$name.response.json" --request "$OUT/$name.request.json" --store "$STORE"
}
for museum in met aic; do
  curl -fsS "$URL/api/museums/$museum" > "$OUT/$museum.detail.json"
  node tools/validate-contracts.mjs --instance 'contracts/planner/openapi.yaml#/components/schemas/MuseumDetail' "$OUT/$museum.detail.json"
  plan "$museum-step-free-45" "{\"museum_id\":\"$museum\",\"interests\":[],\"time_budget_minutes\":45,\"constraints\":{\"step_free\":true}}"
  plan "$museum-child-45" "{\"museum_id\":\"$museum\",\"interests\":[],\"time_budget_minutes\":45,\"constraints\":{\"with_young_child\":true}}"
  plan "$museum-wheelchair-90" "{\"museum_id\":\"$museum\",\"interests\":[],\"time_budget_minutes\":90,\"constraints\":{\"step_free\":true}}"
  plan "$museum-lunch-30" "{\"museum_id\":\"$museum\",\"interests\":[],\"time_budget_minutes\":30,\"constraints\":{\"short_visit\":true}}"
  plan "$museum-stamina-low-sensory-120" "{\"museum_id\":\"$museum\",\"interests\":[],\"time_budget_minutes\":120,\"constraints\":{\"limited_stamina\":true,\"max_walking_minutes\":15,\"low_sensory\":true}}"
done

step "6. Error shapes"
code="$(curl -s -o "$OUT/404.json" -w '%{http_code}' "$URL/api/museums/no-such-museum")"
[ "$code" = "404" ] || { echo "FAIL: expected 404, got $code"; exit 1; }
node tools/validate-contracts.mjs --instance 'contracts/planner/openapi.yaml#/components/schemas/Error' "$OUT/404.json"
code="$(curl -s -o "$OUT/422.json" -w '%{http_code}' -X POST "$URL/api/plan" -H 'content-type: application/json' -d '{"museum_id":"met","time_budget_minutes":5}')"
[ "$code" = "422" ] || { echo "FAIL: expected 422, got $code"; exit 1; }
node tools/validate-contracts.mjs --instance 'contracts/planner/openapi.yaml#/components/schemas/Error' "$OUT/422.json"

step "7. Frontend is served"
curl -fsS -o /dev/null -w 'GET / -> HTTP %{http_code}, %{size_download} bytes\n' "$URL/"

printf '\nintegration audit: all checks passed. Responses are in %s\n' "${OUT#"$ROOT"/}"
