import json
import logging
import os
import random
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

DEFAULT_STATS: Dict[str, int] = {
    "wins": 0,
    "losses": 0,
    "battles": 0,
    "score": 0,
    "anarchy_points": 0,
}


@dataclass
class Quote:
    id: int
    quote: str
    authors: List[str]
    timestamp: int
    context: str
    tags: List[str] = field(default_factory=list)
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
                    tags TEXT NOT NULL DEFAULT '[]',
                    stats TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(quotes)").fetchall()
            }
            if "tags" not in columns:
                conn.execute(
                    "ALTER TABLE quotes ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
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

        # Only migrate when the database has no rows.
        if existing_count > 0:
            return

        json_path = source_path
        if not json_path.lower().endswith(".qbf"):
            json_path = "qb.qbf"

        if not os.path.exists(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                raw_quotes = json.load(f)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse %s: %s", json_path, exc)
            return

        if not raw_quotes:
            return

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, tags, stats)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        q["id"],
                        q["quote"],
                        json.dumps(q.get("authors", []), ensure_ascii=False),
                        q.get("timestamp", 0),
                        q.get("context", ""),
                        json.dumps(
                            self.normalize_tags(q.get("tags", [])), ensure_ascii=False
                        ),
                        json.dumps(
                            self.normalize_stats(q.get("stats")), ensure_ascii=False
                        ),
                    )
                    for q in raw_quotes
                ],
            )

        logger.info(
            "Migrated %s quotes from %s to %s",
            len(raw_quotes),
            json_path,
            self.filepath,
        )

    def _load(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, quote, authors, timestamp, context, tags, stats FROM quotes ORDER BY id"
            ).fetchall()

        self.quotes = [
            Quote(
                id=row["id"],
                quote=row["quote"],
                authors=self._parse_json_list(row["authors"]),
                timestamp=row["timestamp"],
                context=row["context"],
                tags=self.normalize_tags(self._parse_json_list(row["tags"])),
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
                return self.normalize_stats(data)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return DEFAULT_STATS.copy()

    @staticmethod
    def normalize_stats(stats: Dict[str, int] | None) -> Dict[str, int]:
        normalized = DEFAULT_STATS.copy()
        if not isinstance(stats, dict):
            return normalized

        for key, value in stats.items():
            key_text = str(key)
            try:
                normalized[key_text] = int(value)
            except (TypeError, ValueError):
                continue
        return normalized

    @staticmethod
    def normalize_tags(tags: List[str] | None) -> List[str]:
        if not isinstance(tags, list):
            return []

        normalized: List[str] = []
        seen = set()
        for raw in tags:
            text = str(raw or "").strip().lower()
            if not text:
                continue
            text = re.sub(r"[^a-z0-9\s-]", "", text)
            text = re.sub(r"\s+", "-", text)
            text = re.sub(r"-{2,}", "-", text).strip("-")
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
            if len(normalized) >= 12:
                break
        return normalized

    def parse_tags(self, raw) -> List[str]:
        if isinstance(raw, list):
            return self.normalize_tags(raw)
        raw_text = str(raw or "").strip()
        if not raw_text:
            return []
        parts = re.split(r"[,#;\n]", raw_text)
        return self.normalize_tags([part.strip() for part in parts if part.strip()])

    # ------------------------
    # Quote access
    # ------------------------

    def get_random_quote(self) -> Quote | None:
        if not self.quotes:
            return None
        return random.choice(self.quotes)

    def get_quote_by_id(self, quote_id: int) -> Quote | None:
        return next((q for q in self.quotes if q.id == quote_id), None)

    # ------------------------
    # Searching
    # ------------------------

    def search_quotes(self, query: str, tag: str | None = None):
        query = (query or "").strip()
        tag_filter = self.normalize_tags([tag])[0] if tag else ""

        if not query:
            return []

        query_lower = query.lower()
        query_tokens = re.findall(r"\b\w+\b", query_lower)
        query_token_set = set(query_tokens)

        scored_results = []
        for q in self.quotes:
            if tag_filter and tag_filter not in self.normalize_tags(q.tags):
                continue
            quote_text = q.quote.lower()
            authors_text = " ".join(q.authors).lower()
            context_text = (q.context or "").lower()
            tags_text = " ".join(self.normalize_tags(q.tags)).replace("-", " ")

            score = 0.0

            # Phrase boosts
            if query_lower in quote_text:
                score += 8.0
            if query_lower in authors_text:
                score += 10.0
            if query_lower in context_text:
                score += 5.0
            if query_lower in tags_text:
                score += 6.0

            # Token-level boosts
            quote_tokens = set(re.findall(r"\b\w+\b", quote_text))
            author_tokens = set(re.findall(r"\b\w+\b", authors_text))
            context_tokens = set(re.findall(r"\b\w+\b", context_text))
            tag_tokens = set(re.findall(r"\b\w+\b", tags_text))

            for token in query_tokens:
                if token in quote_tokens:
                    score += 2.0
                if token in author_tokens:
                    score += 3.0
                if token in context_tokens:
                    score += 1.0
                if token in tag_tokens:
                    score += 2.0

            if query_token_set and all(
                token in quote_tokens
                or token in author_tokens
                or token in context_tokens
                or token in tag_tokens
                for token in query_token_set
            ):
                score += 3.0

            if score > 0:
                scored_results.append((score, q))

        scored_results.sort(key=lambda item: (-item[0], -item[1].timestamp, item[1].id))
        return [q for _, q in scored_results]

    def get_quotes_between(self, start_ts, end_ts):
        return [q for q in self.quotes if start_ts <= q.timestamp <= end_ts]

    def get_tag_counts(self):
        counts = Counter()
        for quote in self.quotes:
            for tag in self.normalize_tags(quote.tags):
                counts[tag] += 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

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

    @staticmethod
    def _ensure_terminal_punctuation(text: str) -> str:
        if not text:
            return text
        stripped = text.rstrip()
        if not stripped:
            return text
        if stripped[-1] in ".!?…":
            return text
        return f"{stripped}."

    def add_quote(self, quote: Quote):
        quote.quote = self._ensure_terminal_punctuation(quote.quote)
        quote.tags = self.normalize_tags(quote.tags)
        self.quotes.append(quote)
        self._upsert_quote(quote)
        self._recalculate_stats()

    def update_quote(self, quote_id: int, quote_text: str, authors, context: str, tags=None):
        quote = self.get_quote_by_id(quote_id)
        if not quote:
            return None
        quote.quote = self._ensure_terminal_punctuation(quote_text)
        quote.authors = authors
        quote.context = context
        if tags is not None:
            quote.tags = self.normalize_tags(tags)
        else:
            quote.tags = self.normalize_tags(quote.tags)
        self._upsert_quote(quote)
        self._recalculate_stats()
        return quote

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

    def record_quote_anarchy_wins(self, quote_ids: List[int]) -> List[Quote]:
        updated_quotes: List[Quote] = []
        seen_ids = set()

        for raw_id in quote_ids or []:
            try:
                quote_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            if quote_id <= 0 or quote_id in seen_ids:
                continue
            seen_ids.add(quote_id)

            quote = self.get_quote_by_id(quote_id)
            if not quote:
                continue

            quote.stats = self.normalize_stats(quote.stats)
            quote.stats["anarchy_points"] += 1
            updated_quotes.append(quote)

        if updated_quotes:
            self._save()
        return updated_quotes

    def _upsert_quote(self, quote: Quote) -> None:
        quote.stats = self.normalize_stats(quote.stats)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, tags, stats)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.id,
                    quote.quote,
                    json.dumps(quote.authors, ensure_ascii=False),
                    quote.timestamp,
                    quote.context,
                    json.dumps(self.normalize_tags(quote.tags), ensure_ascii=False),
                    json.dumps(self.normalize_stats(quote.stats), ensure_ascii=False),
                ),
            )

    def _save(self):
        for quote in self.quotes:
            quote.stats = self.normalize_stats(quote.stats)
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO quotes
                (id, quote, authors, timestamp, context, tags, stats)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        q.id,
                        q.quote,
                        json.dumps(q.authors, ensure_ascii=False),
                        q.timestamp,
                        q.context,
                        json.dumps(self.normalize_tags(q.tags), ensure_ascii=False),
                        json.dumps(self.normalize_stats(q.stats), ensure_ascii=False),
                    )
                    for q in self.quotes
                ],
            )

        if os.path.exists(self.filepath):
            self.last_mtime = os.path.getmtime(self.filepath)
        logger.debug("Saved %s quotes to %s", len(self.quotes), self.filepath)

    def _recalculate_stats(self):
        self.total_quotes = len(self.quotes)
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
