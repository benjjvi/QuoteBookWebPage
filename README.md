# QuoteBookWebPage

[![](https://img.shields.io/badge/quality-trust%20me%20bro-3C1)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/coverage-not%20much-F73)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/test-passing%20if%20you%20try%20a%20second%20time-3C1)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/code%20style-mix%20of%20tabs%20and%20spaces-F73)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/encoding-ÛT�--€™-08C)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/created%20an%20AGI%20by%20mistake-maybe-3C1)](https://github.com/sebmestrallet/absurd-badges)
[![Project line count](https://img.shields.io/badge/project%20lines-31330-0A84FF?style=flat-square)](#code-line-counts)

A simple **Flask‑powered web application** that lets you browse and share your Quote Book as a neat, user‑friendly web page.

This project takes your collection of quotes (like your **Spoons Quotes quote book**) and renders them in an interactive, searchable, and beautifully formatted website — perfect for sharing with friends or publishing online. Quotes are stored in a local SQLite database (`qb.db`).

---

## Features

- See quotes beautifully displayed in a web interface
- Built with **Flask** for simplicity and extensibility
- Frontend with HTML/CSS/JS in `templates/` and `static/`
- Python backend in `app.py`
- SQLite storage (`qb.db`) with automatic migration from `qb.qbf`
- Animated background canvas with a dark theme
- Optional weekly email digest (Monday 07:00 UK)
- Optional support/sponsorship monetisation blocks
- Optional AdSense placements (env-driven)
- Easily deploy locally or on hosting like **Render / Heroku / GitHub Pages (via static export)**

---

## Getting Started

### Prerequisites

Make sure you have the following installed:

- Python 3.8+
- pip (Python package manager)

### Setup

1. Clone the repo:

   ```git
   git clone https://github.com/benjjvi/QuoteBookWebPage.git
   cd QuoteBookWebPage
   ```

2. Install dependencies:

    ``` bash
   python -m pip install -r requirements.txt
   ```

3. Set up environment. There is an example version in `example.env`.

4. Set up your quote book. There is an example in `qb.qbf.template`.

   On first run, the app will automatically migrate `qb.qbf` into `qb.db` if the database is empty.

5. Run the app:

    ``` bash
   python app.py
   ```

6. Open your browser and navigate to 127.0.0.1:8040

### Run tests

```bash
python -m pip install -r requirements-dev.txt
pytest
```

### Optional launcher

Use the interactive launcher to pick client/server (and standalone mode):

```bash
python run.py
```

### Weekly email digest (optional)

You can send a weekly digest email every **Monday at 07:00 UK time**.

Set these environment variables:

```bash
WEEKLY_EMAIL_ENABLED=true
WEEKLY_EMAIL_FROM="quotes@example.com"   # optional if SMTP_USER is set
WEEKLY_SCHEDULER_MODE="auto"             # auto/thread/external
SMTP_HOST="smtp.example.com"
SMTP_PORT=587
SMTP_USER="smtp_username"
SMTP_PASS="smtp_password"
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_SEND_DELAY_SECONDS=0.25            # optional pacing between recipients
```

Recipients are now stored in SQLite (`qb.db`) table `weekly_email_recipients`.

```bash
sqlite3 qb.db "INSERT OR IGNORE INTO weekly_email_recipients (email, created_at) VALUES ('friend1@example.com', strftime('%s','now'));"
sqlite3 qb.db "INSERT OR IGNORE INTO weekly_email_recipients (email, created_at) VALUES ('friend2@example.com', strftime('%s','now'));"
```

Optional one-time migration seed from env:

```bash
WEEKLY_EMAIL_TO_SEED="friend1@example.com,friend2@example.com"
```

Notes:
- `WEEKLY_SCHEDULER_MODE=thread` runs the scheduler in-process (default on non-PythonAnywhere hosts).
- `WEEKLY_SCHEDULER_MODE=external` disables the in-process thread. Use a host scheduler/cron task to run:
  `python scripts/run_weekly_digest_once.py`
- In `external` mode, the app also performs a throttled due-check during normal requests, so Monday sends still trigger on active sites even without cron.
- On PythonAnywhere, prefer `external` mode and configure a Scheduled Task.
- `SMTP_SEND_DELAY_SECONDS` can reduce provider throttling/rate-limit issues for bulk sends.
- The app deduplicates the weekly run per Monday date in SQLite to avoid duplicate sends.
- If `OPENROUTER_KEY` is set, the digest body/subject are generated via the AI helper (API-style JSON output).

### Monetisation setup (optional)

Set these environment variables to enable support/sponsorship blocks and optional ads:

```bash
ROBOTS_DISALLOW_ALL=false            # set true only for private deployments
SUPPORT_URL="https://buymeacoffee.com/yourname"
SUPPORT_LABEL="Support Quote Book"
SPONSOR_CONTACT_URL="https://example.com/sponsor"
SPONSOR_CONTACT_EMAIL="sponsor@example.com"
AFFILIATE_DISCLOSURE="Disclosure: Some links may be affiliate links."
WEEKLY_DIGEST_SPONSOR_LINE="Sponsored by Acme Co. - https://example.com"
ADSENSE_CLIENT_ID="ca-pub-xxxxxxxxxxxxxxxx"
ADSENSE_SLOT_INLINE="0000000000"
ADSENSE_SLOT_FOOTER="0000000000"
GOOGLE_ADSENSE_ACCOUNT="ca-pub-xxxxxxxxxxxxxxxx"
```

Operational notes:
- `robots.txt` now references `/sitemap.xml` and is index-friendly unless `ROBOTS_DISALLOW_ALL=true`.
- Sponsorship fallback cards are shown when ad slots are not configured.

---

## Split API + Web (Optional)

You can run the quote data service separately and have the web app call it over HTTP. This keeps the Flask frontend as-is, but moves the quote book into a standalone API.

### 1. Start the API server

```bash
export QUOTEBOOK_DB=qb.db
export API_HOST=0.0.0.0
export API_PORT=8050
export CORS_ORIGIN=*

python api_server.py
```

### 2. Point the web app at the API

```bash
export QUOTE_API_URL=http://127.0.0.1:8050
python app.py
```

`run.py` behavior:
- If `APP_MODE=client` and `APP_STANDALONE=true`, `run.py` starts both `api_server.py` and `app.py`.
- In that split standalone mode, the web client is pointed at the local API (`http://127.0.0.1:$API_PORT`).
- If you launch `app.py` directly, `APP_STANDALONE=true` still means local SQLite mode.

Environment toggles:
- `APP_MODE=CLIENT|SERVER` selects what `run.py` launches.
- `APP_STANDALONE=true|false` controls split standalone behavior in `run.py` client mode.

---

## Project Structure

```
QuoteBookWebPage/
├── app.py                   # Flask app entrypoint
├── api_server.py            # Quote API service (optional)
├── run.py                   # Interactive launcher
├── quote_client.py          # API client + local fallback
├── tests/                   # Pytest coverage for smoke/integration flows
├── templates/               # HTML templates
├── static/                  # CSS/JS/SVG assets
├── qb_formats.py            # Quote storage (SQLite) + parsing logic
├── ai_helpers.py            # AI helpers
├── PATTERNS.py              # NSFW patterns
├── profanities.json         # NSFW patterns
├── qb.qbf.template          # Example quote format
├── qb.db                    # SQLite database (auto-created)
└── requirements.txt          # Python dependencies
```
---

## Screenshots

### Homepage

![Homepage view](./docs/screenshots/homepage.png)

### Browse Quotes

![Browse quotes view](./docs/screenshots/browse-quotes.png)

### Search

![Search view](./docs/screenshots/search.png)

### Quote Detail

![Quote detail view](./docs/screenshots/quote-detail.png)

---

## How It Works

The app:

- Loads quotes from SQLite (see `qb_formats.py`)
- Uses Flask routes (in `app.py`) to serve pages
- Renders content via Jinja templates in `templates/`
- Assets like CSS and JavaScript live inside `static/`

---

## Deploying

You can deploy this app easily:

- Heroku – standard Python deploy
- Render – deploy from GitHub with auto‑deploy
- GitHub Pages – if you export as static HTML (using a build step)
- PythonAnywhere - drag and drop install.

> ⚡ GitHub Pages only serves static content — if you choose this path, you’ll need to generate static HTML first.

## Operational docs

- Architecture and routes: `docs/architecture-route-inventory.md`
- API contract draft: `docs/api-contract-draft.md`
- Deployment runbook: `docs/deployment-runbook.md`
- Release checklist and rollback/monitoring: `docs/release-checklist.md`

---

<!-- LINE_COUNT_START -->
## Code Line Counts

Snapshot date: `2026-02-23`

- Total tracked lines (`.py`, `.js`, `.html`, `.css`): **31330**
- Python (`.py`): **14993**
- JavaScript (`.js`): **4315**
- HTML (`.html`): **4748**
- CSS (`.css`): **7274**
- Files counted: **109**

<details>
<summary>Per-file line counts</summary>

| File | Lines |
| --- | ---: |
| `PATTERNS.py` | 166 |
| `ai_helpers.py` | 614 |
| `api_errors.py` | 33 |
| `api_server.py` | 284 |
| `app.py` | 461 |
| `app_services.py` | 1918 |
| `blueprints/__init__.py` | 1 |
| `blueprints/api.py` | 36 |
| `blueprints/api_routes/__init__.py` | 1 |
| `blueprints/api_routes/games.py` | 564 |
| `blueprints/api_routes/notifications.py` | 159 |
| `blueprints/api_routes/quotes.py` | 501 |
| `blueprints/web.py` | 60 |
| `blueprints/web_routes/__init__.py` | 1 |
| `blueprints/web_routes/core.py` | 302 |
| `blueprints/web_routes/quotes.py` | 589 |
| `blueprints/web_routes/social.py` | 467 |
| `datetime_handler.py` | 134 |
| `ops.py` | 8 |
| `ops_control_gui.py` | 1652 |
| `pythonanywhere_wsgi.py` | 42 |
| `qb_formats.py` | 514 |
| `quote_anarchy.py` | 1575 |
| `quote_blackline.py` | 1314 |
| `quote_client.py` | 358 |
| `quote_who_said_it.py` | 1064 |
| `run.py` | 134 |
| `scripts/run_weekly_digest_once.py` | 67 |
| `static/assets/css/add-quote.css` | 260 |
| `static/assets/css/advertise.css` | 136 |
| `static/assets/css/ai-screenplay.css` | 115 |
| `static/assets/css/ai.css` | 151 |
| `static/assets/css/all-quotes.css` | 414 |
| `static/assets/css/battle.css` | 146 |
| `static/assets/css/blackline-rush.css` | 541 |
| `static/assets/css/calendar.css` | 200 |
| `static/assets/css/credits.css` | 137 |
| `static/assets/css/design-system.css` | 334 |
| `static/assets/css/edit-quote.css` | 216 |
| `static/assets/css/error.css` | 42 |
| `static/assets/css/footer.css` | 69 |
| `static/assets/css/games.css` | 170 |
| `static/assets/css/index.css` | 492 |
| `static/assets/css/mailbox.css` | 193 |
| `static/assets/css/main.css` | 362 |
| `static/assets/css/monetize.css` | 69 |
| `static/assets/css/privacy.css` | 184 |
| `static/assets/css/pwa.css` | 74 |
| `static/assets/css/quote-anarchy.css` | 703 |
| `static/assets/css/quote.css` | 229 |
| `static/assets/css/quotes-by-day.css` | 158 |
| `static/assets/css/search.css` | 248 |
| `static/assets/css/social.css` | 780 |
| `static/assets/css/stats.css` | 325 |
| `static/assets/css/support.css` | 96 |
| `static/assets/css/who-said-it.css` | 430 |
| `static/assets/js/background.js` | 133 |
| `static/assets/js/blackline-rush.js` | 760 |
| `static/assets/js/index.js` | 562 |
| `static/assets/js/pwa-sync.js` | 506 |
| `static/assets/js/quote-anarchy.js` | 898 |
| `static/assets/js/social.js` | 358 |
| `static/assets/js/theme.js` | 155 |
| `static/assets/js/who-said-it.js` | 646 |
| `static/offline.html` | 193 |
| `static/sw.js` | 297 |
| `stats_stopwords.py` | 59 |
| `templates/ad_slot.html` | 27 |
| `templates/add_quote.html` | 196 |
| `templates/advertise.html` | 88 |
| `templates/ai.html` | 97 |
| `templates/ai_screenplay.html` | 50 |
| `templates/all_quotes.html` | 455 |
| `templates/base.html` | 225 |
| `templates/battle.html` | 64 |
| `templates/blackline_rush.html` | 161 |
| `templates/calendar.html` | 76 |
| `templates/credits.html` | 118 |
| `templates/edit_index.html` | 167 |
| `templates/edit_quote.html` | 149 |
| `templates/error.html` | 22 |
| `templates/footer.html` | 53 |
| `templates/games.html` | 94 |
| `templates/index.html` | 241 |
| `templates/mailbox.html` | 116 |
| `templates/privacy.html` | 98 |
| `templates/pwa.html` | 224 |
| `templates/quote.html` | 259 |
| `templates/quote_anarchy.html` | 199 |
| `templates/quotes_by_day.html` | 57 |
| `templates/search.html` | 265 |
| `templates/social.html` | 263 |
| `templates/social_post.html` | 189 |
| `templates/stats.html` | 346 |
| `templates/support.html` | 63 |
| `templates/unsubscribe.html` | 44 |
| `templates/who_said_it.html` | 149 |
| `tests/conftest.py` | 203 |
| `tests/test_api_contract.py` | 120 |
| `tests/test_auth_and_tokens.py` | 87 |
| `tests/test_blackline_rush.py` | 162 |
| `tests/test_config_validation.py` | 42 |
| `tests/test_mailbox.py` | 76 |
| `tests/test_quote_anarchy.py` | 273 |
| `tests/test_quote_client.py` | 160 |
| `tests/test_scheduler_and_metrics.py` | 293 |
| `tests/test_smoke_routes.py` | 180 |
| `tests/test_social_engagement.py` | 105 |
| `tests/test_who_said_it.py` | 214 |

</details>

---

<!-- LINE_COUNT_END -->
## Contributing

1. Fork it!
2. Create your feature branch: git checkout -b feature/YourIdea
3. Commit your changes
4. Push to your fork
5. Open a Pull Request

---

## Thanks

Made with coffee and quotes.
This project uses icons from [Lucide](https://lucide.dev). Profanities were gathered from [this repo](https://github.com/dsojevic/profanity-list) by [dsojevic](https://github.com/dsojevic).
Happy quoting! ✨
