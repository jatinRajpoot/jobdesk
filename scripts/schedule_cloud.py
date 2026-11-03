"""Configure the daily IAM-authenticated Cloud Scheduler trigger, initially paused."""
from cloud_common import PROJECT, REGION, gcloud

identity = "jobdesk-scheduler@" + PROJECT + ".iam.gserviceaccount.com"
if gcloud("iam", "service-accounts", "describe", identity, check=False).returncode:
    gcloud("iam", "service-accounts", "create", "jobdesk-scheduler", "--display-name=Jobdesk daily schedule")
gcloud("run", "jobs", "add-iam-policy-binding", "jobdesk-daily", "--region=" + REGION, "--member=serviceAccount:" + identity, "--role=roles/run.invoker")
exists = gcloud("scheduler", "jobs", "describe", "jobdesk-daily", "--location=" + REGION, check=False).returncode == 0
gcloud("scheduler", "jobs", "update" if exists else "create", "http", "jobdesk-daily", "--location=" + REGION, "--schedule=0 9 * * *", "--time-zone=Asia/Kolkata", "--uri=https://run.googleapis.com/v2/projects/" + PROJECT + "/locations/" + REGION + "/jobs/jobdesk-daily:run", "--http-method=POST", "--oauth-service-account-email=" + identity, "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform", "--message-body={}", "--headers=Content-Type=application/json", "--attempt-deadline=60s", "--max-retry-attempts=0")
gcloud("scheduler", "jobs", "pause", "jobdesk-daily", "--location=" + REGION)
print("Daily cloud schedule configured for 09:00 Asia/Kolkata; paused until migration is verified.", flush=True)
