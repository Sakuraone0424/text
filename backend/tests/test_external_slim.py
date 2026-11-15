"""What a Routine is sent: the task (small), the id of its rules (fetched once), its size; and why it put a task back."""
import json

from app import external_executor as ext
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_external_executor import HEADERS, api, lease_one, one_job, submit  # noqa: F401  (fixtures/helpers)
from test_gap_generation import corpus  # noqa: F401  (fixture)


def test_a_lease_is_the_task_and_the_id_of_its_rules_not_the_rules_over_and_over(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    got = lease_one(api, full=False)[0]
    assert "payload" not in got and "prompt" not in got                                  # neither the payload nor the message built from it again
    task = got["task"]
    assert "instructions" not in task and "schema" not in task and {"evidence", "style"} <= set(task)
    assert [{k: v for k, v in u.items() if k != "paragraph_id"} for u in task["evidence"]] == job["payload"]["evidence"]    # the paragraphs to quote from are never touched
    assert all(u["paragraph_id"] == u["unit_id"] for u in task["evidence"])                                              # ... each with its paragraph_id
    assert got["chars"] == len(json.dumps(task, ensure_ascii=False, separators=(",", ":"))) and got["bytes"] >= got["chars"]
    assert got["chars"] < ext.TASK_CHAR_TARGET and got["chars"] < len(json.dumps(job["payload"], ensure_ascii=False)) / 2
    rules = api.get(f"/v2/external/rules/{got['rules_id']}", headers=HEADERS)
    assert rules.status_code == 200
    assert rules.json()["instructions"] == job["payload"]["instructions"] and rules.json()["schema"] == job["payload"]["schema"]
    assert "evidence-bound exam assistant" in rules.json()["prefix"]
    assert api.get("/v2/external/rules/" + "0" * 16, headers=HEADERS).status_code == 404
    assert api.get("/v2/external/rules/not-an-id", headers=HEADERS).status_code == 422
    assert api.get(f"/v2/external/rules/{got['rules_id']}").status_code == 401           # the same token door


def test_five_tasks_fit_in_one_answer(db, corpus, setup_ai, api, monkeypatch):
    from app import auto_generation as gen

    monkeypatch.setattr(gen, "bucket_gaps", lambda conn, uid: [("biochemistry", "fill_blank", 9), ("cell_biology", "fill_blank", 9)])
    for _ in range(5):
        assert gen.plan_gap(db, setup_ai, max_active=9) is not None
    r = api.get("/v2/external/lease?kind=generation&max=5&session=session-aaaaaa", headers=HEADERS)
    assert r.status_code == 200
    leases = r.json()["leases"]
    assert 1 <= len(leases) <= 5 and len(r.text) < 30000                                  # what a reader was cut off at
    assert len({x["rules_id"] for x in leases}) <= 2                                       # the same rules are named, not repeated


def test_a_big_task_is_shortened_in_its_style_examples_never_in_its_paragraphs():
    payload = dict(instructions="x" * 7000, schema={"s": "y" * 4000}, evidence=[dict(unit_id=1, text="教材" * 1200)],
                   style=[dict(unit_id=9, text="真题样例" * 400)] * 3, bibliographies={"f": dict(textbook="书", authors="作者", edition="版", isbn=None, extra="z" * 3000)})
    slim = ext.slim_task(payload, limit=8000)
    assert len(json.dumps(slim, ensure_ascii=False, separators=(",", ":"))) <= 8000
    assert [u["text"] for u in slim["evidence"]] == [u["text"] for u in payload["evidence"]] and "instructions" not in slim and "schema" not in slim
    assert all(len(s["text"]) <= 600 for s in slim["style"])


# ---- putting a task back, with a reason -----------------------------------------------------------------------------------------

def test_release_takes_an_optional_reason_and_keeps_it(db, corpus, setup_ai, api):
    one_job(db, setup_ai)
    lease = lease_one(api)[0]
    r = api.post("/v2/external/release", headers=HEADERS, json={"lease_id": lease["lease_id"], "reason": "教材段落里没有相关内容"})
    assert r.status_code == 200 and r.json() == {"released": True}
    assert db.execute("SELECT release_reason FROM external_leases WHERE lease_id=%s", (lease["lease_id"],)).fetchone()["release_reason"] == "教材段落里没有相关内容"
    assert ext.release_reasons(db)[0]["reason"] == "教材段落里没有相关内容" and ext.release_reasons(db)[0]["count"] == 1


def test_release_without_a_reason_still_works_and_a_too_long_reason_is_refused(db, corpus, setup_ai, api):
    one_job(db, setup_ai)
    lease = lease_one(api)[0]
    assert api.post("/v2/external/release", headers=HEADERS, json={"lease_id": lease["lease_id"], "reason": "x" * 201}).status_code == 422
    assert api.post("/v2/external/release", headers=HEADERS, json={"lease_id": lease["lease_id"]}).status_code == 200
    assert ext.release_reasons(db)[0]["reason"] == "未写原因"
    again = lease_one(api, session="session-bbbbbb")[0]                               # the task is leasable again
    assert api.post("/v2/external/release", headers=HEADERS, json={"lease_id": again["lease_id"], "reason": "  "}).status_code == 200


def test_the_progress_page_data_lists_why_tasks_were_put_back(db, corpus, setup_ai, api):
    from app import progress

    one_job(db, setup_ai)
    lease = lease_one(api)[0]
    api.post("/v2/external/release", headers=HEADERS, json={"lease_id": lease["lease_id"], "reason": "找不到相关段落"})
    assert progress.report(db, setup_ai)["executors"]["released"] == [dict(reason="找不到相关段落", count=1, last_at=ext.release_reasons(db)[0]["last_at"])]
