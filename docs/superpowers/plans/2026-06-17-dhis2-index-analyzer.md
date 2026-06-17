# DHIS2 Index Analyzer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI web app that renders server-side HTML tables analyzing PostgreSQL index usage from `_index_usage_daily`, helping DHIS2 admins identify dead and underused indexes.

**Architecture:** Five server-rendered FastAPI routes backed by psycopg2 queries against `_index_usage_daily`; all metrics computed within the current stats-reset window; Jinja2 + Bootstrap 5 for UI; no JavaScript frameworks.

**Tech Stack:** Python 3.10+, FastAPI 0.111+, Jinja2 3.1+, Bootstrap 5.3 (CDN), psycopg2-binary 2.9+, uvicorn 0.29+, python-dotenv 1.0+, pytest 8.2+, httpx 0.27+

## Global Constraints

- Python 3.10+
- `DATABASE_URL` env var required; app must fail fast at startup if missing (no default)
- Local dev connection: `postgresql://postgres:postgres@localhost/postgres` in `.env` file
- **Current stats window** = rows where `stats_reset = MAX(stats_reset) FROM _index_usage_daily`
- **Dead index** = `idx_scan = 0` in current window AND `snapshots >= 3`
- **Insufficient data** = `idx_scan = 0` AND `snapshots < 3` (shown separately, not classified dead)
- **Usage bands**: dead=0, low=1–999, medium=1000–9999, high=10000–99999, very_high=100000+
- **Family extraction** SQL: `regexp_replace(relname, '_[0-9]+$', '')`
- All DB rows returned as `dict` via `psycopg2.extras.RealDictCursor`
- `LIKE 'analytics%'` literals in psycopg2 SQL strings must be written as `LIKE 'analytics%%'`
- Bootstrap 5.3.3 served from CDN — no local static assets
- App is entirely read-only; no write queries

---

### Task 1: Project Scaffold

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.env` (local only, not committed)
- Create: `app/__init__.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Produces: installable Python environment with all dependencies

- [ ] **Step 1: Initialise git and create .gitignore**

```bash
cd /home/jason/PycharmProjects/tool-index-analyzer
git init
```

Create `.gitignore`:
```
.venv/
.env
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
.idea/
```

- [ ] **Step 2: Create requirements.txt**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
psycopg2-binary==2.9.9
jinja2==3.1.4
python-dotenv==1.0.1
pytest==8.2.2
httpx==0.27.0
```

- [ ] **Step 3: Install dependencies**

```bash
.venv/bin/pip install -r requirements.txt
```

Expected: all packages install without error; last line shows `Successfully installed ...`

- [ ] **Step 4: Create config files and package stubs**

Create `.env.example`:
```
DATABASE_URL=postgresql://postgres:postgres@localhost/postgres
```

Create `.env` (local dev, never committed):
```
DATABASE_URL=postgresql://postgres:postgres@localhost/postgres
```

Create `app/__init__.py` — empty file.

Create `tests/__init__.py` — empty file.

- [ ] **Step 5: Initial commit**

```bash
git add .gitignore requirements.txt .env.example app/__init__.py tests/__init__.py docs/
git commit -m "chore: project scaffold"
```

---

### Task 2: Database Connection

**Files:**
- Create: `app/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces: `get_connection()` — context manager yielding a `psycopg2` connection with `RealDictCursor` as default cursor factory

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:
```python
from app.db import get_connection


def test_connection_opens_and_closes():
    with get_connection() as conn:
        assert not conn.closed
    assert conn.closed


def test_can_execute_query():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS val")
            row = cur.fetchone()
    assert row["val"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_db.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `app.db` does not exist yet.

- [ ] **Step 3: Implement db.py**

Create `app/db.py`:
```python
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]


@contextmanager
def get_connection():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_db.py -v
```

Expected:
```
tests/test_db.py::test_connection_opens_and_closes PASSED
tests/test_db.py::test_can_execute_query PASSED
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: database connection helper"
```

---

### Task 3: Overview and Dead Index Queries

**Files:**
- Create: `app/queries.py`
- Create: `tests/test_queries.py`

**Interfaces:**
- Produces:
  - `get_overview_stats() -> dict` — keys: `total_indexes`, `dead_count`, `analytics_family_count`, `dead_analytics_count`, `current_reset` (datetime), `latest_snapshot` (datetime), `had_reset` (bool)
  - `get_dead_indexes(analytics_only: bool = False) -> tuple[list[dict], list[dict]]` — returns `(dead, insufficient)` where each dict has keys: `schemaname`, `relname`, `indexrelname`, `idx_scan`, `index_size_bytes`, `stats_reset`, `snapshots`, `classification`

- [ ] **Step 1: Write failing tests**

Create `tests/test_queries.py`:
```python
from app.queries import get_overview_stats, get_dead_indexes


def test_get_overview_stats_returns_expected_shape():
    stats = get_overview_stats()
    for key in ("total_indexes", "dead_count", "analytics_family_count",
                "dead_analytics_count", "current_reset", "latest_snapshot", "had_reset"):
        assert key in stats, f"missing key: {key}"
    assert stats["total_indexes"] > 0
    assert stats["dead_count"] >= 0
    assert stats["analytics_family_count"] >= 0
    assert isinstance(stats["had_reset"], bool)


def test_get_dead_indexes_returns_two_lists():
    dead, insufficient = get_dead_indexes()
    assert isinstance(dead, list)
    assert isinstance(insufficient, list)


def test_get_dead_indexes_all_have_zero_scans():
    dead, insufficient = get_dead_indexes()
    for row in dead + insufficient:
        assert row["idx_scan"] == 0


def test_get_dead_indexes_classification_correct():
    dead, insufficient = get_dead_indexes()
    for row in dead:
        assert row["snapshots"] >= 3
        assert row["classification"] == "dead"
    for row in insufficient:
        assert row["snapshots"] < 3
        assert row["classification"] == "insufficient_data"


def test_get_dead_indexes_analytics_only():
    dead, insufficient = get_dead_indexes(analytics_only=True)
    for row in dead + insufficient:
        assert row["relname"].startswith("analytics")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_queries.py -v
```

Expected: `ImportError` — `app.queries` does not exist yet.

- [ ] **Step 3: Implement get_overview_stats and get_dead_indexes in queries.py**

Create `app/queries.py`:
```python
from app.db import get_connection


def get_overview_stats() -> dict:
    sql = """
    WITH current_reset AS (
        SELECT MAX(stats_reset) AS reset_time FROM _index_usage_daily
    ),
    in_window AS (
        SELECT * FROM _index_usage_daily
        WHERE stats_reset = (SELECT reset_time FROM current_reset)
    ),
    latest AS (
        SELECT DISTINCT ON (indexrelid)
            indexrelid, relname, idx_scan
        FROM in_window
        ORDER BY indexrelid, snapshot_at DESC
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM in_window
        GROUP BY indexrelid
    ),
    had_reset AS (
        SELECT COUNT(DISTINCT stats_reset) > 1 AS flag
        FROM _index_usage_daily
    )
    SELECT
        COUNT(l.*) AS total_indexes,
        COUNT(l.*) FILTER (
            WHERE l.idx_scan = 0 AND sc.snapshots >= 3
        ) AS dead_count,
        COUNT(DISTINCT regexp_replace(l.relname, '_[0-9]+$', ''))
            FILTER (WHERE l.relname LIKE 'analytics%%') AS analytics_family_count,
        COUNT(l.*) FILTER (
            WHERE l.relname LIKE 'analytics%%'
            AND l.idx_scan = 0
            AND sc.snapshots >= 3
        ) AS dead_analytics_count,
        (SELECT reset_time FROM current_reset) AS current_reset,
        (SELECT MAX(snapshot_at) FROM _index_usage_daily) AS latest_snapshot,
        (SELECT flag FROM had_reset) AS had_reset
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return dict(cur.fetchone())


def get_dead_indexes(
    analytics_only: bool = False,
) -> tuple[list[dict], list[dict]]:
    analytics_filter = "AND l.relname LIKE 'analytics%%'" if analytics_only else ""
    sql = f"""
    WITH current_reset AS (
        SELECT MAX(stats_reset) AS reset_time FROM _index_usage_daily
    ),
    in_window AS (
        SELECT * FROM _index_usage_daily
        WHERE stats_reset = (SELECT reset_time FROM current_reset)
    ),
    latest AS (
        SELECT DISTINCT ON (indexrelid)
            indexrelid, schemaname, relname, indexrelname,
            idx_scan, index_size_bytes, stats_reset
        FROM in_window
        ORDER BY indexrelid, snapshot_at DESC
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM in_window
        GROUP BY indexrelid
    )
    SELECT
        l.schemaname,
        l.relname,
        l.indexrelname,
        l.idx_scan,
        l.index_size_bytes,
        l.stats_reset,
        sc.snapshots,
        CASE WHEN sc.snapshots >= 3 THEN 'dead' ELSE 'insufficient_data' END AS classification
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    WHERE l.idx_scan = 0
    {analytics_filter}
    ORDER BY l.index_size_bytes DESC NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
    dead = [r for r in rows if r["classification"] == "dead"]
    insufficient = [r for r in rows if r["classification"] == "insufficient_data"]
    return dead, insufficient
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/python -m pytest tests/test_queries.py -v
```

Expected:
```
tests/test_queries.py::test_get_overview_stats_returns_expected_shape PASSED
tests/test_queries.py::test_get_dead_indexes_returns_two_lists PASSED
tests/test_queries.py::test_get_dead_indexes_all_have_zero_scans PASSED
tests/test_queries.py::test_get_dead_indexes_classification_correct PASSED
tests/test_queries.py::test_get_dead_indexes_analytics_only PASSED
5 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/queries.py tests/test_queries.py
git commit -m "feat: overview and dead index queries"
```

---

### Task 4: All-Indexes and Analytics Family Queries

**Files:**
- Modify: `app/queries.py`
- Modify: `tests/test_queries.py`

**Interfaces:**
- Consumes: `get_connection()` from `app.db`
- Produces:
  - `get_all_indexes(bands: list[str] | None = None, analytics_only: bool = False) -> list[dict]` — each dict has keys: `schemaname`, `relname`, `indexrelname`, `idx_scan`, `median_delta`, `index_size_bytes`, `snapshots`, `band`
  - `get_analytics_families() -> list[dict]` — each dict has keys: `family`, `partition_count`, `total_scans`, `total_size_bytes`, `dead_partition_count`, `fully_dead`
  - `get_analytics_family_detail(family: str) -> list[dict]` — each dict has keys: `schemaname`, `relname`, `indexrelname`, `idx_scan`, `median_delta`, `index_size_bytes`, `snapshots`, `band`, `had_reset`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queries.py`:
```python
from app.queries import get_all_indexes, get_analytics_families, get_analytics_family_detail

VALID_BANDS = {"dead", "low", "medium", "high", "very_high"}


def test_get_all_indexes_returns_list_of_dicts():
    rows = get_all_indexes()
    assert isinstance(rows, list)
    assert len(rows) > 0
    for key in ("schemaname", "relname", "indexrelname", "idx_scan",
                "median_delta", "index_size_bytes", "snapshots", "band"):
        assert key in rows[0], f"missing key: {key}"


def test_get_all_indexes_bands_are_valid():
    rows = get_all_indexes()
    for row in rows:
        assert row["band"] in VALID_BANDS


def test_get_all_indexes_band_filter():
    rows = get_all_indexes(bands=["dead"])
    assert all(r["band"] == "dead" for r in rows)


def test_get_all_indexes_analytics_only():
    rows = get_all_indexes(analytics_only=True)
    assert all(r["relname"].startswith("analytics") for r in rows)


def test_get_analytics_families_returns_expected_shape():
    families = get_analytics_families()
    assert isinstance(families, list)
    assert len(families) > 0
    for key in ("family", "partition_count", "total_scans",
                "total_size_bytes", "dead_partition_count", "fully_dead"):
        assert key in families[0], f"missing key: {key}"


def test_get_analytics_families_all_start_with_analytics():
    families = get_analytics_families()
    for f in families:
        assert f["family"].startswith("analytics")


def test_get_analytics_family_detail_returns_rows():
    families = get_analytics_families()
    first_family = families[0]["family"]
    rows = get_analytics_family_detail(first_family)
    assert isinstance(rows, list)
    assert len(rows) > 0
    for key in ("schemaname", "relname", "indexrelname", "idx_scan",
                "median_delta", "index_size_bytes", "snapshots", "band", "had_reset"):
        assert key in rows[0], f"missing key: {key}"


def test_get_analytics_family_detail_all_match_family():
    families = get_analytics_families()
    first_family = families[0]["family"]
    rows = get_analytics_family_detail(first_family)
    import re
    for row in rows:
        extracted = re.sub(r'_[0-9]+$', '', row["relname"])
        assert extracted == first_family
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_queries.py -v -k "all_indexes or analytics"
```

Expected: `ImportError` — functions not yet defined.

- [ ] **Step 3: Implement the three new query functions**

Append to `app/queries.py`:
```python
_VALID_BANDS = {"dead", "low", "medium", "high", "very_high"}


def get_all_indexes(
    bands: list[str] | None = None,
    analytics_only: bool = False,
) -> list[dict]:
    if bands:
        invalid = set(bands) - _VALID_BANDS
        if invalid:
            raise ValueError(f"Invalid bands: {invalid}")

    analytics_filter = "AND l.relname LIKE 'analytics%%'" if analytics_only else ""
    band_filter = "WHERE band = ANY(%(bands)s)" if bands else ""
    params: dict = {"bands": list(bands)} if bands else {}

    sql = f"""
    WITH current_reset AS (
        SELECT MAX(stats_reset) AS reset_time FROM _index_usage_daily
    ),
    in_window AS (
        SELECT * FROM _index_usage_daily
        WHERE stats_reset = (SELECT reset_time FROM current_reset)
    ),
    latest AS (
        SELECT DISTINCT ON (indexrelid)
            indexrelid, schemaname, relname, indexrelname,
            idx_scan, index_size_bytes
        FROM in_window
        ORDER BY indexrelid, snapshot_at DESC
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM in_window
        GROUP BY indexrelid
    ),
    deltas AS (
        SELECT
            indexrelid,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta) AS median_delta
        FROM (
            SELECT
                indexrelid,
                idx_scan - LAG(idx_scan) OVER (
                    PARTITION BY indexrelid ORDER BY snapshot_at
                ) AS delta
            FROM in_window
        ) d
        WHERE delta IS NOT NULL AND delta >= 0
        GROUP BY indexrelid
    ),
    classified AS (
        SELECT
            l.schemaname,
            l.relname,
            l.indexrelname,
            l.idx_scan,
            COALESCE(d.median_delta, 0)::bigint AS median_delta,
            l.index_size_bytes,
            sc.snapshots,
            CASE
                WHEN l.idx_scan = 0         THEN 'dead'
                WHEN l.idx_scan < 1000      THEN 'low'
                WHEN l.idx_scan < 10000     THEN 'medium'
                WHEN l.idx_scan < 100000    THEN 'high'
                ELSE                             'very_high'
            END AS band
        FROM latest l
        JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
        LEFT JOIN deltas d ON l.indexrelid = d.indexrelid
        WHERE 1=1
        {analytics_filter}
    )
    SELECT * FROM classified
    {band_filter}
    ORDER BY idx_scan ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def get_analytics_families() -> list[dict]:
    sql = """
    WITH current_reset AS (
        SELECT MAX(stats_reset) AS reset_time FROM _index_usage_daily
    ),
    in_window AS (
        SELECT * FROM _index_usage_daily
        WHERE stats_reset = (SELECT reset_time FROM current_reset)
    ),
    latest AS (
        SELECT DISTINCT ON (indexrelid)
            indexrelid, relname, idx_scan, index_size_bytes
        FROM in_window
        ORDER BY indexrelid, snapshot_at DESC
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM in_window
        GROUP BY indexrelid
    )
    SELECT
        regexp_replace(l.relname, '_[0-9]+$', '') AS family,
        COUNT(*) AS partition_count,
        SUM(l.idx_scan) AS total_scans,
        SUM(l.index_size_bytes) AS total_size_bytes,
        COUNT(*) FILTER (
            WHERE l.idx_scan = 0 AND sc.snapshots >= 3
        ) AS dead_partition_count,
        BOOL_AND(l.idx_scan = 0 AND sc.snapshots >= 3) AS fully_dead
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    WHERE l.relname LIKE 'analytics%%'
    GROUP BY regexp_replace(l.relname, '_[0-9]+$', '')
    ORDER BY SUM(l.idx_scan) ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def get_analytics_family_detail(family: str) -> list[dict]:
    sql = """
    WITH current_reset AS (
        SELECT MAX(stats_reset) AS reset_time FROM _index_usage_daily
    ),
    in_window AS (
        SELECT * FROM _index_usage_daily
        WHERE stats_reset = (SELECT reset_time FROM current_reset)
    ),
    latest AS (
        SELECT DISTINCT ON (indexrelid)
            indexrelid, schemaname, relname, indexrelname,
            idx_scan, index_size_bytes
        FROM in_window
        ORDER BY indexrelid, snapshot_at DESC
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM in_window
        GROUP BY indexrelid
    ),
    deltas AS (
        SELECT
            indexrelid,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta) AS median_delta
        FROM (
            SELECT
                indexrelid,
                idx_scan - LAG(idx_scan) OVER (
                    PARTITION BY indexrelid ORDER BY snapshot_at
                ) AS delta
            FROM in_window
        ) d
        WHERE delta IS NOT NULL AND delta >= 0
        GROUP BY indexrelid
    ),
    had_reset_per_index AS (
        SELECT indexrelid, COUNT(DISTINCT stats_reset) > 1 AS had_reset
        FROM _index_usage_daily
        GROUP BY indexrelid
    )
    SELECT
        l.schemaname,
        l.relname,
        l.indexrelname,
        l.idx_scan,
        COALESCE(d.median_delta, 0)::bigint AS median_delta,
        l.index_size_bytes,
        sc.snapshots,
        CASE
            WHEN l.idx_scan = 0         THEN 'dead'
            WHEN l.idx_scan < 1000      THEN 'low'
            WHEN l.idx_scan < 10000     THEN 'medium'
            WHEN l.idx_scan < 100000    THEN 'high'
            ELSE                             'very_high'
        END AS band,
        COALESCE(hr.had_reset, false) AS had_reset
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    LEFT JOIN deltas d ON l.indexrelid = d.indexrelid
    LEFT JOIN had_reset_per_index hr ON l.indexrelid = hr.indexrelid
    WHERE regexp_replace(l.relname, '_[0-9]+$', '') = %(family)s
    ORDER BY l.idx_scan ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"family": family})
            return [dict(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Run all query tests**

```bash
.venv/bin/python -m pytest tests/test_queries.py -v
```

Expected: all tests pass (13 total).

- [ ] **Step 5: Commit**

```bash
git add app/queries.py tests/test_queries.py
git commit -m "feat: all-indexes and analytics family queries"
```

---

### Task 5: FastAPI App, Base Template, and Overview Page

**Files:**
- Create: `app/main.py`
- Create: `app/templates/base.html`
- Create: `app/templates/overview.html`
- Create: `tests/test_routes.py`

**Interfaces:**
- Consumes: `get_overview_stats()` from `app.queries`
- Produces: `GET /` returns 200 HTML containing the word "Overview"

- [ ] **Step 1: Write the failing route test**

Create `tests/test_routes.py`:
```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_overview_route_returns_200():
    response = client.get("/")
    assert response.status_code == 200


def test_overview_contains_key_content():
    response = client.get("/")
    assert "Total Indexes" in response.text
    assert "Dead Indexes" in response.text
    assert "Analytics Families" in response.text
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_routes.py::test_overview_route_returns_200 -v
```

Expected: `ImportError` — `app.main` does not exist yet.

- [ ] **Step 3: Create app/main.py**

First create the templates directory:
```bash
mkdir -p app/templates
```

```python
import os
from pathlib import Path
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import List

import app.queries as queries

app = FastAPI(title="DHIS2 Index Analyzer")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _format_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    for unit, threshold in (("GB", 1_073_741_824), ("MB", 1_048_576), ("KB", 1_024)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"


def _format_number(n) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}"


templates.env.filters["format_bytes"] = _format_bytes
templates.env.filters["format_number"] = _format_number


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    stats = queries.get_overview_stats()
    return templates.TemplateResponse(
        "overview.html", {"request": request, "stats": stats}
    )


@app.get("/dead", response_class=HTMLResponse)
async def dead_indexes(request: Request, analytics_only: bool = False):
    dead, insufficient = queries.get_dead_indexes(analytics_only=analytics_only)
    return templates.TemplateResponse(
        "dead.html",
        {
            "request": request,
            "dead": dead,
            "insufficient": insufficient,
            "analytics_only": analytics_only,
        },
    )


@app.get("/indexes", response_class=HTMLResponse)
async def all_indexes(
    request: Request,
    band: List[str] = Query(default=[]),
    analytics_only: bool = False,
):
    selected_bands = band if band else None
    indexes = queries.get_all_indexes(bands=selected_bands, analytics_only=analytics_only)
    return templates.TemplateResponse(
        "indexes.html",
        {
            "request": request,
            "indexes": indexes,
            "selected_bands": band,
            "analytics_only": analytics_only,
            "all_bands": ["dead", "low", "medium", "high", "very_high"],
        },
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_families(request: Request):
    families = queries.get_analytics_families()
    return templates.TemplateResponse(
        "analytics.html", {"request": request, "families": families}
    )


@app.get("/analytics/{family}", response_class=HTMLResponse)
async def analytics_family_detail(request: Request, family: str):
    indexes = queries.get_analytics_family_detail(family)
    return templates.TemplateResponse(
        "analytics_family.html",
        {"request": request, "family": family, "indexes": indexes},
    )
```

- [ ] **Step 4: Create app/templates/base.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DHIS2 Index Analyzer</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand fw-bold" href="/">DHIS2 Index Analyzer</a>
    <div class="navbar-nav ms-3">
      <a class="nav-link {% if request.url.path == '/' %}active{% endif %}" href="/">Overview</a>
      <a class="nav-link {% if request.url.path == '/dead' %}active{% endif %}" href="/dead">Dead Indexes</a>
      <a class="nav-link {% if request.url.path == '/indexes' %}active{% endif %}" href="/indexes">All Indexes</a>
      <a class="nav-link {% if request.url.path.startswith('/analytics') %}active{% endif %}" href="/analytics">Analytics Families</a>
    </div>
  </div>
</nav>
<div class="container-fluid mt-4 px-4">
  {% block content %}{% endblock %}
</div>
</body>
</html>
```

- [ ] **Step 5: Create app/templates/overview.html**

```html
{% extends "base.html" %}
{% block content %}
<h2 class="mb-3">Overview</h2>

{% if stats.had_reset %}
<div class="alert alert-warning">
  <strong>Stats reset detected.</strong> A PostgreSQL statistics reset occurred in the recorded history.
  Historical data before the most recent reset is excluded from all metrics.
</div>
{% endif %}

<div class="row g-3 mb-4">
  <div class="col-md-3">
    <div class="card h-100">
      <div class="card-body">
        <h6 class="card-subtitle mb-1 text-muted">Total Indexes</h6>
        <p class="display-6 mb-0">{{ stats.total_indexes | format_number }}</p>
      </div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card h-100 border-danger">
      <div class="card-body">
        <h6 class="card-subtitle mb-1 text-muted">Dead Indexes</h6>
        <p class="display-6 mb-0 text-danger">{{ stats.dead_count | format_number }}</p>
        <small class="text-muted">zero scans, ≥3 snapshots</small>
      </div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card h-100">
      <div class="card-body">
        <h6 class="card-subtitle mb-1 text-muted">Analytics Families</h6>
        <p class="display-6 mb-0">{{ stats.analytics_family_count | format_number }}</p>
      </div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card h-100 border-warning">
      <div class="card-body">
        <h6 class="card-subtitle mb-1 text-muted">Dead Analytics Indexes</h6>
        <p class="display-6 mb-0 text-warning">{{ stats.dead_analytics_count | format_number }}</p>
      </div>
    </div>
  </div>
</div>

<p class="text-muted small">
  Current stats window since: <strong>{{ stats.current_reset.strftime('%Y-%m-%d %H:%M UTC') }}</strong>
  &nbsp;|&nbsp;
  Latest snapshot: <strong>{{ stats.latest_snapshot.strftime('%Y-%m-%d %H:%M UTC') }}</strong>
</p>
{% endblock %}
```

- [ ] **Step 6: Run route tests**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v
```

Expected:
```
tests/test_routes.py::test_overview_route_returns_200 PASSED
tests/test_routes.py::test_overview_contains_key_content PASSED
2 passed
```

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/base.html app/templates/overview.html tests/test_routes.py
git commit -m "feat: FastAPI app skeleton and overview page"
```

---

### Task 6: Dead Indexes Page

**Files:**
- Create: `app/templates/dead.html`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Consumes: `GET /dead?analytics_only=false`
- Produces: page listing zero-scan indexes in two sections; analytics_only toggle works

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routes.py`:
```python
def test_dead_indexes_route_returns_200():
    response = client.get("/dead")
    assert response.status_code == 200


def test_dead_indexes_analytics_filter():
    response = client.get("/dead?analytics_only=true")
    assert response.status_code == 200
    assert "analytics_only=true" in response.text or "checked" in response.text


def test_dead_indexes_shows_insufficient_section():
    response = client.get("/dead")
    assert "Insufficient Data" in response.text
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v -k dead
```

Expected: `TemplateNotFound` for `dead.html`.

- [ ] **Step 3: Create app/templates/dead.html**

```html
{% extends "base.html" %}
{% block content %}
<h2 class="mb-3">Dead Indexes</h2>

<form method="get" class="mb-3 d-flex align-items-center gap-3">
  <div class="form-check form-switch">
    <input class="form-check-input" type="checkbox" name="analytics_only" value="true" id="analyticsOnly"
           {% if analytics_only %}checked{% endif %}
           onchange="this.form.submit()">
    <label class="form-check-label" for="analyticsOnly">Analytics tables only</label>
  </div>
</form>

<h5>Confirmed Dead <span class="badge bg-danger">{{ dead | length }}</span></h5>
{% if dead %}
<div class="table-responsive mb-4">
  <table class="table table-sm table-hover table-bordered">
    <thead class="table-dark">
      <tr>
        <th>Schema</th>
        <th>Table</th>
        <th>Index</th>
        <th class="text-end">Size</th>
        <th class="text-end">Snapshots</th>
        <th>Stats Reset</th>
      </tr>
    </thead>
    <tbody>
    {% for row in dead %}
      <tr>
        <td>{{ row.schemaname }}</td>
        <td>{{ row.relname }}</td>
        <td><code>{{ row.indexrelname }}</code></td>
        <td class="text-end">{{ row.index_size_bytes | format_bytes }}</td>
        <td class="text-end">{{ row.snapshots }}</td>
        <td>{{ row.stats_reset.strftime('%Y-%m-%d') }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-muted">No confirmed dead indexes found.</p>
{% endif %}

<h5 class="mt-4">Insufficient Data <span class="badge bg-secondary">{{ insufficient | length }}</span></h5>
<p class="text-muted small">Zero scans but fewer than 3 snapshots in current window — cannot confirm dead status.</p>
{% if insufficient %}
<div class="table-responsive">
  <table class="table table-sm table-hover table-bordered">
    <thead class="table-secondary">
      <tr>
        <th>Schema</th>
        <th>Table</th>
        <th>Index</th>
        <th class="text-end">Size</th>
        <th class="text-end">Snapshots</th>
      </tr>
    </thead>
    <tbody>
    {% for row in insufficient %}
      <tr class="table-light">
        <td>{{ row.schemaname }}</td>
        <td>{{ row.relname }}</td>
        <td><code>{{ row.indexrelname }}</code></td>
        <td class="text-end">{{ row.index_size_bytes | format_bytes }}</td>
        <td class="text-end">{{ row.snapshots }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-muted">No insufficient-data indexes.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v -k dead
```

Expected: all 3 dead-index tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/templates/dead.html tests/test_routes.py
git commit -m "feat: dead indexes page"
```

---

### Task 7: All Indexes Page

**Files:**
- Create: `app/templates/indexes.html`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Consumes: `GET /indexes?band=dead&band=low&analytics_only=false`
- Produces: page listing all indexes with band badge; band checkboxes filter the list

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routes.py`:
```python
def test_all_indexes_route_returns_200():
    response = client.get("/indexes")
    assert response.status_code == 200


def test_all_indexes_shows_band_filters():
    response = client.get("/indexes")
    for band in ("dead", "low", "medium", "high", "very_high"):
        assert band in response.text


def test_all_indexes_band_filter_param():
    response = client.get("/indexes?band=dead")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v -k "all_indexes"
```

Expected: `TemplateNotFound` for `indexes.html`.

- [ ] **Step 3: Create app/templates/indexes.html**

```html
{% extends "base.html" %}
{% block content %}
<h2 class="mb-3">All Indexes</h2>

<form method="get" class="mb-3">
  <div class="d-flex flex-wrap align-items-center gap-3">
    <span class="fw-semibold">Band:</span>
    {% for b in all_bands %}
    <div class="form-check">
      <input class="form-check-input" type="checkbox" name="band" value="{{ b }}" id="band_{{ b }}"
             {% if b in selected_bands %}checked{% endif %}>
      <label class="form-check-label" for="band_{{ b }}">{{ b | replace('_', ' ') | title }}</label>
    </div>
    {% endfor %}
    <div class="vr"></div>
    <div class="form-check form-switch">
      <input class="form-check-input" type="checkbox" name="analytics_only" value="true" id="analyticsOnly"
             {% if analytics_only %}checked{% endif %}>
      <label class="form-check-label" for="analyticsOnly">Analytics only</label>
    </div>
    <button type="submit" class="btn btn-sm btn-primary">Apply</button>
    <a href="/indexes" class="btn btn-sm btn-outline-secondary">Reset</a>
  </div>
</form>

<p class="text-muted small">{{ indexes | length }} index(es)</p>

{% if indexes %}
<div class="table-responsive">
  <table class="table table-sm table-hover table-bordered">
    <thead class="table-dark">
      <tr>
        <th>Schema</th>
        <th>Table</th>
        <th>Index</th>
        <th>Band</th>
        <th class="text-end">Scans</th>
        <th class="text-end">Median Δ</th>
        <th class="text-end">Size</th>
        <th class="text-end">Snapshots</th>
      </tr>
    </thead>
    <tbody>
    {% for row in indexes %}
      {% set band_class = {
          'dead': 'danger',
          'low': 'warning',
          'medium': 'info',
          'high': 'success',
          'very_high': 'primary'
      }[row.band] %}
      <tr>
        <td>{{ row.schemaname }}</td>
        <td>{{ row.relname }}</td>
        <td><code>{{ row.indexrelname }}</code></td>
        <td><span class="badge bg-{{ band_class }}">{{ row.band | replace('_', ' ') }}</span></td>
        <td class="text-end">{{ row.idx_scan | format_number }}</td>
        <td class="text-end">{{ row.median_delta | format_number }}</td>
        <td class="text-end">{{ row.index_size_bytes | format_bytes }}</td>
        <td class="text-end">{{ row.snapshots }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-muted">No indexes match the selected filters.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v -k "all_indexes"
```

Expected: all 3 all-indexes tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/templates/indexes.html tests/test_routes.py
git commit -m "feat: all indexes page with band filter"
```

---

### Task 8: Analytics Pages

**Files:**
- Create: `app/templates/analytics.html`
- Create: `app/templates/analytics_family.html`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Consumes: `GET /analytics` and `GET /analytics/{family}`
- Produces: families aggregated table; family detail table with per-partition index rows

- [ ] **Step 1: Write failing tests**

Append to `tests/test_routes.py`:
```python
def test_analytics_families_route_returns_200():
    response = client.get("/analytics")
    assert response.status_code == 200


def test_analytics_families_shows_family_names():
    response = client.get("/analytics")
    assert "analytics" in response.text


def test_analytics_family_detail_route():
    from app.queries import get_analytics_families
    families = get_analytics_families()
    first = families[0]["family"]
    response = client.get(f"/analytics/{first}")
    assert response.status_code == 200
    assert first in response.text


def test_analytics_family_detail_unknown_family():
    response = client.get("/analytics/nonexistent_family_xyz")
    assert response.status_code == 200
    assert "No indexes found" in response.text
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_routes.py -v -k analytics
```

Expected: `TemplateNotFound` for `analytics.html`.

- [ ] **Step 3: Create app/templates/analytics.html**

```html
{% extends "base.html" %}
{% block content %}
<h2 class="mb-3">Analytics Families</h2>
<p class="text-muted small">Sorted by total scans ascending — least-used families first.</p>

{% if families %}
<div class="table-responsive">
  <table class="table table-sm table-hover table-bordered">
    <thead class="table-dark">
      <tr>
        <th>Family</th>
        <th class="text-end">Partitions</th>
        <th class="text-end">Total Scans</th>
        <th class="text-end">Total Size</th>
        <th class="text-end">Dead Partitions</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
    {% for f in families %}
      <tr {% if f.fully_dead %}class="table-danger"{% endif %}>
        <td><a href="/analytics/{{ f.family }}">{{ f.family }}</a></td>
        <td class="text-end">{{ f.partition_count }}</td>
        <td class="text-end">{{ f.total_scans | format_number }}</td>
        <td class="text-end">{{ f.total_size_bytes | format_bytes }}</td>
        <td class="text-end">{{ f.dead_partition_count }}</td>
        <td>
          {% if f.fully_dead %}
            <span class="badge bg-danger">All dead</span>
          {% elif f.dead_partition_count > 0 %}
            <span class="badge bg-warning text-dark">Partial</span>
          {% else %}
            <span class="badge bg-success">Active</span>
          {% endif %}
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-muted">No analytics families found.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Create app/templates/analytics_family.html**

```html
{% extends "base.html" %}
{% block content %}
<nav aria-label="breadcrumb" class="mb-3">
  <ol class="breadcrumb">
    <li class="breadcrumb-item"><a href="/analytics">Analytics Families</a></li>
    <li class="breadcrumb-item active">{{ family }}</li>
  </ol>
</nav>
<h2 class="mb-3">{{ family }}</h2>

{% if indexes %}
<p class="text-muted small">{{ indexes | length }} partition index(es)</p>
<div class="table-responsive">
  <table class="table table-sm table-hover table-bordered">
    <thead class="table-dark">
      <tr>
        <th>Table (Partition)</th>
        <th>Index</th>
        <th>Band</th>
        <th class="text-end">Scans</th>
        <th class="text-end">Median Δ</th>
        <th class="text-end">Size</th>
        <th class="text-end">Snapshots</th>
        <th>Reset?</th>
      </tr>
    </thead>
    <tbody>
    {% for row in indexes %}
      {% set band_class = {
          'dead': 'danger',
          'low': 'warning',
          'medium': 'info',
          'high': 'success',
          'very_high': 'primary'
      }[row.band] %}
      <tr>
        <td>{{ row.relname }}</td>
        <td><code>{{ row.indexrelname }}</code></td>
        <td><span class="badge bg-{{ band_class }}">{{ row.band | replace('_', ' ') }}</span></td>
        <td class="text-end">{{ row.idx_scan | format_number }}</td>
        <td class="text-end">{{ row.median_delta | format_number }}</td>
        <td class="text-end">{{ row.index_size_bytes | format_bytes }}</td>
        <td class="text-end">{{ row.snapshots }}</td>
        <td>{% if row.had_reset %}<span class="badge bg-warning text-dark">Yes</span>{% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-muted">No indexes found for family <strong>{{ family }}</strong>.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests pass (green).

- [ ] **Step 6: Commit**

```bash
git add app/templates/analytics.html app/templates/analytics_family.html tests/test_routes.py
git commit -m "feat: analytics families and family detail pages"
```

---

### Task 9: systemd Service File

**Files:**
- Create: `tool-index-analyzer.service`

**Interfaces:**
- Produces: deployable systemd unit for the production server

- [ ] **Step 1: Create the service file**

Create `tool-index-analyzer.service`:
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
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Adjust `User=` and `WorkingDirectory=` to match the actual deployment path and OS user on the production server.

- [ ] **Step 2: Verify the app starts locally**

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: `Uvicorn running on http://127.0.0.1:8000` — open in browser and verify all 5 pages render without errors. Ctrl+C to stop.

- [ ] **Step 3: Commit**

```bash
git add tool-index-analyzer.service
git commit -m "feat: systemd service unit"
```

---

## Deployment Checklist (Production)

1. Copy repo to `/opt/tool-index-analyzer` (or preferred path)
2. Create `.venv` and install: `.venv/bin/pip install -r requirements.txt`
3. Create `.env` with production `DATABASE_URL`
4. Adjust `User=` and `WorkingDirectory=` in `tool-index-analyzer.service`
5. Copy service file: `sudo cp tool-index-analyzer.service /etc/systemd/system/`
6. Enable and start: `sudo systemctl enable --now tool-index-analyzer`
7. Configure reverse proxy to forward traffic to port 8000
