import re


def _extract_device_id(set_cookie_header: str) -> str:
    match = re.search(r"qb_social_device_id=([^;]+)", set_cookie_header or "")
    if not match:
        return ""
    return match.group(1).strip()


def test_social_reaction_service_lifecycle(services):
    quote_id = 1
    device_id = "device-alpha"
    start = 1_700_000_000

    assert services.record_social_reaction(
        quote_id=quote_id,
        device_id=device_id,
        reaction_type="thumbs_up",
        now_ts=start,
    )
    snapshot = services.get_social_reactions_for_quote(
        quote_id=quote_id,
        device_id=device_id,
        now_ts=start + 5,
    )
    assert snapshot["counts"]["thumbs_up"] == 1
    assert snapshot["total"] == 1
    assert snapshot["user_reaction"] == "thumbs_up"

    assert services.record_social_reaction(
        quote_id=quote_id,
        device_id=device_id,
        reaction_type="anger",
        now_ts=start + 20,
    )
    replaced = services.get_social_reactions_for_quote(
        quote_id=quote_id,
        device_id=device_id,
        now_ts=start + 25,
    )
    assert replaced["counts"]["thumbs_up"] == 0
    assert replaced["counts"]["anger"] == 1
    assert replaced["total"] == 1
    assert replaced["user_reaction"] == "anger"

    assert services.record_social_reaction(
        quote_id=quote_id,
        device_id=device_id,
        reaction_type="anger",
        now_ts=start + 30,
    )
    after_unreact = services.get_social_reactions_for_quote(
        quote_id=quote_id,
        device_id=device_id,
        now_ts=start + 35,
    )
    assert after_unreact["total"] == 0
    assert after_unreact["user_reaction"] == ""


def test_social_comment_service_round_trip(services):
    assert services.add_social_comment(
        quote_id=1,
        display_name="Casey",
        comment_text="This is elite nonsense.",
    )
    comments = services.get_social_comments_for_quote(quote_id=1, limit=20)
    assert comments
    assert comments[-1]["display_name"] == "Casey"
    assert comments[-1]["comment_text"] == "This is elite nonsense."


def test_social_reaction_and_comment_routes(client, services):
    reaction_resp = client.post(
        "/social/quote/1/react",
        data={"reaction": "heart"},
        follow_redirects=False,
    )
    assert reaction_resp.status_code == 302

    cookie = getattr(client, "get_cookie", lambda _name: None)("qb_social_device_id")
    device_id = cookie.value if cookie else ""
    if not device_id:
        device_id = _extract_device_id(reaction_resp.headers.get("Set-Cookie", ""))
    assert device_id

    snapshot = services.get_social_reactions_for_quote(quote_id=1, device_id=device_id)
    assert snapshot["counts"]["heart"] == 1
    assert snapshot["user_reaction"] == "heart"

    comment_resp = client.post(
        "/social/quote/1/comment",
        data={
            "commenter_name": "Jordan",
            "comment_text": "This one needs framing.",
        },
        follow_redirects=True,
    )
    assert comment_resp.status_code == 200
    assert b"Jordan" in comment_resp.data
    assert b"This one needs framing." in comment_resp.data
