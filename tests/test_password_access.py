from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import access, gmail, maildb
from app.main import app
from test_cloud_access import client

TEST_PASSWORD = "workspace-test-passphrase"


@pytest.fixture
def password(client, monkeypatch):
    monkeypatch.setenv("JOBDESK_PASSWORD_HASH", access.hash_password(TEST_PASSWORD))
    maildb.save_secret("gmail_tokens", {"email": "bhartisingh8397@gmail.com", "refresh_token": "saved-gmail-grant"})
    return TEST_PASSWORD


def test_password_login_opens_shared_workspace_without_google(client, password):
    assert client.get("/api/state").status_code == 401
    assert client.get("/auth/google/start", follow_redirects=False).headers["location"] == "/login"
    response = client.post("/api/login", json={"password": password})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    cookie = response.headers["set-cookie"]
    for flag in ["Secure", "HttpOnly", "SameSite=lax", "Max-Age=2592000"]:
        assert flag in cookie
    assert client.get("/api/state").status_code == 200
    assert maildb.secret("gmail_tokens")["refresh_token"] == "saved-gmail-grant"
    assert password not in client.get("/api/state").text + client.get("/api/export").text


def test_two_devices_have_independent_sessions_and_keep_gmail_connected(client, password):
    client.post("/api/login", json={"password": password}).raise_for_status()
    with TestClient(app, headers={"x-jobdesk": "1"}, base_url="https://127.0.0.1:8000") as laptop:
        assert laptop.get("/api/state").status_code == 401
        laptop.post("/api/login", json={"password": password}).raise_for_status()
        assert laptop.cookies.get(access.COOKIE) != client.cookies.get(access.COOKIE)
        client.post("/api/logout").raise_for_status()
        assert client.get("/api/state").status_code == 401
        assert laptop.get("/api/state").status_code == 200
        assert maildb.secret("gmail_tokens")["refresh_token"] == "saved-gmail-grant"


def test_wrong_password_never_creates_session_or_echoes_input(client, password):
    response = client.post("/api/login", json={"password": "incorrect-secret-value"})
    assert response.status_code == 401
    assert "incorrect-secret-value" not in response.text
    assert access.COOKIE not in client.cookies
    assert client.get("/api/state").status_code == 401
    assert "Continue with Google" not in client.get("/login").text


def test_gmail_callback_does_not_bypass_workspace_password(client, password, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setattr(gmail, "client_config", lambda: {"client_id": "test.apps.googleusercontent.com", "client_secret": "test-secret"})
    url, browser = gmail.begin_oauth()
    state = parse_qs(urlsplit(url).query)["state"][0]
    client.cookies.set("jobdesk_oauth", browser)
    async def finish(code, verifier):
        return "bhartisingh8397@gmail.com"
    monkeypatch.setattr(gmail, "finish_oauth", finish)
    assert client.get("/auth/google/callback", params={"state": state, "code": "test-code"}, follow_redirects=False).status_code == 303
    assert client.get("/api/state").status_code == 401
    client.post("/api/login", json={"password": password}).raise_for_status()
    response = client.get("/auth/google/start", follow_redirects=False)
    assert urlsplit(response.headers["location"]).hostname == "accounts.google.com"


def test_login_attempt_limit_is_durable_and_recovers(client, password, monkeypatch):
    for _ in range(10):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/login", json={"password": password})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    with TestClient(app, headers={"x-jobdesk": "1"}, base_url="https://127.0.0.1:8000") as other:
        assert other.post("/api/login", json={"password": password}).status_code == 429
    original_time = access.time.time()
    monkeypatch.setattr(access.time, "time", lambda: original_time + 61)
    assert client.post("/api/login", json={"password": password}).status_code == 200


def test_simultaneous_login_attempts_cannot_bypass_shared_limit(client):
    with ThreadPoolExecutor(max_workers=12) as pool:
        allowed = list(pool.map(lambda _: maildb.claim_login_attempt(1000), range(20)))
    assert sum(allowed) == 10
    assert maildb.claim_login_attempt(1060)


def test_password_endpoint_fails_closed_and_rejects_cross_origin_and_bad_input(client, monkeypatch):
    monkeypatch.delenv("JOBDESK_PASSWORD_HASH", raising=False)
    assert client.post("/api/login", json={"password": "test"}).status_code == 503
    assert client.post("/api/login", json={"password": "test"}, headers={"origin": "https://other.example"}).status_code == 403
    for payload in [{}, {"password": 123}, {"password": ""}, {"password": "x" * 257}]:
        response = client.post("/api/login", json=payload)
        assert response.status_code == 422
        assert "x" * 257 not in response.text


def test_password_verifiers_use_unique_salts_and_reject_malformed_values():
    one, two = access.hash_password(TEST_PASSWORD), access.hash_password(TEST_PASSWORD)
    assert one != two and TEST_PASSWORD not in one
    assert access.verify_password(TEST_PASSWORD, one)
    assert not access.verify_password("wrong", one)
    for encoded in ["broken", "other$600000$aa$bb", "pbkdf2_sha256$999999999$aa$bb", "pbkdf2_sha256$600000$zz$bb"]:
        assert not access.verify_password(TEST_PASSWORD, encoded)
