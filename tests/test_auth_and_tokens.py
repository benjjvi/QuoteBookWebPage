def test_push_subscription_token_flow(client):
    token_resp = client.get("/api/push/token")
    assert token_resp.status_code == 200
    token = token_resp.get_json()["token"]

    bad = client.post(
        "/api/push/subscribe",
        json={
            "token": "wrong",
            "subscription": {"endpoint": "https://push.example.com/sub-1"},
        },
    )
    assert bad.status_code == 403

    good = client.post(
        "/api/push/subscribe",
        json={
            "token": token,
            "subscription": {"endpoint": "https://push.example.com/sub-1"},
            "userAgent": "pytest",
        },
    )
    assert good.status_code == 200
    assert good.get_json()["ok"] is True


def test_email_subscription_token_flow(client):
    token_resp = client.get("/api/email/token")
    assert token_resp.status_code == 200
    token = token_resp.get_json()["token"]

    bad_email = client.post(
        "/api/email/subscribe",
        json={"token": token, "email": "not-an-email"},
    )
    assert bad_email.status_code == 400

    good_email = client.post(
        "/api/email/subscribe",
        json={"token": token, "email": "person@example.com"},
    )
    assert good_email.status_code == 200
    assert good_email.get_json()["ok"] is True


def test_edit_pin_auth_flow(client, quote_store):
    wrong_pin = client.post(
        "/quote/1/edit",
        data={"action": "pin", "pin": "0000"},
        follow_redirects=True,
    )
    assert wrong_pin.status_code == 200
    assert b"Incorrect PIN" in wrong_pin.data

    pin_ok = client.post(
        "/quote/1/edit",
        data={"action": "pin", "pin": "1234"},
        follow_redirects=True,
    )
    assert pin_ok.status_code == 200

    updated = client.post(
        "/quote/1/edit",
        data={
            "action": "edit",
            "quote_text": "Edited through test",
            "author_info": "Alice",
            "context": "Changed",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    assert b"Edited through test." in updated.data
    assert quote_store.get_quote_by_id(1).context == "Changed"
