import os
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import ai_helpers
from app_services import AppServiceConfig, AppServices
from blueprints.api import create_api_blueprint
from blueprints.web import create_web_blueprint
from quote_client import get_quote_client

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
ROBOTS_DISALLOW_ALL = (
    os.getenv("ROBOTS_DISALLOW_ALL", "false").strip().lower() in {"1", "true", "yes", "on"}
)
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
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=IS_PROD,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),
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
        weekly_digest_sponsor_line=WEEKLY_DIGEST_SPONSOR_LINE,
    ),
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
        services=services,
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


@app.before_request
def start_timer():
    services.start_timer()


@app.after_request
def log_request(response):
    return services.log_request(response)


@app.teardown_request
def log_exception(exception):
    services.log_exception(exception)


@app.before_request
def refresh_qb():
    services.refresh_qb()


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    if isinstance(e, HTTPException):
        if wants_json_response():
            return jsonify(error=e.name, description=e.description), e.code
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


services.refresh_stats_cache("startup")
services.start_weekly_email_scheduler()


if __name__ == "__main__":
    app.run(debug=not IS_PROD, host=HOST, port=PORT)
