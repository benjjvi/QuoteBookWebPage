import calendar as pycalendar
import json
import os
import re
import secrets
import string
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException

import ai_helpers
import datetime_handler
import qb_formats

# Load the .env file
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
qb = qb_formats.QuoteBook()
ai_worker = ai_helpers.AI()


# Define the character set: uppercase, lowercase, digits
chars = string.ascii_letters + string.digits

CACHE_DIR = "cache"

IS_PROD = os.getenv("IS_PROD", "False").lower() in ("true", "1", "t")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = os.getenv("PORT", "8040")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # prevents JS from reading cookie
    SESSION_COOKIE_SECURE=True,  # only send cookie over HTTPS
    SESSION_COOKIE_SAMESITE="Lax",  # protects against CSRF
)


def parse_authors(raw):
    """
    Accepts:
      - "Ben"
      - "Ben and James"
      - "Ben, James"
      - "Ben, James, and Kim"
      - "test1, test2, test3, and test4"
    Returns:
      ["Ben", "James", "Kim"]
    """

    # Normalise " and " / ", and " into commas
    cleaned = re.sub(r"\s*,?\s+and\s+", ",", raw, flags=re.IGNORECASE)

    # Split on commas
    authors = [a.strip() for a in cleaned.split(",") if a.strip()]

    return authors


def to_uk_datetime(ts):
    uk_tz = ZoneInfo("Europe/London")
    dt = datetime.fromtimestamp(ts, tz=uk_tz)
    day = dt.day
    suffix = (
        "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return {"date": f"{day}{suffix} {dt.strftime('%B')}", "time": dt.strftime("%H:%M")}

def uk_date(epoch):
    return datetime.fromtimestamp(
        epoch,
        ZoneInfo("Europe/London")
    ).strftime("%d %B %Y")


def uk_time(epoch):
    return datetime.fromtimestamp(
        epoch,
        ZoneInfo("Europe/London")
    ).strftime("%H:%M")

def month_name(month: int) -> str:
    try:
        return datetime(2000, int(month), 1).strftime("%B")
    except Exception:
        return ""


app.jinja_env.filters["month_name"] = month_name
app.jinja_env.filters["to_uk_datetime"] = to_uk_datetime
app.jinja_env.filters["uk_time"] = uk_time
app.jinja_env.filters["uk_date"] = uk_date


@app.before_request
def refresh_qb():
    status = qb.reload()
    if status != 200 and status != 304:
        if status < 400 or status > 600:
            abort(500)


@app.route("/robots.txt")
def robots_txt():
    content = """
    User-agent: *
    Disallow: /
    """.strip()
    return Response(content, mimetype="text/plain")


@app.route("/")
def index():
    try:
        return render_template(
            "index.html",
            total_quotes=qb.total_quotes,
            speaker_counts=qb.speaker_counts,
            now=datetime.now(ZoneInfo("Europe/London")),
        )
    except Exception as e:
        print(e)
        abort(501)


@app.route("/add_quote", methods=["GET", "POST"])
def add_quote():
    if request.method == "POST":
        # Get form inputs
        quote_text = request.form.get("quote_text", "").strip()
        context = request.form.get("context", "").strip()
        author_raw = request.form.get("author_info", "Unknown").strip()

        # Only proceed if there is quote text
        if quote_text:
            # Parse authors
            authors = parse_authors(author_raw)

            # Get current UK timestamp in UTC
            timestamp = datetime_handler.get_current_uk_timestamp()

            # Build new quote object
            new_quote = qb_formats.Quote(
                id=qb.next_id(),
                quote=quote_text,
                authors=authors,
                timestamp=timestamp,  # UTC timestamp
                context=context,
            )

            # Add quote to quote book
            qb.add_quote(new_quote)

            # Reload quotes
            status = qb.reload()
            if status not in (200, 304):
                if status < 400 or status > 600:
                    abort(500)
                else:
                    abort(status)
            else:
                return redirect(url_for("index"))

    # GET request or empty quote_text
    return render_template("add_quote.html")


@app.route("/ai")
def ai():
    return render_template("ai.html")


@app.route("/ai_screenplay")
def ai_screenplay():
    scored_quotes = [
        (q, ai_worker.classify_funny_score(q.quote, q.authors)) for q in qb.quotes
    ]
    top_20 = ai_worker.get_top_20_with_cache(scored_quotes)
    resp = ai_worker.get_ai(top_20)

    resp = jsonify(resp=f"{resp.encode("utf-8").decode("unicode-escape")}")
    print(resp)
    return resp


@app.route("/ai_screenplay_render", methods=["POST"])
def ai_screenplay_render():
    data = json.loads(request.form["data"])
    return render_template(
        "ai_screenplay.html",
        title="AI Screenplay",
        screenplay=data.get("screenplay", ""),
    )


@app.route("/random_quote")
def random_quote():
    q = qb.get_random_quote()

    # Decode UTC timestamp into UK local date and time
    date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(q.timestamp)

    return render_template(
        "quote.html",
        quote=q.quote,
        author=", ".join(q.authors),
        date=date_str,
        time=time_str,
        id=str(q.id),
        context=q.context,
        reroll_button=True,
        quote_id=q.id,
    )


@app.route("/quote/<int:quote_id>")
def quote_by_id(quote_id):
    q = qb.get_quote_by_id(quote_id)
    if not q:
        abort(404)

    # Decode UTC timestamp into UK local date and time
    date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(q.timestamp)

    return render_template(
        "quote.html",
        quote=q.quote,
        author=", ".join(q.authors),
        id=str(q.id),
        date=date_str,
        time=time_str,
        context=q.context,
        reroll_button=False,
        quote_id=quote_id,
    )


@app.route("/all_quotes")
def all_quotes():
    try:
        # Get the speaker from query parameter
        speaker_filter = request.args.get("speaker", None)

        # Filter quotes if a speaker is selected
        if speaker_filter:
            speaker_lower = speaker_filter.lower()

            filtered_quotes = [
                q
                for q in qb.quotes
                if any(speaker_lower == author.lower() for author in q.authors)
            ]
        else:
            filtered_quotes = qb.quotes

        # Sort speakers by count (most common first)
        sorted_speakers = sorted(qb.speaker_counts, key=lambda x: x[1], reverse=True)

        print(sorted_speakers)

        return render_template(
            "all_quotes.html",
            quotes=filtered_quotes,
            selected_speaker=speaker_filter,
            speakers=sorted_speakers,
        )
    except Exception as e:
        print(e)
        abort(500)


@app.route("/search", methods=["GET", "POST"])
def search():
    results = []
    query = ""

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            results = qb.search_quotes(query)

    return render_template(
        "search.html",
        results=results,  # List[Quote]
        len_results=len(results),
        query=query,
    )


@app.route("/timeline/<int:year>/<int:month>")
def timeline(year, month):
    try:
        uk_tz = ZoneInfo("Europe/London")

        cal = pycalendar.Calendar(firstweekday=0)  # Monday
        month_days = cal.monthdatescalendar(year, month)

        calendar_days = []

        for week in month_days:
            week_days = []
            for day in week:
                day_start = datetime(day.year, day.month, day.day, tzinfo=uk_tz)
                day_end = day_start + timedelta(days=1) - timedelta(seconds=1)

                start_ts = int(day_start.timestamp())
                end_ts = int(day_end.timestamp())

                quotes = qb.get_quotes_between(start_ts, end_ts)
                count = len(quotes)

                week_days.append(
                    {
                        "date": day,
                        "in_month": day.month == month,
                        "count": count,
                        "timestamp": start_ts if count > 0 else None,
                    }
                )

            calendar_days.append(week_days)

        years = sorted(
            {datetime.fromtimestamp(q.timestamp, uk_tz).year for q in qb.quotes}
        )

        months = list(range(1, 13))

        return render_template(
            "calendar.html",
            year=year,
            month=month,
            years=years,
            months=months,
            calendar_days=calendar_days,
        )
    except Exception as e:
        print(e)
        abort(500)


@app.route("/timeline/day/<int:timestamp>")
def quotes_by_day(timestamp):
    uk_tz = ZoneInfo("Europe/London")

    day_dt = datetime.fromtimestamp(timestamp, tz=uk_tz)

    start_of_day = datetime.combine(
        day_dt.date(),
        time.min,
        tzinfo=uk_tz,
    )

    end_of_day = datetime.combine(
        day_dt.date(),
        time.max,
        tzinfo=uk_tz,
    )

    start_ts = int(start_of_day.timestamp())
    end_ts = int(end_of_day.timestamp())

    quotes = qb.get_quotes_between(start_ts, end_ts)

    return render_template(
        "quotes_by_day.html",
        quotes=quotes,
        day=day_dt.strftime("%d %B %Y"),
    )


@app.route("/credits")
def credits():
    return render_template("credits.html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.errorhandler(Exception)
def handle_all_errors(e):
    if isinstance(e, HTTPException):
        code = e.code
        name = e.name
        description = e.description
    else:
        code = 500
        name = "Internal Server Error"
        description = "The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application."

    return (
        render_template("error.html", code=code, name=name, description=description),
        code,
    )


if __name__ == "__main__":
    app.run(debug=False if IS_PROD else True, host=HOST, port=PORT)
