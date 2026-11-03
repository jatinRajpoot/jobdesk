"""Entry point for Windows Task Scheduler; use `python -m app.worker`."""
import asyncio
import argparse
import json
import logging
from datetime import datetime

from . import automation, config, db, maildb


async def main(scheduled=False):
    db.init()
    maildb.init()
    today = datetime.now().date().isoformat()
    try:
        if scheduled and maildb.settings().get("scheduled_last_date") == today:
            result = {"outcome": "already_checked_today"}
        else:
            result = await automation.run_cycle(force=scheduled)
            if scheduled and result["outcome"] == "ok":
                maildb.save_settings({"scheduled_last_date": today})
    except Exception as exc:
        result = {"outcome": "error", "type": type(exc).__name__}
    # Only operation summaries. Never mailbox contents, OAuth codes or credentials.
    path = config.DATA / "mail-worker.log"
    with path.open("a", encoding="utf-8") as out:
        out.write(db.now() + " " + json.dumps(result) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", action="store_true", help="Run the daily calendar task without a 24-hour finish-time drift")
    args = parser.parse_args()
    asyncio.run(main(scheduled=args.scheduled))
