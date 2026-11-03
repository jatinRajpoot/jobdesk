"""Persistent mail queue, OAuth state and worker leases. No access/refresh tokens here."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db
from .storage import cloud

DEFAULT_SETTINGS = {"enabled": True, "discover_daily": True, "daily_limit": 5, "minimum_fit_score": 60, "interval_hours": 24, "expected_email": "bhartisingh8397@gmail.com"}


@cloud("mail")
def init():
    with db.connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS mail_settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS oauth_states (hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, created TEXT NOT NULL, browser_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mail_applications (
          id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), recipient TEXT NOT NULL,
          sender TEXT NOT NULL, source_url TEXT NOT NULL, source_quote TEXT NOT NULL,
          status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, sent_at TEXT,
          message_id TEXT, gmail_id TEXT, thread_id TEXT, subject TEXT NOT NULL, body TEXT NOT NULL,
          resume TEXT NOT NULL, profile_fingerprint TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
          last_checked TEXT, reply_at TEXT, reply_from TEXT, reply_subject TEXT,
          response_kind TEXT, UNIQUE(job_id,sender), UNIQUE(message_id)
        );
        CREATE TABLE IF NOT EXISTS mail_events (message_id TEXT PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES mail_applications(id), received TEXT NOT NULL, sender TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_runs (id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT, outcome TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS oauth_secrets (name TEXT PRIMARY KEY, encrypted BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS mail_checks (job_id TEXT PRIMARY KEY REFERENCES jobs(id), checked TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS web_sessions (hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_limits (name TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL);
        """)
        conn.execute("INSERT OR IGNORE INTO mail_settings VALUES(1,?)", (json.dumps(DEFAULT_SETTINGS),))


@cloud("mail")
def settings():
    with db.connection() as conn:
        row = conn.execute("SELECT body FROM mail_settings WHERE id=1").fetchone()
    return {**DEFAULT_SETTINGS, **json.loads(row[0])}


@cloud("mail")
def claim_login_attempt(now, limit=10, window_seconds=60):
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT started,attempts FROM login_limits WHERE name='password'").fetchone()
        started, attempts = (row["started"], row["attempts"]) if row and now < row["started"] + window_seconds else (now, 0)
        if attempts >= limit:
            return False
        conn.execute("INSERT INTO login_limits VALUES('password',?,?) ON CONFLICT(name) DO UPDATE SET started=excluded.started,attempts=excluded.attempts", (started, attempts + 1))
    return True


@cloud("mail")
def save_settings(values):
    with db.connection() as conn:
        conn.execute("UPDATE mail_settings SET body=? WHERE id=1", (json.dumps({**settings(), **values}),))


@cloud("mail")
def applications():
    with db.connection() as conn:
        # Keep full application bodies in their job's editable packet, not the dashboard response.
        return [dict(r) for r in conn.execute("SELECT id,job_id,recipient,sender,status,created,updated,sent_at,subject,source_url,source_quote,last_error,last_checked,reply_at,reply_from,reply_subject,response_kind,thread_id FROM mail_applications ORDER BY id DESC")]


@cloud("mail")
def application(aid):
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM mail_applications WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else None


@cloud("mail")
def update(aid, **fields):
    allowed = {"status", "sent_at", "message_id", "gmail_id", "thread_id", "last_error", "last_checked", "reply_at", "reply_from", "reply_subject", "response_kind"}
    if not fields or not fields.keys() <= allowed:
        raise ValueError("Invalid application update")
    fields["updated"] = db.now()
    with db.connection() as conn:
        conn.execute("UPDATE mail_applications SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), aid))


@cloud("mail")
def enqueue(job, sender, recipient, source_url, source_quote):
    draft = job["draft"]
    with db.connection() as conn:
        duplicate = conn.execute("SELECT a.id FROM mail_applications a JOIN jobs j ON j.id=a.job_id WHERE a.job_id<>? AND lower(a.sender)=? AND lower(a.recipient)=? AND lower(trim(j.title))=lower(trim(?)) AND lower(trim(j.company))=lower(trim(?)) AND a.status NOT IN ('cancelled','failed')", (job["id"], sender.lower(), recipient.lower(), job["title"], job["company"])).fetchone()
        if duplicate:
            raise ValueError("This company and role already have an email application in the tracker, possibly through another job URL.")
        conn.execute("INSERT OR IGNORE INTO mail_applications(job_id,recipient,sender,source_url,source_quote,status,created,updated,subject,body,resume,profile_fingerprint) VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?)", (job["id"], recipient, sender.lower(), source_url, source_quote, db.now(), db.now(), draft["email_subject"], draft["email_body"], draft["resume"], draft["profile_fingerprint"]))
        row = conn.execute("SELECT * FROM mail_applications WHERE job_id=? AND sender=?", (job["id"], sender.lower())).fetchone()
        if row["status"] in {"needs_update", "failed", "cancelled"} and not row["sent_at"] and not row["gmail_id"]:
            conn.execute("UPDATE mail_applications SET recipient=?,source_url=?,source_quote=?,status='queued',updated=?,subject=?,body=?,resume=?,profile_fingerprint=?,message_id=NULL,last_error='' WHERE id=?", (recipient, source_url, source_quote, db.now(), draft["email_subject"], draft["email_body"], draft["resume"], draft["profile_fingerprint"], row["id"]))
            row = conn.execute("SELECT * FROM mail_applications WHERE id=?", (row["id"],)).fetchone()
    return dict(row)


@cloud("mail")
def claim_send(aid, limit):
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM mail_applications WHERE id=?", (aid,)).fetchone()
        if not row or row["status"] != "queued":
            return False
        count = conn.execute("SELECT COUNT(*) FROM mail_applications WHERE sender=? AND (sent_at>=? OR (status IN ('sending','delivery_unknown') AND updated>=?))", (row["sender"], db.now()[:10], db.now()[:10])).fetchone()[0]
        if count >= limit:
            return False
        conn.execute("UPDATE mail_applications SET status='sending',updated=? WHERE id=? AND status='queued'", (db.now(), aid))
    return True


@cloud("mail")
def acquire_lease(name, owner, minutes=30):
    expires = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM worker_leases WHERE expires<?", (db.now(),))
        try:
            conn.execute("INSERT INTO worker_leases VALUES(?,?,?)", (name, owner, expires))
        except sqlite3.IntegrityError:
            return False
    return True


@cloud("mail")
def release_lease(name, owner):
    with db.connection() as conn:
        conn.execute("DELETE FROM worker_leases WHERE name=? AND owner=?", (name, owner))


@cloud("mail")
def last_run():
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM worker_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


@cloud("mail")
def overview():
    with db.connection() as conn:
        checks = [dict(r) for r in conn.execute("SELECT * FROM mail_checks ORDER BY checked DESC LIMIT 50")]
    return {"settings": settings(), "applications": applications(), "last_run": last_run(), "checks": checks}


@cloud("mail")
def check(job_id, status, message):
    with db.connection() as conn:
        conn.execute("INSERT INTO mail_checks VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET checked=excluded.checked,status=excluded.status,message=excluded.message", (job_id, db.now(), status, message))


@cloud("mail")
def secret(name):
    from .config import _crypt
    with db.connection() as conn:
        row = conn.execute("SELECT encrypted FROM oauth_secrets WHERE name=?", (name,)).fetchone()
    return json.loads(_crypt(row[0], decrypt=True)) if row else None


@cloud("mail")
def save_secret(name, value):
    from .config import _crypt
    with db.connection() as conn:
        if value is None:
            conn.execute("DELETE FROM oauth_secrets WHERE name=?", (name,))
        else:
            encrypted = _crypt(json.dumps(value).encode())
            conn.execute("INSERT INTO oauth_secrets VALUES(?,?) ON CONFLICT(name) DO UPDATE SET encrypted=excluded.encrypted", (name, encrypted))


@cloud("mail")
def queued():
    with db.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE status='queued' ORDER BY id")]


@cloud("mail")
def unconfirmed(cutoff):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE status='delivery_unknown' OR (status='sending' AND updated<?)", (cutoff,))]


@cloud("mail")
def reply_targets(sender):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE thread_id IS NOT NULL AND status IN ('sent','replied','automatic_reply','bounced') AND sender=?", (sender,))]


@cloud("mail")
def record_sent(row, receipt):
    stamp = db.now()
    with db.connection() as conn:
        conn.execute("UPDATE mail_applications SET status='sent',sent_at=?,gmail_id=?,thread_id=?,last_error='',updated=? WHERE id=?", (row.get("sent_at") or stamp, receipt["id"], receipt["threadId"], stamp, row["id"]))
        conn.execute("UPDATE jobs SET status='applied',updated=? WHERE id=? AND status IN ('discovered','shortlisted','drafted')", (stamp, row["job_id"]))


@cloud("mail")
def record_event(message_id, aid, received, sender, subject, kind):
    with db.connection() as conn:
        existing = conn.execute("SELECT 1 FROM mail_events WHERE message_id=?", (message_id,)).fetchone()
        if existing:
            return False
        conn.execute("INSERT INTO mail_events VALUES(?,?,?,?,?,?)", (message_id, aid, received, sender, subject, kind))
    return True


@cloud("mail")
def start_run():
    with db.connection() as conn:
        return conn.execute("INSERT INTO worker_runs(started) VALUES(?)", (db.now(),)).lastrowid


@cloud("mail")
def finish_run(run_id, outcome, summary):
    with db.connection() as conn:
        conn.execute("UPDATE worker_runs SET finished=?,outcome=?,summary=? WHERE id=?", (db.now(), outcome, summary, run_id))


@cloud("mail")
def cancel(aid):
    with db.connection() as conn:
        return bool(conn.execute("UPDATE mail_applications SET status='cancelled',updated=? WHERE id=? AND status IN ('queued','needs_update','failed')", (db.now(), aid)).rowcount)


@cloud("mail")
def save_oauth_state(digest, verifier, browser_hash):
    with db.connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE created<?", ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),))
        conn.execute("INSERT INTO oauth_states VALUES(?,?,?,?)", (digest, verifier, db.now(), browser_hash))


@cloud("mail")
def consume_oauth_state(digest, browser_hash):
    import secrets
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM oauth_states WHERE hash=?", (digest,)).fetchone()
        if not row or row["created"] < (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat() or not secrets.compare_digest(row["browser_hash"], browser_hash):
            raise ValueError("Invalid or expired OAuth state")
        conn.execute("DELETE FROM oauth_states WHERE hash=?", (digest,))
    return row["verifier"]


@cloud("mail")
def save_session(digest, email, expires):
    with db.connection() as conn:
        conn.execute("INSERT INTO web_sessions VALUES(?,?,?)", (digest, email, expires))


@cloud("mail")
def session(digest):
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM web_sessions WHERE hash=? AND expires>?", (digest, db.now())).fetchone()
    return dict(row) if row else None


@cloud("mail")
def delete_session(digest):
    with db.connection() as conn:
        conn.execute("DELETE FROM web_sessions WHERE hash=?", (digest,))
