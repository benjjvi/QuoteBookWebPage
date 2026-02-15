def _seed_quotes(quote_store, target_total=55):
    base_ts = 1_735_690_000
    while quote_store.get_total_quotes() < target_total:
        next_index = quote_store.get_total_quotes() + 1
        quote_store.add_quote(
            quote_text=f"Generated quote {next_index}",
            authors=[f"Speaker {next_index % 5}"],
            context="Seed for Quote Anarchy tests",
            timestamp=base_ts + next_index,
        )


def test_quote_anarchy_lock_gate(client):
    bootstrap = client.get("/api/quote-anarchy/bootstrap")
    assert bootstrap.status_code == 200
    payload = bootstrap.get_json()
    assert payload["unlocked"] is False
    assert payload["total_quotes"] == 2
    assert payload["min_quotes_required"] == 50

    solo = client.post("/api/quote-anarchy/solo/deal")
    assert solo.status_code == 403


def test_quote_anarchy_multiplayer_round_flow(client, quote_store, services):
    _seed_quotes(quote_store, target_total=60)

    bootstrap = client.get("/api/quote-anarchy/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.get_json()["unlocked"] is True

    create = client.post("/api/quote-anarchy/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    created_payload = create.get_json()
    session_code = created_payload["session_code"]
    host_player_id = created_payload["player_id"]
    assert len(session_code) == 6

    join = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/join",
        json={"player_name": "Guest"},
    )
    assert join.status_code == 200
    guest_player_id = join.get_json()["player_id"]

    start = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/start",
        json={"player_id": host_player_id},
    )
    assert start.status_code == 200

    host_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert host_state.status_code == 200
    host_payload = host_state.get_json()
    assert host_payload["session"]["status"] == "collecting"

    judge_player_id = host_payload["session"]["judge_player_id"]
    player_ids = [player["player_id"] for player in host_payload["players"]]
    non_judges = [pid for pid in player_ids if pid != judge_player_id]
    assert len(non_judges) == 1

    submitting_player_id = non_judges[0]
    submitter_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": submitting_player_id},
    )
    assert submitter_state.status_code == 200
    submitter_payload = submitter_state.get_json()
    assert len(submitter_payload["round"]["hand"]) == 7
    quote_id = submitter_payload["round"]["hand"][0]["quote_id"]

    submit = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/submit",
        json={
            "player_id": submitting_player_id,
            "quote_id": quote_id,
        },
    )
    assert submit.status_code == 200

    judge_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": judge_player_id},
    )
    assert judge_state.status_code == 200
    judge_payload = judge_state.get_json()
    assert judge_payload["session"]["status"] == "judging"
    assert len(judge_payload["round"]["submissions"]) == len(non_judges)

    winner_submission = judge_payload["round"]["submissions"][0]
    winner_player_id = winner_submission["player_id"]
    winner_quote_id = winner_submission["quote_id"]
    pick_winner = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/pick-winner",
        json={
            "player_id": judge_player_id,
            "winner_player_id": winner_player_id,
        },
    )
    assert pick_winner.status_code == 200
    assert pick_winner.get_json()["game_completed"] is False

    reveal_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": guest_player_id},
    )
    assert reveal_state.status_code == 200
    reveal_payload = reveal_state.get_json()
    assert reveal_payload["session"]["status"] == "reveal"
    assert reveal_payload["round"]["result"]["winner_player_id"] == winner_player_id

    winner_scores = [
        player["score"]
        for player in reveal_payload["players"]
        if player["player_id"] == winner_player_id
    ]
    assert winner_scores == [1]
    winner_quote = quote_store.get_quote_by_id(winner_quote_id)
    assert winner_quote is not None
    assert winner_quote.stats.get("anarchy_points", 0) == 1
    assert services.get_stats_cache_snapshot()["total_anarchy_points"] == 1

    next_round = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/next-round",
        json={"player_id": host_player_id},
    )
    assert next_round.status_code == 200

    next_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert next_state.status_code == 200
    next_payload = next_state.get_json()
    assert next_payload["session"]["status"] == "collecting"
    assert next_payload["session"]["round_number"] == 2


def test_quote_anarchy_everyone_votes_tie_and_round_cap(client, quote_store, services):
    _seed_quotes(quote_store, target_total=65)

    create = client.post(
        "/api/quote-anarchy/sessions",
        json={
            "player_name": "Host",
            "judging_mode": "all_vote",
            "max_rounds": 1,
        },
    )
    assert create.status_code == 200
    payload = create.get_json()
    session_code = payload["session_code"]
    host_player_id = payload["player_id"]

    join = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/join",
        json={"player_name": "Guest"},
    )
    assert join.status_code == 200
    guest_player_id = join.get_json()["player_id"]

    start = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/start",
        json={"player_id": host_player_id},
    )
    assert start.status_code == 200

    host_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    ).get_json()
    guest_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": guest_player_id},
    ).get_json()

    assert host_state["session"]["judging_mode"] == "all_vote"
    assert host_state["session"]["max_rounds"] == 1
    assert host_state["session"]["status"] == "collecting"
    assert len(host_state["round"]["hand"]) == 7
    assert len(guest_state["round"]["hand"]) == 7

    host_quote_id = host_state["round"]["hand"][0]["quote_id"]
    guest_quote_id = guest_state["round"]["hand"][0]["quote_id"]

    submit_host = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/submit",
        json={"player_id": host_player_id, "quote_id": host_quote_id},
    )
    assert submit_host.status_code == 200

    submit_guest = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/submit",
        json={"player_id": guest_player_id, "quote_id": guest_quote_id},
    )
    assert submit_guest.status_code == 200

    judging_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    ).get_json()
    assert judging_state["session"]["status"] == "judging"
    assert len(judging_state["round"]["submissions"]) == 2

    vote_host = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/vote",
        json={"player_id": host_player_id, "voted_player_id": guest_player_id},
    )
    assert vote_host.status_code == 200

    vote_guest = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/vote",
        json={"player_id": guest_player_id, "voted_player_id": host_player_id},
    )
    assert vote_guest.status_code == 200
    assert vote_guest.get_json()["game_completed"] is True

    reveal_state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert reveal_state.status_code == 200
    reveal_payload = reveal_state.get_json()
    assert reveal_payload["session"]["status"] == "reveal"
    assert reveal_payload["session"]["is_active"] is False
    assert "after 1 rounds" in reveal_payload["session"]["ended_reason"].lower()

    winners = reveal_payload["round"]["result"]["winners"]
    winner_ids = {winner["player_id"] for winner in winners}
    assert winner_ids == {host_player_id, guest_player_id}
    assert reveal_payload["round"]["result"]["is_tie"] is True

    scores = {
        player["player_id"]: player["score"] for player in reveal_payload["players"]
    }
    assert scores[host_player_id] == 1
    assert scores[guest_player_id] == 1

    host_quote = quote_store.get_quote_by_id(host_quote_id)
    guest_quote = quote_store.get_quote_by_id(guest_quote_id)
    assert host_quote is not None
    assert guest_quote is not None
    assert host_quote.stats.get("anarchy_points", 0) == 1
    assert guest_quote.stats.get("anarchy_points", 0) == 1
    assert services.get_stats_cache_snapshot()["total_anarchy_points"] == 2


def test_quote_anarchy_host_can_end_game(client, quote_store):
    _seed_quotes(quote_store, target_total=60)

    create = client.post("/api/quote-anarchy/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    payload = create.get_json()
    session_code = payload["session_code"]
    host_player_id = payload["player_id"]

    end_game = client.post(
        f"/api/quote-anarchy/sessions/{session_code}/end",
        json={"player_id": host_player_id},
    )
    assert end_game.status_code == 200

    state = client.get(
        f"/api/quote-anarchy/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert state.status_code == 200
    payload = state.get_json()
    assert payload["session"]["is_active"] is False
    assert payload["session"]["ended_reason"] == "Game ended by host."
