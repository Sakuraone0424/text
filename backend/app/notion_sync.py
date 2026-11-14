"""Notion sync: the learner's own results filed into two Notion databases under one parent page.

Setup (server side only): NOTION_TOKEN (an Internal Integration secret) and NOTION_PARENT_PAGE_ID (a page shared with that
integration). The first sync creates 「训练记录」 (one row per submitted paper) and 「错题本」 (one row per question the learner
lost points on) under the parent page, or reuses databases of those names that are already there. Nothing is sent unless the
learner switched the sync on (archive_consent). The secret is read from the environment at the moment of a request, never
stored, logged or returned. Textbook paragraphs are never sent: only 「教材第 X 章 · 第 Y 页」.

Every write is idempotent: a paper is found by its id, a mistake by its question id, so a retry (or a second run) updates the
same row instead of adding another.
"""

import os
import re
import time
from datetime import timedelta

import httpx
from psycopg.types.json import Jsonb

API = "https://api.notion.com/v1/"
TRANSPORT = None   # tests put an httpx transport here; None means the real network
VERSION = "2022-06-28"
TRAINING_DB, MISTAKES_DB = "训练记录", "错题本"
RETRY_DELAYS = (60, 300, 1800)     # seconds before the 2nd, 3rd and (never) 4th try: three tries in all
MAX_TRIES = 3
SUBJECT_NAME = {"biochemistry": "生物化学", "cell_biology": "细胞生物学"}
TYPE_NAME = {"single_choice": "单选", "fill_blank": "填空", "term": "名词解释", "short_answer": "简答", "essay": "问答"}


class NotionError(Exception):
    """A failure with a stable code (never the response body, never the token)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# What each code means for the learner (shown on the archive page).
MESSAGES = {
    "not_configured": "服务器还没有配置 NOTION_TOKEN 和 NOTION_PARENT_PAGE_ID",
    "unauthorized": "密钥无效或已被撤销，请重新复制集成的密钥",
    "forbidden": "这个集成没有权限，请检查集成的权限设置",
    "not_found": "找不到这个页面：请把页面分享（连接）给集成，并确认页面 ID 正确",
    "invalid_request": "Notion 拒绝了这次请求（数据库结构或内容不合要求）",
    "conflict": "Notion 里的数据正在被修改，稍后会自动重试",
    "rate_limited": "Notion 请求太频繁，稍后会自动重试",
    "notion_unavailable": "Notion 暂时不可用，稍后会自动重试",
    "network": "连不上 Notion，稍后会自动重试",
    "database_missing": "找不到训练记录或错题本数据库，已重新创建失败",
    "unexpected": "出现未预料的错误，稍后会自动重试",
}


def configuration():
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    parent = page_id(os.environ.get("NOTION_PARENT_PAGE_ID"))
    return token, parent


def configured():
    token, parent = configuration()
    return bool(token and parent)


def page_id(value):
    """The page id out of a bare id, a dashed id or a page URL (the id is the last 32 hex characters of the address); None
    if there is none."""
    text = re.split(r"[?#]", (value or "").strip())[0].rstrip("/")
    found = re.search(r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})$", text)
    if not found:
        return None
    raw = found.group(1).replace("-", "").lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


class Notion:
    """The few Notion calls this module makes. Errors become NotionError(code); `pause` spaces the calls out (Notion allows
    about three requests a second)."""

    def __init__(self, token, transport=None, pause=0.35):
        self.pause = pause
        self.client = httpx.Client(base_url=API, timeout=20, transport=transport or TRANSPORT,
                                   headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION})

    def call(self, method, path, body=None):
        if self.pause:
            time.sleep(self.pause)
        try:
            response = self.client.request(method, path, json=body)
        except httpx.TimeoutException:
            raise NotionError("network") from None
        except httpx.HTTPError:
            raise NotionError("network") from None
        if response.status_code >= 400:
            raise NotionError({400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
                               429: "rate_limited"}.get(response.status_code, "notion_unavailable" if response.status_code >= 500 else "unexpected"))
        return response.json()

    def close(self):
        self.client.close()


def make_client(transport=None, pause=0.35):
    token, _ = configuration()
    if not configured():
        raise NotionError("not_configured")
    return Notion(token, transport, pause)


# ---------------------------------------------------------------- the two databases

TRAINING_PROPERTIES = {
    "名称": {"title": {}}, "试卷ID": {"number": {"format": "number"}}, "日期": {"date": {}},
    "生化得分": {"number": {"format": "number"}}, "生化满分": {"number": {"format": "number"}},
    "细胞得分": {"number": {"format": "number"}}, "细胞满分": {"number": {"format": "number"}},
    "总分": {"number": {"format": "number"}}, "满分": {"number": {"format": "number"}},
    "单选得分率": {"number": {"format": "percent"}}, "填空得分率": {"number": {"format": "percent"}},
    "名词解释得分率": {"number": {"format": "percent"}}, "简答得分率": {"number": {"format": "percent"}},
    "问答得分率": {"number": {"format": "percent"}},
    "用时（分钟）": {"number": {"format": "number"}}, "含复用题": {"checkbox": {}}, "成绩页": {"url": {}},
}
MISTAKE_PROPERTIES = {
    "题干": {"title": {}}, "题目ID": {"number": {"format": "number"}},
    "科目": {"select": {"options": [{"name": n} for n in SUBJECT_NAME.values()]}},
    "题型": {"select": {"options": [{"name": n} for n in TYPE_NAME.values()]}},
    "章节": {"rich_text": {}}, "我的答案": {"rich_text": {}}, "参考答案": {"rich_text": {}},
    "得分": {"number": {"format": "number"}}, "满分": {"number": {"format": "number"}},
    "评分要点": {"rich_text": {}}, "依据": {"rich_text": {}}, "错误次数": {"number": {"format": "number"}},
    "下次复习": {"date": {}}, "最近一次": {"date": {}}, "试卷ID": {"number": {"format": "number"}},
}


def _state(conn, key):
    row = conn.execute("SELECT value FROM notion_state WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key, value):
    conn.execute("INSERT INTO notion_state(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,"
                 "updated_at=clock_timestamp()", (key, Jsonb(value)))


def _child_databases(client, parent):
    found, cursor = {}, None
    for _ in range(50):
        result = client.call("GET", f"blocks/{parent}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        for block in result.get("results", []):
            if block.get("type") == "child_database" and not block.get("archived"):
                found.setdefault(block["child_database"]["title"], block["id"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return found


def ensure_databases(client, conn, parent):
    """The ids of 训练记录 and 错题本 under `parent`: remembered ones if they still exist, else the databases of those names
    already under the page, else new ones. Existing databases are reused, never duplicated."""
    state = _state(conn, "databases") or {}
    ids = {}
    if state.get("parent") == parent:
        for name, key in ((TRAINING_DB, "training"), (MISTAKES_DB, "mistakes")):
            if state.get(key):
                try:
                    if not client.call("GET", f"databases/{state[key]}").get("archived"):
                        ids[key] = state[key]
                except NotionError as error:
                    if error.code != "not_found":
                        raise
    missing = [(n, k) for n, k in ((TRAINING_DB, "training"), (MISTAKES_DB, "mistakes")) if k not in ids]
    if missing:
        existing = _child_databases(client, parent)
        for name, key in missing:
            if name in existing:
                ids[key] = existing[name]
        for name, key in missing:
            if key not in ids:
                created = client.call("POST", "databases", {
                    "parent": {"type": "page_id", "page_id": parent},
                    "title": [{"type": "text", "text": {"content": name}}],
                    "properties": TRAINING_PROPERTIES if key == "training" else MISTAKE_PROPERTIES})
                ids[key] = created["id"]
    _set_state(conn, "databases", dict(parent=parent, **ids))
    return ids


# ---------------------------------------------------------------- what is sent

def rich(text, limit=1900):
    text = str(text or "")
    return [{"type": "text", "text": {"content": text[i:i + limit]}} for i in range(0, min(len(text), limit * 20), limit)] or []


def _site_link(paper_id):
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/training#/stats/history" if base.startswith("http") else None


def paper_record(conn, user_id, paper_id):
    """One row of 训练记录 for a submitted and fully graded paper."""
    from app.quiz_v2 import result

    r = result(conn, user_id, paper_id)
    row = conn.execute(
        "SELECT p.paper_date, s.started_at, t.submitted_at FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id "
        "LEFT JOIN training_sessions s ON s.paper_id=p.id WHERE p.id=%s", (paper_id,)).fetchone()
    by_type = {}
    for a in r["answers"]:
        if a["excluded"] or a["points"] is None:
            continue
        got, top = by_type.get(a["question_type"], (0, 0))
        by_type[a["question_type"]] = (got + a["points"], top + a["max_points"])
    minutes = None
    if row["started_at"] and row["submitted_at"]:
        minutes = round((row["submitted_at"] - row["started_at"]).total_seconds() / 60, 1)
    scores = r["subject_scores"]
    return dict(
        paper_id=paper_id, date=row["paper_date"].isoformat(), score=r["score"], max_points=r["max_points"],
        subjects={s: scores.get(s, {}) for s in SUBJECT_NAME}, rates={k: round(a / b, 4) for k, (a, b) in by_type.items() if b},
        minutes=minutes, reused=bool(r["includes_reused"]), link=_site_link(paper_id), answers=r["answers"],
    )


def training_properties(rec):
    def num(v):
        return {"number": None if v is None else v}

    props = {
        "名称": {"title": rich(f"{rec['date']} 训练 #{rec['paper_id']}")}, "试卷ID": num(rec["paper_id"]), "日期": {"date": {"start": rec["date"]}},
        "总分": num(rec["score"]), "满分": num(rec["max_points"]), "用时（分钟）": num(rec["minutes"]), "含复用题": {"checkbox": rec["reused"]},
        "成绩页": {"url": rec["link"]},
    }
    for subject, (got, top) in (("biochemistry", ("生化得分", "生化满分")), ("cell_biology", ("细胞得分", "细胞满分"))):
        s = rec["subjects"].get(subject) or {}
        props[got] = num(s.get("score") if s.get("score") is not None else s.get("graded_points"))
        props[top] = num(s.get("max_points"))
    for kind, name in TYPE_NAME.items():
        props[f"{name}得分率"] = num(rec["rates"].get(kind))
    return props


def _position(evidence):
    """「依据：教材第 X 章 · 第 Y 页」 — the place only, never the paragraph."""
    places = []
    for e in evidence or []:
        chapter = str(e.get("chapter") or "").strip()
        page = e.get("ocr_page")
        text = (chapter + (" · " if chapter and page else "") + (f"第 {page} 页" if page else "")).strip()
        if text and text not in places:
            places.append(text)
    return "依据：教材" + "；".join(places[:4]) if places else ""


def mistake_rows(conn, user_id, rec):
    """The rows of 错题本 for the answers of this paper that lost points (reported questions do not count)."""
    out = []
    for a in rec["answers"]:
        if a["excluded"] or a["points"] is None or a["points"] >= a["max_points"]:
            continue
        feedback = conn.execute("SELECT feedback FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.question_id=%s",
                                (rec["paper_id"], a["question_id"])).fetchone()["feedback"] or {}
        reasons = feedback.get("criterion_reasons") or []
        lines = []
        for i, c in enumerate(feedback.get("criteria") or []):
            reason = f"：{str(reasons[i])[:200]}" if i < len(reasons) and reasons[i] else ""
            lines.append(f"{'✔' if c['points'] >= c['max_points'] else '✘'} {c['description'][:120]}（{c['points']}/{c['max_points']}）{reason}")
        wrong = conn.execute(
            "SELECT count(*) AS n FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
            "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id WHERE p.user_id=%s AND a.question_id=%s "
            "AND a.points IS NOT NULL AND a.points < (i.snapshot->>'max_points')::float8", (user_id, a["question_id"])).fetchone()["n"]
        due = conn.execute("SELECT due FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (user_id, a["question_id"])).fetchone()
        chapters = [str(e.get("chapter") or "") for e in a["evidence"] or [] if e.get("chapter")]
        out.append(dict(question_id=a["question_id"], subject=SUBJECT_NAME.get(a["subject"], a["subject"]), type=TYPE_NAME.get(a["question_type"], a["question_type"]),
                        chapter=chapters[0] if chapters else "", stem=a["stem"], answer=a["answer"], solution=a["solution_display"], points=a["points"],
                        max_points=a["max_points"], rubric="\n".join(lines), basis=_position(a["evidence"]), wrong=wrong,
                        due=due["due"].date().isoformat() if due else None, date=rec["date"], paper_id=rec["paper_id"]))
    return out


def mistake_properties(m):
    def num(v):
        return {"number": v}

    return {
        "题干": {"title": rich(m["stem"][:200])}, "题目ID": num(m["question_id"]), "科目": {"select": {"name": m["subject"]}},
        "题型": {"select": {"name": m["type"]}}, "章节": {"rich_text": rich(m["chapter"])}, "我的答案": {"rich_text": rich(m["answer"])},
        "参考答案": {"rich_text": rich(m["solution"])}, "得分": num(m["points"]), "满分": num(m["max_points"]),
        "评分要点": {"rich_text": rich(m["rubric"])}, "依据": {"rich_text": rich(m["basis"])}, "错误次数": num(m["wrong"]),
        "下次复习": {"date": {"start": m["due"]} if m["due"] else None}, "最近一次": {"date": {"start": m["date"]}}, "试卷ID": num(m["paper_id"]),
    }


# ---------------------------------------------------------------- writing, idempotently

def _find(client, database, prop, value):
    found = client.call("POST", f"databases/{database}/query", {"filter": {"property": prop, "number": {"equals": value}}, "page_size": 2})
    results = found.get("results", [])
    return results[0]["id"] if results else None


def _upsert(client, database, prop, key, properties, known=None):
    """Update the row of this key (a remembered page id, else the one found by key) or create it: never two rows."""
    page = known
    if page:
        try:
            client.call("PATCH", f"pages/{page}", {"properties": properties})
            return page
        except NotionError as error:
            if error.code != "not_found":
                raise
    page = _find(client, database, prop, key)
    if page:
        client.call("PATCH", f"pages/{page}", {"properties": properties})
        return page
    return client.call("POST", "pages", {"parent": {"database_id": database}, "properties": properties})["id"]


def sync_attempt(conn, client, job):
    """File one paper and its lost questions. Returns the counts."""
    _, parent = configuration()
    paper = conn.execute("SELECT paper_id FROM attempts_v2 WHERE id=%s", (job["attempt_id"],)).fetchone()["paper_id"]
    rec = paper_record(conn, job["user_id"], paper)
    mistakes = mistake_rows(conn, job["user_id"], rec)
    ids = ensure_databases(client, conn, parent)
    training_page = _upsert(client, ids["training"], "试卷ID", paper, training_properties(rec), job["training_page_id"])
    conn.execute("UPDATE notion_sync_jobs SET training_page_id=%s WHERE id=%s", (training_page, job["id"]))
    for m in mistakes:
        known = conn.execute("SELECT page_id FROM notion_mistake_pages WHERE user_id=%s AND question_id=%s", (job["user_id"], m["question_id"])).fetchone()
        page = _upsert(client, ids["mistakes"], "题目ID", m["question_id"], mistake_properties(m), known["page_id"] if known else None)
        conn.execute("INSERT INTO notion_mistake_pages(user_id,question_id,page_id) VALUES (%s,%s,%s) "
                     "ON CONFLICT(user_id,question_id) DO UPDATE SET page_id=EXCLUDED.page_id", (job["user_id"], m["question_id"], page))
    return dict(training=1, mistakes=len(mistakes))


# ---------------------------------------------------------------- queue, retries, status

def consented(conn, user_id):
    row = conn.execute("SELECT enabled FROM archive_consent WHERE user_id=%s", (user_id,)).fetchone()
    return bool(row and row["enabled"])


def attempt_ready(conn, attempt_id):
    """Submitted and every counted answer graded (the free-text ones are graded after the submit)."""
    row = conn.execute(
        "SELECT t.paper_id, count(*) FILTER (WHERE a.points IS NULL AND NOT EXISTS (SELECT 1 FROM question_reports r WHERE r.paper_id=t.paper_id AND r.question_id=a.question_id)) AS pending "
        "FROM attempts_v2 t JOIN answers_v2 a ON a.attempt_id=t.id WHERE t.id=%s GROUP BY t.paper_id", (attempt_id,)).fetchone()
    return bool(row) and row["pending"] == 0


def enqueue(conn, user_id, paper_id):
    """Queue the sync of a paper once it is graded (idempotent; nothing at all while the switch is off)."""
    if not consented(conn, user_id):
        return None
    attempt = conn.execute("SELECT id FROM attempts_v2 WHERE paper_id=%s", (paper_id,)).fetchone()
    if not attempt or not attempt_ready(conn, attempt["id"]):
        return None
    conn.execute("INSERT INTO notion_sync_jobs(user_id,attempt_id) VALUES (%s,%s) ON CONFLICT(attempt_id) DO NOTHING", (user_id, attempt["id"]))
    return attempt["id"]


def backfill(conn, user_id):
    """When the switch is turned on: queue every graded paper of this learner that is not queued yet."""
    for row in conn.execute("SELECT t.paper_id FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id WHERE p.user_id=%s ORDER BY t.id", (user_id,)).fetchall():
        enqueue(conn, user_id, row["paper_id"])


def _event(conn, user_id, job_id, ok, code=None, detail=None):
    conn.execute("INSERT INTO notion_sync_events(user_id,job_id,ok,code,detail) VALUES (%s,%s,%s,%s,%s)", (user_id, job_id, ok, code, Jsonb(detail or {})))


def process_due(conn, client, limit=3):
    """Run up to `limit` due jobs of learners who switched the sync on. A failure schedules the next try (1 min, 5 min), after
    the third failure the job waits for 「立即同步」. Never raises for a Notion problem; returns (done, failed)."""
    done = failed = 0
    for _ in range(limit):
        job = conn.execute(
            "SELECT j.* FROM notion_sync_jobs j JOIN archive_consent c ON c.user_id=j.user_id AND c.enabled "
            "WHERE j.state='pending' AND j.next_retry<=clock_timestamp() ORDER BY j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1").fetchone()
        if not job:
            break
        try:
            counts = sync_attempt(conn, client, job)
            conn.execute("UPDATE notion_sync_jobs SET state='done',last_code=NULL,synced_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s", (job["id"],))
            _event(conn, job["user_id"], job["id"], True, None, counts)
            done += 1
        except NotionError as error:
            code = error.code
            tries = job["attempts"] + 1
            if tries >= MAX_TRIES or code in ("unauthorized", "not_configured"):
                conn.execute("UPDATE notion_sync_jobs SET state='failed',attempts=%s,last_code=%s,updated_at=clock_timestamp() WHERE id=%s", (tries, code, job["id"]))
            else:
                conn.execute("UPDATE notion_sync_jobs SET attempts=%s,last_code=%s,next_retry=clock_timestamp()+%s*interval '1 second',updated_at=clock_timestamp() WHERE id=%s",
                             (tries, code, RETRY_DELAYS[min(tries - 1, len(RETRY_DELAYS) - 1)], job["id"]))
            _event(conn, job["user_id"], job["id"], False, code, dict(try_=tries))
            failed += 1
            if code in ("unauthorized", "not_configured"):
                break
        except (KeyError, ValueError, LookupError):
            conn.execute("UPDATE notion_sync_jobs SET state='failed',attempts=attempts+1,last_code='unexpected',updated_at=clock_timestamp() WHERE id=%s", (job["id"],))
            _event(conn, job["user_id"], job["id"], False, "unexpected")
            failed += 1
    return done, failed


def tick(conn, transport=None, pause=0.35):
    """The worker's turn: queue graded papers of switched-on learners, then run a few due jobs."""
    if not configured():
        return None
    for row in conn.execute("SELECT user_id FROM archive_consent WHERE enabled").fetchall():
        backfill(conn, row["user_id"])
    if not conn.execute("SELECT 1 FROM notion_sync_jobs j JOIN archive_consent c ON c.user_id=j.user_id AND c.enabled WHERE j.state='pending' AND j.next_retry<=clock_timestamp() LIMIT 1").fetchone():
        return (0, 0)
    client = make_client(transport, pause)
    try:
        return process_due(conn, client)
    finally:
        client.close()


def sync_now(conn, user_id, transport=None, pause=0.35, limit=3):
    """「立即同步」: failed jobs get a fresh start, missing papers are queued, a few are run right now."""
    if not consented(conn, user_id):
        raise PermissionError("sync_switched_off")
    if not configured():
        raise NotionError("not_configured")
    conn.execute("UPDATE notion_sync_jobs SET state='pending',attempts=0,next_retry=clock_timestamp(),last_code=NULL WHERE user_id=%s AND state='failed'", (user_id,))
    backfill(conn, user_id)
    client = make_client(transport, pause)
    try:
        done, failed = process_due(conn, client, limit)
    finally:
        client.close()
    return dict(done=done, failed=failed)


def test_connection(transport=None, pause=0):
    """Read-only: is the secret valid, and can the integration see the parent page? Writes nothing."""
    token, parent = configuration()
    if not token or not parent:
        return dict(ok=False, code="not_configured", steps=[])
    client = Notion(token, transport, pause)
    steps = []
    try:
        client.call("GET", "users/me")
        steps.append(dict(step="密钥", ok=True))
        client.call("GET", f"pages/{parent}")
        steps.append(dict(step="页面权限", ok=True))
        return dict(ok=True, code=None, steps=steps)
    except NotionError as error:
        steps.append(dict(step="密钥" if not steps else "页面权限", ok=False))
        return dict(ok=False, code=error.code, steps=steps)
    finally:
        client.close()


def status(conn, user_id):
    counts = {r["state"]: r["n"] for r in conn.execute("SELECT state,count(*) AS n FROM notion_sync_jobs WHERE user_id=%s GROUP BY 1", (user_id,)).fetchall()}
    last = conn.execute("SELECT max(synced_at) AS t FROM notion_sync_jobs WHERE user_id=%s", (user_id,)).fetchone()["t"]
    events = conn.execute("SELECT at,ok,code,detail FROM notion_sync_events WHERE user_id=%s ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    return dict(
        configured=configured(), consented=consented(conn, user_id), last_synced_at=last,
        success=counts.get("done", 0), failed=counts.get("failed", 0), pending=counts.get("pending", 0),
        recent=[dict(at=e["at"], ok=e["ok"], code=e["code"], message=None if e["ok"] else MESSAGES.get(e["code"], MESSAGES["unexpected"]), detail=e["detail"]) for e in events],
    )
