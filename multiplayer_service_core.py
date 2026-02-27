from __future__ import annotations

import json
import random
import re
import secrets
import sqlite3
import time
from typing import Callable


class MultiplayerServiceCore:
    """Shared room/session plumbing for multiplayer game services."""

    GAME_NAME = ""
    MAX_PLAYERS = 0
    STALE_SESSION_SECONDS = 12 * 60 * 60
    CREATE_SESSION_CODE_ATTEMPTS = 24

    SESSION_TABLE = ""
    PLAYER_TABLE = ""
    ERROR_CLASS = RuntimeError

    def __init__(self, *, db_path: str):
        self.db_path = str(db_path)

    def _raise_error(self, message: str, status_code: int = 400) -> None:
        raise self.ERROR_CLASS(message, status_code)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _cleanup_stale_sessions(self) -> None:
        if not self.SESSION_TABLE:
            return
        cutoff_ts = int(time.time()) - self.STALE_SESSION_SECONDS
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM {self.SESSION_TABLE} WHERE updated_at < ?",
                (cutoff_ts,),
            )

    def _create_session_identity(
        self,
        *,
        player_name: str,
        insert_session: Callable[[sqlite3.Connection, str, str, int], None],
    ) -> tuple[str, str, str]:
        self._cleanup_stale_sessions()
        display_name = self._sanitize_player_name(player_name)
        player_id = self._new_player_id()
        now_ts = int(time.time())
        code = ""

        with self._connect() as conn:
            for _ in range(self.CREATE_SESSION_CODE_ATTEMPTS):
                code = self._new_session_code()
                try:
                    insert_session(conn, code, player_id, now_ts)
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                self._raise_error("Unable to create a session code right now.", 503)

            conn.execute(
                f"""
                INSERT INTO {self.PLAYER_TABLE}
                (session_code, player_id, display_name, seat, joined_at, score)
                VALUES (?, ?, ?, 1, ?, 0)
                """,
                (code, player_id, display_name, now_ts),
            )

        return code, player_id, display_name

    def _join_session_identity(
        self,
        *,
        session_code: str,
        player_name: str,
        player_id: str | None,
        waiting_status: str = "waiting",
        session_code_required_message: str = "Session code is required.",
        started_message: str = "This session already started. Try another code.",
        session_full_message: str | None = None,
        identity_conflict_message: str = "Unable to join with this player identity.",
    ) -> tuple[str, str, str, sqlite3.Row]:
        self._cleanup_stale_sessions()

        code = self._normalize_code(session_code)
        if not code:
            self._raise_error(session_code_required_message, 400)

        display_name = self._sanitize_player_name(player_name)
        requested_player_id = self._normalize_player_id(player_id)
        now_ts = int(time.time())

        full_message = (
            session_full_message
            or f"Session is full ({self.MAX_PLAYERS} players max)."
        )

        with self._connect() as conn:
            session = self._get_session(conn, code)
            if not session:
                self._raise_error("Session not found.", 404)

            if requested_player_id:
                existing = conn.execute(
                    f"""
                    SELECT session_code, player_id, display_name
                    FROM {self.PLAYER_TABLE}
                    WHERE session_code = ? AND player_id = ?
                    """,
                    (code, requested_player_id),
                ).fetchone()
                if existing:
                    if display_name and existing["display_name"] != display_name:
                        conn.execute(
                            f"""
                            UPDATE {self.PLAYER_TABLE}
                            SET display_name = ?
                            WHERE session_code = ? AND player_id = ?
                            """,
                            (display_name, code, requested_player_id),
                        )
                    conn.execute(
                        f"UPDATE {self.SESSION_TABLE} SET updated_at = ? WHERE code = ?",
                        (now_ts, code),
                    )
                    return (
                        code,
                        requested_player_id,
                        display_name or existing["display_name"],
                        session,
                    )

            if not self._session_is_active(session):
                self._raise_error(self._session_end_message(session), 409)
            if session["status"] != waiting_status:
                self._raise_error(started_message, 409)

            current_players = self._list_players(conn, code)
            if len(current_players) >= self.MAX_PLAYERS:
                self._raise_error(full_message, 409)

            new_player_id = requested_player_id or self._new_player_id()
            seat = max([int(player["seat"]) for player in current_players] + [0]) + 1
            try:
                conn.execute(
                    f"""
                    INSERT INTO {self.PLAYER_TABLE}
                    (session_code, player_id, display_name, seat, joined_at, score)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (code, new_player_id, display_name, seat, now_ts),
                )
            except sqlite3.IntegrityError as exc:
                raise self.ERROR_CLASS(identity_conflict_message, 409) from exc

            conn.execute(
                f"UPDATE {self.SESSION_TABLE} SET updated_at = ? WHERE code = ?",
                (now_ts, code),
            )

        return code, new_player_id, display_name, session

    def _list_players(
        self, conn: sqlite3.Connection, session_code: str
    ) -> list[sqlite3.Row]:
        return conn.execute(
            f"""
            SELECT session_code, player_id, display_name, seat, joined_at, score
            FROM {self.PLAYER_TABLE}
            WHERE session_code = ?
            ORDER BY seat ASC, joined_at ASC
            """,
            (session_code,),
        ).fetchall()

    def _require_session(
        self, conn: sqlite3.Connection, session_code: str
    ) -> sqlite3.Row:
        session = self._get_session(conn, session_code)
        if not session:
            self._raise_error("Session not found.", 404)
        return session

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

        out: list[int] = []
        for item in payload:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out

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
