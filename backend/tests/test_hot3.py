"""Requests that are too big are made smaller instead of failing for ever; the executor default; the progress notice."""
import pytest

from app import auto_execution, auto_grading, auto_insights, grading_recovery
from test_ai_automation import setup_ai  # noqa: F401  (fixture)


def cited(n_units, per_unit, size=900):
    return [dict(source_unit_id=u, locator=f"p{u}", excerpt=("甲" * size) + f"{u}-{k}")
            for u in range(1, n_units + 1) for k in range(per_unit)]


# ---- a request may carry four textbook entries and 5000 characters ---------------------------------------------------------

def test_up_to_the_limit_nothing_changes_above_it_the_entries_are_merged_one_per_paragraph():
    small = [dict(unit_id=i, text="x" * 100) for i in range(4)]
    assert auto_execution.fit_evidence(small) == small
    many = [dict(unit_id=1 + k % 3, text=f"段落{k}") for k in range(9)]            # nine passages of three paragraphs
    fitted = auto_execution.fit_evidence(many)
    assert [e["unit_id"] for e in fitted] == [1, 2, 3] and all(f"段落{k}" in "".join(e["text"] for e in fitted) for k in range(9))
    seven = [dict(unit_id=i, text="y" * 300) for i in range(7)]
    assert len(auto_execution.fit_evidence(seven)) == auto_execution.MAX_EVIDENCE_UNITS
    long = [dict(unit_id=i, text="z" * 4000) for i in range(3)]
    out = auto_execution.fit_evidence(long)
    assert sum(len(e["text"]) for e in out) <= auto_execution.MAX_EVIDENCE_CHARS


@pytest.mark.parametrize("units,per_unit", [(7, 1), (2, 9), (6, 3)])
def test_a_grading_request_for_a_question_with_many_citations_passes_the_execution_guard(units, per_unit):
    q = dict(subject="biochemistry", question_type="term", stem="s", options={}, answer="a", accepted_answers=[], rubric=[], blanks=[], max_points=5,
             knowledge_tags=[], evidence=cited(units, per_unit, size=200))
    payload = auto_grading.grading_payload(1, dict(question_id=2, attempt_id=3), q, {"text": "我的作答", "blanks": []})
    auto_execution.bounded_material(payload)                       # raised evidence_exceeds_hard_cap before
    assert len(payload["evidence"]) <= auto_execution.MAX_EVIDENCE_UNITS


def test_a_task_stuck_on_the_evidence_limit_is_cut_down_and_starts_again(db, setup_ai):
    from psycopg.types.json import Jsonb

    payload = dict(instructions="x", evidence=[dict(unit_id=i % 3, locator="p", text=f"段落{i}") for i in range(8)])
    jid = db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code,attempts) VALUES ('g-stuck',%s,'grading','retrying','{}',%s,%s,3) RETURNING id",
        (setup_ai, Jsonb(payload), "ValueError:evidence_exceeds_hard_cap")).fetchone()["id"]
    other = db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code) VALUES ('g-other',%s,'grading','retrying','{}',%s,'provider_http_400') RETURNING id",
        (setup_ai, Jsonb(payload))).fetchone()["id"]
    assert grading_recovery.shrink_oversized_requests(db, setup_ai) == 1
    row = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (jid,)).fetchone()
    assert row["state"] == "retrying" and row["attempts"] == 3 and row["transient_attempts"] == 3 and row["error_code"] is None and len(row["payload"]["evidence"]) == 3
    auto_execution.bounded_material(row["payload"])
    assert len(db.execute("SELECT payload FROM ai_jobs WHERE id=%s", (other,)).fetchone()["payload"]["evidence"]) == 8     # other failures untouched
    assert grading_recovery.shrink_oversized_requests(db, setup_ai) == 0


# ---- the advice request (insights) -----------------------------------------------------------------------------------------

def rows_of(n, size):
    return [dict(question_id=i, answer="我的答案" * 10, points=0, feedback={"c": "x" * 200},
                 snapshot=dict(question_type="term", stem="题" * size, knowledge_tags=["a"],
                               evidence=[dict(source_unit_id=u, excerpt="乙" * 400) for u in (1, 2)]))
            for i in range(1, n + 1)]


def test_the_advice_request_is_cut_to_fit_and_only_skipped_when_nothing_fits(db, monkeypatch):
    monkeypatch.setattr(auto_insights, "snapshot_unit", lambda conn, uid: dict(unit_id=uid, unit_sha256="s", text="丙" * 20000))
    import json

    fitted = auto_insights._fit(db, rows_of(3, 300))
    assert fitted is not None and len(json.dumps(fitted, ensure_ascii=False).encode()) + 3000 <= auto_insights.MAX_REQUEST_BYTES
    assert all(len(e["text"]) <= 1500 and e["text"].startswith("乙") for m in fitted for e in m["evidence"])     # the cited passages, not the whole paragraph
    assert auto_insights._fit(db, rows_of(1, 30000)) is None            # one mistake whose own question is too big: skipped, not an error


def test_a_paper_whose_advice_request_cannot_fit_is_skipped_without_an_error(db, monkeypatch):
    monkeypatch.setattr(auto_insights, "_fit", lambda conn, rows: None)
    monkeypatch.setattr(db, "execute", db.execute)
    attempt = dict(id=1, paper_id=1)
    class Rows:
        def fetchall(self):
            return rows_of(1, 10)
    real = db.execute
    monkeypatch.setattr(type(db), "execute", lambda self, *a, **k: Rows() if a and "answers_v2 a JOIN paper_items_v2" in a[0] else real(*a, **k), raising=False)
    assert auto_insights._plan_attempt(db, 1, attempt) is False


# ---- who takes the tasks --------------------------------------------------------------------------------------------------

def test_everything_starts_with_deepseek_and_the_page_says_how_many_wait_for_the_routine(db, setup_ai, monkeypatch):
    from psycopg.types.json import Jsonb

    from app import external_executor as ext, progress

    for name in ("EXECUTOR_GENERATION", "EXECUTOR_REVIEW", "EXECUTOR_GRADING"):
        monkeypatch.delenv(name, raising=False)
    assert ext.policy(db)["generation"] == "deepseek" and ext.waiting_for_claude(db) is None

    def add(key):
        return db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,'generation','pending','{}',%s) RETURNING id",
                          (key, setup_ai, Jsonb({"subject": "biochemistry", "question_type": "fill_blank"}))).fetchone()["id"]

    for n in range(3):
        add(f"w{n}")
    assert ext.waiting_for_claude(db) is None                        # DeepSeek's: nobody is waited for
    ext.save_policy(db, {"generation": "claude_first", "fallback_hours": 4})
    db.execute("UPDATE ai_jobs SET created_at=clock_timestamp()-interval '1 hour'")
    waiting = ext.waiting_for_claude(db)
    assert waiting["count"] == 3 and waiting["fallback_hours"] == 4 and 2.9 <= waiting["hand_over_hours"] <= 3.0 and waiting["oldest_hours"] >= 1
    assert progress.report(db, setup_ai)["executors"]["waiting"]["count"] == 3          # the progress page reads it from here
    ext.save_policy(db, {"generation": "claude"})
    assert ext.waiting_for_claude(db)["hand_over_hours"] is None      # 'Claude only': never handed over
