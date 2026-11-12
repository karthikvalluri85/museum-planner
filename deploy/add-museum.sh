#!/usr/bin/env bash
# Adds or refreshes a museum on a deployed system: publish its id, then start the
# job now instead of waiting for the next Scheduler tick. No redeploy.
#
#   GCP_PROJECT_ID=my-project GCP_REGION=us-central1 ./deploy/add-museum.sh aic
#
# The museum must be one the ingestion image has an adapter and a floor graph for.
# UNTESTED: written without access to a GCP project.

set -euo pipefail

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
: "${GCP_REGION:?Set GCP_REGION}"
MUSEUM="${1:?usage: add-museum.sh <museum id>}"
[[ "$MUSEUM" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "Museum id must match ^[a-z][a-z0-9-]{1,30}\$"; exit 1; }

cd "$(dirname "$0")/.."
eval "$(node tools/deploy-vars.mjs)"

gcloud pubsub topics publish "$PUBSUB_TOPIC" --project "$GCP_PROJECT_ID" \
  --message "{\"museum_id\":\"$MUSEUM\",\"reason\":\"add\"}"
gcloud run jobs execute "$JOB_NAME" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --wait

URL="$(gcloud run services describe "$PLANNER_NAME" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --format='value(status.url)')"
echo "Planner instances cache a museum for up to 10 minutes, so a refresh can take that long to show."
./deploy/verify.sh "$URL" "$MUSEUM"
