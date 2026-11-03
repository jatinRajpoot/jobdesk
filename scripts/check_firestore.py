"""Opt-in deployment check against an isolated Firestore collection prefix; no email is sent."""
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud_common import PROJECT, google_credentials
from cryptography.fernet import Fernet
from google.cloud import firestore

os.environ.update(STORAGE_BACKEND="firestore", GOOGLE_CLOUD_PROJECT=PROJECT, TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), JOBDESK_COLLECTION_PREFIX="jobdesk_qa_" + uuid.uuid4().hex[:10])
from app import access, cloudstore, db, gmail, maildb, workflows
cloudstore.client = lambda: firestore_client
firestore_client = firestore.Client(project=PROJECT, credentials=google_credentials())
db.init();maildb.init()
p=db.profile();p.update(name="Cloud QA Candidate",email="qa@example.invalid",resume="Synthetic deployment check. No real person, application or email operation.")
db.save_profile(p)
assert db.profile()["name"] == "Cloud QA Candidate"
j, created=db.upsert({"url":"https://example.org/qa/support","title":"QA role","company":"QA employer","location":"Remote","description":"Synthetic QA job"})
assert created
db.update_job(j["id"],notes="Preserve this")
assert not db.upsert(j)[1]
assert db.job(j["id"])["notes"] == "Preserve this"
u=db.usage("river","qa");db.update_usage(u,100,20);db.update_usage(u,100,20)
assert db.usage_count("river",db.now()[:10]) == 1
assert db.overview()["usage"][0]["output_tokens"] == 20
packet={"resume":p["resume"],"email_subject":"QA only","email_body":"Synthetic QA","profile_fingerprint":workflows.profile_fingerprint(p)}
db.update_job(j["id"],draft=packet,status="drafted")
a=maildb.enqueue(db.job(j["id"]),"qa@example.invalid","jobs@example.org",j["url"],"Apply by email to jobs@example.org")
assert maildb.enqueue(db.job(j["id"]),"qa@example.invalid","jobs@example.org",j["url"],"Apply by email to jobs@example.org")["id"] == a["id"]
assert maildb.claim_send(a["id"],1)
assert not maildb.claim_send(a["id"],1)
maildb.record_sent(a,{"id":"qa-message","threadId":"qa-thread"})
assert db.job(j["id"])["status"] == "applied"
assert maildb.record_event("qa-response",a["id"],db.now(),"jobs@example.org","QA","reply")
assert not maildb.record_event("qa-response",a["id"],db.now(),"jobs@example.org","QA","reply")
assert maildb.acquire_lease("qa","first")
assert not maildb.acquire_lease("qa","second")
maildb.release_lease("qa","second")
assert not maildb.acquire_lease("qa","third")
maildb.release_lease("qa","first")
assert maildb.acquire_lease("qa","second")
maildb.save_oauth_state("qa-state","qa-verifier","qa-browser")
try:maildb.consume_oauth_state("qa-state","wrong");raise AssertionError("Wrong browser accepted")
except ValueError:pass
assert maildb.consume_oauth_state("qa-state","qa-browser") == "qa-verifier"
try:maildb.consume_oauth_state("qa-state","qa-browser");raise AssertionError("State replay accepted")
except ValueError:pass
maildb.save_settings({"expected_email":"qa@example.invalid"})
token=access.issue("qa@example.invalid")
assert access.authenticated(token)
access.revoke(token)
assert not access.authenticated(token)
maildb.save_secret("qa",{"private":"synthetic-secret"})
assert maildb.secret("qa")["private"] == "synthetic-secret"
raw=cloudstore.get("secrets","qa")["encrypted"]
assert b"synthetic-secret" not in raw
r=maildb.start_run();maildb.finish_run(r,"ok","QA passed")
assert maildb.last_run()["outcome"] == "ok"
print(json.dumps({"firestore_checks":"passed","isolated_prefix":os.environ["JOBDESK_COLLECTION_PREFIX"],"email_sent":False}),flush=True)
