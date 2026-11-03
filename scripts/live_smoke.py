"""Opt-in, paid integration smoke test using an isolated synthetic candidate."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QA = ROOT / "data" / "qa"
QA.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
os.environ["DATABASE_PATH"] = str(QA / f"live-{stamp}.sqlite3")

from fastapi.testclient import TestClient
from app.main import app

report = {"synthetic_candidate": True, "started_at": stamp, "steps": []}


def step(client, method, path, body=None):
    response = client.request(method, path, json=body)
    if response.status_code != 200:
        print(path, response.status_code, response.text[:500], flush=True)
        raise RuntimeError("Live workflow failed")
    report["steps"].append({"path": path, "status": response.status_code})
    print(path + ": passed", flush=True)
    return response


with TestClient(app, headers={"x-jobdesk": "1"}) as client:
    step(client, "POST", "/api/connections/parallel/test")
    step(client, "POST", "/api/connections/river/test")
    step(client, "PUT", "/api/profile", {"name": "Local QA Candidate", "email": "qa@example.com", "headline": "Python Backend Developer", "location": "India", "target_roles": "Python Backend Engineer", "target_locations": "Remote", "resume": "Local QA Candidate\nqa@example.com\n\nEXPERIENCE\nPython Developer, Example QA Labs, January 2022 - December 2024\n- Built internal REST APIs using Python and FastAPI.\n- Maintained PostgreSQL tables and wrote SQL queries.\n- Wrote pytest integration tests for API workflows.\n- Containerized a small internal service using Docker.\n\nSKILLS\nPython, FastAPI, PostgreSQL, SQL, Docker, Git, pytest.\n\nEDUCATION\nBachelor of Technology in Computer Science, Example Test Institute, 2021.\n\nThis is a synthetic candidate used only to test the local application.", "preferences": "Remote roles. Work authorization, salary expectations and notice period have not been provided. Do not infer these."})
    discovery = step(client, "POST", "/api/discover", {"roles": "Python Backend Engineer", "location": "India", "remote": True}).json()
    report["discovery"] = discovery
    jobs = client.get("/api/state").json()["jobs"]
    if not jobs:
        raise RuntimeError("Search returned no individual roles. Adjust the smoke test search.")
    job = max(jobs, key=lambda j: len(j["description"]))
    jid = job["id"]
    report["job"] = {k: job[k] for k in ["url", "title", "company"]}
    assessed = step(client, "POST", f"/api/jobs/{jid}/assess").json()
    report["assessment"] = {"verdict": assessed["assessment"]["verdict"], "signals": len(assessed["assessment"]["signals"]), "sources": len(assessed["assessment"]["sources"])}
    drafted = step(client, "POST", f"/api/jobs/{jid}/generate").json()
    assert drafted["draft"] and drafted["status"] == "drafted"
    assert "qa@example.com" in drafted["draft"]["resume"]
    for fmt in ["pdf", "docx", "txt"]:
        exported = step(client, "GET", f"/api/jobs/{jid}/resume/{fmt}")
        (QA / f"synthetic-resume.{fmt}").write_bytes(exported.content)
    step(client, "PUT", f"/api/jobs/{jid}/draft", drafted["draft"])
    step(client, "PATCH", f"/api/jobs/{jid}", {"status": "shortlisted", "notes": "Synthetic local smoke test; no application sent."})
    report["final_state"] = client.get("/api/state").json()

path = QA / "live-smoke-report.json"
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("Live smoke report: " + str(path), flush=True)
