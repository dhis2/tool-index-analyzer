-- Snapshot current index usage into _index_usage_daily.
-- Run on a schedule (e.g. twice daily) against the DHIS2 database.
INSERT INTO _index_usage_daily (
  snapshot_at, stats_reset, schemaname, relname, indexrelname, indexrelid,
  idx_scan, idx_tup_read, idx_tup_fetch, index_size_bytes, index_columns
)
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
  pg_relation_size(s.indexrelid),
  -- Index definition without the index and table names, e.g. 'btree (uidlevel4)'.
  -- Stable across analytics table rebuilds, unlike indexrelid and indexrelname
  -- (which carry a random suffix, and on DHIS2 2.43 omit the column name).
  regexp_replace(pg_get_indexdef(s.indexrelid), '^.* USING ', '')
FROM pg_stat_user_indexes s
CROSS JOIN (
  -- stats_reset is NULL until stats are explicitly reset (e.g. on a fresh or
  -- upgraded cluster); fall back to server start so the NOT NULL column never
  -- rejects the snapshot.
  SELECT coalesce(min(stats_reset), pg_postmaster_start_time()) AS stats_reset
  FROM pg_stat_database
) d;
