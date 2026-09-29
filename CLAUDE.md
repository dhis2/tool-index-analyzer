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

**Analytics indexes are recreated on every analytics run** with a new `indexrelid` and a randomly suffixed `indexrelname` (on DHIS2 2.41+ the name also omits the column, DHIS2-22186). Per-`indexrelid` counters therefore only cover the time since the last rebuild.

How `queries.py` handles this:

- **Logical index** = (schema, table, `index_key`). For analytics tables `index_key` is the indexed column(s): from `index_columns` (quotes and the `btree (...)` wrapper stripped) or, for rows before 2026-09-29, parsed from the 2.40-style name `in_<column>_ax_...` (with `_lower` -> `lower(col)`, `_desc` -> `col DESC NULLS LAST`, `dx_co`/`dx_ao` -> `dx, co`/`dx, ao`, matching what `pg_get_indexdef` gives). For other tables it is the index name, which is stable.
- **Scans are accumulated across all stats resets and rebuilds**: per `indexrelid`, the first observation counts in full and each later snapshot adds the increase, or the whole counter if it went down (reset). There is no "current stats window" any more.
- **Dead** = 0 accumulated scans AND >= 3 snapshots; fewer snapshots is "insufficient data".
- Only logical indexes present in the **latest snapshot** are reported, so indexes that were dropped or are no longer built (e.g. `analytics.table.skip_index`) disappear.
- **Performance**: one `_SUMMARY_SQL` builds all logical-index rows. It walks the `(indexrelid, snapshot_at DESC)` index with `LEAD()` so the full history (~7.6M rows on ASC) is never sorted; ~25 s cold at that scale. The result is cached per latest `snapshot_at` and refreshed by a background thread in `main.py`; the page functions only filter/aggregate the cached rows in Python. Do not add per-page SQL over the full history. `avg_delta` is a mean per snapshot interval; an exact median would need the full sort this design avoids.
- `LIKE 'analytics%%'`: psycopg2 treats `%` as a placeholder when parameters are passed; keep `%%` in SQL that may be executed with parameters.

## Deployment

Runs under systemd (`tool-index-analyzer.service`) as `uvicorn app.main:app` on port 8000, with `DATABASE_URL` supplied via `EnvironmentFile`. Auth and TLS are handled by an upstream reverse proxy — the app implements neither. The app is strictly read-only; do not add write queries.

## Reference docs

`docs/superpowers/specs/2026-06-17-dhis2-index-analyzer-design.md` is the authoritative design spec (full table schema, metric definitions, route-by-route behavior). `docs/superpowers/plans/` holds the implementation plan.
