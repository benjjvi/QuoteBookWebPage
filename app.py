import calendar as pycalendar
import json
import logging
import os
import random as randlib
import re
import secrets
import time as timelib
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+
from urllib.parse import urljoin

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException

import ai_helpers
import datetime_handler
from quote_client import get_quote_client

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)

# Load the .env file
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
quote_store = get_quote_client()
ai_worker = ai_helpers.AI()


UK_TZ = ZoneInfo("Europe/London")

IS_PROD = os.getenv("IS_PROD", "False").lower() in ("true", "1", "t")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = os.getenv("PORT", "8040")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()
PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE = 9


app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # prevents JS from reading cookie
    SESSION_COOKIE_SECURE=True,  # only send cookie over HTTPS
    SESSION_COOKIE_SAMESITE="Lax",  # protects against CSRF
)


def wants_json_response() -> bool:
    """Return True when the client prefers JSON (API routes and JSON accept headers)."""
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best
    if not best:
        return False
    return (
        best == "application/json"
        and request.accept_mimetypes[best] >= request.accept_mimetypes["text/html"]
    )


def to_uk_datetime(ts):
    dt = datetime.fromtimestamp(ts, tz=UK_TZ)
    day = dt.day
    suffix = (
        "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return {"date": f"{day}{suffix} {dt.strftime('%B')}", "time": dt.strftime("%H:%M")}


def uk_date(epoch):
    return datetime.fromtimestamp(epoch, UK_TZ).strftime("%d %B %Y")


def uk_time(epoch):
    return datetime.fromtimestamp(epoch, UK_TZ).strftime("%H:%M")


def month_name(month: int) -> str:
    try:
        return datetime(2000, int(month), 1).strftime("%B")
    except (TypeError, ValueError):
        return ""


def build_public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or request.url_root
    if not base:
        return path
    if not base.endswith("/"):
        base = f"{base}/"
    return urljoin(base, path.lstrip("/"))


app.jinja_env.filters["month_name"] = month_name
app.jinja_env.filters["to_uk_datetime"] = to_uk_datetime
app.jinja_env.filters["uk_time"] = uk_time
app.jinja_env.filters["uk_date"] = uk_date

# ─────────────────────────────────────────────
# Hard-capped file handler (no deletion)
# ─────────────────────────────────────────────


class MaxSizeFileHandler(logging.FileHandler):
    def __init__(self, filename, max_bytes, **kwargs):
        self.max_bytes = max_bytes
        super().__init__(filename, **kwargs)

    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename):
                if os.path.getsize(self.baseFilename) >= self.max_bytes:
                    return
            super().emit(record)
        except Exception:
            self.handleError(record)


# ─────────────────────────────────────────────
# Logging configuration
# ─────────────────────────────────────────────

formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")

# Console logging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# File logging (hard cap 2MB)
file_handler = MaxSizeFileHandler(
    "app.log",
    max_bytes=2 * 1024 * 1024,
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# Reset Flask logger
app.logger.handlers.clear()
app.logger.setLevel(logging.INFO)
app.logger.propagate = False

app.logger.addHandler(console_handler)
app.logger.addHandler(file_handler)

# Silence Werkzeug access logs
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# Optional sanity log
app.logger.info("Logging initialised")


@app.before_request
def start_timer():
    g.start_time = timelib.time()


@app.after_request
def log_request(response):
    duration = round(timelib.time() - g.start_time, 3)

    app.logger.info(
        "%s %s (%s) -> %s [%ss]",
        request.method,
        request.path,
        request.endpoint,
        response.status_code,
        duration,
    )

    return response


@app.teardown_request
def log_exception(exception):
    if exception:
        app.logger.warning(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.path,
            type(exception).__name__,
        )


@app.before_request
def refresh_qb():
    status = quote_store.reload()
    if status == 200:
        app.logger.info("Quote book reloaded.")
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
    return render_template(
        "index.html",
        total_quotes=quote_store.get_total_quotes(),
        speaker_counts=quote_store.get_speaker_counts(),
        now=datetime.now(UK_TZ),
    )


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
            authors = quote_store.parse_authors(author_raw)

            # Get current UK timestamp in UTC
            timestamp = datetime_handler.get_current_uk_timestamp()

            # Add quote to quote store (local DB or remote API)
            new_quote = quote_store.add_quote(
                quote_text=quote_text,
                authors=authors,
                context=context,
                timestamp=timestamp,
            )
            app.logger.info(
                "Added quote %s by %s",
                new_quote.id,
                ", ".join(new_quote.authors),
            )

            return redirect(url_for("index"))

    # GET request or empty quote_text
    return render_template("add_quote.html")


@app.route("/ai")
def ai():
    return render_template("ai.html", ai_available=ai_worker.can_generate)


@app.route("/ai_screenplay")
def ai_screenplay():
    if not ai_worker.can_generate:
        return (
            jsonify(
                error="AI screenplay generation is disabled. Set OPENROUTER_KEY to enable."
            ),
            503,
        )
    app.logger.info("AI screenplay requested.")
    quotes = quote_store.get_all_quotes()
    scored_quotes = [
        (q, ai_worker.classify_funny_score(q.quote, q.authors, q.stats))
        for q in quotes
    ]
    top_20 = ai_worker.get_top_20_with_cache(scored_quotes)
    resp = ai_worker.get_ai(top_20)

    return jsonify(resp=resp)


@app.route("/ai_screenplay_render", methods=["POST"])
def ai_screenplay_render():
    data = json.loads(request.form["data"])
    rendered_at = datetime.now().strftime("%d %b %Y, %H:%M")
    return render_template(
        "ai_screenplay.html",
        title="AI Screenplay",
        screenplay=data.get("screenplay", ""),
        rendered_at=rendered_at,
    )


@app.route("/battle", methods=["GET", "POST"])
def battle():
    if request.method == "POST":
        winner_id = int(request.form["winner"])
        loser_id = int(request.form["loser"])

        winner, loser = quote_store.record_battle(winner_id, loser_id)
        if winner and loser:
            app.logger.info("Battle result: winner=%s loser=%s", winner_id, loser_id)
        else:
            app.logger.warning(
                "Battle POST with missing quote(s): winner=%s loser=%s",
                winner_id,
                loser_id,
            )

        return redirect(url_for("battle"))

    quotes = quote_store.get_all_quotes()
    if len(quotes) < 2:
        return "Not enough quotes for a battle", 400

    quote_a, quote_b = randlib.sample(quotes, 2)

    return render_template(
        "battle.html",
        quote_a=quote_a,
        quote_b=quote_b,
    )


@app.route("/random")
def random():
    q = quote_store.get_random_quote()
    if not q:
        abort(404)
    app.logger.info("Random quote served: %s", q.id)

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
        permalink=build_public_url(url_for("quote_by_id", quote_id=q.id)),
    )


@app.route("/quote/<int:quote_id>")
def quote_by_id(quote_id):
    q = quote_store.get_quote_by_id(quote_id)
    if not q:
        app.logger.info("Quote not found: %s", quote_id)
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
        permalink=build_public_url(url_for("quote_by_id", quote_id=quote_id)),
    )


@app.route("/all_quotes")
def all_quotes():
    speaker_filter = request.args.get("speaker", None)
    page = request.args.get("page", 1, type=int)

    paginated_quotes, page, total_pages = quote_store.get_quote_page(
        speaker_filter, page, PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE
    )
    sorted_speakers = quote_store.get_speaker_counts()

    return render_template(
        "all_quotes.html",
        quotes=paginated_quotes,
        selected_speaker=speaker_filter,
        speakers=sorted_speakers,
        page=page,
        total_pages=total_pages,
    )


@app.route("/search", methods=["GET", "POST"])
def search():
    results = []
    query = ""

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            results = quote_store.search_quotes(query)
            app.logger.info("Search query: '%s' (%s results)", query, len(results))

    return render_template(
        "search.html",
        results=results,  # List[Quote]
        len_results=len(results),
        query=query,
    )


@app.route("/stats")
def stats():
    # Aggregate all stats here to keep templates simple.
    quotes = quote_store.get_all_quotes()
    total_quotes = quote_store.get_total_quotes()
    speaker_counts = quote_store.get_speaker_counts()
    unique_authors = len(speaker_counts)
    top_authors = speaker_counts[:5]

    word_counts = [len(re.findall(r"\b\w+\b", q.quote)) for q in quotes]
    avg_words = round(sum(word_counts) / len(word_counts), 1) if word_counts else 0
    avg_chars = round(sum(len(q.quote) for q in quotes) / len(quotes), 1) if quotes else 0

    longest_quote = max(quotes, key=lambda q: len(q.quote), default=None)
    shortest_quote = min(quotes, key=lambda q: len(q.quote), default=None)

    newest_quote = max(quotes, key=lambda q: q.timestamp, default=None)
    oldest_quote = min(quotes, key=lambda q: q.timestamp, default=None)

    day_counts = Counter()
    hour_counts = Counter()
    for quote in quotes:
        dt = datetime.fromtimestamp(quote.timestamp, tz=UK_TZ)
        day_counts[dt.date()] += 1
        hour_counts[dt.hour] += 1

    busiest_day = None
    if day_counts:
        day, count = day_counts.most_common(1)[0]
        busiest_day = {
            "label": day.strftime("%d %b %Y"),
            "count": count,
        }

    hour_buckets = [
        ("Late night", "12am-3am", range(0, 3)),
        ("Early morning", "3am-6am", range(3, 6)),
        ("Morning", "6am-9am", range(6, 9)),
        ("Late morning", "9am-12pm", range(9, 12)),
        ("Afternoon", "12pm-3pm", range(12, 15)),
        ("Late afternoon", "3pm-6pm", range(15, 18)),
        ("Evening", "6pm-9pm", range(18, 21)),
        ("Late evening", "9pm-12am", range(21, 24)),
    ]
    bucket_data = []
    for label, range_label, hours in hour_buckets:
        count = sum(hour_counts[h] for h in hours)
        bucket_data.append(
            {
                "label": label,
                "range_label": range_label,
                "count": count,
            }
        )
    max_bucket = max((bucket["count"] for bucket in bucket_data), default=1)
    for bucket in bucket_data:
        bucket["percent"] = int((bucket["count"] / max_bucket) * 100) if max_bucket else 0

    total_battle_entries = sum(q.stats.get("battles", 0) for q in quotes)
    total_battles = total_battle_entries // 2 if total_battle_entries else 0
    most_battled = max(
        quotes, key=lambda q: q.stats.get("battles", 0), default=None
    )

    top_winners = sorted(
        quotes, key=lambda q: q.stats.get("wins", 0), reverse=True
    )
    top_winners = [q for q in top_winners if q.stats.get("wins", 0) > 0][:5]

    min_battles_for_rate = 3
    win_rate_candidates = []
    for q in quotes:
        battles = q.stats.get("battles", 0)
        if battles >= min_battles_for_rate:
            win_rate = q.stats.get("wins", 0) / battles
            win_rate_candidates.append((q, win_rate))

    best_win_rates = sorted(win_rate_candidates, key=lambda x: x[1], reverse=True)[:5]

    # AI heuristic scoring (no external API call).
    funny_scores = []
    for q in quotes:
        score = ai_worker.classify_funny_score(q.quote, q.authors, q.stats)
        funny_scores.append((q, score))
    funny_scores.sort(key=lambda x: x[1], reverse=True)
    top_funny = funny_scores[:5]
    avg_funny = round(
        sum(score for _, score in funny_scores) / len(funny_scores), 2
    ) if funny_scores else 0

    return render_template(
        "stats.html",
        total_quotes=total_quotes,
        unique_authors=unique_authors,
        top_authors=top_authors,
        avg_words=avg_words,
        avg_chars=avg_chars,
        longest_quote=longest_quote,
        shortest_quote=shortest_quote,
        newest_quote=newest_quote,
        oldest_quote=oldest_quote,
        busiest_day=busiest_day,
        hour_buckets=bucket_data,
        total_battles=total_battles,
        most_battled=most_battled,
        top_winners=top_winners,
        best_win_rates=best_win_rates,
        top_funny=top_funny,
        avg_funny=avg_funny,
    )


@app.route("/timeline/<int:year>/<int:month>")
def timeline(year, month):
    uk_tz = ZoneInfo("Europe/London")

    cal = pycalendar.Calendar(firstweekday=0)  # Monday
    month_days = cal.monthdatescalendar(year, month)

    calendar_days = []
    quotes = quote_store.get_all_quotes()

    for week in month_days:
        week_days = []
        for day in week:
            day_start = datetime(day.year, day.month, day.day, tzinfo=uk_tz)
            day_end = day_start + timedelta(days=1) - timedelta(seconds=1)

            start_ts = int(day_start.timestamp())
            end_ts = int(day_end.timestamp())

            day_quotes = [q for q in quotes if start_ts <= q.timestamp <= end_ts]
            count = len(day_quotes)

            week_days.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "count": count,
                    "timestamp": start_ts if count > 0 else None,
                }
            )

        calendar_days.append(week_days)

    years = sorted({datetime.fromtimestamp(q.timestamp, uk_tz).year for q in quotes})

    months = list(range(1, 13))

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        years=years,
        months=months,
        calendar_days=calendar_days,
    )


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

    quotes = quote_store.get_quotes_between(start_ts, end_ts)

    return render_template(
        "quotes_by_day.html",
        quotes=quotes,
        day=day_dt.strftime("%d %B %Y"),
        year=day_dt.year,
        month=day_dt.month,
    )


@app.route("/api/latest")
def api_latest_quote():
    newest_quote = quote_store.get_latest_quote()
    if not newest_quote:
        app.logger.warning("API latest requested with no quotes.")
        return jsonify({"error": "No quotes found"}), 404
    app.logger.info("API latest quote served: %s", newest_quote.id)

    return jsonify(
        {
            "id": newest_quote.id,
            "quote": newest_quote.quote,
            "authors": newest_quote.authors,
            "timestamp": newest_quote.timestamp,
        }
    )


@app.route("/credits")
def credits():
    return render_template("credits.html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/cuppa")
def cuppa():
    abort(418)


@app.route("/err")
def err():
    abort(500)


@app.errorhandler(HTTPException)
def handle_http_error(e):
    if wants_json_response():
        return jsonify(error=e.name, description=e.description), e.code

    return (
        render_template(
            "error.html", code=e.code, name=e.name, description=e.description
        ),
        e.code,
    )


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.error(
        "Unhandled exception",
        exc_info=(type(e), e, e.__traceback__),
    )
    description = (
        "The server encountered an internal error and was unable to complete your request. "
        "Either the server is overloaded or there is an error in the application."
    )

    if wants_json_response():
        return jsonify(error="Internal Server Error", description=description), 500

    return (
        render_template(
            "error.html",
            code=500,
            name="Internal Server Error",
            description=description,
        ),
        500,
    )


if __name__ == "__main__":
    app.run(debug=not IS_PROD, host=HOST, port=PORT)
