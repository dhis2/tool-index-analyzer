# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only FastAPI web app that surfaces PostgreSQL index-usage statistics for a DHIS2 instance. Its purpose is helping admins identify truly *dead* indexes — especially in the partitioned `analytics_*` table family — so they can safely disable them during the analytics process. Pages are server-rendered Jinja2 + Bootstrap 5 (CDN); there is no JS framework and no client-side state.

## Commands

```bash
# Install deps (Python 3.10+)
.venv/bin/pip install -r requirements.txt

# Run dev server (reads .env automatically via python-dotenv)
.venv/bin/uvicorn app.main:app --reload

# Run all tests
.venv/bin/pytest

# Run a single test file / test
.venv/bin/pytest tests/test_queries.py
.venv/bin/pytest tests/test_queries.py::test_get_dead_indexes_classification_correct
```

There is no lint/format tooling configured.

## Tests require a live database

Every test hits a real PostgreSQL connection (`DATABASE_URL`) — there are no mocks or fixtures, and the query tests assert against real data (`total_indexes > 0`, families exist, etc.). You must have a populated `_index_usage_daily` table to run the suite. Copy `.env.example` to `.env` and set `DATABASE_URL`. The app reads `os.environ["DATABASE_URL"]` with **no fallback** — missing config fails fast at startup (and tests error at import time via `app/db.py`).

## Architecture

Three-layer split, all under `app/`:

- **`app/main.py`** — FastAPI app + route handlers. Routes are thin: call a `queries.*` function, pass the result to a template. Also registers two Jinja filters (`format_bytes`, `format_number`).
- **`app/queries.py`** — *all* SQL lives here, one function per query. This is where the domain logic is.
- **`app/db.py`** — single `get_connection()` context manager using psycopg2 with `RealDictCursor` (every row comes back as a `dict`).
- **`app/templates/`** — Jinja2; `base.html` provides the navbar + Bootstrap CDN, the rest extend it.

Routes → queries → templates. There is no ORM, no model layer, no service layer.

## Domain model — read this before touching `queries.py`

The data source is `_index_usage_daily`, populated twice daily by an external cron job snapshotting `pg_stat_user_indexes`. Key columns: `snapshot_at`, `stats_reset`, `relname` (table), `indexrelname` (index), `indexrelid` (stable OID), `idx_scan` (cumulative), `index_size_bytes`, `index_columns` (index definition minus names, e.g. `btree (uidlevel4)`; NULL for rows before 2026-09-29).

**Analytics indexes are recreated on every analytics run** with a new `indexrelid` and a randomly suffixed `indexrelname` (on DHIS2 2.43 the name also omits the column). So per-`indexrelid` metrics only cover the time since the last rebuild; following a logical index across rebuilds requires grouping on (table family, `index_columns`). The current queries still key on `indexrelid` and need reworking for this.

Critical invariants the queries depend on:

- **`idx_scan` is cumulative, not a rate**, and resets to 0 whenever Postgres stats are reset. The "current stats window" = rows where `stats_reset = MAX(stats_reset)`. All primary metrics are computed within this window only.
- **Dead index** = `idx_scan = 0` in the current window AND `snapshots >= 3`. Fewer than 3 snapshots → classified **"insufficient data"** (shown separately), never dead. The `>= 3` guard avoids flagging newly-created indexes.
- **Usage bands**: dead=0, low=1–999, medium=1,000–9,999, high=10,000–99,999, very_high=100,000+. The valid set is enforced in `get_all_indexes` (`_VALID_BANDS`) — keep it in sync with the `CASE` expression and the `all_bands` list in `main.py`.
- **Analytics family extraction**: `regexp_replace(relname, '_[0-9]+$', '')` strips a trailing numeric partition suffix (`analytics_2021` → `analytics`). Used everywhere analytics tables are grouped.

### Two query-writing conventions that will bite you

1. **`LIKE 'analytics%'` must be written `LIKE 'analytics%%'`** in these SQL strings, because psycopg2 treats `%` as a parameter placeholder. A single `%` will raise at execute time.
2. Every query starts from a shared **`_BOUNDS_CTE`** that computes `latest_snap` / `current_reset` in one pass. Queries then seek rows via `snapshot_at = latest_snap` rather than `DISTINCT ON` over the full table — this is a deliberate performance choice (index seek over ~119k rows vs. a scan of ~545k). Follow this pattern for new queries.

## Deployment

Runs under systemd (`tool-index-analyzer.service`) as `uvicorn app.main:app` on port 8000, with `DATABASE_URL` supplied via `EnvironmentFile`. Auth and TLS are handled by an upstream reverse proxy — the app implements neither. The app is strictly read-only; do not add write queries.

## Reference docs

`docs/superpowers/specs/2026-06-17-dhis2-index-analyzer-design.md` is the authoritative design spec (full table schema, metric definitions, route-by-route behavior). `docs/superpowers/plans/` holds the implementation plan.
