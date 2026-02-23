def test_api_contract_endpoints(client):
    assert client.get("/api/speakers").status_code == 200
    assert client.get("/api/quotes/random").status_code == 200
    assert client.get("/api/quotes/1").status_code == 200
    assert client.get("/api/search", query_string={"query": "first"}).status_code == 200

    between = client.get(
        "/api/quotes/between",
        query_string={"start_ts": 1_735_679_000, "end_ts": 1_735_681_000},
    )
    assert between.status_code == 200
    assert len(between.get_json()["quotes"]) >= 1

    metrics = client.get("/api/ops/metrics")
    assert metrics.status_code == 200
    assert "metrics" in metrics.get_json()
    assert client.get("/api/tags").status_code == 200


def test_api_quotes_order_and_pagination(client):
    oldest = client.get("/api/quotes", query_string={"order": "oldest"}).get_json()
    newest = client.get("/api/quotes", query_string={"order": "newest"}).get_json()

    assert oldest["quotes"][0]["id"] != newest["quotes"][0]["id"]

    paged = client.get(
        "/api/quotes",
        query_string={"order": "oldest", "page": 2, "per_page": 1},
    ).get_json()
    assert paged["page"] == 2
    assert paged["total_pages"] == 2
    assert len(paged["quotes"]) == 1

    by_tag = client.get("/api/quotes", query_string={"tag": "work"}).get_json()
    assert by_tag["total"] >= 1
    assert all("work" in (item.get("tags") or []) for item in by_tag["quotes"])


def test_api_add_update_and_battle(client, services):
    created = client.post(
        "/api/quotes",
        json={
            "quote": "Created through API",
            "authors": ["Charlie"],
            "context": "Test create",
            "timestamp": 1_735_681_500,
        },
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    new_id = created_payload["id"]

    updated = client.put(
        f"/api/quotes/{new_id}",
        json={
            "quote": "Updated through API",
            "authors": ["Charlie", "Alice"],
            "context": "Updated",
        },
    )
    assert updated.status_code == 200
    assert "Updated through API" in updated.get_json()["quote"]

    battle = client.post("/api/battles", json={"winner_id": 1, "loser_id": 2})
    assert battle.status_code == 200
    payload = battle.get_json()
    assert payload["winner"]["id"] == 1
    assert payload["loser"]["id"] == 2
    assert services.get_stats_cache_snapshot()["total_battles"] == 1

    anarchy = client.post("/api/quote-anarchy-wins", json={"quote_ids": [new_id]})
    assert anarchy.status_code == 200
    anarchy_payload = anarchy.get_json()
    assert anarchy_payload["updated_count"] == 1
    assert anarchy_payload["quotes"][0]["id"] == new_id
    assert anarchy_payload["quotes"][0]["stats"]["anarchy_points"] == 1
    assert "tags" in anarchy_payload["quotes"][0]


def test_api_add_quote_rejects_invalid_timestamp(client):
    response = client.post(
        "/api/quotes",
        json={
            "quote": "Bad timestamp",
            "authors": ["Alice"],
            "timestamp": "not-an-int",
        },
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "timestamp must be an integer"


def test_api_battle_rejects_invalid_or_duplicate_ids(client):
    bad_type = client.post("/api/battles", json={"winner_id": "abc", "loser_id": 2})
    assert bad_type.status_code == 400
    assert bad_type.get_json()["error"] == "winner_id and loser_id must be integers"

    duplicate = client.post("/api/battles", json={"winner_id": 1, "loser_id": 1})
    assert duplicate.status_code == 400
    assert duplicate.get_json()["error"] == "winner_id and loser_id must be different"


def test_api_error_payload_shape(client):
    response = client.post("/api/battles", json={"winner_id": "abc", "loser_id": 2})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "battle_ids_invalid"
    assert payload["message"] == "winner_id and loser_id must be integers"
    assert "details" in payload
    assert payload["error"] == payload["message"]


def test_api_social_feed_pagination(client):
    first = client.get("/api/social/feed", query_string={"page": 1, "per_page": 1})
    assert first.status_code == 200
    payload = first.get_json()
    assert "items" in payload
    assert payload["page"] == 1
    assert "has_more" in payload
