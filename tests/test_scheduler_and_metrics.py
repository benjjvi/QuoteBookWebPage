from datetime import datetime
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from pywebpush import WebPushException

UK_TZ = ZoneInfo("Europe/London")


@pytest.mark.usefixtures("services")
def test_weekly_digest_idempotency(services, monkeypatch):
    assert services.add_weekly_email_recipient("digest@example.com") is True

    monkeypatch.setattr(
        services,
        "build_weekly_digest_email",
        lambda _now: ("Weekly digest", "Body"),
    )

    sent_calls = []

    def fake_send_email(subject, body):
        sent_calls.append((subject, body))

    monkeypatch.setattr(services, "send_email", fake_send_email)

    monday = datetime(2026, 2, 9, 8, 0, tzinfo=UK_TZ)
    assert services.maybe_send_weekly_email_digest(monday) is True
    assert services.maybe_send_weekly_email_digest(monday) is False

    metrics = services.get_runtime_metrics()
    assert len(sent_calls) == 1
    assert "/unsubscribe" in sent_calls[0][1]
    archived = services.get_weekly_digest_archive(limit=3)
    assert len(archived) == 1
    assert archived[0]["run_key"] == "2026-02-09"
    assert "/unsubscribe" in archived[0]["body"]
    assert metrics["weekly_digest_sent"] >= 1
    assert metrics["weekly_digest_claim_conflict"] >= 1


@pytest.mark.usefixtures("services")
def test_weekly_digest_failure_releases_claim(services, monkeypatch):
    assert services.add_weekly_email_recipient("retry@example.com") is True

    monkeypatch.setattr(
        services,
        "build_weekly_digest_email",
        lambda _now: ("Weekly digest", "Body"),
    )

    monday = datetime(2026, 2, 16, 8, 0, tzinfo=UK_TZ)

    def failing_send_email(_subject, _body):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(services, "send_email", failing_send_email)
    with pytest.raises(RuntimeError):
        services.maybe_send_weekly_email_digest(monday)

    monkeypatch.setattr(services, "send_email", lambda _subject, _body: None)
    assert services.maybe_send_weekly_email_digest(monday) is True

    archived = services.get_weekly_digest_archive(limit=3)
    assert len(archived) == 1
    assert archived[0]["run_key"] == "2026-02-16"

    metrics = services.get_runtime_metrics()
    assert metrics["weekly_digest_failure"] >= 1
    assert metrics["weekly_digest_sent"] >= 1


@pytest.mark.usefixtures("services")
def test_push_metrics_success_and_failure(services, monkeypatch):
    services.save_push_subscription(
        {
            "endpoint": "https://push.example.com/sub",
            "keys": {"auth": "a", "p256dh": "b"},
        },
        "pytest",
    )

    monkeypatch.setattr("app_services.webpush", lambda **_kwargs: None)
    sent = services.send_push_notification("Title", "Body", "https://example.com")
    assert sent == 1

    monkeypatch.setattr(
        "app_services.webpush",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("push failed")),
    )
    sent_failed = services.send_push_notification(
        "Title", "Body", "https://example.com"
    )
    assert sent_failed == 0

    metrics = services.get_runtime_metrics()
    assert metrics["push_attempted"] >= 2
    assert metrics["push_sent"] >= 1
    assert metrics["push_failed"] >= 1


@pytest.mark.usefixtures("services")
def test_add_weekly_email_recipient_attempts_scheduler_start(services, monkeypatch):
    calls = []

    monkeypatch.setattr(
        services, "start_weekly_email_scheduler", lambda: calls.append("started")
    )

    assert services.add_weekly_email_recipient("scheduler@example.com") is True
    assert calls == ["started"]


@pytest.mark.usefixtures("services")
def test_push_403_prunes_subscription(services, monkeypatch):
    services.save_push_subscription(
        {
            "endpoint": "https://push.example.com/sub-403",
            "keys": {"auth": "a", "p256dh": "b"},
        },
        "pytest",
    )

    class DummyResponse:
        status_code = 403
        text = "forbidden"

    monkeypatch.setattr(
        "app_services.webpush",
        lambda **_kwargs: (_ for _ in ()).throw(
            WebPushException("forbidden", response=DummyResponse())
        ),
    )

    sent = services.send_push_notification("Title", "Body", "https://example.com")
    assert sent == 0
    assert services.load_push_subscriptions() == []

    metrics = services.get_runtime_metrics()
    assert metrics["push_failed"] >= 1
    assert metrics["push_pruned"] >= 1
    assert "status=403" in metrics["push_last_error"]


@pytest.mark.usefixtures("services")
def test_opportunistic_scheduler_runs_in_external_mode(services, monkeypatch):
    checks = []
    now = {"value": 1000.0}

    monkeypatch.setattr(services, "resolve_weekly_scheduler_mode", lambda: "external")
    monkeypatch.setattr(services, "maybe_send_weekly_email_digest", lambda: checks.append("run"))
    monkeypatch.setattr("app_services.timelib.time", lambda: now["value"])

    services.maybe_run_scheduled_jobs_opportunistically()
    services.maybe_run_scheduled_jobs_opportunistically()
    assert checks == ["run"]

    now["value"] += services.opportunistic_scheduler_interval_seconds + 1
    services.maybe_run_scheduled_jobs_opportunistically()
    assert checks == ["run", "run"]


@pytest.mark.usefixtures("services")
def test_send_email_sends_individual_messages_with_delay(services, monkeypatch):
    services.add_weekly_email_recipient("alpha@example.com")
    services.add_weekly_email_recipient("beta@example.com")
    services.config = replace(
        services.config,
        smtp_send_delay_seconds=0.25,
        smtp_use_tls=True,
        smtp_use_ssl=False,
        smtp_user="",
        smtp_pass="",
    )

    sent_to = []
    sleep_calls = []

    class DummySMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, *_args, **_kwargs):
            return None

        def send_message(self, message):
            sent_to.append(message["To"])

    monkeypatch.setattr("app_services.smtplib.SMTP", DummySMTP)
    monkeypatch.setattr("app_services.timelib.sleep", lambda seconds: sleep_calls.append(seconds))

    services.send_email("Digest", "Body")

    metrics = services.get_runtime_metrics()
    assert sent_to == ["alpha@example.com", "beta@example.com"]
    assert metrics["email_attempted"] == 2
    assert metrics["email_sent"] == 2
    assert metrics["email_failed"] == 0
    assert sleep_calls == [0.25]


@pytest.mark.usefixtures("services")
def test_send_email_partial_failure_records_metrics_without_raising(services, monkeypatch):
    services.add_weekly_email_recipient("alpha@example.com")
    services.add_weekly_email_recipient("beta@example.com")
    services.config = replace(
        services.config,
        smtp_send_delay_seconds=0.0,
        smtp_use_tls=False,
        smtp_use_ssl=False,
        smtp_user="",
        smtp_pass="",
    )

    class DummySMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def send_message(self, message):
            if message["To"] == "beta@example.com":
                raise RuntimeError("recipient rejected")
            return None

    monkeypatch.setattr("app_services.smtplib.SMTP", DummySMTP)

    services.send_email("Digest", "Body")

    metrics = services.get_runtime_metrics()
    assert metrics["email_attempted"] == 2
    assert metrics["email_sent"] == 1
    assert metrics["email_failed"] == 1
    assert "Partial delivery" in metrics["email_last_error"]


@pytest.mark.usefixtures("services")
def test_send_email_all_fail_raises(services, monkeypatch):
    services.add_weekly_email_recipient("alpha@example.com")
    services.config = replace(
        services.config,
        smtp_send_delay_seconds=0.0,
        smtp_use_tls=False,
        smtp_use_ssl=False,
        smtp_user="",
        smtp_pass="",
    )

    class DummySMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def send_message(self, _message):
            raise RuntimeError("recipient rejected")

    monkeypatch.setattr("app_services.smtplib.SMTP", DummySMTP)

    with pytest.raises(RuntimeError, match="failed for all recipients"):
        services.send_email("Digest", "Body")

    metrics = services.get_runtime_metrics()
    assert metrics["email_attempted"] == 1
    assert metrics["email_sent"] == 0
    assert metrics["email_failed"] == 1
