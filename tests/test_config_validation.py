from zoneinfo import ZoneInfo

from app_services import AppServiceConfig, AppServices


class DummyAI:
    can_generate = False

    @staticmethod
    def classify_funny_score(_quote, _authors, _stats):
        return 5.0


def test_runtime_config_validation_flags_partial_settings(app, quote_store):
    services = AppServices(
        app=app,
        quote_store=quote_store,
        ai_worker=DummyAI(),
        uk_tz=ZoneInfo("Europe/London"),
        config=AppServiceConfig(
            public_base_url="example.com",
            vapid_public_key="public-only",
            vapid_private_key="",
            vapid_email="mailto:test@example.com",
            weekly_email_enabled=True,
            weekly_email_to_seed=[],
            weekly_email_from="",
            smtp_host="",
            smtp_port=587,
            smtp_user="",
            smtp_pass="",
            smtp_use_tls=True,
            smtp_use_ssl=True,
            is_prod=False,
        ),
    )

    warnings = services.validate_runtime_config()
    assert len(warnings) >= 4
    assert any("PUBLIC_BASE_URL" in warning for warning in warnings)
    assert any("VAPID_PUBLIC_KEY" in warning for warning in warnings)
    assert any("WEEKLY_EMAIL_ENABLED" in warning for warning in warnings)
