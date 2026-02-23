import re
from pathlib import Path


def _offline_allowed_for_path(html: str, path: str) -> str:
    match = re.search(
        rf'href="{re.escape(path)}"[^>]*data-offline-allowed="(true|false)"',
        html,
    )
    assert match, f"Missing nav item for {path}"
    return match.group(1)


def test_smoke_routes(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"

    home = client.get("/")
    assert home.status_code == 200
    assert b"Quote Book" in home.data
    assert b"Games" in home.data
    assert b"card-support offline-hide" in home.data

    all_quotes = client.get("/all_quotes")
    assert all_quotes.status_code == 200

    social = client.get("/social")
    assert social.status_code == 200
    assert b"Quote Stream" in social.data

    social_author = client.get("/social/author/Alice")
    assert social_author.status_code == 200
    assert b"Author page" in social_author.data

    social_post = client.get("/social/quote/1")
    assert social_post.status_code == 200
    assert b"Reactions" in social_post.data
    assert b"Comments" in social_post.data

    stats = client.get("/stats")
    assert stats.status_code == 200
    assert b"Quote Anarchy points" in stats.data

    quote_anarchy = client.get("/quote-anarchy")
    assert quote_anarchy.status_code == 200
    assert b"Quote Anarchy" in quote_anarchy.data

    games = client.get("/games")
    assert games.status_code == 200
    assert b"Launcher" in games.data

    blackline = client.get("/games/blackline-rush")
    assert blackline.status_code == 200
    assert b"Redacted: Black Line Rush" in blackline.data

    who_said = client.get("/games/who-said-it")
    assert who_said.status_code == 200
    assert b"Who Even Said That?" in who_said.data

    mailbox = client.get("/mailbox")
    assert mailbox.status_code == 200
    assert b"Weekly Digest Mailbox" in mailbox.data

    unsubscribe = client.get("/unsubscribe")
    assert unsubscribe.status_code == 200
    assert b"Unsubscribe from Weekly Digest" in unsubscribe.data

    api_quotes = client.get("/api/quotes")
    assert api_quotes.status_code == 200
    payload = api_quotes.get_json()
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["total_pages"] == 1
    assert payload["order"] == "oldest"
    assert len(payload["quotes"]) == 2


def test_health_details_exposes_runtime_metrics(client):
    response = client.get("/health/details")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert "metrics" in payload
    assert "features" in payload


def test_robots_and_sitemap_are_indexable(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    body = robots.data.decode("utf-8")
    assert "Allow: /" in body
    assert "Sitemap:" in body

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    text = sitemap.data.decode("utf-8")
    assert "<urlset" in text
    assert "/quote/" in text


def test_monetization_pages_render(client):
    assert client.get("/support").status_code == 200
    assert client.get("/advertise").status_code == 200


def test_search_supports_get_query(client):
    response = client.get("/search", query_string={"q": "first"})
    assert response.status_code == 200
    assert b"results for" in response.data


def test_timeline_rejects_invalid_params(client):
    assert client.get("/timeline/2024/13").status_code == 404
    assert client.get("/timeline/0/1").status_code == 404
    assert client.get("/timeline/day/999999999999").status_code == 404


def test_battle_post_invalid_ids_redirects_safely(client):
    bad_type = client.post("/battle", data={"winner": "abc", "loser": "1"})
    assert bad_type.status_code == 302
    assert bad_type.headers["Location"].endswith("/battle")

    duplicate = client.post("/battle", data={"winner": "1", "loser": "1"})
    assert duplicate.status_code == 302
    assert duplicate.headers["Location"].endswith("/battle")


def test_home_offline_flags_match_offline_ready_pages(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.data.decode("utf-8")

    assert _offline_allowed_for_path(html, "/all_quotes") == "true"
    assert _offline_allowed_for_path(html, "/random") == "true"
    assert _offline_allowed_for_path(html, "/search") == "true"
    assert _offline_allowed_for_path(html, "/mailbox") == "true"
    assert _offline_allowed_for_path(html, "/stats") == "true"

    assert _offline_allowed_for_path(html, "/add_quote") == "false"
    assert _offline_allowed_for_path(html, "/ai") == "false"
    assert _offline_allowed_for_path(html, "/games") == "false"
    assert _offline_allowed_for_path(html, "/social") == "false"


def test_service_worker_precaches_offline_ready_pages():
    sw_source = Path("static/sw.js").read_text(encoding="utf-8")
    expected = [
        '"/"',
        '"/all_quotes"',
        '"/random"',
        '"/search"',
        '"/stats"',
        '"/mailbox"',
        '"/static/assets/css/all-quotes.css"',
        '"/static/assets/css/quote.css"',
        '"/static/assets/css/search.css"',
        '"/static/assets/css/stats.css"',
        '"/static/assets/js/index.js"',
    ]
    for token in expected:
        assert token in sw_source
