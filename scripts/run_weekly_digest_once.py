#!/usr/bin/env python3
"""Run one weekly digest eligibility check and send if due."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _forced_monday_window(now_uk: datetime) -> datetime:
    monday_date = (now_uk - timedelta(days=now_uk.weekday())).date()
    return datetime.combine(monday_date, time(hour=7, minute=1), tzinfo=now_uk.tzinfo)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Quote Book weekly digest check once."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Run using this week's Monday 07:01 UK timestamp so a send can be"
            " triggered outside the normal schedule window."
        ),
    )
    args = parser.parse_args()

    load_dotenv()
    os.environ.setdefault("WEEKLY_SCHEDULER_MODE", "external")

    from app import services  # Imported after env setup.

    uk_tz = ZoneInfo("Europe/London")
    now_uk = datetime.now(uk_tz)
    evaluated_now = _forced_monday_window(now_uk) if args.force else now_uk

    try:
        sent = services.maybe_send_weekly_email_digest(evaluated_now)
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "evaluated_now_uk": evaluated_now.isoformat(),
            "metrics": services.get_runtime_metrics(),
        }
        print(json.dumps(payload, indent=2))
        return 2

    payload = {
        "ok": True,
        "sent": bool(sent),
        "forced": bool(args.force),
        "evaluated_now_uk": evaluated_now.isoformat(),
        "metrics": services.get_runtime_metrics(),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
