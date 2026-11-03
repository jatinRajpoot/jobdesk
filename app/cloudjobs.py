"""Start the durable Cloud Run worker from an authenticated dashboard action."""
import os

import httpx


async def launch(replies_only=False):
    import google.auth
    from google.auth.transport.requests import Request
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    job = os.environ["CLOUD_RUN_WORKER"]
    args = ["--replies-only"] if replies_only else ["--force"]
    payload = {"overrides": {"containerOverrides": [{"args": ["-m", "app.worker", *args]}]}}
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.post("https://run.googleapis.com/v2/" + job + ":run", headers={"Authorization": "Bearer " + credentials.token}, json=payload)
    if result.status_code >= 400:
        raise ValueError("The cloud worker could not be started. Check Cloud Run job permissions and try again.")
    return {"outcome": "started", "message": "The cloud worker is running. Refresh the email tracker for its results."}
