"""The practice centre's sources: used questions first, surplus new ones next, the daily paper's two-day reserve never.
Synthetic rows only."""
import pytest

from app import auto_generation, auto_quality, chapters, cost_control, paper_review, practice, practice_center
from app.common import ensure_user
from stats_helpers import item, paper, question
from test_learning_stats import at, today


@pytest.fixture
def uid(db):
    return ensure_user(db, "sources-student")


@pytest.fixture(autouse=True)
def stock_rules(monkeypatch):
    monkeypatch.setattr(chapters, "textbook_chapters", lambda conn, subject: [("第六章 核酸", 6, "核酸"), ("第二章 细胞膜", 2, "细胞膜")])
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: True)
    monkeypatch.setattr(auto_generation, "pinned_science_evidence", lambda content: True)


def make(db, subject, kind, n, chapter="6 核酸", prefix="题"):
    """n ready, published, new questions of one subject x type (max points as the paper specification has them)."""
    points = auto_generation.POINTS[kind]
    ids = []
    for k in range(n):
        extra = dict(options={"A": "a", "B": "b"}, answer="A") if kind == "single_choice" else dict(options={}, answer="x", accepted_answers=["x"])
        qid = question(db, subject, kind, points, chapter, stem=f"{prefix}-{subject}-{kind}-{k}-{n}", **extra)
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
        ids.append(qid)
    return ids


def daily_stock(db, uid):
    """What the daily paper can use: per cell, the new verified questions (the number progress.py and the worker report)."""
    return {(s, k): auto_generation.eligible_reserve_count(db, uid, s, k) for s in ("biochemistry", "cell_biology") for k in practice_center.TYPE_NAMES}


def test_the_two_day_reserve_is_never_offered_and_only_the_surplus_counts(db, uid):
    make(db, "biochemistry", "single_choice", 13)          # reserve is 20: nothing spare
    make(db, "biochemistry", "term", 11)                    # reserve is 8: three spare
    make(db, "cell_biology", "single_choice", 3)
    o = practice_center.overview(db, uid)
    types = {t["value"]: t for t in o["type"]["types"]}
    assert types["single_choice"]["available"] == 0 and types["single_choice"]["new"] == 0
    assert types["term"]["available"] == 3 and types["term"]["new"] == 3 and types["term"]["graded_by_ai"] is True
    assert o["quick"]["available"] == 0 and o["quick"]["reason"] and not o["quick"]["ready"]       # objective: nothing spare in any cell
    assert o["chapter"]["available"] == 0 and o["type"]["available"] == 3


def test_a_surplus_in_one_cell_does_not_cover_a_shortage_in_another(db, uid):
    make(db, "biochemistry", "single_choice", 35)           # 5 spare
    make(db, "cell_biology", "single_choice", 3)            # short: all reserved
    o = practice_center.overview(db, uid)
    assert o["quick"]["available"] == 5 and o["quick"]["short_by"] == 5 and o["quick"]["ready"]       # may start with 5; says it is 5 short of 10
    s = practice_center.start(db, uid, "quick")
    assert len(s["questions"]) == 5
    assert {q["subject"] for q in s["questions"]} == {"biochemistry"}


def test_a_practice_set_leaves_the_daily_papers_stock_unchanged_when_only_used_and_surplus_questions_are_taken(db, uid):
    required = dict(auto_generation.COUNTS)
    make(db, "biochemistry", "single_choice", 34)           # 4 spare
    make(db, "biochemistry", "fill_blank", 30)              # no spare
    make(db, "cell_biology", "single_choice", 30)           # no spare
    before = daily_stock(db, uid)
    taken = set()
    for mode, kw in (("quick", {}), ("type", dict(question_type="single_choice")), ("chapter", dict(subject="biochemistry", chapter="第六章 核酸"))):
        try:
            s = practice_center.start(db, uid, mode, **kw)
        except ValueError:
            continue                                         # nothing left to practise in this kind: fine
        for q in s["questions"]:
            practice.answer(db, uid, s["set_id"], q["id"], {"text": "A"}, 3)
            taken.add(q["id"])
    assert len(taken) == 4                                   # only the 4 spare ones were ever available; the later sets reuse those 4 (now "used")
    after = daily_stock(db, uid)
    for cell, n in before.items():
        # what a daily paper can draw (up to one paper's worth) did not change, and the two-day reserve is whole
        assert min(after[cell], required[cell[1]]) == min(n, required[cell[1]]), (cell, before, after)
        assert after[cell] >= min(n, 2 * required[cell[1]]), (cell, before, after)


def test_questions_the_learner_already_had_come_first_and_cost_the_stock_nothing(db, uid):
    ids = make(db, "biochemistry", "single_choice", 30)     # exactly the reserve: no surplus
    done = paper(db, uid, today(db), [dict(item("biochemistry", "single_choice", 3, 3, "6 核酸"), question_id=ids[0]),
                                      dict(item("biochemistry", "single_choice", 3, 0, "6 核酸"), question_id=ids[1])], when=at(today(db)))
    assert done["question_ids"] == ids[:2]
    stock_before = daily_stock(db, uid)[("biochemistry", "single_choice")]
    o = practice_center.overview(db, uid)
    cell = {t["value"]: t for t in o["type"]["types"]}["single_choice"]
    assert cell["used"] == 2 and cell["new"] == 0 and cell["available"] == 2         # the two he had are practisable; the 28 left are the reserve
    s = practice_center.start(db, uid, "type", question_type="single_choice")
    assert {q["id"] for q in s["questions"]} == set(ids[:2])
    for q in s["questions"]:
        practice.answer(db, uid, s["set_id"], q["id"], {"text": "A"}, 2)
    assert daily_stock(db, uid)[("biochemistry", "single_choice")] == stock_before


def test_a_paper_not_handed_in_does_not_leak_its_questions_into_practice(db, uid):
    ids = make(db, "biochemistry", "single_choice", 32)     # 1 spare (the prepared one is neither used nor stock)
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (ids[0],)).fetchone()["content"]
    from psycopg.types.json import Jsonb

    pid = db.execute("INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,%s,%s) RETURNING id", (uid, today(db), Jsonb({"specification": "x"}))).fetchone()["id"]
    db.execute("INSERT INTO paper_items_v2(paper_id,question_id,position,snapshot) VALUES (%s,%s,1,%s)", (pid, ids[0], Jsonb(content)))
    cell = {t["value"]: t for t in practice_center.overview(db, uid)["type"]["types"]}["single_choice"]
    assert cell["used"] == 0                                  # prepared, not handed in: not "had" (it would give today's paper away)
    s = practice_center.start(db, uid, "type", question_type="single_choice")
    assert ids[0] not in {q["id"] for q in s["questions"]}


def test_every_type_can_be_chosen_and_a_subjective_one_waits_for_the_ai(db, uid):
    make(db, "biochemistry", "term", 12)                    # reserve 8: 4 spare
    o = practice_center.overview(db, uid)
    assert [t["value"] for t in o["type"]["types"]] == ["single_choice", "fill_blank", "term", "short_answer", "essay"] and all(t["supported"] for t in o["type"]["types"])
    s = practice_center.start(db, uid, "type", question_type="term")
    assert len(s["questions"]) == 4 and {q["question_type"] for q in s["questions"]} == {"term"}
    r = practice.answer(db, uid, s["set_id"], s["questions"][0]["id"], {"text": "这是一个名词解释的作答"}, 20)
    assert r["pending"] is True and r["points"] is None and r["correct"] is None
    row = db.execute("SELECT points FROM practice_answers WHERE set_id=%s", (s["set_id"],)).fetchone()
    assert row["points"] is None                                                  # no score yet: not 0
    review = paper_review.practice_summary(db, uid, s["set_id"])
    assert review["items"][0]["status"] == "pending" and review["pending"] == 1
    assert practice.summary(db, uid, s["set_id"])["pending"] == 1
    with pytest.raises(ValueError, match="answer_required"):
        practice.answer(db, uid, s["set_id"], s["questions"][1]["id"], {"text": "  "})


def test_weak_practice_says_why_and_starts_with_fewer_than_ten(db, uid):
    make(db, "biochemistry", "single_choice", 36)           # 6 spare, all in 第六章 核酸
    paper(db, uid, today(db), [item("biochemistry", "single_choice", 3, 0, "6 核酸") for _ in range(4)], when=at(today(db)))
    o = practice_center.overview(db, uid)
    assert o["weak"]["ready"] and o["weak"]["available"] == 6 and o["weak"]["short_by"] == 4 and o["weak"]["size"] == 6
    s = practice_center.start(db, uid, "weak")
    assert len(s["questions"]) == 6


def test_weak_practice_with_nothing_to_practise_gives_a_specific_reason(db, uid):
    paper(db, uid, today(db), [item("biochemistry", "single_choice", 3, 0, "6 核酸") for _ in range(4)], when=at(today(db)))
    o = practice_center.overview(db, uid)
    assert not o["weak"]["ready"] and "还差 10 道" in o["weak"]["reason"] and "题库准备中" not in o["weak"]["reason"]
    with pytest.raises(ValueError, match="no_practice_questions"):
        practice_center.start(db, uid, "weak")


def test_missed_practice_questions_can_be_redone_and_have_a_review(db, uid):
    make(db, "biochemistry", "single_choice", 36)
    s = practice_center.start(db, uid, "quick", size=6)
    wrong = s["questions"][0]
    practice.answer(db, uid, s["set_id"], wrong["id"], {"text": "B"}, 5)
    redo = practice_center.start(db, uid, "retry")
    assert [q["id"] for q in redo["questions"]] == [wrong["id"]] and redo["mode"] == "retry"
    rv = paper_review.practice_summary(db, uid, s["set_id"])
    assert rv["kind"] == "practice" and rv["items"][0]["status"] in ("wrong", "partial") and rv["read_only"]
    d = paper_review.practice_detail(db, uid, s["set_id"], wrong["id"])
    assert d["points"] == 0 and d["status"] == "wrong"


# ---- a subjective practice answer is graded by the AI and the score is written back -------------------------------------------

from psycopg.types.json import Jsonb  # noqa: E402

from app import auto_worker, grading_activity  # noqa: E402
from test_auto_grading import finish, learner, verdict  # noqa: E402,F401
from test_ai_automation import setup_ai  # noqa: E402,F401


def test_a_subjective_practice_answer_is_graded_like_a_paper_answer_and_a_low_score_joins_the_mistake_book(db, learner, monkeypatch):
    uid, made = learner
    term = next(q for q in made["questions"] if q["question_type"] == "term")
    snapshot = db.execute("SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s", (made["paper_id"], term["id"])).fetchone()["snapshot"]
    set_id = db.execute("INSERT INTO practice_sets(user_id,mode,question_ids,detail) VALUES (%s,'type',%s,'term') RETURNING id", (uid, [term["id"]])).fetchone()["id"]
    monkeypatch.setattr(grading_activity, "auto_queue_allowed", lambda conn, user: (True, None))
    r = practice.answer(db, uid, set_id, term["id"], {"text": "一个不完整的作答"}, 12)
    assert r["pending"] and r["queued"]
    job = db.execute("SELECT * FROM ai_jobs WHERE kind='grading' AND job_key LIKE 'pgrade:%'").fetchone()
    assert job["payload"]["practice_set_id"] == set_id and job["payload"]["question_id"] == term["id"]
    low = [0 for _ in snapshot["rubric"]]
    finish(db, job, verdict(job, points=low))
    auto_worker.dispatch_completed(db)
    graded = db.execute("SELECT * FROM practice_answers WHERE set_id=%s", (set_id,)).fetchone()
    assert graded["points"] == 0 and graded["graded_by"] == "AI自动核验" and graded["feedback"]["criterion_reasons"]
    assert db.execute("SELECT status FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (uid, term["id"])).fetchone()["status"] == "mistake"
    assert paper_review.practice_detail(db, uid, set_id, term["id"])["status"] == "wrong"


def test_the_paper_is_ready_only_when_every_cell_has_a_papers_worth_of_new_questions(db, uid):
    from app import learning_stats

    required = dict(auto_generation.COUNTS)
    for subject in ("biochemistry", "cell_biology"):
        for kind, n in required.items():
            make(db, subject, kind, n if (subject, kind) != ("cell_biology", "single_choice") else 3, chapter="6 核酸" if subject == "biochemistry" else "2 膜", prefix="就绪")
    state = learning_stats.tomorrow_ready(db, uid)
    assert state["complete"] is False and state["missing"] == 12
    assert state["short_cells"] == [dict(subject="cell_biology", question_type="single_choice", have=3, need=15, missing=12)]
    make(db, "cell_biology", "single_choice", 12, chapter="2 膜", prefix="补")
    assert learning_stats.tomorrow_ready(db, uid)["complete"] is True
    # a used question does not count: the paper after it is short again
    ids = [r["id"] for r in db.execute("SELECT id FROM reviewed_questions WHERE content->>'subject'='cell_biology' AND content->>'question_type'='single_choice' ORDER BY id").fetchall()]
    paper(db, uid, today(db), [dict(item("cell_biology", "single_choice", 3, 3, "2 膜"), question_id=ids[0])], when=at(today(db)))
    again = learning_stats.tomorrow_ready(db, uid)
    assert again["complete"] is False and again["short_cells"][0]["missing"] == 1


def test_the_chapter_list_is_grouped_by_subject_in_the_textbooks_order_not_by_how_many(db, uid, monkeypatch):
    monkeypatch.setattr(chapters, "textbook_chapters", lambda conn, subject: [("第二章 蛋白质", 2, "蛋白质"), ("第十二章 糖代谢", 12, "糖代谢"), ("第二章 细胞膜", 2, "细胞膜"), ("第十一章 细胞信号转导", 11, "细胞信号转导")])
    make(db, "biochemistry", "single_choice", 25, chapter="12 糖代谢", prefix="糖")         # many questions, but chapter 12
    make(db, "biochemistry", "single_choice", 21, chapter="2 蛋白质", prefix="蛋")
    make(db, "cell_biology", "single_choice", 21, chapter="11 细胞信号转导", prefix="信")
    make(db, "cell_biology", "single_choice", 21, chapter="2 细胞膜", prefix="膜")
    listed = [(c["subject"], c["chapter"]) for c in practice_center.overview(db, uid)["chapter"]["chapters"]]
    assert listed == [("biochemistry", "第二章 蛋白质"), ("biochemistry", "第十二章 糖代谢"), ("cell_biology", "第二章 细胞膜"), ("cell_biology", "第十一章 细胞信号转导")]
    assert not any(c[1][:1].isdigit() for c in listed)
