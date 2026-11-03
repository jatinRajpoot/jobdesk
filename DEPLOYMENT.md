# Google Cloud deployment

The deployment uses project `bharti` (`analog-height-492914-t1`) and region `asia-south1` (Mumbai).

| Resource | Purpose |
| --- | --- |
| Cloud Run service `jobdesk` | HTTPS dashboard, profile and Gmail OAuth callback |
| Firestore Standard `(default)` | Durable profile, jobs, mail queue, locks, sessions and tracking |
| Secret Manager `jobdesk-*` | Provider keys, Google OAuth client and token encryption key |
| Cloud Run job `jobdesk-daily` | Durable daily discovery, email applications and reply checks |
| Cloud Scheduler `jobdesk-daily` | Runs the job at 09:00 Asia/Kolkata |
| Artifact Registry `jobdesk-images` | Container images built through Cloud Build |

The web endpoint accepts public HTTPS requests so Google can reach the OAuth callback, but personal pages and every data/action API require Bharti's authenticated session. Session cookies are Secure, HttpOnly and SameSite=Lax; Firestore stores only their hashes. The configured Gmail identity is checked before a session is created. Gmail's send and read-only scopes remain unchanged. No Google Cloud IAM tokens, provider keys or Gmail credentials are exposed to browsers.

The runtime service account has Firestore access and access to the five named secrets. It can execute the single email worker with argument overrides. The scheduler uses a separate identity allowed to invoke that worker. The provider and encryption keys are not included in the image, source upload or Git repository.

Google OAuth must list the deployed service URL followed by `/auth/google/callback` as an authorized redirect URI on the existing Web client. Keep the local callback too if localhost testing is needed. Bharti remains the sole test user. The app stays in Google Testing mode, so Gmail authorization normally needs renewal after seven days.

## Build and deploy from the authenticated gcloud CLI

The helper scripts use the existing CLI identity without printing access tokens or secret values. Run them from the repository root with the local virtual environment. Credentials are sourced from the existing local encrypted configuration and transferred to Secret Manager through stdin.

```powershell
.\.venv\Scripts\python.exe scripts/prepare_cloud.py
.\.venv\Scripts\python.exe scripts/provision_build.py
gcloud builds submit . --config=cloudbuild.yaml --substitutions=_IMAGE=asia-south1-docker.pkg.dev/analog-height-492914-t1/jobdesk-images/jobdesk:YOUR_TAG --gcs-source-staging-dir=gs://analog-height-492914-t1-jobdesk-builds/source --project=analog-height-492914-t1
.\.venv\Scripts\python.exe scripts/deploy_cloud.py asia-south1-docker.pkg.dev/analog-height-492914-t1/jobdesk-images/jobdesk:YOUR_TAG
```

`data/cloud-deployment.json` records the deployment URL and image reference. The folder is ignored. The initial database is created separately with:

```powershell
gcloud firestore databases create --database="(default)" --location=asia-south1 --type=firestore-native --delete-protection --project=analog-height-492914-t1
```

For the first deployment only, `scripts/migrate_cloud.py` transfers the existing local profile, jobs, mail history and Gmail grant if present. It pauses local automatic sending. It does not copy browser sessions or incomplete OAuth states. The migration is marked complete and does not overwrite later cloud edits when rerun. Local SQLite data is retained as a backup. Disable the old Windows daily task when switching to the cloud scheduler to prevent independent local runs.

```powershell
.\.venv\Scripts\python.exe scripts/migrate_cloud.py
.\.venv\Scripts\python.exe scripts/schedule_cloud.py
gcloud scheduler jobs resume jobdesk-daily --location=asia-south1 --project=analog-height-492914-t1
```

The web service scales to zero; daily work runs in the separate job and does not rely on background execution in a web instance. Manual Run/Check replies actions also start the durable job. Transactional queue claims, aliases and leases coordinate instances. The daily cloud send quota conservatively counts claimed send attempts, including uncertain outcomes.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_firestore.py
```

The second command uses an isolated QA collection prefix and synthetic records to exercise actual Firestore transactions; it sends no email. The deployed health endpoint is public, while `/api/state` and `/api/export` must return HTTP 401 without a valid session. OAuth start must redirect to Google with HTTPS callback, PKCE, offline access and exactly the two configured Gmail scopes. A live Gmail authorization or send/receive test requires Bharti's consent.

Cloud Run, Firestore, image storage, builds, Secret Manager and Scheduler use metered Google Cloud billing. The service has zero minimum instances, at most three instances and a default daily email limit of five. No continuously running SQL instance is used.
