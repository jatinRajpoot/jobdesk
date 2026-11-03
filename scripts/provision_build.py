from cloud_common import PROJECT, REGION, gcloud

repo = "jobdesk-images"
if gcloud("artifacts", "repositories", "describe", repo, "--location=" + REGION, check=False).returncode:
    gcloud("artifacts", "repositories", "create", repo, "--repository-format=docker", "--location=" + REGION, "--description=Jobdesk application images")
account = gcloud("builds", "get-default-service-account", "--format=value(serviceAccountEmail)").stdout.decode().strip()
if not account:
    # Older SDKs print the address directly without the field projection.
    account = gcloud("builds", "get-default-service-account").stdout.decode().strip().split("/")[-1]
if "@" not in account:
    raise RuntimeError("Could not determine the Cloud Build service account")
gcloud("artifacts", "repositories", "add-iam-policy-binding", repo, "--location=" + REGION, "--member=serviceAccount:" + account, "--role=roles/artifactregistry.writer")
gcloud("projects", "add-iam-policy-binding", PROJECT, "--member=serviceAccount:" + account, "--role=roles/logging.logWriter", "--condition=None")
bucket = "gs://" + PROJECT + "-jobdesk-builds"
if gcloud("storage", "buckets", "describe", bucket, check=False).returncode:
    gcloud("storage", "buckets", "create", bucket, "--location=" + REGION, "--uniform-bucket-level-access")
gcloud("storage", "buckets", "add-iam-policy-binding", bucket, "--member=serviceAccount:" + account, "--role=roles/storage.objectViewer")
print("Cloud Build repository, source bucket and identity are ready.", flush=True)
