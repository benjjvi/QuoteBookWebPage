# QuoteBookWP Architecture and Route Inventory

## Current Architecture
- Flask monolith (`app.py`) split into two blueprints:
- `blueprints/web.py` for HTML pages.
- `blueprints/api.py` for JSON API and subscription endpoints.
- Optional split deployment mode:
- `api_server.py` can run standalone quote API.
- `quote_client.py` selects local SQLite (`qb_formats.QuoteBook`) or remote API based on env.
- Primary persistence is SQLite (`qb.db`) for:
- quotes
- push subscriptions
- weekly email recipients
- scheduled job run keys

## Core Runtime Constraints
- Weekly digest scheduler runs inside Flask process (`AppServices.start_weekly_email_scheduler`).
- Push depends on both `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`.
- Weekly digest depends on SMTP settings and recipient table.
- PWA offline cache and IndexedDB sync depend on `/api/quotes` pagination contract.

## Web Routes (`blueprints/web.py`)
- `/`
- `/add_quote`
- `/ai`
- `/ai_screenplay`
- `/ai_screenplay_render`
- `/battle`
- `/random`
- `/quote/<quote_id>`
- `/quote/<quote_id>/edit`
- `/edit`
- `/all_quotes`
- `/search`
- `/social`
- `/social/author/<author_name>`
- `/social/quote/<quote_id>`
- `/social/quote/<quote_id>/react` (`POST`)
- `/social/quote/<quote_id>/comment` (`POST`)
- `/stats`
- `/timeline/<year>/<month>`
- `/timeline/day/<timestamp>`
- `/pwa`
- `/offline`
- `/support`
- `/advertise`
- `/sitemap.xml`
- `/health`
- `/health/details`
- `/credits`
- `/privacy`
- `/sw.js`
- `/manifest.webmanifest`
- `/robots.txt`

## API Routes (`blueprints/api.py`)
- `/api/latest`
- `/api/speakers`
- `/api/quotes` (`GET`, `POST`)
- `/api/quotes/random`
- `/api/quotes/<quote_id>` (`GET`, `PUT`)
- `/api/quotes/between`
- `/api/search`
- `/api/battles`
- `/api/ops/metrics`
- `/api/push/token`
- `/api/push/subscribe`
- `/api/push/unsubscribe`
- `/api/email/token`
- `/api/email/subscribe`
