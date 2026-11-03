"""Automatic email applications and reply checks. Website-only forms require a connector."""
import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr

from . import db, gmail, maildb, providers, workflows

EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and not any(c.isspace() for c in value) and bool(EMAIL.fullmatch(value))


async def application_contact(job):
    # Only evidence actually fetched from the web may supply application destinations.
    fresh = workflows.clean_sources(await providers.extract(job["url"]))
    sources = fresh + (job.get("assessment") or {}).get("sources", [])[:6]
    if not sources:
        raise ValueError("No readable application instructions were found. This role needs manual application.")
    result = await providers.llm(workflows.UNTRUSTED + " Find an explicit email application route for this exact role. Return {accepts_email_applications:boolean,email:string,source_index:integer,quote:string,reason:string}. A valid route requires source text directly telling applicants to send a resume/application to the email address for this role. Do NOT use guessed addresses, privacy/legal/press/support mailboxes, generic contact addresses, recruiter names, or email notification signup. Do NOT bypass instructions to apply only through an online form. The quote must be a contiguous exact source excerpt including the email and the application instruction. If not found, return accepts_email_applications:false,email:'',source_index:0,quote:'',reason:'No email application route found'.", {"job_title": job["title"], "company": job["company"], "sources": sources}, "application_contact", 2200)
    idx = result.get("source_index")
    email = result.get("email", "").strip().lower() if isinstance(result.get("email"), str) else ""
    quote = result.get("quote", "")
    if result.get("accepts_email_applications") is not True or not valid_email(email) or not isinstance(idx, int) or not 0 <= idx < len(sources) or not isinstance(quote, str):
        raise ValueError("No explicit email application route was found. Use the employer's application form.")
    source = sources[idx]
    normalized = workflows._normalized(quote)
    if len(normalized) < 20 or email not in normalized or normalized not in workflows._normalized(source["excerpt"]):
        raise ValueError("The proposed application email could not be validated against a source quotation.")
    if not re.search(r"\b(apply|application|applicants|resume|résumé|cv|send your)\b", normalized, re.I):
        raise ValueError("The source does not identify this address as an application destination.")
    return {"email": email, "source_url": source["url"], "quote": quote}


async def queue_job(job_id):
    job = db.job(job_id)
    if not job:
        raise ValueError("Job not found")
    status = gmail.status()
    if not status["connected"] or status["reconnect_required"]:
        raise gmail.GmailError("Connect Bharti's Gmail first.")
    if job["status"] in {"applied", "interviewing", "offer", "rejected", "archived"}:
        raise ValueError("This role is already submitted or closed; another application will not be sent.")
    fingerprint = workflows.profile_fingerprint(db.profile())
    assessment = job.get("assessment") or {}
    if not assessment or not job["assessment_at"] or job["assessment_at"] < (datetime.now(timezone.utc) - timedelta(days=2)).isoformat() or assessment.get("profile_fingerprint") != fingerprint:
        job = await workflows.assess(job)
        assessment = job["assessment"]
    if assessment["verdict"] != "corroborated":
        raise ValueError("Automatic applications require a corroborated listing. Review the evidence first.")
    fit = assessment.get("fit_score")
    if fit is None or fit < maildb.settings()["minimum_fit_score"]:
        raise ValueError("This role does not meet the configured fit threshold for automatic applications.")
    contact = await application_contact(job)
    if not job.get("draft") or job["draft"].get("profile_fingerprint") != fingerprint:
        job = await workflows.generate(job)
    row = maildb.enqueue(job, status["email"], contact["email"], contact["source_url"], contact["quote"])
    maildb.check(job_id, "queued", "Application email verified against source instructions.")
    db.event("mail", f"Queued email application for {job['title']} at {job['company']}.")
    return row


def _record_sent(row, receipt):
    # The receipt and tracker transition commit together, including after crash reconciliation.
    stamp = db.now()
    with db.connection() as conn:
        conn.execute("UPDATE mail_applications SET status='sent',sent_at=?,gmail_id=?,thread_id=?,last_error='',updated=? WHERE id=?", (row.get("sent_at") or stamp, receipt["id"], receipt["threadId"], stamp, row["id"]))
        conn.execute("UPDATE jobs SET status='applied',updated=? WHERE id=? AND status IN ('discovered','shortlisted','drafted')", (stamp, row["job_id"]))
    db.event("mail", f"Gmail accepted the application email to {row['recipient']}.")


async def send_queued():
    status = gmail.status()
    if not status["connected"] or status["reconnect_required"]:
        raise gmail.GmailError("Gmail is not connected, or needs reconnection.")
    await gmail.access_token()
    settings = maildb.settings()
    sent = 0
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE status='queued' ORDER BY id")]
    for row in rows:
        if row["sender"].lower() != status["email"].lower():
            maildb.update(row["id"], status="needs_update", last_error="The connected sender differs from the queued sender.")
            continue
        if row["profile_fingerprint"] != workflows.profile_fingerprint(db.profile()):
            maildb.update(row["id"], status="needs_update", last_error="The profile changed after this application was queued. Prepare it again before sending.")
            continue
        job = db.job(row["job_id"])
        if not job or job["status"] in {"applied", "interviewing", "offer", "rejected", "archived"}:
            maildb.update(row["id"], status="cancelled", last_error="The job was submitted or closed before this queued send.")
            continue
        if not valid_email(row["recipient"]) or not row["source_quote"] or (job.get("assessment") or {}).get("verdict") != "corroborated":
            maildb.update(row["id"], status="needs_update", last_error="Application destination or job evidence needs review.")
            continue
        if not job.get("assessment_at") or job["assessment_at"] < (datetime.now(timezone.utc) - timedelta(days=2)).isoformat():
            maildb.update(row["id"], status="needs_update", last_error="The listing evidence is older than two days. Refresh the job before sending.")
            continue
        if not row["message_id"]:
            row["message_id"] = "<jobdesk-" + uuid.uuid4().hex + "@jobdesk.local>"
            maildb.update(row["id"], message_id=row["message_id"])
        # Build/validate the MIME message before claiming a send; no network call yet.
        try:
            gmail.message_for(row)
        except (ValueError, TypeError):
            maildb.update(row["id"], status="failed", last_error="The email or resume could not be prepared. Review the packet.")
            continue
        if not maildb.claim_send(row["id"], settings["daily_limit"]):
            continue
        try:
            receipt = await gmail.send(row)
        except gmail.GmailError as exc:
            state = "delivery_unknown" if exc.uncertain else "queued" if exc.status == 429 else "failed"
            maildb.update(row["id"], status=state, last_error=str(exc))
            if exc.status in {401, 403, 429}:
                break
            continue
        _record_sent(row, receipt)
        sent += 1
    return sent


async def reconcile():
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE status='delivery_unknown' OR (status='sending' AND updated<?)", (cutoff,))]
    reconciled = 0
    for row in rows:
        if not row["message_id"]:
            maildb.update(row["id"], status="delivery_unknown", last_error="No send identifier was recorded. This application will not be retried automatically.")
            continue
        result = await gmail.find_sent(row["message_id"])
        if result:
            _record_sent(row, result[0])
            reconciled += 1
        else:
            maildb.update(row["id"], status="delivery_unknown", last_error="Send receipt is unconfirmed. Checked Sent mail; no automatic resend will occur.")
    return reconciled


def classify_response(headers):
    sender = parseaddr(headers.get("from", ""))[1].lower()
    if "mailer-daemon" in sender or "postmaster" in sender or headers.get("return-path", "").strip() == "<>":
        return "bounce"
    if headers.get("auto-submitted", "no").lower() != "no" or headers.get("precedence", "").lower() in {"bulk", "auto_reply", "junk", "list"}:
        return "automatic_reply"
    return "reply"


async def sync_replies():
    status = gmail.status()
    if not status["connected"]:
        raise gmail.GmailError("Connect Gmail before checking replies.")
    await reconcile()
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM mail_applications WHERE thread_id IS NOT NULL AND status IN ('sent','replied','automatic_reply','bounced') AND sender=?", (status["email"].lower(),))]
    count = 0
    for row in rows:
        try:
            conversation = await gmail.thread(row["thread_id"])
        except gmail.GmailError as exc:
            if exc.status == 404:
                maildb.update(row["id"], last_checked=db.now(), last_error="The tracked conversation could not be found in Gmail.")
                continue
            raise
        for message in conversation.get("messages", []):
            if message.get("id") == row["gmail_id"] or "SENT" in message.get("labelIds", []):
                continue
            headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
            sender = parseaddr(headers.get("from", ""))[1].lower()
            if not sender or sender == row["sender"].lower():
                continue
            received = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone.utc).isoformat()
            if not row["sent_at"] or received < row["sent_at"]:
                continue
            # Check a matching recipient or an actual reply header, not just a similar subject.
            refs = headers.get("in-reply-to", "") + " " + headers.get("references", "")
            kind = classify_response(headers)
            if refs.strip() and row["message_id"] not in refs and kind != "bounce":
                continue
            if sender != row["recipient"].lower() and row["message_id"] not in refs and kind != "bounce":
                continue
            with db.connection() as conn:
                existing = conn.execute("SELECT 1 FROM mail_events WHERE message_id=?", (message["id"],)).fetchone()
                if existing:
                    continue
                conn.execute("INSERT INTO mail_events VALUES(?,?,?,?,?,?)", (message["id"], row["id"], received, sender, headers.get("subject", "")[:500], kind))
            # Later automatic acknowledgements must not overwrite an already recorded reply.
            if kind == "reply" or not row["reply_at"]:
                maildb.update(row["id"], status={"reply": "replied", "bounce": "bounced", "automatic_reply": "automatic_reply"}[kind], reply_at=received if kind == "reply" else row["reply_at"], reply_from=sender, reply_subject=headers.get("subject", "")[:500], response_kind=kind)
                if kind == "reply":
                    row["reply_at"] = received
            count += 1
            db.event("mail", f"{kind.replace('_', ' ').capitalize()} received for application to {row['recipient']}.")
        maildb.update(row["id"], last_checked=db.now(), last_error="")
    return count


def due():
    last = maildb.last_run()
    if not last:
        return True
    if not last["finished"]:
        return last["started"] < (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    hours = maildb.settings()["interval_hours"] if last["outcome"] == "ok" else 1
    return last["finished"] < (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


async def run_cycle(force=False, replies_only=False):
    if not gmail.status()["connected"]:
        return {"outcome": "not_connected", "message": "Connect Bharti's Gmail to start automation."}
    if not force and not due():
        return {"outcome": "not_due"}
    owner = uuid.uuid4().hex
    if not maildb.acquire_lease("gmail_automation", owner, minutes=60):
        return {"outcome": "already_running"}
    run_id = None
    summary = {"sent": 0, "responses": 0, "queued": 0, "needs_manual": 0}
    try:
        if not replies_only:
            with db.connection() as conn:
                run_id = conn.execute("INSERT INTO worker_runs(started) VALUES(?)", (db.now(),)).lastrowid
        summary["responses"] = await sync_replies()
        settings = maildb.settings()
        if settings["enabled"] and not replies_only:
            summary["sent"] = await send_queued()
            p = db.profile()
            if settings["discover_daily"] and p.get("target_roles"):
                await workflows.discover(p["target_roles"][:200], p.get("target_locations", "India")[:200], p.get("remote_first", True))
            processed = 0
            existing_ids = {a["job_id"] for a in maildb.applications() if a["sender"].lower() == gmail.status()["email"].lower()}
            for job in db.jobs():
                if processed >= settings["daily_limit"]:
                    break
                if job["id"] in existing_ids or job["status"] not in {"discovered", "shortlisted", "drafted"}:
                    continue
                processed += 1
                try:
                    await queue_job(job["id"])
                    summary["queued"] += 1
                except (ValueError, providers.ProviderError) as exc:
                    maildb.check(job["id"], "needs_manual", str(exc))
                    summary["needs_manual"] += 1
            summary["sent"] += await send_queued()
        if run_id:
            with db.connection() as conn:
                conn.execute("UPDATE worker_runs SET finished=?,outcome='ok',summary=? WHERE id=?", (db.now(), json.dumps(summary), run_id))
        return {"outcome": "ok", **summary}
    except (gmail.GmailError, providers.ProviderError, ValueError) as exc:
        if run_id:
            with db.connection() as conn:
                conn.execute("UPDATE worker_runs SET finished=?,outcome='error',summary=? WHERE id=?", (db.now(), str(exc), run_id))
        db.event("mail", "Automatic email run paused: " + str(exc))
        raise
    finally:
        maildb.release_lease("gmail_automation", owner)


async def background_loop():
    while True:
        try:
            await run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Details are exposed through the saved run state, never raw provider responses/tokens.
            pass
        await asyncio.sleep(60)
