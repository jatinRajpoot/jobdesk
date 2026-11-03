import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import access, automation, config, db, gmail, maildb, providers, workflows
from .documents import docx_resume, pdf_resume


@asynccontextmanager
async def lifespan(app):
    db.init()
    maildb.init()
    task = asyncio.create_task(automation.background_loop()) if __import__('os').environ.get("JOBDESK_DISABLE_BACKGROUND") != "1" else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str) and record.args[2].startswith('/auth/google/'):
            args = list(record.args)
            args[2] = args[2].split('?')[0]
            record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())


app = FastAPI(title="Jobdesk", docs_url="/api/docs", lifespan=lifespan)
cloud_host = urlsplit(os.environ.get("PUBLIC_BASE_URL", "")).hostname
allowed_hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
if cloud_host:
    allowed_hosts.append(cloud_host)
elif os.environ.get("K_SERVICE"):
    allowed_hosts.append("*.run.app")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
operation_locks = {}
search_lock = asyncio.Lock()


@app.middleware("http")
async def local_security(request: Request, call_next):
    public = request.url.path in {"/login", "/privacy", "/api/health", "/api/login", "/auth/google/start", "/auth/google/callback"} or request.url.path.startswith("/static/")
    if request.url.path == "/auth/google/start" and access.password_hash():
        public = False
    if access.required() and not public and not access.authenticated(request.cookies.get(access.COOKIE, "")):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Enter the workspace password to continue."}, status_code=401, headers={"Cache-Control": "no-store"})
        return RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Requests must originate from this app."}, status_code=403)
        # Browser JSON mutations require same-origin custom headers; multipart uploads also checked.
        if request.headers.get("x-jobdesk") != "1":
            return JSONResponse({"detail": "Missing application request header."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(providers.ProviderError)
async def provider_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(gmail.GmailError)
async def gmail_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error includes the input; credential inputs must not be echoed.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


def required_job(jid):
    job = db.job(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/")
async def index():
    if os.environ.get("K_SERVICE"):
        from fastapi.responses import HTMLResponse
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("LOCAL PREVIEW", "PRIVATE CLOUD").replace("Personal · Local", "Personal · Cloud")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": "cloud" if os.environ.get("K_SERVICE") else "local"}


@app.get("/login")
async def login(request: Request):
    if request.query_params.get("gmail") == "failed":
        from fastapi.responses import HTMLResponse
        html = (STATIC / "login.html").read_text(encoding="utf-8").replace('<form id="login-form"', '<p class="notice">The Gmail connection could not be completed. Open the workspace with your password, then try again in Connections.</p><form id="login-form"')
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


class PasswordLogin(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=256)


@app.post("/api/login")
def password_login(body: PasswordLogin):
    encoded = access.password_hash()
    if not encoded:
        raise HTTPException(503, "Password login is not configured yet.")
    if not access.claim_login_attempt():
        raise HTTPException(429, "Too many login attempts. Try again in one minute.", headers={"Retry-After": "60"})
    if not access.verify_password(body.password.get_secret_value(), encoded):
        raise HTTPException(401, "Incorrect workspace password.")
    response = JSONResponse({"ok": True})
    response.set_cookie(access.COOKIE, access.issue(maildb.settings()["expected_email"]), max_age=access.SESSION_DAYS*86400, httponly=True, secure=True, samesite="lax", path="/")
    return response


@app.get("/privacy")
async def privacy():
    from fastapi.responses import HTMLResponse
    return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Privacy · Jobdesk</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/login.css"></head><body><main class="login"><h1>Your data in Jobdesk</h1><p>Jobdesk is a private job-search workspace for Bharti. It stores her profile, job records and application history. Resume and job details are sent to River AI for writing and analysis; search requests and job links are sent to Parallel for research.</p><p>After permission is granted, Gmail access sends eligible application emails and reads tracked conversation metadata to detect replies and bounces. Inbox content is not sent to an AI provider. Gmail data is not sold or used for advertising or model training. The app does not delete or mark messages as read.</p><p>Cloud records are stored in the project\'s Firestore database. Gmail tokens are encrypted with a key held in Secret Manager. The private dashboard provides a workspace export and Gmail disconnection controls; Google account permissions can also be revoked from the Google account.</p><p>Google API data use follows the Google API Services User Data Policy, including Limited Use requirements.</p><a class="light-link" href="/">Return to Jobdesk</a></main></body></html>')


def connection_status():
    river = config.river_credentials()
    return {"parallel": {"configured": bool(config.parallel_key()), "name": "Parallel Search + Extract"}, "river": {"configured": bool(river["key"]), "name": "DeepSeek V4.1 Flash 1017", "model": river["model"], "origin": river["origin"], "reasoning": "low"}, "email": gmail.status()}


@app.get("/api/state")
async def state():
    profile = db.profile()
    return {"profile": profile, "profile_fingerprint": workflows.profile_fingerprint(profile), "jobs": db.jobs(), "connections": connection_status(), "mail": maildb.overview(), "deployment": {"mode": "cloud" if os.environ.get("K_SERVICE") else "local"}, **db.overview()}


class Profile(BaseModel):
    remote_first: bool = False
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=250)
    headline: str = Field(default="", max_length=250)
    location: str = Field(default="", max_length=250)
    target_roles: str = Field(default="", max_length=300)
    target_locations: str = Field(default="", max_length=300)
    resume: str = Field(default="", max_length=50000)
    preferences: str = Field(default="", max_length=10000)


@app.put("/api/profile")
async def put_profile(body: Profile):
    db.save_profile(body.model_dump())
    db.event("profile", "Updated your master profile.")
    return body


@app.post("/api/profile/import")
async def import_resume(file: UploadFile = File(...)):
    content = await file.read(3 * 1024 * 1024 + 1)
    if len(content) > 3 * 1024 * 1024:
        raise HTTPException(413, "Use a resume smaller than 3 MB.")
    suffix = Path(file.filename or "").suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > 20:
                raise ValueError("Use a resume with 20 pages or fewer.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix == ".docx":
            from docx import Document
            from zipfile import ZipFile
            with ZipFile(BytesIO(content)) as z:
                if sum(i.file_size for i in z.infolist()) > 20 * 1024 * 1024:
                    raise ValueError("The document is too large when expanded.")
            doc = Document(BytesIO(content))
            text = "\n".join([p.text for p in doc.paragraphs] + [" | ".join(c.text for c in r.cells) for table in doc.tables for r in table.rows])
        elif suffix in {".txt", ".md"}:
            text = content.decode("utf-8-sig")
        else:
            raise HTTPException(400, "Choose a PDF, DOCX or plain text file.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "This document could not be read. Paste the resume text instead.") from None
    if len(text.strip()) < 40:
        raise HTTPException(400, "No readable resume text was found. For a scanned PDF, paste the text instead.")
    return {"text": text[:50000], "truncated": len(text) > 50000}


class Search(BaseModel):
    roles: str = Field(min_length=2, max_length=200)
    location: str = Field(default="", max_length=200)
    remote: bool = False


@app.post("/api/discover")
async def discover(body: Search):
    if search_lock.locked():
        raise HTTPException(409, "A discovery search is already running.")
    async with search_lock:
        return await workflows.discover(body.roles, body.location, body.remote)


class Import(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@app.post("/api/jobs/import")
async def import_job(body: Import):
    return await workflows.import_url(body.url)


class ManualJob(Import):
    title: str = Field(min_length=2, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    location: str = Field(default="Not specified", max_length=200)
    description: str = Field(min_length=30, max_length=30000)


@app.post("/api/jobs")
async def add_job(body: ManualJob):
    data = body.model_dump()
    data["url"] = workflows.public_url(data["url"])
    data["source"] = "Added manually"
    job, created = db.upsert(data)
    if created:
        db.event("import", f"Saved {body.title} at {body.company}.")
    return {"job": job, "created": created}


class JobUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(discovered|shortlisted|drafted|applied|interviewing|offer|rejected|archived)$")
    notes: str | None = Field(default=None, max_length=10000)
    confirm_submitted: bool = False


@app.patch("/api/jobs/{jid}")
async def update_job(jid: str, body: JobUpdate):
    current = required_job(jid)
    if body.status == "applied" and current["status"] != "applied" and not body.confirm_submitted:
        raise HTTPException(400, "Confirm you actually submitted the application before marking it applied.")
    values = body.model_dump(exclude_none=True, exclude={"confirm_submitted"})
    if values:
        db.update_job(jid, **values)
    if body.status:
        db.event("status", f"Moved {current['title']} to {body.status}.")
    return required_job(jid)


@app.post("/api/jobs/{jid}/assess")
async def assess(jid: str):
    required_job(jid)
    lock = operation_locks.setdefault(jid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "This job already has an operation running.")
    async with lock:
        return await workflows.assess(required_job(jid))


@app.post("/api/jobs/{jid}/generate")
async def generate(jid: str):
    required_job(jid)
    lock = operation_locks.setdefault(jid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "This job already has an operation running.")
    async with lock:
        return await workflows.generate(required_job(jid))


@app.put("/api/jobs/{jid}/draft")
async def save_draft(jid: str, body: workflows.Packet):
    job = required_job(jid)
    if not job["draft"]:
        raise HTTPException(400, "Generate an application packet first.")
    draft = {**job["draft"], **body.model_dump()}
    db.update_job(jid, draft=draft)
    return required_job(jid)


@app.get("/api/jobs/{jid}/resume/{format}")
async def export_resume(jid: str, format: str):
    job = required_job(jid)
    if not job["draft"]:
        raise HTTPException(400, "Generate a resume first.")
    text = job["draft"]["resume"]
    if format == "pdf":
        content, media = pdf_resume(text, job["title"]), "application/pdf"
    elif format == "docx":
        content, media = docx_resume(text), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "txt":
        content, media = text.encode(), "text/plain; charset=utf-8"
    else:
        raise HTTPException(404, "Use pdf, docx or txt")
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="tailored-resume.{format}"'})


class Credential(BaseModel):
    key: str = Field(min_length=10, max_length=1000)


@app.put("/api/connections/parallel")
async def save_key(body: Credential):
    config.save_parallel(body.key)
    return connection_status()


@app.post("/api/connections/{provider}/test")
async def test_connection(provider: str):
    if provider == "parallel":
        results = await providers.search(["Parallel Search API documentation"], "Find Parallel's official Search API documentation to verify this connection.", 2)
        if not results:
            raise providers.ProviderError("The API responded but returned no search results. Try again.")
        db.event("connection", "Parallel Search connection tested successfully.")
        return {"ok": True, "message": f"Search succeeded · {len(results)} results returned", "checked_at": db.now()}
    if provider == "river":
        result = await providers.llm("Reply with OK.", {"test": "connection"}, "connection_test", 256, json_output=False)
        if not result:
            raise providers.ProviderError("The model returned no completion.")
        db.event("connection", "DeepSeek connection tested with low reasoning.")
        return {"ok": True, "message": "DeepSeek replied successfully · low reasoning", "checked_at": db.now()}
    raise HTTPException(404, "Unknown provider")


@app.get("/api/export")
async def export_workspace():
    import json
    return Response(json.dumps({"profile": db.profile(), "jobs": db.jobs(), "exported_at": db.now()}, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="jobdesk-workspace.json"'})


@app.post("/api/connections/google/client")
async def import_google_client(file: UploadFile = File(...)):
    raw = await file.read(32769)
    if len(raw) > 32768:
        raise HTTPException(413, "The OAuth client file is too large.")
    try:
        data = json.loads(raw)
        gmail.import_client(data)
    except (ValueError, TypeError):
        raise HTTPException(400, "Use a Google Web application OAuth client JSON with the callback URI shown in Connections.") from None
    return gmail.status()


@app.get("/auth/google/start")
async def google_start(request: Request, reconnect: bool = False):
    # Keep the cookie and callback on the same host, including localhost/127.0.0.1 aliases.
    canonical = urlsplit(gmail.REDIRECT)
    if request.headers.get("host") != canonical.netloc:
        suffix = "?reconnect=true" if reconnect else ""
        return RedirectResponse(f"{canonical.scheme}://{canonical.netloc}/auth/google/start{suffix}", status_code=303)
    url, browser = gmail.begin_oauth(reconnect=reconnect)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie("jobdesk_oauth", browser, max_age=600, httponly=True, secure=canonical.scheme == "https", samesite="lax", path="/auth/google")
    return response


@app.get("/auth/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    # No codes or provider error text are reflected into HTML or logs.
    try:
        verifier = gmail.consume_state(state, request.cookies.get("jobdesk_oauth", ""))
        if error or not code:
            raise gmail.GmailError("Google authorization was cancelled. Start again from Connections.")
        email = await gmail.finish_oauth(code, verifier)
        response = RedirectResponse("/#connections?gmail=connected", status_code=303)
        if access.required() and not access.password_hash():
            response.set_cookie(access.COOKIE, access.issue(email), max_age=access.SESSION_DAYS*86400, httponly=True, secure=True, samesite="lax", path="/")
    except gmail.GmailError as exc:
        maildb.save_settings({"oauth_error": str(exc)})
        response = RedirectResponse("/login?gmail=failed" if access.required() else "/#connections?gmail=failed", status_code=303)
    response.delete_cookie("jobdesk_oauth", path="/auth/google")
    return response


@app.post("/api/connections/gmail/disconnect")
async def disconnect_gmail():
    await gmail.disconnect()
    return gmail.status()


class MailSettings(BaseModel):
    enabled: bool
    discover_daily: bool = True
    daily_limit: int = Field(default=5, ge=1, le=20)
    minimum_fit_score: int = Field(default=60, ge=0, le=100)


@app.put("/api/mail/settings")
async def mail_settings(body: MailSettings):
    maildb.save_settings(body.model_dump())
    return maildb.settings()


@app.post("/api/jobs/{jid}/email-application")
async def queue_email(jid: str):
    required_job(jid)
    owner = __import__('uuid').uuid4().hex
    if not maildb.acquire_lease("gmail_automation", owner, minutes=15):
        raise HTTPException(409, "An automatic application run is already working. Try again shortly.")
    try:
        row = await automation.queue_job(jid)
        if maildb.settings()["enabled"]:
            await automation.send_queued()
        return {"application": {k: v for k, v in maildb.application(row["id"]).items() if k not in {"resume", "body", "profile_fingerprint"}}}
    finally:
        maildb.release_lease("gmail_automation", owner)


@app.post("/api/mail/check-replies")
async def check_mail_replies():
    if os.environ.get("CLOUD_RUN_WORKER"):
        from .cloudjobs import launch
        return await launch(replies_only=True)
    return await automation.run_cycle(force=True, replies_only=True)


@app.post("/api/mail/run")
async def run_mail_automation():
    if os.environ.get("CLOUD_RUN_WORKER"):
        from .cloudjobs import launch
        return await launch()
    return await automation.run_cycle(force=True)


@app.post("/api/mail/applications/{aid}/cancel")
async def cancel_queued_mail(aid: int):
    row = maildb.application(aid)
    if not row:
        raise HTTPException(404, "Application not found")
    if row["status"] not in {"queued", "needs_update", "failed"}:
        raise HTTPException(409, "Only an unsent queued application can be cancelled.")
    changed = maildb.cancel(aid)
    if not changed:
        raise HTTPException(409, "This application is already being sent or was sent.")
    return {"cancelled": True}


@app.post("/api/logout")
async def logout(request: Request):
    access.revoke(request.cookies.get(access.COOKIE, ""))
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(access.COOKIE, path="/")
    return response
