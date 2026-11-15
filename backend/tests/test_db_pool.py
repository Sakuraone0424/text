"""The connection pool: connections are reused, a transaction still commits or rolls back as a whole, nothing set in one use leaks
into the next, and local tools / tests do not pool unless asked."""
import os

import pytest

from app import db


@pytest.fixture
def pooled(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an explicitly selected development PostgreSQL database")
    for name in list(os.environ):
        if name.startswith("RAILWAY_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("QUIZ_REQUIRE_RAILWAY_IDENTITY", raising=False)
    monkeypatch.delenv("QUIZ_DB_SCHEMA", raising=False)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("QUIZ_DB_POOL", "1")
    yield
    db.close_pools()


def test_local_tools_and_tests_do_not_pool_unless_asked(monkeypatch):
    for name in list(os.environ):
        if name.startswith("RAILWAY_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("QUIZ_REQUIRE_RAILWAY_IDENTITY", raising=False)
    monkeypatch.delenv("QUIZ_DB_POOL", raising=False)
    assert db.pooling() is False
    monkeypatch.setenv("QUIZ_REQUIRE_RAILWAY_IDENTITY", "1")
    assert db.pooling() is True                       # the deployed service pools by default
    monkeypatch.setenv("QUIZ_DB_POOL", "0")
    assert db.pooling() is False                      # and can be told not to


def test_a_connection_is_reused_instead_of_opened_for_every_request(pooled):
    pids = []
    for _ in range(5):
        with db.get_db() as conn:
            pids.append(conn.execute("SELECT pg_backend_pid() AS p").fetchone()["p"])
    assert len(set(pids)) < len(pids) and len(set(pids)) <= 2          # the same one or two connections, not five new ones
    assert len(db.pool_stats()) == 1


def test_a_block_commits_as_a_whole_and_an_error_rolls_everything_back(pooled):
    with db.get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS pool_probe(n int)")
        conn.execute("TRUNCATE pool_probe")
    try:
        with pytest.raises(RuntimeError):
            with db.get_db() as conn:
                conn.execute("INSERT INTO pool_probe VALUES (1)")
                raise RuntimeError("boom")
        with db.get_db() as conn:
            assert conn.execute("SELECT count(*) AS n FROM pool_probe").fetchone()["n"] == 0        # rolled back, and the connection is usable again
            conn.execute("INSERT INTO pool_probe VALUES (2)")
        with db.get_db() as conn:
            assert conn.execute("SELECT count(*) AS n FROM pool_probe").fetchone()["n"] == 1        # committed
    finally:
        with db.get_db() as conn:
            conn.execute("DROP TABLE IF EXISTS pool_probe")


def test_nothing_set_inside_one_use_is_seen_by_the_next(pooled):
    with db.get_db() as conn:
        conn.execute("SELECT set_config('app.manual_grade','1',true)")
        conn.execute("SET LOCAL statement_timeout='30s'")
        assert conn.execute("SELECT current_setting('app.manual_grade', true) AS v").fetchone()["v"] == "1"
    with db.get_db() as conn:
        assert (conn.execute("SELECT current_setting('app.manual_grade', true) AS v").fetchone()["v"] or "") == ""
        assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] in ("0", "0ms")


def test_many_threads_share_a_few_connections(pooled, monkeypatch):
    import threading

    monkeypatch.setenv("QUIZ_DB_POOL_SIZE", "3")
    pids, errors = set(), []

    def work():
        try:
            for _ in range(5):
                with db.get_db() as conn:
                    pids.add(conn.execute("SELECT pg_backend_pid() AS p").fetchone()["p"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and 1 <= len(pids) <= 3
