from app.queries import get_overview_stats, get_dead_indexes, get_all_indexes, get_analytics_families, get_analytics_family_detail

VALID_BANDS = {"dead", "low", "medium", "high", "very_high"}


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


def test_get_all_indexes_returns_list_of_dicts():
    rows = get_all_indexes()
    assert isinstance(rows, list)
    assert len(rows) > 0
    for key in ("schemaname", "relname", "indexrelname", "idx_scan",
                "avg_delta", "index_size_bytes", "snapshots", "band"):
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
                "avg_delta", "index_size_bytes", "snapshots", "band", "had_reset"):
        assert key in rows[0], f"missing key: {key}"


def test_get_analytics_family_detail_all_match_family():
    families = get_analytics_families()
    first_family = families[0]["family"]
    rows = get_analytics_family_detail(first_family)
    import re
    for row in rows:
        extracted = re.sub(r'_[0-9]+$', '', row["relname"])
        assert extracted == first_family
