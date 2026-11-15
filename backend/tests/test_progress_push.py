"""The progress page refresh: one shared build, database changes pushed to open pages, p95 under 300 ms at a 5 s poll."""
import asyncio
import json
import threading
import time

import httpx
import psycopg
import pytest

from app import progress_events
from app.server_cache import StaleCache
from test_api_v2 import LEARNER
from test_live_training import live_app  # noqa: F401


# ---- 4. one build for many callers -------------------------------------------------------------------------------
def test_simultaneous_requests_share_one_build():
    cache, built = StaleCache(ttl=30), []

    def build():
        built.append(1)
        time.sleep(0.3)
        return {"n": len(built)}

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get("k", build, fresh=True)["n"])) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(built) == 1 and set(results) == {1}


def test_ten_open_pages_polling_at_five_seconds_cost_one_build_per_cache_lifetime():
    clock = type("C", (), {"now": 0.0, "__call__": lambda self: self.now})()
    cache, built = StaleCache(ttl=30, clock=clock), []
    for second in range(0, 29, 5):                    # ten pages, each polling every 5 s for ~30 s
        clock.now = second
        for _ in range(10):
            cache.get("owner", lambda: built.append(1) or {"n": len(built)})
    assert len(built) == 1


def test_a_change_rebuilds_every_kept_page_once():
    cache, built = StaleCache(ttl=30), []
    cache.get("a", lambda: built.append("a") or 1)
    cache.get("b", lambda: built.append("b") or 2)
    assert cache.rebuild_all() == 2 and sorted(built) == ["a", "a", "b", "b"]


def _own_db(db, monkeypatch):
    from contextlib import contextmanager
    from app import model_api

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(model_api, "get_db", test_db)
    model_api._PROGRESS.invalidate()
    model_api._BANK_CARD.invalidate()


def test_the_cached_progress_report_answers_within_300_ms_at_p95(client, db, monkeypatch):
    _own_db(db, monkeypatch)
    from app import progress as report_module
    calls = []
    real = report_module.report
    monkeypatch.setattr(report_module, "report", lambda c, u: calls.append(1) or real(c, u))
    client.get("/v2/model/progress", headers=LEARNER)            # the one build
    times = []
    for _ in range(120):                                         # 10 minutes of 5-second polls
        began = time.perf_counter()
        assert client.get("/v2/model/progress", headers=LEARNER).status_code == 200
        times.append((time.perf_counter() - began) * 1000)
    times.sort()
    assert times[int(len(times) * 0.95)] < 300 and len(calls) == 1


# ---- 2. the event hub -----------------------------------------------------------------------------------------------
def test_publish_reaches_every_stream_once_and_a_slow_one_only_keeps_the_latest():
    async def scenario():
        hub = progress_events.Hub()
        a, b = hub.subscribe(), hub.subscribe()
        hub.publish("job")
        hub.publish("bank")
        hub.publish("worker")
        await asyncio.sleep(0.05)
        first = a[1].get_nowait()
        assert a[1].empty() and first["version"] == 3 and first["reason"] == "worker"   # only the newest is kept
        assert b[1].get_nowait()["version"] == 3
        hub.unsubscribe(a)
        hub.publish("job")
        await asyncio.sleep(0.05)
        assert a[1].empty() and b[1].get_nowait()["version"] == 4 and hub.count() == 1

    asyncio.run(scenario())


def test_announce_rebuilds_before_telling_the_pages():
    order = []
    progress_events.on_change(lambda: order.append("rebuilt"))
    hub_publish = progress_events.HUB.publish
    try:
        progress_events.HUB.publish = lambda reason="update": order.append("told") or 1
        progress_events.announce("job")
    finally:
        progress_events.HUB.publish = hub_publish
        progress_events._REBUILDERS.pop()
    assert order == ["rebuilt", "told"]


# ---- 2. database -> trigger -> listener -> open event stream ----------------------------------------------------
def _stream_lines(client, timeout):
    """Read the event stream in a thread. Returns (lines, stop): stop() closes the connection (a live server cannot shut
    down while a stream is open)."""
    lines, holder = [], {}

    def read():
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout), trust_env=False) as http:
                with http.stream("GET", str(client.base_url) + "/v2/model/progress/events", headers={"x-api-key": "learner-live"}) as response:
                    holder["response"] = response
                    lines.append(f"status:{response.status_code}")
                    for line in response.iter_lines():
                        lines.append(line)
        except Exception:  # noqa: BLE001  (closed by stop())
            pass

    thread = threading.Thread(target=read, daemon=True)
    thread.start()

    class Stop:
        def set(self):
            response = holder.get("response")
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001
                    pass
            thread.join(3)

    return lines, Stop()


def _wait(lines, text, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if any(text in l for l in lines):
            return True
        time.sleep(0.05)
    return False


def test_a_database_change_reaches_an_open_stream_and_the_cached_report_is_rebuilt_first(live_app):
    client, dsn, _ = live_app
    headers = {"x-api-key": "learner-live"}
    before = client.get("/v2/model/progress", headers=headers).json()["generated_at"]
    lines, stop = _stream_lines(client, 30)
    assert _wait(lines, "event: hello", 10), lines
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO worker_heartbeats(started_at,outcome) VALUES (clock_timestamp(),'ok')")
    assert _wait(lines, "event: update", 5), lines                       # pushed within the 0.5 s debounce + rebuild
    after = client.get("/v2/model/progress", headers=headers).json()
    assert after["generated_at"] > before                                 # the copy it fetches was rebuilt before the push
    assert after["worker"]["last_run_at"] is not None
    stop.set()


def test_changes_to_jobs_and_to_the_bank_also_push(live_app):
    client, dsn, _ = live_app
    lines, stop = _stream_lines(client, 30)
    assert _wait(lines, "event: hello", 10)
    seen = len([l for l in lines if "event: update" in l])
    with psycopg.connect(dsn, autocommit=True) as conn:
        qid = conn.execute("SELECT id FROM reviewed_questions LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x') ON CONFLICT DO NOTHING", (qid,))
    assert _wait(lines, "bank", 5) or len([l for l in lines if "event: update" in l]) > seen
    seen = len([l for l in lines if "event: update" in l])
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE auto_question_states SET status='candidate' WHERE question_id=%s", (qid,))
    end = time.monotonic() + 5
    while time.monotonic() < end and len([l for l in lines if "event: update" in l]) <= seen:
        time.sleep(0.05)
    assert len([l for l in lines if "event: update" in l]) > seen
    stop.set()


def test_a_burst_of_changes_is_one_event_and_another_schemas_changes_are_ignored(live_app):
    client, dsn, _ = live_app
    lines, stop = _stream_lines(client, 30)
    assert _wait(lines, "event: hello", 10)
    base = len([l for l in lines if "event: update" in l])
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_notify('progress_update', 'some_other_schema:job')")
    time.sleep(1.5)
    assert len([l for l in lines if "event: update" in l]) == base         # not ours: nothing is pushed
    with psycopg.connect(dsn, autocommit=True) as conn:
        for _ in range(8):
            conn.execute("INSERT INTO worker_heartbeats(started_at,outcome) VALUES (clock_timestamp(),'ok')")
    time.sleep(2.5)
    assert len([l for l in lines if "event: update" in l]) - base <= 2     # eight changes, a burst: one (at most two) events
    stop.set()


def test_the_stream_needs_a_sign_in(live_app):
    client, _, _ = live_app
    r = httpx.get(str(client.base_url) + "/v2/model/progress/events", timeout=5, trust_env=False)
    assert r.status_code == 401


def test_the_home_card_endpoint_returns_only_the_stock_block(client, db, monkeypatch):
    _own_db(db, monkeypatch)
    body = client.get("/v2/model/progress/bank", headers=LEARNER).json()
    assert {"ready", "needed", "missing", "complete", "eta_state"} <= set(body) and "buckets" not in body
