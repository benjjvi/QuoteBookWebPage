from __future__ import annotations

import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time as timelib
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from statistics import median
from urllib.parse import urljoin

from flask import abort, g, request, session
from pywebpush import WebPushException, webpush

from stats_stopwords import STOPWORDS


class MaxSizeFileHandler(logging.FileHandler):
    def __init__(self, filename: str, max_bytes: int, **kwargs):
        self.max_bytes = max_bytes
        super().__init__(filename, **kwargs)

    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename):
                if os.path.getsize(self.baseFilename) >= self.max_bytes:
                    return
            super().emit(record)
        except Exception:
            self.handleError(record)


@dataclass(frozen=True)
class AppServiceConfig:
    public_base_url: str
    vapid_public_key: str
    vapid_private_key: str
    vapid_email: str
    weekly_email_enabled: bool
    weekly_email_to_seed: list[str]
    weekly_email_from: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    is_prod: bool
    weekly_scheduler_mode: str = "auto"
    weekly_digest_sponsor_line: str = ""
    smtp_send_delay_seconds: float = 0.0


class AppServices:
    SOCIAL_COMMENT_NAME_MAX = 40
    SOCIAL_COMMENT_TEXT_MAX = 280
    SOCIAL_REACTIONS = (
        {"key": "thumbs_up", "emoji": "👍", "label": "Thumbs up"},
        {"key": "thumbs_down", "emoji": "👎", "label": "Thumbs down"},
        {"key": "heart", "emoji": "❤️", "label": "Heart"},
        {"key": "laugh_cry", "emoji": "😂", "label": "Crying with laughter"},
        {"key": "sob", "emoji": "😭", "label": "Sobbing"},
        {"key": "anger", "emoji": "😡", "label": "Anger"},
    )
    SOCIAL_REACTION_KEYS = {item["key"] for item in SOCIAL_REACTIONS}

    def __init__(self, app, quote_store, ai_worker, uk_tz, config: AppServiceConfig):
        self.app = app
        self.quote_store = quote_store
        self.ai_worker = ai_worker
        self.uk_tz = uk_tz
        self.config = config

        self.stats_cache_lock = threading.Lock()
        self.stats_cache_snapshot: dict | None = None

        self._weekly_scheduler_thread: threading.Thread | None = None
        self._weekly_scheduler_lock = threading.Lock()
        self._opportunistic_scheduler_lock = threading.Lock()
        self._opportunistic_last_check_at = 0.0
        self._scheduler_mode_cache: str | None = None
        self._external_scheduler_notice_emitted = False
        self.metrics_lock = threading.Lock()
        self.runtime_metrics: dict[str, int] = {
            "push_attempted": 0,
            "push_sent": 0,
            "push_failed": 0,
            "push_pruned": 0,
            "email_attempted": 0,
            "email_sent": 0,
            "email_failed": 0,
            "weekly_digest_sent": 0,
            "weekly_digest_failure": 0,
            "weekly_digest_skipped_unconfigured": 0,
            "weekly_digest_skipped_not_due": 0,
            "weekly_digest_claim_conflict": 0,
            "weekly_scheduler_loop_errors": 0,
            "rate_limited": 0,
        }
        self.runtime_status: dict[str, str] = {
            "push_last_error": "",
            "email_last_error": "",
        }
        self.opportunistic_scheduler_interval_seconds = 300
        self._rate_limit_lock = threading.Lock()
        self._rate_limit_hits: dict[str, deque[float]] = {}

    # ------------------------
    # Runtime validation + metrics
    # ------------------------

    def validate_runtime_config(self) -> list[str]:
        warnings: list[str] = []
        if self.config.public_base_url and not re.match(
            r"^https?://", self.config.public_base_url, re.IGNORECASE
        ):
            warnings.append("PUBLIC_BASE_URL should start with http:// or https://.")

        if bool(self.config.vapid_public_key) ^ bool(self.config.vapid_private_key):
            warnings.append(
                "VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY must both be set to enable push."
            )

        if self.config.weekly_email_enabled and not self.config.smtp_host:
            warnings.append(
                "WEEKLY_EMAIL_ENABLED is true but SMTP_HOST is not configured."
            )

        if self.config.weekly_email_enabled and not (
            self.config.weekly_email_from or self.config.smtp_user
        ):
            warnings.append(
                "Set WEEKLY_EMAIL_FROM or SMTP_USER when weekly email is enabled."
            )

        if self.config.smtp_use_ssl and self.config.smtp_use_tls:
            warnings.append(
                "SMTP_USE_SSL and SMTP_USE_TLS are both enabled; SSL takes precedence."
            )

        if self.config.smtp_send_delay_seconds < 0:
            warnings.append(
                "SMTP_SEND_DELAY_SECONDS should be 0 or greater."
            )

        mode = (self.config.weekly_scheduler_mode or "auto").strip().lower()
        if mode not in {"auto", "thread", "external"}:
            warnings.append(
                "WEEKLY_SCHEDULER_MODE should be one of: auto, thread, external."
            )

        if self._looks_like_pythonanywhere() and self.resolve_weekly_scheduler_mode() == "thread":
            warnings.append(
                "PythonAnywhere detected; prefer WEEKLY_SCHEDULER_MODE=external and run digest via scheduled task."
            )

        if warnings:
            for warning in warnings:
                self.app.logger.warning("Config warning: %s", warning)
        else:
            self.app.logger.info("Runtime configuration checks passed.")
        return warnings

    def _increment_metric(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self.metrics_lock:
            self.runtime_metrics[name] = self.runtime_metrics.get(name, 0) + amount

    def _set_runtime_status(self, key: str, value: str) -> None:
        text = (value or "").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        with self.metrics_lock:
            self.runtime_status[key] = text

    @staticmethod
    def _looks_like_pythonanywhere() -> bool:
        if any(
            os.getenv(name)
            for name in (
                "PYTHONANYWHERE_SITE",
                "PYTHONANYWHERE_DOMAIN",
                "PYTHONANYWHERE_USERNAME",
                "PA_SITE",
            )
        ):
            return True
        hostname = (os.getenv("HOSTNAME") or "").strip().lower()
        return "pythonanywhere" in hostname

    def resolve_weekly_scheduler_mode(self) -> str:
        if self._scheduler_mode_cache is not None:
            return self._scheduler_mode_cache

        raw_mode = (self.config.weekly_scheduler_mode or "auto").strip().lower()
        if raw_mode not in {"auto", "thread", "external"}:
            raw_mode = "auto"
        if raw_mode == "auto":
            mode = "external" if self._looks_like_pythonanywhere() else "thread"
        else:
            mode = raw_mode
        self._scheduler_mode_cache = mode
        return mode

    def get_runtime_metrics(self) -> dict:
        with self.metrics_lock:
            snapshot = dict(self.runtime_metrics)
            snapshot.update(self.runtime_status)
        snapshot["weekly_scheduler_thread_alive"] = (
            self._weekly_scheduler_thread.is_alive()
            if self._weekly_scheduler_thread is not None
            else False
        )
        snapshot["weekly_email_enabled"] = bool(self.config.weekly_email_enabled)
        snapshot["weekly_scheduler_mode"] = self.resolve_weekly_scheduler_mode()
        return snapshot

    # ------------------------
    # Formatting helpers
    # ------------------------

    def to_uk_datetime(self, ts):
        dt = datetime.fromtimestamp(ts, tz=self.uk_tz)
        day = dt.day
        suffix = (
            "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        )
        return {
            "date": f"{day}{suffix} {dt.strftime('%B')}",
            "time": dt.strftime("%H:%M"),
        }

    def uk_date(self, epoch):
        return datetime.fromtimestamp(epoch, self.uk_tz).strftime("%d %B %Y")

    def uk_time(self, epoch):
        return datetime.fromtimestamp(epoch, self.uk_tz).strftime("%H:%M")

    @staticmethod
    def month_name(month: int) -> str:
        try:
            return datetime(2000, int(month), 1).strftime("%B")
        except (TypeError, ValueError):
            return ""

    def _configured_public_base_url(self) -> str:
        return (self.config.public_base_url or "").strip()

    def build_public_url(self, path: str) -> str:
        base = self._configured_public_base_url()
        if not base:
            try:
                base = request.url_root
            except RuntimeError:
                base = ""
        if not base:
            return path
        if not base.endswith("/"):
            base = f"{base}/"
        return urljoin(base, path.lstrip("/"))

    @staticmethod
    def quote_to_dict(quote) -> dict:
        return {
            "id": quote.id,
            "quote": quote.quote,
            "authors": quote.authors,
            "timestamp": quote.timestamp,
            "context": quote.context,
            "tags": list(getattr(quote, "tags", []) or []),
            "stats": getattr(quote, "stats", {}),
        }

    # ------------------------
    # Session tokens
    # ------------------------

    @staticmethod
    def _session_token(key: str) -> str:
        token = session.get(key)
        if not token:
            token = secrets.token_urlsafe(24)
            session[key] = token
        return token

    def get_push_subscribe_token(self) -> str:
        return self._session_token("push_subscribe_token")

    def get_email_subscribe_token(self) -> str:
        return self._session_token("email_subscribe_token")

    def get_ai_request_token(self) -> str:
        return self._session_token("ai_request_token")

    def get_csrf_token(self) -> str:
        return self._session_token("csrf_token")

    def validate_csrf_token(self, token: str) -> bool:
        candidate = (token or "").strip()
        expected = (session.get("csrf_token") or "").strip()
        if not candidate or not expected:
            return False
        return secrets.compare_digest(candidate, expected)

    @staticmethod
    def get_request_client_ip() -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return first
        real_ip = (request.headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        return (request.remote_addr or "unknown").strip() or "unknown"

    def consume_rate_limit(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now_ts: float | None = None,
    ) -> tuple[bool, int]:
        if limit <= 0 or window_seconds <= 0:
            return True, 0

        now = float(now_ts if now_ts is not None else timelib.time())
        min_allowed = now - float(window_seconds)

        with self._rate_limit_lock:
            bucket = self._rate_limit_hits.setdefault(key, deque())
            while bucket and bucket[0] < min_allowed:
                bucket.popleft()

            if len(bucket) >= int(limit):
                retry_after = max(1, int(bucket[0] + float(window_seconds) - now))
                self._increment_metric("rate_limited", 1)
                return False, retry_after

            bucket.append(now)

            if len(self._rate_limit_hits) > 5000:
                stale_keys = [name for name, values in self._rate_limit_hits.items() if not values]
                for stale_key in stale_keys:
                    self._rate_limit_hits.pop(stale_key, None)

        return True, 0

    # ------------------------
    # DB helpers
    # ------------------------

    def get_push_db_path(self) -> str:
        if getattr(self.quote_store, "_local", None):
            return self.quote_store._local.filepath
        return os.getenv("QUOTEBOOK_DB", "qb.db")

    def get_social_reaction_catalog(self) -> list[dict]:
        return [dict(item) for item in self.SOCIAL_REACTIONS]

    def ensure_social_tables(self) -> bool:
        db_path = self.get_push_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS social_post_reactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        quote_id INTEGER NOT NULL,
                        device_id TEXT NOT NULL,
                        reaction_type TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        UNIQUE (quote_id, device_id)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_social_post_reactions_quote_expires
                    ON social_post_reactions (quote_id, expires_at)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS social_post_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        quote_id INTEGER NOT NULL,
                        display_name TEXT NOT NULL,
                        comment_text TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_social_post_comments_quote_created
                    ON social_post_comments (quote_id, created_at, id)
                    """
                )
            return True
        except sqlite3.Error as exc:
            self.app.logger.error("Unable to ensure social tables: %s", exc)
            return False

    def record_social_reaction(
        self,
        *,
        quote_id: int,
        device_id: str,
        reaction_type: str,
        now_ts: int | None = None,
    ) -> bool:
        normalized_device_id = (device_id or "").strip()
        normalized_reaction = (reaction_type or "").strip()
        if (
            quote_id <= 0
            or not normalized_device_id
            or normalized_reaction not in self.SOCIAL_REACTION_KEYS
        ):
            return False
        if len(normalized_device_id) > 255:
            return False
        if not self.ensure_social_tables():
            return False

        now_ts = int(now_ts if now_ts is not None else timelib.time())

        with sqlite3.connect(self.get_push_db_path()) as conn:
            existing = conn.execute(
                """
                SELECT reaction_type
                FROM social_post_reactions
                WHERE quote_id = ? AND device_id = ?
                """,
                (int(quote_id), normalized_device_id),
            ).fetchone()
            if existing and str(existing[0]) == normalized_reaction:
                conn.execute(
                    """
                    DELETE FROM social_post_reactions
                    WHERE quote_id = ? AND device_id = ?
                    """,
                    (int(quote_id), normalized_device_id),
                )
                return True

            conn.execute(
                """
                INSERT INTO social_post_reactions
                (quote_id, device_id, reaction_type, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (quote_id, device_id)
                DO UPDATE SET
                    reaction_type = excluded.reaction_type,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    int(quote_id),
                    normalized_device_id,
                    normalized_reaction,
                    now_ts,
                    now_ts,
                    0,
                ),
            )
        return True

    def get_social_reactions_for_quote(
        self,
        *,
        quote_id: int,
        device_id: str = "",
        now_ts: int | None = None,
    ) -> dict:
        counts = {item["key"]: 0 for item in self.SOCIAL_REACTIONS}
        payload = {
            "counts": counts,
            "total": 0,
            "user_reaction": "",
        }
        if quote_id <= 0:
            return payload
        if not self.ensure_social_tables():
            return payload

        normalized_device_id = (device_id or "").strip()
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT reaction_type, COUNT(*) AS reaction_count
                FROM social_post_reactions
                WHERE quote_id = ?
                GROUP BY reaction_type
                """,
                (int(quote_id),),
            ).fetchall()
            for row in rows:
                key = str(row["reaction_type"])
                if key in counts:
                    counts[key] = int(row["reaction_count"])

            if normalized_device_id:
                row = conn.execute(
                    """
                    SELECT reaction_type
                    FROM social_post_reactions
                    WHERE quote_id = ? AND device_id = ?
                    """,
                    (int(quote_id), normalized_device_id),
                ).fetchone()
                if row:
                    payload["user_reaction"] = str(row["reaction_type"])

        payload["total"] = sum(counts.values())
        return payload

    def add_social_comment(
        self,
        *,
        quote_id: int,
        display_name: str,
        comment_text: str,
        now_ts: int | None = None,
    ) -> bool:
        if quote_id <= 0:
            return False
        if not self.ensure_social_tables():
            return False

        normalized_name = " ".join((display_name or "").split()).strip()
        normalized_comment = (comment_text or "").strip()
        if (
            not normalized_name
            or not normalized_comment
            or len(normalized_name) > self.SOCIAL_COMMENT_NAME_MAX
            or len(normalized_comment) > self.SOCIAL_COMMENT_TEXT_MAX
        ):
            return False

        now_ts = int(now_ts if now_ts is not None else timelib.time())
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO social_post_comments
                (quote_id, display_name, comment_text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(quote_id), normalized_name, normalized_comment, now_ts),
            )
        return True

    def get_social_comments_for_quote(self, *, quote_id: int, limit: int = 200) -> list[dict]:
        if quote_id <= 0:
            return []
        if not self.ensure_social_tables():
            return []

        cap = max(1, min(int(limit), 500))
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, quote_id, display_name, comment_text, created_at
                FROM social_post_comments
                WHERE quote_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (int(quote_id), cap),
            ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "quote_id": int(row["quote_id"]),
                "display_name": str(row["display_name"]),
                "comment_text": str(row["comment_text"]),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def ensure_scheduler_table(self) -> bool:
        db_path = self.get_push_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                        job_name TEXT NOT NULL,
                        run_key TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY (job_name, run_key)
                    )
                    """
                )
                row = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'scheduled_job_runs'
                    """
                ).fetchone()
            return bool(row)
        except sqlite3.Error as exc:
            self.app.logger.error("Unable to ensure scheduled_job_runs table: %s", exc)
            return False

    def ensure_weekly_email_recipients_table(self) -> bool:
        db_path = self.get_push_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS weekly_email_recipients (
                        email TEXT PRIMARY KEY,
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                row = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'weekly_email_recipients'
                    """
                ).fetchone()
            return bool(row)
        except sqlite3.Error as exc:
            self.app.logger.error(
                "Unable to ensure weekly_email_recipients table: %s", exc
            )
            return False

    def ensure_weekly_digest_archive_table(self) -> bool:
        db_path = self.get_push_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS weekly_email_digest_archive (
                        run_key TEXT PRIMARY KEY,
                        subject TEXT NOT NULL,
                        body TEXT NOT NULL,
                        sent_at INTEGER NOT NULL,
                        recipient_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_weekly_email_digest_archive_sent_at
                    ON weekly_email_digest_archive (sent_at DESC)
                    """
                )
                row = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'weekly_email_digest_archive'
                    """
                ).fetchone()
            return bool(row)
        except sqlite3.Error as exc:
            self.app.logger.error(
                "Unable to ensure weekly_email_digest_archive table: %s", exc
            )
            return False

    def seed_weekly_email_recipients_from_env(self) -> None:
        if not self.config.weekly_email_to_seed:
            return
        if not self.ensure_weekly_email_recipients_table():
            self.app.logger.error(
                "Skipping weekly email recipient seed: recipients table missing."
            )
            return
        with sqlite3.connect(self.get_push_db_path()) as conn:
            existing_count = conn.execute(
                "SELECT COUNT(*) FROM weekly_email_recipients"
            ).fetchone()[0]
            if existing_count > 0:
                return
            now = int(timelib.time())
            conn.executemany(
                """
                INSERT OR IGNORE INTO weekly_email_recipients (email, created_at)
                VALUES (?, ?)
                """,
                [(email, now) for email in self.config.weekly_email_to_seed],
            )
        self.app.logger.info(
            "Seeded %s weekly email recipient(s) from environment.",
            len(self.config.weekly_email_to_seed),
        )

    def get_weekly_email_recipients(self) -> list[str]:
        if not self.ensure_weekly_email_recipients_table():
            return []
        with sqlite3.connect(self.get_push_db_path()) as conn:
            rows = conn.execute(
                """
                SELECT email
                FROM weekly_email_recipients
                ORDER BY created_at ASC, email ASC
                """
            ).fetchall()
        return [row[0].strip() for row in rows if row and row[0] and row[0].strip()]

    @staticmethod
    def is_valid_email_address(email: str) -> bool:
        if not email:
            return False
        pattern = re.compile(
            r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE
        )
        return bool(pattern.match(email))

    def add_weekly_email_recipient(self, email: str) -> bool:
        normalized = (email or "").strip().lower()
        if not self.is_valid_email_address(normalized):
            return False
        if not self.ensure_weekly_email_recipients_table():
            self.app.logger.error(
                "Cannot add weekly email recipient: recipients table missing."
            )
            return False
        now = int(timelib.time())
        with sqlite3.connect(self.get_push_db_path()) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO weekly_email_recipients (email, created_at)
                VALUES (?, ?)
                """,
                (normalized, now),
            )
        # Scheduler may not have started at boot if recipients were added later.
        self.start_weekly_email_scheduler()
        return cur.rowcount > 0

    def remove_weekly_email_recipient(self, email: str) -> bool:
        normalized = (email or "").strip().lower()
        if not self.is_valid_email_address(normalized):
            return False
        if not self.ensure_weekly_email_recipients_table():
            self.app.logger.error(
                "Cannot remove weekly email recipient: recipients table missing."
            )
            return False
        with sqlite3.connect(self.get_push_db_path()) as conn:
            cur = conn.execute(
                "DELETE FROM weekly_email_recipients WHERE email = ?",
                (normalized,),
            )
        return cur.rowcount > 0

    def is_weekly_email_recipient(self, email: str) -> bool:
        normalized = (email or "").strip().lower()
        if not self.is_valid_email_address(normalized):
            return False
        if not self.ensure_weekly_email_recipients_table():
            return False
        with sqlite3.connect(self.get_push_db_path()) as conn:
            row = conn.execute(
                "SELECT 1 FROM weekly_email_recipients WHERE email = ?",
                (normalized,),
            ).fetchone()
        return bool(row)

    def archive_weekly_digest(
        self,
        *,
        run_key: str,
        subject: str,
        body: str,
        sent_at: int,
        recipient_count: int,
    ) -> bool:
        if not self.ensure_weekly_digest_archive_table():
            return False
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_email_digest_archive
                (run_key, subject, body, sent_at, recipient_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_key, subject, body, int(sent_at), max(int(recipient_count), 0)),
            )
        return True

    def get_weekly_digest_archive(self, limit: int = 10) -> list[dict]:
        if not self.ensure_weekly_digest_archive_table():
            return []
        cap = max(1, min(int(limit), 50))
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_key, subject, body, sent_at, recipient_count
                FROM weekly_email_digest_archive
                ORDER BY sent_at DESC, run_key DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
        return [
            {
                "run_key": str(row["run_key"]),
                "subject": str(row["subject"]),
                "body": str(row["body"]),
                "sent_at": int(row["sent_at"]),
                "recipient_count": int(row["recipient_count"]),
            }
            for row in rows
        ]

    def get_mailbox_public_digest(self) -> dict | None:
        # Delay public mailbox by one send cycle.
        archive = self.get_weekly_digest_archive(limit=2)
        if len(archive) < 2:
            return None
        return archive[1]

    def claim_scheduled_run(self, job_name: str, run_key: str) -> bool:
        if not self.ensure_scheduler_table():
            self.app.logger.error(
                "Cannot claim scheduled run: scheduled_job_runs table missing."
            )
            return False
        try:
            with sqlite3.connect(self.get_push_db_path()) as conn:
                conn.execute(
                    """
                    INSERT INTO scheduled_job_runs (job_name, run_key, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (job_name, run_key, int(timelib.time())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_scheduled_run(self, job_name: str, run_key: str) -> None:
        if not self.ensure_scheduler_table():
            self.app.logger.error(
                "Cannot release scheduled run: scheduled_job_runs table missing."
            )
            return
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.execute(
                "DELETE FROM scheduled_job_runs WHERE job_name = ? AND run_key = ?",
                (job_name, run_key),
            )

    # ------------------------
    # Weekly digest + email
    # ------------------------

    def weekly_email_is_configured(self) -> bool:
        return bool(
            self.config.weekly_email_enabled
            and self.config.smtp_host
            and self.get_weekly_email_recipients()
        )

    def _digest_quote_payload(self, quote) -> dict:
        quote_time = datetime.fromtimestamp(quote.timestamp, tz=self.uk_tz).strftime(
            "%Y-%m-%d %H:%M"
        )
        return {
            "id": quote.id,
            "timestamp_uk": quote_time,
            "authors": quote.authors or [],
            "quote": quote.quote,
            "context": quote.context or "",
            "stats": getattr(quote, "stats", {}),
        }

    def _append_digest_sponsor(self, body: str) -> str:
        sponsor_line = (self.config.weekly_digest_sponsor_line or "").strip()
        if not sponsor_line:
            return body
        if not body.strip():
            return sponsor_line
        return f"{body}\n\n{sponsor_line}"

    def _append_digest_unsubscribe_footer(self, body: str) -> str:
        unsubscribe_url = self.build_public_url("/unsubscribe")
        footer = f"Unsubscribe from weekly digest emails: {unsubscribe_url}"
        if not body.strip():
            return footer
        return f"{body}\n\n---\n{footer}"

    def build_weekly_digest_email(self, now_uk: datetime) -> tuple[str, str]:
        start_uk = now_uk - timedelta(days=7)
        weekly_quotes = self.quote_store.get_quotes_between(
            int(start_uk.timestamp()),
            int(now_uk.timestamp()),
        )
        weekly_quotes = sorted(
            weekly_quotes, key=lambda q: (q.timestamp, q.id), reverse=True
        )
        all_quotes = sorted(
            self.quote_store.get_all_quotes(),
            key=lambda q: (q.timestamp, q.id),
            reverse=True,
        )

        top_authors = Counter(
            author.strip()
            for q in weekly_quotes
            for author in q.authors
            if isinstance(author, str) and author.strip()
        ).most_common(5)
        all_time_top_authors = [
            {"name": speaker, "count": count}
            for speaker, count in self.quote_store.get_speaker_counts()[:8]
        ]

        weekly_leagues: list[dict] = []
        funny_rankings: list[tuple] = []
        if weekly_quotes:
            funny_rankings = sorted(
                [
                    (
                        q,
                        self.ai_worker.classify_funny_score(
                            q.quote, q.authors, q.stats
                        ),
                    )
                    for q in weekly_quotes
                ],
                key=lambda item: item[1],
                reverse=True,
            )
            if funny_rankings:
                winner_quote, winner_score = funny_rankings[0]
                weekly_leagues.append(
                    {
                        "name": "Funniest Quote League",
                        "winner": {
                            "quote_id": winner_quote.id,
                            "authors": winner_quote.authors,
                            "score": round(winner_score, 2),
                        },
                        "contenders": [
                            {
                                "quote_id": q.id,
                                "authors": q.authors,
                                "score": round(score, 2),
                            }
                            for q, score in funny_rankings[:3]
                        ],
                    }
                )

            speaker_counter = Counter(
                author.strip()
                for q in weekly_quotes
                for author in q.authors
                if isinstance(author, str) and author.strip()
            )
            if speaker_counter:
                speaker, count = speaker_counter.most_common(1)[0]
                weekly_leagues.append(
                    {
                        "name": "Most Prolific Speaker League",
                        "winner": {"name": speaker, "count": count},
                        "contenders": [
                            {"name": n, "count": c}
                            for n, c in speaker_counter.most_common(3)
                        ],
                    }
                )

            duo_counter = Counter()
            for q in weekly_quotes:
                names = sorted(
                    [a.strip() for a in q.authors if isinstance(a, str) and a.strip()]
                )
                if len(names) >= 2:
                    duo_counter[tuple(names)] += 1
            if duo_counter:
                duo, count = duo_counter.most_common(1)[0]
                weekly_leagues.append(
                    {
                        "name": "Best Duo League",
                        "winner": {"names": list(duo), "count": count},
                        "contenders": [
                            {"names": list(names), "count": duo_count}
                            for names, duo_count in duo_counter.most_common(3)
                        ],
                    }
                )

            hourly_counter = Counter(
                datetime.fromtimestamp(q.timestamp, tz=self.uk_tz).hour
                for q in weekly_quotes
            )
            if hourly_counter:
                hour, count = hourly_counter.most_common(1)[0]
                weekly_leagues.append(
                    {
                        "name": "Chaos Hour League",
                        "winner": {
                            "hour_uk": f"{hour:02d}:00-{hour:02d}:59",
                            "count": count,
                        },
                        "contenders": [
                            {"hour_uk": f"{h:02d}:00-{h:02d}:59", "count": c}
                            for h, c in hourly_counter.most_common(3)
                        ],
                    }
                )

            word_rankings = sorted(
                weekly_quotes,
                key=lambda q: len(re.findall(r"\b\w+\b", q.quote)),
                reverse=True,
            )
            if word_rankings:
                winner = word_rankings[0]
                winner_words = len(re.findall(r"\b\w+\b", winner.quote))
                weekly_leagues.append(
                    {
                        "name": "Longform Legend League",
                        "winner": {
                            "quote_id": winner.id,
                            "authors": winner.authors,
                            "word_count": winner_words,
                        },
                        "contenders": [
                            {
                                "quote_id": q.id,
                                "authors": q.authors,
                                "word_count": len(re.findall(r"\b\w+\b", q.quote)),
                            }
                            for q in word_rankings[:3]
                        ],
                    }
                )

        digest_data = {
            "mode": "weekly_email",
            "api_context": "This text is generated for an API response that will be sent by email.",
            "window_uk": {
                "start": start_uk.strftime("%Y-%m-%d %H:%M"),
                "end": now_uk.strftime("%Y-%m-%d %H:%M"),
            },
            "counts": {
                "new_quotes": len(weekly_quotes),
                "total_quotes": len(all_quotes),
            },
            "weekly_top_authors": [
                {"name": name, "count": count} for name, count in top_authors
            ],
            "all_time_top_authors": all_time_top_authors,
            "weekly_leagues": weekly_leagues,
            "weekly_quotes": [
                self._digest_quote_payload(q) for q in weekly_quotes[:40]
            ],
            "recent_existing_quotes": [
                self._digest_quote_payload(q) for q in all_quotes[:40]
            ],
        }

        if self.ai_worker.can_generate:
            try:
                subject, body = self.ai_worker.generate_weekly_digest(digest_data)
                return subject, self._append_digest_sponsor(body)
            except Exception as exc:
                self.app.logger.warning(
                    "AI weekly digest failed; using fallback digest: %s", exc
                )

        subject = f"Quote Book Weekly Digest ({len(weekly_quotes)} new)"
        if not weekly_quotes:
            body = (
                "Quiet week in Quote Book: no new entries landed in the last seven days, "
                "so everyone either behaved themselves or forgot to submit the evidence.\n\n"
                f"Still, the archive is sitting at {len(all_quotes)} total quotes, so there is "
                "plenty of historical chaos to revisit. Normal service resumes as soon as the "
                "next unhinged one-liner appears."
            )
            return subject, self._append_digest_sponsor(body)

        top_author_sentence = "Everyone contributed evenly this week."
        if top_authors:
            first_name, first_count = top_authors[0]
            if len(top_authors) >= 3:
                second_name, second_count = top_authors[1]
                third_name, third_count = top_authors[2]
                top_author_sentence = (
                    f"{first_name} led the board with {first_count}, with {second_name} on "
                    f"{second_count} and {third_name} on {third_count}."
                )
            elif len(top_authors) == 2:
                second_name, second_count = top_authors[1]
                top_author_sentence = f"{first_name} led with {first_count}, followed by {second_name} on {second_count}."
            else:
                top_author_sentence = (
                    f"{first_name} topped the week with {first_count} quote(s)."
                )

        league_blurbs: list[str] = []
        for league in weekly_leagues[:5]:
            winner = league.get("winner", {})
            league_name = league.get("name", "League")
            if "quote_id" in winner:
                authors = ", ".join(winner.get("authors") or ["Unknown"])
                league_blurbs.append(
                    f"{league_name}: quote #{winner['quote_id']} by {authors}"
                )
            elif "name" in winner:
                league_blurbs.append(
                    f"{league_name}: {winner['name']} ({winner.get('count', 0)})"
                )
            elif "names" in winner:
                league_blurbs.append(
                    f"{league_name}: {', '.join(winner['names'])} ({winner.get('count', 0)})"
                )
            elif "hour_uk" in winner:
                league_blurbs.append(
                    f"{league_name}: {winner['hour_uk']} with {winner.get('count', 0)} quotes"
                )

        top_pick_ids = (
            [q.id for q, _score in funny_rankings[:5]]
            if funny_rankings
            else [q.id for q in weekly_quotes[:5]]
        )
        top_picks_text = (
            ", ".join(str(qid) for qid in top_pick_ids) if top_pick_ids else "none"
        )
        leagues_text = (
            "; ".join(league_blurbs)
            if league_blurbs
            else "league table still warming up"
        )
        paragraph_one = (
            f"Ah, another week, another {len(weekly_quotes)} fresh quotes to keep the group chat lively. "
            f"{top_author_sentence} Across the whole archive you're now sitting on {len(all_quotes)} "
            "total quotes, which is both impressive and mildly concerning."
        )
        paragraph_two = f"This week's leagues: {leagues_text}. Top picks this week: {top_picks_text}. Cheers, you lot!"
        return subject, self._append_digest_sponsor(
            f"{paragraph_one}\n\n{paragraph_two}"
        )

    def send_email(self, subject: str, body: str) -> None:
        sender = self.config.weekly_email_from or self.config.smtp_user
        if not sender:
            raise RuntimeError("WEEKLY_EMAIL_FROM or SMTP_USER must be configured.")
        recipients = self.get_weekly_email_recipients()
        if not recipients:
            raise RuntimeError("No weekly email recipients configured in database.")
        recipient_count = len(recipients)
        self._increment_metric("email_attempted", recipient_count)
        send_delay_seconds = max(float(self.config.smtp_send_delay_seconds or 0.0), 0.0)
        sent_count = 0
        failures: list[tuple[str, Exception]] = []

        try:
            if self.config.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.config.smtp_host, self.config.smtp_port, timeout=30
                ) as server:
                    if self.config.smtp_user and self.config.smtp_pass:
                        server.login(self.config.smtp_user, self.config.smtp_pass)
                    for index, recipient in enumerate(recipients):
                        message = EmailMessage()
                        message["Subject"] = subject
                        message["From"] = sender
                        message["To"] = recipient
                        message.set_content(body)
                        try:
                            server.send_message(message)
                            sent_count += 1
                        except Exception as exc:
                            failures.append((recipient, exc))
                            self.app.logger.warning(
                                "Email send failed for %s: %s", recipient, exc
                            )
                        if (
                            send_delay_seconds > 0
                            and index < recipient_count - 1
                        ):
                            timelib.sleep(send_delay_seconds)
            else:
                with smtplib.SMTP(
                    self.config.smtp_host, self.config.smtp_port, timeout=30
                ) as server:
                    server.ehlo()
                    if self.config.smtp_use_tls:
                        server.starttls()
                        server.ehlo()
                    if self.config.smtp_user and self.config.smtp_pass:
                        server.login(self.config.smtp_user, self.config.smtp_pass)
                    for index, recipient in enumerate(recipients):
                        message = EmailMessage()
                        message["Subject"] = subject
                        message["From"] = sender
                        message["To"] = recipient
                        message.set_content(body)
                        try:
                            server.send_message(message)
                            sent_count += 1
                        except Exception as exc:
                            failures.append((recipient, exc))
                            self.app.logger.warning(
                                "Email send failed for %s: %s", recipient, exc
                            )
                        if (
                            send_delay_seconds > 0
                            and index < recipient_count - 1
                        ):
                            timelib.sleep(send_delay_seconds)
        except Exception as exc:
            failed_count = max(recipient_count - sent_count, 0)
            self._increment_metric("email_failed", failed_count or recipient_count)
            if sent_count:
                self._increment_metric("email_sent", sent_count)
            self._set_runtime_status(
                "email_last_error", f"{type(exc).__name__}: {exc}"
            )
            raise

        if failures:
            failed_count = len(failures)
            first_recipient, first_exc = failures[0]
            if sent_count:
                self._increment_metric("email_sent", sent_count)
            self._increment_metric("email_failed", failed_count)
            self._set_runtime_status(
                "email_last_error",
                (
                    f"Partial delivery ({sent_count}/{recipient_count}) "
                    f"first failure {first_recipient}: {type(first_exc).__name__}: {first_exc}"
                ),
            )
            self.app.logger.warning(
                "Partial email delivery: sent=%s failed=%s",
                sent_count,
                failed_count,
            )
            if sent_count == 0:
                raise RuntimeError("Email delivery failed for all recipients.")
            return

        self._set_runtime_status("email_last_error", "")
        self._increment_metric("email_sent", sent_count)

    def maybe_send_weekly_email_digest(self, now_uk: datetime | None = None) -> bool:
        if not self.weekly_email_is_configured():
            self._increment_metric("weekly_digest_skipped_unconfigured")
            return False
        now_uk = now_uk or datetime.now(self.uk_tz)
        if now_uk.weekday() != 0:
            self._increment_metric("weekly_digest_skipped_not_due")
            return False

        scheduled_time = datetime.combine(
            now_uk.date(), time(hour=7, minute=0), tzinfo=self.uk_tz
        )
        if now_uk < scheduled_time:
            self._increment_metric("weekly_digest_skipped_not_due")
            return False

        run_key = now_uk.date().isoformat()
        job_name = "weekly_email_digest"
        if not self.claim_scheduled_run(job_name, run_key):
            self._increment_metric("weekly_digest_claim_conflict")
            return False

        try:
            subject, body = self.build_weekly_digest_email(now_uk)
            body_with_footer = self._append_digest_unsubscribe_footer(body)
            self.send_email(subject, body_with_footer)
            self.archive_weekly_digest(
                run_key=run_key,
                subject=subject,
                body=body_with_footer,
                sent_at=int(now_uk.timestamp()),
                recipient_count=len(self.get_weekly_email_recipients()),
            )
            self._increment_metric("weekly_digest_sent")
            self.app.logger.info(
                "Weekly digest email sent for %s to %s.",
                run_key,
                self.get_weekly_email_recipients(),
            )
            return True
        except Exception:
            self._increment_metric("weekly_digest_failure")
            self.release_scheduled_run(job_name, run_key)
            raise

    def weekly_email_scheduler_loop(self) -> None:
        self.app.logger.info("Weekly email scheduler started (Monday 07:00 UK).")
        while True:
            try:
                self.maybe_send_weekly_email_digest()
            except Exception as exc:
                self._increment_metric("weekly_scheduler_loop_errors")
                self.app.logger.warning("Weekly email digest failed: %s", exc)
            timelib.sleep(60)

    def start_weekly_email_scheduler(self) -> None:
        if (
            not self.ensure_scheduler_table()
            or not self.ensure_weekly_email_recipients_table()
            or not self.ensure_weekly_digest_archive_table()
        ):
            self.app.logger.warning(
                "Weekly email scheduler unavailable: required tables are missing."
            )
            return

        self.seed_weekly_email_recipients_from_env()
        if not self.weekly_email_is_configured():
            self.app.logger.info("Weekly email scheduler disabled or not configured.")
            return
        if self.resolve_weekly_scheduler_mode() == "external":
            if not self._external_scheduler_notice_emitted:
                self.app.logger.info(
                    "Weekly email scheduler is in external mode; use host scheduler/cron to run digest checks."
                )
                self._external_scheduler_notice_emitted = True
            return
        if not self.config.is_prod and os.getenv("WERKZEUG_RUN_MAIN") != "true":
            return

        with self._weekly_scheduler_lock:
            if (
                self._weekly_scheduler_thread
                and self._weekly_scheduler_thread.is_alive()
            ):
                return
            self._weekly_scheduler_thread = threading.Thread(
                target=self.weekly_email_scheduler_loop,
                name="weekly-email-scheduler",
                daemon=True,
            )
            self._weekly_scheduler_thread.start()

    def maybe_run_scheduled_jobs_opportunistically(self, force: bool = False) -> None:
        """
        In external scheduler mode, run digest eligibility checks on live requests.
        This keeps PythonAnywhere deployments functional even without background
        threads, while still allowing real cron/scheduled-task execution.
        """
        if self.resolve_weekly_scheduler_mode() != "external":
            return
        if not self.config.weekly_email_enabled:
            return

        now_ts = timelib.time()
        with self._opportunistic_scheduler_lock:
            if not force and (
                now_ts - self._opportunistic_last_check_at
                < self.opportunistic_scheduler_interval_seconds
            ):
                return
            self._opportunistic_last_check_at = now_ts

        try:
            self.maybe_send_weekly_email_digest()
        except Exception as exc:
            self._increment_metric("weekly_scheduler_loop_errors")
            self.app.logger.warning(
                "Opportunistic weekly digest check failed: %s", exc
            )

    # ------------------------
    # Push
    # ------------------------

    def ensure_push_table(self) -> None:
        db_path = self.get_push_db_path()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    endpoint TEXT PRIMARY KEY,
                    subscription TEXT NOT NULL,
                    user_agent TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def save_push_subscription(
        self, subscription: dict, user_agent: str | None = None
    ) -> bool:
        endpoint = (subscription or {}).get("endpoint")
        if not endpoint:
            return False
        self.ensure_push_table()
        payload = json.dumps(subscription, ensure_ascii=False)
        now = int(timelib.time())
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO push_subscriptions
                (endpoint, subscription, user_agent, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (endpoint, payload, user_agent or "", now, now),
            )
        return True

    def delete_push_subscription(self, endpoint: str) -> None:
        if not endpoint:
            return
        self.ensure_push_table()
        with sqlite3.connect(self.get_push_db_path()) as conn:
            conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            )

    def load_push_subscriptions(self) -> list[dict]:
        self.ensure_push_table()
        with sqlite3.connect(self.get_push_db_path()) as conn:
            rows = conn.execute(
                "SELECT subscription FROM push_subscriptions"
            ).fetchall()
        subscriptions = []
        for row in rows:
            try:
                subscriptions.append(json.loads(row[0]))
            except json.JSONDecodeError:
                continue
        return subscriptions

    @staticmethod
    def _should_prune_push_subscription(status_code: int | None) -> bool:
        # Client errors generally mean the subscription is no longer usable.
        return status_code in {400, 401, 403, 404, 410}

    @staticmethod
    def _truncate_push_error(text: str) -> str:
        compact = " ".join((text or "").split())
        if len(compact) > 240:
            return compact[:237] + "..."
        return compact

    def send_push_notification(self, title: str, body: str, url: str) -> int:
        if not self.config.vapid_private_key or not self.config.vapid_public_key:
            self.app.logger.warning("Push notification skipped: missing VAPID keys.")
            return 0

        payload = json.dumps({"title": title, "body": body, "url": url})
        subscriptions = self.load_push_subscriptions()
        if not subscriptions:
            return 0
        self._increment_metric("push_attempted", len(subscriptions))

        sent = 0
        for subscription in subscriptions:
            endpoint = subscription.get("endpoint")
            try:
                webpush(
                    subscription_info=subscription,
                    data=payload,
                    vapid_private_key=self.config.vapid_private_key,
                    vapid_claims={"sub": self.config.vapid_email},
                )
                sent += 1
                self._increment_metric("push_sent")
            except WebPushException as exc:
                self._increment_metric("push_failed")
                status = getattr(exc.response, "status_code", None)
                response_text = ""
                if getattr(exc, "response", None) is not None:
                    response_text = self._truncate_push_error(
                        getattr(exc.response, "text", "")
                    )
                detail = f"status={status} {response_text or str(exc)}".strip()
                self._set_runtime_status("push_last_error", detail)

                if self._should_prune_push_subscription(status) and endpoint:
                    self.delete_push_subscription(endpoint)
                    self._increment_metric("push_pruned")
                else:
                    self.app.logger.warning(
                        "Push failed for %s: %s", endpoint, detail
                    )
            except Exception as exc:
                self._increment_metric("push_failed")
                self._set_runtime_status(
                    "push_last_error", f"{type(exc).__name__}: {exc}"
                )
                self.app.logger.warning("Push error for %s: %s", endpoint, exc)
        if sent:
            self._set_runtime_status("push_last_error", "")
        return sent

    # ------------------------
    # Stats cache
    # ------------------------

    @staticmethod
    def _empty_stats_snapshot() -> dict:
        return {
            "total_quotes": 0,
            "unique_authors": 0,
            "top_authors": [],
            "avg_words": 0,
            "median_words": 0,
            "avg_chars": 0,
            "median_chars": 0,
            "longest_quote": None,
            "shortest_quote": None,
            "newest_quote": None,
            "oldest_quote": None,
            "busiest_day": None,
            "busiest_month": None,
            "peak_hour": None,
            "active_days": 0,
            "avg_quotes_per_active_day": 0,
            "avg_quotes_per_calendar_day": 0,
            "longest_streak_days": 0,
            "streak_start_label": "",
            "streak_end_label": "",
            "recent_7_count": 0,
            "recent_30_count": 0,
            "today_count": 0,
            "context_count": 0,
            "quotes_with_context_percent": 0,
            "multi_author_count": 0,
            "multi_author_percent": 0,
            "avg_authors_per_quote": 0,
            "length_distribution": [],
            "top_terms": [],
            "hour_buckets": [],
            "total_battles": 0,
            "total_anarchy_points": 0,
            "battle_participation": 0,
            "anarchy_participation": 0,
            "most_battled": None,
            "most_anarchy": None,
            "top_winners": [],
            "top_anarchy_quotes": [],
            "best_win_rates": [],
            "top_losers": [],
            "undefeated_quotes": [],
            "top_funny": [],
            "avg_funny": 0,
        }

    def _compute_stats_snapshot(self) -> dict:
        quotes = self.quote_store.get_all_quotes()
        quote_count = len(quotes)
        snapshot = self._empty_stats_snapshot()
        snapshot["total_quotes"] = self.quote_store.get_total_quotes()
        speaker_counts = self.quote_store.get_speaker_counts()
        snapshot["unique_authors"] = len(speaker_counts)
        snapshot["top_authors"] = speaker_counts[:5]

        word_counts = [len(re.findall(r"\b\w+\b", q.quote)) for q in quotes]
        snapshot["avg_words"] = (
            round(sum(word_counts) / len(word_counts), 1) if word_counts else 0
        )
        snapshot["median_words"] = round(median(word_counts), 1) if word_counts else 0
        char_counts = [len(q.quote) for q in quotes]
        snapshot["avg_chars"] = (
            round(sum(char_counts) / len(char_counts), 1) if char_counts else 0
        )
        snapshot["median_chars"] = round(median(char_counts), 1) if char_counts else 0

        snapshot["longest_quote"] = max(
            quotes, key=lambda q: len(q.quote), default=None
        )
        snapshot["shortest_quote"] = min(
            quotes, key=lambda q: len(q.quote), default=None
        )
        snapshot["newest_quote"] = max(quotes, key=lambda q: q.timestamp, default=None)
        snapshot["oldest_quote"] = min(quotes, key=lambda q: q.timestamp, default=None)

        day_counts = Counter()
        hour_counts = Counter()
        month_counts = Counter()
        context_count = 0
        multi_author_count = 0
        author_slots = 0
        for quote in quotes:
            dt = datetime.fromtimestamp(quote.timestamp, tz=self.uk_tz)
            day_counts[dt.date()] += 1
            hour_counts[dt.hour] += 1
            month_counts[(dt.year, dt.month)] += 1
            if (quote.context or "").strip():
                context_count += 1
            author_total = len(
                [a for a in quote.authors if isinstance(a, str) and a.strip()]
            )
            author_slots += max(author_total, 1)
            if author_total > 1:
                multi_author_count += 1

        if day_counts:
            day, count = day_counts.most_common(1)[0]
            snapshot["busiest_day"] = {
                "label": day.strftime("%d %b %Y"),
                "count": count,
            }
        if month_counts:
            (year, month), count = month_counts.most_common(1)[0]
            snapshot["busiest_month"] = {
                "label": datetime(year, month, 1).strftime("%b %Y"),
                "count": count,
            }
        if hour_counts:
            hour, count = hour_counts.most_common(1)[0]
            snapshot["peak_hour"] = {
                "label": f"{hour:02d}:00-{hour:02d}:59",
                "count": count,
            }

        hour_buckets = [
            ("Late night", "12am-3am", range(0, 3)),
            ("Early morning", "3am-6am", range(3, 6)),
            ("Morning", "6am-9am", range(6, 9)),
            ("Late morning", "9am-12pm", range(9, 12)),
            ("Afternoon", "12pm-3pm", range(12, 15)),
            ("Late afternoon", "3pm-6pm", range(15, 18)),
            ("Evening", "6pm-9pm", range(18, 21)),
            ("Late evening", "9pm-12am", range(21, 24)),
        ]
        bucket_data = []
        for label, range_label, hours in hour_buckets:
            count = sum(hour_counts[h] for h in hours)
            bucket_data.append(
                {"label": label, "range_label": range_label, "count": count}
            )
        max_bucket = max((bucket["count"] for bucket in bucket_data), default=1)
        for bucket in bucket_data:
            bucket["percent"] = (
                int((bucket["count"] / max_bucket) * 100) if max_bucket else 0
            )
        snapshot["hour_buckets"] = bucket_data

        longest_streak_days = 0
        streak_start_label = ""
        streak_end_label = ""
        if day_counts:
            sorted_days = sorted(day_counts.keys())
            best_start = best_end = current_start = current_end = sorted_days[0]
            best_len = current_len = 1
            for idx in range(1, len(sorted_days)):
                if (sorted_days[idx] - sorted_days[idx - 1]).days == 1:
                    current_end = sorted_days[idx]
                    current_len += 1
                else:
                    if current_len > best_len:
                        best_len = current_len
                        best_start = current_start
                        best_end = current_end
                    current_start = current_end = sorted_days[idx]
                    current_len = 1
            if current_len > best_len:
                best_len = current_len
                best_start = current_start
                best_end = current_end
            longest_streak_days = best_len
            streak_start_label = best_start.strftime("%d %b %Y")
            streak_end_label = best_end.strftime("%d %b %Y")

        snapshot["longest_streak_days"] = longest_streak_days
        snapshot["streak_start_label"] = streak_start_label
        snapshot["streak_end_label"] = streak_end_label
        snapshot["active_days"] = len(day_counts)
        snapshot["avg_quotes_per_active_day"] = (
            round(quote_count / len(day_counts), 2) if day_counts and quote_count else 0
        )

        if snapshot["newest_quote"] and snapshot["oldest_quote"] and quote_count:
            span_days = max(
                1,
                (
                    datetime.fromtimestamp(
                        snapshot["newest_quote"].timestamp, tz=self.uk_tz
                    ).date()
                    - datetime.fromtimestamp(
                        snapshot["oldest_quote"].timestamp, tz=self.uk_tz
                    ).date()
                ).days
                + 1,
            )
            snapshot["avg_quotes_per_calendar_day"] = round(quote_count / span_days, 2)

        now_uk = datetime.now(self.uk_tz)
        now_ts = int(now_uk.timestamp())
        snapshot["recent_7_count"] = len(
            [q for q in quotes if q.timestamp >= now_ts - 7 * 24 * 3600]
        )
        snapshot["recent_30_count"] = len(
            [q for q in quotes if q.timestamp >= now_ts - 30 * 24 * 3600]
        )
        today_start = datetime.combine(now_uk.date(), time.min, tzinfo=self.uk_tz)
        tomorrow_start = today_start + timedelta(days=1)
        snapshot["today_count"] = len(
            [
                q
                for q in quotes
                if int(today_start.timestamp())
                <= q.timestamp
                < int(tomorrow_start.timestamp())
            ]
        )

        snapshot["context_count"] = context_count
        snapshot["quotes_with_context_percent"] = (
            round((context_count / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["multi_author_count"] = multi_author_count
        snapshot["multi_author_percent"] = (
            round((multi_author_count / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["avg_authors_per_quote"] = (
            round(author_slots / quote_count, 2) if quote_count else 0
        )

        length_distribution = [
            {
                "label": "Short (1-8 words)",
                "count": sum(1 for c in word_counts if c <= 8),
            },
            {
                "label": "Medium (9-20 words)",
                "count": sum(1 for c in word_counts if 9 <= c <= 20),
            },
            {
                "label": "Long (21+ words)",
                "count": sum(1 for c in word_counts if c >= 21),
            },
        ]
        max_length_bucket = max(
            (bucket["count"] for bucket in length_distribution), default=1
        )
        for bucket in length_distribution:
            bucket["percent"] = (
                int((bucket["count"] / max_length_bucket) * 100)
                if max_length_bucket
                else 0
            )
        snapshot["length_distribution"] = length_distribution

        token_counts = Counter()
        for quote in quotes:
            for token in re.findall(r"[A-Za-z']+", quote.quote.lower()):
                if len(token) < 3 or token in STOPWORDS:
                    continue
                token_counts[token] += 1
        snapshot["top_terms"] = token_counts.most_common(10)

        total_battle_entries = sum(q.stats.get("battles", 0) for q in quotes)
        snapshot["total_battles"] = (
            total_battle_entries // 2 if total_battle_entries else 0
        )
        snapshot["most_battled"] = max(
            quotes, key=lambda q: q.stats.get("battles", 0), default=None
        )
        battle_quotes = [q for q in quotes if q.stats.get("battles", 0) > 0]
        snapshot["battle_participation"] = (
            round((len(battle_quotes) / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["total_anarchy_points"] = sum(
            q.stats.get("anarchy_points", 0) for q in quotes
        )
        snapshot["most_anarchy"] = max(
            quotes,
            key=lambda q: q.stats.get("anarchy_points", 0),
            default=None,
        )
        anarchy_quotes = [q for q in quotes if q.stats.get("anarchy_points", 0) > 0]
        snapshot["anarchy_participation"] = (
            round((len(anarchy_quotes) / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["top_anarchy_quotes"] = sorted(
            anarchy_quotes,
            key=lambda q: (q.stats.get("anarchy_points", 0), q.id),
            reverse=True,
        )[:5]
        snapshot["top_winners"] = [
            q
            for q in sorted(quotes, key=lambda q: q.stats.get("wins", 0), reverse=True)
            if q.stats.get("wins", 0) > 0
        ][:5]

        min_battles_for_rate = 3
        win_rate_candidates = []
        for q in quotes:
            battles = q.stats.get("battles", 0)
            if battles >= min_battles_for_rate:
                win_rate_candidates.append((q, q.stats.get("wins", 0) / battles))
        snapshot["best_win_rates"] = sorted(
            win_rate_candidates, key=lambda x: x[1], reverse=True
        )[:5]
        snapshot["top_losers"] = sorted(
            [q for q in quotes if q.stats.get("losses", 0) > 0],
            key=lambda q: q.stats.get("losses", 0),
            reverse=True,
        )[:5]
        snapshot["undefeated_quotes"] = sorted(
            [
                q
                for q in quotes
                if q.stats.get("battles", 0) >= min_battles_for_rate
                and q.stats.get("losses", 0) == 0
            ],
            key=lambda q: (q.stats.get("wins", 0), q.stats.get("battles", 0)),
            reverse=True,
        )[:5]

        funny_scores = []
        for q in quotes:
            score = self.ai_worker.classify_funny_score(q.quote, q.authors, q.stats)
            funny_scores.append((q, score))
        funny_scores.sort(key=lambda x: x[1], reverse=True)
        snapshot["top_funny"] = funny_scores[:5]
        snapshot["avg_funny"] = (
            round(sum(score for _, score in funny_scores) / len(funny_scores), 2)
            if funny_scores
            else 0
        )
        return snapshot

    def refresh_stats_cache(self, reason: str = "manual") -> bool:
        try:
            snapshot = self._compute_stats_snapshot()
        except Exception as exc:
            self.app.logger.warning("Stats cache refresh failed (%s): %s", reason, exc)
            return False
        with self.stats_cache_lock:
            self.stats_cache_snapshot = snapshot
        self.app.logger.info("Stats cache refreshed (%s).", reason)
        return True

    def get_stats_cache_snapshot(self) -> dict:
        with self.stats_cache_lock:
            snapshot = self.stats_cache_snapshot
        if snapshot is not None:
            return snapshot
        if self.refresh_stats_cache("lazy-load"):
            with self.stats_cache_lock:
                if self.stats_cache_snapshot is not None:
                    return self.stats_cache_snapshot
        return self._empty_stats_snapshot()

    # ------------------------
    # Logging + request hooks
    # ------------------------

    def configure_logging(self) -> None:
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        file_handler = MaxSizeFileHandler("app.log", max_bytes=2 * 1024 * 1024)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        self.app.logger.handlers.clear()
        self.app.logger.setLevel(logging.INFO)
        self.app.logger.propagate = False
        self.app.logger.addHandler(console_handler)
        self.app.logger.addHandler(file_handler)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self.app.logger.info("Logging initialised")

    @staticmethod
    def start_timer():
        g.start_time = timelib.time()

    def log_request(self, response):
        duration = round(timelib.time() - g.start_time, 3)
        self.app.logger.info(
            "%s %s (%s) -> %s [%ss]",
            request.method,
            request.path,
            request.endpoint,
            response.status_code,
            duration,
        )
        return response

    def log_exception(self, exception):
        if exception:
            self.app.logger.warning(
                "Unhandled exception on %s %s: %s",
                request.method,
                request.path,
                type(exception).__name__,
            )

    def refresh_qb(self):
        status = self.quote_store.reload()
        if status == 200:
            self.app.logger.info("Quote book reloaded.")
        if status != 200 and status != 304:
            if status < 400 or status > 600:
                abort(500)
