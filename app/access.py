"""Private workspace passwords and browser sessions, separate from Gmail authorization."""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from . import maildb

COOKIE = "jobdesk_session"
SESSION_DAYS = 30


def required():
    return os.environ.get("REQUIRE_AUTH") == "1"


def password_hash():
    return os.environ.get("JOBDESK_PASSWORD_HASH") or (maildb.secret("workspace_password") or {}).get("hash", "")


def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, count, salt, expected = encoded.split("$")
        iterations = int(count)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def claim_login_attempt():
    # Shared across instances/IPs because this is one private workspace.
    return maildb.claim_login_attempt(time.time(), limit=10, window_seconds=60)


def issue(email):
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
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
