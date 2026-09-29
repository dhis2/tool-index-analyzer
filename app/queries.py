import re
import threading

from app.db import get_connection

# DHIS2 drops and recreates every analytics index on each analytics run, with a
# new indexrelid and a randomly suffixed name, so per-indexrelid counters only
# cover the time since the last rebuild. A *logical* index is therefore keyed on
# (schema, table, index_key):
#
#   - analytics tables: the indexed column(s), taken from index_columns (the
#     index definition minus names, recorded since 2026-09-29) or, for older
#     rows, parsed from the 2.40-style name in_<column>_ax_<table>_<code>
#     (plus the _lower / _desc suffixes of the derived indexes);
#   - all other tables: the index name, which is stable.
#
# Scans are accumulated across every stats reset and every rebuild: for each
# physical index (indexrelid) the first observation counts in full, and each
# later snapshot adds the increase since the previous one, or the whole counter
# if it went down (a stats reset). avg_delta is the mean increase per snapshot
# interval (an exact median would need a full sort of the history).
#
# Only indexes present in the latest snapshot are reported, so dropped or no
# longer created indexes (e.g. excluded via analytics.table.skip_index) disappear.
#
# The summary scans the whole history table, but the data only changes when a
# snapshot is taken, so it is computed once per latest snapshot and cached; the
# page functions below filter and aggregate the cached rows.

_NAME_COLUMN = "regexp_replace(substring(indexrelname from '^in_(.+)_ax_'), '^dx_(co|ao)$', 'dx, \\1')"

# Window over the (indexrelid, snapshot_at DESC) index so no sort of the full
# history is needed: LEAD() is the previous snapshot of the same physical index.
_SUMMARY_SQL = f"""
WITH bounds AS (SELECT MAX(snapshot_at) AS latest_snap FROM _index_usage_daily),
obs AS (
    SELECT indexrelid, snapshot_at, idx_scan, stats_reset, schemaname, relname, indexrelname,
           index_columns, index_size_bytes,
           LEAD(idx_scan) OVER w AS prev_scan
    FROM _index_usage_daily
    WINDOW w AS (PARTITION BY indexrelid ORDER BY snapshot_at DESC)
),
per_oid AS (
    SELECT indexrelid,
           MIN(schemaname)    AS schemaname,
           MIN(relname)       AS relname,
           MIN(indexrelname)  AS indexrelname,
           MAX(index_columns) AS index_columns,
           SUM(CASE WHEN prev_scan IS NULL OR idx_scan < prev_scan THEN idx_scan
                    ELSE idx_scan - prev_scan END)                           AS scans,
           SUM(CASE WHEN prev_scan IS NULL THEN 0
                    WHEN idx_scan < prev_scan THEN idx_scan
                    ELSE idx_scan - prev_scan END)                           AS later_scans,
           COUNT(*)                                                          AS obs,
           BOOL_OR(idx_scan < prev_scan)                                     AS had_reset,
           MIN(snapshot_at)                                                  AS first_seen,
           MAX(snapshot_at)                                                  AS last_seen,
           MAX(index_size_bytes) FILTER (WHERE snapshot_at = (SELECT latest_snap FROM bounds)) AS latest_size,
           MAX(stats_reset)                                                  AS stats_reset
    FROM obs
    GROUP BY indexrelid
),
keyed AS (
    SELECT p.*,
        CASE
            WHEN relname NOT LIKE 'analytics%%' THEN indexrelname
            WHEN index_columns IS NOT NULL THEN
                replace(regexp_replace(index_columns, '^\\w+ \\((.*)\\)$', '\\1'), '"', '')
            WHEN indexrelname ~ '^in_.+_ax_' THEN
                CASE
                    WHEN indexrelname LIKE '%%\\_lower' THEN 'lower(' || {_NAME_COLUMN} || ')'
                    WHEN indexrelname LIKE '%%\\_desc' THEN {_NAME_COLUMN} || ' DESC NULLS LAST'
                    ELSE {_NAME_COLUMN}
                END
            ELSE indexrelname
        END AS index_key
    FROM per_oid p
)
SELECT
    schemaname,
    relname,
    index_key,
    MAX(indexrelname) FILTER (WHERE latest_size IS NOT NULL)   AS indexrelname,
    MAX(latest_size)                                           AS index_size_bytes,
    MAX(stats_reset)                                           AS stats_reset,
    SUM(scans)::bigint                                         AS idx_scan,
    COALESCE(SUM(later_scans) / NULLIF(SUM(obs) - COUNT(*), 0), 0)::bigint AS avg_delta,
    SUM(obs)                                                   AS snapshots,
    COUNT(*)                                                   AS rebuilds,
    COALESCE(BOOL_OR(had_reset), false)                        AS had_reset,
    MIN(first_seen)                                            AS first_seen
FROM keyed
GROUP BY schemaname, relname, index_key
HAVING MAX(last_seen) = (SELECT latest_snap FROM bounds)
"""

_BOUNDS_SQL = """
SELECT
    MAX(snapshot_at)                AS latest_snapshot,
    MAX(stats_reset)                AS current_reset,
    COUNT(DISTINCT stats_reset) > 1 AS had_reset
FROM _index_usage_daily
"""

_LATEST_SQL = "SELECT MAX(snapshot_at) AS latest FROM _index_usage_daily"

_MIN_SNAPSHOTS = 3

_cache: dict = {}
_cache_lock = threading.Lock()


def _summary() -> tuple[dict, list[dict]]:
    """Returns (bounds, per-logical-index rows), recomputed only when a new
    snapshot has been taken."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_LATEST_SQL)
            latest = cur.fetchone()["latest"]
            with _cache_lock:
                if _cache.get("latest") == latest:
                    return _cache["bounds"], _cache["rows"]
                cur.execute(_BOUNDS_SQL)
                bounds = dict(cur.fetchone())
                cur.execute("SET work_mem = '256MB'")
                cur.execute(_SUMMARY_SQL)
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    r["family"] = _family(r["relname"])
                    r["band"] = _band(r["idx_scan"])
                    r["is_dead"] = r["idx_scan"] == 0 and r["snapshots"] >= _MIN_SNAPSHOTS
                _cache.update(latest=latest, bounds=bounds, rows=rows)
                return bounds, rows


def _family(relname: str) -> str:
    return re.sub(r"_[0-9]+$", "", relname)


def _is_analytics(row: dict) -> bool:
    return row["relname"].startswith("analytics")


def _band(idx_scan: int) -> str:
    if idx_scan == 0:
        return "dead"
    if idx_scan < 1_000:
        return "low"
    if idx_scan < 10_000:
        return "medium"
    if idx_scan < 100_000:
        return "high"
    return "very_high"


def get_overview_stats() -> dict:
    bounds, rows = _summary()
    analytics = [r for r in rows if _is_analytics(r)]
    return {
        "total_indexes": len(rows),
        "dead_count": sum(r["is_dead"] for r in rows),
        "analytics_family_count": len({r["family"] for r in analytics}),
        "dead_analytics_count": sum(r["is_dead"] for r in analytics),
        "current_reset": bounds["current_reset"],
        "latest_snapshot": bounds["latest_snapshot"],
        "had_reset": bool(bounds["had_reset"]),
    }


def get_dead_indexes(
    analytics_only: bool = False,
) -> tuple[list[dict], list[dict]]:
    _, rows = _summary()
    zero = [
        dict(r, classification="dead" if r["is_dead"] else "insufficient_data")
        for r in rows
        if r["idx_scan"] == 0 and (_is_analytics(r) or not analytics_only)
    ]
    zero.sort(key=lambda r: r["index_size_bytes"] or 0, reverse=True)
    dead = [r for r in zero if r["classification"] == "dead"]
    insufficient = [r for r in zero if r["classification"] == "insufficient_data"]
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

    _, rows = _summary()
    result = [
        r
        for r in rows
        if (not bands or r["band"] in bands) and (_is_analytics(r) or not analytics_only)
    ]
    result.sort(key=lambda r: r["idx_scan"])
    return result


def _group(rows: list[dict], key) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    return [
        {
            "key": k,
            "partition_count": len({r["relname"] for r in g}),
            "index_count": len(g),
            "total_scans": sum(r["idx_scan"] for r in g),
            "total_size_bytes": sum(r["index_size_bytes"] or 0 for r in g),
            "dead_partition_count": sum(r["is_dead"] for r in g),
            "fully_dead": all(r["is_dead"] for r in g),
        }
        for k, g in groups.items()
    ]


def get_analytics_families() -> list[dict]:
    _, rows = _summary()
    families = _group([r for r in rows if _is_analytics(r)], lambda r: r["family"])
    for f in families:
        f["family"] = f.pop("key")
    families.sort(key=lambda f: f["total_scans"])
    return families


def get_analytics_index_family_summary(table_family: str) -> list[dict]:
    _, rows = _summary()
    groups = _group([r for r in rows if r["family"] == table_family], lambda r: r["index_key"])
    for g in groups:
        g["index_family"] = g.pop("key")
    groups.sort(key=lambda g: g["total_size_bytes"], reverse=True)
    return groups


def _format_cardinality(n_distinct) -> tuple[str, int]:
    """Returns (display_string, sort_value) from a pg_stats n_distinct float.
    Negative means high cardinality (fraction of rows); None means no stats."""
    if n_distinct is None:
        return "—", 999_999_999
    if n_distinct < 0:
        return "high", 999_999_999
    n = int(n_distinct)
    return str(n), n


_CARDINALITY_SQL = """
SELECT tablename, attname, n_distinct
FROM pg_stats
WHERE tablename = ANY(%(tables)s)
"""


def get_analytics_family_detail(family: str) -> list[dict]:
    _, rows = _summary()
    detail = [dict(r) for r in rows if r["family"] == family]
    if not detail:
        return []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CARDINALITY_SQL, {"tables": sorted({r["relname"] for r in detail})})
            stats = {(s["tablename"], s["attname"]): s["n_distinct"] for s in cur.fetchall()}

    for r in detail:
        display, sort_val = _format_cardinality(stats.get((r["relname"], r["index_key"])))
        r["cardinality"] = display
        r["cardinality_sort"] = sort_val
    detail.sort(key=lambda r: r["index_size_bytes"] or 0, reverse=True)
    return detail
