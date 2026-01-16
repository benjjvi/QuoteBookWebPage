import re
from datetime import datetime
from zoneinfo import ZoneInfo


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
