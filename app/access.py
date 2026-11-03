"""Cloud-only private browser sessions, issued after Google's verified Gmail identity."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from . import maildb

COOKIE = "jobdesk_session"


def required():
    return os.environ.get("REQUIRE_AUTH") == "1"


def issue(email):
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    maildb.save_session(digest, email.lower(), expires)
    return token


def authenticated(token):
    if not token:
        return False
    row = maildb.session(hashlib.sha256(token.encode()).hexdigest())
    return bool(row and row["email"].lower() == maildb.settings()["expected_email"].lower())


def revoke(token):
    if token:
        maildb.delete_session(hashlib.sha256(token.encode()).hexdigest())
