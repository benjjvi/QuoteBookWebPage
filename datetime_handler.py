import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
import logging
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")
logger = logging.getLogger(__name__)


def build_calendar_data(quotes, year: int, month: int):
    """
    Build calendar heatmap data for a given month/year.

    Returns a list of dicts:
    [
        {
            "date": date(2025, 6, 15),
            "timestamp": 1755564000,
            "count": 3,
            "radius": 12,
            "clickable": True
        },
        ...
    ]
    """

    # Group quotes by UK-local date
    quotes_by_day = defaultdict(list)

    for q in quotes:
        dt = datetime.fromtimestamp(q.timestamp, tz=UK_TZ)
        quotes_by_day[dt.date()].append(q)

    days_in_month = monthrange(year, month)[1]
    calendar_data = []

    max_count = max((len(v) for v in quotes_by_day.values()), default=1)

    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        day_quotes = quotes_by_day.get(d, [])
        count = len(day_quotes)

        if count == 0:
            calendar_data.append(
                {
                    "date": d,
                    "timestamp": None,
                    "count": 0,
                    "radius": 0,
                    "clickable": False,
                }
            )
            continue

        # Pick midday for stable positioning
        midday = datetime(year, month, day, 12, 0, tzinfo=UK_TZ)

        # Scale circle radius (tweak numbers freely)
        radius = int(6 + (count / max_count) * 14)

        calendar_data.append(
            {
                "date": d,
                "timestamp": int(midday.timestamp()),
                "count": count,
                "radius": radius,
                "clickable": True,
            }
        )

    logger.debug(
        "Built calendar data for %s-%s (%s days).", year, month, days_in_month
    )
    return calendar_data


def get_current_uk_timestamp():
    """Return the current time in the UK as a UTC timestamp (int)."""
    now = datetime.now(UK_TZ)
    return int(now.timestamp())


def parse_timestamp_input(input_str: str) -> int:
    """
    Parse a user-provided timestamp string (like '12th February 02:23')
    into a UTC timestamp.
    """

    if not input_str.strip():
        # Empty input → use current UK time
        return get_current_uk_timestamp()

    # Remove day suffixes safely (st, nd, rd, th)
    input_clean = re.sub(
        r"(\d+)(st|nd|rd|th)", r"\1", input_str.strip(), flags=re.IGNORECASE
    )

    # Try parsing formats
    formats = ["%d %B %H:%M", "%d %b %H:%M"]  # day month + 24h time
    now_year = datetime.now(UK_TZ).year
    for fmt in formats:
        try:
            dt_local = datetime.strptime(input_clean, fmt)
            # Assign UK timezone and current year
            dt_local = dt_local.replace(year=now_year, tzinfo=UK_TZ)
            return int(dt_local.timestamp())
        except ValueError:
            continue

    # If cannot parse, fallback to current time
    logger.warning("Failed to parse timestamp input: '%s'", input_str)
    return get_current_uk_timestamp()


def format_uk_datetime_from_timestamp(ts: int) -> tuple[str, str]:
    """
    Convert a UTC timestamp to UK local time and return
    (date_str, time_str) suitable for your quote format.
    Example output: ('12th February', '02:23')
    """

    dt = datetime.fromtimestamp(ts, tz=UK_TZ)

    # Build day with suffix
    day = dt.day
    suffix = (
        "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )

    date_str = f"{day}{suffix} {dt.strftime('%B')}"
    time_str = dt.strftime("%H:%M")

    return date_str, time_str
