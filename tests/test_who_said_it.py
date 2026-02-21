import json
import sqlite3


def _seed_who_said_quotes(quote_store):
    base_ts = 1_735_900_000
    single_author_quotes = [
        ("The most dangerous phrase in software is this should be easy.", "Thomas Hall"),
        ("Ship your first draft before your fear ships your excuses.", "Mina Patel"),
        ("The bug report was free consulting and we ignored it.", "Rory Kim"),
        ("If you keep the logs you keep your future self employed.", "Sal Vega"),
        ("Every shortcut has a receipt attached to it.", "Nia Brooks"),
    ]

    for index, (quote_text, author) in enumerate(single_author_quotes):
        quote_store.add_quote(
            quote_text=quote_text,
            authors=[author],
            context="Seed for Who Said It tests",
            timestamp=base_ts + index,
        )

    multi_author_quote = quote_store.add_quote(
        quote_text="Pair programming turns panic into a spectator sport.",
        authors=["Avery", "Jordan"],
        context="Should not be used by Who Said It",
        timestamp=base_ts + 99,
    )
    return multi_author_quote.id


def _session_answer_key(quote_store, session_code):
    db_path = quote_store._local.filepath
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT correct_author, option_authors
            FROM wsi_sessions
            WHERE code = ?
            """,
            (session_code,),
        ).fetchone()

    assert row is not None
    correct_author = row[0]
    option_authors = json.loads(row[1])
    return correct_author, option_authors


def test_who_said_it_turn_flow_speed_scoring_and_single_author_filter(
    client, quote_store
):
    multi_author_id = _seed_who_said_quotes(quote_store)

    create = client.post("/api/who-said-it/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    created = create.get_json()
    session_code = created["session_code"]
    host_player_id = created["player_id"]

    join_one = client.post(
        f"/api/who-said-it/sessions/{session_code}/join",
        json={"player_name": "Guest One"},
    )
    assert join_one.status_code == 200
    guest_one_id = join_one.get_json()["player_id"]

    join_two = client.post(
        f"/api/who-said-it/sessions/{session_code}/join",
        json={"player_name": "Guest Two"},
    )
    assert join_two.status_code == 200
    guest_two_id = join_two.get_json()["player_id"]

    start = client.post(
        f"/api/who-said-it/sessions/{session_code}/start",
        json={"player_id": host_player_id},
    )
    assert start.status_code == 200

    host_state = client.get(
        f"/api/who-said-it/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert host_state.status_code == 200
    host_payload = host_state.get_json()
    assert host_payload["session"]["status"] == "guessing"
    assert host_payload["turn"]["can_submit_answer"] is True
    assert len(host_payload["turn"]["option_authors"]) == 4

    turn_quote_id = host_payload["turn"]["source_quote_id"]
    assert turn_quote_id != multi_author_id
    turn_quote = quote_store.get_quote_by_id(turn_quote_id)
    assert turn_quote is not None
    assert len(turn_quote.authors) == 1

    correct_author, option_authors = _session_answer_key(quote_store, session_code)
    wrong_author = next(
        option for option in option_authors if option.lower() != correct_author.lower()
    )

    guest_two_correct = client.post(
        f"/api/who-said-it/sessions/{session_code}/answer",
        json={"player_id": guest_two_id, "selected_author": correct_author},
    )
    assert guest_two_correct.status_code == 200
    guest_two_payload = guest_two_correct.get_json()
    assert guest_two_payload["is_correct"] is True
    assert guest_two_payload["answer_order"] == 1
    assert guest_two_payload["points_awarded"] == 3

    host_correct = client.post(
        f"/api/who-said-it/sessions/{session_code}/answer",
        json={"player_id": host_player_id, "selected_author": correct_author},
    )
    assert host_correct.status_code == 200
    host_correct_payload = host_correct.get_json()
    assert host_correct_payload["is_correct"] is True
    assert host_correct_payload["answer_order"] == 2
    assert host_correct_payload["points_awarded"] == 2

    guest_one_wrong = client.post(
        f"/api/who-said-it/sessions/{session_code}/answer",
        json={"player_id": guest_one_id, "selected_author": wrong_author},
    )
    assert guest_one_wrong.status_code == 200
    guest_one_payload = guest_one_wrong.get_json()
    assert guest_one_payload["is_correct"] is False
    assert guest_one_payload["all_answered"] is True

    reveal_state = client.get(
        f"/api/who-said-it/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert reveal_state.status_code == 200
    reveal_payload = reveal_state.get_json()
    assert reveal_payload["session"]["status"] == "reveal"
    assert reveal_payload["turn"]["correct_author"] == correct_author

    scores = {
        player["player_id"]: player["score"] for player in reveal_payload["players"]
    }
    assert scores[guest_two_id] == 3
    assert scores[host_player_id] == 2
    assert scores[guest_one_id] == 0

    fastest = reveal_payload["turn"]["fastest_correct"]
    assert fastest[0]["player_id"] == guest_two_id
    assert fastest[0]["rank"] == 1
    assert fastest[1]["player_id"] == host_player_id
    assert fastest[1]["rank"] == 2

    next_turn = client.post(
        f"/api/who-said-it/sessions/{session_code}/next-turn",
        json={"player_id": host_player_id},
    )
    assert next_turn.status_code == 200

    turn_two_state = client.get(
        f"/api/who-said-it/sessions/{session_code}",
        query_string={"player_id": host_player_id},
    )
    assert turn_two_state.status_code == 200
    turn_two_payload = turn_two_state.get_json()
    assert turn_two_payload["session"]["status"] == "guessing"
    assert turn_two_payload["session"]["turn_number"] == 2
    second_quote = quote_store.get_quote_by_id(turn_two_payload["turn"]["source_quote_id"])
    assert second_quote is not None
    assert len(second_quote.authors) == 1


def test_who_said_it_enforces_minimum_players(client, quote_store):
    _seed_who_said_quotes(quote_store)

    create = client.post("/api/who-said-it/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    payload = create.get_json()
    session_code = payload["session_code"]
    host_player_id = payload["player_id"]

    join_one = client.post(
        f"/api/who-said-it/sessions/{session_code}/join",
        json={"player_name": "Guest One"},
    )
    assert join_one.status_code == 200

    start = client.post(
        f"/api/who-said-it/sessions/{session_code}/start",
        json={"player_id": host_player_id},
    )
    assert start.status_code == 400
    assert "at least 3 players" in start.get_json()["error"].lower()


def test_who_said_it_room_cap(client, quote_store):
    _seed_who_said_quotes(quote_store)

    create = client.post("/api/who-said-it/sessions", json={"player_name": "Host"})
    assert create.status_code == 200
    session_code = create.get_json()["session_code"]

    for idx in range(7):
        join = client.post(
            f"/api/who-said-it/sessions/{session_code}/join",
            json={"player_name": f"Guest {idx + 1}"},
        )
        assert join.status_code == 200

    overflow = client.post(
        f"/api/who-said-it/sessions/{session_code}/join",
        json={"player_name": "Too Many"},
    )
    assert overflow.status_code == 409
    assert "full" in overflow.get_json()["error"].lower()
