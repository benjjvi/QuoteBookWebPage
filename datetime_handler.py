import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo


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

    uk_tz = ZoneInfo("Europe/London")

    # Group quotes by UK-local date
    quotes_by_day = defaultdict(list)

    for q in quotes:
        dt = datetime.fromtimestamp(q.timestamp, tz=uk_tz)
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
        midday = datetime(year, month, day, 12, 0, tzinfo=uk_tz)

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

    return calendar_data


def get_current_uk_timestamp():
    """Return the current time in the UK as a UTC timestamp (int)."""
    now = datetime.now(ZoneInfo("Europe/London"))
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
    for fmt in formats:
        try:
            dt_local = datetime.strptime(input_clean, fmt)
            # Assign UK timezone and current year
            from datetime import datetime
            from zoneinfo import ZoneInfo

            uk_tz = ZoneInfo("Europe/London")
            dt_local = dt_local.replace(year=datetime.now().year, tzinfo=uk_tz)
            return int(dt_local.timestamp())
        except ValueError:
            continue

    # If cannot parse, fallback to current time
    return get_current_uk_timestamp()


def format_uk_datetime_from_timestamp(ts: int) -> tuple[str, str]:
    """
    Convert a UTC timestamp to UK local time and return
    (date_str, time_str) suitable for your quote format.
    Example output: ('12th February', '02:23')
    """

    uk_tz = ZoneInfo("Europe/London")
    dt = datetime.fromtimestamp(ts, tz=uk_tz)

    # Build day with suffix
    day = dt.day
    suffix = (
        "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )

    date_str = f"{day}{suffix} {dt.strftime('%B')}"
    time_str = dt.strftime("%H:%M")

    return date_str, time_str
