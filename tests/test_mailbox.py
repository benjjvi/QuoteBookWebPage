def _seed_digest_archive(services):
    services.archive_weekly_digest(
        run_key="2026-02-02",
        subject="Digest older",
        body="Older body",
        sent_at=1_738_790_400,  # 2025-02-02
        recipient_count=2,
    )
    services.archive_weekly_digest(
        run_key="2026-02-09",
        subject="Digest current",
        body="Current body",
        sent_at=1_739_395_200,  # 2025-02-09
        recipient_count=3,
    )
    services.archive_weekly_digest(
        run_key="2026-02-16",
        subject="Digest latest",
        body="Latest body",
        sent_at=1_740_000_000,  # 2025-02-16
        recipient_count=4,
    )


def test_mailbox_public_mode_shows_single_previous_issue(client, services):
    _seed_digest_archive(services)

    response = client.get("/mailbox")
    assert response.status_code == 200
    body = response.data.decode("utf-8")

    # Public mode excludes the latest and shows only one delayed issue.
    assert "Digest current" in body
    assert "Digest older" not in body
    assert "Digest latest" not in body


def test_mailbox_subscribed_cookie_shows_full_previous_archive(client, services):
    _seed_digest_archive(services)

    token_resp = client.get("/api/email/token")
    token = token_resp.get_json()["token"]
    subscribe_resp = client.post(
        "/api/email/subscribe",
        json={"token": token, "email": "reader@example.com"},
    )
    assert subscribe_resp.status_code == 200

    response = client.get("/mailbox")
    assert response.status_code == 200
    body = response.data.decode("utf-8")

    # Subscribed + cookie mode excludes latest but shows all older entries.
    assert "Digest current" in body
    assert "Digest older" in body
    assert "Digest latest" not in body
    assert "Signed in as reader@example.com via subscription cookie." in body


def test_unsubscribe_page_removes_email_and_clears_cookie(client, csrf_token_for):
    token_resp = client.get("/api/email/token")
    token = token_resp.get_json()["token"]
    subscribe_resp = client.post(
        "/api/email/subscribe",
        json={"token": token, "email": "reader@example.com"},
    )
    assert subscribe_resp.status_code == 200

    csrf = csrf_token_for("/unsubscribe")
    unsubscribe_resp = client.post(
        "/unsubscribe",
        data={"email": "reader@example.com", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert unsubscribe_resp.status_code == 200
    assert b"has been unsubscribed" in unsubscribe_resp.data
