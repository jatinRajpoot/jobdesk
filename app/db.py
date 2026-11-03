import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATA
from .storage import cloud, is_cloud


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    if is_cloud():
        raise RuntimeError("SQLite connections must not be used in the cloud backend.")
    path = os.environ.get("DATABASE_PATH", str(DATA / "jobdesk.sqlite3"))
    with sqlite3.connect(path, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        yield db


@cloud("db")
def init():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, title TEXT NOT NULL, company TEXT NOT NULL,
          location TEXT NOT NULL, description TEXT NOT NULL, source TEXT NOT NULL, published TEXT,
          created TEXT NOT NULL, updated TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'discovered',
          assessment TEXT, assessment_at TEXT, notes TEXT NOT NULL DEFAULT '', draft TEXT, draft_at TEXT
        );
        CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, time TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, time TEXT NOT NULL, provider TEXT NOT NULL, operation TEXT NOT NULL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0);
        """)
        db.execute("INSERT OR IGNORE INTO profile VALUES(1, ?)", (json.dumps({"name": "", "email": "", "headline": "", "location": "", "target_roles": "", "target_locations": "", "resume": "", "preferences": ""}),))


@cloud("db")
def profile():
    with connection() as db:
        return json.loads(db.execute("SELECT body FROM profile WHERE id=1").fetchone()[0])


@cloud("db")
def save_profile(body):
    with connection() as db:
        db.execute("UPDATE profile SET body=? WHERE id=1", (json.dumps(body),))


@cloud("db")
def event(kind, message):
    with connection() as db:
        db.execute("INSERT INTO activity(time,kind,message) VALUES(?,?,?)", (now(), kind, message))


@cloud("db")
def usage(provider, operation, input_tokens=0, output_tokens=0):
    with connection() as db:
        cursor = db.execute("INSERT INTO usage(time,provider,operation,input_tokens,output_tokens) VALUES(?,?,?,?,?)", (now(), provider, operation, input_tokens, output_tokens))
        return cursor.lastrowid


def decode(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("assessment", "draft"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


@cloud("db")
def jobs():
    with connection() as db:
        return [decode(r) for r in db.execute("SELECT * FROM jobs ORDER BY created DESC, rowid DESC")]


@cloud("db")
def job(job_id):
    with connection() as db:
        return decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


@cloud("db")
def upsert(body):
    jid = hashlib.sha256(body["url"].encode()).hexdigest()[:20]
    stamp = now()
    with connection() as db:
        existing = db.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
        if existing:
            # Rediscovery must not erase the user's edited content, research or pipeline state.
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (stamp, jid))
        else:
            db.execute("INSERT INTO jobs(id,url,title,company,location,description,source,published,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)", (jid, body["url"], body["title"], body.get("company", "To confirm"), body.get("location", "Not specified"), body.get("description", ""), body.get("source", "Manual"), body.get("published"), stamp, stamp))
    return job(jid), not bool(existing)


@cloud("db")
def update_job(jid, **values):
    allowed = {"status", "notes", "assessment", "assessment_at", "draft", "draft_at"}
    if not values or not values.keys() <= allowed:
        raise ValueError("Invalid job update")
    encoded = {k: json.dumps(v) if k in {"assessment", "draft"} else v for k, v in values.items()}
    encoded["updated"] = now()
    with connection() as db:
        db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in encoded) + " WHERE id=?", (*encoded.values(), jid))


@cloud("db")
def overview():
    with connection() as db:
        activity = [dict(r) for r in db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 12")]
        usage = [dict(r) for r in db.execute("SELECT provider,operation,COUNT(*) calls,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens FROM usage GROUP BY provider,operation")]
    return {"activity": activity, "usage": usage}


@cloud("db")
def usage_count(provider, since):
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM usage WHERE provider=? AND time>=?", (provider, since)).fetchone()[0]


@cloud("db")
def update_usage(attempt_id, input_tokens, output_tokens):
    with connection() as conn:
        conn.execute("UPDATE usage SET input_tokens=?,output_tokens=? WHERE id=?", (input_tokens, output_tokens, attempt_id))
