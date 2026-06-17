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
