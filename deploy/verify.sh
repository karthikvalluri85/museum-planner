#!/usr/bin/env bash
# Hits a running planner with real requests and checks the answers against the
# contract (schema plus invariants I1-I8). Works against Cloud Run or localhost.
#
#   ./deploy/verify.sh https://planner-xxxx.a.run.app
#   ./deploy/verify.sh http://localhost:8080 met
#
# Exits non-zero on the first failure. Prints what it received either way:
# do not call something deployed until this has passed against it.

set -euo pipefail

URL="${1:?usage: verify.sh <base url> [museum id]}"
MUSEUM="${2:-met}"
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "-- GET $URL/healthz"
curl -fsS "$URL/healthz"; echo

echo "-- GET $URL/api/museums"
curl -fsS "$URL/api/museums" | tee "$TMP/museums.json"; echo
grep -q "\"id\":[[:space:]]*\"$MUSEUM\"" "$TMP/museums.json" || { echo "FAIL: museum '$MUSEUM' is not listed. Has the ingestion job run?"; exit 1; }

echo "-- GET $URL/ (frontend)"
curl -fsS -o /dev/null -w 'HTTP %{http_code}, %{size_download} bytes\n' "$URL/"

check_plan() {
  local name="$1" body="$2"
  echo "-- POST $URL/api/plan  [$name]"
  echo "$body" > "$TMP/$name.request.json"
  curl -fsS -X POST "$URL/api/plan" -H 'content-type: application/json' -d "$body" > "$TMP/$name.response.json"
  node tools/check-plan.mjs "$TMP/$name.response.json" --request "$TMP/$name.request.json"
}

check_plan "step-free-45" "{\"museum_id\":\"$MUSEUM\",\"interests\":[],\"time_budget_minutes\":45,\"constraints\":{\"step_free\":true}}"
check_plan "child-45" "{\"museum_id\":\"$MUSEUM\",\"interests\":[],\"time_budget_minutes\":45,\"constraints\":{\"with_young_child\":true}}"
check_plan "wheelchair-90" "{\"museum_id\":\"$MUSEUM\",\"interests\":[],\"time_budget_minutes\":90,\"constraints\":{\"step_free\":true}}"
check_plan "lunch-30" "{\"museum_id\":\"$MUSEUM\",\"interests\":[],\"time_budget_minutes\":30,\"constraints\":{\"short_visit\":true}}"

echo "-- 404 for an unknown museum"
CODE="$(curl -s -o "$TMP/404.json" -w '%{http_code}' -X POST "$URL/api/plan" -H 'content-type: application/json' -d '{"museum_id":"no-such-museum","time_budget_minutes":30}')"
[ "$CODE" = "404" ] || { echo "FAIL: expected 404, got $CODE"; cat "$TMP/404.json"; exit 1; }
node tools/validate-contracts.mjs --instance 'contracts/planner/openapi.yaml#/components/schemas/Error' "$TMP/404.json" >/dev/null || { echo "FAIL: 404 body is not the contract's Error shape"; cat "$TMP/404.json"; exit 1; }
echo "ok   404 with contract Error body"

echo
echo "The step-free 45-minute plan, as returned:"
cat "$TMP/step-free-45.response.json"; echo
echo
echo "verify: all checks passed against $URL"
