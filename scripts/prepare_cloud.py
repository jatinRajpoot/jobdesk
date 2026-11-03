"""Provision the dedicated runtime identity and migrate API credentials to Secret Manager."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloud_common import PROJECT, ensure_secret, gcloud
from app import config, db, gmail, maildb

db.init()
maildb.init()
runtime = "jobdesk-runtime@" + PROJECT + ".iam.gserviceaccount.com"
if gcloud("iam", "service-accounts", "describe", runtime, check=False).returncode:
    gcloud("iam", "service-accounts", "create", "jobdesk-runtime", "--display-name=Jobdesk runtime")
gcloud("projects", "add-iam-policy-binding", PROJECT, "--member=serviceAccount:" + runtime, "--role=roles/datastore.user", "--condition=None")

river = config.river_credentials()
google = gmail.client_config()
parallel = config.parallel_key()
if not river["key"] or not google or not parallel:
    raise RuntimeError("A local provider credential is missing; nothing can be deployed without it.")
secrets = {
    "jobdesk-river-api-key": river["key"].encode(),
    "jobdesk-parallel-api-key": parallel.encode(),
    "jobdesk-google-client-id": google["client_id"].encode(),
    "jobdesk-google-client-secret": google["client_secret"].encode(),
}
for name, value in secrets.items():
    ensure_secret(name, value)
from cryptography.fernet import Fernet
name = "jobdesk-token-encryption-key"
if gcloud("secrets", "describe", name, check=False).returncode:
    ensure_secret(name, Fernet.generate_key())
else:
    ensure_secret(name)
for name in [*secrets, "jobdesk-token-encryption-key"]:
    gcloud("secrets", "add-iam-policy-binding", name, "--member=serviceAccount:" + runtime, "--role=roles/secretmanager.secretAccessor", "--condition=None")
print("Runtime identity and five secrets are ready; no secret values were displayed.", flush=True)
