import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_services import AppServiceConfig, AppServices
from blueprints.api import create_api_blueprint
from blueprints.web import create_web_blueprint
from quote_anarchy import QuoteAnarchyService
from quote_client import QuoteClient

UK_TZ = ZoneInfo("Europe/London")


class DummyAI:
    can_generate = False

    @staticmethod
    def classify_funny_score(_quote, _authors, _stats):
        return 5.0


def _register_legacy_endpoint_aliases(app: Flask) -> None:
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


@pytest.fixture
def app_ctx(tmp_path):
    db_path = tmp_path / "test.db"
    quote_store = QuoteClient(base_url=None, db_path=str(db_path))
    base_ts = 1_735_680_000
    quote_store.add_quote(
        quote_text="First test quote.",
        authors=["Alice"],
        timestamp=base_ts,
        context="Context 1",
    )
    quote_store.add_quote(
        quote_text="Second test quote.",
        authors=["Bob", "Alice"],
        timestamp=base_ts + 60,
        context="Context 2",
    )

    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    services = AppServices(
        app=app,
        quote_store=quote_store,
        ai_worker=DummyAI(),
        uk_tz=UK_TZ,
        config=AppServiceConfig(
            public_base_url="http://localhost:8040",
            vapid_public_key="test-public",
            vapid_private_key="test-private",
            vapid_email="mailto:test@example.com",
            weekly_email_enabled=True,
            weekly_email_to_seed=[],
            weekly_email_from="quotes@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="",
            smtp_pass="",
            smtp_use_tls=True,
            smtp_use_ssl=False,
            is_prod=False,
        ),
    )

    app.jinja_env.filters["month_name"] = services.month_name
    app.jinja_env.filters["to_uk_datetime"] = services.to_uk_datetime
    app.jinja_env.filters["uk_time"] = services.uk_time
    app.jinja_env.filters["uk_date"] = services.uk_date

    quote_anarchy_service = QuoteAnarchyService(
        db_path=str(db_path),
        quote_store=quote_store,
        black_cards_path=str(
            ROOT / "static" / "assets" / "quote-anarchy" / "black-cards.json"
        ),
    )

    app.register_blueprint(
        create_web_blueprint(
            quote_store=quote_store,
            ai_worker=DummyAI(),
            services=services,
            quote_anarchy_service=quote_anarchy_service,
            uk_tz=UK_TZ,
            edit_pin="1234",
            vapid_public_key="test-public",
            per_page_quote_limit=9,
            support_url="https://example.com/support",
            support_label="Support Quote Book",
            sponsor_contact_url="https://example.com/sponsor",
            sponsor_contact_email="sponsor@example.com",
            affiliate_disclosure="Some outbound links may be affiliate links.",
            adsense_client_id="ca-pub-1234567890",
            adsense_slot_inline="1234567890",
            adsense_slot_footer="0987654321",
            google_adsense_account="ca-pub-1234567890",
            robots_disallow_all=False,
        )
    )
    app.register_blueprint(
        create_api_blueprint(
            quote_store=quote_store,
            services=services,
            quote_anarchy_service=quote_anarchy_service,
            vapid_public_key="test-public",
            vapid_private_key="test-private",
        )
    )
    _register_legacy_endpoint_aliases(app)

    services.refresh_stats_cache("tests")
    return {"app": app, "services": services, "quote_store": quote_store}


@pytest.fixture
def app(app_ctx):
    return app_ctx["app"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def services(app_ctx):
    return app_ctx["services"]


@pytest.fixture
def quote_store(app_ctx):
    return app_ctx["quote_store"]
