"""Deploy a built image to the dedicated Cloud Run web service and worker."""
import argparse
import json
from pathlib import Path

from cloud_common import PROJECT, REGION, gcloud

parser = argparse.ArgumentParser()
parser.add_argument("image")
args = parser.parse_args()
runtime = "jobdesk-runtime@" + PROJECT + ".iam.gserviceaccount.com"
worker = "projects/" + PROJECT + "/locations/" + REGION + "/jobs/jobdesk-daily"
secrets = ",".join(["RIVER_API_KEY=jobdesk-river-api-key:latest", "PARALLEL_API_KEY=jobdesk-parallel-api-key:latest", "GOOGLE_CLIENT_ID=jobdesk-google-client-id:latest", "GOOGLE_CLIENT_SECRET=jobdesk-google-client-secret:latest", "TOKEN_ENCRYPTION_KEY=jobdesk-token-encryption-key:latest"])
common = "STORAGE_BACKEND=firestore,GOOGLE_CLOUD_PROJECT=" + PROJECT + ",JOBDESK_DISABLE_BACKGROUND=1,TZ=Asia/Kolkata"
print("Deploying web service...", flush=True)
gcloud("run", "deploy", "jobdesk", "--image=" + args.image, "--region=" + REGION, "--service-account=" + runtime, "--allow-unauthenticated", "--min-instances=0", "--max-instances=3", "--concurrency=20", "--memory=512Mi", "--cpu=1", "--timeout=900", "--set-env-vars=" + common + ",REQUIRE_AUTH=1,CLOUD_RUN_WORKER=" + worker, "--set-secrets=" + secrets)
url = gcloud("run", "services", "describe", "jobdesk", "--region=" + REGION, "--format=value(status.url)").stdout.decode().strip()
print("Setting the HTTPS callback for " + url, flush=True)
gcloud("run", "services", "update", "jobdesk", "--region=" + REGION, "--update-env-vars=PUBLIC_BASE_URL=" + url + ",GOOGLE_REDIRECT_URI=" + url + "/auth/google/callback")
print("Deploying daily worker...", flush=True)
gcloud("run", "jobs", "deploy", "jobdesk-daily", "--image=" + args.image, "--region=" + REGION, "--service-account=" + runtime, "--command=python", "--args=-m,app.worker,--scheduled", "--tasks=1", "--parallelism=1", "--max-retries=0", "--task-timeout=3600s", "--memory=512Mi", "--cpu=1", "--set-env-vars=" + common + ",REQUIRE_AUTH=1,GOOGLE_REDIRECT_URI=" + url + "/auth/google/callback", "--set-secrets=" + secrets)
gcloud("run", "jobs", "add-iam-policy-binding", "jobdesk-daily", "--region=" + REGION, "--member=serviceAccount:" + runtime, "--role=roles/run.jobsExecutorWithOverrides")
result = {"url": url, "callback": url + "/auth/google/callback", "project": PROJECT, "region": REGION, "image": args.image, "worker": worker}
path = Path(__file__).resolve().parents[1] / "data" / "cloud-deployment.json"
path.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result), flush=True)
