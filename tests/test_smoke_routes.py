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
