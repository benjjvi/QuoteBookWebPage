# Release Checklist

## Pre-release
- Confirm database backup exists and is restorable.
- Verify required env vars for selected features:
- `SECRET_KEY`
- `EDIT_PIN` (if edit UI should be enabled)
- `VAPID_PUBLIC_KEY` + `VAPID_PRIVATE_KEY` + `VAPID_EMAIL` (push)
- `WEEKLY_EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`/`WEEKLY_EMAIL_FROM` (weekly digest)
- Run regression tests: `pytest`.
- Run security scan: Bandit workflow.
- Confirm `/health` and `/health/details` are healthy in staging.

## Deploy
- Deploy web app and DB migration-compatible code together.
- If using split mode, deploy API service before web client rollout.
- Verify service worker update (`CACHE_VERSION`) when shipping static changes.

## Post-deploy Validation
- Open `/`, `/all_quotes`, `/random`, `/search`, `/stats`, `/timeline`.
- Verify API contract checks:
- `GET /api/quotes?page=1&per_page=1&order=oldest`
- `GET /api/quotes/random`
- `GET /api/ops/metrics`
- Validate add/edit quote flow and battle updates.
- Validate push/email subscription token flow.

## Rollback Plan
- Revert to previous app image/revision.
- Restore previous static assets if service worker behavior regressed.
- If schema/data issue occurs, restore SQLite backup and restart app.
- Disable weekly email (`WEEKLY_EMAIL_ENABLED=false`) and push keys if feature-specific failures occur.

## Monitoring Checklist
- Monitor application logs for:
- quote reload failures
- scheduler loop errors
- email delivery failures
- push delivery failures
- Track runtime counters from `/api/ops/metrics`:
- `weekly_digest_sent`, `weekly_digest_failure`
- `email_sent`, `email_failed`
- `push_sent`, `push_failed`, `push_pruned`
- Alert if health endpoints fail or error rates spike.
