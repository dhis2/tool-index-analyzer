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
