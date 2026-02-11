import calendar as pycalendar
import json
import logging
import os
import random as randlib
import re
import secrets
import sqlite3
import smtplib
import threading
import time as timelib
from collections import Counter
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from urllib.parse import urljoin
from zoneinfo import ZoneInfo  # Python 3.9+

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
    send_from_directory,
    session,
    url_for,
)
from pywebpush import WebPushException, webpush

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
EDIT_PIN = os.getenv("EDIT_PIN", "").strip()
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@example.com").strip()
WEEKLY_EMAIL_ENABLED = (
    os.getenv("WEEKLY_EMAIL_ENABLED", "false").strip().lower()
    in {"1", "true", "yes", "y", "on"}
)
WEEKLY_EMAIL_TO_SEED = [
    email.strip()
    for email in os.getenv("WEEKLY_EMAIL_TO_SEED", "").split(",")
    if email.strip()
]
WEEKLY_EMAIL_FROM = os.getenv("WEEKLY_EMAIL_FROM", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
except ValueError:
    SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE = 9


app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # prevents JS from reading cookie
    SESSION_COOKIE_SECURE=IS_PROD,  # only send cookie over HTTPS in prod
    SESSION_COOKIE_SAMESITE="Lax",  # protects against CSRF
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),
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


def quote_to_dict(quote) -> dict:
    return {
        "id": quote.id,
        "quote": quote.quote,
        "authors": quote.authors,
        "timestamp": quote.timestamp,
        "context": quote.context,
        "stats": getattr(quote, "stats", {}),
    }


def get_push_subscribe_token() -> str:
    token = session.get("push_subscribe_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["push_subscribe_token"] = token
    return token


def get_email_subscribe_token() -> str:
    token = session.get("email_subscribe_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["email_subscribe_token"] = token
    return token


def get_ai_request_token() -> str:
    token = session.get("ai_request_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["ai_request_token"] = token
    return token


def get_push_db_path() -> str:
    if getattr(quote_store, "_local", None):
        return quote_store._local.filepath
    return os.getenv("QUOTEBOOK_DB", "qb.db")


def ensure_scheduler_table() -> bool:
    db_path = get_push_db_path()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                    job_name TEXT NOT NULL,
                    run_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (job_name, run_key)
                )
                """
            )
            row = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'scheduled_job_runs'
                """
            ).fetchone()
        return bool(row)
    except sqlite3.Error as exc:
        app.logger.error("Unable to ensure scheduled_job_runs table: %s", exc)
        return False


def ensure_weekly_email_recipients_table() -> bool:
    db_path = get_push_db_path()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_email_recipients (
                    email TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL
                )
                """
            )
            row = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'weekly_email_recipients'
                """
            ).fetchone()
        return bool(row)
    except sqlite3.Error as exc:
        app.logger.error("Unable to ensure weekly_email_recipients table: %s", exc)
        return False


def seed_weekly_email_recipients_from_env() -> None:
    if not WEEKLY_EMAIL_TO_SEED:
        return
    if not ensure_weekly_email_recipients_table():
        app.logger.error("Skipping weekly email recipient seed: recipients table missing.")
        return
    with sqlite3.connect(get_push_db_path()) as conn:
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM weekly_email_recipients"
        ).fetchone()[0]
        if existing_count > 0:
            return
        now = int(timelib.time())
        conn.executemany(
            """
            INSERT OR IGNORE INTO weekly_email_recipients (email, created_at)
            VALUES (?, ?)
            """,
            [(email, now) for email in WEEKLY_EMAIL_TO_SEED],
        )
    app.logger.info(
        "Seeded %s weekly email recipient(s) from environment.",
        len(WEEKLY_EMAIL_TO_SEED),
    )


def get_weekly_email_recipients() -> list[str]:
    if not ensure_weekly_email_recipients_table():
        return []
    with sqlite3.connect(get_push_db_path()) as conn:
        rows = conn.execute(
            """
            SELECT email
            FROM weekly_email_recipients
            ORDER BY created_at ASC, email ASC
            """
        ).fetchall()
    return [row[0].strip() for row in rows if row and row[0] and row[0].strip()]


def is_valid_email_address(email: str) -> bool:
    if not email:
        return False
    pattern = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE)
    return bool(pattern.match(email))


def add_weekly_email_recipient(email: str) -> bool:
    normalized = (email or "").strip().lower()
    if not is_valid_email_address(normalized):
        return False
    if not ensure_weekly_email_recipients_table():
        app.logger.error("Cannot add weekly email recipient: recipients table missing.")
        return False
    now = int(timelib.time())
    with sqlite3.connect(get_push_db_path()) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO weekly_email_recipients (email, created_at)
            VALUES (?, ?)
            """,
            (normalized, now),
        )
    return cur.rowcount > 0


def claim_scheduled_run(job_name: str, run_key: str) -> bool:
    if not ensure_scheduler_table():
        app.logger.error("Cannot claim scheduled run: scheduled_job_runs table missing.")
        return False
    try:
        with sqlite3.connect(get_push_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO scheduled_job_runs (job_name, run_key, created_at)
                VALUES (?, ?, ?)
                """,
                (job_name, run_key, int(timelib.time())),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def release_scheduled_run(job_name: str, run_key: str) -> None:
    if not ensure_scheduler_table():
        app.logger.error("Cannot release scheduled run: scheduled_job_runs table missing.")
        return
    with sqlite3.connect(get_push_db_path()) as conn:
        conn.execute(
            "DELETE FROM scheduled_job_runs WHERE job_name = ? AND run_key = ?",
            (job_name, run_key),
        )


def weekly_email_is_configured() -> bool:
    return bool(WEEKLY_EMAIL_ENABLED and SMTP_HOST and get_weekly_email_recipients())


def _digest_quote_payload(quote) -> dict:
    quote_time = datetime.fromtimestamp(quote.timestamp, tz=UK_TZ).strftime(
        "%Y-%m-%d %H:%M"
    )
    return {
        "id": quote.id,
        "timestamp_uk": quote_time,
        "authors": quote.authors or [],
        "quote": quote.quote,
        "context": quote.context or "",
        "stats": getattr(quote, "stats", {}),
    }


def build_weekly_digest_email(now_uk: datetime) -> tuple[str, str]:
    start_uk = now_uk - timedelta(days=7)
    weekly_quotes = quote_store.get_quotes_between(
        int(start_uk.timestamp()),
        int(now_uk.timestamp()),
    )
    weekly_quotes = sorted(weekly_quotes, key=lambda q: (q.timestamp, q.id), reverse=True)
    all_quotes = sorted(
        quote_store.get_all_quotes(),
        key=lambda q: (q.timestamp, q.id),
        reverse=True,
    )

    top_authors = Counter(
        author.strip()
        for q in weekly_quotes
        for author in q.authors
        if isinstance(author, str) and author.strip()
    ).most_common(5)
    top_authors_text = ", ".join(f"{name} ({count})" for name, count in top_authors)
    if not top_authors_text:
        top_authors_text = "No authors recorded this week."
    all_time_top_authors = [
        {"name": speaker, "count": count}
        for speaker, count in quote_store.get_speaker_counts()[:8]
    ]

    digest_data = {
        "mode": "weekly_email",
        "api_context": "This text is generated for an API response that will be sent by email.",
        "window_uk": {
            "start": start_uk.strftime("%Y-%m-%d %H:%M"),
            "end": now_uk.strftime("%Y-%m-%d %H:%M"),
        },
        "counts": {
            "new_quotes": len(weekly_quotes),
            "total_quotes": len(all_quotes),
        },
        "weekly_top_authors": [
            {"name": name, "count": count} for name, count in top_authors
        ],
        "all_time_top_authors": all_time_top_authors,
        "weekly_quotes": [_digest_quote_payload(q) for q in weekly_quotes[:40]],
        "recent_existing_quotes": [_digest_quote_payload(q) for q in all_quotes[:40]],
    }

    if ai_worker.can_generate:
        try:
            return ai_worker.generate_weekly_digest(digest_data)
        except Exception as exc:
            app.logger.warning("AI weekly digest failed; using fallback digest: %s", exc)

    subject = f"Quote Book Weekly Digest ({len(weekly_quotes)} new)"

    lines = [
        "Quote Book weekly update",
        "",
        f"Window (UK): {start_uk.strftime('%d %b %Y %H:%M')} to {now_uk.strftime('%d %b %Y %H:%M')}",
        f"New quotes: {len(weekly_quotes)}",
        f"Total quotes: {len(all_quotes)}",
        f"Top speakers: {top_authors_text}",
        "",
    ]

    if weekly_quotes:
        lines.append("Latest quotes:")
        for quote in weekly_quotes[:10]:
            quote_time = datetime.fromtimestamp(quote.timestamp, tz=UK_TZ).strftime(
                "%d %b %H:%M"
            )
            authors = ", ".join(quote.authors) if quote.authors else "Unknown"
            lines.append(f"- #{quote.id} [{quote_time}] {authors}: {quote.quote}")
    else:
        lines.append("No new quotes were added this week.")

    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    sender = WEEKLY_EMAIL_FROM or SMTP_USER
    if not sender:
        raise RuntimeError("WEEKLY_EMAIL_FROM or SMTP_USER must be configured.")
    recipients = get_weekly_email_recipients()
    if not recipients:
        raise RuntimeError("No weekly email recipients configured in database.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(message)
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        if SMTP_USE_TLS:
            server.starttls()
            server.ehlo()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.send_message(message)


def maybe_send_weekly_email_digest(now_uk: datetime | None = None) -> bool:
    if not weekly_email_is_configured():
        return False

    now_uk = now_uk or datetime.now(UK_TZ)
    if now_uk.weekday() != 0:
        return False

    scheduled_time = datetime.combine(now_uk.date(), time(hour=7, minute=0), tzinfo=UK_TZ)
    if now_uk < scheduled_time:
        return False

    run_key = now_uk.date().isoformat()
    job_name = "weekly_email_digest"
    if not claim_scheduled_run(job_name, run_key):
        return False

    try:
        subject, body = build_weekly_digest_email(now_uk)
        send_email(subject, body)
        app.logger.info(
            "Weekly digest email sent for %s to %s.",
            run_key,
            get_weekly_email_recipients(),
        )
        return True
    except Exception:
        release_scheduled_run(job_name, run_key)
        raise


_weekly_scheduler_thread: threading.Thread | None = None
_weekly_scheduler_lock = threading.Lock()


def weekly_email_scheduler_loop() -> None:
    app.logger.info("Weekly email scheduler started (Monday 07:00 UK).")
    while True:
        try:
            maybe_send_weekly_email_digest()
        except Exception as exc:
            app.logger.warning("Weekly email digest failed: %s", exc)
        timelib.sleep(60)


def start_weekly_email_scheduler() -> None:
    global _weekly_scheduler_thread

    if not ensure_scheduler_table() or not ensure_weekly_email_recipients_table():
        app.logger.warning("Weekly email scheduler unavailable: required tables are missing.")
        return

    seed_weekly_email_recipients_from_env()

    if not weekly_email_is_configured():
        app.logger.info("Weekly email scheduler disabled or not configured.")
        return

    # In dev mode with Flask reloader, only start on the child process.
    if not IS_PROD and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        return

    with _weekly_scheduler_lock:
        if _weekly_scheduler_thread and _weekly_scheduler_thread.is_alive():
            return

        _weekly_scheduler_thread = threading.Thread(
            target=weekly_email_scheduler_loop,
            name="weekly-email-scheduler",
            daemon=True,
        )
        _weekly_scheduler_thread.start()


def ensure_push_table() -> None:
    db_path = get_push_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint TEXT PRIMARY KEY,
                subscription TEXT NOT NULL,
                user_agent TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )


def save_push_subscription(subscription: dict, user_agent: str | None = None) -> bool:
    endpoint = (subscription or {}).get("endpoint")
    if not endpoint:
        return False
    ensure_push_table()
    payload = json.dumps(subscription, ensure_ascii=False)
    now = int(timelib.time())
    with sqlite3.connect(get_push_db_path()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO push_subscriptions
            (endpoint, subscription, user_agent, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (endpoint, payload, user_agent or "", now, now),
        )
    return True


def delete_push_subscription(endpoint: str) -> None:
    if not endpoint:
        return
    ensure_push_table()
    with sqlite3.connect(get_push_db_path()) as conn:
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        )


def load_push_subscriptions() -> list[dict]:
    ensure_push_table()
    with sqlite3.connect(get_push_db_path()) as conn:
        rows = conn.execute(
            "SELECT subscription FROM push_subscriptions"
        ).fetchall()
    subscriptions = []
    for row in rows:
        try:
            subscriptions.append(json.loads(row[0]))
        except json.JSONDecodeError:
            continue
    return subscriptions


def send_push_notification(title: str, body: str, url: str) -> int:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        app.logger.warning("Push notification skipped: missing VAPID keys.")
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
        }
    )

    subscriptions = load_push_subscriptions()
    if not subscriptions:
        return 0

    sent = 0
    for subscription in subscriptions:
        endpoint = subscription.get("endpoint")
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_EMAIL},
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410) and endpoint:
                delete_push_subscription(endpoint)
            else:
                app.logger.warning("Push failed for %s: %s", endpoint, exc)
        except Exception as exc:
            app.logger.warning("Push error for %s: %s", endpoint, exc)
    return sent


app.jinja_env.filters["month_name"] = month_name
app.jinja_env.filters["to_uk_datetime"] = to_uk_datetime
app.jinja_env.filters["uk_time"] = uk_time
app.jinja_env.filters["uk_date"] = uk_date

# ─────────────────────────────────────────────
# PWA assets
# ─────────────────────────────────────────────


@app.route("/sw.js")
def sw_js():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/manifest.webmanifest")
def manifest_webmanifest():
    return send_from_directory(
        "static", "manifest.webmanifest", mimetype="application/manifest+json"
    )


@app.route("/offline")
def offline_page():
    return send_from_directory("static", "offline.html", mimetype="text/html")


@app.route("/pwa")
def pwa_diag():
    return render_template("pwa.html")


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
        edit_enabled=bool(EDIT_PIN),
        vapid_public_key=VAPID_PUBLIC_KEY,
        push_subscribe_token=get_push_subscribe_token(),
        email_subscribe_token=get_email_subscribe_token(),
    )


@app.route("/add_quote", methods=["GET", "POST"])
def add_quote():
    if request.method == "POST":
        # Get form inputs
        quote_text = request.form.get("quote_text", "").strip()
        context = request.form.get("context", "").strip()
        author_raw = request.form.get("author_info", "Unknown").strip()
        quote_datetime_raw = request.form.get("quote_datetime", "").strip()

        # Only proceed if there is quote text
        if quote_text:
            # Parse authors
            authors = quote_store.parse_authors(author_raw)

            # Use selected UK date/time when provided, otherwise fallback to now.
            timestamp = datetime_handler.get_current_uk_timestamp()
            if quote_datetime_raw:
                try:
                    selected_dt = datetime.strptime(quote_datetime_raw, "%Y-%m-%dT%H:%M")
                    selected_dt = selected_dt.replace(tzinfo=UK_TZ)
                    timestamp = int(selected_dt.timestamp())
                except ValueError:
                    app.logger.warning(
                        "Invalid quote_datetime value '%s'; using current time.",
                        quote_datetime_raw,
                    )

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
            try:
                author_name = ", ".join(new_quote.authors) or "Unknown"
                sent_count = send_push_notification(
                    "People are chatting...",
                    f"New quote by {author_name}",
                    build_public_url(url_for("quote_by_id", quote_id=new_quote.id)),
                )
                app.logger.info("Push notifications sent: %s", sent_count)
            except Exception as exc:
                app.logger.warning("Push notification failed: %s", exc)

            return redirect(url_for("index"))

    # GET request or empty quote_text
    return render_template("add_quote.html")


@app.route("/ai")
def ai():
    return render_template(
        "ai.html",
        ai_available=ai_worker.can_generate,
        ai_request_token=get_ai_request_token() if ai_worker.can_generate else "",
    )


@app.route("/ai_screenplay", methods=["POST"])
def ai_screenplay():
    if not ai_worker.can_generate:
        return (
            jsonify(
                error="AI screenplay generation is disabled. Set OPENROUTER_KEY to enable."
            ),
            503,
        )
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token or token != session.get("ai_request_token"):
        return jsonify(error="Invalid AI request token."), 403
    app.logger.info("AI screenplay requested.")
    quotes = quote_store.get_all_quotes()
    scored_quotes = [
        (q, ai_worker.classify_funny_score(q.quote, q.authors, q.stats)) for q in quotes
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
        permalink_base=build_public_url("/quote/"),
        edit_enabled=bool(EDIT_PIN),
        edit_authed=bool(session.get("edit_authed")),
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
        permalink_base=build_public_url("/quote/"),
        edit_enabled=bool(EDIT_PIN),
        edit_authed=bool(session.get("edit_authed")),
    )


@app.route("/quote/<int:quote_id>/edit", methods=["GET", "POST"])
def edit_quote(quote_id):
    if not EDIT_PIN:
        return (
            render_template(
                "error.html",
                code=503,
                name="Edit Disabled",
                description="Editing is disabled. Set EDIT_PIN to enable editing.",
            ),
            503,
        )

    quote = quote_store.get_quote_by_id(quote_id)
    if not quote:
        abort(404)

    pin_error = None
    edit_error = None

    if request.method == "POST":
        action = request.form.get("action", "").strip().lower()

        if action == "pin":
            pin = (request.form.get("pin") or "").strip()
            if pin == EDIT_PIN:
                session["edit_authed"] = True
                session.permanent = True
                return redirect(url_for("edit_quote", quote_id=quote_id))
            pin_error = "Incorrect PIN. Try again."

        if action == "edit":
            if not session.get("edit_authed"):
                pin_error = "Please enter your PIN to edit."
            else:
                quote_text = request.form.get("quote_text", "").strip()
                context = request.form.get("context", "").strip()
                author_raw = request.form.get("author_info", "Unknown").strip()

                if not quote_text:
                    edit_error = "Quote text cannot be empty."
                else:
                    authors = quote_store.parse_authors(author_raw)
                    updated = quote_store.update_quote(
                        quote_id=quote_id,
                        quote_text=quote_text,
                        authors=authors,
                        context=context,
                    )
                    if not updated:
                        abort(404)
                    return redirect(url_for("quote_by_id", quote_id=quote_id))

    return render_template(
        "edit_quote.html",
        quote=quote,
        pin_error=pin_error,
        edit_error=edit_error,
        is_authed=bool(session.get("edit_authed")),
    )


@app.route("/edit", methods=["GET", "POST"])
def edit_index():
    if not EDIT_PIN:
        return (
            render_template(
                "error.html",
                code=503,
                name="Edit Disabled",
                description="Editing is disabled. Set EDIT_PIN to enable editing.",
            ),
            503,
        )

    pin_error = None

    if request.method == "POST":
        action = request.form.get("action", "").strip().lower()
        if action == "pin":
            pin = (request.form.get("pin") or "").strip()
            if pin == EDIT_PIN:
                session["edit_authed"] = True
                session.permanent = True
                return redirect(url_for("edit_index"))
            pin_error = "Incorrect PIN. Try again."

    page = request.args.get("page", 1, type=int)
    quotes = []
    total_pages = 1
    if session.get("edit_authed"):
        quotes, page, total_pages = quote_store.get_quote_page(None, page, 10)

    return render_template(
        "edit_index.html",
        quotes=quotes,
        page=page,
        total_pages=total_pages,
        pin_error=pin_error,
        is_authed=bool(session.get("edit_authed")),
    )


@app.route("/all_quotes")
def all_quotes():
    speaker_filter = request.args.get("speaker", None)
    sort_order = (request.args.get("order") or "oldest").strip().lower()
    if sort_order not in ("oldest", "newest"):
        sort_order = "oldest"
    page = request.args.get("page", 1, type=int)

    paginated_quotes, page, total_pages = quote_store.get_quote_page(
        speaker_filter,
        page,
        PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE,
        sort_order,
    )
    sorted_speakers = quote_store.get_speaker_counts()

    return render_template(
        "all_quotes.html",
        quotes=paginated_quotes,
        selected_speaker=speaker_filter,
        sort_order=sort_order,
        speakers=sorted_speakers,
        page=page,
        total_pages=total_pages,
        per_page=PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE,
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
    avg_chars = (
        round(sum(len(q.quote) for q in quotes) / len(quotes), 1) if quotes else 0
    )

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
        bucket["percent"] = (
            int((bucket["count"] / max_bucket) * 100) if max_bucket else 0
        )

    total_battle_entries = sum(q.stats.get("battles", 0) for q in quotes)
    total_battles = total_battle_entries // 2 if total_battle_entries else 0
    most_battled = max(quotes, key=lambda q: q.stats.get("battles", 0), default=None)

    top_winners = sorted(quotes, key=lambda q: q.stats.get("wins", 0), reverse=True)
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
    avg_funny = (
        round(sum(score for _, score in funny_scores) / len(funny_scores), 2)
        if funny_scores
        else 0
    )

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


@app.route("/api/quotes")
def api_quotes():
    speaker = request.args.get("speaker")
    page = request.args.get("page", type=int)
    per_page = request.args.get("per_page", type=int)
    order = (request.args.get("order") or "oldest").strip().lower()
    if order not in ("oldest", "newest", "desc", "reverse"):
        order = "oldest"
    reverse_sort = order in ("newest", "desc", "reverse")

    quotes = quote_store.get_all_quotes()
    if speaker:
        speaker_lower = speaker.lower()
        quotes = [
            q
            for q in quotes
            if any(speaker_lower == author.lower() for author in q.authors)
        ]

    quotes = sorted(quotes, key=lambda q: (q.timestamp, q.id), reverse=reverse_sort)
    total = len(quotes)

    if page and per_page and per_page > 0:
        page = max(1, page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        end = start + per_page
        quotes = quotes[start:end]
    else:
        total_pages = 1

    return jsonify(
        quotes=[quote_to_dict(q) for q in quotes],
        total=total,
        page=page or 1,
        per_page=per_page or total,
        total_pages=total_pages,
    )


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return jsonify(error="Push notifications are not configured."), 503

    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token or token != session.get("push_subscribe_token"):
        return jsonify(error="Invalid subscription token."), 403
    subscription = data.get("subscription") or data
    user_agent = data.get("userAgent") or request.headers.get("User-Agent", "")

    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return jsonify(error="Invalid subscription payload."), 400

    saved = save_push_subscription(subscription, user_agent)
    return jsonify(ok=bool(saved))


@app.route("/api/push/token", methods=["GET"])
def api_push_token():
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return jsonify(error="Push notifications are not configured."), 503
    return jsonify(token=get_push_subscribe_token())


@app.route("/api/email/token", methods=["GET"])
def api_email_token():
    return jsonify(token=get_email_subscribe_token())


@app.route("/api/email/subscribe", methods=["POST"])
def api_email_subscribe():
    if not ensure_weekly_email_recipients_table():
        return jsonify(error="Email subscriptions are unavailable right now."), 503

    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token or token != session.get("email_subscribe_token"):
        return jsonify(error="Invalid subscription token."), 403

    email = (data.get("email") or "").strip().lower()
    if not is_valid_email_address(email):
        return jsonify(error="Please enter a valid email address."), 400

    created = add_weekly_email_recipient(email)
    session["email_subscribe_token"] = secrets.token_urlsafe(24)
    return jsonify(
        ok=True,
        already_subscribed=not created,
    )


@app.route("/api/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify(error="Missing endpoint."), 400
    delete_push_subscription(endpoint)
    return jsonify(ok=True)

@app.route("/credits")
def credits():
    return render_template("credits.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/cuppa")
def cuppa():
    abort(418)


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


start_weekly_email_scheduler()


if __name__ == "__main__":
    app.run(debug=not IS_PROD, host=HOST, port=PORT)
