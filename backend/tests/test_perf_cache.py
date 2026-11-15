"""Performance work: stale-while-revalidate server cache, per-route timings, one-time file hashing, cached readiness."""
import threading
import time

import pytest

from app import auto_quality, ocr_sources, perf
from app.server_cache import StaleCache
from test_api_v2 import LEARNER


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_fresh_copy_is_returned_without_rebuilding():
    clock, calls = Clock(), []
    cache = StaleCache(ttl=30, clock=clock)
    build = lambda: calls.append(1) or {"n": len(calls)}
    first = cache.get("k", build)
    clock.now = 29
    assert cache.get("k", build) is first and len(calls) == 1


def test_an_old_copy_is_served_at_once_and_rebuilt_in_the_background():
    clock, release = Clock(), threading.Event()
    cache = StaleCache(ttl=30, max_stale=600, clock=clock)
    built = []

    def build():
        built.append(1)
        if len(built) > 1:
            release.wait(5)          # the rebuild is slow: the caller must not wait for it
        return {"n": len(built)}

    assert cache.get("k", build)["n"] == 1
    clock.now = 100
    began = time.monotonic()
    assert cache.get("k", build)["n"] == 1                       # the old copy, immediately
    assert time.monotonic() - began < 1
    assert cache.get("k", build)["n"] == 1 and len(built) == 2    # one rebuild at a time, however many callers
    release.set()
    for _ in range(100):
        if cache.get("k", build)["n"] == 2:
            break
        time.sleep(0.02)
    assert cache.get("k", build)["n"] == 2


def test_fresh_and_invalidate_rebuild_now_and_a_too_old_copy_is_not_served():
    clock, calls = Clock(), []
    cache = StaleCache(ttl=30, max_stale=600, clock=clock)
    build = lambda: calls.append(1) or len(calls)
    cache.get("k", build)
    assert cache.get("k", build, fresh=True) == 2
    cache.invalidate("k")
    assert cache.get("k", build) == 3
    clock.now = 10000
    assert cache.get("k", build) == 4                              # beyond max_stale: built on the spot


def test_a_failing_background_rebuild_keeps_the_old_copy():
    clock = Clock()
    cache = StaleCache(ttl=1, clock=clock)
    cache.get("k", lambda: "old")
    clock.now = 5

    def broken():
        raise RuntimeError("db down")

    assert cache.get("k", broken) == "old"
    time.sleep(0.1)
    assert cache.get("k", broken) == "old"


def test_timings_are_kept_per_route_with_percentiles(client):
    perf.reset()
    for _ in range(5):
        client.get("/health")
    reply = client.get("/health")
    assert "app;dur=" in reply.headers["server-timing"]
    assert client.get("/v2/perf").status_code == 401
    report = client.get("/v2/perf", headers=LEARNER).json()
    row = next(r for r in report["routes"] if r["route"] == "/health")
    assert row["count"] >= 6 and row["p50_ms"] <= row["p95_ms"] <= row["max_ms"]
    assert report["slow_ms"] == 300
    perf.record("/slow", "GET", 400, 200)
    assert next(r for r in perf.report() if r["route"] == "/slow")["p95_ms"] == 400


def test_a_stored_ocr_file_is_hashed_once_not_for_every_paragraph_of_every_question(db, monkeypatch):
    from psycopg.types.json import Jsonb

    content = b"x" * (3 * 1024 * 1024)                              # a multi-megabyte OCR file
    digest = ocr_sources.sha(content)
    doc = db.execute(
        "INSERT INTO source_documents(file_id,title,edition,subject,sha256,original_sha256,original_content,metadata) "
        "VALUES ('f','t','e','biochemistry',%s,%s,'\\x00',%s) RETURNING id", (digest, digest, Jsonb({}))).fetchone()["id"]
    db.execute("INSERT INTO source_versions(document_id,collection_version,material_key,folder_id,source_sha256,source_format,source_content) "
        "VALUES (%s,'v','m','fo',%s,'txt',%s)", (doc, digest, content))
    row = {"document_id": doc, "sha256": digest}
    hashed = []
    real = ocr_sources.sha
    monkeypatch.setattr(ocr_sources, "sha", lambda data: hashed.append(len(data)) or real(data))
    ocr_sources._VERIFIED_FILES.discard((doc, digest))
    began = time.perf_counter()
    for _ in range(30):                                              # 30 cited paragraphs of 30 questions
        assert ocr_sources.integrity_issues(db, row) == []
    after = time.perf_counter() - began
    assert hashed == [len(content)]                                  # read and hashed exactly once
    # without the memo every call loads and hashes the whole file again
    began = time.perf_counter()
    for _ in range(30):
        ocr_sources._VERIFIED_FILES.discard((doc, digest))
        ocr_sources.integrity_issues(db, row)
    before = time.perf_counter() - began
    print(f"integrity check of 30 paragraphs: {before * 1000:.0f} ms before, {after * 1000:.0f} ms after")
    assert after < before / 5
    # a changed file is still caught
    db.execute("ALTER TABLE source_versions DISABLE TRIGGER USER")
    db.execute("UPDATE source_versions SET source_content=%s WHERE document_id=%s", (b"y" * 100, doc))
    ocr_sources._VERIFIED_FILES.discard((doc, digest))
    assert ocr_sources.integrity_issues(db, row) == ["missing_or_changed_ocr_file"]


def test_readiness_for_display_is_reused_for_a_minute_but_never_for_admission(db, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: calls.append(qid) or True)
    db.execute("SET CONSTRAINTS ALL DEFERRED")
    monkeypatch.setattr(db, "execute", db.execute)
    auto_quality._READY_SEEN.clear()

    class Row(dict):
        pass

    class Conn:
        def execute(self, *a, **k):
            class R:
                def fetchone(self_inner):
                    return {"content_sha256": "abc"}
            return R()

    conn = Conn()
    assert auto_quality.ready_cached(conn, 7) is True and auto_quality.ready_cached(conn, 7) is True
    assert calls == [7]                                              # the second call came from the copy
    assert auto_quality.ready(conn, 7) is True and calls == [7, 7]   # the admission path always re-verifies
    auto_quality._READY_SEEN[(7, "abc")] = (time.monotonic() - 61, True)
    auto_quality.ready_cached(conn, 7)
    assert calls == [7, 7, 7]                                        # older than a minute: checked again
