"""One-time, idempotent transfer of local records and encrypted Gmail tokens to Firestore."""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud_common import PROJECT, google_credentials, secret_value
from app import config, db, maildb

db.init();maildb.init()
with db.connection() as conn:
    conn.execute("BEGIN")
    profile = json.loads(conn.execute("SELECT body FROM profile WHERE id=1").fetchone()[0])
    jobs = [db.decode(row) for row in conn.execute("SELECT * FROM jobs")]
    names = ["mail_applications", "mail_events", "activity", "usage", "worker_runs", "mail_checks"]
    records = {name: [dict(row) for row in conn.execute("SELECT * FROM " + name)] for name in names}
settings = maildb.settings()
tokens = maildb.secret("gmail_tokens")
# Pause local sending before enabling the independent cloud worker.
maildb.save_settings({"enabled": False, "cloud_migration_paused_local": True})
key = secret_value("jobdesk-token-encryption-key").decode().strip()
credentials = google_credentials()
os.environ.update(STORAGE_BACKEND="firestore", GOOGLE_CLOUD_PROJECT=PROJECT, TOKEN_ENCRYPTION_KEY=key)
from google.cloud import firestore
from app import cloudstore
firestore_client = firestore.Client(project=PROJECT, credentials=credentials)
cloudstore.client = lambda: firestore_client
marker = cloudstore.get("deployment", "migration")
if marker and marker.get("complete"):
    print("Cloud migration already completed; cloud records were left unchanged.")
    raise SystemExit(0)
if not marker:
    remote = cloudstore.get("profile", "main")
    if remote and remote.get("resume"):
        raise RuntimeError("Cloud already has a profile; refusing to overwrite it automatically.")
    cloudstore.ref("deployment", "migration").set({"started": db.now(), "complete": False})
db.init();maildb.init()
db.save_profile(profile)
settings.pop("scheduled_last_date", None)
settings.pop("scheduled_task", None)
settings.pop("oauth_error", None)
settings.pop("cloud_migration_paused_local", None)
settings["scheduled_task"] = {"kind": "cloud", "name": "jobdesk-daily", "time": "09:00", "timezone": "Asia/Kolkata", "installed": True}
maildb.save_settings(settings)
for job in jobs:
    cloudstore.ref("jobs", job["id"]).set(job)
for row in records["mail_applications"]:
    cloudstore.ref("applications", row["id"]).set(row)
    job = next(j for j in jobs if j["id"] == row["job_id"])
    identity, alias = cloudstore.application_keys(job, row["sender"], row["recipient"])
    cloudstore.ref("application_keys", identity).set({"id": row["id"]})
    cloudstore.ref("application_aliases", alias).set({"id": row["id"]})
cloudstore.ref("counters", "applications").set({"last": max((r["id"] for r in records["mail_applications"]), default=0)})
for row in records["mail_events"]:
    cloudstore.ref("mail_events", row["message_id"]).set(row)
for table, collection, field in [("activity", "activity", "id"), ("worker_runs", "runs", "id"), ("mail_checks", "checks", "job_id")]:
    for row in records[table]:
        cloudstore.ref(collection, row[field]).set(row)
totals, quotas = {}, {}
for row in records["usage"]:
    cloudstore.ref("usage", row["id"]).set(row)
    statid = row["provider"] + "_" + row["operation"]
    stat = totals.setdefault(statid, {"provider": row["provider"], "operation": row["operation"], "calls": 0, "input_tokens": 0, "output_tokens": 0})
    stat["calls"] += 1
    stat["input_tokens"] += row["input_tokens"] or 0
    stat["output_tokens"] += row["output_tokens"] or 0
    quota = row["time"][:10] + "_" + row["provider"]
    quotas[quota] = quotas.get(quota, 0) + 1
for key, value in totals.items():cloudstore.ref("usage_totals", key).set(value)
for key, value in quotas.items():cloudstore.ref("provider_quota", key).set({"count": value})
sent_quotas = {}
for row in records["mail_applications"]:
    day = (row.get("sent_at") or row["updated"])[:10]
    if row.get("sent_at") or row["status"] in {"sending", "delivery_unknown"}:
        quota = day + "_" + hashlib.sha256(row["sender"].encode()).hexdigest()
        sent_quotas[quota] = sent_quotas.get(quota, 0) + 1
for key, value in sent_quotas.items():cloudstore.ref("mail_quota", key).set({"attempts": value})
if tokens:
    maildb.save_secret("gmail_tokens", tokens)
cloudstore.ref("deployment", "migration").set({"complete": True, "finished": db.now(), "jobs": len(jobs), "applications": len(records["mail_applications"])})
print(json.dumps({"migration": "complete", "jobs": len(jobs), "email_applications": len(records["mail_applications"]), "gmail_grant_migrated": bool(tokens), "local_automatic_sending": "paused"}), flush=True)
