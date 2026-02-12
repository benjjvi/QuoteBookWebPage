# QuoteBookWP API Contract (Draft)

## Conventions
- Content type: `application/json`
- Errors return `{ "error": "..." }` with HTTP 4xx/5xx.
- Quote payload shape:
```json
{
  "id": 1,
  "quote": "Example quote.",
  "authors": ["Alice", "Bob"],
  "timestamp": 1735680000,
  "context": "Optional context",
  "stats": { "wins": 0, "losses": 0, "battles": 0, "score": 0 }
}
```

## Read Endpoints
- `GET /api/latest`
- Response: quote payload or `404` if none.

- `GET /api/speakers`
- Response:
```json
{ "speakers": [{ "speaker": "Alice", "count": 12 }] }
```

- `GET /api/quotes?speaker=&order=&page=&per_page=`
- `order`: `oldest | newest | desc | reverse` (invalid values coerce to `oldest`).
- Pagination only applies when both `page` and `per_page > 0` are provided.
- Response:
```json
{
  "quotes": [],
  "total": 0,
  "page": 1,
  "per_page": 0,
  "total_pages": 1,
  "order": "oldest"
}
```

- `GET /api/quotes/random`
- Response: quote payload or `404` if none.

- `GET /api/quotes/<quote_id>`
- Response: quote payload or `404`.

- `GET /api/quotes/between?start_ts=&end_ts=`
- Response: `{ "quotes": [...] }`.

- `GET /api/search?query=`
- Response: `{ "quotes": [...], "query": "..." }`.

- `GET /api/ops/metrics`
- Response: runtime counters for push/email/weekly scheduler events.

## Mutation Endpoints
- `POST /api/quotes`
- Request:
```json
{ "quote": "Text", "authors": ["Alice"], "context": "", "timestamp": 1735680000 }
```
- `timestamp` optional (server current UK time if omitted).
- Response: created quote payload (`201`).

- `PUT /api/quotes/<quote_id>`
- Request:
```json
{ "quote": "Updated", "authors": ["Alice"], "context": "Updated context" }
```
- Response: updated quote payload (`200`) or `404`.

- `POST /api/battles`
- Request:
```json
{ "winner_id": 1, "loser_id": 2 }
```
- Response:
```json
{ "winner": { ... }, "loser": { ... } }
```

## Subscription Endpoints
- `GET /api/push/token`
- `POST /api/push/subscribe`
- `POST /api/push/unsubscribe`
- `GET /api/email/token`
- `POST /api/email/subscribe`

### Token Rules
- Push/email subscription writes require matching session token.
- Invalid token returns `403`.
