from app.db import get_connection

# All queries use a `bounds` CTE to get latest_snap and current_reset in one
# pass, then look up the most-recent-per-index row via snapshot_at = latest_snap
# (index seek, ~119k rows) rather than DISTINCT ON over the full 545k-row table.

_BOUNDS_CTE = """
bounds AS (
    SELECT
        MAX(snapshot_at)                  AS latest_snap,
        MAX(stats_reset)                  AS current_reset,
        COUNT(DISTINCT stats_reset) > 1   AS had_reset_flag
    FROM _index_usage_daily
)
"""


def get_overview_stats() -> dict:
    sql = f"""
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, relname, idx_scan
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
        GROUP BY indexrelid
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
        (SELECT current_reset FROM bounds) AS current_reset,
        (SELECT latest_snap  FROM bounds) AS latest_snapshot,
        (SELECT had_reset_flag FROM bounds) AS had_reset
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
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, schemaname, relname, indexrelname,
               idx_scan, index_size_bytes, stats_reset
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
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
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, schemaname, relname, indexrelname, idx_scan, index_size_bytes
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
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
            FROM _index_usage_daily
            WHERE stats_reset = (SELECT current_reset FROM bounds)
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
    sql = f"""
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, relname, idx_scan, index_size_bytes
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
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


def get_analytics_index_family_summary(table_family: str) -> list[dict]:
    sql = f"""
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, relname, indexrelname, idx_scan, index_size_bytes
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
        GROUP BY indexrelid
    )
    SELECT
        regexp_replace(l.indexrelname, '_ax_.*$', '') AS index_family,
        COUNT(*)                                           AS partition_count,
        SUM(l.index_size_bytes)                           AS total_size_bytes,
        SUM(l.idx_scan)                                   AS total_scans,
        COUNT(*) FILTER (
            WHERE l.idx_scan = 0 AND sc.snapshots >= 3
        )                                                  AS dead_partition_count,
        BOOL_AND(l.idx_scan = 0 AND sc.snapshots >= 3)   AS fully_dead
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    WHERE regexp_replace(l.relname, '_[0-9]+$', '') = %(family)s
    GROUP BY regexp_replace(l.indexrelname, '_ax_.*$', '')
    ORDER BY SUM(l.index_size_bytes) DESC NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"family": table_family})
            return [dict(r) for r in cur.fetchall()]


def _format_cardinality(n_distinct) -> tuple[str, int]:
    """Returns (display_string, sort_value) from a pg_stats n_distinct float.
    Negative means high cardinality (fraction of rows); None means no stats."""
    if n_distinct is None:
        return "—", 999_999_999
    if n_distinct < 0:
        return "high", 999_999_999
    n = int(n_distinct)
    return str(n), n


def get_analytics_family_detail(family: str) -> list[dict]:
    sql = f"""
    WITH {_BOUNDS_CTE},
    latest AS (
        SELECT indexrelid, schemaname, relname, indexrelname, idx_scan, index_size_bytes
        FROM _index_usage_daily
        WHERE snapshot_at = (SELECT latest_snap FROM bounds)
    ),
    snapshot_counts AS (
        SELECT indexrelid, COUNT(*) AS snapshots
        FROM _index_usage_daily
        WHERE stats_reset = (SELECT current_reset FROM bounds)
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
            FROM _index_usage_daily
            WHERE stats_reset = (SELECT current_reset FROM bounds)
        ) d
        WHERE delta IS NOT NULL AND delta >= 0
        GROUP BY indexrelid
    ),
    had_reset_per_index AS (
        SELECT indexrelid, COUNT(DISTINCT stats_reset) > 1 AS had_reset
        FROM _index_usage_daily
        GROUP BY indexrelid
    ),
    index_cardinality AS (
        SELECT
            snap.indexrelid,
            ps.n_distinct
        FROM (
            SELECT DISTINCT indexrelid, relname, indexrelname
            FROM _index_usage_daily
            WHERE snapshot_at = (SELECT latest_snap FROM bounds)
              AND regexp_replace(relname, '_[0-9]+$', '') = %(family)s
        ) snap
        LEFT JOIN pg_stats ps
            ON ps.tablename = snap.relname
           AND ps.attname = regexp_replace(snap.indexrelname, '^in_(.+)_ax_.*$', '\1')
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
        COALESCE(hr.had_reset, false) AS had_reset,
        ic.n_distinct
    FROM latest l
    JOIN snapshot_counts sc ON l.indexrelid = sc.indexrelid
    LEFT JOIN deltas d ON l.indexrelid = d.indexrelid
    LEFT JOIN had_reset_per_index hr ON l.indexrelid = hr.indexrelid
    LEFT JOIN index_cardinality ic ON l.indexrelid = ic.indexrelid
    WHERE regexp_replace(l.relname, '_[0-9]+$', '') = %(family)s
    ORDER BY l.index_size_bytes DESC NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"family": family})
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        display, sort_val = _format_cardinality(r.pop("n_distinct"))
        r["cardinality"] = display
        r["cardinality_sort"] = sort_val
    return rows
