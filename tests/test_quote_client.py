import qb_formats
from quote_client import QuoteClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_quote_client_local_mode_total_count(tmp_path):
    db_path = tmp_path / "local.db"
    qb = qb_formats.QuoteBook(str(db_path))
    qb.add_quote(
        qb_formats.Quote(
            id=1,
            quote="One",
            authors=["Alice"],
            timestamp=1_700_000_000,
            context="",
        )
    )

    client = QuoteClient(base_url=None, db_path=str(db_path))
    assert client.get_total_quotes() == 1


def test_quote_client_remote_mode_total_count(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=10):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(
            {
                "quotes": [],
                "total": 4,
                "page": 1,
                "per_page": 1,
                "total_pages": 4,
            }
        )

    monkeypatch.setattr("quote_client.requests.get", fake_get)

    client = QuoteClient(base_url="http://api.example.com", db_path="unused.db")
    assert client.get_total_quotes() == 4
    assert captured["url"] == "http://api.example.com/api/quotes"
    assert captured["params"] == {"page": 1, "per_page": 1}


def test_quote_client_remote_mode_pagination(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=10):
        calls.append((url, params, timeout))
        return FakeResponse(
            {
                "quotes": [
                    {
                        "id": 2,
                        "quote": "Two",
                        "authors": ["Bob"],
                        "timestamp": 1_700_000_060,
                        "context": "",
                        "stats": {"wins": 0, "losses": 0, "battles": 0, "score": 0},
                    }
                ],
                "total": 2,
                "page": 2,
                "per_page": 1,
                "total_pages": 2,
            }
        )

    monkeypatch.setattr("quote_client.requests.get", fake_get)

    client = QuoteClient(base_url="http://api.example.com", db_path="unused.db")
    quotes, page, total_pages = client.get_quote_page(
        speaker=None,
        page=2,
        per_page=1,
        order="newest",
    )

    assert page == 2
    assert total_pages == 2
    assert len(quotes) == 1
    assert quotes[0].id == 2
    assert calls[0][1]["order"] == "newest"
