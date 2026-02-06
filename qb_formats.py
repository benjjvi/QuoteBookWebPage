import json
import logging
import os
import random
import re
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
    def __init__(self, filepath: str = "qb.qbf"):
        self.filepath = filepath
        self.last_mtime = 0
        self.quotes: List[Quote] = []

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

    def _load(self):
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"{self.filepath} does not exist")

        with open(self.filepath, "r", encoding="utf-8") as f:
            raw_quotes = json.load(f)

        self.quotes = [
            Quote(
                id=q["id"],
                quote=q["quote"],
                authors=q.get("authors", []),
                timestamp=q.get("timestamp", 0),
                context=q.get("context", ""),
                stats=q.get("stats", DEFAULT_STATS.copy()),
            )
            for q in raw_quotes
        ]

        self.last_mtime = os.path.getmtime(self.filepath)
        logger.debug("Loaded %s quotes from %s", len(self.quotes), self.filepath)

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
        self._save()
        self.reload(force=True)

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

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(
                [q.__dict__ for q in self.quotes],
                f,
                ensure_ascii=False,
                indent=4,
            )

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
