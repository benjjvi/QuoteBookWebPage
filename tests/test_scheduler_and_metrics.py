from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


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
    sent_failed = services.send_push_notification("Title", "Body", "https://example.com")
    assert sent_failed == 0

    metrics = services.get_runtime_metrics()
    assert metrics["push_attempted"] >= 2
    assert metrics["push_sent"] >= 1
    assert metrics["push_failed"] >= 1
