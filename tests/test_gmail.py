import asyncio
import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import automation, config, db, gmail, maildb, workflows
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mail-tests.sqlite3"))
    monkeypatch.setenv("JOBDESK_DISABLE_BACKGROUND", "1")
    if os.name != "nt":
        monkeypatch.setattr(config, "_crypt", lambda value, decrypt=False: value)
    with TestClient(app, headers={"x-jobdesk": "1"}) as client:
        yield client


def connect():
    maildb.save_secret("google_client", {"client_id": "test.apps.googleusercontent.com", "client_secret": "client-secret-test"})
    maildb.save_secret("gmail_tokens", {"email": "bhartisingh8397@gmail.com", "access_token": "access-token-test", "refresh_token": "refresh-token-test", "expires_at": time.time() + 3600})
    p = db.profile()
    p.update({"name": "Bharti Singh", "email": "bhartisingh8397@gmail.com", "resume": "Bharti Singh\nBCA graduate, pursuing MCA. Python, Django and Microsoft Office. Interested in remote support roles."})
    db.save_profile(p)


def queued(index=0):
    job, _ = db.upsert({"url": f"https://jobs.example.org/support/{index}", "title": f"Support trainee {index}", "company": "Example", "location": "Remote India", "description": "Apply by email with your CV. Freshers welcome."})
    fingerprint = workflows.profile_fingerprint(db.profile())
    packet = {"resume": db.profile()["resume"], "email_subject": "Support trainee application", "email_body": "Hello, please consider my application for the support trainee role.", "cover_letter": "", "answers": [], "changes": [], "missing_information": [], "profile_fingerprint": fingerprint}
    db.update_job(job["id"], draft=packet, assessment={"verdict": "corroborated", "fit_score": 85, "profile_fingerprint": fingerprint}, assessment_at=db.now(), status="drafted")
    row = maildb.enqueue(db.job(job["id"]), "bhartisingh8397@gmail.com", "jobs@example.org", job["url"], "Send your CV to jobs@example.org to apply.")
    return row


def test_oauth_start_requests_only_needed_scopes_with_pkce_and_cookie(client):
    connect()
    r = client.get("/auth/google/start", headers={"host": "127.0.0.1:8000"}, follow_redirects=False)
    assert r.status_code == 303
    parts = urlsplit(r.headers["location"])
    assert parts.netloc == "accounts.google.com"
    q = parse_qs(parts.query)
    assert set(q["scope"][0].split()) == set(gmail.SCOPES)
    assert q["access_type"] == ["offline"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["login_hint"] == ["bhartisingh8397@gmail.com"]
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "client-secret-test" not in r.headers["location"]


@pytest.mark.parametrize("saved", [None, {"email": "bhartisingh8397@gmail.com"}, {"email": "wrong@example.org", "refresh_token": "wrong"}, {"email": "bhartisingh8397@gmail.com", "refresh_token": "expired", "reconnect_required": True}])
def test_initial_or_invalid_grant_requests_offline_consent(client, saved):
    connect()
    maildb.save_secret("gmail_tokens", saved)
    response = client.get("/auth/google/start", headers={"host": "127.0.0.1:8000"}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers["location"]).query)["prompt"] == ["consent"]


def test_explicit_renewal_requests_consent_and_survives_canonical_redirect(client):
    connect()
    response = client.get("/auth/google/start?reconnect=true", follow_redirects=False)
    assert response.headers["location"] == gmail.REDIRECT.replace("/callback", "/start") + "?reconnect=true"
    response = client.get(response.headers["location"], follow_redirects=False)
    assert parse_qs(urlsplit(response.headers["location"]).query)["prompt"] == ["consent"]


def test_oauth_state_is_bound_to_browser_expires_and_cannot_be_replayed(client):
    connect()
    url, cookie = gmail.begin_oauth()
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(gmail.GmailError):
        gmail.consume_state(state, "wrong-browser")
    assert gmail.consume_state(state, cookie)
    with pytest.raises(gmail.GmailError):
        gmail.consume_state(state, cookie)
    url, cookie = gmail.begin_oauth()
    state = parse_qs(urlsplit(url).query)["state"][0]
    with db.connection() as conn:
        conn.execute("UPDATE oauth_states SET created=?", ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),))
    with pytest.raises(gmail.GmailError):
        gmail.consume_state(state, cookie)


def test_invalid_callback_does_not_exchange_code_or_reflect_it(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("An invalid callback must not exchange an authorization code")
    monkeypatch.setattr(gmail, "finish_oauth", forbidden)
    r = client.get("/auth/google/callback?state=bad&code=private-code-test", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("gmail=failed")
    assert "private-code-test" not in r.text + str(r.headers)


def test_oauth_rejects_wrong_mailbox_without_replacing_bharti_tokens(client, monkeypatch):
    connect()
    revoked = []
    async def tokens(data):
        return {"access_token": "wrong-account-access", "refresh_token": "wrong-account-refresh", "scope": " ".join(gmail.SCOPES)}
    async def profile(*args, **kwargs):
        return {"emailAddress": "different-account@example.org"}
    async def revoke(token):
        revoked.append(token)
    monkeypatch.setattr(gmail, "token_request", tokens)
    monkeypatch.setattr(gmail, "api", profile)
    monkeypatch.setattr(gmail, "revoke", revoke)
    with pytest.raises(gmail.GmailError):
        asyncio.run(gmail.finish_oauth("authorization-code-test", "verifier-test"))
    assert revoked == ["wrong-account-refresh"]
    assert maildb.secret("gmail_tokens")["email"] == "bhartisingh8397@gmail.com"


def test_oauth_requires_both_permissions(client, monkeypatch):
    connect()
    async def tokens(data):
        return {"access_token": "test", "scope": gmail.SCOPES[0]}
    async def revoke(token):
        pass
    monkeypatch.setattr(gmail, "token_request", tokens)
    monkeypatch.setattr(gmail, "revoke", revoke)
    with pytest.raises(gmail.GmailError, match="Both Gmail"):
        asyncio.run(gmail.finish_oauth("code-test", "verifier-test"))


def test_tokens_and_client_secrets_never_appear_in_app_state_or_export(client):
    connect()
    for path in ["/api/state", "/api/export"]:
        r = client.get(path)
        assert r.status_code == 200
        for secret in ["access-token-test", "refresh-token-test", "client-secret-test"]:
            assert secret not in r.text


def test_oauth_client_import_rejects_wrong_callback_without_echoing_secret(client):
    data = {"web": {"client_id": "test.apps.googleusercontent.com", "client_secret": "private-client-secret-test", "redirect_uris": ["https://other.example.org/callback"]}}
    r = client.post("/api/connections/google/client", files={"file": ("client.json", json.dumps(data), "application/json")})
    assert r.status_code == 400
    assert "private-client-secret-test" not in r.text


def test_successful_send_attaches_resume_marks_applied_and_is_not_duplicated(client, monkeypatch):
    connect()
    row = queued()
    calls = []
    async def send(application):
        calls.append(application)
        raw = gmail.message_for(application)
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        assert message["To"] == "jobs@example.org"
        assert "bhartisingh8397@gmail.com" in message["From"]
        assert message["Message-ID"]
        attachments = list(message.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_content_type() == "application/pdf"
        assert attachments[0].get_payload(decode=True).startswith(b"%PDF")
        return {"id": "gmail-sent-test", "threadId": "thread-test"}
    monkeypatch.setattr(gmail, "send", send)
    assert asyncio.run(automation.send_queued()) == 1
    assert asyncio.run(automation.send_queued()) == 0
    assert len(calls) == 1
    assert maildb.application(row["id"])["status"] == "sent"
    assert db.job(row["job_id"])["status"] == "applied"


def test_ambiguous_send_is_reconciled_without_retrying(client, monkeypatch):
    connect()
    row = queued()
    calls = []
    async def send(application):
        calls.append(application)
        raise gmail.GmailError("Transport timed out", uncertain=True)
    async def find(message_id):
        assert message_id.startswith("<jobdesk-")
        return [{"id": "accepted-before-timeout", "threadId": "tracked-thread"}]
    monkeypatch.setattr(gmail, "send", send)
    monkeypatch.setattr(gmail, "find_sent", find)
    assert asyncio.run(automation.send_queued()) == 0
    assert maildb.application(row["id"])["status"] == "delivery_unknown"
    assert asyncio.run(automation.send_queued()) == 0
    assert asyncio.run(automation.reconcile()) == 1
    assert len(calls) == 1
    assert db.job(row["job_id"])["status"] == "applied"


def test_daily_limit_and_changed_profile_prevent_unwanted_sends(client, monkeypatch):
    connect()
    first, second = queued(0), queued(1)
    maildb.save_settings({"daily_limit": 1})
    async def send(row):
        return {"id": str(row["id"]), "threadId": "thread" + str(row["id"])}
    monkeypatch.setattr(gmail, "send", send)
    assert asyncio.run(automation.send_queued()) == 1
    assert maildb.application(second["id"])["status"] == "queued"
    p = db.profile();p["resume"] += "\nUpdated profile facts.";db.save_profile(p)
    assert asyncio.run(automation.send_queued()) == 0
    assert maildb.application(second["id"])["status"] == "needs_update"


def test_reply_and_auto_ack_are_distinct_and_idempotent(client, monkeypatch):
    connect()
    row = queued()
    sent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    maildb.update(row["id"], status="sent", sent_at=sent, gmail_id="outgoing", thread_id="thread", message_id="<application-id@test.local>")
    async def thread(tid):
        stamp = str(int(time.time() * 1000))
        def message(mid, auto=False):
            headers = [{"name": "From", "value": "jobs@example.org"}, {"name": "Subject", "value": "Re: Your application"}, {"name": "In-Reply-To", "value": "<application-id@test.local>"}]
            if auto:
                headers.append({"name": "Auto-Submitted", "value": "auto-replied"})
            return {"id": mid, "internalDate": stamp, "labelIds": ["INBOX"], "payload": {"headers": headers}}
        return {"messages": [message("ack", True), message("response")]}
    monkeypatch.setattr(gmail, "thread", thread)
    assert asyncio.run(automation.sync_replies()) == 2
    assert maildb.application(row["id"])["status"] == "replied"
    assert asyncio.run(automation.sync_replies()) == 0
    assert maildb.application(row["id"])["status"] == "replied"


def test_a_different_application_in_the_same_thread_is_not_a_reply(client, monkeypatch):
    connect()
    row = queued()
    maildb.update(row["id"], status="sent", sent_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(), gmail_id="outgoing", thread_id="thread", message_id="<this-application@test.local>")
    async def thread(tid):
        return {"messages": [{"id": "another-reply", "internalDate": str(int(time.time()*1000)), "payload": {"headers": [{"name": "From", "value": "jobs@example.org"}, {"name": "In-Reply-To", "value": "<another-application@test.local>"}]}}]}
    monkeypatch.setattr(gmail, "thread", thread)
    assert asyncio.run(automation.sync_replies()) == 0
    assert maildb.application(row["id"])["status"] == "sent"


def test_bounce_is_not_reported_as_a_recruiter_reply(client):
    assert automation.classify_response({"from": "Mail Delivery System <mailer-daemon@example.org>"}) == "bounce"
    assert automation.classify_response({"from": "jobs@example.org", "auto-submitted": "auto-generated"}) == "automatic_reply"


def test_unknown_application_email_is_never_guessed(client, monkeypatch):
    connect()
    row = queued()
    async def extract(url):
        return [{"url": url, "title": "Support job", "excerpts": ["Apply through our careers form. Privacy contact: privacy@example.org."]}]
    async def invented(*args, **kwargs):
        return {"accepts_email_applications": True, "email": "jobs@example.org", "source_index": 0, "quote": "Send your CV to jobs@example.org to apply."}
    monkeypatch.setattr(automation.providers, "extract", extract)
    monkeypatch.setattr(automation.providers, "llm", invented)
    with pytest.raises(ValueError, match="validated"):
        asyncio.run(automation.application_contact(db.job(row["job_id"])))


def test_worker_lease_prevents_concurrent_application_runs(client):
    assert maildb.acquire_lease("gmail_automation", "first")
    assert not maildb.acquire_lease("gmail_automation", "second")
    maildb.release_lease("gmail_automation", "second")
    assert not maildb.acquire_lease("gmail_automation", "third")
    maildb.release_lease("gmail_automation", "first")
    assert maildb.acquire_lease("gmail_automation", "second")


def test_alias_url_for_same_company_and_role_cannot_send_second_application(client):
    connect()
    original = queued()
    original_job = db.job(original["job_id"])
    alias, _ = db.upsert({**original_job, "url": "https://another.example.org/same-job"})
    db.update_job(alias["id"], draft=original_job["draft"])
    with pytest.raises(ValueError, match="already have an email application"):
        maildb.enqueue(db.job(alias["id"]), original["sender"], original["recipient"], original["source_url"], original["source_quote"])


def test_calendar_task_runs_daily_without_finish_time_drift(client, monkeypatch, tmp_path):
    from app import worker
    monkeypatch.setattr(config, "DATA", tmp_path)
    calls = []
    async def cycle(force=False):
        calls.append(force)
        return {"outcome": "ok"}
    monkeypatch.setattr(automation, "run_cycle", cycle)
    assert asyncio.run(worker.main(scheduled=True))["outcome"] == "ok"
    assert calls == [True]
    assert asyncio.run(worker.main(scheduled=True))["outcome"] == "already_checked_today"
    assert calls == [True]
