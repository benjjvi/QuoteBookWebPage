def test_smoke_routes(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"

    home = client.get("/")
    assert home.status_code == 200
    assert b"Quote Book" in home.data

    all_quotes = client.get("/all_quotes")
    assert all_quotes.status_code == 200

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
