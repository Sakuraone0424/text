"""Quality PR 1: analysis tools (review errors split around PR #26, the past-exam route, single-choice option checks, the
failure kinds first in the report) and the stop-loss: past-exam term requests take at most 20% of the tasks in flight."""
import pytest

from app import ai_jobs, past_exam_import as pei, waste_report
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import corpus, review_round  # noqa: F401


def test_the_term_is_read_from_the_stem_and_looked_for_word_for_word_in_the_paragraphs():
    assert pei.term_of_stem("1. 名词解释：糖酵解（5分）") == "糖酵解"
    assert pei.term_of_stem("糖酵解") == "糖酵解"
    units = [{"text": "葡萄糖经 糖酵解 途径生成丙酮酸。"}]          # spacing differs: still the same word
    assert pei.term_in_units("糖酵解", units)
    assert not pei.term_in_units("三羧酸循环", units)
    assert not pei.term_in_units("", units)


def _generation(db, uid, key, origin=None, kind="term"):
    payload = dict(subject="biochemistry", question_type=kind, stem=key, evidence=[])
    if origin:
        payload["origin"] = origin
    return ai_jobs.enqueue(db, uid, key, "generation", payload)


def test_past_exam_term_requests_are_at_most_a_fifth_of_the_tasks_in_flight(db, setup_ai):
    assert pei.term_room_now(db) == 0                                  # nothing else in flight: no term request
    for i in range(4):
        _generation(db, setup_ai, f"new-{i}")
    assert pei.term_room_now(db) == 1                                  # 1 of 5 = 20%
    _generation(db, setup_ai, "past-term", origin=pei.ORIGIN_PAST_EXAM)
    assert pei.term_room_now(db) == 0
    _generation(db, setup_ai, "past-short", origin=pei.ORIGIN_PAST_EXAM, kind="short_answer")   # other past-exam types are not capped
    assert pei.term_room_now(db) == 0 and pei.term_room_now(db, share=1) > 1000


def test_plan_gives_the_places_to_other_types_and_queues_terms_only_within_the_cap(db, corpus, setup_ai, monkeypatch):
    doc = db.execute("SELECT d.id AS d, min(u.id) AS u FROM source_documents d JOIN source_units u ON u.document_id=d.id GROUP BY d.id LIMIT 1").fetchone()
    for kind, n in (("term", 5), ("fill_blank", 2), ("short_answer", 2)):
        for i in range(n):
            stem = f"{kind}{i}"
            db.execute("INSERT INTO past_exam_items(subject,question_type,stem,stem_hash,document_id,unit_id,question_number,year,years) "
                       "VALUES ('biochemistry',%s,%s,%s,%s,%s,%s,2024,'[2024]')",
                       (kind, stem, pei.stem_hash(kind, stem), doc["d"], doc["u"], str(i + 1)))
    for i in range(8):
        _generation(db, setup_ai, f"new-{i}")                          # 8 new tasks in flight -> 2 term places
    order = []
    monkeypatch.setattr(pei, "_enqueue_item", lambda conn, uid, item: order.append(item["question_type"]) or "queued")
    monkeypatch.setattr(pei.cost_control, "bank_target", lambda: 0)
    pei.plan(db, setup_ai, batch=20)
    assert "term" in order and order.count("term") == 2               # capped at 8 * 0.25 = 2 places
    assert order.index("term") > max(i for i, k in enumerate(order) if k != "term")   # and queued after every other type
    assert order.count("fill_blank") >= 2 and order.count("short_answer") >= 2


def test_the_failure_kinds_come_first_and_every_new_section_is_in_the_report(db, corpus, setup_ai):
    qid, review = review_round(db, setup_ai)
    stats = waste_report.full_stats(db, 24)
    assert list(stats)[0] == "review_failure_kinds" and list(stats)[1] == "review_pass_by_type"
    for key in ("single_choice_options", "review_errors", "past_exam_route"):
        assert key in stats
    route = stats["past_exam_route"]
    assert route["term_cap_share"] == pei.PAST_TERM_MAX_SHARE and "insufficient_textbook_evidence" in route


def test_review_errors_are_split_before_and_after_the_first_contract_10_review(db, corpus, setup_ai):
    qid, review = review_round(db, setup_ai)
    db.execute("UPDATE ai_jobs SET state='blocked',result=jsonb_set(COALESCE(result,'{}'::jsonb),'{dispatch_failure}',%s::jsonb) WHERE id=%s",
               ('{"code":"evidence_version_changed"}', review["id"]))
    errors = waste_report.review_value_errors(db, 24)
    assert errors["contract10_first_job_at"] is not None
    assert any("evidence_version_changed" in k and "合约10" in k for k in errors["after_pr26"]) and not errors["before_pr26"]
    row = errors["rows"][0]
    assert row["code"] == "evidence_version_changed" and row["after_pr26"] and row["count"] == 1
