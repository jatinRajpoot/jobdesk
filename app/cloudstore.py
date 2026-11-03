"""Firestore repositories for Cloud Run. Local SQLite stays behind the same API."""
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore


@lru_cache(maxsize=1)
def client():
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"), database=os.environ.get("FIRESTORE_DATABASE", "(default)"))


def coll(name):
    return client().collection(os.environ.get("JOBDESK_COLLECTION_PREFIX", "jobdesk") + "_" + name)


def ref(name, key):
    return coll(name).document(str(key))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def atomic(fn):
    return firestore.transactional(fn)(client().transaction())


def get(name, key, transaction=None):
    return ref(name, key).get(transaction=transaction).to_dict()


def all_docs(name):
    return [d.to_dict() for d in coll(name).stream()]


def create_once(name, key, value):
    try:
        ref(name, key).create(value)
    except AlreadyExists:
        pass


def db_init():
    create_once("profile", "main", {"name": "", "email": "", "headline": "", "location": "", "target_roles": "", "target_locations": "", "resume": "", "preferences": "", "remote_first": False})


def db_profile():
    return get("profile", "main")


def db_save_profile(body):
    ref("profile", "main").set(body)


def db_event(kind, message):
    coll("activity").document().set({"id": uuid.uuid4().hex, "time": stamp(), "kind": kind, "message": message})


def db_usage(provider, operation, input_tokens=0, output_tokens=0):
    attempt = uuid.uuid4().hex
    day = stamp()[:10]
    stat_id = provider + "_" + operation
    def insert(tx):
        stat = get("usage_totals", stat_id, tx) or {"provider": provider, "operation": operation, "calls": 0, "input_tokens": 0, "output_tokens": 0}
        quota = get("provider_quota", day + "_" + provider, tx) or {"count": 0}
        stat.update(calls=stat["calls"] + 1, input_tokens=stat["input_tokens"] + input_tokens, output_tokens=stat["output_tokens"] + output_tokens)
        tx.set(ref("usage", attempt), {"id": attempt, "time": stamp(), "provider": provider, "operation": operation, "input_tokens": input_tokens, "output_tokens": output_tokens})
        tx.set(ref("usage_totals", stat_id), stat)
        tx.set(ref("provider_quota", day + "_" + provider), {"count": quota["count"] + 1})
    atomic(insert)
    return attempt


def db_usage_count(provider, since):
    return (get("provider_quota", since[:10] + "_" + provider) or {}).get("count", 0)


def db_update_usage(attempt_id, input_tokens, output_tokens):
    def update(tx):
        row = get("usage", attempt_id, tx)
        if not row:
            return
        stat_id = row["provider"] + "_" + row["operation"]
        stat = get("usage_totals", stat_id, tx)
        stat["input_tokens"] += input_tokens - row["input_tokens"]
        stat["output_tokens"] += output_tokens - row["output_tokens"]
        tx.update(ref("usage", attempt_id), {"input_tokens": input_tokens, "output_tokens": output_tokens})
        tx.set(ref("usage_totals", stat_id), stat)
    atomic(update)


def db_jobs():
    return sorted(all_docs("jobs"), key=lambda j: (j["created"], j["id"]), reverse=True)


def db_job(job_id):
    return get("jobs", job_id)


def db_upsert(body):
    jid = hashlib.sha256(body["url"].encode()).hexdigest()[:20]
    def upsert(tx):
        existing = get("jobs", jid, tx)
        if existing:
            tx.update(ref("jobs", jid), {"updated": stamp()})
            return {**existing, "updated": stamp()}, False
        row = {"id": jid, "url": body["url"], "title": body["title"], "company": body.get("company", "To confirm"), "location": body.get("location", "Not specified"), "description": body.get("description", ""), "source": body.get("source", "Manual"), "published": body.get("published"), "created": stamp(), "updated": stamp(), "status": "discovered", "assessment": None, "assessment_at": None, "notes": "", "draft": None, "draft_at": None}
        tx.create(ref("jobs", jid), row)
        return row, True
    return atomic(upsert)


def db_update_job(jid, **values):
    allowed = {"status", "notes", "assessment", "assessment_at", "draft", "draft_at"}
    if not values or not values.keys() <= allowed:
        raise ValueError("Invalid job update")
    ref("jobs", jid).update({**values, "updated": stamp()})


def db_overview():
    activity = [d.to_dict() for d in coll("activity").order_by("time", direction=firestore.Query.DESCENDING).limit(12).stream()]
    return {"activity": activity, "usage": all_docs("usage_totals")}


def mail_init():
    from .maildb import DEFAULT_SETTINGS
    create_once("settings", "mail", DEFAULT_SETTINGS)


def mail_settings():
    from .maildb import DEFAULT_SETTINGS
    return {**DEFAULT_SETTINGS, **(get("settings", "mail") or {})}


def mail_save_settings(values):
    ref("settings", "mail").set(values, merge=True)


def mail_applications():
    excluded = {"resume", "body", "profile_fingerprint", "message_id", "gmail_id"}
    return [{k: v for k, v in row.items() if k not in excluded} for row in sorted(all_docs("applications"), key=lambda a: a["id"], reverse=True)]


def mail_application(aid):
    return get("applications", aid)


def mail_update(aid, **fields):
    allowed = {"status", "sent_at", "message_id", "gmail_id", "thread_id", "last_error", "last_checked", "reply_at", "reply_from", "reply_subject", "response_kind"}
    if not fields or not fields.keys() <= allowed:
        raise ValueError("Invalid application update")
    ref("applications", aid).update({**fields, "updated": stamp()})


def application_keys(job, sender, recipient):
    identity = hashlib.sha256((job["id"] + "|" + sender.lower()).encode()).hexdigest()
    alias = hashlib.sha256("|".join([sender.lower(), recipient.lower(), job["company"].strip().lower(), job["title"].strip().lower()]).encode()).hexdigest()
    return identity, alias


def mail_enqueue(job, sender, recipient, source_url, source_quote):
    identity, alias = application_keys(job, sender, recipient)
    draft = job["draft"]
    def enqueue(tx):
        key = get("application_keys", identity, tx)
        guard = get("application_aliases", alias, tx)
        existing = get("applications", key["id"], tx) if key else None
        duplicate = get("applications", guard["id"], tx) if guard and (not key or key["id"] != guard["id"]) else None
        if duplicate and duplicate["status"] not in {"cancelled", "failed"}:
            raise ValueError("This company and role already have an email application in the tracker, possibly through another job URL.")
        if existing and not (existing["status"] in {"needs_update", "failed", "cancelled"} and not existing.get("sent_at") and not existing.get("gmail_id")):
            return existing
        counter = get("counters", "applications", tx) or {"last": 0}
        aid = existing["id"] if existing else counter["last"] + 1
        row = {"id": aid, "job_id": job["id"], "recipient": recipient, "sender": sender.lower(), "source_url": source_url, "source_quote": source_quote, "status": "queued", "created": existing["created"] if existing else stamp(), "updated": stamp(), "subject": draft["email_subject"], "body": draft["email_body"], "resume": draft["resume"], "profile_fingerprint": draft["profile_fingerprint"], "sent_at": None, "message_id": None, "gmail_id": None, "thread_id": None, "last_error": "", "last_checked": None, "reply_at": None, "reply_from": None, "reply_subject": None, "response_kind": None}
        tx.set(ref("applications", aid), row)
        tx.set(ref("application_keys", identity), {"id": aid})
        tx.set(ref("application_aliases", alias), {"id": aid})
        if not existing:
            tx.set(ref("counters", "applications"), {"last": aid})
        return row
    return atomic(enqueue)


def mail_claim_send(aid, limit):
    def claim(tx):
        row = get("applications", aid, tx)
        if not row or row["status"] != "queued":
            return False
        key = stamp()[:10] + "_" + hashlib.sha256(row["sender"].encode()).hexdigest()
        quota = get("mail_quota", key, tx) or {"attempts": 0}
        if quota["attempts"] >= limit:
            return False
        tx.update(ref("applications", aid), {"status": "sending", "updated": stamp()})
        tx.set(ref("mail_quota", key), {"attempts": quota["attempts"] + 1})
        return True
    return atomic(claim)


def mail_acquire_lease(name, owner, minutes=30):
    def acquire(tx):
        lease = get("leases", name, tx)
        if lease and lease["expires"] >= stamp():
            return False
        tx.set(ref("leases", name), {"owner": owner, "expires": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()})
        return True
    return atomic(acquire)


def mail_release_lease(name, owner):
    def release(tx):
        lease = get("leases", name, tx)
        if lease and lease["owner"] == owner:
            tx.delete(ref("leases", name))
    atomic(release)


def mail_last_run():
    rows = list(coll("runs").order_by("started", direction=firestore.Query.DESCENDING).limit(1).stream())
    return rows[0].to_dict() if rows else None


def mail_overview():
    checks = [d.to_dict() for d in coll("checks").order_by("checked", direction=firestore.Query.DESCENDING).limit(50).stream()]
    return {"settings": mail_settings(), "applications": mail_applications(), "last_run": mail_last_run(), "checks": checks}


def mail_check(job_id, status, message):
    ref("checks", job_id).set({"job_id": job_id, "checked": stamp(), "status": status, "message": message})


def mail_secret(name):
    from .config import _crypt
    row = get("secrets", name)
    return json.loads(_crypt(row["encrypted"], decrypt=True)) if row else None


def mail_save_secret(name, value):
    from .config import _crypt
    if value is None:
        ref("secrets", name).delete()
    else:
        ref("secrets", name).set({"encrypted": _crypt(json.dumps(value).encode())})


def mail_queued():
    return sorted([d for d in all_docs("applications") if d["status"] == "queued"], key=lambda d: d["id"])


def mail_unconfirmed(cutoff):
    return [d for d in all_docs("applications") if d["status"] == "delivery_unknown" or (d["status"] == "sending" and d["updated"] < cutoff)]


def mail_reply_targets(sender):
    return [d for d in all_docs("applications") if d.get("thread_id") and d["status"] in {"sent", "replied", "automatic_reply", "bounced"} and d["sender"] == sender]


def mail_record_sent(row, receipt):
    def record(tx):
        job = get("jobs", row["job_id"], tx)
        tx.update(ref("applications", row["id"]), {"status": "sent", "sent_at": row.get("sent_at") or stamp(), "gmail_id": receipt["id"], "thread_id": receipt["threadId"], "last_error": "", "updated": stamp()})
        if job and job["status"] in {"discovered", "shortlisted", "drafted"}:
            tx.update(ref("jobs", row["job_id"]), {"status": "applied", "updated": stamp()})
    atomic(record)


def mail_record_event(message_id, aid, received, sender, subject, kind):
    try:
        ref("mail_events", message_id).create({"message_id": message_id, "application_id": aid, "received": received, "sender": sender, "subject": subject, "kind": kind})
        return True
    except AlreadyExists:
        return False


def mail_start_run():
    rid = uuid.uuid4().hex
    ref("runs", rid).set({"id": rid, "started": stamp(), "finished": None, "outcome": None, "summary": None})
    return rid


def mail_finish_run(run_id, outcome, summary):
    ref("runs", run_id).update({"finished": stamp(), "outcome": outcome, "summary": summary})


def mail_cancel(aid):
    def cancel(tx):
        row = get("applications", aid, tx)
        if not row or row["status"] not in {"queued", "needs_update", "failed"}:
            return False
        tx.update(ref("applications", aid), {"status": "cancelled", "updated": stamp()})
        return True
    return atomic(cancel)


def mail_save_oauth_state(digest, verifier, browser_hash):
    from .config import _crypt
    ref("oauth_states", digest).set({"verifier": _crypt(verifier.encode()), "created": stamp(), "browser_hash": browser_hash})


def mail_consume_oauth_state(digest, browser_hash):
    from .config import _crypt
    def consume(tx):
        row = get("oauth_states", digest, tx)
        if not row or row["created"] < (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat() or not secrets.compare_digest(row["browser_hash"], browser_hash):
            raise ValueError("Invalid or expired OAuth state")
        tx.delete(ref("oauth_states", digest))
        return _crypt(row["verifier"], decrypt=True).decode()
    return atomic(consume)


def mail_save_session(digest, email, expires):
    ref("sessions", digest).set({"email": email, "expires": expires})


def mail_claim_login_attempt(now, limit=10, window_seconds=60):
    def claim(tx):
        row = get("login_limits", "password", tx)
        started, attempts = (row["started"], row["attempts"]) if row and now < row["started"] + window_seconds else (now, 0)
        if attempts >= limit:
            return False
        tx.set(ref("login_limits", "password"), {"started": started, "attempts": attempts + 1})
        return True
    return atomic(claim)


def mail_session(digest):
    row = get("sessions", digest)
    return row if row and row["expires"] > stamp() else None


def mail_delete_session(digest):
    ref("sessions", digest).delete()
