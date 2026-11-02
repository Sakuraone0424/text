"""Part 4 backend: overview, profile, mistakes, timing, practice. Synthetic rows only."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from app import learning_stats as stats, practice
from app.common import ensure_user
from stats_helpers import SPEC, item, paper, publish, question

SECRET = "不得出现在统计里的题干"


@pytest.fixture
def uid(db):
    return ensure_user(db, "stats-student")


def today(db):
    return stats.today_cn(db)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone(timedelta(hours=8)))


def test_a_new_learner_gets_honest_empty_numbers(db, uid):
    o = stats.overview(db, uid)
    assert o["has_data"] is False and o["streak"] == {"current": 0, "longest": 0}
    assert o["today_paper"] == {"state": "none"} and o["weak_chapters"] == []
    assert o["last7"]["avg_points"] is None and o["objective_rate"] is None and o["subjective_rate"] is None
    assert o["days_left"] == max(0, (stats.EXAM_DATE - today(db)).days)
    p = stats.profile(db, uid)
    assert p["trend"] == [] and p["chapters"] == [] and p["summary"]["papers"] == 0
    assert len(p["calendar"]) == 56 and all(c["answered"] == 0 for c in p["calendar"])
    assert p["timing"]["by_type"] == [] and p["timing"]["recorded_questions"] == 0


def test_countdown_streak_and_averages_are_computed_from_real_papers(db, uid):
    t = today(db)
    for back in (3, 2, 1, 0):  # four consecutive days ending today
        paper(db, uid, t - timedelta(days=back), [
            item("biochemistry", "single_choice", 4, 4 if back else 0), item("cell_biology", "term", 6, 3),
        ], when=at(t - timedelta(days=back)))
    o = stats.overview(db, uid)
    assert o["streak"]["current"] == 4 and o["streak"]["longest"] == 4 and o["has_data"]
    assert o["last7"]["papers"] == 4
    assert o["objective_rate"] == round(12 / 16, 4)  # three papers 4/4, today 0/4
    assert o["subjective_rate"] == 0.5
    assert o["today_paper"]["state"] == "submitted"
    assert o["today_paper"]["points"] == 3 and o["today_paper"]["max"] == 10


def test_the_streak_survives_an_unplayed_today_and_breaks_on_a_gap(db, uid):
    t = today(db)
    paper(db, uid, t - timedelta(days=1), [item()], when=at(t - timedelta(days=1)))
    paper(db, uid, t - timedelta(days=2), [item()], when=at(t - timedelta(days=2)))
    paper(db, uid, t - timedelta(days=5), [item()], when=at(t - timedelta(days=5)))
    s = stats.overview(db, uid)["streak"]
    assert s == {"current": 2, "longest": 2}
    assert stats.streaks([date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 4)], date(2026, 1, 10)) == (0, 2)


def test_a_paper_with_a_pending_answer_is_not_scored_yet(db, uid):
    t = today(db)
    paper(db, uid, t, [item(got=4), item("biochemistry", "short_answer", 12, None)], when=at(t))
    o = stats.overview(db, uid)
    assert o["last7"]["papers"] == 0 and o["has_data"] is False
    assert stats.profile(db, uid)["trend"] == []


def test_scores_of_different_totals_are_compared_as_rates_never_mixed(db, uid):
    t = today(db)
    paper(db, uid, t - timedelta(days=9), [item(points=2, got=1)] * 4, spec="xiaohan-60q-150pt-v2", when=at(t - timedelta(days=9)))
    paper(db, uid, t - timedelta(days=1), [item(points=4, got=4)] * 3, when=at(t - timedelta(days=1)))
    trend = stats.profile(db, uid)["trend"]
    assert [(x["points"], x["max"], x["rate"]) for x in trend] == [(4, 8, 0.5), (12, 12, 1.0)]


def test_weak_chapters_need_a_minimum_of_answers_and_sort_lowest_first(db, uid):
    t = today(db)
    paper(db, uid, t, [item(chapter="A章", got=0)] * 3 + [item(chapter="B章", got=4)] * 3 + [item(chapter="C章", got=0)] * 2
          + [item(chapter="D章", got=2)] * 3, when=at(t))
    weak = stats.overview(db, uid)["weak_chapters"]
    assert [w["chapter"] for w in weak] == ["A章", "D章", "B章"]  # C章 has too few answers
    assert weak[0]["rate"] == 0.0 and weak[0]["answers"] == 3
    chapters = stats.profile(db, uid)["chapters"]
    assert [c["chapter"] for c in chapters][:2] == ["A章", "C章"] or chapters[0]["rate"] == 0.0  # all chapters listed
    assert {c["chapter"] for c in chapters} == {"A章", "B章", "C章", "D章"}


def test_reported_questions_are_excluded_everywhere(db, uid):
    t = today(db)
    made = paper(db, uid, t, [item(chapter="X章", got=0), item(chapter="X章", got=4)], when=at(t))
    db.execute("INSERT INTO question_reports(user_id,paper_id,question_id,reason) VALUES (%s,%s,%s,'x')",
               (uid, made["paper_id"], made["question_ids"][0])) if False else None
    cols = [r["column_name"] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='question_reports'").fetchall()]
    values = {"user_id": uid, "paper_id": made["paper_id"], "question_id": made["question_ids"][0]}
    for c in cols:
        values.setdefault(c, "x" if c in ("reason", "note", "kind", "category") else None)
    use = [c for c in cols if values[c] is not None]
    db.execute(f"INSERT INTO question_reports({','.join(use)}) VALUES ({','.join(['%s'] * len(use))})", [values[c] for c in use])
    assert len(stats.mistakes(db, uid)["items"]) == 0
    assert stats.overview(db, uid)["objective_rate"] == 1.0


def test_the_mistake_list_filters_by_subject_type_and_chapter_and_counts_each_facet(db, uid):
    t = today(db)
    paper(db, uid, t, [
        item("biochemistry", "single_choice", 4, 0, "糖代谢"), item("biochemistry", "fill_blank", 4, 0, "糖代谢"),
        item("biochemistry", "term", 6, 3, "脂代谢"), item("cell_biology", "single_choice", 4, 0, "细胞骨架"),
        item("cell_biology", "single_choice", 4, 4, "细胞骨架"),
    ], when=at(t))
    everything = stats.mistakes(db, uid)
    assert everything["total"] == 4  # the correct one is not a mistake; the partly right term is
    assert {f["value"]: f["count"] for f in everything["facets"]["subject"]} == {"biochemistry": 3, "cell_biology": 1}
    assert {f["value"]: f["count"] for f in everything["facets"]["question_type"]}["single_choice"] == 2
    assert {(f["subject"], f["value"]): f["count"] for f in everything["facets"]["chapter"]}[("biochemistry", "糖代谢")] == 2
    assert stats.mistakes(db, uid, subject="biochemistry")["total"] == 3
    assert stats.mistakes(db, uid, question_type="single_choice")["total"] == 2
    assert stats.mistakes(db, uid, subject="biochemistry", chapter="糖代谢")["total"] == 2
    one = stats.mistakes(db, uid, subject="cell_biology", question_type="single_choice", chapter="细胞骨架")["items"][0]
    assert (one["points"], one["max_points"]) == (0, 4) and one["stem"].startswith("合成题干")
    assert len(stats.mistakes(db, uid, limit=2)["items"]) == 2 and stats.mistakes(db, uid, limit=2, offset=3)["items"].__len__() == 1


def test_time_per_question_is_summed_by_type_and_flags_overtime(db, uid):
    t = today(db)
    made = paper(db, uid, t, [
        item("biochemistry", "single_choice", 4, 4, seconds=30), item("biochemistry", "single_choice", 4, 0, seconds=50),
        item("biochemistry", "essay", 18, 9, seconds=3000), item("cell_biology", "term", 6, 3, seconds=60),
    ], when=at(t))
    timing = stats.profile(db, uid)["timing"]
    assert {x["question_type"]: x["avg_seconds"] for x in timing["by_type"]} == {"single_choice": 40, "essay": 3000, "term": 60}
    assert timing["recorded_questions"] == 4 and len(timing["papers"]) == 1 and timing["papers"][0]["seconds"] == 50 * 60
    out = stats.overtime_types(db, uid, made["paper_id"])
    assert out["recorded"] is True
    flags = {x["question_type"]: x["over"] for x in out["types"]}
    assert flags["essay"] is True and flags["single_choice"] is False  # the essay took 50 of the hour's 60 minutes
    assert stats.overtime_types(db, uid, 999999) == {"recorded": False, "types": []}
    none = paper(db, uid, t - timedelta(days=1), [item()], when=at(t - timedelta(days=1)))
    assert stats.overtime_types(db, uid, none["paper_id"])["recorded"] is False


def test_calendar_has_eight_weeks_and_marks_days_with_work(db, uid):
    t = today(db)
    paper(db, uid, t - timedelta(days=3), [item(got=4), item(got=0)], when=at(t - timedelta(days=3)))
    paper(db, uid, t - timedelta(days=80), [item()], when=at(t - timedelta(days=80)))
    cal = {c["day"]: c for c in stats.profile(db, uid)["calendar"]}
    assert len(cal) == 56 and min(cal) == (t - timedelta(days=55)).isoformat() and max(cal) == t.isoformat()
    assert cal[(t - timedelta(days=3)).isoformat()]["answered"] == 2 and cal[(t - timedelta(days=3)).isoformat()]["rate"] == 0.5
    assert sum(c["answered"] for c in cal.values()) == 2  # the 80-days-old paper is outside the window


def test_review_numbers_come_from_the_real_cards(db, uid):
    t = today(db)
    qid = question(db, "biochemistry", "single_choice", 4, "章")
    for offset, qid in enumerate([qid, question(db, "biochemistry", "single_choice", 4, "章"), question(db, "biochemistry", "single_choice", 4, "章")]):
        due = datetime.now(timezone.utc) + timedelta(days=offset * 2 - 1)
        db.execute("INSERT INTO fsrs_cards(user_id,question_id,card,due) VALUES (%s,%s,'{}',%s)", (uid, qid, due))
    r = stats.profile(db, uid)["reviews"]
    assert r["due_now"] == 1 and len(r["next_7_days"]) == 7
    assert r["next_7_days"][0]["count"] == 1 and sum(d["count"] for d in r["next_7_days"]) == 3
    assert r["mastered"] == 0


def test_nothing_in_the_statistics_carries_a_stem_or_a_secret(db, uid):
    t = today(db)
    paper(db, uid, t, [dict(item(got=0), question_id=question(db, "biochemistry", "single_choice", 4, "章", stem=SECRET))], when=at(t))
    body = json.dumps([stats.overview(db, uid), stats.profile(db, uid)], default=str, ensure_ascii=False)
    assert SECRET not in body  # only the mistake list (an explicit, owner-only view) shows stems


# ---- practice ----------------------------------------------------------------------------------


@pytest.fixture
def bank(db, monkeypatch):
    from app import auto_quality

    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: True)
    ids = {}
    for n in range(6):
        kind = "single_choice" if n % 2 == 0 else "fill_blank"
        extra = (dict(options={"A": "a", "B": "b", "C": "c", "D": "d"}, answer="A", stem="合成练习题") if kind == "single_choice"
                 else dict(options={}, answer="糖酵解", accepted_answers=["糖酵解"], stem="合成练习题 ____"))
        qid = question(db, "biochemistry", kind, 4, "第三章 糖代谢", **extra)
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
        ids[qid] = kind
    other = question(db, "cell_biology", "single_choice", 4, "别的章")
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (other,))
    return ids


def test_chapter_practice_picks_only_ready_objective_questions_of_that_chapter(db, uid, bank):
    s = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 4)
    assert s["counts_toward_daily_paper"] is False and len(s["questions"]) == 4
    assert {q["id"] for q in s["questions"]} <= set(bank)
    assert all("answer" not in q and "accepted_answers" not in q and "rubric" not in q for q in s["questions"])
    assert next(q for q in s["questions"] if q["question_type"] == "fill_blank")["blank_count"] == 1
    with pytest.raises(ValueError, match="no_practice_questions"):
        practice.start(db, uid, "chapter", "biochemistry", "不存在的章", 4)
    with pytest.raises(ValueError, match="subject_and_chapter_required"):
        practice.start(db, uid, "chapter", None, None, 4)


def test_answers_are_checked_at_once_recorded_once_and_summarised(db, uid, bank):
    s = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 6)
    first = next(q for q in s["questions"] if q["question_type"] == "single_choice")
    right = practice.answer(db, uid, s["set_id"], first["id"], {"text": "a"}, 12)
    assert right["correct"] is True and right["points"] == 4 and right["answer"] == "A"
    fill = next(q for q in s["questions"] if q["question_type"] == "fill_blank")
    wrong = practice.answer(db, uid, s["set_id"], fill["id"], {"text": "别的"})
    assert wrong["correct"] is False and wrong["accepted_answers"] == ["糖酵解"]
    with pytest.raises(ValueError, match="already_answered"):
        practice.answer(db, uid, s["set_id"], fill["id"], {"text": "糖酵解"})
    with pytest.raises(ValueError, match="not_in_this_set"):
        practice.answer(db, uid, s["set_id"], 999999, {"text": "A"})
    sm = practice.summary(db, uid, s["set_id"])
    assert (sm["answered"], sm["correct"], sm["points"], sm["max_points"], sm["total"]) == (2, 1, 4, 8, 6) and len(sm["remaining"]) == 4


def test_previously_missed_questions_come_first_and_other_users_cannot_touch_a_set(db, uid, bank):
    s1 = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 6)
    missed = s1["questions"][-1]
    practice.answer(db, uid, s1["set_id"], missed["id"], {"text": "错误答案"})
    s2 = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 3)
    assert s2["questions"][0]["id"] == missed["id"]
    stranger = ensure_user(db, "someone-else")
    with pytest.raises(LookupError):
        practice.summary(db, stranger, s1["set_id"])
    with pytest.raises(LookupError):
        practice.answer(db, stranger, s1["set_id"], missed["id"], {"text": "A"})


def test_practice_feeds_chapter_accuracy_but_never_the_papers_or_trends(db, uid, bank):
    s = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 3)
    for q in s["questions"]:
        practice.answer(db, uid, s["set_id"], q["id"], {"text": "错误答案"})
    p = stats.profile(db, uid)
    assert p["trend"] == [] and p["summary"]["papers"] == 0 and p["summary"]["answered"] == 3
    assert p["chapters"][0]["chapter"] == "第三章 糖代谢" and p["chapters"][0]["rate"] == 0.0 and p["chapters"][0]["answers"] == 3
    assert stats.overview(db, uid)["streak"]["current"] == 1  # practising counts as studying today
    assert stats.overview(db, uid)["last7"]["papers"] == 0
    weak = practice.start(db, uid, "weak", size=5)
    assert weak["mode"] == "weak" and weak["questions"]


def test_a_question_of_an_unfinished_paper_is_never_offered_in_practice(db, uid, bank):
    qid = next(iter(bank))
    made = paper(db, uid, today(db), [dict(item("biochemistry", bank[qid], 4, 0, "第三章 糖代谢"), question_id=qid)])
    db.execute("UPDATE training_sessions SET submitted_at=NULL WHERE paper_id=%s", (made["paper_id"],))
    s = practice.start(db, uid, "chapter", "biochemistry", "第三章 糖代谢", 6)
    assert qid not in {q["id"] for q in s["questions"]} and len(s["questions"]) == 5


def test_weak_practice_without_history_says_why(db, uid, bank):
    with pytest.raises(ValueError, match="no_weak_chapters_yet"):
        practice.start(db, uid, "weak")


def test_the_time_spent_in_a_draft_grows_never_shrinks_and_is_stored_at_submit(db, uid):
    from app import training_sessions as ts

    made = paper(db, uid, today(db), [item("biochemistry", "single_choice", 4, 0)])  # only to get a question + paper rows
    qid = made["question_ids"][0]
    with pytest.raises(ValueError, match="Invalid time spent"):
        ts.valid_value(db, made["paper_id"], qid, {"text": "A", "seconds": -1})
    with pytest.raises(ValueError, match="Invalid time spent"):
        ts.valid_value(db, made["paper_id"], qid, {"text": "A", "seconds": "9"})
    assert ts.valid_value(db, made["paper_id"], qid, {"text": "A", "seconds": 42})["seconds"] == 42
    assert "seconds" not in ts.valid_value(db, made["paper_id"], qid, {"text": "A"})  # nothing recorded, nothing stored
    db.execute("INSERT INTO answer_drafts VALUES (%s,%s,%s)", (made["paper_id"], qid, Jsonb({"text": "A", "seconds": 90})))
    assert ts.valid_value(db, made["paper_id"], qid, {"text": "A", "seconds": 20})["seconds"] == 90  # a stale tab cannot erase it
