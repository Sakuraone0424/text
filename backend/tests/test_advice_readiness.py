"""Learning advice: when it is written, and what the empty state says it is waiting for."""
from datetime import timedelta

from app import auto_insights as insights
from stats_helpers import item, paper
from test_learning_stats import at, today, uid  # noqa: F401


def test_no_paper_yet_says_what_is_needed(db, uid):
    state = insights.readiness(db, uid)
    assert state["state"] == "no_paper" and "还差 1 套" in state["text"]


def test_two_papers_still_being_graded_say_so(db, uid):
    t = today(db)
    for back in (2, 1):
        paper(db, uid, t - timedelta(days=back), [item(got=4), item("biochemistry", "short_answer", 12, None)], when=at(t - timedelta(days=back)))
    state = insights.readiness(db, uid)
    assert state["state"] == "grading" and state["submitted"] == 2 and state["graded"] == 0 and "判分完成后自动生成" in state["text"]


def test_a_graded_paper_with_lost_points_is_queued_without_anyone_opening_its_result_page(db, uid):
    t = today(db)
    pending = paper(db, uid, t - timedelta(days=2), [item(got=4), item("biochemistry", "short_answer", 12, None)], when=at(t - timedelta(days=2)))
    done = paper(db, uid, t - timedelta(days=1), [item(got=4), item(got=0)], when=at(t - timedelta(days=1)))
    ids = [r["id"] for r in db.execute(insights.GRADED_ATTEMPTS_SQL, (uid,)).fetchall()]
    assert ids == [done["attempt_id"]] and pending["attempt_id"] not in ids          # finalized_at is not needed; a waiting paper is not used
    state = insights.readiness(db, uid)
    assert state["state"] == "queued" and state["graded"] == 1 and state["with_lost"] == 1 and "判分已完成" in state["text"]


def test_a_perfect_paper_needs_no_advice_and_does_not_hold_up_the_next_one(db, uid):
    t = today(db)
    perfect = paper(db, uid, t - timedelta(days=2), [item(got=4), item(got=4)], when=at(t - timedelta(days=2)))
    lost = paper(db, uid, t - timedelta(days=1), [item(got=0)], when=at(t - timedelta(days=1)))
    ids = [r["id"] for r in db.execute(insights.GRADED_ATTEMPTS_SQL, (uid,)).fetchall()]
    assert ids == [perfect["attempt_id"], lost["attempt_id"]]
    assert insights.readiness(db, uid)["with_lost"] == 1
