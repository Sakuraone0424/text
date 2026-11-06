"""The mistake book, the whole-paper review and the practice centre (backend). Synthetic rows only."""
import json
from datetime import date, timedelta

import pytest
from psycopg.types.json import Jsonb

from app import chapters, mistake_book, paper_review, practice, practice_center, learning_stats as stats
from app.common import ensure_user
from stats_helpers import SPEC, item, paper, question


@pytest.fixture
def uid(db):
    return ensure_user(db, "review-student")


@pytest.fixture(autouse=True)
def textbook(monkeypatch):
    book = [("第二章 蛋白质的结构与功能", 2, "蛋白质的结构与功能"), ("第六章 核酸", 6, "核酸")]
    monkeypatch.setattr(chapters, "textbook_chapters", lambda conn, subject: book)


def card(db, uid, qid, status="mistake", due_days=0, good=0):
    db.execute("INSERT INTO fsrs_cards(user_id,question_id,card,due,status) VALUES (%s,%s,'{}',clock_timestamp()+%s*interval '1 day',%s)", (uid, qid, due_days, status))
    db.execute("INSERT INTO fsrs_events(user_id,question_id,event_key,rating,before_card,after_card,reviewed_at) VALUES (%s,%s,%s,1,'{}','{}',clock_timestamp())", (uid, qid, f"graded-failure:1:{qid}"))
    for k in range(good):
        db.execute("INSERT INTO fsrs_events(user_id,question_id,event_key,rating,before_card,after_card,reviewed_at) VALUES (%s,%s,%s,3,'{}','{}',clock_timestamp())", (uid, qid, f"good-{qid}-{k}"))


def graded(db, uid, day, items):
    return paper(db, uid, day, items)


def test_the_book_groups_by_due_date_consolidate_and_mastered_and_names_chapters_like_the_textbook(db, uid):
    day = stats.today_cn(db) - timedelta(days=3)
    qa = question(db, "biochemistry", "single_choice", 4, "6 核酸", stem="下列说法正确的是 ______", answer="A", options={"A": "x", "B": "y"})
    p = graded(db, uid, day, [
        dict(item("biochemistry", "single_choice", 4, 0, "6 核酸"), answer='{"text":"B"}', question_id=qa),
        item("biochemistry", "term", 6, 2, "2 蛋白质 结构", answer='{"text":"我的答案"}'),
        item("cell_biology", "short_answer", 12, 9, "别的章"),
        item("biochemistry", "fill_blank", 4, 0, "6 核酸"),
        item("biochemistry", "essay", 18, 2, "99 不存在"),
    ])
    a, b, c, d, e = p["question_ids"]
    card(db, uid, a, due_days=0)
    card(db, uid, b, due_days=1)
    card(db, uid, c, status="consolidate", due_days=2)
    card(db, uid, d, due_days=5)
    card(db, uid, e, due_days=0, good=3)                                              # three successful reviews: mastered
    out = mistake_book.book(db, uid)
    by = {i["question_id"]: i for i in out["items"]}
    assert [g["key"] for g in out["groups"]] == ["today", "tomorrow", "later", "consolidate", "mastered"]
    assert {k: v["group"] for k, v in by.items()} == {a: "today", b: "tomorrow", c: "consolidate", d: "later", e: "mastered"}
    assert {g["key"]: g["count"] for g in out["groups"]} == {"today": 1, "tomorrow": 1, "later": 1, "consolidate": 1, "mastered": 1}
    assert by[a]["chapter"] == "第六章 核酸" and by[b]["chapter"] == "第二章 蛋白质的结构与功能" and by[c]["chapter"] == "别的章"
    assert by[e]["chapter"] == "其他" and out["unmapped"] == {"99 不存在": 1}
    assert "______" not in by[a]["stem"] and by[a]["your_answer"] == "B" and by[a]["correct"] == "A"       # single choice: letters, no placeholder
    assert by[b]["subjective"] and by[b]["points"] == 2 and by[b]["max_points"] == 6 and by[b]["your_answer"] == "我的答案"
    assert by[e]["dots"] == 3 and by[a]["dots"] == 0
    assert out["open"] == 4 and {s["value"]: s["count"] for s in out["subjects"]} == {"biochemistry": 3, "cell_biology": 1}


def test_a_reported_question_is_not_in_the_book(db, uid):
    p = graded(db, uid, stats.today_cn(db) - timedelta(days=1), [item("biochemistry", "single_choice", 4, 0, "6 核酸")])
    card(db, uid, p["question_ids"][0])
    db.execute("INSERT INTO question_reports(user_id,paper_id,question_id,note) VALUES (%s,%s,%s,'x')", (uid, p["paper_id"], p["question_ids"][0]))
    assert mistake_book.book(db, uid)["total"] == 0


def test_the_detail_has_the_full_question_the_history_and_no_internal_unit_numbers(db, uid):
    day = stats.today_cn(db) - timedelta(days=2)
    qid = question(db, "biochemistry", "term", 6, "6 核酸", evidence=[dict(chapter="6 核酸", textbook="生物化学", excerpt="UNIT:12 核酸由核苷酸组成")])
    p = graded(db, uid, day, [dict(item("biochemistry", "term", 6, 2, "6 核酸", answer='{"text":"部分作答"}'), question_id=qid,
                                  feedback={"criteria": [{"description": "要点一", "points": 2, "max_points": 3}], "criterion_reasons": ["写到一半"]})])
    card(db, uid, qid)
    d = mistake_book.detail(db, uid, qid)
    assert d["stem"] and d["your_answer"] == "部分作答" and d["points"] == 2 and d["chapter"] == "第六章 核酸"
    assert d["scores"]["criteria"][0]["points"] == 2 and "UNIT" not in json.dumps(d["evidence"], ensure_ascii=False) and "核酸由核苷酸组成" in d["evidence"][0]["excerpt"]
    assert d["history"] == [dict(date=day.isoformat(), points=2, max_points=6, kind="paper")]
    with pytest.raises(LookupError):
        mistake_book.detail(db, uid, qid + 999)


# ---- the whole-paper review ---------------------------------------------------------------------------------------------

def sample_paper(db, uid, spec=SPEC):
    day = stats.today_cn(db) - timedelta(days=1)
    qb = question(db, "biochemistry", "single_choice", 4, "6 核酸", options={"A": "甲", "B": "乙"}, answer="A")
    return paper(db, uid, day, spec=spec, items=[
        dict(item("biochemistry", "single_choice", 4, 4, "6 核酸"), answer='{"text":"A"}'),
        dict(item("biochemistry", "single_choice", 4, 0, "6 核酸"), answer='{"text":"B"}', question_id=qb),
        dict(item("biochemistry", "fill_blank", 4, 0, "2 蛋白质"), answer='{"text":""}'),
        item("cell_biology", "term", 6, 3, "别的章", seconds=70),
        item("cell_biology", "short_answer", 12, 11, "别的章"),
        item("cell_biology", "essay", 18, None, "别的章"),
    ])


def test_the_summary_has_the_header_one_line_per_question_and_the_colours(db, uid):
    p = sample_paper(db, uid)
    db.execute("INSERT INTO question_reports(user_id,paper_id,question_id,note) VALUES (%s,%s,%s,'x')", (uid, p["paper_id"], p["question_ids"][4]))
    s = paper_review.summary(db, uid, p["paper_id"])
    assert [i["position"] for i in s["items"]] == [1, 2, 3, 4, 5, 6]
    assert [i["status"] for i in s["items"]] == ["right", "wrong", "blank", "partial", "reported", "pending"]
    assert s["pending"] == 1 and s["score"] is None and s["graded_points"] == 7 and s["max_points"] == 4 * 3 + 6 + 18     # the reported one is not counted
    assert s["subject_scores"]["biochemistry"] == dict(points=4, max_points=12, pending=0)
    assert any(t["question_type"] == "term" and t["rate"] == 0.5 for t in s["type_rates"])
    assert s["items"][0]["chapter"] == "第六章 核酸" and s["items"][1]["flag"] == "mistake" and s["items"][3]["flag"] == "mistake"   # 50% of a term: a mistake
    assert s["flags"]["mistake"] >= 2 and s["counts"]["reported"] == 1 and not s["read_only"]
    assert len(json.dumps(s)) < 6000                                                                 # light: no stems in full, no answers, no rubrics


def test_one_question_in_full_is_loaded_when_it_is_opened(db, uid):
    p = sample_paper(db, uid)
    qid = p["question_ids"][1]
    d = paper_review.detail(db, uid, p["paper_id"], qid)
    assert d["your_choice"] == "B" and d["correct_choice"] == "A" and d["reference"] == "A. 甲" and d["status"] == "wrong" and d["position"] == 2
    term = paper_review.detail(db, uid, p["paper_id"], p["question_ids"][3])
    assert term["points"] == 3 and term["rubric"] and term["status"] == "partial"
    with pytest.raises(LookupError):
        paper_review.detail(db, uid, p["paper_id"], 123456789)


def test_the_old_28_question_paper_opens_read_only_and_other_learners_cannot_open_it(db, uid):
    p = sample_paper(db, uid, spec="xiaohan-150-60-v1")
    s = paper_review.summary(db, uid, p["paper_id"])
    assert s["read_only"] and "旧卷" in s["specification_name"]
    with pytest.raises(LookupError):
        paper_review.summary(db, ensure_user(db, "stranger"), p["paper_id"])
    unsubmitted = db.execute("INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,%s,%s) RETURNING id", (uid, date(2026, 1, 1), Jsonb({"specification": SPEC}))).fetchone()["id"]
    with pytest.raises(LookupError):
        paper_review.summary(db, uid, unsubmitted)


def test_recent_trainings_mix_papers_and_practice_newest_first(db, uid):
    sample_paper(db, uid)
    set_id = db.execute("INSERT INTO practice_sets(user_id,mode,question_ids) VALUES (%s,'quick',%s) RETURNING id", (uid, [])).fetchone()["id"]
    q = question(db, "biochemistry", "single_choice", 4, "6 核酸")
    db.execute("INSERT INTO practice_answers(set_id,question_id,answer,points,max_points,seconds) VALUES (%s,%s,'{}',4,4,12)", (set_id, q))
    out = paper_review.recent(db, uid, 5)
    assert [r["kind"] for r in out] == ["practice", "paper"] and out[0]["label"] == "随机快练" and out[0]["points"] == 4 and out[1]["label"] == "每日训练卷"
    assert out[1]["pending"] == 1 and out[1]["points"] is None


def test_a_detail_says_who_graded_it(db, uid):
    made = graded(db, uid, date.today(), [item("biochemistry", "single_choice", 4, 0)])
    d = paper_review.detail(db, uid, made["paper_id"], made["question_ids"][0])
    assert "graded_by" in d and d["points"] == 0
