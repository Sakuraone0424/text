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
    assert any("evidence_version_changed" in k and "合约11" in k for k in errors["after_pr26"]) and not errors["before_pr26"]
    row = errors["rows"][0]
    assert row["code"] == "evidence_version_changed" and row["after_pr26"] and row["count"] == 1


# ---- the 名词解释 route: short definitions, and no model call for a term the paragraphs do not contain ----------

from test_gap_generation import PARAGRAPH  # noqa: E402


def add_units(db, texts):
    row = db.execute("SELECT u.document_id AS d, u.chapter, max(u.ordinal) OVER () AS top FROM source_units u JOIN source_documents doc ON doc.id=u.document_id "
                     "WHERE doc.metadata->>'source_kind'='textbook' AND u.chapter NOT IN ('','unclassified','contents') LIMIT 1").fetchone()
    ids = []
    for i, text in enumerate(texts):
        ids.append(db.execute("INSERT INTO source_units(document_id,ordinal,chapter,section,text) VALUES (%s,%s,%s,'s',%s) RETURNING id",
                              (row["d"], 100000 + i, row["chapter"], text)).fetchone()["id"])
    return ids


def test_a_short_definition_is_evidence_on_the_term_route_only(db, corpus):
    short = "孟德尔试剂甲是指在测试中使用的一类虚构试剂。"          # 22 characters
    (uid,) = add_units(db, [short])
    assert 20 <= len(short) < 80
    assert uid in [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", "孟德尔试剂甲", term=True)]
    assert uid not in [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", "孟德尔试剂甲")]   # other types keep the 80 floor


def test_a_very_short_line_is_kept_with_its_neighbours_when_together_they_are_long_enough(db, corpus):
    before = "上文：这一节讨论一类在实验中使用的虚构试剂的来源、制备方法和保存条件，供对照使用。"
    line = "孟德尔试剂乙：虚构物。"                                  # 12 characters: too short alone
    after = "下文：该试剂只用于说明测试方法，不参与任何真实的代谢反应或者调控过程的讨论。"
    ids = add_units(db, [before, line, after])
    assert len(line) < 20 and len(before + line + after) >= 80
    got = [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", "孟德尔试剂乙", term=True)]
    assert ids[1] in got and ids[0] in got and ids[2] in got                # the group, in reading order
    assert got.index(ids[0]) < got.index(ids[1]) < got.index(ids[2])


def test_a_term_is_found_in_a_paragraph_of_any_length_and_its_neighbours_come_along(db, corpus):
    ids = add_units(db, ["丙", "孟德尔试剂丙：虚构物。", "丁"])
    got = [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", "孟德尔试剂丙", term=True)]
    assert got == ids                      # the 12-character hit and the paragraph before and after it, in reading order


def _term_item(db, stem):
    doc = db.execute("SELECT d.id AS d, min(u.id) AS u FROM source_documents d JOIN source_units u ON u.document_id=d.id "
                     "WHERE d.metadata->>'source_kind'='exam_transcription' GROUP BY d.id LIMIT 1").fetchone()
    db.execute("INSERT INTO past_exam_items(subject,question_type,stem,stem_hash,document_id,unit_id,question_number,year,years) "
               "VALUES ('biochemistry','term',%s,%s,%s,%s,'1',2024,'[2024]')", (stem, pei.stem_hash("term", stem), doc["d"], doc["u"]))
    return db.execute("SELECT * FROM past_exam_items WHERE stem=%s", (stem,)).fetchone()


def test_no_model_call_for_a_term_that_is_not_in_the_chosen_paragraphs(db, corpus, setup_ai):
    # "冈崎片段酶" shares words with the paragraph (so paragraphs are found) but the term itself is not in it
    item = _term_item(db, "冈崎片段酶")
    before = db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"]
    assert pei._enqueue_item(db, setup_ai, item) == "blocked"
    row = db.execute("SELECT status,blocked_reason FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()
    assert (row["status"], row["blocked_reason"]) == ("blocked", "term_not_in_textbook")
    assert db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"] == before          # nothing was queued, nothing paid
    skipped = waste_report.past_exam_route(db, 24)["skipped_before_model"]
    assert skipped["term_not_in_textbook"] == 1 and skipped["by_type_and_reason"] == {"term:term_not_in_textbook": 1}


def test_a_term_that_is_in_the_paragraph_is_still_queued(db, corpus, setup_ai):
    item = _term_item(db, "冈崎片段")
    result = pei._enqueue_item(db, setup_ai, item)
    assert result == "queued", db.execute("SELECT blocked_reason FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()


# ---- the before / after view around a deploy ---------------------------------------------------------------------------

def test_compare_windows_splits_reviews_and_cost_around_a_moment(db, corpus, setup_ai):
    from app import auto_quality as quality

    qid, review = review_round(db, setup_ai)
    assert quality.accept_round(db, review)
    db.execute("INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail) VALUES ('cmp-1',%s,1,'usage_estimate',0.5,'{}')", (review["id"],))
    now = db.execute("SELECT clock_timestamp() AS t").fetchone()["t"].isoformat()
    result = waste_report.compare_windows(db, now, 6)                              # everything happened BEFORE `now`
    assert result["before"]["single_choice"]["reviewed"] == 1 and result["before"]["single_choice"]["cost_cny"] == "0.50"
    assert result["before"]["all"]["reviewed"] == 1 and result["after"]["all"]["reviewed"] == 0
    earlier = db.execute("SELECT (clock_timestamp()-interval '7 hours') AS t").fetchone()["t"].isoformat()
    assert "single_choice" not in waste_report.compare_windows(db, earlier, 6)["after"]   # 7 h ago + 6 h: it is outside
    ago_1h = db.execute("SELECT (clock_timestamp()-interval '1 hour') AS t").fetchone()["t"].isoformat()
    assert waste_report.compare_windows(db, ago_1h, 6)["after"]["single_choice"]["reviewed"] == 1
