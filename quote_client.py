"""Quote client wrapper for remote API or local SQLite fallback."""

import logging
import os
import re
from typing import List, Optional
from urllib.parse import urljoin

import requests

import qb_formats

logger = logging.getLogger(__name__)


class QuoteClient:
    """Client wrapper for quotes with local-db fallback."""

    def __init__(self, base_url: Optional[str] = None, db_path: str = "qb.db"):
        self.base_url = base_url.rstrip("/") if base_url else None
        self._local = None if self.base_url else qb_formats.QuoteBook(db_path)

    @property
    def is_remote(self) -> bool:
        return self.base_url is not None

    def reload(self) -> int:
        if self._local:
            return self._local.reload()
        return 200

    def get_all_quotes(self) -> List[qb_formats.Quote]:
        if self._local:
            return list(self._local.quotes)

        payload = self._get_json("/api/quotes")
        return [self._quote_from_dict(q) for q in payload.get("quotes", [])]

    def get_total_quotes(self) -> int:
        if self._local:
            return self._local.total_quotes

        payload = self._get_json("/api/quotes", params={"page": 1, "per_page": 1})
        total = payload.get("total", 0)
        return total

    def get_quote_page(
        self,
        speaker: Optional[str],
        page: int,
        per_page: int,
        order: str = "oldest",
    ):
        """Return a paginated slice of quotes plus page metadata."""
        normalized_order = (order or "oldest").strip().lower()
        if normalized_order not in ("oldest", "newest", "desc", "reverse"):
            normalized_order = "oldest"
        reverse_sort = normalized_order in ("newest", "desc", "reverse")
        if self._local:
            quotes = self._local.quotes
            if speaker:
                speaker_lower = speaker.lower()
                quotes = [
                    q
                    for q in quotes
                    if any(speaker_lower == author.lower() for author in q.authors)
                ]
            quotes = sorted(
                quotes, key=lambda q: (q.timestamp, q.id), reverse=reverse_sort
            )
            total = len(quotes)
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page
            end = start + per_page
            return quotes[start:end], page, total_pages

        params = {"page": page, "per_page": per_page}
        if speaker:
            params["speaker"] = speaker
        if normalized_order:
            params["order"] = normalized_order
        payload = self._get_json("/api/quotes", params=params)
        quotes = [self._quote_from_dict(q) for q in payload.get("quotes", [])]
        return quotes, payload.get("page", page), payload.get("total_pages", 1)

    def get_quote_by_id(self, quote_id: int) -> Optional[qb_formats.Quote]:
        if self._local:
            return self._local.get_quote_by_id(quote_id)

        payload = self._get_json(f"/api/quotes/{quote_id}")
        if not payload:
            return None
        return self._quote_from_dict(payload)

    def get_random_quote(self) -> Optional[qb_formats.Quote]:
        if self._local:
            return self._local.get_random_quote()

        payload = self._get_json("/api/quotes/random")
        if not payload:
            return None
        return self._quote_from_dict(payload)

    def get_latest_quote(self) -> Optional[qb_formats.Quote]:
        if self._local:
            if not self._local.quotes:
                return None
            return max(self._local.quotes, key=lambda q: q.id)

        payload = self._get_json("/api/latest")
        if not payload:
            return None
        return self._quote_from_dict(payload)

    def search_quotes(self, query: str) -> List[qb_formats.Quote]:
        if self._local:
            return self._local.search_quotes(query)

        payload = self._get_json("/api/search", params={"query": query})
        return [self._quote_from_dict(q) for q in payload.get("quotes", [])]

    def get_quotes_between(self, start_ts: int, end_ts: int) -> List[qb_formats.Quote]:
        if self._local:
            return self._local.get_quotes_between(start_ts, end_ts)

        payload = self._get_json(
            "/api/quotes/between", params={"start_ts": start_ts, "end_ts": end_ts}
        )
        return [self._quote_from_dict(q) for q in payload.get("quotes", [])]

    def get_speaker_counts(self):
        if self._local:
            return self._local.speaker_counts

        payload = self._get_json("/api/speakers")
        return [
            (item["speaker"], item["count"]) for item in payload.get("speakers", [])
        ]

    def add_quote(self, quote_text: str, authors, context: str, timestamp: int):
        """Insert a quote via local DB or remote API."""
        if self._local:
            new_quote = qb_formats.Quote(
                id=self._local.next_id(),
                quote=quote_text,
                authors=authors,
                timestamp=timestamp,
                context=context,
            )
            self._local.add_quote(new_quote)
            return new_quote

        payload = self._post_json(
            "/api/quotes",
            {
                "quote": quote_text,
                "authors": authors,
                "context": context,
                "timestamp": timestamp,
            },
        )
        return self._quote_from_dict(payload)

    def update_quote(self, quote_id: int, quote_text: str, authors, context: str):
        """Update a quote via local DB or remote API."""
        if self._local:
            return self._local.update_quote(
                quote_id=quote_id,
                quote_text=quote_text,
                authors=authors,
                context=context,
            )

        payload = self._put_json(
            f"/api/quotes/{quote_id}",
            {
                "quote": quote_text,
                "authors": authors,
                "context": context,
            },
        )
        if not payload:
            return None
        return self._quote_from_dict(payload)

    def record_battle(self, winner_id: int, loser_id: int):
        """Record a battle result and return updated quotes."""
        if self._local:
            winner = self._local.get_quote_by_id(winner_id)
            loser = self._local.get_quote_by_id(loser_id)
            if not winner or not loser:
                return None, None

            winner.stats["wins"] += 1
            winner.stats["battles"] += 1
            winner.stats["score"] += 1

            loser.stats["losses"] += 1
            loser.stats["battles"] += 1

            self._local._save()
            return winner, loser

        payload = self._post_json(
            "/api/battles", {"winner_id": winner_id, "loser_id": loser_id}
        )
        winner = self._quote_from_dict(payload.get("winner"))
        loser = self._quote_from_dict(payload.get("loser"))
        return winner, loser

    def parse_authors(self, raw: str):
        cleaned = re.sub(r"(?:,\s*)?\band\b\s*", ", ", raw, flags=re.IGNORECASE)
        return [a.strip() for a in cleaned.split(",") if a.strip()]

    @staticmethod
    def _quote_from_dict(payload: dict) -> qb_formats.Quote:
        return qb_formats.Quote(
            id=payload["id"],
            quote=payload["quote"],
            authors=payload.get("authors", []),
            timestamp=payload.get("timestamp", 0),
            context=payload.get("context", ""),
            stats=payload.get("stats", qb_formats.DEFAULT_STATS.copy()),
        )

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = self._url(path)
        if self.base_url:
            if params:
                logger.info("Quote client request: GET %s params=%s", url, params)
            else:
                logger.info("Quote client request: GET %s", url)
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def _post_json(self, path: str, payload: dict) -> dict:
        url = self._url(path)
        if self.base_url:
            logger.info("Quote client request: POST %s", url)
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def _put_json(self, path: str, payload: dict) -> dict:
        url = self._url(path)
        if self.base_url:
            logger.info("Quote client request: PUT %s", url)
        response = requests.put(url, json=payload, timeout=10)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))


def get_quote_client() -> QuoteClient:
    standalone = os.getenv("APP_STANDALONE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    api_url = os.getenv("QUOTE_API_URL")
    db_path = os.getenv("QUOTEBOOK_DB", "qb.db")

    if standalone:
        api_url = None

    client = QuoteClient(api_url, db_path)
    if client.is_remote:
        logger.info("Quote client: using API at %s", client.base_url)
    else:
        logger.info("Quote client: using local SQLite database (%s)", db_path)
    return client
