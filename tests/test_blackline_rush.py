def _seed_long_quotes(quote_store, target_total=20):
    base_ts = 1_735_780_000
    seed_quotes = [
        "This internal report covers compliance checks and several delayed approval chains for quarterly planning.",
        "The meeting transcript includes budget figures risk notes and staffing updates for the current cycle.",
        "An operations memo outlined release blockers incident follow ups and escalation paths for leadership review.",
        "A project brief captured milestone slips owner handoffs and revised estimates under tight delivery pressure.",
        "The daily standup notes recorded outages retries mitigation steps and final rollout communication details.",
    ]
    while quote_store.get_total_quotes() < target_total:
        index = quote_store.get_total_quotes()
        quote_store.add_quote(
            quote_text=seed_quotes[index % len(seed_quotes)],
            authors=[f"Speaker {index % 4}"],
            context="Seed for Blackline Rush tests",
            timestamp=base_ts + index,
        )


def test_blackline_rush_turn_flow_and_scoring(client, quote_store):
    _seed_long_quotes(quote_store, target_total=22)

    create = client.post("/api/blackline-rush/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    created = create.get_json()
    session_code = created["session_code"]
    host_player_id = created["player_id"]

    join_one = client.post(
        f"/api/blackline-rush/sessions/{session_code}/join",
        json={"player_name": "Guest One"},
    )
    assert join_one.status_code == 200
    guest_one_id = join_one.get_json()["player_id"]

    join_two = client.post(
        f"/api/blackline-rush/sessions/{session_code}/join",
        json={"player_name": "Guest Two"},
    )
    assert join_two.status_code == 200
    guest_two_id = join_two.get_json()["player_id"]

    start = client.post(
        f"/api/blackline-rush/sessions/{session_code}/start",
        json={"player_id": host_player_id},
    )
    assert start.status_code == 200

    host_state = client.get(
        f"/api/blackline-rush/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert host_state.status_code == 200
    host_payload = host_state.get_json()
    assert host_payload["session"]["status"] == "redacting"
    assert host_payload["turn"]["can_submit_redaction"] is True
    assert host_payload["turn"]["allowed_redactions"] >= 1
    assert len(host_payload["turn"]["redaction_options"]) >= 10

    redaction_submit = client.post(
        f"/api/blackline-rush/sessions/{session_code}/submit-redaction",
        json={"player_id": host_player_id, "redaction_indices": [0]},
    )
    assert redaction_submit.status_code == 200
    assert redaction_submit.get_json()["gap_count"] == 1

    guessing_state = client.get(
        f"/api/blackline-rush/sessions/{session_code}",
        query_string={"player_id": guest_one_id},
    )
    assert guessing_state.status_code == 200
    guessing_payload = guessing_state.get_json()
    assert guessing_payload["session"]["status"] == "guessing"
    assert guessing_payload["turn"]["gap_count"] == 1
    assert "[[" in guessing_payload["turn"]["puzzle_text"]

    redactor_view = client.get(
        f"/api/blackline-rush/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    ).get_json()
    answer_word = redactor_view["turn"]["answers"][0]

    wrong_guess = client.post(
        f"/api/blackline-rush/sessions/{session_code}/guess",
        json={"player_id": guest_one_id, "guesses": ["wrong"]},
    )
    assert wrong_guess.status_code == 200
    assert wrong_guess.get_json()["correct"] is False

    guest_one_correct = client.post(
        f"/api/blackline-rush/sessions/{session_code}/guess",
        json={"player_id": guest_one_id, "guesses": [answer_word]},
    )
    assert guest_one_correct.status_code == 200
    guest_one_payload = guest_one_correct.get_json()
    assert guest_one_payload["correct"] is True
    assert guest_one_payload["solved_rank"] == 1
    assert guest_one_payload["points_awarded"] == 2

    guest_two_correct = client.post(
        f"/api/blackline-rush/sessions/{session_code}/guess",
        json={"player_id": guest_two_id, "guesses": [answer_word]},
    )
    assert guest_two_correct.status_code == 200
    guest_two_payload = guest_two_correct.get_json()
    assert guest_two_payload["correct"] is True
    assert guest_two_payload["solved_rank"] == 2
    assert guest_two_payload["points_awarded"] == 1
    assert guest_two_payload["all_solved"] is True

    reveal_state = client.get(
        f"/api/blackline-rush/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert reveal_state.status_code == 200
    reveal_payload = reveal_state.get_json()
    assert reveal_payload["session"]["status"] == "reveal"

    scores = {
        player["player_id"]: player["score"] for player in reveal_payload["players"]
    }
    assert scores[guest_one_id] == 2
    assert scores[guest_two_id] == 1

    next_turn = client.post(
        f"/api/blackline-rush/sessions/{session_code}/next-turn",
        json={"player_id": host_player_id},
    )
    assert next_turn.status_code == 200

    turn_two_state = client.get(
        f"/api/blackline-rush/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert turn_two_state.status_code == 200
    turn_two_payload = turn_two_state.get_json()
    assert turn_two_payload["session"]["status"] == "redacting"
    assert turn_two_payload["session"]["turn_number"] == 2
    assert turn_two_payload["session"]["redactor_player_id"] == guest_one_id


def test_blackline_rush_room_cap(client, quote_store):
    _seed_long_quotes(quote_store, target_total=20)

    create = client.post("/api/blackline-rush/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    payload = create.get_json()
    session_code = payload["session_code"]

    for idx in range(7):
        join = client.post(
            f"/api/blackline-rush/sessions/{session_code}/join",
            json={"player_name": f"Guest {idx + 1}"},
        )
        assert join.status_code == 200

    overflow = client.post(
        f"/api/blackline-rush/sessions/{session_code}/join",
        json={"player_name": "Too Many"},
    )
    assert overflow.status_code == 409
    assert "full" in overflow.get_json()["error"].lower()
