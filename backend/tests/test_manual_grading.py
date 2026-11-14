"""Grading by hand: first scores, changes, the AI's original, mistakes and review cards, and who may overwrite whom."""
import pytest

from app import auto_grading as grading, auto_worker, grading_recovery, manual_grading as manual, memory, quiz_v2 as quiz
from test_ai_automation import setup_ai  # noqa: F401
from test_auto_grading import finish, learner, submit, verdict  # noqa: F401


def pending_term(db, paper):
    rows = db.execute("SELECT a.question_id,i.snapshot FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN paper_items_v2 i ON i.paper_id=t.paper_id "
                      "AND i.question_id=a.question_id WHERE t.paper_id=%s AND a.points IS NULL ORDER BY i.position", (paper["paper_id"],)).fetchall()
    return next(r for r in rows if r["snapshot"]["question_type"] in ("term", "short_answer") and r["snapshot"].get("rubric"))


def points_of(db, paper, qid):
    return db.execute("SELECT a.* FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.question_id=%s", (paper["paper_id"], qid)).fetchone()


def card(db, uid, qid):
    return db.execute("SELECT status FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (uid, qid)).fetchone()


def test_a_pending_answer_is_graded_by_hand_the_total_follows_and_a_low_score_is_a_mistake(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    row = pending_term(db, paper)
    q = row["snapshot"]
    form = manual.view(db, uid, paper["paper_id"], row["question_id"])
    assert form["points"] is None and form["label"] is None and form["criteria"] and form["your_answer"] and form["reference"]
    before = quiz.result(db, uid, paper["paper_id"])
    low = [0] * len(q["rubric"])
    saved = manual.save(db, uid, paper["paper_id"], row["question_id"], low, "答非所问")
    assert saved["points"] == 0 and saved["label"] == "手动判分" and saved["note"] == "答非所问" and saved["graded_by"] == "手动判分"
    after = quiz.result(db, uid, paper["paper_id"])
    assert after["pending"] == before["pending"] - 1 and after["graded_points"] == before["graded_points"]
    assert card(db, uid, row["question_id"])["status"] == "mistake"                                    # 0 % is below the first line
    mid = [c["points"] for c in q["rubric"]]
    mid[0] = max(0, mid[0] - 1) if len(mid) == 1 else mid[0]
    full = manual.save(db, uid, paper["paper_id"], row["question_id"], [c["points"] for c in q["rubric"]], None)        # again: changeable
    assert full["points"] == q["max_points"] and full["label"] == "手动判分"
    assert card(db, uid, row["question_id"])["status"] == "mastered"                                    # full marks: leaves the mistake book


def test_partial_scores_use_the_lines_and_must_stay_inside_each_scoring_point(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    row = pending_term(db, paper)
    rubric = row["snapshot"]["rubric"]
    with pytest.raises(ValueError, match="scores_must_match"):
        manual.save(db, uid, paper["paper_id"], row["question_id"], [c["points"] + 1 for c in rubric])
    with pytest.raises(ValueError, match="scores_must_match"):
        manual.save(db, uid, paper["paper_id"], row["question_id"], [0] * (len(rubric) + 1))
    with pytest.raises(ValueError, match="scores_must_match"):
        manual.save(db, uid, paper["paper_id"], row["question_id"], [-1] * len(rubric))
    total = sum(c["points"] for c in rubric)
    # about 70 % of the total: "to consolidate" (60 % - 85 %)
    import math

    target, picks = math.ceil(total * 0.7), []
    for c in rubric:
        take = min(c["points"], target)
        picks.append(take)
        target -= take
    saved = manual.save(db, uid, paper["paper_id"], row["question_id"], picks)
    assert 0.6 <= saved["points"] / saved["max_points"] < 0.85 and card(db, uid, row["question_id"])["status"] == "consolidate"


def test_changing_an_ai_score_keeps_the_ai_original_and_is_marked_adjusted(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    job = grading.schedule(db, uid, paper["paper_id"])[0]
    qid = job["payload"]["question_id"]
    rubric = job["payload"]["question"]["rubric"]
    finish(db, job, verdict(job, points=[c["points"] for c in rubric]))
    auto_worker.dispatch_completed(db)
    ai = points_of(db, paper, qid)
    assert ai["graded_by"] == "AI自动核验" and ai["points"] == job["payload"]["question"]["max_points"]
    changed = manual.save(db, uid, paper["paper_id"], qid, [0] * len(rubric), "AI 给高了")
    assert changed["label"] == "已手动调整" and changed["points"] == 0
    assert changed["ai_original"]["points"] == ai["points"] and changed["ai_original"]["graded_by"] == "AI自动核验"
    again = manual.save(db, uid, paper["paper_id"], qid, [rubric[0]["points"]] + [0] * (len(rubric) - 1))
    assert again["label"] == "已手动调整" and again["ai_original"]["points"] == ai["points"]              # the first AI score stays the original
    assert card(db, uid, qid) is not None


def test_an_ai_regrade_never_overwrites_a_manual_score_unless_the_learner_hands_it_back(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    job = grading.schedule(db, uid, paper["paper_id"])[0]
    qid = job["payload"]["question_id"]
    rubric = job["payload"]["question"]["rubric"]
    manual.save(db, uid, paper["paper_id"], qid, [0] * len(rubric))
    # the AI task that was already on its way finishes afterwards: its result is refused
    finish(db, job, verdict(job, points=[c["points"] for c in rubric]))
    auto_worker.dispatch_completed(db)
    assert points_of(db, paper, qid)["points"] == 0 and points_of(db, paper, qid)["graded_by"] == "手动判分"
    # the regrade buttons and the housekeeping leave it alone (it is not pending)
    assert grading_recovery.requeue(db, uid, paper_id=paper["paper_id"], question_id=qid, final_too=True).get("new_task") is None
    # only the learner's own choice gives it back to the AI
    queued = manual.hand_to_ai(db, uid, paper["paper_id"], qid)
    assert (queued.get("new_task") or queued.get("already_waiting")) == 1 and points_of(db, paper, qid)["points"] is None      # (the refused task spawned its second look)
    fresh = [j for j in db.execute("SELECT * FROM ai_jobs WHERE kind='grading' AND state='pending'").fetchall() if j["payload"]["question_id"] == qid][0]
    finish(db, fresh, verdict(fresh, points=[c["points"] for c in rubric]))
    auto_worker.dispatch_completed(db)
    now = points_of(db, paper, qid)
    assert now["points"] == fresh["payload"]["question"]["max_points"] and now["graded_by"] == "AI自动核验"
    with pytest.raises(ValueError, match="no_manual_score"):
        manual.hand_to_ai(db, uid, paper["paper_id"], qid)


def test_an_objective_answer_can_be_adjusted_too_and_other_learners_cannot_touch_it(db, learner):
    from app.common import ensure_user

    uid, paper = learner
    submit(db, uid, paper)
    single = db.execute("SELECT a.question_id,i.snapshot FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN paper_items_v2 i ON i.paper_id=t.paper_id "
                        "AND i.question_id=a.question_id WHERE t.paper_id=%s AND i.snapshot->>'question_type'='single_choice' LIMIT 1", (paper["paper_id"],)).fetchone()
    form = manual.view(db, uid, paper["paper_id"], single["question_id"])
    assert len(form["criteria"]) >= 1 and form["points"] is not None
    saved = manual.save(db, uid, paper["paper_id"], single["question_id"], [c["points"] for c in form["criteria"]])
    assert saved["points"] == single["snapshot"]["max_points"] and saved["label"] == "已手动调整"
    stranger = ensure_user(db, "someone-else")
    with pytest.raises(LookupError):
        manual.save(db, stranger, paper["paper_id"], single["question_id"], [0])
    with pytest.raises(LookupError):
        manual.view(db, stranger, paper["paper_id"], single["question_id"])


def test_the_trigger_still_refuses_a_score_change_that_does_not_come_through_the_manual_code(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    graded = db.execute("SELECT a.question_id FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.points IS NOT NULL LIMIT 1", (paper["paper_id"],)).fetchone()
    with pytest.raises(Exception, match="immutable"):
        db.execute("UPDATE answers_v2 SET points=0 WHERE question_id=%s", (graded["question_id"],))


def test_the_http_endpoints(client, db, learner):
    from test_api_v2 import LEARNER

    uid, paper = learner
    submit(db, uid, paper)
    row = pending_term(db, paper)
    pid, qid = paper["paper_id"], row["question_id"]
    form = client.get(f"/v2/papers/{pid}/manual/{qid}", headers=LEARNER)
    assert form.status_code == 200 and form.json()["criteria"]
    saved = client.put(f"/v2/papers/{pid}/manual/{qid}", headers=LEARNER, json={"criterion_points": [c["points"] for c in form.json()["criteria"]], "note": "对"})
    assert saved.status_code == 200 and saved.json()["label"] == "手动判分" and saved.json()["points"] == row["snapshot"]["max_points"]
    assert client.put(f"/v2/papers/{pid}/manual/{qid}", headers=LEARNER, json={"criterion_points": [999] * len(form.json()["criteria"])}).status_code == 409
    back = client.post(f"/v2/papers/{pid}/manual/{qid}/ai", headers=LEARNER)
    assert back.status_code == 200 and back.json()["new_task"] == 1
    assert client.get("/v2/papers/999999/manual/1", headers=LEARNER).status_code == 404
