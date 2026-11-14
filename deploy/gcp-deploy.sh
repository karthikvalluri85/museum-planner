#!/usr/bin/env bash
# Deploys the whole system to an existing Google Cloud project.
#
#   GCP_PROJECT_ID=my-project GCP_REGION=us-central1 ./deploy/gcp-deploy.sh
#
# UNTESTED: written without access to a GCP project. Read it before running it.
#
# It does NOT create a project and does NOT enable billing: both must already be
# true, and the script stops if they are not. It asks before enabling APIs and
# before granting IAM roles. Re-running it is safe: every step is create-if-missing
# or a redeploy.
#
# Needs: gcloud (authenticated), node 20+, curl, bash. Run from anywhere.

set -euo pipefail

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to an existing project with billing enabled}"
: "${GCP_REGION:?Set GCP_REGION, for example us-central1}"
ASSUME_YES="${ASSUME_YES:-false}"   # ASSUME_YES=true skips the confirmation prompts (CI)
SEED_MUSEUMS="${SEED_MUSEUMS:-met aic}"

cd "$(dirname "$0")/.."
eval "$(node tools/deploy-vars.mjs)"

P="$GCP_PROJECT_ID"
R="$GCP_REGION"
sa_email() { echo "$1@$P.iam.gserviceaccount.com"; }

confirm() {
  [ "$ASSUME_YES" = "true" ] && return 0
  read -r -p "$1 [y/N] " answer
  [ "$answer" = "y" ] || [ "$answer" = "Y" ] || { echo "Stopped. Nothing further was changed."; exit 1; }
}
step() { printf '\n== %s\n' "$1"; }

step "0. Preconditions"
node tools/selftest.mjs
node tools/validate-contracts.mjs
ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n1)"
[ -n "$ACCOUNT" ] || { echo "gcloud is not authenticated. Run: gcloud auth login"; exit 1; }
gcloud projects describe "$P" --format='value(projectId)' >/dev/null || { echo "Project $P not found or not visible to this account."; exit 1; }
BILLING="$(gcloud billing projects describe "$P" --format='value(billingEnabled)' 2>/dev/null || true)"
[ "$BILLING" = "True" ] || { echo "Billing is not enabled on $P (or this account cannot see it). Enable it yourself, then re-run."; exit 1; }
echo "Authenticated. Project $P exists, billing is on, region $R."

step "1. APIs"
APIS="run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com pubsub.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com"
echo "Will enable (if not already): $APIS"
confirm "Enable these APIs on $P?"
# shellcheck disable=SC2086
gcloud services enable $APIS --project "$P"

step "2. Firestore (Native mode)"
if gcloud firestore databases describe --database='(default)' --project "$P" >/dev/null 2>&1; then
  echo "Database (default) already exists."
else
  confirm "Create the (default) Firestore database in $R? Its location cannot be changed later."
  gcloud firestore databases create --database='(default)' --location="$R" --type=firestore-native --project "$P"
fi

step "3. Service accounts and IAM"
echo "Will create: $PLANNER_SA ($PLANNER_ROLES), $JOB_SA ($JOB_ROLES), $SCHEDULER_SA (run.invoker on the job only)."
confirm "Create these service accounts and grant these roles?"
for sa in "$PLANNER_SA" "$JOB_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts describe "$(sa_email "$sa")" --project "$P" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$sa" --project "$P" --display-name "museum-planner $sa"
done
for role in $PLANNER_ROLES; do
  gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:$(sa_email "$PLANNER_SA")" --role "$role" --condition=None --quiet >/dev/null
done
gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:$(sa_email "$JOB_SA")" --role roles/datastore.user --condition=None --quiet >/dev/null

step "4. Pub/Sub"
gcloud pubsub topics describe "$PUBSUB_TOPIC" --project "$P" >/dev/null 2>&1 ||
  gcloud pubsub topics create "$PUBSUB_TOPIC" --project "$P"
gcloud pubsub subscriptions describe "$PUBSUB_SUBSCRIPTION" --project "$P" >/dev/null 2>&1 ||
  gcloud pubsub subscriptions create "$PUBSUB_SUBSCRIPTION" --topic "$PUBSUB_TOPIC" --ack-deadline 600 --project "$P"
gcloud pubsub subscriptions add-iam-policy-binding "$PUBSUB_SUBSCRIPTION" --project "$P" \
  --member "serviceAccount:$(sa_email "$JOB_SA")" --role roles/pubsub.subscriber --quiet >/dev/null

step "5. Ingestion job"
gcloud run jobs deploy "$JOB_NAME" --source "$JOB_PATH" --project "$P" --region "$R" \
  --service-account "$(sa_email "$JOB_SA")" \
  --tasks "$JOB_TASKS" --parallelism "$JOB_PARALLELISM" --max-retries "$JOB_MAX_RETRIES" \
  --task-timeout "${JOB_TASK_TIMEOUT}s" --cpu "$JOB_CPU" --memory "$JOB_MEMORY" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$P,STORE_BACKEND=firestore,SOURCE_MODE=live,PUBSUB_SUBSCRIPTION=$PUBSUB_SUBSCRIPTION,MAX_OBJECTS=$JOB_MAX_OBJECTS,REFRESH_AFTER_HOURS=$JOB_REFRESH_AFTER_HOURS"
gcloud run jobs add-iam-policy-binding "$JOB_NAME" --project "$P" --region "$R" \
  --member "serviceAccount:$(sa_email "$SCHEDULER_SA")" --role roles/run.invoker --quiet >/dev/null

step "6. Seed: publish museums, run the job once, wait for it"
for museum in $SEED_MUSEUMS; do
  gcloud pubsub topics publish "$PUBSUB_TOPIC" --project "$P" --message "{\"museum_id\":\"$museum\",\"reason\":\"add\"}"
done
gcloud run jobs execute "$JOB_NAME" --project "$P" --region "$R" --wait

step "7. Planner service"
gcloud run deploy "$PLANNER_NAME" --source "$PLANNER_PATH" --project "$P" --region "$R" \
  --service-account "$(sa_email "$PLANNER_SA")" --allow-unauthenticated \
  --min-instances "$PLANNER_MIN_INSTANCES" --max-instances "$PLANNER_MAX_INSTANCES" \
  --concurrency "$PLANNER_CONCURRENCY" --cpu "$PLANNER_CPU" --memory "$PLANNER_MEMORY" --timeout "${PLANNER_TIMEOUT}s" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$P,STORE_BACKEND=firestore"
URL="$(gcloud run services describe "$PLANNER_NAME" --project "$P" --region "$R" --format='value(status.url)')"

step "8. Scheduler tick"
# The documented form (docs.cloud.google.com/run/docs/execute/jobs-on-schedule, read 2026-11-13).
RUN_URI="https://run.googleapis.com/v2/projects/$P/locations/$R/jobs/$JOB_NAME:run"
if gcloud scheduler jobs describe "$SCHEDULER_JOB" --project "$P" --location "$R" >/dev/null 2>&1; then
  echo "Scheduler job $SCHEDULER_JOB already exists."
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" --project "$P" --location "$R" \
    --schedule "*/10 * * * *" --uri "$RUN_URI" --http-method POST \
    --oauth-service-account-email "$(sa_email "$SCHEDULER_SA")"
fi

step "9. Verify with real requests"
./deploy/verify.sh "$URL"

printf '\nDeployed and verified: %s\n' "$URL"
