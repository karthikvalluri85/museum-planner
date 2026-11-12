# Handover: what to run once you have credentials

The system was built local-first on a machine with no Python, no Docker, no GCP project, and no GitHub remote. **Nothing is deployed, and the Python code has never been executed.** This page is the path from here to a verified deployment.

Placeholders used below: `GCP_PROJECT_ID`, `GCP_REGION`, `GITHUB_REPO_URL`, `WORKLOAD_IDENTITY_PROVIDER`.

## Step 0. Run the tests locally (do this first, it needs no credentials)

The contract gate already runs here, with nothing installed:

```bash
node tools/selftest.mjs
```

```bash
node tools/validate-contracts.mjs
```

The Python suites and the integration audit need **either** Python 3.12 **or** Docker Desktop. Pick one.

With Python 3.12:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r services/planner/requirements-dev.txt -r services/ingestion-job/requirements-dev.txt
```

```bash
cd services/planner && ../../.venv/Scripts/python -m pytest -q; cd ../..
```

```bash
cd services/ingestion-job && ../../.venv/Scripts/python -m pytest -q; cd ../..
```

```bash
PYTHON="$(pwd)/.venv/Scripts/python" bash tools/integration-audit.sh
```

With Docker Desktop instead:

```bash
docker compose --profile test run --rm planner-tests
```

```bash
docker compose --profile test run --rm ingestion-tests
```

```bash
docker compose up --build
```

Then open http://localhost:8080 and, in a second terminal, run `bash deploy/verify.sh http://localhost:8080`.

Expect some failures on the first run: the code was desk-checked, not executed. Fix them before going further.

## Step 1. Authenticate and point at your project

The project must already exist with billing enabled. The scripts never create a project or enable billing.

```bash
gcloud auth login
```

```bash
gcloud config set project GCP_PROJECT_ID
```

```bash
gcloud billing projects describe GCP_PROJECT_ID --format="value(billingEnabled)"
```

The last command must print `True`.

## Step 2. Deploy and verify

```bash
GCP_PROJECT_ID=GCP_PROJECT_ID GCP_REGION=GCP_REGION bash deploy/gcp-deploy.sh
```

The script asks before each of these and stops if you say no:

| It asks before | What it does |
|---|---|
| Enabling APIs | Cloud Run, Cloud Build, Artifact Registry, Firestore, Pub/Sub, Cloud Scheduler, IAM |
| Creating Firestore | `(default)` database, Native mode, in `GCP_REGION`. The location is permanent. |
| Creating service accounts | `planner-sa` (Firestore read only), `ingestion-sa` (Firestore write, subscribe), `scheduler-sa` (may start the job, nothing else) |

It then deploys the job, publishes `met` and `aic`, runs the job once, deploys the planner, creates the 10-minute Scheduler tick, and finishes by running `deploy/verify.sh` against the live URL. If verification fails, the system is not deployed in any sense that matters; read the output.

To add a museum later, with no redeploy:

```bash
GCP_PROJECT_ID=GCP_PROJECT_ID GCP_REGION=GCP_REGION bash deploy/add-museum.sh aic
```

## Step 3. Push to GitHub and enable the deploy workflow

```bash
git remote add origin GITHUB_REPO_URL
```

```bash
git push -u origin main
```

Pushing runs `ci` only. `deploy-cloud-run` is manual and does nothing until four repository variables exist (Settings, then Secrets and variables, then Actions, then Variables): `GCP_PROJECT_ID`, `GCP_REGION`, `WORKLOAD_IDENTITY_PROVIDER`, `DEPLOY_SERVICE_ACCOUNT`.

Creating the Workload Identity Federation pieces (no long-lived keys). Replace `OWNER/REPO` with the repository's path:

```bash
gcloud iam workload-identity-pools create github --project GCP_PROJECT_ID --location global --display-name "GitHub Actions"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github --project GCP_PROJECT_ID --location global --workload-identity-pool github --issuer-uri "https://token.actions.githubusercontent.com" --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition "assertion.repository=='OWNER/REPO'"
```

```bash
gcloud iam service-accounts create github-deployer --project GCP_PROJECT_ID
```

```bash
gcloud iam workload-identity-pools providers describe github --project GCP_PROJECT_ID --location global --workload-identity-pool github --format "value(name)"
```

The last command prints the value for `WORKLOAD_IDENTITY_PROVIDER`. `DEPLOY_SERVICE_ACCOUNT` is `github-deployer@GCP_PROJECT_ID.iam.gserviceaccount.com`. That account then needs permission to deploy: `roles/run.admin`, `roles/cloudbuild.builds.editor`, `roles/artifactregistry.writer`, `roles/storage.admin`, `roles/iam.serviceAccountUser` on the project, plus `roles/iam.workloadIdentityUser` on itself for the principal set `attribute.repository/OWNER/REPO`. These are broad roles; grant them yourself rather than by script, and narrow them after the first successful run.

## Things that are unverified, in the order they are likely to bite

1. **All Python code.** Never executed. Step 0 is where that changes.
2. **`docker-compose.yml`.** Never run. The emulator image tag, its health checks, and the Pub/Sub REST calls in `pubsub-init` are from memory.
3. **`deploy/gcp-deploy.sh` and both workflows.** Never run. Shell syntax is checked; the gcloud flags are not.
4. **The Scheduler-to-job call** (step 8 of the deploy script) and decision D05 (Pub/Sub cannot start a job directly). Confirm both against current Cloud Run documentation.
5. **The live museum adapters.** The fixture path is what tests cover. The first live run against the Met and AIC APIs may hit rate limits or query-parameter mistakes.
6. **The floor graphs.** Hand-built and unverified on site. See [FLOOR_GRAPHS.md](FLOOR_GRAPHS.md). Until someone checks them, the app says so on every plan.

## Costs

Designed to stay inside the Cloud Run, Firestore, Pub/Sub, and Cloud Scheduler free tiers at hackathon traffic: the planner scales to zero and is capped at 3 instances, the job runs for seconds when idle, and a planner cold start reads about 8 Firestore documents per museum. Cloud Build and Artifact Registry storage for `--source` deploys are the likeliest small charges. Nothing here has been measured.
