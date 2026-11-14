"""Subjective answers are sorted by score rate into mistake / to consolidate / mastered (lines adjustable); single choice and
fill-in stay right or wrong; the cards that already exist are sorted once by the same rule and nothing is deleted."""
import pytest

from app import memory, model_router
from app.common import ensure_user
from stats_helpers import item, paper
from datetime import date


@pytest.mark.parametrize("qtype,points,maximum,expected", [
    ("single_choice", 4, 4, "mastered"), ("single_choice", 0, 4, "mistake"),
    ("fill_blank", 4, 4, "mastered"), ("fill_blank", 2, 4, "consolidate"), ("fill_blank", 1, 4, "mistake"),   # a fill-in goes by its blanks: half right is still to be consolidated, fewer is a mistake
    ("term", 3, 6, "mistake"),                                                          # 50% < 60%
    ("term", 3.5, 6, "mistake"),                                                        # 58%
    ("term", 4, 6, "consolidate"),                                                      # 67%: 60-85% to consolidate
    ("short_answer", 10, 12, "consolidate"),                                            # 83%
    ("short_answer", 11, 12, "mastered"),                                               # 92%
    ("essay", 15, 18, "consolidate"),                                                   # 83.3%
    ("essay", 16, 18, "mastered"),                                                      # 88.9%
    ("essay", 0, 18, "mistake"),
])
def test_the_rule(qtype, points, maximum, expected):
    assert memory.classify(qtype, points, maximum) == expected


def test_the_lines_are_adjustable():
    assert memory.classify("term", 4, 6, 0.7, 0.9) == "mistake"         # 67% is now below the first line
    assert memory.classify("term", 5.5, 6, 0.7, 0.9) == "mastered"       # 92%
    assert memory.classify("term", 4, 6, 0.5, 0.6) == "mastered"
    assert memory.classify("term", None, 6) is None and memory.classify("term", 3, 0) is None


def test_the_settings_keep_the_lines_in_order_and_inside_0_1(db):
    uid = ensure_user(db, "lines-learner")
    s = model_router.settings(db, uid)
    assert float(s["mistake_below"]) == 0.6 and float(s["consolidate_below"]) == 0.85
    model_router.configure(db, uid, s["model_id"], s["effort"], verify=False, mistake_below="0.5", consolidate_below="0.8")
    assert memory.lines(db, uid) == (0.5, 0.8)
    for bad in ({"mistake_below": "0.9"}, {"consolidate_below": "0.4"}, {"mistake_below": "0"}, {"consolidate_below": "1.1"}):
        with pytest.raises(ValueError):
            model_router.configure(db, uid, s["model_id"], s["effort"], verify=False, **bad)


def graded(db, uid, qtype, points, maximum, day=date(2026, 10, 1)):
    p = paper(db, uid, day, [item("biochemistry", qtype, maximum, points, "第一章 测试")])
    return p["attempt_id"], p["question_ids"][0]


@pytest.mark.parametrize("qtype,points,maximum,status,rating", [
    ("term", 2, 6, "mistake", 1), ("term", 4, 6, "consolidate", 2), ("term", 6, 6, None, None),
    ("single_choice", 0, 4, "mistake", 1), ("single_choice", 4, 4, None, None),
])
def test_a_graded_answer_makes_the_right_card_or_none(db, qtype, points, maximum, status, rating):
    uid = ensure_user(db, "card-learner")
    attempt, qid = graded(db, uid, qtype, points, maximum)
    memory.record_failure(db, attempt, qid)
    card = db.execute("SELECT status FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (uid, qid)).fetchone()
    assert (card["status"] if card else None) == status
    if rating:
        assert db.execute("SELECT rating FROM fsrs_events WHERE user_id=%s AND question_id=%s", (uid, qid)).fetchone()["rating"] == rating


def test_the_cards_that_exist_are_sorted_once_by_the_new_rule_and_nothing_is_deleted(db):
    from app import legacy_reuse

    uid = ensure_user(db, "old-cards-learner")
    cases = [("term", 2, 6), ("term", 4, 6), ("term", 6, 6), ("single_choice", 0, 4), ("essay", 16, 18), ("short_answer", 6, 12)]
    qids = []
    for k, (qtype, points, maximum) in enumerate(cases):
        attempt, qid = graded(db, uid, qtype, points, maximum, day=date(2026, 9, 1 + k))
        # the OLD rule: anything short of full marks became a mistake card (status defaults to 'mistake')
        db.execute("INSERT INTO fsrs_cards(user_id,question_id,card,due) VALUES (%s,%s,'{}',clock_timestamp())", (uid, qid))
        db.execute("INSERT INTO fsrs_events(user_id,question_id,event_key,rating,before_card,after_card,reviewed_at) VALUES (%s,%s,%s,1,'{}','{}',clock_timestamp())", (uid, qid, f"old-{qid}"))
        qids.append(qid)
    result = memory.reclassify_existing(db)
    assert result["before"] == {"mistake": 6}
    assert result["after"] == {"mistake": 3, "consolidate": 1, "mastered": 2}
    status = {r["question_id"]: r["status"] for r in db.execute("SELECT question_id,status FROM fsrs_cards WHERE user_id=%s", (uid,)).fetchall()}
    assert [status[x] for x in qids] == ["mistake", "consolidate", "mastered", "mistake", "mastered", "mistake"]
    assert db.execute("SELECT count(*) AS n FROM fsrs_cards WHERE user_id=%s", (uid,)).fetchone()["n"] == 6          # no card deleted
    assert db.execute("SELECT count(*) AS n FROM fsrs_events WHERE user_id=%s", (uid,)).fetchone()["n"] == 6        # nor any history
    assert memory.reclassify_existing(db) is None                                                                   # once
    assert legacy_reuse._state(db, memory.STATE_KEY)["before"] == {"mistake": 6}
    due = {r["question_id"] for r in memory.due(db, uid)}
    assert qids[2] not in due and qids[4] not in due and qids[0] in due                                          # mastered cards are not reviewed
