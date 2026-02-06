import json
import logging
import os
import random
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

DEFAULT_STATS: Dict[str, int] = {
    "wins": 0,
    "losses": 0,
    "battles": 0,
    "score": 0,
}


@dataclass
class Quote:
    id: int
    quote: str
    authors: List[str]
    timestamp: int
    context: str
    stats: Dict[str, int] = field(default_factory=lambda: DEFAULT_STATS.copy())


class QuoteBook:
    def __init__(self, filepath: str = "qb.db"):
        self.filepath = self._resolve_db_path(filepath)
        self.last_mtime = 0
        self.quotes: List[Quote] = []

        self._ensure_schema()
        self._maybe_migrate_from_json(filepath)
        self._load()
        self._recalculate_stats()

        logger.info(
            "Loaded %s quotes with %s unique speakers.",
            self.total_quotes,
            len(self.speaker_counts),
        )

    # ------------------------
    # Internal loading logic
    # ------------------------

    def _resolve_db_path(self, filepath: str) -> str:
        if filepath.lower().endswith(".qbf"):
            return os.path.splitext(filepath)[0] + ".db"
        if filepath.lower().endswith(".db"):
            return filepath
        return filepath + ".db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.filepath)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY,
                    quote TEXT NOT NULL,
                    authors TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    context TEXT NOT NULL,
                    stats TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quotes_timestamp ON quotes (timestamp)"
            )

    def _maybe_migrate_from_json(self, source_path: str) -> None:
        existing_count = 0
        if os.path.exists(self.filepath):
            try:
                with self._connect() as conn:
                    existing_count = conn.execute(
                        "SELECT COUNT(*) FROM quotes"
                    ).fetchone()[0]
            except sqlite3.Error:
                existing_count = 0

        if existing_count > 0:
            return

        json_path = source_path
        if not json_path.lower().endswith(".qbf"):
            json_path = "qb.qbf"

        if not os.path.exists(json_path):
            return

        with open(json_path, "r", encoding="utf-8") as f:
            raw_quotes = json.load(f)

        if not raw_quotes:
            return

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, stats)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        q["id"],
                        q["quote"],
                        json.dumps(q.get("authors", []), ensure_ascii=False),
                        q.get("timestamp", 0),
                        q.get("context", ""),
                        json.dumps(q.get("stats", DEFAULT_STATS.copy()), ensure_ascii=False),
                    )
                    for q in raw_quotes
                ],
            )

        logger.info("Migrated %s quotes from %s to %s", len(raw_quotes), json_path, self.filepath)

    def _load(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, quote, authors, timestamp, context, stats FROM quotes"
            ).fetchall()

        self.quotes = [
            Quote(
                id=row["id"],
                quote=row["quote"],
                authors=self._parse_json_list(row["authors"]),
                timestamp=row["timestamp"],
                context=row["context"],
                stats=self._parse_json_dict(row["stats"]),
            )
            for row in rows
        ]

        if os.path.exists(self.filepath):
            self.last_mtime = os.path.getmtime(self.filepath)
        logger.debug("Loaded %s quotes from %s", len(self.quotes), self.filepath)

    def _parse_json_list(self, raw: str) -> List[str]:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item) for item in data]
        except json.JSONDecodeError:
            pass
        return []

    def _parse_json_dict(self, raw: str) -> Dict[str, int]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return DEFAULT_STATS.copy()

    # ------------------------
    # Quote access
    # ------------------------

    def get_random_quote(self) -> Quote:
        return random.choice(self.quotes)

    def get_quote_by_id(self, quote_id: int) -> Quote | None:
        return next((q for q in self.quotes if q.id == quote_id), None)

    # ------------------------
    # Searching
    # ------------------------

    def search_quotes(self, query: str):
        query_lower = query.lower()
        results = []

        for q in self.quotes:
            if (
                query_lower in q.quote.lower()
                or any(query_lower in author.lower() for author in q.authors)
                or (q.context and query_lower in q.context.lower())
            ):
                results.append(q)

        return results

    def get_quotes_between(self, start_ts, end_ts):
        return [q for q in self.quotes if start_ts <= q.timestamp <= end_ts]

    # ------------------------
    # Stats
    # ------------------------

    def get_quote_counts(self):
        counts = defaultdict(int)

        for q in self.quotes:
            for author in q.authors:
                counts[author] += 1

        return list(counts.items())

    def get_sorted_quote_counts(self):
        return sorted(
            self.get_quote_counts(),
            key=lambda x: x[1],
            reverse=True,
        )

    # ------------------------
    # Mutations
    # ------------------------

    def add_quote(self, quote: Quote):
        self.quotes.append(quote)
        self._upsert_quote(quote)
        self._recalculate_stats()

    def next_id(self) -> int:
        if not self.quotes:
            return 1
        return max(q.id for q in self.quotes) + 1

    def parse_authors(self, raw):
        """
        Accepts:
        - "Ben"
        - "Ben and James"
        - "Ben, James"
        - "Ben, James, and Kim"
        - "test1, test2, test3, and test4"
        Returns:
        ["Ben", "James", "Kim"]
        """

        # Normalise " and " / ", and " into commas
        cleaned = re.sub(r"(?:,\s*)?\band\b\s*", ", ", raw, flags=re.IGNORECASE)

        # Split on commas
        authors = [a.strip() for a in cleaned.split(",") if a.strip()]

        return authors

    def _upsert_quote(self, quote: Quote) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, stats)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.id,
                    quote.quote,
                    json.dumps(quote.authors, ensure_ascii=False),
                    quote.timestamp,
                    quote.context,
                    json.dumps(quote.stats, ensure_ascii=False),
                ),
            )

    def _save(self):
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, stats)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        q.id,
                        q.quote,
                        json.dumps(q.authors, ensure_ascii=False),
                        q.timestamp,
                        q.context,
                        json.dumps(q.stats, ensure_ascii=False),
                    )
                    for q in self.quotes
                ],
            )

        if os.path.exists(self.filepath):
            self.last_mtime = os.path.getmtime(self.filepath)
        logger.debug("Saved %s quotes to %s", len(self.quotes), self.filepath)

    def _recalculate_stats(self):
        self.total_quotes = len(self.quotes) + 1
        self.speaker_counts = self.get_sorted_quote_counts()

    # ------------------------
    # Reloading
    # ------------------------

    def reload(self, force: bool = False) -> int:
        try:
            mtime = os.path.getmtime(self.filepath)
        except FileNotFoundError:
            logger.error("Quote book file missing: %s", self.filepath)
            return 500

        if not force and mtime == self.last_mtime:
            return 304  # Not Modified

        logger.info("Reloading quote book from disk.")
        self._load()
        self._recalculate_stats()
        return 200


if __name__ == "__main__":
    exit()  # Prevent running standalone
