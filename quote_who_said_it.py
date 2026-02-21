from __future__ import annotations

import json
import random
import re
import secrets
import sqlite3
import time


class QuoteWhoSaidItError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class QuoteWhoSaidItService:
    GAME_NAME = "Who Even Said That?"
    MAX_PLAYERS = 8
    MIN_PLAYERS = 3
    OPTIONS_PER_QUESTION = 4
    STALE_SESSION_SECONDS = 12 * 60 * 60

    def __init__(self, *, db_path: str, quote_store):
        self.db_path = str(db_path)
        self.quote_store = quote_store
        self.ensure_schema()

    # ------------------------
    # Public API helpers
    # ------------------------

    def bootstrap(self) -> dict:
        quotes = list(self.quote_store.get_all_quotes())
        author_pool = self._collect_author_pool(quotes)
        eligible_quotes = self._build_eligible_quotes(quotes, author_pool)
        return {
            "game_name": self.GAME_NAME,
            "min_players": self.MIN_PLAYERS,
            "max_players": self.MAX_PLAYERS,
            "options_per_question": self.OPTIONS_PER_QUESTION,
            "speed_rule": "Correct answers score by speed rank each round.",
            "eligible_quote_count": len(eligible_quotes),
            "author_pool_count": len(author_pool),
            "total_quote_count": len(quotes),
            "ready": bool(eligible_quotes),
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
                        INSERT INTO wsi_sessions
                        (code, host_player_id, status, is_active, ended_reason, ended_at,
                         turn_number, source_quote_id, source_quote_text, correct_author,
                         option_authors, used_quote_ids, created_at, updated_at)
                        VALUES (?, ?, 'waiting', 1, '', 0, 0, 0, '', '', '[]', '[]', ?, ?)
                        """,
                        (code, player_id, now_ts, now_ts),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise QuoteWhoSaidItError(
                    "Unable to create a session code right now.", 503
                )

            conn.execute(
                """
                INSERT INTO wsi_players
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
            "min_players": self.MIN_PLAYERS,
            "game_name": self.GAME_NAME,
        }

    def join_session(
        self, session_code: str, player_name: str, player_id: str | None = None
    ) -> dict:
        self._cleanup_stale_sessions()
        code = self._normalize_code(session_code)
        if not code:
            raise QuoteWhoSaidItError("Session code is required.", 400)

        display_name = self._sanitize_player_name(player_name)
        requested_player_id = self._normalize_player_id(player_id)
        now_ts = int(time.time())

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteWhoSaidItError("Session not found.", 404)

            if requested_player_id:
                existing = conn.execute(
                    """
                    SELECT session_code, player_id, display_name
                    FROM wsi_players
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (code, requested_player_id),
                ).fetchone()
                if existing:
                    if display_name and existing["display_name"] != display_name:
                        conn.execute(
                            """
                            UPDATE wsi_players
                            SET display_name = ?
                            WHERE session_code = ? AND player_id = ?
                            """,
                            (display_name, code, requested_player_id),
                        )
                    conn.execute(
                        "UPDATE wsi_sessions SET updated_at = ? WHERE code = ?",
                        (now_ts, code),
                    )
                    return {
                        "session_code": code,
                        "player_id": requested_player_id,
                        "display_name": display_name or existing["display_name"],
                        "max_players": self.MAX_PLAYERS,
                        "min_players": self.MIN_PLAYERS,
                        "game_name": self.GAME_NAME,
                    }

            if not self._session_is_active(session):
                raise QuoteWhoSaidItError(self._session_end_message(session), 409)

            if session["status"] != "waiting":
                raise QuoteWhoSaidItError(
                    "This session already started. Try another code.", 409
                )

            current_players = self._list_players(conn, code)
            if len(current_players) >= self.MAX_PLAYERS:
                raise QuoteWhoSaidItError("Session is full (8 players max).", 409)

            new_player_id = requested_player_id or self._new_player_id()
            seat = max([int(player["seat"]) for player in current_players] + [0]) + 1

            try:
                conn.execute(
                    """
                    INSERT INTO wsi_players
                    (session_code, player_id, display_name, seat, joined_at, score)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (code, new_player_id, display_name, seat, now_ts),
                )
            except sqlite3.IntegrityError as exc:
                raise QuoteWhoSaidItError(
                    "Unable to join with this player identity.", 409
                ) from exc

            conn.execute(
                "UPDATE wsi_sessions SET updated_at = ? WHERE code = ?",
                (now_ts, code),
            )

        return {
            "session_code": code,
            "player_id": new_player_id,
            "display_name": display_name,
            "max_players": self.MAX_PLAYERS,
            "min_players": self.MIN_PLAYERS,
            "game_name": self.GAME_NAME,
        }

    def get_state(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                raise QuoteWhoSaidItError("Session not found.", 404)

            players = self._list_players(conn, code)
            player_map = {row["player_id"]: row for row in players}
            viewer = player_map.get(player_id)
            if not viewer:
                raise QuoteWhoSaidItError("You are not part of this session.", 403)

            status = str(session["status"] or "waiting")
            is_active = self._session_is_active(session)
            turn_number = int(session["turn_number"] or 0)
            source_quote_text = str(session["source_quote_text"] or "")
            option_authors = self._json_loads_list(session["option_authors"])
            correct_author = str(session["correct_author"] or "")

            answer_rows = conn.execute(
                """
                SELECT player_id, selected_author, is_correct, answered_at,
                       answer_order, points_awarded
                FROM wsi_answers
                WHERE session_code = ? AND turn_number = ?
                ORDER BY answer_order ASC, answered_at ASC
                """,
                (code, turn_number),
            ).fetchall()

            answer_map = {row["player_id"]: row for row in answer_rows}
            viewer_answer = answer_map.get(player_id)

            reveal_answers = status == "reveal" or not is_active
            answered_count = len(answer_rows)
            correct_count = sum(
                1 for row in answer_rows if bool(int(row["is_correct"] or 0))
            )

            fastest_correct = []
            for row in answer_rows:
                rank = int(row["answer_order"] or 0)
                if rank <= 0:
                    continue
                player_row = player_map.get(row["player_id"])
                fastest_correct.append(
                    {
                        "player_id": row["player_id"],
                        "player_name": (
                            player_row["display_name"] if player_row else "Unknown"
                        ),
                        "rank": rank,
                        "points_awarded": int(row["points_awarded"] or 0),
                        "answered_at": int(row["answered_at"] or 0),
                    }
                )

            answer_rows_for_ui = []
            for player in players:
                player_answer = answer_map.get(player["player_id"])
                answered = bool(player_answer)
                answer_rows_for_ui.append(
                    {
                        "player_id": player["player_id"],
                        "player_name": player["display_name"],
                        "answered": answered,
                        "selected_author": (
                            str(player_answer["selected_author"] or "")
                            if answered and reveal_answers
                            else ""
                        ),
                        "is_correct": (
                            bool(int(player_answer["is_correct"] or 0))
                            if answered and reveal_answers
                            else False
                        ),
                        "answer_order": (
                            int(player_answer["answer_order"] or 0)
                            if answered and reveal_answers
                            else 0
                        ),
                        "points_awarded": (
                            int(player_answer["points_awarded"] or 0)
                            if answered and reveal_answers
                            else 0
                        ),
                    }
                )

            viewer_is_host = player_id == session["host_player_id"]
            can_start = (
                status == "waiting"
                and is_active
                and viewer_is_host
                and len(players) >= self.MIN_PLAYERS
            )
            can_submit_answer = (
                status == "guessing"
                and is_active
                and not bool(viewer_answer)
                and bool(option_authors)
            )
            can_end_turn = status == "guessing" and is_active and viewer_is_host
            can_next_turn = status == "reveal" and is_active and viewer_is_host

            return {
                "session": {
                    "code": code,
                    "status": status,
                    "is_active": is_active,
                    "ended_reason": self._session_end_message(session),
                    "host_player_id": session["host_player_id"],
                    "turn_number": turn_number,
                    "max_players": self.MAX_PLAYERS,
                    "min_players": self.MIN_PLAYERS,
                },
                "viewer": {
                    "player_id": player_id,
                    "display_name": viewer["display_name"],
                    "is_host": viewer_is_host,
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
                    "source_quote": (
                        source_quote_text if status in {"guessing", "reveal"} else ""
                    ),
                    "option_authors": (
                        option_authors if status in {"guessing", "reveal"} else []
                    ),
                    "correct_author": correct_author if reveal_answers else "",
                    "answered_count": answered_count,
                    "correct_count": correct_count,
                    "total_players": len(players),
                    "waiting_count": max(len(players) - answered_count, 0),
                    "answers": answer_rows_for_ui,
                    "fastest_correct": fastest_correct,
                    "you_answered": bool(viewer_answer),
                    "your_selected_author": (
                        str(viewer_answer["selected_author"] or "")
                        if viewer_answer
                        else ""
                    ),
                    "your_is_correct": (
                        bool(int(viewer_answer["is_correct"] or 0))
                        if viewer_answer
                        else False
                    ),
                    "your_answer_order": (
                        int(viewer_answer["answer_order"] or 0) if viewer_answer else 0
                    ),
                    "your_points_awarded": (
                        int(viewer_answer["points_awarded"] or 0)
                        if viewer_answer
                        else 0
                    ),
                    "can_start": can_start,
                    "can_submit_answer": can_submit_answer,
                    "can_end_turn": can_end_turn,
                    "can_next_turn": can_next_turn,
                    "can_end_game": is_active and viewer_is_host,
                },
            }

    def start_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteWhoSaidItError("Only the host can start the game.", 403)
            if not self._session_is_active(session):
                raise QuoteWhoSaidItError(self._session_end_message(session), 409)
            if session["status"] != "waiting":
                raise QuoteWhoSaidItError("This session already started.", 409)

            players = self._list_players(conn, code)
            if len(players) < self.MIN_PLAYERS:
                raise QuoteWhoSaidItError(
                    f"At least {self.MIN_PLAYERS} players are required to start.", 400
                )

            self._start_turn(conn=conn, session_code=code, turn_number=1)

        return {"ok": True}

    def submit_answer(
        self, session_code: str, player_id: str, selected_author: str
    ) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        selected_author = str(selected_author or "").strip()
        if not code or not player_id:
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)
        if not selected_author:
            raise QuoteWhoSaidItError("selected_author is required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteWhoSaidItError(self._session_end_message(session), 409)
            if session["status"] != "guessing":
                raise QuoteWhoSaidItError("Answering is not open right now.", 409)

            turn_number = int(session["turn_number"] or 0)
            option_authors = self._json_loads_list(session["option_authors"])
            if len(option_authors) != self.OPTIONS_PER_QUESTION:
                raise QuoteWhoSaidItError("This turn has invalid answer options.", 409)

            canonical_selected = self._match_option(selected_author, option_authors)
            if not canonical_selected:
                raise QuoteWhoSaidItError("Pick one of the provided author options.", 400)

            players = self._list_players(conn, code)
            player_ids = {row["player_id"] for row in players}
            if player_id not in player_ids:
                raise QuoteWhoSaidItError("You are not part of this session.", 403)

            existing_answer = conn.execute(
                """
                SELECT player_id, selected_author, is_correct, answer_order, points_awarded
                FROM wsi_answers
                WHERE session_code = ? AND turn_number = ? AND player_id = ?
                """,
                (code, turn_number, player_id),
            ).fetchone()
            if existing_answer:
                return {
                    "ok": True,
                    "already_answered": True,
                    "is_correct": bool(int(existing_answer["is_correct"] or 0)),
                    "answer_order": int(existing_answer["answer_order"] or 0),
                    "points_awarded": int(existing_answer["points_awarded"] or 0),
                }

            correct_author = str(session["correct_author"] or "")
            is_correct = (
                self._normalize_author(canonical_selected)
                == self._normalize_author(correct_author)
            )

            answered_at = int(time.time() * 1000)
            answer_order = 0
            points_awarded = 0

            if is_correct:
                solved_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM wsi_answers
                    WHERE session_code = ? AND turn_number = ? AND is_correct = 1
                    """,
                    (code, turn_number),
                ).fetchone()[0]
                answer_order = int(solved_count) + 1
                player_total = max(len(players), 1)
                points_awarded = max(player_total - answer_order + 1, 1)

            conn.execute(
                """
                INSERT INTO wsi_answers
                (session_code, turn_number, player_id, selected_author, is_correct,
                 answered_at, answer_order, points_awarded, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    turn_number,
                    player_id,
                    canonical_selected,
                    1 if is_correct else 0,
                    answered_at,
                    answer_order,
                    points_awarded,
                    int(time.time()),
                ),
            )

            if is_correct and points_awarded > 0:
                conn.execute(
                    """
                    UPDATE wsi_players
                    SET score = score + ?
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (points_awarded, code, player_id),
                )

            answered_total = conn.execute(
                """
                SELECT COUNT(*)
                FROM wsi_answers
                WHERE session_code = ? AND turn_number = ?
                """,
                (code, turn_number),
            ).fetchone()[0]
            all_answered = int(answered_total) >= len(players)

            now_ts = int(time.time())
            if all_answered:
                conn.execute(
                    """
                    UPDATE wsi_sessions
                    SET status = 'reveal',
                        updated_at = ?
                    WHERE code = ?
                    """,
                    (now_ts, code),
                )
            else:
                conn.execute(
                    "UPDATE wsi_sessions SET updated_at = ? WHERE code = ?",
                    (now_ts, code),
                )

        return {
            "ok": True,
            "is_correct": bool(is_correct),
            "answer_order": answer_order,
            "points_awarded": points_awarded,
            "all_answered": all_answered,
        }

    def end_turn(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteWhoSaidItError(self._session_end_message(session), 409)
            if session["host_player_id"] != player_id:
                raise QuoteWhoSaidItError("Only the host can reveal answers.", 403)
            if session["status"] != "guessing":
                raise QuoteWhoSaidItError("Turn reveal is only available while guessing.", 409)

            conn.execute(
                """
                UPDATE wsi_sessions
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
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if not self._session_is_active(session):
                raise QuoteWhoSaidItError(self._session_end_message(session), 409)
            if session["host_player_id"] != player_id:
                raise QuoteWhoSaidItError("Only the host can start the next turn.", 403)
            if session["status"] != "reveal":
                raise QuoteWhoSaidItError(
                    "Next turn is only available after reveal.", 409
                )

            players = self._list_players(conn, code)
            if len(players) < self.MIN_PLAYERS:
                raise QuoteWhoSaidItError(
                    f"At least {self.MIN_PLAYERS} players are required.", 400
                )

            next_turn_number = int(session["turn_number"] or 0) + 1
            self._start_turn(conn=conn, session_code=code, turn_number=next_turn_number)

        return {"ok": True}

    def end_session(self, session_code: str, player_id: str) -> dict:
        code = self._normalize_code(session_code)
        player_id = self._normalize_player_id(player_id)
        if not code or not player_id:
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._require_session(conn, code)
            if session["host_player_id"] != player_id:
                raise QuoteWhoSaidItError("Only the host can end the game.", 403)
            if not self._session_is_active(session):
                return {"ok": True, "ended": True}

            now_ts = int(time.time())
            conn.execute(
                """
                UPDATE wsi_sessions
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
            raise QuoteWhoSaidItError("Session code and player_id are required.", 400)

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                return {"ok": True, "ended": True}

            conn.execute(
                """
                DELETE FROM wsi_players
                WHERE session_code = ? AND player_id = ?
                """,
                (code, player_id),
            )

            players = self._list_players(conn, code)
            if not players:
                conn.execute("DELETE FROM wsi_answers WHERE session_code = ?", (code,))
                conn.execute("DELETE FROM wsi_sessions WHERE code = ?", (code,))
                return {"ok": True, "ended": True}

            for index, row in enumerate(players, start=1):
                conn.execute(
                    """
                    UPDATE wsi_players
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
                    UPDATE wsi_sessions
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
                CREATE TABLE IF NOT EXISTS wsi_sessions (
                    code TEXT PRIMARY KEY,
                    host_player_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('waiting', 'guessing', 'reveal')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    ended_reason TEXT NOT NULL DEFAULT '',
                    ended_at INTEGER NOT NULL DEFAULT 0,
                    turn_number INTEGER NOT NULL DEFAULT 0,
                    source_quote_id INTEGER NOT NULL DEFAULT 0,
                    source_quote_text TEXT NOT NULL DEFAULT '',
                    correct_author TEXT NOT NULL DEFAULT '',
                    option_authors TEXT NOT NULL DEFAULT '[]',
                    used_quote_ids TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wsi_players (
                    session_code TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    seat INTEGER NOT NULL,
                    joined_at INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_code, player_id),
                    FOREIGN KEY (session_code) REFERENCES wsi_sessions(code) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wsi_answers (
                    session_code TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    selected_author TEXT NOT NULL DEFAULT '',
                    is_correct INTEGER NOT NULL DEFAULT 0,
                    answered_at INTEGER NOT NULL DEFAULT 0,
                    answer_order INTEGER NOT NULL DEFAULT 0,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (session_code, turn_number, player_id),
                    FOREIGN KEY (session_code) REFERENCES wsi_sessions(code) ON DELETE CASCADE,
                    FOREIGN KEY (session_code, player_id)
                        REFERENCES wsi_players(session_code, player_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wsi_players_session_seat ON wsi_players(session_code, seat)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wsi_answers_turn ON wsi_answers(session_code, turn_number)"
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
                "DELETE FROM wsi_sessions WHERE updated_at < ?",
                (cutoff_ts,),
            )

    def _start_turn(
        self,
        *,
        conn: sqlite3.Connection,
        session_code: str,
        turn_number: int,
    ) -> None:
        session = self._require_session(conn, session_code)
        turn_payload = self._pick_turn_question(conn=conn, session=session)
        now_ts = int(time.time())

        conn.execute("DELETE FROM wsi_answers WHERE session_code = ?", (session_code,))
        conn.execute(
            """
            UPDATE wsi_sessions
            SET status = 'guessing',
                is_active = 1,
                ended_reason = '',
                ended_at = 0,
                turn_number = ?,
                source_quote_id = ?,
                source_quote_text = ?,
                correct_author = ?,
                option_authors = ?,
                used_quote_ids = ?,
                updated_at = ?
            WHERE code = ?
            """,
            (
                turn_number,
                int(turn_payload["source_quote_id"]),
                str(turn_payload["source_quote"]),
                str(turn_payload["correct_author"]),
                json.dumps(turn_payload["option_authors"], ensure_ascii=False),
                json.dumps(turn_payload["used_quote_ids"], ensure_ascii=False),
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
        conn.execute("DELETE FROM wsi_answers WHERE session_code = ?", (session_code,))
        conn.execute(
            """
            UPDATE wsi_sessions
            SET host_player_id = ?,
                status = 'waiting',
                source_quote_id = 0,
                source_quote_text = '',
                correct_author = '',
                option_authors = '[]',
                ended_reason = ?,
                ended_at = 0,
                updated_at = ?
            WHERE code = ?
            """,
            (host_player_id, reason, int(time.time()), session_code),
        )

    def _pick_turn_question(
        self, *, conn: sqlite3.Connection, session: sqlite3.Row
    ) -> dict:
        quotes = list(self.quote_store.get_all_quotes())
        author_pool = self._collect_author_pool(quotes)
        if len(author_pool) < self.OPTIONS_PER_QUESTION:
            raise QuoteWhoSaidItError(
                "Need at least four distinct authors to run this game.", 409
            )

        eligible_quotes = self._build_eligible_quotes(quotes, author_pool)
        if not eligible_quotes:
            raise QuoteWhoSaidItError("No playable quotes are available yet.", 409)

        used_ids = set(self._json_loads_ints(session["used_quote_ids"]))
        fresh_quotes = [quote for quote in eligible_quotes if quote["id"] not in used_ids]
        quote_pool = fresh_quotes or eligible_quotes
        selected_quote = random.choice(quote_pool)

        correct_author = random.choice(selected_quote["authors"])
        decoy_pool = [
            author
            for author in author_pool
            if self._normalize_author(author) != self._normalize_author(correct_author)
        ]
        if len(decoy_pool) < self.OPTIONS_PER_QUESTION - 1:
            raise QuoteWhoSaidItError(
                "Not enough decoy authors are available for this round.", 409
            )

        decoys = random.sample(decoy_pool, self.OPTIONS_PER_QUESTION - 1)
        option_authors = [correct_author, *decoys]
        random.shuffle(option_authors)

        if fresh_quotes:
            next_used = [*used_ids, selected_quote["id"]]
        else:
            next_used = [selected_quote["id"]]
        next_used = [int(item) for item in next_used][-200:]

        return {
            "source_quote_id": selected_quote["id"],
            "source_quote": selected_quote["quote"],
            "correct_author": correct_author,
            "option_authors": option_authors,
            "used_quote_ids": next_used,
        }

    def _build_eligible_quotes(self, quotes: list, author_pool: list[str]) -> list[dict]:
        eligible = []
        for quote in quotes:
            quote_id = int(getattr(quote, "id", 0) or 0)
            quote_text = str(getattr(quote, "quote", "") or "").strip()
            if not quote_text:
                continue

            quote_authors = self._sanitize_authors(getattr(quote, "authors", []))
            if len(quote_authors) != 1:
                continue

            correct_author = quote_authors[0]
            has_valid_decoys = (
                sum(
                    1
                    for author in author_pool
                    if self._normalize_author(author)
                    != self._normalize_author(correct_author)
                )
                >= self.OPTIONS_PER_QUESTION - 1
            )
            if not has_valid_decoys:
                continue

            eligible.append(
                {
                    "id": quote_id,
                    "quote": quote_text,
                    "authors": quote_authors,
                }
            )
        return eligible

    def _collect_author_pool(self, quotes: list) -> list[str]:
        pool = []
        seen = set()
        for quote in quotes:
            quote_authors = self._sanitize_authors(getattr(quote, "authors", []))
            if len(quote_authors) != 1:
                continue
            author = quote_authors[0]
            normalized = self._normalize_author(author)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            pool.append(author)
        return pool

    @staticmethod
    def _sanitize_authors(raw_authors) -> list[str]:
        if isinstance(raw_authors, str):
            candidates = re.split(r",| and ", raw_authors)
        elif isinstance(raw_authors, (list, tuple, set)):
            candidates = list(raw_authors)
        else:
            candidates = []

        output = []
        seen = set()
        for raw_author in candidates:
            collapsed = re.sub(r"\s+", " ", str(raw_author or "")).strip()
            if not collapsed:
                continue
            normalized = re.sub(r"[^a-z0-9]", "", collapsed.lower())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(collapsed[:48])
        return output

    @staticmethod
    def _match_option(selected_author: str, option_authors: list[str]) -> str:
        normalized_selected = re.sub(r"[^a-z0-9]", "", selected_author.lower())
        if not normalized_selected:
            return ""
        for option in option_authors:
            option_normalized = re.sub(r"[^a-z0-9]", "", str(option).lower())
            if option_normalized == normalized_selected:
                return str(option)
        return ""

    @staticmethod
    def _normalize_author(author_name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(author_name or "").lower())

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
            FROM wsi_players
            WHERE session_code = ?
            ORDER BY seat ASC, joined_at ASC
            """,
            (session_code,),
        ).fetchall()

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
                source_quote_id,
                source_quote_text,
                correct_author,
                option_authors,
                used_quote_ids,
                created_at,
                updated_at
            FROM wsi_sessions
            WHERE code = ?
            """,
            (session_code,),
        ).fetchone()

    def _require_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row:
        session = self._get_session(conn, session_code)
        if not session:
            raise QuoteWhoSaidItError("Session not found.", 404)
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
