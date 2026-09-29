# DHIS2 Index Analyzer

A small, read-only web app that surfaces PostgreSQL index-usage statistics for a DHIS2 instance. It helps administrators identify **dead indexes** — indexes that are never scanned — especially across the partitioned `analytics_*` tables, so they can be safely disabled to speed up the analytics generation process.

Pages are server-rendered (FastAPI + Jinja2 + Bootstrap 5). There is no JavaScript framework, no login, and the app never writes to the database.

## How it works

The app does **not** read live `pg_stat_user_indexes` directly. Instead it reads a history table, `_index_usage_daily`, that you populate on a schedule. Because `idx_scan` counters are cumulative and reset whenever PostgreSQL statistics are reset, snapshotting over time is what lets the app tell a genuinely-dead index apart from a brand-new one.

### 1. Create the snapshot table

```sql
CREATE TABLE _index_usage_daily (
    snapshot_at      timestamptz NOT NULL DEFAULT now(),
    stats_reset      timestamptz NOT NULL,
    schemaname       text        NOT NULL,
    relname          text        NOT NULL,
    indexrelname     text        NOT NULL,
    indexrelid       oid         NOT NULL,
    idx_scan         bigint      NOT NULL,
    idx_tup_read     bigint      NOT NULL,
    idx_tup_fetch    bigint      NOT NULL,
    index_size_bytes bigint      NOT NULL,
    index_columns    text        -- e.g. 'btree (uidlevel4)'; stable across analytics rebuilds
);

CREATE INDEX _index_usage_daily_indexrelid_snapshot_at_idx
    ON _index_usage_daily (indexrelid, snapshot_at DESC);
CREATE INDEX _index_usage_daily_snapshot_at_idx
    ON _index_usage_daily (snapshot_at);
CREATE INDEX _index_usage_daily_stats_reset_idx
    ON _index_usage_daily (stats_reset);
```

### 2. Populate it on a schedule

Run this snapshot query against the DHIS2 database on a cron job (twice daily is a good default — more snapshots means faster, more confident dead-index detection):

The query lives in [`snapshot.sql`](snapshot.sql). Two details matter:

- **`index_columns`** records the index definition without its name (e.g. `btree (uidlevel4)`). DHIS2 drops and recreates analytics indexes on every analytics run, with a new OID and a randomly suffixed name (and on 2.43 the name no longer contains the column at all), so this is the only stable way to follow one logical index across rebuilds.
- **`stats_reset`** falls back to `pg_postmaster_start_time()`, because `pg_stat_database.stats_reset` is NULL until statistics are explicitly reset (e.g. on a fresh or upgraded cluster). Without the fallback every snapshot fails the `NOT NULL` constraint.

Example `/etc/cron.d/index-usage-snapshot` entry (snapshot at 00:00 and 12:00, as the `postgres` OS user via peer auth). Log to a file the job's user can write, so failures are visible rather than silently discarded:

```cron
0 0,12 * * * postgres psql -q -d dhis -v ON_ERROR_STOP=1 -f /var/lib/postgresql/snapshot.sql >> /var/lib/postgresql/index-snapshot.log 2>&1
```

To use the least-privilege **collector** role instead (see [Database users](#database-users-least-privilege)), run as that user with a `~/.pgpass` entry. Never use the app's read-only `DATABASE_URL`, which cannot `INSERT`.

An index needs **at least 3 snapshots** in the current stats window before it can be classified as dead, so the dashboard is most useful after a few days of collection.

## Requirements

- Python 3.10+
- A PostgreSQL DHIS2 database with the `_index_usage_daily` table above, being populated on a schedule
- A dedicated least-privilege database user for the app (see below)

## Database users (least privilege)

**Do not point the app at the DHIS2 application's database user.** That account has read/write access to every table in the instance; the analyzer never needs it. Create dedicated accounts instead — the app is strictly read-only, and only ever touches the snapshot table.

### App user (read-only)

The app only runs `SELECT`, and at runtime only reads `_index_usage_daily` (plus the `pg_stats` system view for index cardinality). Grant exactly that:

```sql
CREATE ROLE index_analyzer LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE dhis TO index_analyzer;   -- your DHIS2 database name
GRANT USAGE  ON SCHEMA public  TO index_analyzer;
GRANT SELECT ON _index_usage_daily TO index_analyzer;
```

This is the most locked-down option and is the recommended default.

> **Cardinality caveat.** The `/analytics/{family}` page shows each index's column cardinality, read from the `pg_stats` view. PostgreSQL only exposes `pg_stats` rows for tables the user can `SELECT` from, so with the grant above the cardinality column shows "—" for everything. Granting `pg_read_all_stats`/`pg_monitor` does **not** change this. If you want cardinality populated, additionally grant `SELECT` on the analytics tables — accepting that the user can then read those data rows:
>
> ```sql
> -- Optional: only if you want the cardinality column populated
> GRANT SELECT ON ALL TABLES IN SCHEMA public TO index_analyzer;
> ```

### Collector user (writes snapshots)

The cron job that populates `_index_usage_daily` is separate from the app and needs different privileges: `INSERT` into the snapshot table, plus the ability to read the statistics views. Keep it distinct from both the DHIS2 user and the app user:

```sql
CREATE ROLE index_collector LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE dhis TO index_collector;
GRANT USAGE  ON SCHEMA public  TO index_collector;
GRANT INSERT ON _index_usage_daily TO index_collector;
GRANT pg_monitor TO index_collector;   -- ensures full read of pg_stat_user_indexes / pg_stat_database
```

## Configuration

The app reads a single environment variable, `DATABASE_URL`, with **no fallback** — if it is missing the app fails fast at startup. Point it at the read-only **app** user created above.

```bash
cp .env.example .env
# edit .env:
DATABASE_URL=postgresql://index_analyzer:password@localhost/dhis
```

For local development, `python-dotenv` loads `.env` automatically. In production, `DATABASE_URL` is supplied by the systemd `EnvironmentFile` (see below).

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000>.

## Production deployment (systemd)

The repo ships a systemd unit, `tool-index-analyzer.service`, that runs the app under `uvicorn` on port 8000.

1. **Place the code** on the server (the unit expects `/opt/tool-index-analyzer`):

   ```bash
   sudo git clone https://github.com/dhis2/tool-index-analyzer.git /opt/tool-index-analyzer
   cd /opt/tool-index-analyzer
   ```

2. **Create the virtualenv and install dependencies:**

   ```bash
   sudo python3 -m venv .venv
   sudo .venv/bin/pip install -r requirements.txt
   ```

3. **Create the `.env` file** with the production connection string:

   ```bash
   sudo cp .env.example .env
   sudo nano .env      # set DATABASE_URL
   ```

4. **Install and start the service:**

   ```bash
   sudo cp tool-index-analyzer.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tool-index-analyzer
   sudo systemctl status tool-index-analyzer
   ```

The unit runs as user `dhis2` from `/opt/tool-index-analyzer` and reads `DATABASE_URL` from the `.env` file via `EnvironmentFile`. Adjust the `User`, `WorkingDirectory`, and paths in the unit file if your layout differs.

### Reverse proxy & authentication

The app binds to `0.0.0.0:8000` and implements **no authentication or TLS** of its own. Put it behind a reverse proxy (nginx or Apache) that terminates TLS and handles access control. A minimal nginx location block:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    # add auth_basic or your SSO here
}
```

### Updating

```bash
cd /opt/tool-index-analyzer
sudo git pull
sudo .venv/bin/pip install -r requirements.txt
sudo systemctl restart tool-index-analyzer
```

## Pages

| Route | Purpose |
|---|---|
| `/` | Overview: total indexes, dead count, analytics families, current stats window, reset warning |
| `/dead` | All zero-scan indexes with enough data to be confident they're dead (plus an "insufficient data" section). Optional analytics-only filter |
| `/indexes` | Every index with its usage band, scan count, median delta, and size. Filter by band and analytics-only |
| `/analytics` | One row per analytics table family, with dead-partition counts and a "fully dead" flag |
| `/analytics/{family}` | Per-partition index detail for a single analytics family |

### How indexes are classified

| Term | Meaning |
|---|---|
| **Current stats window** | Rows since the most recent `stats_reset`. All metrics are computed within this window |
| **Dead** | `idx_scan = 0` in the current window **and** at least 3 snapshots exist |
| **Insufficient data** | `idx_scan = 0` but fewer than 3 snapshots — not yet classified dead |
| **Usage bands** | dead = 0, low = 1–999, medium = 1,000–9,999, high = 10,000–99,999, very high = 100,000+ |

## Tests

```bash
.venv/bin/pytest
```

Note: the test suite connects to a **live** database (via `DATABASE_URL`) and asserts against real data — it requires a populated `_index_usage_daily` table to run.

## License

Internal tool. See repository for details.
