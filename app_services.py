from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import smtplib
import threading
import time as timelib
from collections import Counter
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


class AppServices:
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

    # ------------------------
    # Formatting helpers
    # ------------------------

    def to_uk_datetime(self, ts):
        dt = datetime.fromtimestamp(ts, tz=self.uk_tz)
        day = dt.day
        suffix = (
            "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        )
        return {"date": f"{day}{suffix} {dt.strftime('%B')}", "time": dt.strftime("%H:%M")}

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

    def build_public_url(self, path: str) -> str:
        base = self.config.public_base_url or request.url_root
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

    # ------------------------
    # DB helpers
    # ------------------------

    def get_push_db_path(self) -> str:
        if getattr(self.quote_store, "_local", None):
            return self.quote_store._local.filepath
        return os.getenv("QUOTEBOOK_DB", "qb.db")

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
            self.app.logger.error("Unable to ensure weekly_email_recipients table: %s", exc)
            return False

    def seed_weekly_email_recipients_from_env(self) -> None:
        if not self.config.weekly_email_to_seed:
            return
        if not self.ensure_weekly_email_recipients_table():
            self.app.logger.error("Skipping weekly email recipient seed: recipients table missing.")
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
        pattern = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE)
        return bool(pattern.match(email))

    def add_weekly_email_recipient(self, email: str) -> bool:
        normalized = (email or "").strip().lower()
        if not self.is_valid_email_address(normalized):
            return False
        if not self.ensure_weekly_email_recipients_table():
            self.app.logger.error("Cannot add weekly email recipient: recipients table missing.")
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
        return cur.rowcount > 0

    def claim_scheduled_run(self, job_name: str, run_key: str) -> bool:
        if not self.ensure_scheduler_table():
            self.app.logger.error("Cannot claim scheduled run: scheduled_job_runs table missing.")
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
            self.app.logger.error("Cannot release scheduled run: scheduled_job_runs table missing.")
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

    def build_weekly_digest_email(self, now_uk: datetime) -> tuple[str, str]:
        start_uk = now_uk - timedelta(days=7)
        weekly_quotes = self.quote_store.get_quotes_between(
            int(start_uk.timestamp()),
            int(now_uk.timestamp()),
        )
        weekly_quotes = sorted(weekly_quotes, key=lambda q: (q.timestamp, q.id), reverse=True)
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
                    (q, self.ai_worker.classify_funny_score(q.quote, q.authors, q.stats))
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
                            {"name": n, "count": c} for n, c in speaker_counter.most_common(3)
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
                datetime.fromtimestamp(q.timestamp, tz=self.uk_tz).hour for q in weekly_quotes
            )
            if hourly_counter:
                hour, count = hourly_counter.most_common(1)[0]
                weekly_leagues.append(
                    {
                        "name": "Chaos Hour League",
                        "winner": {"hour_uk": f"{hour:02d}:00-{hour:02d}:59", "count": count},
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
            "weekly_quotes": [self._digest_quote_payload(q) for q in weekly_quotes[:40]],
            "recent_existing_quotes": [self._digest_quote_payload(q) for q in all_quotes[:40]],
        }

        if self.ai_worker.can_generate:
            try:
                return self.ai_worker.generate_weekly_digest(digest_data)
            except Exception as exc:
                self.app.logger.warning("AI weekly digest failed; using fallback digest: %s", exc)

        subject = f"Quote Book Weekly Digest ({len(weekly_quotes)} new)"
        if not weekly_quotes:
            body = (
                "Quiet week in Quote Book: no new entries landed in the last seven days, "
                "so everyone either behaved themselves or forgot to submit the evidence.\n\n"
                f"Still, the archive is sitting at {len(all_quotes)} total quotes, so there is "
                "plenty of historical chaos to revisit. Normal service resumes as soon as the "
                "next unhinged one-liner appears."
            )
            return subject, body

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
                top_author_sentence = (
                    f"{first_name} led with {first_count}, followed by {second_name} on {second_count}."
                )
            else:
                top_author_sentence = f"{first_name} topped the week with {first_count} quote(s)."

        league_blurbs: list[str] = []
        for league in weekly_leagues[:5]:
            winner = league.get("winner", {})
            league_name = league.get("name", "League")
            if "quote_id" in winner:
                authors = ", ".join(winner.get("authors") or ["Unknown"])
                league_blurbs.append(f"{league_name}: quote #{winner['quote_id']} by {authors}")
            elif "name" in winner:
                league_blurbs.append(f"{league_name}: {winner['name']} ({winner.get('count', 0)})")
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
        top_picks_text = ", ".join(str(qid) for qid in top_pick_ids) if top_pick_ids else "none"
        leagues_text = "; ".join(league_blurbs) if league_blurbs else "league table still warming up"
        paragraph_one = (
            f"Ah, another week, another {len(weekly_quotes)} fresh quotes to keep the group chat lively. "
            f"{top_author_sentence} Across the whole archive you're now sitting on {len(all_quotes)} "
            "total quotes, which is both impressive and mildly concerning."
        )
        paragraph_two = (
            f"This week's leagues: {leagues_text}. Top picks this week: {top_picks_text}. Cheers, you lot!"
        )
        return subject, f"{paragraph_one}\n\n{paragraph_two}"

    def send_email(self, subject: str, body: str) -> None:
        sender = self.config.weekly_email_from or self.config.smtp_user
        if not sender:
            raise RuntimeError("WEEKLY_EMAIL_FROM or SMTP_USER must be configured.")
        recipients = self.get_weekly_email_recipients()
        if not recipients:
            raise RuntimeError("No weekly email recipients configured in database.")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(body)

        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, timeout=30
            ) as server:
                if self.config.smtp_user and self.config.smtp_pass:
                    server.login(self.config.smtp_user, self.config.smtp_pass)
                server.send_message(message)
            return

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as server:
            server.ehlo()
            if self.config.smtp_use_tls:
                server.starttls()
                server.ehlo()
            if self.config.smtp_user and self.config.smtp_pass:
                server.login(self.config.smtp_user, self.config.smtp_pass)
            server.send_message(message)

    def maybe_send_weekly_email_digest(self, now_uk: datetime | None = None) -> bool:
        if not self.weekly_email_is_configured():
            return False
        now_uk = now_uk or datetime.now(self.uk_tz)
        if now_uk.weekday() != 0:
            return False

        scheduled_time = datetime.combine(now_uk.date(), time(hour=7, minute=0), tzinfo=self.uk_tz)
        if now_uk < scheduled_time:
            return False

        run_key = now_uk.date().isoformat()
        job_name = "weekly_email_digest"
        if not self.claim_scheduled_run(job_name, run_key):
            return False

        try:
            subject, body = self.build_weekly_digest_email(now_uk)
            self.send_email(subject, body)
            self.app.logger.info(
                "Weekly digest email sent for %s to %s.",
                run_key,
                self.get_weekly_email_recipients(),
            )
            return True
        except Exception:
            self.release_scheduled_run(job_name, run_key)
            raise

    def weekly_email_scheduler_loop(self) -> None:
        self.app.logger.info("Weekly email scheduler started (Monday 07:00 UK).")
        while True:
            try:
                self.maybe_send_weekly_email_digest()
            except Exception as exc:
                self.app.logger.warning("Weekly email digest failed: %s", exc)
            timelib.sleep(60)

    def start_weekly_email_scheduler(self) -> None:
        if not self.ensure_scheduler_table() or not self.ensure_weekly_email_recipients_table():
            self.app.logger.warning("Weekly email scheduler unavailable: required tables are missing.")
            return

        self.seed_weekly_email_recipients_from_env()
        if not self.weekly_email_is_configured():
            self.app.logger.info("Weekly email scheduler disabled or not configured.")
            return
        if not self.config.is_prod and os.getenv("WERKZEUG_RUN_MAIN") != "true":
            return

        with self._weekly_scheduler_lock:
            if self._weekly_scheduler_thread and self._weekly_scheduler_thread.is_alive():
                return
            self._weekly_scheduler_thread = threading.Thread(
                target=self.weekly_email_scheduler_loop,
                name="weekly-email-scheduler",
                daemon=True,
            )
            self._weekly_scheduler_thread.start()

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

    def save_push_subscription(self, subscription: dict, user_agent: str | None = None) -> bool:
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
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def load_push_subscriptions(self) -> list[dict]:
        self.ensure_push_table()
        with sqlite3.connect(self.get_push_db_path()) as conn:
            rows = conn.execute("SELECT subscription FROM push_subscriptions").fetchall()
        subscriptions = []
        for row in rows:
            try:
                subscriptions.append(json.loads(row[0]))
            except json.JSONDecodeError:
                continue
        return subscriptions

    def send_push_notification(self, title: str, body: str, url: str) -> int:
        if not self.config.vapid_private_key or not self.config.vapid_public_key:
            self.app.logger.warning("Push notification skipped: missing VAPID keys.")
            return 0

        payload = json.dumps({"title": title, "body": body, "url": url})
        subscriptions = self.load_push_subscriptions()
        if not subscriptions:
            return 0

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
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410) and endpoint:
                    self.delete_push_subscription(endpoint)
                else:
                    self.app.logger.warning("Push failed for %s: %s", endpoint, exc)
            except Exception as exc:
                self.app.logger.warning("Push error for %s: %s", endpoint, exc)
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
            "battle_participation": 0,
            "most_battled": None,
            "top_winners": [],
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
        snapshot["avg_words"] = round(sum(word_counts) / len(word_counts), 1) if word_counts else 0
        snapshot["median_words"] = round(median(word_counts), 1) if word_counts else 0
        char_counts = [len(q.quote) for q in quotes]
        snapshot["avg_chars"] = round(sum(char_counts) / len(char_counts), 1) if char_counts else 0
        snapshot["median_chars"] = round(median(char_counts), 1) if char_counts else 0

        snapshot["longest_quote"] = max(quotes, key=lambda q: len(q.quote), default=None)
        snapshot["shortest_quote"] = min(quotes, key=lambda q: len(q.quote), default=None)
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
            author_total = len([a for a in quote.authors if isinstance(a, str) and a.strip()])
            author_slots += max(author_total, 1)
            if author_total > 1:
                multi_author_count += 1

        if day_counts:
            day, count = day_counts.most_common(1)[0]
            snapshot["busiest_day"] = {"label": day.strftime("%d %b %Y"), "count": count}
        if month_counts:
            (year, month), count = month_counts.most_common(1)[0]
            snapshot["busiest_month"] = {
                "label": datetime(year, month, 1).strftime("%b %Y"),
                "count": count,
            }
        if hour_counts:
            hour, count = hour_counts.most_common(1)[0]
            snapshot["peak_hour"] = {"label": f"{hour:02d}:00-{hour:02d}:59", "count": count}

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
            bucket_data.append({"label": label, "range_label": range_label, "count": count})
        max_bucket = max((bucket["count"] for bucket in bucket_data), default=1)
        for bucket in bucket_data:
            bucket["percent"] = int((bucket["count"] / max_bucket) * 100) if max_bucket else 0
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
                    datetime.fromtimestamp(snapshot["newest_quote"].timestamp, tz=self.uk_tz).date()
                    - datetime.fromtimestamp(snapshot["oldest_quote"].timestamp, tz=self.uk_tz).date()
                ).days
                + 1,
            )
            snapshot["avg_quotes_per_calendar_day"] = round(quote_count / span_days, 2)

        now_uk = datetime.now(self.uk_tz)
        now_ts = int(now_uk.timestamp())
        snapshot["recent_7_count"] = len([q for q in quotes if q.timestamp >= now_ts - 7 * 24 * 3600])
        snapshot["recent_30_count"] = len([q for q in quotes if q.timestamp >= now_ts - 30 * 24 * 3600])
        today_start = datetime.combine(now_uk.date(), time.min, tzinfo=self.uk_tz)
        tomorrow_start = today_start + timedelta(days=1)
        snapshot["today_count"] = len(
            [q for q in quotes if int(today_start.timestamp()) <= q.timestamp < int(tomorrow_start.timestamp())]
        )

        snapshot["context_count"] = context_count
        snapshot["quotes_with_context_percent"] = (
            round((context_count / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["multi_author_count"] = multi_author_count
        snapshot["multi_author_percent"] = (
            round((multi_author_count / quote_count) * 100, 1) if quote_count else 0
        )
        snapshot["avg_authors_per_quote"] = round(author_slots / quote_count, 2) if quote_count else 0

        length_distribution = [
            {"label": "Short (1-8 words)", "count": sum(1 for c in word_counts if c <= 8)},
            {"label": "Medium (9-20 words)", "count": sum(1 for c in word_counts if 9 <= c <= 20)},
            {"label": "Long (21+ words)", "count": sum(1 for c in word_counts if c >= 21)},
        ]
        max_length_bucket = max((bucket["count"] for bucket in length_distribution), default=1)
        for bucket in length_distribution:
            bucket["percent"] = (
                int((bucket["count"] / max_length_bucket) * 100) if max_length_bucket else 0
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
        snapshot["total_battles"] = total_battle_entries // 2 if total_battle_entries else 0
        snapshot["most_battled"] = max(quotes, key=lambda q: q.stats.get("battles", 0), default=None)
        battle_quotes = [q for q in quotes if q.stats.get("battles", 0) > 0]
        snapshot["battle_participation"] = (
            round((len(battle_quotes) / quote_count) * 100, 1) if quote_count else 0
        )
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
