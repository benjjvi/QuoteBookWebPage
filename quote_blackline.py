from __future__ import annotations

import json
import logging
import random
import re
import secrets
import sqlite3
import time

logger = logging.getLogger(__name__)


class QuoteBlacklineError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class QuoteBlacklineService:
    GAME_NAME = "The Epstein Files: Blackline Rush"
    MAX_PLAYERS = 8
    MIN_PLAYERS = 2
    MIN_WORDS_FOR_QUOTE = 10
    STALE_SESSION_SECONDS = 12 * 60 * 60
    WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
    STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "there",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "with",
        "you",
        "your",
    }

    def __init__(self, *, db_path: str, quote_store):
        self.db_path = str(db_path)
        self.quote_store = quote_store
        self.ensure_schema()

    # ------------------------
    # Public API helpers
    # ------------------------

    def bootstrap(self) -> dict:
        quotes = list(self.quote_store.get_all_quotes())
        eligible = sum(
            1 for quote in quotes if self._word_count(str(getattr(quote, "quote", ""))) >= self.MIN_WORDS_FOR_QUOTE
        )
        return {
            "game_name": self.GAME_NAME,
            "max_players": self.MAX_PLAYERS,
            "min_players": self.MIN_PLAYERS,
            "min_words_for_quote": self.MIN_WORDS_FOR_QUOTE,
            "turn_rule": "Redactor can remove up to one word for every ten words in the quote.",
            "eligible_quote_count": eligible,
            "total_quote_count": len(quotes),
            "ready": eligible > 0,
        }

    def create_session(self, player_name: str) -> dict:
        self._cleanup_stale_sessions()
        display_name = self._sanitize_player_name(player_name)
        player_id = self._new_player_id()
        now_ts = int(time.time())

        with self._connect() as conn:
            for _ in range(24):
                code = self._new_session_code()
                try:
                    conn.execute(
                        """
                        INSERT INTO blr_sessions
                        (code, host_player_id, status, is_active, ended_reason, ended_at, turn_number,
                         redactor_player_id, source_quote_id, source_quote_text, source_quote_authors,
                         source_word_count, allowed_redactions, redaction_indices, redacted_words,
                         redacted_norms, filler_words, puzzle_text, created_at, updated_at)
                        VALUES (?, ?, 'waiting', 1, '', 0, 0, '', 0, '', '[]', 0, 0, '[]', '[]', '[]', '[]', '', ?, ?)
                        """,
                        (code, player_id, now_ts, now_ts),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise QuoteBlacklineError(
                    "Unable to create a session code right now.", 503
                )

            conn.execute(
                """
                INSERT INTO blr_players
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
            "game_name": self.GAME_NAME,
        }

    def join_session(
        self, session_code: str, player_name: str, player_id: str | None = None
    ) -> dict:
        self._cleanup_stale_sessions()
        code = self._normalize_code(session_code)
        if not code:
            raise QuoteBlacklineError("Session code is required.", 400)

        display_name = self._sanitize_player_name(player_name)
        requested_player_id = self._normalize_player_id(player_id)
        now_ts = int(time.time())

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteBlacklineError("Session not found.", 404)

            if requested_player_id:
                existing = conn.execute(
                    """
                    SELECT session_code, player_id, display_name
                    FROM blr_players
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (code, requested_player_id),
                ).fetchone()
                if existing:
                    if display_name and existing["display_name"] != display_name:
                        conn.execute(
                            """
                            UPDATE blr_players
                            SET display_name = ?
                            WHERE session_code = ? AND player_id = ?
                            """,
                            (display_name, code, requested_player_id),
                        )
                    conn.execute(
                        "UPDATE blr_sessions SET updated_at = ? WHERE code = ?",
                        (now_ts, code),
                    )
                    return {
                        "session_code": code,
                        "player_id": requested_player_id,
                        "display_name": display_name or existing["display_name"],
                        "max_players": self.MAX_PLAYERS,
                        "game_name": self.GAME_NAME,
                    }

            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)

            if session["status"] != "waiting":
                raise QuoteBlacklineError(
                    "This session already started. Try another code.", 409
                )

            current_players = self._list_players(conn, code)
            if len(current_players) >= self.MAX_PLAYERS:
                raise QuoteBlacklineError("Session is full (8 players max).", 409)

            new_player_id = requested_player_id or self._new_player_id()
            seat = max([int(player["seat"]) for player in current_players] + [0]) + 1

            try:
                conn.execute(
                    """
                    INSERT INTO blr_players
                    (session_code, player_id, display_name, seat, joined_at, score)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (code, new_player_id, display_name, seat, now_ts),
                )
            except sqlite3.IntegrityError as exc:
                raise QuoteBlacklineError(
                    "Unable to join with this player identity.", 409
                ) from exc

            conn.execute(
                "UPDATE blr_sessions SET updated_at = ? WHERE code = ?",
                (now_ts, code),
            )

        return {
            "session_code": code,
            "player_id": new_player_id,
            "display_name": display_name,
            "max_players": self.MAX_PLAYERS,
            "game_name": self.GAME_NAME,
        }

    def get_state(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteBlacklineError("Session not found.", 404)

            players = self._list_players(conn, code)
            player_map = {row["player_id"]: row for row in players}
            viewer = player_map.get(player_id)
            if not viewer:
                raise QuoteBlacklineError("You are not part of this session.", 403)

            status = session["status"]
            is_active = self._session_is_active(session)
            turn_number = int(session["turn_number"] or 0)
            redactor_player_id = str(session["redactor_player_id"] or "")
            redactor_row = player_map.get(redactor_player_id)
            redactor_name = (
                redactor_row["display_name"] if redactor_row else "Unassigned"
            )

            answer_words = self._json_loads_list(session["redacted_words"])
            answer_norms = self._json_loads_list(session["redacted_norms"])
            filler_words = self._json_loads_list(session["filler_words"])
            redaction_indices = [
                idx
                for idx in self._json_loads_ints(session["redaction_indices"])
                if idx >= 0
            ]
            gap_count = len(answer_words)

            guess_rows = conn.execute(
                """
                SELECT player_id, guess_words, guess_norms, attempt_count, is_correct,
                       solved_rank, points_awarded, solved_at, updated_at
                FROM blr_guesses
                WHERE session_code = ? AND turn_number = ?
                ORDER BY solved_rank ASC, solved_at ASC, updated_at ASC
                """,
                (code, turn_number),
            ).fetchall()

            viewer_guess = None
            solvers = []
            for row in guess_rows:
                if row["player_id"] == player_id:
                    viewer_guess = row
                solved_rank = int(row["solved_rank"] or 0)
                if solved_rank <= 0:
                    continue
                player_row = player_map.get(row["player_id"])
                solvers.append(
                    {
                        "player_id": row["player_id"],
                        "player_name": (
                            player_row["display_name"] if player_row else "Unknown"
                        ),
                        "rank": solved_rank,
                        "points_awarded": int(row["points_awarded"] or 0),
                        "solved_at": int(row["solved_at"] or 0),
                    }
                )

            solvers.sort(key=lambda item: (item["rank"], item["solved_at"]))
            solved_count = len(solvers)
            guesser_count = max(len(players) - 1, 0)

            source_quote_text = str(session["source_quote_text"] or "")
            source_quote_words = self._extract_words(source_quote_text)
            redaction_options = []
            if status == "redacting" and player_id == redactor_player_id:
                redaction_options = [
                    {"index": idx, "word": word["word"]}
                    for idx, word in enumerate(source_quote_words)
                ]

            viewer_is_host = player_id == session["host_player_id"]
            viewer_is_redactor = player_id == redactor_player_id
            viewer_solved_rank = int(viewer_guess["solved_rank"] or 0) if viewer_guess else 0
            viewer_points_awarded = int(viewer_guess["points_awarded"] or 0) if viewer_guess else 0
            viewer_attempt_count = int(viewer_guess["attempt_count"] or 0) if viewer_guess else 0
            viewer_last_guess = (
                self._json_loads_list(viewer_guess["guess_words"]) if viewer_guess else []
            )

            can_start = (
                status == "waiting"
                and is_active
                and viewer_is_host
                and len(players) >= self.MIN_PLAYERS
            )
            can_submit_redaction = (
                status == "redacting" and is_active and viewer_is_redactor
            )
            can_submit_guess = (
                status == "guessing"
                and is_active
                and not viewer_is_redactor
                and gap_count > 0
                and viewer_solved_rank == 0
            )
            can_end_turn = status == "guessing" and is_active and viewer_is_host
            can_next_turn = status == "reveal" and is_active and viewer_is_host

            reveal_answers = status == "reveal" or viewer_is_redactor
            return {
                "session": {
                    "code": code,
                    "status": status,
                    "is_active": is_active,
                    "ended_reason": self._session_end_message(session),
                    "host_player_id": session["host_player_id"],
                    "turn_number": turn_number,
                    "redactor_player_id": redactor_player_id,
                    "redactor_name": redactor_name,
                    "max_players": self.MAX_PLAYERS,
                },
                "viewer": {
                    "player_id": player_id,
                    "display_name": viewer["display_name"],
                    "is_host": viewer_is_host,
                    "is_redactor": viewer_is_redactor,
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
                "turn": {
                    "number": turn_number,
                    "status": status,
                    "source_quote_id": int(session["source_quote_id"] or 0),
                    "source_quote": source_quote_text if reveal_answers else "",
                    "source_quote_authors": (
                        self._json_loads_list(session["source_quote_authors"])
                        if reveal_answers
                        else []
                    ),
                    "source_word_count": int(session["source_word_count"] or 0),
                    "allowed_redactions": int(session["allowed_redactions"] or 0),
                    "redaction_options": redaction_options,
                    "selected_redaction_indices": redaction_indices,
                    "gap_count": gap_count,
                    "puzzle_text": (
                        str(session["puzzle_text"] or "")
                        if status in {"guessing", "reveal"}
                        else ""
                    ),
                    "filler_words": filler_words if status in {"guessing", "reveal"} else [],
                    "answers": answer_words if reveal_answers else [],
                    "solvers": solvers,
                    "solved_count": solved_count,
                    "guesser_count": guesser_count,
                    "you_solved_rank": viewer_solved_rank,
                    "you_points_awarded": viewer_points_awarded,
                    "you_attempt_count": viewer_attempt_count,
                    "your_last_guess": viewer_last_guess,
                    "can_start": can_start,
                    "can_submit_redaction": can_submit_redaction,
                    "can_submit_guess": can_submit_guess,
                    "can_end_turn": can_end_turn,
                    "can_next_turn": can_next_turn,
                    "can_end_game": is_active and viewer_is_host,
                },
            }

    def start_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteBlacklineError("Only the host can start the game.", 403)
            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)
            if session["status"] != "waiting":
                raise QuoteBlacklineError("This session already started.", 409)

            players = self._list_players(conn, code)
            if len(players) < self.MIN_PLAYERS:
                raise QuoteBlacklineError(
                    f"At least {self.MIN_PLAYERS} players are required to start.", 400
                )

            first_redactor_id = players[0]["player_id"]
            self._start_turn(
                conn=conn,
                session_code=code,
                turn_number=1,
                redactor_player_id=first_redactor_id,
            )

        return {"ok": True}

    def submit_redaction(
        self, session_code: str, player_id: str, redaction_indices: list[int]
    ) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)
        if not isinstance(redaction_indices, list):
            raise QuoteBlacklineError("redaction_indices must be a list.", 400)

        normalized_indices = []
        seen = set()
        for raw_index in redaction_indices:
            try:
                parsed = int(raw_index)
            except (TypeError, ValueError):
                continue
            if parsed < 0 or parsed in seen:
                continue
            seen.add(parsed)
            normalized_indices.append(parsed)
        normalized_indices.sort()

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)
            if session["status"] != "redacting":
                raise QuoteBlacklineError("Redaction is not open right now.", 409)
            if session["redactor_player_id"] != player_id:
                raise QuoteBlacklineError("Only this turn's redactor can submit.", 403)

            source_quote_text = str(session["source_quote_text"] or "")
            source_words = self._extract_words(source_quote_text)
            if not source_words:
                raise QuoteBlacklineError("This turn has no valid quote words.", 409)

            allowed = int(session["allowed_redactions"] or 0)
            if allowed <= 0:
                raise QuoteBlacklineError("No redactions are available for this quote.", 409)

            if not normalized_indices:
                raise QuoteBlacklineError(
                    f"Select at least one word (max {allowed}).", 400
                )
            if len(normalized_indices) > allowed:
                raise QuoteBlacklineError(
                    f"You can redact at most {allowed} words this turn.", 400
                )
            if max(normalized_indices) >= len(source_words):
                raise QuoteBlacklineError("One or more redaction indices are invalid.", 400)

            redacted_words = [source_words[idx]["word"] for idx in normalized_indices]
            redacted_norms = [
                source_words[idx]["normalized"] or self._normalize_word(source_words[idx]["word"])
                for idx in normalized_indices
            ]
            filler_words = self._pick_relevant_words(
                source_quote_id=int(session["source_quote_id"] or 0),
                source_quote_text=source_quote_text,
                excluded_norms=set(redacted_norms),
                count=len(normalized_indices),
            )
            puzzle_text = self._render_puzzle_text(
                source_quote_text=source_quote_text,
                redaction_indices=normalized_indices,
                filler_words=filler_words,
            )
            now_ts = int(time.time())

            conn.execute(
                """
                UPDATE blr_sessions
                SET status = 'guessing',
                    redaction_indices = ?,
                    redacted_words = ?,
                    redacted_norms = ?,
                    filler_words = ?,
                    puzzle_text = ?,
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    json.dumps(normalized_indices, ensure_ascii=False),
                    json.dumps(redacted_words, ensure_ascii=False),
                    json.dumps(redacted_norms, ensure_ascii=False),
                    json.dumps(filler_words, ensure_ascii=False),
                    puzzle_text,
                    now_ts,
                    code,
                ),
            )
            conn.execute(
                "DELETE FROM blr_guesses WHERE session_code = ? AND turn_number = ?",
                (code, int(session["turn_number"] or 0)),
            )

        return {"ok": True, "gap_count": len(normalized_indices)}

    def submit_guess(
        self, session_code: str, player_id: str, guesses: list[str]
    ) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)
        if not isinstance(guesses, list):
            raise QuoteBlacklineError("guesses must be a list.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)
            if session["status"] != "guessing":
                raise QuoteBlacklineError("Guessing is not open right now.", 409)
            if session["redactor_player_id"] == player_id:
                raise QuoteBlacklineError("The redactor cannot submit guesses.", 409)

            turn_number = int(session["turn_number"] or 0)
            answer_norms = self._json_loads_list(session["redacted_norms"])
            if not answer_norms:
                raise QuoteBlacklineError("This turn has no answers to guess.", 409)
            if len(guesses) != len(answer_norms):
                raise QuoteBlacklineError(
                    f"Expected {len(answer_norms)} guesses, got {len(guesses)}.", 400
                )

            players = self._list_players(conn, code)
            player_ids = {row["player_id"] for row in players}
            if player_id not in player_ids:
                raise QuoteBlacklineError("You are not part of this session.", 403)

            existing_guess = conn.execute(
                """
                SELECT player_id, attempt_count, solved_rank
                FROM blr_guesses
                WHERE session_code = ? AND turn_number = ? AND player_id = ?
                """,
                (code, turn_number, player_id),
            ).fetchone()
            if existing_guess and int(existing_guess["solved_rank"] or 0) > 0:
                return {
                    "ok": True,
                    "correct": True,
                    "already_solved": True,
                    "solved_rank": int(existing_guess["solved_rank"]),
                    "points_awarded": 0,
                }

            guess_words = [str(item or "").strip() for item in guesses]
            guess_norms = [self._normalize_word(item) for item in guess_words]
            is_correct = guess_norms == answer_norms
            now_ts = int(time.time())

            previous_attempts = (
                int(existing_guess["attempt_count"]) if existing_guess else 0
            )
            attempt_count = previous_attempts + 1
            solved_rank = 0
            points_awarded = 0
            solved_at = 0

            if is_correct:
                solved_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM blr_guesses
                    WHERE session_code = ? AND turn_number = ? AND solved_rank > 0
                    """,
                    (code, turn_number),
                ).fetchone()[0]
                solved_rank = int(solved_count) + 1
                guesser_count = max(len(players) - 1, 1)
                points_awarded = max(guesser_count - solved_rank + 1, 1)
                solved_at = now_ts

            conn.execute(
                """
                INSERT OR REPLACE INTO blr_guesses
                (session_code, turn_number, player_id, guess_words, guess_norms, attempt_count,
                 is_correct, solved_rank, points_awarded, solved_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    turn_number,
                    player_id,
                    json.dumps(guess_words, ensure_ascii=False),
                    json.dumps(guess_norms, ensure_ascii=False),
                    attempt_count,
                    1 if is_correct else 0,
                    solved_rank,
                    points_awarded,
                    solved_at,
                    now_ts,
                ),
            )

            if is_correct and points_awarded > 0:
                conn.execute(
                    """
                    UPDATE blr_players
                    SET score = score + ?
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (points_awarded, code, player_id),
                )

            solved_total = conn.execute(
                """
                SELECT COUNT(*)
                FROM blr_guesses
                WHERE session_code = ? AND turn_number = ? AND solved_rank > 0
                """,
                (code, turn_number),
            ).fetchone()[0]
            guesser_total = max(len(players) - 1, 0)
            all_solved = guesser_total > 0 and int(solved_total) >= guesser_total
            if all_solved:
                conn.execute(
                    """
                    UPDATE blr_sessions
                    SET status = 'reveal', updated_at = ?
                    WHERE code = ?
                    """,
                    (now_ts, code),
                )
            else:
                conn.execute(
                    "UPDATE blr_sessions SET updated_at = ? WHERE code = ?",
                    (now_ts, code),
                )

        return {
            "ok": True,
            "correct": bool(is_correct),
            "solved_rank": solved_rank,
            "points_awarded": points_awarded,
            "all_solved": all_solved,
        }

    def end_turn(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)
            if session["host_player_id"] != player_id:
                raise QuoteBlacklineError("Only the host can reveal answers.", 403)
            if session["status"] != "guessing":
                raise QuoteBlacklineError("Turn reveal is only available while guessing.", 409)

            conn.execute(
                """
                UPDATE blr_sessions
                SET status = 'reveal',
                    updated_at = ?
                WHERE code = ?
                """,
                (int(time.time()), code),
            )

        return {"ok": True}

    def next_turn(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteBlacklineError(self._session_end_message(session), 409)
            if session["host_player_id"] != player_id:
                raise QuoteBlacklineError("Only the host can start the next turn.", 403)
            if session["status"] != "reveal":
                raise QuoteBlacklineError(
                    "Next turn is only available after reveal.", 409
                )

            players = self._list_players(conn, code)
            if len(players) < self.MIN_PLAYERS:
                raise QuoteBlacklineError(
                    f"At least {self.MIN_PLAYERS} players are required.", 400
                )

            next_turn_number = int(session["turn_number"] or 0) + 1
            next_redactor_id = self._next_redactor_player_id(
                players=players,
                current_redactor_id=str(session["redactor_player_id"] or ""),
            )
            self._start_turn(
                conn=conn,
                session_code=code,
                turn_number=next_turn_number,
                redactor_player_id=next_redactor_id,
            )

        return {"ok": True}

    def end_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteBlacklineError("Only the host can end the game.", 403)
            if not self._session_is_active(session):
                return {"ok": True, "ended": True}

            now_ts = int(time.time())
            conn.execute(
                """
                UPDATE blr_sessions
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
            raise QuoteBlacklineError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                return {"ok": True, "ended": True}

            conn.execute(
                """
                DELETE FROM blr_players
                WHERE session_code = ? AND player_id = ?
                """,
                (code, player_id),
            )

            players = self._list_players(conn, code)
            if not players:
                conn.execute("DELETE FROM blr_guesses WHERE session_code = ?", (code,))
                conn.execute("DELETE FROM blr_sessions WHERE code = ?", (code,))
                return {"ok": True, "ended": True}

            for index, row in enumerate(players, start=1):
                conn.execute(
                    """
                    UPDATE blr_players
                    SET seat = ?
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (index, code, row["player_id"]),
                )

            updated_host_id = session["host_player_id"]
            if session["host_player_id"] == player_id:
                updated_host_id = players[0]["player_id"]

            now_ts = int(time.time())
            if session["status"] == "waiting" or not self._session_is_active(session):
                conn.execute(
                    """
                    UPDATE blr_sessions
                    SET host_player_id = ?,
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (updated_host_id, now_ts, code),
                )
                return {"ok": True, "ended": False}

            self._reset_to_waiting(
                conn=conn,
                session_code=code,
                host_player_id=updated_host_id,
                reason="Round reset after a player left. Host can start a new turn.",
            )

        return {"ok": True, "ended": False}

    # ------------------------
    # Internal helpers
    # ------------------------

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blr_sessions (
                    code TEXT PRIMARY KEY,
                    host_player_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('waiting', 'redacting', 'guessing', 'reveal')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    ended_reason TEXT NOT NULL DEFAULT '',
                    ended_at INTEGER NOT NULL DEFAULT 0,
                    turn_number INTEGER NOT NULL DEFAULT 0,
                    redactor_player_id TEXT NOT NULL DEFAULT '',
                    source_quote_id INTEGER NOT NULL DEFAULT 0,
                    source_quote_text TEXT NOT NULL DEFAULT '',
                    source_quote_authors TEXT NOT NULL DEFAULT '[]',
                    source_word_count INTEGER NOT NULL DEFAULT 0,
                    allowed_redactions INTEGER NOT NULL DEFAULT 0,
                    redaction_indices TEXT NOT NULL DEFAULT '[]',
                    redacted_words TEXT NOT NULL DEFAULT '[]',
                    redacted_norms TEXT NOT NULL DEFAULT '[]',
                    filler_words TEXT NOT NULL DEFAULT '[]',
                    puzzle_text TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blr_players (
                    session_code TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    seat INTEGER NOT NULL,
                    joined_at INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_code, player_id),
                    FOREIGN KEY (session_code) REFERENCES blr_sessions(code) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blr_guesses (
                    session_code TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    guess_words TEXT NOT NULL DEFAULT '[]',
                    guess_norms TEXT NOT NULL DEFAULT '[]',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    is_correct INTEGER NOT NULL DEFAULT 0,
                    solved_rank INTEGER NOT NULL DEFAULT 0,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    solved_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, turn_number, player_id),
                    FOREIGN KEY (session_code) REFERENCES blr_sessions(code) ON DELETE CASCADE,
                    FOREIGN KEY (session_code, player_id)
                        REFERENCES blr_players(session_code, player_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_blr_players_session_seat ON blr_players(session_code, seat)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_blr_guesses_turn ON blr_guesses(session_code, turn_number)"
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
                "DELETE FROM blr_sessions WHERE updated_at < ?",
                (cutoff_ts,),
            )

    def _start_turn(
        self,
        *,
        conn: sqlite3.Connection,
        session_code: str,
        turn_number: int,
        redactor_player_id: str,
    ) -> None:
        quote_payload = self._pick_turn_quote()
        source_word_count = int(quote_payload["word_count"])
        allowed_redactions = max(1, source_word_count // 10)
        now_ts = int(time.time())

        conn.execute("DELETE FROM blr_guesses WHERE session_code = ?", (session_code,))
        conn.execute(
            """
            UPDATE blr_sessions
            SET status = 'redacting',
                is_active = 1,
                ended_reason = '',
                ended_at = 0,
                turn_number = ?,
                redactor_player_id = ?,
                source_quote_id = ?,
                source_quote_text = ?,
                source_quote_authors = ?,
                source_word_count = ?,
                allowed_redactions = ?,
                redaction_indices = '[]',
                redacted_words = '[]',
                redacted_norms = '[]',
                filler_words = '[]',
                puzzle_text = '',
                updated_at = ?
            WHERE code = ?
            """,
            (
                turn_number,
                redactor_player_id,
                int(quote_payload["id"]),
                quote_payload["quote"],
                json.dumps(quote_payload["authors"], ensure_ascii=False),
                source_word_count,
                allowed_redactions,
                now_ts,
                session_code,
            ),
        )

    def _reset_to_waiting(
        self,
        *,
        conn: sqlite3.Connection,
        session_code: str,
        host_player_id: str,
        reason: str = "",
    ) -> None:
        conn.execute("DELETE FROM blr_guesses WHERE session_code = ?", (session_code,))
        conn.execute(
            """
            UPDATE blr_sessions
            SET host_player_id = ?,
                status = 'waiting',
                redactor_player_id = '',
                source_quote_id = 0,
                source_quote_text = '',
                source_quote_authors = '[]',
                source_word_count = 0,
                allowed_redactions = 0,
                redaction_indices = '[]',
                redacted_words = '[]',
                redacted_norms = '[]',
                filler_words = '[]',
                puzzle_text = '',
                ended_reason = ?,
                ended_at = 0,
                updated_at = ?
            WHERE code = ?
            """,
            (host_player_id, reason, int(time.time()), session_code),
        )

    def _pick_turn_quote(self) -> dict:
        eligible_quotes = []
        for quote in self.quote_store.get_all_quotes():
            text = str(getattr(quote, "quote", "") or "")
            words = self._extract_words(text)
            if len(words) < self.MIN_WORDS_FOR_QUOTE:
                continue
            eligible_quotes.append(
                {
                    "id": int(getattr(quote, "id", 0) or 0),
                    "quote": text,
                    "authors": list(getattr(quote, "authors", []) or []),
                    "word_count": len(words),
                }
            )

        if not eligible_quotes:
            raise QuoteBlacklineError(
                f"No quotes with at least {self.MIN_WORDS_FOR_QUOTE} words are available yet.",
                409,
            )
        return random.choice(eligible_quotes)

    def _pick_relevant_words(
        self,
        *,
        source_quote_id: int,
        source_quote_text: str,
        excluded_norms: set[str],
        count: int,
    ) -> list[str]:
        count = max(int(count or 0), 0)
        if count == 0:
            return []

        source_keywords = {
            word["normalized"]
            for word in self._extract_words(source_quote_text)
            if word["normalized"]
            and len(word["normalized"]) >= 4
            and word["normalized"] not in self.STOPWORDS
        }

        scored_norms: dict[str, int] = {}
        display_for_norm: dict[str, str] = {}
        fallback_norms: dict[str, str] = {}

        for quote in self.quote_store.get_all_quotes():
            quote_id = int(getattr(quote, "id", 0) or 0)
            quote_text = str(getattr(quote, "quote", "") or "")
            words = self._extract_words(quote_text)
            if not words:
                continue

            norms_in_quote = {
                word["normalized"]
                for word in words
                if word["normalized"]
                and len(word["normalized"]) >= 3
                and word["normalized"] not in self.STOPWORDS
                and word["normalized"] not in excluded_norms
            }
            if not norms_in_quote:
                continue

            overlap_score = (
                len(source_keywords.intersection(norms_in_quote))
                if quote_id != source_quote_id
                else 0
            )
            for word in words:
                norm = word["normalized"]
                if (
                    not norm
                    or len(norm) < 3
                    or norm in self.STOPWORDS
                    or norm in excluded_norms
                ):
                    continue
                fallback_norms.setdefault(norm, word["word"].lower())
                if overlap_score <= 0:
                    continue
                scored_norms[norm] = scored_norms.get(norm, 0) + overlap_score
                display_for_norm.setdefault(norm, word["word"].lower())

        picks: list[str] = []
        chosen_norms: set[str] = set()
        weighted_pool = dict(scored_norms)
        while weighted_pool and len(picks) < count:
            picked_norm = self._weighted_pick(weighted_pool)
            if not picked_norm:
                break
            weighted_pool.pop(picked_norm, None)
            if picked_norm in chosen_norms:
                continue
            chosen_norms.add(picked_norm)
            picks.append(display_for_norm.get(picked_norm, picked_norm))

        if len(picks) < count:
            fallback_candidates = [
                norm
                for norm in fallback_norms.keys()
                if norm not in chosen_norms
            ]
            random.shuffle(fallback_candidates)
            for norm in fallback_candidates:
                picks.append(fallback_norms.get(norm, norm))
                chosen_norms.add(norm)
                if len(picks) >= count:
                    break

        while len(picks) < count:
            picks.append("redacted")

        return picks[:count]

    @staticmethod
    def _weighted_pick(weighted_items: dict[str, int]) -> str:
        if not weighted_items:
            return ""
        total = sum(max(1, int(weight)) for weight in weighted_items.values())
        if total <= 0:
            return random.choice(list(weighted_items.keys()))
        marker = random.randint(1, total)
        running = 0
        for key, weight in weighted_items.items():
            running += max(1, int(weight))
            if marker <= running:
                return key
        return next(iter(weighted_items.keys()))

    def _render_puzzle_text(
        self,
        *,
        source_quote_text: str,
        redaction_indices: list[int],
        filler_words: list[str],
    ) -> str:
        words = self._extract_words(source_quote_text)
        if not words or not redaction_indices:
            return source_quote_text

        replacement_by_index: dict[int, str] = {}
        for idx, word_index in enumerate(redaction_indices):
            filler = (
                filler_words[idx]
                if idx < len(filler_words)
                else "redacted"
            )
            replacement_by_index[word_index] = self._sanitize_filler_display(filler)

        parts = []
        cursor = 0
        for idx, word in enumerate(words):
            parts.append(source_quote_text[cursor : word["start"]])
            if idx not in replacement_by_index:
                parts.append(source_quote_text[word["start"] : word["end"]])
            else:
                filler = replacement_by_index[idx] or "REDACTED"
                parts.append(f"[[{filler.upper()}]]")
            cursor = word["end"]
        parts.append(source_quote_text[cursor:])
        return "".join(parts)

    @staticmethod
    def _sanitize_filler_display(raw_word: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9'’-]", "", str(raw_word or "")).strip()
        return cleaned[:24] or "REDACTED"

    def _extract_words(self, text: str) -> list[dict]:
        output = []
        for match in self.WORD_RE.finditer(str(text or "")):
            word = match.group(0)
            output.append(
                {
                    "word": word,
                    "normalized": self._normalize_word(word),
                    "start": match.start(),
                    "end": match.end(),
                }
            )
        return output

    def _word_count(self, text: str) -> int:
        return len(self._extract_words(text))

    @staticmethod
    def _normalize_word(word: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(word or "").lower())

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
    def _json_loads_ints(raw_value: str) -> list[int]:
        try:
            payload = json.loads(raw_value)
            if not isinstance(payload, list):
                return []
        except (json.JSONDecodeError, TypeError):
            return []

        out = []
        for item in payload:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _list_players(conn: sqlite3.Connection, session_code: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT session_code, player_id, display_name, seat, joined_at, score
            FROM blr_players
            WHERE session_code = ?
            ORDER BY seat ASC, joined_at ASC
            """,
            (session_code,),
        ).fetchall()

    @staticmethod
    def _next_redactor_player_id(
        *, players: list[sqlite3.Row], current_redactor_id: str
    ) -> str:
        if not players:
            return ""
        ids = [row["player_id"] for row in players]
        if current_redactor_id in ids:
            idx = ids.index(current_redactor_id)
            return ids[(idx + 1) % len(ids)]
        return ids[0]

    def _get_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                code,
                host_player_id,
                status,
                is_active,
                ended_reason,
                ended_at,
                turn_number,
                redactor_player_id,
                source_quote_id,
                source_quote_text,
                source_quote_authors,
                source_word_count,
                allowed_redactions,
                redaction_indices,
                redacted_words,
                redacted_norms,
                filler_words,
                puzzle_text,
                created_at,
                updated_at
            FROM blr_sessions
            WHERE code = ?
            """,
            (session_code,),
        ).fetchone()

    def _require_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row:
        session = self._get_session(conn, session_code)
        if not session:
            raise QuoteBlacklineError("Session not found.", 404)
        return session

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
