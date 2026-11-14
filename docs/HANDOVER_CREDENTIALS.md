# Handover: what to run once you have credentials

**Deployed on 2026-11-13, taken offline on 2026-11-14, and the Google Cloud project `museum-planner-514613` was deleted the same day.** Nothing is running and nothing is being billed to it.

Google keeps a deleted project recoverable for about 30 days. Until then, this restores it with everything in it (the service, the job, the database contents, the images):

```bash
gcloud projects undelete museum-planner-514613
```

After that window it is gone for good, and that project ID cannot be reused. To run the system again from scratch, create a new project and follow steps 1 and 2 below with its ID; `deploy/gcp-deploy.sh` and the commands in it are what was used. The table below records what the deleted project contained.

| What | Value |
|---|---|
| Google Cloud project | `museum-planner-514613` |
| Region | `us-central1` |
| Planner (public) | Cloud Run service `planner`, runs as `planner-sa` |
| Ingestion | Cloud Run job `ingestion-job`, runs as `ingestion-sa` |
| Database | Firestore `(default)`, Native mode |
| Messages | Pub/Sub topic `ingest-museum`, subscription `ingest-museum-job` |
| Timer | Cloud Scheduler `ingest-tick`, every 10 minutes, as `scheduler-sa` |
| Data loaded | 976 Art Institute of Chicago works, 41 Met objects |

`deploy/verify.sh` passed against the live address for both museums, and a timer-started run of the job completed. The deployment was done one step at a time with the commands in `deploy/gcp-deploy.sh`, not by running that script in one go. Steps 1 and 2 below are therefore done; they stay here as the record of how to do it again. The code is on GitHub at https://github.com/karthikvalluri85/museum-planner; the deploy workflow in step 3 is not set up.

To redeploy after a code change:

```bash
gcloud run deploy planner --source services/planner --project museum-planner-514613 --region us-central1 --quiet
```

```bash
gcloud run jobs deploy ingestion-job --source services/ingestion-job --project museum-planner-514613 --region us-central1 --quiet
```

```bash
bash deploy/verify.sh "$(gcloud run services describe planner --project museum-planner-514613 --region us-central1 --format='value(status.url)')" aic
```

On this Windows machine, `gcloud` mishandles arguments that contain spaces when started from Git Bash. Run it from PowerShell, or avoid spaces in arguments.

Both Python test suites and the offline integration audit pass locally, using a Python 3.12 installed inside the project folder.

Placeholders used below: `GCP_PROJECT_ID`, `GCP_REGION`, `GITHUB_REPO_URL`, `WORKLOAD_IDENTITY_PROVIDER`.

## Step 0. Run the tests locally (needs no credentials)

This already passes on the build machine. Python 3.12 lives in `.tools/` (the `uv` installer and the interpreter) and `.venv/` (the packages); both are gitignored, and deleting those two folders removes it. The installer also left one launcher file outside the project, `python3.12.exe` in the `.local/bin` folder of your user profile, which is not on PATH and is safe to delete.

The contract gate needs only Node:

```bash
node tools/selftest.mjs
```

```bash
node tools/validate-contracts.mjs
```

The Python suites and the integration audit, using the existing `.venv` (on a fresh machine, create it first with `python -m venv .venv` and install both `requirements-dev.txt` files):

```bash
cd services/planner && ../../.venv/Scripts/python -m pytest -q; cd ../..
```

```bash
cd services/ingestion-job && ../../.venv/Scripts/python -m pytest -q; cd ../..
```

```bash
PYTHON="$(pwd)/.venv/Scripts/python" bash tools/integration-audit.sh
```

With Docker Desktop instead (never run; this machine has no Docker):

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

Last results on the build machine: ingestion job 92 tests passed, planner 187 passed, integration audit all checks passed.

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

1. **The Firestore and Pub/Sub code paths.** Everything was tested through the file-backed store and a fake queue. `FirestoreStore` (both services) and `PubSubPullQueue` have never run, not even against the emulators, because there is no Docker or Java here. `docker compose up` is the first real test of them.
2. **`docker-compose.yml`.** Never run. The emulator image tag, its health checks, and the Pub/Sub REST calls in `pubsub-init` are from memory.
3. **`deploy/gcp-deploy.sh` as a single run, and both workflows.** Every command in the script has now been run by hand against the real project and works, with one correction (the timer's address). The script itself has not been run start to finish, and the GitHub workflows have never run.
4. **Decision D05** (Pub/Sub cannot start a job directly) is still an assumption. The Scheduler-to-job call is confirmed: the address was taken from Google's documentation and a timer-started run completed.
5. **The live museum adapters at full size.** Both ran against the real APIs on 2026-11-13 with small caps (200 AIC works, 25 Met objects); the output passed the contract schemas and produced valid plans. From Cloud Run, the Met API answers HTTP 403 after about 40 object requests, at 10 per second and at 3 per second alike, so the Met has 41 objects. The job therefore collects the Met a batch at a time: every 10-minute timer run adds about 40 objects until the museum reaches its target (decision D22). On 2026-11-14 the Met went from 42 to 213 objects over four timer runs toward a target of 600. To change the target, publish `{"museum_id":"met","max_objects":1200}`; to start over, add `"reason":"refresh"`. Also checked that day: the Met's `/v1/search` returns HTTP 410 (retired) and `/v1.1/search` returns 200, so the adapter uses `v1.1`. The Met blocks scripted clients after a burst of requests (HTTP 403), which is why only 41 real Met records are in the fixtures; a live run from Cloud Run may hit the same wall. The AIC API refuses to page past 1,000 search results, so one run ingests about 1,000 AIC works at most.
6. **The floor graphs.** Hand-built and unverified on site. See [FLOOR_GRAPHS.md](FLOOR_GRAPHS.md). Until someone checks them, the app says so on every plan. Known gaps found by comparing the graphs with real API records in the fixtures:
   - The Met graph was built from a map dated about 2016 to 2020. Live Met records use gallery numbers `341`, `361`, `362`, `363` (the Rockefeller Wing, renumbered when it reopened) and `099`, none of which are in the graph, so objects there cannot be planned yet. Get the current map from metmuseum.org and update `services/ingestion-job/seed/floor_graphs/met.json`.
   - The Met graph marks no place as near both a restroom and a cafe, and only 4 places with seating, so plans for a young child end near a restroom only and limited-stamina plans get few rest stops.
   - Three Met areas are stairs-only in the graph because step-free access could not be confirmed: the Greek and Roman mezzanine, the Modern mezzanine, and the Robert Lehman Collection.

## Behaviors the builders chose that you may want to change

- **Low sensory passes through a tagged space when there is no other way.** The first design refused to route through any loud, bright, or crowded space, and the audit showed that made low-sensory plans nearly empty: at the AIC the only way in from the entrance is through the bright Griffin Court. The planner now never stops in such a space, routes around it when it can, and otherwise passes through and names it in the plan's warnings.

The planner and ingestion code went beyond the brief in a few places. None is wrong; each is a product call.

- **Step-free, but only filler is reachable.** If your interests match objects that all sit behind stairs, the planner answers "no step-free route" and names the stairs, even though it could have returned a step-free plan made only of unrelated highlights. If nothing matches your interests anywhere, it returns the step-free highlights plan.
- **A museum that comes back empty is a failed ingest.** If a run finds zero plannable objects, nothing is written and the message is retried, so an API hiccup cannot wipe a good museum.
- **A plan that fails its own safety check is withheld.** The planner returns HTTP 500 `internal_error` instead of a plan whose step-free claim disagrees with its route.
- **Low-value highlights can pad a plan.** When time remains, highlights that do not match your interests are added, even far away. There is no minimum score.
- **`max_walking_minutes` is ignored unless limited stamina is on.**

## Costs

Designed to stay inside the Cloud Run, Firestore, Pub/Sub, and Cloud Scheduler free tiers at hackathon traffic: the planner scales to zero and is capped at 3 instances, the job runs for seconds when idle, and a planner cold start reads about 8 Firestore documents per museum. Cloud Build and Artifact Registry storage for `--source` deploys are the likeliest small charges. Nothing here has been measured.
