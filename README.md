# Jobdesk

A personal job-search workspace for Bharti Singh with Parallel Search/Extract, River AI's DeepSeek V4.1 Flash 1017 using low reasoning, and Gmail OAuth. Runs locally with a FastAPI backend, a responsive browser interface and SQLite storage.

## Run locally on Windows

```powershell
cd C:\Projects\job
.\run.ps1
```

Open http://127.0.0.1:8000. The app binds to loopback and is designed for one trusted person on this computer. If PowerShell blocks local scripts, run these commands directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Use the workspace

1. Open **Connections**, save your Parallel key, and test both providers.
2. In **My profile**, paste or import your real resume (PDF, DOCX or text), add preferences, and save it.
3. In **Discover jobs**, search a role/location, import a direct job URL, or paste a job description manually.
4. Open a job and choose **Check evidence**. Review source quotations, uncertain facts and the estimated fit.
5. Choose **Prepare application**, then review/edit the resume, email, cover letter and proposed screening answers. Export the resume as PDF, DOCX or text.
6. Connect Bharti's Gmail in **Connections**. In **Email applications**, control the daily limit, fit threshold and automatic discovery. A job's **Apply by email automatically** action checks its application instructions, prepares a packet and sends a PDF resume when an explicit email application route exists. Complete website-only forms on the employer's site and record those submissions manually.

Search results, evidence and writing are real provider responses. The interface starts with no example jobs and no invented profile. Email applications can be sent automatically after Gmail authorization; third-party website forms are not automated. AI-written facts and evidence should be reviewed. Parallel cannot read every site, and some links require manual entry.

The profile can default new searches to **Remote only**. Discovery uses the saved background and preferences to screen clear work-mode, location and experience mismatches. Remote-only results require a source quotation supporting remote work; unknown and hybrid modes are excluded from that search. These checks assist screening and do not replace checking the original listing's location eligibility. Pay targets guide matching without treating an undisclosed salary as a rejection.

## Credentials and personal data

- Credentials are read only on the server. The browser sees presence/status, never existing key values.
- River reads `RIVER_API_KEY`, or resolves the existing Windows Claude Code River configuration at `~/.river-claude-code/litellm.yaml`. The default endpoint is `https://api.river.ai/v1`; the default model is `deepseek-ai/DeepSeek-V4.1-Flash-1017`.
- Parallel reads `PARALLEL_API_KEY` or the local Windows-user-encrypted store at `data/credentials.dpapi`. A new key saved through Connections replaces that local stored value. An environment variable takes precedence.
- `data/jobdesk.sqlite3` contains the master profile, job records, generated packets and email application snapshots in plaintext. OAuth secrets and Gmail tokens are encrypted with Windows DPAPI. The `data` directory, credentials and environment files are excluded from Git and Docker.
- A search sends search terms to Parallel; research sends job details/URLs to Parallel and relevant profile information to River; generation sends your resume, preferences and job description to River. Once Gmail is connected and sending is enabled, verified email applications are sent to employers through Gmail. Reply tracking reads conversation metadata; inbox contents are not sent to the AI provider.
- Daily local limits: 150 Parallel requests and 100 River requests per UTC day. Counts include failed attempts; there is no automatic paid retry loop. River token totals come from provider responses and are not a billing guarantee.
- Export the workspace from Connections to back up your profile and jobs. Keep the exported file private. Export excludes API credentials.

Environment settings are listed in `.env.example`; this app reads process environment variables rather than automatically loading `.env` files.

## Gmail authorization

The Google Cloud project is `bharti` (`analog-height-492914-t1`). The OAuth app is External / Testing with `bhartisingh8397@gmail.com` as its only test user. The Web application callback is:

```text
http://127.0.0.1:8000/auth/google/callback
```

Only `gmail.send` and `gmail.readonly` are requested. Google Cloud CLI login does not authorize the mailbox. Bharti must click **Connect Bharti's Gmail**, sign in to that account on Google's page and grant both permissions. The callback rejects other accounts. OAuth state is single-use, expires after ten minutes, is bound to an HTTP-only browser cookie, and uses PKCE. Authorization codes are removed from access logs. Tokens and client secrets never appear in API responses or workspace exports.

The downloaded Web client JSON has been imported into encrypted local storage. On another local installation, import it through Connections. Optional `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` environment variables take precedence. Keep the original downloaded JSON private.

Google External Testing refresh tokens for these Gmail permissions normally expire after seven days; reconnecting weekly may be necessary during local testing. The app reports expired or revoked authorization instead of recording a successful inbox check.

## Automatic email applications and tracking

Default settings are five sends per UTC day and a minimum estimated fit score of 60. In **Email applications**, change those settings or pause sending. Once Gmail is connected, the daily worker can discover roles, corroborate listings, find explicit application email instructions, generate packets and send PDF attachments. A destination must appear in an exact source quotation that includes application instructions; addresses are never guessed from company domains or names. Website-only forms appear as needing manual attention.

Every queue entry snapshots the sender, recipient, source instruction, subject, body, resume and profile fingerprint. Profile changes require an updated packet. Duplicate job/sender applications are prevented. Gmail's send receipt and the Applied tracker transition commit together. Sent means Gmail accepted the email; it does not guarantee employer delivery or review. A timeout is reconciled through the unique Message-ID in Sent mail and never blindly retried. Replies, automatic acknowledgements and bounces in tracked conversations are recorded separately. No follow-ups or replies to incoming messages are sent automatically.

## Daily scheduling on Windows

The server checks whether a cycle is due approximately every minute, with complete cycles spaced by 24 hours. A database lease prevents overlap with the independent Windows worker. Install or update that task with:

```powershell
.\scripts\install-mail-schedule.ps1
```

Task **Jobdesk Bharti Daily Email** runs at **09:00 Windows local time**, using the current Windows user's interactive session. The computer must be awake and that user signed in. The scheduled worker uses a once-per-local-calendar-day trigger, so a long previous run does not cause the next day to be skipped. The worker can run while the web server is closed; it does not start the web interface. It stores no Windows password and opens no console window.

Run a due cycle directly with:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Use **Check replies** or **Run applications now** for immediate runs. Pausing application sends leaves reply checks available; disconnecting Gmail revokes access where reachable and removes local tokens. Worker summaries are in `data/mail-worker.log`; detailed states are in the database.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
```

Tests use isolated temporary databases and mocked providers. They check duplicate preservation, submission confirmation, unsafe URLs, cross-origin mutation protection, missing-profile generation, evidence-quotation validation, failed-generation preservation, PDF/DOCX exports and credential redaction. Live provider checks are separate, incur provider usage and can be run from Connections.

Gmail tests cover OAuth state, PKCE, expected-account checks, missing permissions, secret redaction, PDF attachments, duplicate prevention, uncertain-send reconciliation, daily limits, changed profiles, replies, acknowledgements, bounces and worker leases. They send no real emails. A live send/receive test requires Bharti's own OAuth authorization first.

For a paid end-to-end integration test using a clearly labelled synthetic candidate and a separate database:

```powershell
.\.venv\Scripts\python.exe scripts/live_smoke.py
```

This searches real roles, checks evidence, generates a packet and exports documents. It never sends email or submits an application. Results are saved under the ignored `data/qa` folder, without changing your main workspace.

## Before Cloud Run deployment

The Dockerfile is a starting point, not a completed cloud deployment. Before publishing this personal-data app:

- Add authentication and per-user data access controls (or restrict access with Cloud Run IAM).
- Replace local SQLite with durable managed storage; Cloud Run's writable filesystem is ephemeral and is not a database backup.
- Move credentials to Secret Manager/environment configuration; Windows DPAPI cannot be decrypted on Cloud Run.
- Configure the public hostname in TrustedHostMiddleware and confirm origin protection through the deployment proxy.
- Move long-running research to durable queued workers. Replace the local Windows daily task with Cloud Scheduler, and Windows DPAPI token encryption with managed encryption before deployment.

The Gmail API and OAuth client are configured in the existing project. This local setup does not deploy Cloud Run resources.
