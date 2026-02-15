from __future__ import annotations

import json
import logging
import random
import re
import secrets
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class QuoteAnarchyError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class QuoteAnarchyService:
    MIN_QUOTES_REQUIRED = 50
    MAX_PLAYERS = 4
    HAND_SIZE = 7
    STALE_SESSION_SECONDS = 12 * 60 * 60
    DEFAULT_MAX_ROUNDS = 8
    MAX_ROUNDS_LIMIT = 30
    JUDGING_MODE_JUDGE = "judge"
    JUDGING_MODE_ALL_VOTE = "all_vote"

    def __init__(self, *, db_path: str, quote_store, black_cards_path: str | Path):
        self.db_path = str(db_path)
        self.quote_store = quote_store
        self.black_cards_path = Path(black_cards_path)
        self.black_cards = self._load_black_cards()
        self.ensure_schema()

    # ------------------------
    # Public API helpers
    # ------------------------

    def bootstrap(self) -> dict:
        total_quotes = int(self.quote_store.get_total_quotes() or 0)
        return {
            "game_name": "Quote Anarchy",
            "min_quotes_required": self.MIN_QUOTES_REQUIRED,
            "max_players": self.MAX_PLAYERS,
            "hand_size": self.HAND_SIZE,
            "total_quotes": total_quotes,
            "unlocked": total_quotes >= self.MIN_QUOTES_REQUIRED,
            "default_max_rounds": self.DEFAULT_MAX_ROUNDS,
            "max_rounds_limit": self.MAX_ROUNDS_LIMIT,
            "judging_modes": [
                {"id": self.JUDGING_MODE_JUDGE, "label": "Classic Judge"},
                {"id": self.JUDGING_MODE_ALL_VOTE, "label": "Everyone Votes"},
            ],
        }

    def deal_solo_hand(self) -> dict:
        self._require_unlocked()
        return {
            "black_card": self._draw_black_card(),
            "hand": self._sample_quote_cards(self.HAND_SIZE),
            "dealt_at": int(time.time()),
        }

    def create_session(
        self,
        player_name: str,
        judging_mode: str = JUDGING_MODE_JUDGE,
        max_rounds: int | None = None,
    ) -> dict:
        self._require_unlocked()
        self._cleanup_stale_sessions()

        display_name = self._sanitize_player_name(player_name)
        mode = self._normalize_judging_mode(judging_mode)
        rounds = self._normalize_max_rounds(max_rounds)
        player_id = self._new_player_id()
        now_ts = int(time.time())

        with self._connect() as conn:
            for _ in range(24):
                code = self._new_session_code()
                try:
                    conn.execute(
                        """
                        INSERT INTO qa_sessions
                        (code, host_player_id, status, round_number, judge_index, black_card,
                         judging_mode, max_rounds, is_active, ended_reason, ended_at, created_at, updated_at)
                        VALUES (?, ?, 'waiting', 0, 0, '', ?, ?, 1, '', 0, ?, ?)
                        """,
                        (code, player_id, mode, rounds, now_ts, now_ts),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise QuoteAnarchyError(
                    "Unable to create a session code right now.", 503
                )

            conn.execute(
                """
                INSERT INTO qa_players
                (session_code, player_id, display_name, seat, joined_at, score)
                VALUES (?, ?, ?, 1, ?, 0)
                """,
                (code, player_id, display_name, now_ts),
            )

        return {
            "session_code": code,
            "player_id": player_id,
            "display_name": display_name,
            "max_players": self.MAX_PLAYERS,
            "judging_mode": mode,
            "max_rounds": rounds,
        }

    def join_session(
        self, session_code: str, player_name: str, player_id: str | None = None
    ) -> dict:
        self._require_unlocked()
        self._cleanup_stale_sessions()

        code = self._normalize_code(session_code)
        if not code:
            raise QuoteAnarchyError("Session code is required.", 400)

        display_name = self._sanitize_player_name(player_name)
        requested_player_id = self._normalize_player_id(player_id)
        now_ts = int(time.time())

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteAnarchyError("Session not found.", 404)

            if requested_player_id:
                existing = conn.execute(
                    """
                    SELECT session_code, player_id, display_name
                    FROM qa_players
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (code, requested_player_id),
                ).fetchone()
                if existing:
                    if display_name and existing["display_name"] != display_name:
                        conn.execute(
                            """
                            UPDATE qa_players
                            SET display_name = ?
                            WHERE session_code = ? AND player_id = ?
                            """,
                            (display_name, code, requested_player_id),
                        )
                    conn.execute(
                        "UPDATE qa_sessions SET updated_at = ? WHERE code = ?",
                        (now_ts, code),
                    )
                    return {
                        "session_code": code,
                        "player_id": requested_player_id,
                        "display_name": display_name or existing["display_name"],
                        "max_players": self.MAX_PLAYERS,
                        "judging_mode": self._session_judging_mode(session),
                        "max_rounds": self._session_max_rounds(session),
                    }

            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)

            if session["status"] != "waiting":
                raise QuoteAnarchyError(
                    "This session already started. Try another code.", 409
                )

            current_players = self._list_players(conn, code)
            if len(current_players) >= self.MAX_PLAYERS:
                raise QuoteAnarchyError("Session is full (4 players max).", 409)

            new_player_id = requested_player_id or self._new_player_id()
            seat = max([int(player["seat"]) for player in current_players] + [0]) + 1

            try:
                conn.execute(
                    """
                    INSERT INTO qa_players
                    (session_code, player_id, display_name, seat, joined_at, score)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (code, new_player_id, display_name, seat, now_ts),
                )
            except sqlite3.IntegrityError as exc:
                raise QuoteAnarchyError(
                    "Unable to join with this player identity.", 409
                ) from exc

            conn.execute(
                "UPDATE qa_sessions SET updated_at = ? WHERE code = ?",
                (now_ts, code),
            )

        return {
            "session_code": code,
            "player_id": new_player_id,
            "display_name": display_name,
            "max_players": self.MAX_PLAYERS,
            "judging_mode": self._session_judging_mode(session),
            "max_rounds": self._session_max_rounds(session),
        }

    def get_state(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteAnarchyError("Session not found.", 404)

            players = self._list_players(conn, code)
            player_map = {row["player_id"]: row for row in players}
            viewer = player_map.get(player_id)
            if not viewer:
                raise QuoteAnarchyError("You are not part of this session.", 403)

            judging_mode = self._session_judging_mode(session)
            max_rounds = self._session_max_rounds(session)
            is_active = self._session_is_active(session)
            ended_reason = self._session_end_message(session)

            judge_player = None
            if players:
                judge_index = int(session["judge_index"]) % len(players)
                judge_player = players[judge_index]
            judge_player_id = judge_player["player_id"] if judge_player else ""

            round_number = int(session["round_number"])
            status = session["status"]
            current_black_card = session["black_card"] or ""

            submitted_rows = conn.execute(
                """
                SELECT session_code, round_number, player_id, quote_id, quote_text, quote_authors, submitted_at
                FROM qa_submissions
                WHERE session_code = ? AND round_number = ?
                ORDER BY submitted_at ASC
                """,
                (code, round_number),
            ).fetchall()

            required_submissions = self._required_submissions(players, judging_mode)
            submitted_count = len(submitted_rows)
            you_submitted = any(row["player_id"] == player_id for row in submitted_rows)

            hand_rows = conn.execute(
                """
                SELECT slot, quote_id, quote_text, quote_authors
                FROM qa_hands
                WHERE session_code = ? AND round_number = ? AND player_id = ?
                ORDER BY slot ASC
                """,
                (code, round_number, player_id),
            ).fetchall()

            hand = [
                {
                    "slot": int(row["slot"]),
                    "quote_id": int(row["quote_id"]),
                    "quote": row["quote_text"],
                    "authors": self._json_loads_list(row["quote_authors"]),
                }
                for row in hand_rows
            ]

            vote_rows = conn.execute(
                """
                SELECT voter_player_id, voted_player_id
                FROM qa_votes
                WHERE session_code = ? AND round_number = ?
                """,
                (code, round_number),
            ).fetchall()
            votes_submitted_count = len(vote_rows)
            required_votes = (
                len(players) if judging_mode == self.JUDGING_MODE_ALL_VOTE else 0
            )
            viewer_vote = next(
                (row for row in vote_rows if row["voter_player_id"] == player_id), None
            )

            round_winners = conn.execute(
                """
                SELECT winner_player_id, quote_id, quote_text, quote_authors, vote_count, created_at
                FROM qa_round_winners
                WHERE session_code = ? AND round_number = ?
                ORDER BY vote_count DESC, winner_player_id ASC
                """,
                (code, round_number),
            ).fetchall()

            if not round_winners:
                legacy = conn.execute(
                    """
                    SELECT winner_player_id, quote_id, quote_text, quote_authors, created_at
                    FROM qa_round_results
                    WHERE session_code = ? AND round_number = ?
                    """,
                    (code, round_number),
                ).fetchone()
                if legacy:
                    round_winners = [
                        {
                            "winner_player_id": legacy["winner_player_id"],
                            "quote_id": legacy["quote_id"],
                            "quote_text": legacy["quote_text"],
                            "quote_authors": legacy["quote_authors"],
                            "vote_count": 0,
                            "created_at": legacy["created_at"],
                        }
                    ]

            result_payload = None
            if round_winners:
                winners = []
                for winner in round_winners:
                    winner_id = winner["winner_player_id"]
                    winner_row = player_map.get(winner_id)
                    winners.append(
                        {
                            "player_id": winner_id,
                            "player_name": (
                                winner_row["display_name"] if winner_row else "Unknown"
                            ),
                            "quote_id": int(winner["quote_id"]),
                            "quote": winner["quote_text"],
                            "authors": self._json_loads_list(winner["quote_authors"]),
                            "vote_count": int(winner["vote_count"] or 0),
                        }
                    )

                first = winners[0]
                created_at = int(round_winners[0]["created_at"])
                result_payload = {
                    "winner_player_id": first["player_id"],
                    "winner_name": first["player_name"],
                    "quote_id": first["quote_id"],
                    "quote": first["quote"],
                    "authors": first["authors"],
                    "created_at": created_at,
                    "winners": winners,
                    "is_tie": len(winners) > 1,
                }

            submissions_payload = []
            if (
                status == "judging"
                and judging_mode == self.JUDGING_MODE_JUDGE
                and player_id == judge_player_id
            ):
                submissions_payload = [
                    {
                        "player_id": row["player_id"],
                        "quote_id": int(row["quote_id"]),
                        "quote": row["quote_text"],
                        "authors": self._json_loads_list(row["quote_authors"]),
                    }
                    for row in submitted_rows
                ]
            elif status == "judging" and judging_mode == self.JUDGING_MODE_ALL_VOTE:
                submissions_payload = [
                    {
                        "player_id": row["player_id"],
                        "quote_id": int(row["quote_id"]),
                        "quote": row["quote_text"],
                        "authors": self._json_loads_list(row["quote_authors"]),
                    }
                    for row in submitted_rows
                ]
            elif status == "reveal":
                submissions_payload = [
                    {
                        "player_id": row["player_id"],
                        "player_name": (
                            player_map[row["player_id"]]["display_name"]
                            if row["player_id"] in player_map
                            else "Unknown"
                        ),
                        "quote_id": int(row["quote_id"]),
                        "quote": row["quote_text"],
                        "authors": self._json_loads_list(row["quote_authors"]),
                    }
                    for row in submitted_rows
                ]

            can_advance = (
                status == "reveal"
                and player_id == session["host_player_id"]
                and len(players) >= 2
                and is_active
                and round_number < max_rounds
            )

            return {
                "session": {
                    "code": code,
                    "status": status,
                    "round_number": round_number,
                    "black_card": current_black_card,
                    "host_player_id": session["host_player_id"],
                    "judge_player_id": judge_player_id,
                    "judge_name": judge_player["display_name"] if judge_player else "",
                    "max_players": self.MAX_PLAYERS,
                    "judging_mode": judging_mode,
                    "max_rounds": max_rounds,
                    "is_active": is_active,
                    "ended_reason": ended_reason,
                    "updated_at": int(session["updated_at"]),
                },
                "viewer": {
                    "player_id": player_id,
                    "display_name": viewer["display_name"],
                    "is_host": player_id == session["host_player_id"],
                    "is_judge": judging_mode == self.JUDGING_MODE_JUDGE
                    and player_id == judge_player_id,
                    "score": int(viewer["score"]),
                },
                "players": [
                    {
                        "player_id": row["player_id"],
                        "display_name": row["display_name"],
                        "seat": int(row["seat"]),
                        "score": int(row["score"]),
                    }
                    for row in players
                ],
                "round": {
                    "status": status,
                    "number": round_number,
                    "black_card": current_black_card,
                    "hand": hand,
                    "you_submitted": you_submitted,
                    "submitted_count": submitted_count,
                    "required_submissions": required_submissions,
                    "submissions": submissions_payload,
                    "result": result_payload,
                    "votes_submitted_count": votes_submitted_count,
                    "required_votes": required_votes,
                    "you_voted": bool(viewer_vote),
                    "voted_player_id": (
                        viewer_vote["voted_player_id"] if viewer_vote else ""
                    ),
                    "can_start": status == "waiting"
                    and player_id == session["host_player_id"]
                    and len(players) >= 2
                    and is_active,
                    "can_pick_winner": status == "judging"
                    and judging_mode == self.JUDGING_MODE_JUDGE
                    and player_id == judge_player_id
                    and is_active,
                    "can_vote": status == "judging"
                    and judging_mode == self.JUDGING_MODE_ALL_VOTE
                    and not bool(viewer_vote)
                    and is_active,
                    "can_advance": can_advance,
                    "can_end_game": player_id == session["host_player_id"]
                    and is_active,
                },
            }

    def start_session(self, session_code: str, player_id: str) -> dict:
        self._require_unlocked()
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteAnarchyError("Only the host can start the game.", 403)
            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)
            if session["status"] != "waiting":
                raise QuoteAnarchyError("This session already started.", 409)

            players = self._list_players(conn, code)
            if len(players) < 2:
                raise QuoteAnarchyError(
                    "At least 2 players are required to start.", 400
                )

            self._deal_round(
                conn=conn,
                session_code=code,
                round_number=1,
                judge_index=0,
                judging_mode=self._session_judging_mode(session),
            )
        return {"ok": True}

    def submit_card(self, session_code: str, player_id: str, quote_id: int) -> dict:
        self._require_unlocked()
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)
        try:
            quote_id = int(quote_id)
        except (TypeError, ValueError) as exc:
            raise QuoteAnarchyError("A valid quote_id is required.", 400) from exc

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)
            if session["status"] != "collecting":
                raise QuoteAnarchyError("This round is not accepting submissions.", 409)

            players = self._list_players(conn, code)
            player_map = {row["player_id"]: row for row in players}
            if player_id not in player_map:
                raise QuoteAnarchyError("You are not part of this session.", 403)

            mode = self._session_judging_mode(session)
            if mode == self.JUDGING_MODE_JUDGE:
                judge_player = players[int(session["judge_index"]) % len(players)]
                if judge_player["player_id"] == player_id:
                    raise QuoteAnarchyError("The judge cannot submit this round.", 409)

            round_number = int(session["round_number"])

            hand_row = conn.execute(
                """
                SELECT quote_id, quote_text, quote_authors
                FROM qa_hands
                WHERE session_code = ? AND round_number = ? AND player_id = ? AND quote_id = ?
                """,
                (code, round_number, player_id, quote_id),
            ).fetchone()
            if not hand_row:
                raise QuoteAnarchyError("That quote is not in your hand.", 400)

            conn.execute(
                """
                INSERT OR REPLACE INTO qa_submissions
                (session_code, round_number, player_id, quote_id, quote_text, quote_authors, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    round_number,
                    player_id,
                    int(hand_row["quote_id"]),
                    hand_row["quote_text"],
                    hand_row["quote_authors"],
                    int(time.time()),
                ),
            )

            submitted_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qa_submissions
                WHERE session_code = ? AND round_number = ?
                """,
                (code, round_number),
            ).fetchone()[0]

            required_submissions = self._required_submissions(players, mode)
            next_status = (
                "judging" if submitted_count >= required_submissions else "collecting"
            )
            conn.execute(
                "UPDATE qa_sessions SET status = ?, updated_at = ? WHERE code = ?",
                (next_status, int(time.time()), code),
            )

        return {"ok": True}

    def pick_winner(
        self, session_code: str, player_id: str, winner_player_id: str
    ) -> dict:
        self._require_unlocked()
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        winner_player_id = self._normalize_player_id(winner_player_id)
        if not code or not player_id or not winner_player_id:
            raise QuoteAnarchyError(
                "Session code, player_id, and winner_player_id are required.", 400
            )

        winner_quote_ids: list[int] = []
        game_completed = False
        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)
            if self._session_judging_mode(session) != self.JUDGING_MODE_JUDGE:
                raise QuoteAnarchyError(
                    "This session is using everyone-votes mode.", 409
                )
            if session["status"] != "judging":
                raise QuoteAnarchyError("Winner selection is not open right now.", 409)

            players = self._list_players(conn, code)
            if not players:
                raise QuoteAnarchyError("Session has no players.", 404)
            judge_player = players[int(session["judge_index"]) % len(players)]
            if judge_player["player_id"] != player_id:
                raise QuoteAnarchyError("Only the judge can pick the winner.", 403)

            round_number = int(session["round_number"])
            winning_submission = conn.execute(
                """
                SELECT player_id, quote_id, quote_text, quote_authors
                FROM qa_submissions
                WHERE session_code = ? AND round_number = ? AND player_id = ?
                """,
                (code, round_number, winner_player_id),
            ).fetchone()
            if not winning_submission:
                raise QuoteAnarchyError(
                    "The selected winner did not submit a card.", 400
                )

            winner_quote_ids = self._store_round_winners(
                conn=conn,
                session=session,
                round_number=round_number,
                winner_rows=[
                    {
                        "player_id": winning_submission["player_id"],
                        "quote_id": int(winning_submission["quote_id"]),
                        "quote_text": winning_submission["quote_text"],
                        "quote_authors": winning_submission["quote_authors"],
                        "vote_count": 0,
                    }
                ],
            )
            self._set_reveal_or_end(
                conn=conn, session=session, round_number=round_number
            )
            updated_session = self._require_session(conn, code)
            game_completed = not self._session_is_active(updated_session)

        self._record_quote_anarchy_points(winner_quote_ids)
        return {"ok": True, "winners_recorded": True, "game_completed": game_completed}

    def vote_submission(
        self, session_code: str, player_id: str, voted_player_id: str
    ) -> dict:
        self._require_unlocked()
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        voted_player_id = self._normalize_player_id(voted_player_id)
        if not code or not player_id or not voted_player_id:
            raise QuoteAnarchyError(
                "Session code, player_id, and voted_player_id are required.", 400
            )

        winner_quote_ids: list[int] = []
        round_resolved = False
        game_completed = False
        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)
            if self._session_judging_mode(session) != self.JUDGING_MODE_ALL_VOTE:
                raise QuoteAnarchyError(
                    "Voting endpoint is only for everyone-votes mode.", 409
                )
            if session["status"] != "judging":
                raise QuoteAnarchyError("Voting is not open right now.", 409)

            round_number = int(session["round_number"])
            players = self._list_players(conn, code)
            player_ids = {row["player_id"] for row in players}
            if player_id not in player_ids:
                raise QuoteAnarchyError("You are not part of this session.", 403)

            voter_submission = conn.execute(
                """
                SELECT player_id
                FROM qa_submissions
                WHERE session_code = ? AND round_number = ? AND player_id = ?
                """,
                (code, round_number, player_id),
            ).fetchone()
            if not voter_submission:
                raise QuoteAnarchyError("Submit a white card before voting.", 409)

            voted_submission = conn.execute(
                """
                SELECT player_id, quote_id, quote_text, quote_authors
                FROM qa_submissions
                WHERE session_code = ? AND round_number = ? AND player_id = ?
                """,
                (code, round_number, voted_player_id),
            ).fetchone()
            if not voted_submission:
                raise QuoteAnarchyError(
                    "That player does not have a valid submission.", 400
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO qa_votes
                (session_code, round_number, voter_player_id, voted_player_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, round_number, player_id, voted_player_id, int(time.time())),
            )

            vote_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM qa_votes
                WHERE session_code = ? AND round_number = ?
                """,
                (code, round_number),
            ).fetchone()[0]

            required_votes = len(players)
            if vote_count >= required_votes:
                grouped = conn.execute(
                    """
                    SELECT voted_player_id, COUNT(*) AS total_votes
                    FROM qa_votes
                    WHERE session_code = ? AND round_number = ?
                    GROUP BY voted_player_id
                    ORDER BY total_votes DESC, voted_player_id ASC
                    """,
                    (code, round_number),
                ).fetchall()
                if not grouped:
                    raise QuoteAnarchyError("No votes recorded for this round.", 409)

                top_score = int(grouped[0]["total_votes"])
                winning_ids = [
                    row["voted_player_id"]
                    for row in grouped
                    if int(row["total_votes"]) == top_score
                ]

                winner_rows = []
                for winner_id in winning_ids:
                    winner_submission = conn.execute(
                        """
                        SELECT player_id, quote_id, quote_text, quote_authors
                        FROM qa_submissions
                        WHERE session_code = ? AND round_number = ? AND player_id = ?
                        """,
                        (code, round_number, winner_id),
                    ).fetchone()
                    if not winner_submission:
                        continue
                    winner_rows.append(
                        {
                            "player_id": winner_submission["player_id"],
                            "quote_id": int(winner_submission["quote_id"]),
                            "quote_text": winner_submission["quote_text"],
                            "quote_authors": winner_submission["quote_authors"],
                            "vote_count": top_score,
                        }
                    )

                if not winner_rows:
                    raise QuoteAnarchyError("Could not resolve round winners.", 409)

                winner_quote_ids = self._store_round_winners(
                    conn=conn,
                    session=session,
                    round_number=round_number,
                    winner_rows=winner_rows,
                )
                self._set_reveal_or_end(
                    conn=conn, session=session, round_number=round_number
                )
                round_resolved = True
                updated_session = self._require_session(conn, code)
                game_completed = not self._session_is_active(updated_session)
            else:
                conn.execute(
                    "UPDATE qa_sessions SET updated_at = ? WHERE code = ?",
                    (int(time.time()), code),
                )

        if round_resolved:
            self._record_quote_anarchy_points(winner_quote_ids)
        return {
            "ok": True,
            "winners_recorded": round_resolved,
            "game_completed": game_completed,
        }

    def next_round(self, session_code: str, player_id: str) -> dict:
        self._require_unlocked()
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteAnarchyError(self._session_end_message(session), 409)
            if session["status"] != "reveal":
                raise QuoteAnarchyError(
                    "Next round is only available after revealing the winner.", 409
                )
            if session["host_player_id"] != player_id:
                raise QuoteAnarchyError("Only the host can start the next round.", 403)

            players = self._list_players(conn, code)
            if len(players) < 2:
                raise QuoteAnarchyError("At least 2 players are required.", 400)

            max_rounds = self._session_max_rounds(session)
            current_round = int(session["round_number"])
            if current_round >= max_rounds:
                raise QuoteAnarchyError(
                    f"This game is capped at {max_rounds} rounds and has already ended.",
                    409,
                )

            next_round_number = current_round + 1
            mode = self._session_judging_mode(session)
            if mode == self.JUDGING_MODE_JUDGE:
                next_judge_index = (int(session["judge_index"]) + 1) % len(players)
            else:
                next_judge_index = 0

            self._deal_round(
                conn=conn,
                session_code=code,
                round_number=next_round_number,
                judge_index=next_judge_index,
                judging_mode=mode,
            )

        return {"ok": True}

    def end_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteAnarchyError("Only the host can end the game.", 403)
            if not self._session_is_active(session):
                return {"ok": True, "ended": True}

            now_ts = int(time.time())
            conn.execute(
                """
                UPDATE qa_sessions
                SET is_active = 0,
                    ended_reason = ?,
                    ended_at = ?,
                    updated_at = ?
                WHERE code = ?
                """,
                ("Game ended by host.", now_ts, now_ts, code),
            )

        return {"ok": True, "ended": True}

    def leave_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteAnarchyError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                return {"ok": True, "ended": True}

            conn.execute(
                """
                DELETE FROM qa_players
                WHERE session_code = ? AND player_id = ?
                """,
                (code, player_id),
            )

            players = self._list_players(conn, code)
            if not players:
                conn.execute("DELETE FROM qa_sessions WHERE code = ?", (code,))
                return {"ok": True, "ended": True}

            for index, player in enumerate(players, start=1):
                conn.execute(
                    """
                    UPDATE qa_players
                    SET seat = ?
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (index, code, player["player_id"]),
                )

            updated_host_id = session["host_player_id"]
            if session["host_player_id"] == player_id:
                updated_host_id = players[0]["player_id"]

            next_status = session["status"]
            next_black_card = session["black_card"]
            next_round_number = int(session["round_number"])
            next_judge_index = int(session["judge_index"])
            next_is_active = int(session["is_active"])
            next_ended_reason = session["ended_reason"]
            next_ended_at = int(session["ended_at"])

            if session["status"] != "waiting" or not self._session_is_active(session):
                next_status = "waiting"
                next_black_card = ""
                next_round_number = 0
                next_judge_index = 0
                next_is_active = 1
                next_ended_reason = ""
                next_ended_at = 0
                conn.execute("DELETE FROM qa_hands WHERE session_code = ?", (code,))
                conn.execute(
                    "DELETE FROM qa_submissions WHERE session_code = ?", (code,)
                )
                conn.execute(
                    "DELETE FROM qa_round_results WHERE session_code = ?", (code,)
                )
                conn.execute(
                    "DELETE FROM qa_round_winners WHERE session_code = ?", (code,)
                )
                conn.execute("DELETE FROM qa_votes WHERE session_code = ?", (code,))

            conn.execute(
                """
                UPDATE qa_sessions
                SET host_player_id = ?,
                    status = ?,
                    black_card = ?,
                    round_number = ?,
                    judge_index = ?,
                    is_active = ?,
                    ended_reason = ?,
                    ended_at = ?,
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    updated_host_id,
                    next_status,
                    next_black_card,
                    next_round_number,
                    next_judge_index,
                    next_is_active,
                    next_ended_reason,
                    next_ended_at,
                    int(time.time()),
                    code,
                ),
            )

        return {"ok": True, "ended": False}

    # ------------------------
    # Internal helpers
    # ------------------------

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_sessions (
                    code TEXT PRIMARY KEY,
                    host_player_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('waiting', 'collecting', 'judging', 'reveal')),
                    round_number INTEGER NOT NULL DEFAULT 0,
                    judge_index INTEGER NOT NULL DEFAULT 0,
                    black_card TEXT NOT NULL DEFAULT '',
                    judging_mode TEXT NOT NULL DEFAULT 'judge',
                    max_rounds INTEGER NOT NULL DEFAULT 8,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    ended_reason TEXT NOT NULL DEFAULT '',
                    ended_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            self._ensure_column(
                conn,
                "qa_sessions",
                "judging_mode",
                "ALTER TABLE qa_sessions ADD COLUMN judging_mode TEXT NOT NULL DEFAULT 'judge'",
            )
            self._ensure_column(
                conn,
                "qa_sessions",
                "max_rounds",
                f"ALTER TABLE qa_sessions ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT {self.DEFAULT_MAX_ROUNDS}",
            )
            self._ensure_column(
                conn,
                "qa_sessions",
                "is_active",
                "ALTER TABLE qa_sessions ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                conn,
                "qa_sessions",
                "ended_reason",
                "ALTER TABLE qa_sessions ADD COLUMN ended_reason TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "qa_sessions",
                "ended_at",
                "ALTER TABLE qa_sessions ADD COLUMN ended_at INTEGER NOT NULL DEFAULT 0",
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_players (
                    session_code TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    seat INTEGER NOT NULL,
                    joined_at INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_code, player_id),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_hands (
                    session_code TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    quote_id INTEGER NOT NULL,
                    quote_text TEXT NOT NULL,
                    quote_authors TEXT NOT NULL,
                    PRIMARY KEY (session_code, round_number, player_id, slot),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE,
                    FOREIGN KEY (session_code, player_id)
                        REFERENCES qa_players(session_code, player_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_submissions (
                    session_code TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    quote_id INTEGER NOT NULL,
                    quote_text TEXT NOT NULL,
                    quote_authors TEXT NOT NULL,
                    submitted_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, round_number, player_id),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE,
                    FOREIGN KEY (session_code, player_id)
                        REFERENCES qa_players(session_code, player_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_round_results (
                    session_code TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    winner_player_id TEXT NOT NULL,
                    black_card TEXT NOT NULL,
                    quote_id INTEGER NOT NULL,
                    quote_text TEXT NOT NULL,
                    quote_authors TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, round_number),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_round_winners (
                    session_code TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    winner_player_id TEXT NOT NULL,
                    quote_id INTEGER NOT NULL,
                    quote_text TEXT NOT NULL,
                    quote_authors TEXT NOT NULL,
                    vote_count INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, round_number, winner_player_id),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_votes (
                    session_code TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    voter_player_id TEXT NOT NULL,
                    voted_player_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, round_number, voter_player_id),
                    FOREIGN KEY (session_code) REFERENCES qa_sessions(code) ON DELETE CASCADE
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qa_players_session_seat ON qa_players(session_code, seat)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qa_submissions_round ON qa_submissions(session_code, round_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qa_votes_round ON qa_votes(session_code, round_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qa_winners_round ON qa_round_winners(session_code, round_number)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _cleanup_stale_sessions(self) -> None:
        cutoff_ts = int(time.time()) - self.STALE_SESSION_SECONDS
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM qa_sessions WHERE updated_at < ?",
                (cutoff_ts,),
            )

    def _load_black_cards(self) -> list[str]:
        if self.black_cards_path.exists():
            try:
                payload = json.loads(self.black_cards_path.read_text(encoding="utf-8"))
                cards = [
                    str(item).strip().replace("\\n", "\n")
                    for item in payload
                    if isinstance(item, str) and str(item).strip()
                ]
                if cards:
                    return cards
            except json.JSONDecodeError:
                pass

        return [
            "This meeting could have been an email, but instead we got ____.",
            "The group chat exploded after someone posted ____.",
            "My entire personality this week is just ____.",
            "The real reason we were late: ____.",
            "At 2am, all good ideas become ____.",
        ]

    def _deal_round(
        self,
        *,
        conn: sqlite3.Connection,
        session_code: str,
        round_number: int,
        judge_index: int,
        judging_mode: str,
    ) -> None:
        players = self._list_players(conn, session_code)
        if len(players) < 2:
            raise QuoteAnarchyError("At least 2 players are required.", 400)

        mode = self._normalize_judging_mode(judging_mode)
        if judge_index < 0 or judge_index >= len(players):
            judge_index = 0

        if mode == self.JUDGING_MODE_JUDGE:
            judge_player_id = players[judge_index]["player_id"]
            participants = [
                player for player in players if player["player_id"] != judge_player_id
            ]
        else:
            judge_index = 0
            participants = list(players)

        required_cards = self.HAND_SIZE * len(participants)
        quote_cards = self._sample_quote_cards(required_cards)
        dealt_at = int(time.time())

        conn.execute("DELETE FROM qa_hands WHERE session_code = ?", (session_code,))
        conn.execute(
            "DELETE FROM qa_submissions WHERE session_code = ?", (session_code,)
        )
        conn.execute("DELETE FROM qa_votes WHERE session_code = ?", (session_code,))

        card_index = 0
        for player in participants:
            for slot in range(self.HAND_SIZE):
                card = quote_cards[card_index]
                card_index += 1
                conn.execute(
                    """
                    INSERT INTO qa_hands
                    (session_code, round_number, player_id, slot, quote_id, quote_text, quote_authors)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_code,
                        round_number,
                        player["player_id"],
                        slot,
                        int(card["id"]),
                        card["quote"],
                        json.dumps(card["authors"], ensure_ascii=False),
                    ),
                )

        conn.execute(
            """
            UPDATE qa_sessions
            SET status = 'collecting',
                round_number = ?,
                judge_index = ?,
                black_card = ?,
                is_active = 1,
                ended_reason = '',
                ended_at = 0,
                updated_at = ?
            WHERE code = ?
            """,
            (
                round_number,
                judge_index,
                self._draw_black_card(),
                dealt_at,
                session_code,
            ),
        )

    def _store_round_winners(
        self,
        *,
        conn: sqlite3.Connection,
        session: sqlite3.Row,
        round_number: int,
        winner_rows: list[dict],
    ) -> list[int]:
        now_ts = int(time.time())
        session_code = session["code"]
        black_card = session["black_card"] or ""

        conn.execute(
            "DELETE FROM qa_round_winners WHERE session_code = ? AND round_number = ?",
            (session_code, round_number),
        )
        conn.execute(
            "DELETE FROM qa_round_results WHERE session_code = ? AND round_number = ?",
            (session_code, round_number),
        )

        unique_winner_ids = set()
        winner_quote_ids = []
        seen_quote_ids = set()
        for row in winner_rows:
            winner_id = row["player_id"]
            unique_winner_ids.add(winner_id)
            quote_id = int(row["quote_id"])
            conn.execute(
                """
                INSERT OR REPLACE INTO qa_round_winners
                (session_code, round_number, winner_player_id, quote_id, quote_text, quote_authors, vote_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_code,
                    round_number,
                    winner_id,
                    quote_id,
                    row["quote_text"],
                    row["quote_authors"],
                    int(row.get("vote_count", 0)),
                    now_ts,
                ),
            )
            if quote_id > 0 and quote_id not in seen_quote_ids:
                seen_quote_ids.add(quote_id)
                winner_quote_ids.append(quote_id)

        for winner_id in unique_winner_ids:
            conn.execute(
                """
                UPDATE qa_players
                SET score = score + 1
                WHERE session_code = ? AND player_id = ?
                """,
                (session_code, winner_id),
            )

        first = winner_rows[0]
        conn.execute(
            """
            INSERT OR REPLACE INTO qa_round_results
            (session_code, round_number, winner_player_id, black_card, quote_id, quote_text, quote_authors, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_code,
                round_number,
                first["player_id"],
                black_card,
                int(first["quote_id"]),
                first["quote_text"],
                first["quote_authors"],
                now_ts,
            ),
        )
        return winner_quote_ids

    def _set_reveal_or_end(
        self,
        *,
        conn: sqlite3.Connection,
        session: sqlite3.Row,
        round_number: int,
    ) -> None:
        max_rounds = self._session_max_rounds(session)
        now_ts = int(time.time())

        if round_number >= max_rounds:
            conn.execute(
                """
                UPDATE qa_sessions
                SET status = 'reveal',
                    is_active = 0,
                    ended_reason = ?,
                    ended_at = ?,
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    f"Game ended after {max_rounds} rounds.",
                    now_ts,
                    now_ts,
                    session["code"],
                ),
            )
            return

        conn.execute(
            """
            UPDATE qa_sessions
            SET status = 'reveal',
                ended_reason = '',
                ended_at = 0,
                updated_at = ?
            WHERE code = ?
            """,
            (now_ts, session["code"]),
        )

    def _record_quote_anarchy_points(self, winner_quote_ids: list[int]) -> None:
        if not winner_quote_ids:
            return

        record_fn = getattr(self.quote_store, "record_quote_anarchy_wins", None)
        if callable(record_fn):
            try:
                record_fn(winner_quote_ids)
            except Exception as exc:
                logger.warning("Could not persist Quote Anarchy win points: %s", exc)
            return

        # Fallback for quote stores that expose quote objects directly but no helper method.
        updated_quotes = []
        for raw_quote_id in winner_quote_ids:
            try:
                quote_id = int(raw_quote_id)
            except (TypeError, ValueError):
                continue
            quote = self.quote_store.get_quote_by_id(quote_id)
            if not quote:
                continue
            stats = dict(getattr(quote, "stats", {}) or {})
            stats["anarchy_points"] = int(stats.get("anarchy_points", 0)) + 1
            quote.stats = stats
            updated_quotes.append(quote)

        if not updated_quotes:
            return

        save_fn = getattr(self.quote_store, "_save", None)
        if callable(save_fn):
            try:
                save_fn()
            except Exception as exc:
                logger.warning("Could not save Quote Anarchy fallback points: %s", exc)

    @staticmethod
    def _required_submissions(players: list[sqlite3.Row], mode: str) -> int:
        if mode == QuoteAnarchyService.JUDGING_MODE_ALL_VOTE:
            return len(players)
        return max(len(players) - 1, 0)

    def _sample_quote_cards(self, count: int) -> list[dict]:
        quotes = list(self.quote_store.get_all_quotes())
        if len(quotes) < count:
            raise QuoteAnarchyError(
                f"Not enough quotes to deal this round. Need {count}, found {len(quotes)}.",
                409,
            )
        sampled = random.sample(quotes, count)
        return [
            {
                "id": int(quote.id),
                "quote": quote.quote,
                "authors": list(quote.authors or []),
            }
            for quote in sampled
        ]

    def _require_unlocked(self) -> None:
        info = self.bootstrap()
        if not info["unlocked"]:
            raise QuoteAnarchyError(
                f"Quote Anarchy unlocks at {self.MIN_QUOTES_REQUIRED} quotes. "
                f"Current total: {info['total_quotes']}.",
                403,
            )

    def _draw_black_card(self) -> str:
        if not self.black_cards:
            return "The best response to this moment is ____."
        return random.choice(self.black_cards)

    def _normalize_judging_mode(self, judging_mode: str | None) -> str:
        mode = str(judging_mode or self.JUDGING_MODE_JUDGE).strip().lower()
        if mode not in {self.JUDGING_MODE_JUDGE, self.JUDGING_MODE_ALL_VOTE}:
            mode = self.JUDGING_MODE_JUDGE
        return mode

    def _normalize_max_rounds(self, max_rounds: int | None) -> int:
        if max_rounds is None:
            return self.DEFAULT_MAX_ROUNDS
        try:
            parsed = int(max_rounds)
        except (TypeError, ValueError):
            parsed = self.DEFAULT_MAX_ROUNDS
        return max(1, min(parsed, self.MAX_ROUNDS_LIMIT))

    @staticmethod
    def _sanitize_player_name(player_name: str) -> str:
        collapsed = re.sub(r"\s+", " ", str(player_name or "")).strip()
        if not collapsed:
            return "Player"
        return collapsed[:28]

    @staticmethod
    def _normalize_code(code: str) -> str:
        if not code:
            return ""
        return re.sub(r"[^A-Z0-9]", "", str(code).upper())[:6]

    @staticmethod
    def _normalize_player_id(player_id: str | None) -> str:
        if not player_id:
            return ""
        return re.sub(r"[^A-Za-z0-9_-]", "", str(player_id))[:48]

    @staticmethod
    def _new_player_id() -> str:
        return secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:32]

    @staticmethod
    def _new_session_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(random.choice(alphabet) for _ in range(6))

    @staticmethod
    def _json_loads_list(raw_value: str) -> list[str]:
        try:
            payload = json.loads(raw_value)
            if isinstance(payload, list):
                return [str(item) for item in payload]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    @staticmethod
    def _list_players(conn: sqlite3.Connection, session_code: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT session_code, player_id, display_name, seat, joined_at, score
            FROM qa_players
            WHERE session_code = ?
            ORDER BY seat ASC, joined_at ASC
            """,
            (session_code,),
        ).fetchall()

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table_name: str, column_name: str, ddl_sql: str
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(ddl_sql)

    def _get_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                code,
                host_player_id,
                status,
                round_number,
                judge_index,
                black_card,
                COALESCE(judging_mode, 'judge') AS judging_mode,
                COALESCE(max_rounds, ?) AS max_rounds,
                COALESCE(is_active, 1) AS is_active,
                COALESCE(ended_reason, '') AS ended_reason,
                COALESCE(ended_at, 0) AS ended_at,
                created_at,
                updated_at
            FROM qa_sessions
            WHERE code = ?
            """,
            (self.DEFAULT_MAX_ROUNDS, session_code),
        ).fetchone()

    def _require_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row:
        session = self._get_session(conn, session_code)
        if not session:
            raise QuoteAnarchyError("Session not found.", 404)
        return session

    def _session_judging_mode(self, session: sqlite3.Row) -> str:
        return self._normalize_judging_mode(session["judging_mode"])

    def _session_max_rounds(self, session: sqlite3.Row) -> int:
        return self._normalize_max_rounds(session["max_rounds"])

    @staticmethod
    def _session_is_active(session: sqlite3.Row) -> bool:
        return bool(int(session["is_active"] or 0))

    def _session_end_message(self, session: sqlite3.Row) -> str:
        reason = str(session["ended_reason"] or "").strip()
        if reason:
            return reason
        if not self._session_is_active(session):
            return "This game has ended."
        return ""
