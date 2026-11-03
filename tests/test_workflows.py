import asyncio
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app import config, db, providers, workflows
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(app, headers={"x-jobdesk": "1"}) as client:
        yield client


def add_job(client, url="https://example.com/jobs/backend?utm_source=test"):
    r = client.post("/api/jobs", json={"url": url, "title": "Backend Engineer", "company": "Example", "location": "Remote", "description": "Build Python APIs and relational database services for a small engineering team."})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def test_duplicates_preserve_application_and_research(client):
    job = add_job(client)
    db.update_job(job["id"], status="applied", notes="Already submitted", assessment={"verdict": "needs_review"})
    duplicate = add_job(client, "https://example.com/jobs/backend?utm_source=second#apply")
    assert duplicate["id"] == job["id"]
    assert duplicate["status"] == "applied"
    assert duplicate["notes"] == "Already submitted"
    assert duplicate["assessment"]["verdict"] == "needs_review"
    assert len(client.get("/api/state").json()["jobs"]) == 1


def test_applied_requires_actual_submission_confirmation(client):
    job = add_job(client)
    path = f"/api/jobs/{job['id']}"
    assert client.patch(path, json={"status": "applied"}).status_code == 400
    assert db.job(job["id"])["status"] == "discovered"
    assert client.patch(path, json={"status": "applied", "confirm_submitted": True}).json()["status"] == "applied"


@pytest.mark.parametrize("url", ["file:///C:/secret.txt", "http://localhost/x", "http://127.0.0.1/x", "http://[::1]/x", "https://user:password@example.com", "https://example.com:8787/jobs", "javascript:alert(1)"])
def test_rejects_unsafe_job_destinations(client, url):
    response = client.post("/api/jobs/import", json={"url": url})
    assert response.status_code in {400, 422}


def test_browser_mutations_need_same_origin_and_header(client):
    assert client.put("/api/profile", json={}, headers={"origin": "https://unrelated.example"}).status_code == 403
    with TestClient(app) as raw:
        assert raw.put("/api/profile", json={}).status_code == 403


def test_generation_requires_factual_profile(client, monkeypatch):
    job = add_job(client)
    async def forbidden(*args, **kwargs):
        pytest.fail("No paid model call should run without a factual profile")
    monkeypatch.setattr(providers, "llm", forbidden)
    response = client.post(f"/api/jobs/{job['id']}/generate")
    assert response.status_code == 400
    assert not db.job(job["id"])["draft"]


def test_assessment_discards_invented_quotes_and_downgrades_verdict(client, monkeypatch):
    job = add_job(client)
    async def research(*args, **kwargs):
        return [{"url": "https://example.com/jobs/backend", "title": "Backend Engineer", "excerpts": ["A role for a backend engineer. Location unspecified."]}]
    async def invented(*args, **kwargs):
        return {"verdict": "corroborated", "summary": "Potential match", "fit_score": 99, "fit_reason": "Match", "signals": [{"kind": "support", "claim": "Confirmed on official careers page", "quote": "This exact role is verified open and official.", "source_index": 0}], "uncertainties": [], "recommended_checks": ["Contact the employer"]}
    monkeypatch.setattr(providers, "search", research)
    monkeypatch.setattr(providers, "extract", research)
    monkeypatch.setattr(providers, "llm", invented)
    response = client.post(f"/api/jobs/{job['id']}/assess")
    assert response.status_code == 200, response.text
    assessment = response.json()["assessment"]
    assert assessment["signals"] == []
    assert assessment["verdict"] == "needs_review"
    assert assessment["fit_score"] is None


def test_failed_generation_leaves_existing_packet_unchanged(client, monkeypatch):
    job = add_job(client)
    profile = db.profile()
    profile["resume"] = "Sample candidate. Python developer at Example Labs, 2022-2024. Built APIs and maintained PostgreSQL services."
    db.save_profile(profile)
    original = {"resume": "Original saved resume", "email_body": "Original draft"}
    db.update_job(job["id"], draft=original, status="applied")
    async def incomplete(*args, **kwargs):
        return {"resume": "An incomplete resume response"}
    monkeypatch.setattr(providers, "llm", incomplete)
    response = client.post(f"/api/jobs/{job['id']}/generate")
    assert response.status_code == 502
    assert db.job(job["id"])["draft"] == original
    assert db.job(job["id"])["status"] == "applied"


def test_exports_are_real_documents_and_workspace_never_contains_credentials(client, monkeypatch):
    job = add_job(client)
    text = "SAMPLE CANDIDATE\ncandidate@example.com\n\nEXPERIENCE\nPython Developer, Example Labs\n- Built API services with PostgreSQL."
    db.update_job(job["id"], draft={"resume": text})
    from pypdf import PdfReader
    from docx import Document
    pdf = client.get(f"/api/jobs/{job['id']}/resume/pdf")
    assert pdf.status_code == 200
    assert "SAMPLE CANDIDATE" in PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
    docx = client.get(f"/api/jobs/{job['id']}/resume/docx")
    assert "SAMPLE CANDIDATE" in "\n".join(p.text for p in Document(BytesIO(docx.content)).paragraphs)
    monkeypatch.setattr(config, "parallel_key", lambda: "private-test-credential")
    monkeypatch.setattr(config, "river_credentials", lambda: {"key": "river-private-test", "model": "test-model", "origin": "Test"})
    for path in ["/api/state", "/api/export"]:
        response = client.get(path)
        assert "private-test-credential" not in response.text
        assert "river-private-test" not in response.text


def test_unreadable_resume_upload_does_not_overwrite_profile(client):
    response = client.post("/api/profile/import", files={"file": ("broken.pdf", b"not a PDF", "application/pdf")})
    assert response.status_code == 400
    assert db.profile()["resume"] == ""


def test_provider_error_does_not_echo_credentials(client, monkeypatch):
    async def failed(*args, **kwargs):
        raise providers.ProviderError("Parallel: The API key was not accepted. Check Connections.")
    monkeypatch.setattr(providers, "search", failed)
    response = client.post("/api/connections/parallel/test")
    assert response.status_code == 502
    assert "not accepted" in response.json()["detail"]


def test_invalid_credential_is_not_reflected_in_validation_errors(client):
    response = client.put("/api/connections/parallel", json={"key": "shortkey"})
    assert response.status_code == 422
    assert "shortkey" not in response.text


def test_remote_search_rejects_hybrid_unsupported_and_location_mismatch(client, monkeypatch):
    p = db.profile()
    p.update({"headline": "Fresher", "preferences": "Remote from India; target email and chat support."})
    db.save_profile(p)
    excerpt = "This is a fully remote role available from India. Freshers welcome."
    async def research(*args, **kwargs):
        assert "Fully remote" in args[1]
        return [{"url": f"https://example.com/jobs/{i}", "title": "Support", "excerpts": [excerpt]} for i in range(4)]
    async def organized(system, payload, *args, **kwargs):
        assert payload["remote_only"] is True
        assert payload["candidate"]["preferences"] == p["preferences"]
        base = {"title": "Support trainee", "company": "Example", "location": "India", "description": excerpt, "is_specific_job": True, "matches_preferences": True, "work_mode": "remote", "remote_evidence": "fully remote role available from India"}
        return {"jobs": [
            {**base, "source_index": 0},
            {**base, "source_index": 1, "work_mode": "hybrid"},
            {**base, "source_index": 2, "remote_evidence": "Work from anywhere in the world with no restrictions"},
            {**base, "source_index": 3, "matches_preferences": False},
        ]}
    monkeypatch.setattr(providers, "search", research)
    monkeypatch.setattr(providers, "llm", organized)
    response = client.post("/api/discover", json={"roles": "Email support", "location": "India", "remote": True})
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1
    assert db.jobs()[0]["url"] == "https://example.com/jobs/0"


def test_remote_default_persists_with_profile_changes(client):
    response = client.put("/api/profile", json={"name": "Test candidate", "remote_first": True})
    assert response.status_code == 200
    profile = client.get("/api/state").json()["profile"]
    assert profile["remote_first"] is True
    profile["headline"] = "Fresher seeking support roles"
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.get("/api/state").json()["profile"]["remote_first"] is True
