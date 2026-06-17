# DHIS2 Index Usage Analyzer — Design Spec

**Date:** 2026-06-17
**Status:** Approved

---

## Overview

A small Flask-style web application (FastAPI) that surfaces PostgreSQL index usage statistics for a DHIS2 instance. The primary goal is to help administrators identify truly dead indexes — particularly in the `analytics_*` table family — so they can safely decide which indexes to disable during the analytics process.

Data is sourced from a `_index_usage_daily` table populated by a cron job twice daily, which snapshots `pg_stat_user_indexes`.

---

## Stack

| Component | Choice |
|---|---|
| Web framework | FastAPI |
| Templating | Jinja2 |
| CSS | Bootstrap 5 |
| Database driver | psycopg2 |
| Server | uvicorn |
| Config | Environment variables + `.env` for local dev |

FastAPI is chosen over Flask because it is native ASGI and pairs cleanly with uvicorn for the systemd deployment target.

---

## Source Table Schema

The `_index_usage_daily` table is populated by:

```sql
INSERT INTO _index_usage_daily
SELECT
  now(),
  d.stats_reset,
  s.schemaname,
  s.relname,
  s.indexrelname,
  s.indexrelid,
  s.idx_scan,
  s.idx_tup_read,
  s.idx_tup_fetch,
  pg_relation_size(s.indexrelid)
FROM pg_stat_user_indexes s
CROSS JOIN (
  SELECT min(stats_reset) AS stats_reset
  FROM pg_stat_database
) d;
```

Columns used by this application:

| Column | Notes |
|---|---|
| `snapshot_at` | Snapshot timestamp |
| `stats_reset` | Cumulative stats reset timestamp — changes when Postgres stats are reset |
| `schemaname` | Schema |
| `relname` | Table name |
| `indexrelname` | Index name |
| `indexrelid` | Index OID (stable identifier across snapshots) |
| `idx_scan` | Cumulative scan count since last `stats_reset` |
| `idx_tup_read` | Cumulative tuples read |
| `idx_tup_fetch` | Cumulative tuples fetched |
| `index_size_bytes` | Index size in bytes at snapshot time |

`idx_scan` is **cumulative**, not a daily rate. All delta calculations must account for stats resets.

---

## Data Model & Metrics

### Stats Reset Handling

Because `idx_scan` resets to zero whenever Postgres statistics are reset, the application defines a **current stats window** as the period from the most recent `stats_reset` timestamp to now. All primary metrics are computed within this window only.

Detection: a stats reset is identified when `stats_reset` changes between consecutive snapshots for the same `indexrelid`.

### Per-Index Metrics

| Metric | Definition |
|---|---|
| **Scans (current window)** | Latest `idx_scan` value within the current `stats_reset` window |
| **Median delta** | Median of per-snapshot scan deltas within the current window |
| **Snapshot count** | Number of snapshots recorded in the current window |
| **Stats reset flag** | Whether any reset occurred in the full history for this index |
| **Index size** | `pg_relation_size` from the most recent snapshot |

### Dead Index Definition

An index is classified as **dead** when:
- `idx_scan = 0` in the current stats window, AND
- At least 3 snapshots exist in the current window (to avoid false positives for newly-created indexes)

Indexes with fewer than 3 snapshots in the current window are shown with an **"Insufficient data"** flag rather than a dead classification.

### Usage Bands

| Band | Scan count (current window) |
|---|---|
| Dead | 0 |
| Low | 1 – 999 |
| Medium | 1,000 – 9,999 |
| High | 10,000 – 99,999 |
| Very High | 100,000+ |

---

## Analytics Index Handling

### Analytics Table Families

Analytics tables are partitioned. The four table families, identified by `relname` prefix:

| Family | Table pattern | Partition key |
|---|---|---|
| `analytics` | `analytics_<year>` | Calendar year |
| `analytics_completeness` | `analytics_completeness_<id>` | Numeric ID |
| `analytics_enrollment_<uid>` | `analytics_enrollment_<uid>` | Not further partitioned |
| `analytics_event_<uid>` | `analytics_event_<uid>_<year>` | Calendar year |

### Family Extraction

Family is derived from `relname` by stripping a trailing `_<numeric-suffix>`:

- `analytics_2021` → `analytics`
- `analytics_completeness_1962` → `analytics_completeness`
- `analytics_event_akcudav8fu7_2021` → `analytics_event_akcudav8fu7`
- `analytics_enrollment_akcudav8fu7` → `analytics_enrollment_akcudav8fu7` (no suffix to strip)

SQL expression: `regexp_replace(relname, '_[0-9]+$', '')`

### Special Index Types

Continuous analytics indexes (e.g., `in_id_ax_1974_PCLne`) have been disabled and may not appear in recent snapshots. They are shown if present in the data but require no special treatment — they will appear as dead if not recently scanned.

### Aggregated Family View

The analytics families page aggregates across all partitions in a family:

| Column | Aggregation |
|---|---|
| Partition count | COUNT(DISTINCT indexrelname) |
| Total scans | SUM(idx_scan) in current window |
| Total size | SUM(pg_relation_size) from latest snapshot |
| Dead partitions | COUNT where idx_scan = 0 in current window |

A family where **all** partitions are dead is flagged as a candidate for removal.

---

## Pages & Routes

### `/` — Overview

Key stats at a glance:
- Total indexes tracked
- Count of dead indexes
- Count of analytics families, and how many are fully dead
- Current window start date (last stats reset)
- Warning banner if a stats reset occurred in the recorded history

### `/dead` — Dead Indexes

Table of all zero-scan indexes in the current window with sufficient data. Columns: schema, table, index name, size, snapshot count, stats reset flag.

Filter toggle: **Analytics only** (restricts to `analytics_*` tables).

"Insufficient data" indexes shown in a separate section below the main table.

### `/indexes` — All Indexes

Full index list. Columns: schema, table, index name, band, scans (current window), median delta, size.

Filters:
- Usage band (multi-select)
- Analytics only toggle

Sortable by scan count and size.

### `/analytics` — Analytics Families

One row per family. Columns: family name, partition count, total scans, total size, dead partition count, fully dead flag.

Sorted by total scans ascending (least-used families first, since the primary goal is finding dead ones).

### `/analytics/{family}` — Family Detail

All individual partition indexes for a single family. Columns: index name, table (partition), scans, median delta, size, band, stats reset flag.

---

## Project Structure

```
tool-index-analyzer/
├── app/
│   ├── main.py                  # FastAPI app, route handlers
│   ├── db.py                    # psycopg2 connection helper
│   ├── queries.py               # all SQL queries
│   └── templates/
│       ├── base.html            # navbar, Bootstrap CDN
│       ├── overview.html
│       ├── dead.html
│       ├── indexes.html
│       ├── analytics.html
│       └── analytics_family.html
├── requirements.txt
├── .env.example
└── tool-index-analyzer.service  # systemd unit
```

---

## Configuration

```bash
# .env.example
DATABASE_URL=postgresql://postgres:postgres@localhost/postgres
```

In production, `DATABASE_URL` is set as a systemd environment variable. The app reads it via `os.environ` with no fallback — missing config fails fast at startup.

For local development, `python-dotenv` loads `.env` automatically.

---

## Deployment

**systemd unit** (`tool-index-analyzer.service`):

```ini
[Unit]
Description=DHIS2 Index Analyzer
After=network.target

[Service]
User=dhis2
WorkingDirectory=/opt/tool-index-analyzer
EnvironmentFile=/opt/tool-index-analyzer/.env
ExecStart=/opt/tool-index-analyzer/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Authentication and TLS are handled by the reverse proxy (nginx or Apache). The app does not implement its own auth.

---

## Out of Scope (First Iteration)

- Visual charts / time-series plots
- Index usage over time (delta history view)
- Multi-instance / multi-database support
- Export to CSV
- Any write operations (the app is read-only)
