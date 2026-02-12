# Deployment Runbook

## Topology Options
- `Single-process`: run `app.py` with local SQLite access.
- `Split mode`: run `api_server.py` and point web app to `QUOTE_API_URL`.

## Option A: Single-process Deployment
1. Set env vars:
- `IS_PROD=true`
- `HOST=0.0.0.0`
- `PORT=<port>`
- `QUOTEBOOK_DB=qb.db`
2. Start app: `python app.py`.
3. Verify:
- `GET /health`
- `GET /health/details`
- `GET /api/quotes?page=1&per_page=1`

## Option B: Split Deployment
1. Start API service:
- `API_HOST=0.0.0.0`
- `API_PORT=8050`
- `QUOTEBOOK_DB=qb.db`
- `python api_server.py`
2. Start web app with API target:
- `QUOTE_API_URL=http://<api-host>:8050`
- `APP_STANDALONE=false`
- `python app.py`
3. Verify:
- API health: `GET http://<api-host>:8050/health`
- Web health: `GET /health`
- Web API passthrough behavior: `GET /api/quotes?page=1&per_page=1`

## Weekly Digest Notes
- Scheduler runs in Flask app process.
- Avoid running multiple scheduler-active replicas without a clear leader strategy.
- Confirm recipients table is populated before expecting sends.

## Operational Commands
- Run tests: `pytest`
- Run launcher locally: `python run.py`
