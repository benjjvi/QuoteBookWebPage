import os
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, render_template, request
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import HTTPException

import ai_helpers
from api_errors import error_response
from app_services import AppServiceConfig, AppServices
from blueprints.api import create_api_blueprint
from blueprints.web import create_web_blueprint
from quote_blackline import QuoteBlacklineService
from quote_anarchy import QuoteAnarchyService
from quote_who_said_it import QuoteWhoSaidItService
from quote_client import get_quote_client

load_dotenv()


class RequestAwareSessionInterface(SecureCookieSessionInterface):
    """Allow localhost HTTP sessions while keeping secure cookies for HTTPS traffic."""

    def get_cookie_secure(self, app: Flask) -> bool:
        secure_config = bool(app.config.get("SESSION_COOKIE_SECURE"))
        if not secure_config:
            return False

        forwarded_proto = (
            request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        )
        if request.is_secure or forwarded_proto == "https":
            return True

        host = (request.host or "").split(":", 1)[0].strip().lower()
        if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
            return False

        return True

app = Flask(__name__)
app.session_interface = RequestAwareSessionInterface()
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
WEEKLY_EMAIL_ENABLED = os.getenv("WEEKLY_EMAIL_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
WEEKLY_EMAIL_TO_SEED = [
    email.strip()
    for email in os.getenv("WEEKLY_EMAIL_TO_SEED", "").split(",")
    if email.strip()
]
WEEKLY_EMAIL_FROM = os.getenv("WEEKLY_EMAIL_FROM", "").strip()
WEEKLY_DIGEST_SPONSOR_LINE = os.getenv("WEEKLY_DIGEST_SPONSOR_LINE", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()
SUPPORT_LABEL = os.getenv("SUPPORT_LABEL", "Support Quote Book").strip()
SPONSOR_CONTACT_URL = os.getenv("SPONSOR_CONTACT_URL", "").strip()
SPONSOR_CONTACT_EMAIL = os.getenv("SPONSOR_CONTACT_EMAIL", "").strip()
AFFILIATE_DISCLOSURE = os.getenv("AFFILIATE_DISCLOSURE", "").strip()
ADSENSE_CLIENT_ID = os.getenv("ADSENSE_CLIENT_ID", "").strip()
ADSENSE_SLOT_INLINE = os.getenv("ADSENSE_SLOT_INLINE", "").strip()
ADSENSE_SLOT_FOOTER = os.getenv("ADSENSE_SLOT_FOOTER", "").strip()
GOOGLE_ADSENSE_ACCOUNT = (
    os.getenv("GOOGLE_ADSENSE_ACCOUNT", "").strip() or ADSENSE_CLIENT_ID
)
ROBOTS_DISALLOW_ALL = os.getenv("ROBOTS_DISALLOW_ALL", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
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
try:
    SMTP_SEND_DELAY_SECONDS = float(os.getenv("SMTP_SEND_DELAY_SECONDS", "0"))
except ValueError:
    SMTP_SEND_DELAY_SECONDS = 0.0
WEEKLY_SCHEDULER_MODE = os.getenv("WEEKLY_SCHEDULER_MODE", "auto").strip().lower()
PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE = 9
STATIC_ASSET_CACHE_TTL = timedelta(days=7)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=IS_PROD,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),
    SEND_FILE_MAX_AGE_DEFAULT=STATIC_ASSET_CACHE_TTL,
    TEMPLATES_AUTO_RELOAD=not IS_PROD,
)

services = AppServices(
    app=app,
    quote_store=quote_store,
    ai_worker=ai_worker,
    uk_tz=UK_TZ,
    config=AppServiceConfig(
        public_base_url=PUBLIC_BASE_URL,
        vapid_public_key=VAPID_PUBLIC_KEY,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_email=VAPID_EMAIL,
        weekly_email_enabled=WEEKLY_EMAIL_ENABLED,
        weekly_email_to_seed=WEEKLY_EMAIL_TO_SEED,
        weekly_email_from=WEEKLY_EMAIL_FROM,
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER,
        smtp_pass=SMTP_PASS,
        smtp_use_tls=SMTP_USE_TLS,
        smtp_use_ssl=SMTP_USE_SSL,
        is_prod=IS_PROD,
        weekly_scheduler_mode=WEEKLY_SCHEDULER_MODE,
        weekly_digest_sponsor_line=WEEKLY_DIGEST_SPONSOR_LINE,
        smtp_send_delay_seconds=SMTP_SEND_DELAY_SECONDS,
    ),
)

quote_anarchy_service = QuoteAnarchyService(
    db_path=services.get_push_db_path(),
    quote_store=quote_store,
    black_cards_path=os.path.join(
        app.static_folder or "static",
        "assets",
        "quote-anarchy",
        "black-cards.json",
    ),
)
quote_blackline_service = QuoteBlacklineService(
    db_path=services.get_push_db_path(),
    quote_store=quote_store,
)
quote_who_said_service = QuoteWhoSaidItService(
    db_path=services.get_push_db_path(),
    quote_store=quote_store,
)

app.jinja_env.filters["month_name"] = services.month_name
app.jinja_env.filters["to_uk_datetime"] = services.to_uk_datetime
app.jinja_env.filters["uk_time"] = services.uk_time
app.jinja_env.filters["uk_date"] = services.uk_date

services.configure_logging()
services.validate_runtime_config()

app.register_blueprint(
    create_web_blueprint(
        quote_store=quote_store,
        ai_worker=ai_worker,
        services=services,
        quote_anarchy_service=quote_anarchy_service,
        quote_blackline_service=quote_blackline_service,
        quote_who_said_service=quote_who_said_service,
        uk_tz=UK_TZ,
        edit_pin=EDIT_PIN,
        vapid_public_key=VAPID_PUBLIC_KEY,
        per_page_quote_limit=PER_PAGE_QUOTE_LIMIT_FOR_ALL_QUOTES_PAGE,
        support_url=SUPPORT_URL,
        support_label=SUPPORT_LABEL,
        sponsor_contact_url=SPONSOR_CONTACT_URL,
        sponsor_contact_email=SPONSOR_CONTACT_EMAIL,
        affiliate_disclosure=AFFILIATE_DISCLOSURE,
        adsense_client_id=ADSENSE_CLIENT_ID,
        adsense_slot_inline=ADSENSE_SLOT_INLINE,
        adsense_slot_footer=ADSENSE_SLOT_FOOTER,
        google_adsense_account=GOOGLE_ADSENSE_ACCOUNT,
        robots_disallow_all=ROBOTS_DISALLOW_ALL,
    )
)
app.register_blueprint(
    create_api_blueprint(
        quote_store=quote_store,
        ai_worker=ai_worker,
        services=services,
        quote_anarchy_service=quote_anarchy_service,
        quote_blackline_service=quote_blackline_service,
        quote_who_said_service=quote_who_said_service,
        vapid_public_key=VAPID_PUBLIC_KEY,
        vapid_private_key=VAPID_PRIVATE_KEY,
    )
)


def register_legacy_endpoint_aliases() -> None:
    """Keep historical un-prefixed endpoint names working after blueprint split."""
    rules = list(app.url_map.iter_rules())
    for rule in rules:
        if "." not in rule.endpoint:
            continue
        namespace, endpoint = rule.endpoint.split(".", 1)
        if namespace not in {"web", "api"}:
            continue
        if endpoint in app.view_functions:
            continue

        methods = [m for m in rule.methods if m not in {"HEAD", "OPTIONS"}]
        app.add_url_rule(
            rule.rule,
            endpoint=endpoint,
            view_func=app.view_functions[rule.endpoint],
            defaults=rule.defaults,
            methods=methods or None,
        )


register_legacy_endpoint_aliases()


def wants_json_response() -> bool:
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best
    if not best:
        return False
    return (
        best == "application/json"
        and request.accept_mimetypes[best] >= request.accept_mimetypes["text/html"]
    )


CSRF_PROTECTED_ENDPOINTS = {
    "web.add_quote",
    "add_quote",
    "web.battle",
    "battle",
    "web.mailbox",
    "mailbox",
    "web.unsubscribe_page",
    "unsubscribe_page",
    "web.edit_index",
    "edit_index",
    "web.edit_quote",
    "edit_quote",
    "web.social_quote_react",
    "social_quote_react",
    "web.social_quote_comment",
    "social_quote_comment",
    "web.ai_screenplay_render",
    "ai_screenplay_render",
}

RATE_LIMIT_RULES = {
    "POST:api.api_add_quote": {"limit": 20, "window_seconds": 60},
    "POST:api_add_quote": {"limit": 20, "window_seconds": 60},
    "POST:api.api_battle": {"limit": 120, "window_seconds": 60},
    "POST:api_battle": {"limit": 120, "window_seconds": 60},
    "GET:api.api_quotes": {"limit": 600, "window_seconds": 60},
    "GET:api_quotes": {"limit": 600, "window_seconds": 60},
    "GET:api.api_social_feed": {"limit": 360, "window_seconds": 60},
    "GET:api_social_feed": {"limit": 360, "window_seconds": 60},
    "GET:web.social_quote_post": {"limit": 240, "window_seconds": 60},
    "GET:social_quote_post": {"limit": 240, "window_seconds": 60},
    "POST:web.social_quote_react": {"limit": 180, "window_seconds": 60},
    "POST:social_quote_react": {"limit": 180, "window_seconds": 60},
    "POST:web.social_quote_comment": {"limit": 24, "window_seconds": 60},
    "POST:social_quote_comment": {"limit": 24, "window_seconds": 60},
}


@app.context_processor
def inject_security_tokens():
    return {
        "csrf_token": services.get_csrf_token,
    }


@app.before_request
def start_timer():
    services.start_timer()


@app.before_request
def enforce_form_csrf():
    if request.method != "POST":
        return None
    if request.endpoint not in CSRF_PROTECTED_ENDPOINTS:
        return None

    candidate = (
        (request.form.get("csrf_token") or "").strip()
        or (request.headers.get("X-CSRF-Token") or "").strip()
    )
    if services.validate_csrf_token(candidate):
        return None

    if wants_json_response():
        return error_response(
            status=403,
            code="csrf_invalid",
            message="CSRF token is missing or invalid.",
            details={"endpoint": request.endpoint},
        )

    return (
        render_template(
            "error.html",
            code=403,
            name="Forbidden",
            description="Your session token was missing or invalid. Refresh and try again.",
        ),
        403,
    )


@app.before_request
def enforce_rate_limits():
    method_endpoint = f"{request.method}:{request.endpoint}"
    rule = RATE_LIMIT_RULES.get(method_endpoint)
    if not rule:
        return None

    client_ip = services.get_request_client_ip()
    device_key = (
        (request.cookies.get("qb_social_device_id") or "").strip()
        if (
            request.endpoint.startswith("web.social_quote_")
            or request.endpoint.startswith("social_quote_")
        )
        else ""
    )
    actor = device_key or client_ip
    limiter_key = f"{request.endpoint}:{actor}"
    allowed, retry_after = services.consume_rate_limit(
        key=limiter_key,
        limit=int(rule["limit"]),
        window_seconds=int(rule["window_seconds"]),
    )
    if allowed:
        return None

    if wants_json_response():
        response, status = error_response(
            status=429,
            code="rate_limited",
            message="Too many requests. Please slow down and try again shortly.",
            details={
            "endpoint": request.endpoint,
            "method": request.method,
            "retry_after_seconds": retry_after,
            "limit": int(rule["limit"]),
            "window_seconds": int(rule["window_seconds"]),
            },
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, status

    return (
        render_template(
            "error.html",
            code=429,
            name="Too Many Requests",
            description=(
                "That action is being performed too quickly. "
                f"Please wait {retry_after} seconds and try again."
            ),
        ),
        429,
    )


@app.after_request
def log_request(response):
    return services.log_request(response)


@app.teardown_request
def log_exception(exception):
    services.log_exception(exception)


@app.before_request
def refresh_qb():
    endpoint = request.endpoint or ""
    if endpoint == "static" or request.path.startswith("/static/"):
        return None
    if request.path in {"/sw.js", "/manifest.webmanifest"}:
        return None

    services.maybe_run_scheduled_jobs_opportunistically()
    services.refresh_qb()


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    if isinstance(e, HTTPException):
        if wants_json_response():
            return error_response(
                status=e.code or 500,
                code=f"http_{(e.name or 'error').lower().replace(' ', '_')}",
                message=e.name or "HTTP error",
                details={"description": e.description or ""},
            )
        return (
            render_template(
                "error.html",
                code=e.code,
                name=e.name,
                description=e.description,
            ),
            e.code,
        )

    app.logger.error(
        "Unhandled exception",
        exc_info=(type(e), e, e.__traceback__),
    )
    description = (
        "The server encountered an internal error and was unable to complete your request. "
        "Either the server is overloaded or there is an error in the application."
    )

    if wants_json_response():
        return error_response(
            status=500,
            code="internal_server_error",
            message="Internal Server Error",
            details={"description": description},
        )

    return (
        render_template(
            "error.html",
            code=500,
            name="Internal Server Error",
            description=description,
        ),
        500,
    )


services.refresh_stats_cache("startup")
services.start_weekly_email_scheduler()


if __name__ == "__main__":
    app.run(debug=not IS_PROD, host=HOST, port=PORT)
