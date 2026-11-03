import os

import pytest
from fastapi.testclient import TestClient

from app import access, config, db, gmail, maildb
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "access.sqlite3"))
    monkeypatch.setenv("JOBDESK_DISABLE_BACKGROUND", "1")
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    if os.name != "nt":
        monkeypatch.setattr(config, "_crypt", lambda value, decrypt=False: value)
    with TestClient(app, headers={"x-jobdesk": "1"}, base_url="https://127.0.0.1:8000") as client:
        yield client


def test_anonymous_visitors_cannot_read_or_change_personal_data(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    for path in ["/api/state", "/api/export", "/api/docs"]:
        assert client.get(path).status_code == 401
    assert client.put("/api/profile", json={"name": "Unwanted change"}).status_code == 401
    assert db.profile()["name"] == ""
    assert client.get("/login").status_code == 200
    assert client.get("/privacy").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_only_expected_verified_account_session_is_allowed(client):
    wrong = access.issue("not-bharti@example.org")
    client.cookies.set(access.COOKIE, wrong)
    assert client.get("/api/state").status_code == 401
    good = access.issue("bhartisingh8397@gmail.com")
    client.cookies.set(access.COOKIE, good)
    assert client.get("/api/state").status_code == 200
    access.revoke(good)
    assert client.get("/api/state").status_code == 401


def test_cloud_oauth_callback_creates_secure_session_after_identity_check(client, monkeypatch):
    monkeypatch.setattr(gmail, "client_config", lambda: {"client_id": "test.apps.googleusercontent.com", "client_secret": "test-secret"})
    from urllib.parse import urlsplit, parse_qs
    url, browser = gmail.begin_oauth()
    state = parse_qs(urlsplit(url).query)["state"][0]
    client.cookies.set("jobdesk_oauth", browser)
    async def finish(code, verifier):
        return "bhartisingh8397@gmail.com"
    monkeypatch.setattr(gmail, "finish_oauth", finish)
    response = client.get("/auth/google/callback", params={"state": state, "code": "private-test-code"}, follow_redirects=False)
    assert response.status_code == 303
    session_cookie = next(h for h in response.headers.get_list("set-cookie") if h.startswith(access.COOKIE + "="))
    assert "Secure" in session_cookie and "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert "private-test-code" not in response.text + response.headers["location"]
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/state").status_code == 401


def test_cloud_token_encryption_works_without_windows_dpapi(client, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    maildb.save_secret("qa", {"refresh_token": "private-token-test"})
    assert maildb.secret("qa")["refresh_token"] == "private-token-test"
    with db.connection() as conn:
        encrypted = conn.execute("SELECT encrypted FROM oauth_secrets WHERE name='qa'").fetchone()[0]
    assert b"private-token-test" not in encrypted


def test_new_device_sign_in_reuses_saved_gmail_grant(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setattr(gmail, "client_config", lambda: {"client_id": "test.apps.googleusercontent.com", "client_secret": "test-secret"})
    maildb.save_secret("gmail_tokens", {"email": "bhartisingh8397@gmail.com", "refresh_token": "saved-phone-grant"})

    # The laptop has no session, even though the phone connected the mailbox.
    assert client.get("/api/state").status_code == 401
    response = client.get("/auth/google/start", follow_redirects=False)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert "consent" not in query.get("prompt", [""])[0].split()

    async def tokens(data):
        return {"access_token": "new-device-access", "scope": " ".join(gmail.SCOPES)}
    async def profile(*args, **kwargs):
        return {"emailAddress": "bhartisingh8397@gmail.com"}
    monkeypatch.setattr(gmail, "token_request", tokens)
    monkeypatch.setattr(gmail, "api", profile)
    response = client.get("/auth/google/callback", params={"state": query["state"][0], "code": "new-device-code"}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/state").status_code == 200
    assert maildb.secret("gmail_tokens")["refresh_token"] == "saved-phone-grant"
