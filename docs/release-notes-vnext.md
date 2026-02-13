# Release Notes (vNext)

## Added
- Expanded in-app API surface to match standalone API coverage.
- Runtime operations endpoint: `GET /api/ops/metrics`.
- Detailed health endpoint: `GET /health/details`.
- Startup runtime config validation warnings.
- Automated pytest suite and CI workflow for regression checks.
- Architecture inventory, API contract draft, deployment runbook, and release checklist docs.
- Monetisation scaffolding:
- `robots.txt` + `sitemap.xml` for indexability.
- `/support` and `/advertise` pages.
- Env-driven support links, sponsor fallback slots, optional AdSense slots, and disclosure text.

## Changed
- Fixed quote count consistency in local and remote client paths.
- Standardized `/api/quotes` response contract (`order`, pagination behavior).
- Added runtime counters for weekly digest, email delivery, and push delivery paths.

## Reliability
- Weekly digest scheduling now tracks skip/claim/failure metrics.
- Email and push delivery metrics now capture attempts, successes, and failures.

## Risk Notes
- Existing deployments relying on historical off-by-one total quote count will see corrected totals.
- Consumers should treat `per_page` as informative when pagination parameters are omitted.
