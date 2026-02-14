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
