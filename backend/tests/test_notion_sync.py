"""Notion sync against a pretend Notion: databases are created once and reused, rows are added then updated (never doubled),
failures retry three times with growing waits, a switched-off sync sends nothing, and the secret never leaves the server."""
import json
import logging
import re

import httpx
import pytest

from app import notion_sync as ns, quiz_v2 as q

TOKEN = "secret_SYNTHETIC_notion_token_abc123"
PARENT = "0123456789abcdef0123456789abcdef"
PARENT_DASHED = "01234567-89ab-cdef-0123-456789abcdef"
HEADERS = {"X-Api-Key": "learner-test-key"}


class FakeNotion:
    """Just enough of Notion's API: users/me, pages, blocks/children, databases (create, get, query), pages (create, update)."""

    def __init__(self, shared=True):
        self.shared, self.log, self.databases, self.pages, self.fail = shared, [], {}, {}, []
        self.next_id = 0
        self.children = {}        # parent page -> [child_database blocks]

    def new_id(self, kind):
        self.next_id += 1
        return f"{kind}-{self.next_id:04d}"

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        path = request.url.path.removeprefix("/v1/")
        body = json.loads(request.content) if request.content else {}
        self.log.append((request.method, path, body, dict(request.headers)))
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"code": "unauthorized"})
        if self.fail:
            status = self.fail.pop(0)
            return httpx.Response(status, json={"code": "x"})
        parts = path.split("/")
        if path == "users/me":
            return httpx.Response(200, json={"id": "bot"})
        if parts[0] == "pages" and len(parts) == 2 and request.method == "GET":
            return httpx.Response(200 if self.shared and parts[1] == PARENT_DASHED else 404, json={"id": parts[1]})
        if parts[0] == "blocks" and parts[2] == "children":
            return httpx.Response(200, json={"results": self.children.get(parts[1], []), "has_more": False})
        if path == "databases" and request.method == "POST":
            db_id = self.new_id("db")
            self.databases[db_id] = dict(title=body["title"][0]["text"]["content"], properties=body["properties"], parent=body["parent"]["page_id"])
            self.children.setdefault(body["parent"]["page_id"], []).append({"id": db_id, "type": "child_database", "child_database": {"title": self.databases[db_id]["title"]}})
            return httpx.Response(200, json={"id": db_id})
        if parts[0] == "databases" and len(parts) == 2 and request.method == "GET":
            return httpx.Response(200 if parts[1] in self.databases else 404, json={"id": parts[1]})
        if parts[0] == "databases" and parts[2] == "query":
            prop, value = body["filter"]["property"], body["filter"]["number"]["equals"]
            hits = [{"id": pid} for pid, p in self.pages.items() if p["db"] == parts[1] and p["properties"].get(prop, {}).get("number") == value]
            return httpx.Response(200, json={"results": hits})
        if path == "pages" and request.method == "POST":
            pid = self.new_id("page")
            self.pages[pid] = dict(db=body["parent"]["database_id"], properties=body["properties"])
            return httpx.Response(200, json={"id": pid})
        if parts[0] == "pages" and request.method == "PATCH":
            if parts[1] not in self.pages:
                return httpx.Response(404, json={})
            self.pages[parts[1]]["properties"].update(body["properties"])
            return httpx.Response(200, json={"id": parts[1]})
        raise AssertionError(f"unexpected call {request.method} {path}")

    def rows(self, title):
        ids = [i for i, d in self.databases.items() if d["title"] == title]
        return [p for p in self.pages.values() if p["db"] in ids]

    def writes(self):
        return [r for r in self.log if r[0] in ("POST", "PATCH") and not r[1].endswith("/query")]


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setenv("NOTION_TOKEN", TOKEN)
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", PARENT)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://quiz.example")
    monkeypatch.setattr(ns, "TRANSPORT", fake.transport())
    return fake


def switch(db, uid, on=True):
    db.execute("INSERT INTO archive_consent VALUES (%s,%s) ON CONFLICT(user_id) DO UPDATE SET enabled=EXCLUDED.enabled", (uid, on))


def submitted(db, running, answers=None):
    uid, paper = running
    q.submit(db, uid, paper["paper_id"], answers or {})
    return uid, paper["paper_id"]


def run_jobs(db, limit=5):
    return ns.process_due(db, ns.make_client(pause=0), limit)


def test_page_id_accepts_a_bare_id_a_dashed_id_and_a_link():
    assert ns.page_id(PARENT) == PARENT_DASHED and ns.page_id(PARENT_DASHED) == PARENT_DASHED
    assert ns.page_id("https://www.notion.so/My-Page-" + PARENT + "?pvs=4") == PARENT_DASHED
    assert ns.page_id("") is None and ns.page_id("not an id") is None


def test_nothing_is_sent_while_the_switch_is_off_or_the_keys_are_missing(db, running, notion, monkeypatch):
    uid, paper = submitted(db, running)
    assert ns.enqueue(db, uid, paper) is None                       # switch off (the default): no job at all
    assert db.execute("SELECT count(*) AS n FROM notion_sync_jobs").fetchone()["n"] == 0
    assert ns.tick(db) == (0, 0) and notion.log == []
    switch(db, uid)
    ns.backfill(db, uid)
    switch(db, uid, False)                                           # queued, then switched off again: still nothing leaves
    assert ns.tick(db) == (0, 0) and notion.log == []
    with pytest.raises(PermissionError):
        ns.sync_now(db, uid)
    monkeypatch.delenv("NOTION_TOKEN")
    switch(db, uid)
    assert ns.tick(db) is None and not ns.configured() and notion.log == []


def test_the_first_sync_creates_both_databases_then_a_paper_row_and_the_mistakes(db, running, notion):
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    assert run_jobs(db) == (1, 0)
    titles = sorted(d["title"] for d in notion.databases.values())
    assert titles == ["训练记录", "错题本"] and all(d["parent"] == PARENT_DASHED for d in notion.databases.values())
    (record,) = notion.rows("训练记录")
    p = record["properties"]
    assert p["试卷ID"]["number"] == paper and p["日期"]["date"]["start"] and p["含复用题"]["checkbox"] in (True, False)
    assert {"生化得分", "生化满分", "细胞得分", "细胞满分", "总分", "满分", "单选得分率", "用时（分钟）", "成绩页"} <= set(p)
    assert p["成绩页"]["url"].startswith("https://quiz.example/training")
    mistakes = notion.rows("错题本")
    assert mistakes, "an empty paper loses points on every question"
    m = mistakes[0]["properties"]
    assert {"题干", "题目ID", "科目", "题型", "章节", "我的答案", "参考答案", "得分", "满分", "评分要点", "依据", "错误次数", "下次复习"} <= set(m)
    assert m["错误次数"]["number"] == 1 and "下次复习" in m


def test_a_second_run_updates_the_same_rows_and_existing_databases_are_reused(db, running, notion):
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    run_jobs(db)
    counts = (len(notion.rows("训练记录")), len(notion.rows("错题本")), len(notion.databases))
    db.execute("UPDATE notion_sync_jobs SET state='pending',next_retry=clock_timestamp()")
    run_jobs(db)                                                      # again: updates, no new rows
    assert (len(notion.rows("训练记录")), len(notion.rows("错题本")), len(notion.databases)) == counts
    db.execute("DELETE FROM notion_state WHERE key='databases'")      # the server forgot the ids: found again by name
    db.execute("DELETE FROM notion_mistake_pages")                    # ...and the page ids: found again by question id
    db.execute("UPDATE notion_sync_jobs SET state='pending',training_page_id=NULL,next_retry=clock_timestamp()")
    run_jobs(db)
    assert (len(notion.rows("训练记录")), len(notion.rows("错题本")), len(notion.databases)) == counts
    assert sum(1 for r in notion.log if r[0] == "POST" and r[1] == "databases") == 2


def test_a_mistake_made_again_updates_its_row_with_the_new_count(db, running, notion):
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    run_jobs(db)
    first = notion.rows("错题本")[0]
    qid = first["properties"]["题目ID"]["number"]
    rec = ns.paper_record(db, uid, paper)
    row = next(m for m in ns.mistake_rows(db, uid, rec) if m["question_id"] == qid)
    row["wrong"] = 2
    ns._upsert(ns.make_client(pause=0), next(i for i, d in notion.databases.items() if d["title"] == "错题本"), "题目ID", qid, ns.mistake_properties(row))
    same = [p for p in notion.rows("错题本") if p["properties"]["题目ID"]["number"] == qid]
    assert len(same) == 1 and same[0]["properties"]["错误次数"]["number"] == 2


def test_no_textbook_paragraph_is_sent_only_where_the_basis_is(db, running, notion):
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    run_jobs(db)
    rec = ns.paper_record(db, uid, paper)
    excerpts = [e["excerpt"] for a in rec["answers"] for e in (a["evidence"] or []) if e.get("excerpt")]
    sent = json.dumps([r[2] for r in notion.writes()], ensure_ascii=False)
    assert excerpts and not any(x in sent for x in excerpts)
    basis = [m["properties"]["依据"]["rich_text"][0]["text"]["content"] for m in notion.rows("错题本") if m["properties"]["依据"]["rich_text"]]
    assert basis and all(x.startswith("依据：教材") and len(x) < 200 for x in basis)        # a place, not a paragraph
    assert ns._position([dict(chapter="第三章 糖代谢", ocr_page=57, excerpt="整段教材原文")]) == "依据：教材第三章 糖代谢 · 第 57 页"


def test_a_failure_retries_three_times_with_growing_waits_then_waits_for_the_button(db, running, notion):
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    notion.fail = [503]
    assert run_jobs(db) == (0, 1)
    job = db.execute("SELECT * FROM notion_sync_jobs").fetchone()
    assert (job["state"], job["attempts"], job["last_code"]) == ("pending", 1, "notion_unavailable")
    assert run_jobs(db) == (0, 0)                                     # not due yet
    waits = []
    for status in (429, 500):
        db.execute("UPDATE notion_sync_jobs SET next_retry=clock_timestamp()")
        notion.fail = [status]
        run_jobs(db)
        job = db.execute("SELECT attempts,state,extract(epoch FROM next_retry-clock_timestamp()) AS wait FROM notion_sync_jobs").fetchone()
        waits.append(job["wait"])
    assert job["state"] == "failed" and job["attempts"] == 3          # the third failure is final
    assert 0 < waits[0] <= 300 and waits[0] > 50                      # 1st failure: ~1 min, 2nd: ~5 min
    events = db.execute("SELECT ok,code FROM notion_sync_events ORDER BY id").fetchall()
    assert [(e["ok"], e["code"]) for e in events] == [(False, "notion_unavailable"), (False, "rate_limited"), (False, "notion_unavailable")]
    assert run_jobs(db) == (0, 0)                                     # failed jobs are not retried by themselves
    outcome = ns.sync_now(db, uid, ns.TRANSPORT, 0)                    # 「立即同步」 gives it a fresh start
    assert outcome == dict(done=1, failed=0) and db.execute("SELECT state FROM notion_sync_jobs").fetchone()["state"] == "done"


def test_the_connection_test_only_reads(notion):
    result = ns.test_connection(notion.transport())
    assert result == dict(ok=True, code=None, steps=[dict(step="密钥", ok=True), dict(step="页面权限", ok=True)])
    assert {r[0] for r in notion.log} == {"GET"} and not notion.writes() and not notion.databases
    notion.shared = False
    assert ns.test_connection(notion.transport())["code"] == "not_found"
    notion.log.clear()
    import os
    os.environ["NOTION_TOKEN"] = "wrong"
    assert ns.test_connection(notion.transport())["code"] == "unauthorized"


def test_the_secret_is_not_in_logs_status_errors_or_events(db, running, notion, caplog):
    caplog.set_level(logging.DEBUG)
    uid, paper = submitted(db, running)
    switch(db, uid)
    ns.backfill(db, uid)
    notion.fail = [401]                                               # even a rejected secret is only a code
    run_jobs(db)
    state = ns.status(db, uid)
    dumped = json.dumps(state, default=str, ensure_ascii=False) + json.dumps([dict(r) for r in db.execute("SELECT * FROM notion_sync_events").fetchall()], default=str)
    dumped += json.dumps([dict(r) for r in db.execute("SELECT * FROM notion_sync_jobs").fetchall()], default=str)
    assert TOKEN not in dumped and TOKEN not in caplog.text
    assert state["recent"][0]["code"] == "unauthorized" and "密钥" in state["recent"][0]["message"]


def test_the_status_page_numbers_and_the_http_routes(client, db, running, notion):
    uid, paper = submitted(db, running)
    s = client.get("/v2/archive/status", headers=HEADERS).json()
    assert (s["configured"], s["consented"], s["success"], s["failed"], s["recent"]) == (True, False, 0, 0, [])
    assert client.post("/v2/archive/sync", headers=HEADERS).status_code == 409          # switched off
    assert client.post("/v2/archive/test", headers=HEADERS).json()["ok"] is True
    assert not notion.writes()
    assert client.put("/v2/archive/consent", headers=HEADERS, json={"enabled": True}).json() == dict(consented=True, configured=True)
    done = client.post("/v2/archive/sync", headers=HEADERS).json()
    assert done["done"] == 1 and done["success"] == 1 and done["last_synced_at"] and len(done["recent"]) == 1
    assert done["recent"][0]["ok"] and done["recent"][0]["detail"]["training"] == 1
    assert TOKEN not in json.dumps(done, default=str)
