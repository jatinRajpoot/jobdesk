"""Gmail OAuth and transport. Tokens are encrypted locally and never reach the browser."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from urllib.parse import urlencode, urlsplit

import httpx

from . import db, maildb
from .documents import pdf_resume

SCOPES = ("https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly")
API = "https://gmail.googleapis.com/gmail/v1/users/me"
REDIRECT = os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback")
REFRESH_LOCK = asyncio.Lock()


class GmailError(Exception):
    def __init__(self, message, status=None, uncertain=False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


def client_config():
    if os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
        return {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"]}
    return maildb.secret("google_client")


def import_client(data):
    web = data.get("web")
    if not isinstance(web, dict) or not str(web.get("client_id", "")).endswith(".apps.googleusercontent.com") or not web.get("client_secret"):
        raise ValueError("Upload a Google OAuth Web application client JSON file.")
    if REDIRECT not in web.get("redirect_uris", []):
        raise ValueError(f"Add this authorized redirect URI to the Google client first: {REDIRECT}")
    maildb.save_secret("google_client", {"client_id": web["client_id"], "client_secret": web["client_secret"], "project_id": web.get("project_id", "")})


def status():
    client = client_config()
    tokens = maildb.secret("gmail_tokens")
    return {"configured": bool(client), "connected": bool(tokens and tokens.get("refresh_token")), "email": tokens.get("email") if tokens else None, "expected_email": maildb.settings()["expected_email"], "redirect_uri": REDIRECT, "scopes": list(SCOPES), "mode": "Automatic email applications + reply tracking", "reconnect_required": bool(tokens and tokens.get("reconnect_required")), "last_error": maildb.settings().get("oauth_error", "")}


def begin_oauth():
    client = client_config()
    if not client:
        raise GmailError("Import the Google OAuth client JSON in Connections first.")
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    digest = hashlib.sha256(state.encode()).hexdigest()
    browser_hash = hashlib.sha256(browser.encode()).hexdigest()
    with db.connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE created<?", ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),))
        conn.execute("INSERT INTO oauth_states VALUES(?,?,?,?)", (digest, verifier, db.now(), browser_hash))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    query = {"client_id": client["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": " ".join(SCOPES), "state": state, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "login_hint": maildb.settings()["expected_email"], "code_challenge": challenge, "code_challenge_method": "S256"}
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query), browser


def consume_state(state, browser):
    if not state or not browser:
        raise GmailError("The Gmail connection expired or was not started in this browser. Start again from Connections.")
    digest = hashlib.sha256(state.encode()).hexdigest()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM oauth_states WHERE hash=?", (digest,)).fetchone()
        if not row or row["created"] < (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat() or not secrets.compare_digest(row["browser_hash"], hashlib.sha256(browser.encode()).hexdigest()):
            raise GmailError("Invalid or expired OAuth state. Start again from Connections.")
        conn.execute("DELETE FROM oauth_states WHERE hash=?", (digest,))
    return row["verifier"]


async def token_request(data):
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await client.post("https://oauth2.googleapis.com/token", data=data)
        if response.status_code != 200:
            raise GmailError("Google could not authorize this connection. Reconnect Gmail from Connections.", response.status_code)
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise GmailError("Google authorization could not be reached. Please try connecting again.") from None


async def finish_oauth(code, verifier):
    client = client_config()
    if not client:
        raise GmailError("The OAuth client configuration is missing.")
    tokens = await token_request({"client_id": client["client_id"], "client_secret": client["client_secret"], "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    granted = set(tokens.get("scope", "").split())
    if not set(SCOPES) <= granted:
        await revoke(tokens.get("refresh_token") or tokens.get("access_token"))
        raise GmailError("Both Gmail sending and read-only access are needed. Connect again and select both permissions.")
    profile = await api("GET", "/profile", access_token=tokens["access_token"])
    email = profile.get("emailAddress", "").lower()
    expected = maildb.settings()["expected_email"].lower()
    if email != expected:
        await revoke(tokens.get("refresh_token") or tokens.get("access_token"))
        raise GmailError("This is not Bharti's configured Gmail address. Connect using " + expected + ".")
    old = maildb.secret("gmail_tokens") or {}
    if not tokens.get("refresh_token") and old.get("email") == email:
        tokens["refresh_token"] = old.get("refresh_token")
    if not tokens.get("refresh_token"):
        raise GmailError("Google did not provide offline access. Remove the previous Jobdesk grant and reconnect.")
    tokens.update({"email": email, "expires_at": time.time() + int(tokens.get("expires_in", 3600)), "connected_at": db.now(), "reconnect_required": False})
    maildb.save_secret("gmail_tokens", tokens)
    maildb.save_settings({"oauth_error": ""})
    p = db.profile()
    p["email"] = email
    db.save_profile(p)
    db.event("connection", "Bharti's Gmail connected for application sending and reply checks.")
    return email


async def revoke(token):
    if not token:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post("https://oauth2.googleapis.com/revoke", data={"token": token})
    except httpx.HTTPError:
        pass


async def disconnect():
    tokens = maildb.secret("gmail_tokens") or {}
    await revoke(tokens.get("refresh_token") or tokens.get("access_token"))
    maildb.save_secret("gmail_tokens", None)
    maildb.save_settings({"enabled": False})
    db.event("connection", "Gmail disconnected; automatic applications paused.")


async def access_token(force=False):
    async with REFRESH_LOCK:
        tokens = maildb.secret("gmail_tokens")
        if not tokens or not tokens.get("refresh_token"):
            raise GmailError("Connect Bharti's Gmail in Connections first.")
        if tokens.get("reconnect_required"):
            raise GmailError("Gmail authorization has expired or was revoked. Reconnect in Connections.")
        if not force and tokens.get("expires_at", 0) > time.time() + 90:
            return tokens["access_token"]
        client = client_config()
        if not client:
            raise GmailError("The Google OAuth client configuration is missing.")
        try:
            fresh = await token_request({"client_id": client["client_id"], "client_secret": client["client_secret"], "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token"})
        except GmailError as exc:
            if exc.status in {400, 401}:
                tokens["reconnect_required"] = True
                maildb.save_secret("gmail_tokens", tokens)
            raise
        tokens.update(fresh)
        tokens["expires_at"] = time.time() + int(fresh.get("expires_in", 3600))
        maildb.save_secret("gmail_tokens", tokens)
        return tokens["access_token"]


async def api(method, path, *, params=None, body=None, access_token=None, retry=True):
    token = access_token or await globals()["access_token"]()
    sending = method == "POST" and path == "/messages/send"
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=False) as client:
            response = await client.request(method, API + path, params=params, json=body, headers={"Authorization": "Bearer " + token})
        if response.status_code == 401 and retry and not access_token:
            token = await globals()["access_token"](force=True)
            return await api(method, path, params=params, body=body, access_token=token, retry=False)
        if response.status_code >= 400:
            explanations = {400: "Google rejected the email or request.", 401: "Reconnect Gmail in Connections.", 403: "Google denied this Gmail operation. Check granted permissions and Gmail API enablement.", 404: "This Gmail conversation is no longer available.", 429: "Gmail is rate limiting requests; the queue will wait."}
            raise GmailError(explanations.get(response.status_code, "Gmail is temporarily unavailable."), response.status_code, uncertain=sending and response.status_code >= 500)
        data = response.json()
        if sending and (not data.get("id") or not data.get("threadId")):
            raise GmailError("Google did not return a delivery receipt. The send will be reconciled before any retry.", uncertain=True)
        return data
    except (httpx.HTTPError, ValueError):
        raise GmailError("Gmail could not be reached. A send with an uncertain outcome will not be retried automatically.", uncertain=sending) from None


def message_for(application):
    message = EmailMessage()
    message["From"] = formataddr((db.profile().get("name", ""), application["sender"]))
    message["To"] = application["recipient"]
    message["Subject"] = application["subject"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = application["message_id"]
    message.set_content(application["body"])
    message.add_attachment(pdf_resume(application["resume"], application["subject"]), maintype="application", subtype="pdf", filename="Bharti-Singh-Resume.pdf")
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


async def send(application):
    return await api("POST", "/messages/send", body={"raw": message_for(application)})


async def find_sent(message_id):
    result = await api("GET", "/messages", params={"q": "in:sent rfc822msgid:" + message_id.strip("<>"), "maxResults": 5})
    return result.get("messages", [])


async def thread(thread_id):
    headers = ["From", "To", "Subject", "Message-ID", "In-Reply-To", "References", "Auto-Submitted", "Precedence", "Return-Path"]
    params = [("format", "metadata")] + [("metadataHeaders", h) for h in headers]
    return await api("GET", "/threads/" + thread_id, params=params)
