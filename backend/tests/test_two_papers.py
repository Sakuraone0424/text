"""The daily training is two independent papers (objective + subjective); questions move to the new points without being retired."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import learning_stats, quiz_v2, scoring_rescale, training_sessions, training_spec_v5 as v5
from app.ai_jobs import content_sha
from app.common import ensure_user
from app.question_quality import Blueprint, Question
from stats_helpers import item, paper, question
from two_paper_helpers import add_question, seed_bank


def blueprint(module):
    return Blueprint.model_validate(module.default_blueprint())


# ---- 1. the specification --------------------------------------------------------------------------------------------

def test_objective_paper_specification():
    spec = v5.OBJECTIVE
    assert spec.SPECIFICATION == "xiaohan-obj-150-v5"
    assert spec.COUNTS == {"single_choice": 15, "fill_blank": 15}
    assert (spec.POINTS["single_choice"], spec.POINTS["fill_blank"]) == (3, 2)
    assert spec.TOTAL_QUESTIONS == 60 and spec.TOTAL_POINTS == 150 and spec.POINTS_PER_SUBJECT == 75
    assert spec.DURATION_SECONDS == 3600
    assert 15 * 3 + 15 * 2 == 75


def test_subjective_paper_specification(monkeypatch):
    spec = v5.SUBJECTIVE_PAPER
    assert spec.SPECIFICATION == "xiaohan-subj-150-v5"
    assert spec.COUNTS == {"term": 4, "short_answer": 2, "essay": 1}
    assert (spec.POINTS["term"], spec.POINTS["short_answer"], spec.POINTS["essay"]) == (5, 15, 25)
    assert spec.TOTAL_QUESTIONS == 14 and spec.TOTAL_POINTS == 150 and spec.POINTS_PER_SUBJECT == 75
    assert 4 * 5 + 2 * 15 + 25 == 75
    assert spec.DURATION_SECONDS == 3600
    # the time limit is configurable
    monkeypatch.setenv("QUIZ_SUBJECTIVE_PAPER_SECONDS", "5400")
    assert spec.DURATION_SECONDS == 5400 and Blueprint.model_validate(spec.default_blueprint()).duration_seconds == 5400
    monkeypatch.setenv("QUIZ_SUBJECTIVE_PAPER_SECONDS", "nonsense")
    assert spec.DURATION_SECONDS == 3600
    # the objective paper's is not
    with pytest.raises(ValueError):
        Blueprint.model_validate({**v5.OBJECTIVE.default_blueprint(), "duration_seconds": 5400})


def test_daily_demand_and_two_day_reserve_per_subject():
    assert v5.COUNTS == {"single_choice": 15, "fill_blank": 15, "term": 4, "short_answer": 2, "essay": 1}
    assert v5.RESERVE == {"single_choice": 30, "fill_blank": 30, "term": 8, "short_answer": 4, "essay": 2}
    from app import auto_generation, cost_control
    assert {k: cost_control.reserve_target(n, auto_generation.STARTUP_RESERVE_DAYS) for k, n in v5.COUNTS.items()} == v5.RESERVE
    # single choice comes first when the generator picks what to write next
    order = auto_generation.balanced_generation_buckets({})
    assert order[0][1] == "single_choice"


def test_a_paper_must_hold_exactly_its_own_questions():
    obj = [dict(subject=s, question_type=k, max_points=v5.POINTS[k], rubric=[dict(points=v5.POINTS[k])])
           for s in v5.SUBJECTS for k, n in v5.OBJECTIVE.COUNTS.items() for _ in range(n)]
    assert v5.OBJECTIVE.validate_items(obj) is obj
    with pytest.raises(ValueError):
        v5.OBJECTIVE.validate_items(obj[:-1])
    with pytest.raises(ValueError):
        v5.SUBJECTIVE_PAPER.validate_items(obj)
    wrong = [dict(q) for q in obj]
    wrong[0]["max_points"] = 4
    with pytest.raises(ValueError):
        v5.OBJECTIVE.validate_items(wrong)


# ---- 2. two independent papers -----------------------------------------------------------------------------------------

def make_both(db, question_factory, passing_audit, monkeypatch, name="two-papers"):
    seed_bank(db, question_factory, passing_audit, monkeypatch)
    uid = ensure_user(db, name)
    day = date(2026, 12, 1)
    obj = quiz_v2.create_paper(db, uid, blueprint(v5.OBJECTIVE), day=day)
    subj = quiz_v2.create_paper(db, uid, blueprint(v5.SUBJECTIVE_PAPER), day=day)
    return uid, day, obj, subj


def test_the_two_papers_are_generated_and_timed_independently(db, question_factory, passing_audit, monkeypatch):
    uid, day, obj, subj = make_both(db, question_factory, passing_audit, monkeypatch)
    assert obj["paper_id"] != subj["paper_id"]
    assert (obj["kind"], subj["kind"]) == ("objective", "subjective")
    assert (obj["count"], subj["count"]) == (60, 14)
    assert (obj["max_points"], subj["max_points"]) == (150, 150)
    assert (obj["duration_seconds"], subj["duration_seconds"]) == (3600, 3600)
    # asking again for the same kind on the same day gives the same paper, never a second one
    assert quiz_v2.create_paper(db, uid, blueprint(v5.OBJECTIVE), day=day)["paper_id"] == obj["paper_id"]
    # no question is on both papers
    ids = {p: {r["question_id"] for r in db.execute("SELECT question_id FROM paper_items_v2 WHERE paper_id=%s", (p,)).fetchall()}
           for p in (obj["paper_id"], subj["paper_id"])}
    assert not ids[obj["paper_id"]] & ids[subj["paper_id"]]
    # starting one does not start the other
    started = training_sessions.start(db, uid, obj["paper_id"])
    assert started["status"] == "in_progress"
    assert quiz_v2.load_paper(db, uid, subj["paper_id"])["timing"]["status"] == "not_started"
    # the subjective paper starts later and has its own deadline
    later = training_sessions.start(db, uid, subj["paper_id"])
    assert later["deadline"] > started["deadline"]
    # handing in the objective paper scores it at once and leaves the subjective paper running
    revealed = quiz_v2.load_paper(db, uid, obj["paper_id"])
    answers = {}
    for q in revealed["questions"]:
        snap = db.execute("SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s", (obj["paper_id"], q["id"])).fetchone()["snapshot"]
        answers[q["id"]] = {"text": snap["answer"]}
    outcome = quiz_v2.submit(db, uid, obj["paper_id"], answers, expected_version=0)
    assert outcome["pending"] == 0 and outcome["score"] == 150 and outcome["max_points"] == 150
    assert quiz_v2.load_paper(db, uid, subj["paper_id"])["timing"]["status"] == "in_progress"


def test_either_paper_alone_counts_for_the_days_streak(db, question_factory, passing_audit, monkeypatch):
    seed_bank(db, question_factory, passing_audit, monkeypatch)
    today = learning_stats.today_cn(db)
    for name, module in (("only-objective", v5.OBJECTIVE), ("only-subjective", v5.SUBJECTIVE_PAPER)):
        uid = ensure_user(db, name)
        made = quiz_v2.create_paper(db, uid, blueprint(module), day=today)
        training_sessions.start(db, uid, made["paper_id"])
        db.execute("UPDATE training_sessions SET started_at=clock_timestamp()-interval '2 hours',deadline=clock_timestamp()-interval '1 hour' WHERE paper_id=%s", (made["paper_id"],))
        training_sessions.state(db, uid, made["paper_id"])      # past its deadline: handed in as it stands
        overview = learning_stats.overview(db, uid)
        assert overview["streak"]["current"] == 1, name
        assert overview["today_papers"][module.KIND]["state"] == "submitted"
        assert set(overview["today_papers"]) == {module.KIND}


def test_one_papers_bank_shortage_never_holds_back_the_other(db, question_factory, passing_audit, monkeypatch):
    seed_bank(db, question_factory, passing_audit, monkeypatch, types=("single_choice", "fill_blank"))
    uid = ensure_user(db, "short-subjective")
    day = date(2026, 12, 2)
    assert quiz_v2.create_paper(db, uid, blueprint(v5.OBJECTIVE), day=day)["count"] == 60
    with pytest.raises(quiz_v2.BankShortage) as short:
        quiz_v2.create_paper(db, uid, blueprint(v5.SUBJECTIVE_PAPER), day=day)
    assert {s["question_type"] for s in short.value.shortages} == {"term", "short_answer", "essay"}


def test_the_evening_preparation_makes_both_papers(db, question_factory, passing_audit, monkeypatch):
    from app import automation

    seed_bank(db, question_factory, passing_audit, monkeypatch)
    ensure_user(db, "evening")
    monkeypatch.setenv("QUIZ_DAILY_PREP_ENABLED", "1")
    monkeypatch.delenv("QUIZ_DAILY_SPECIFICATION", raising=False)
    now = training_sessions.server_now(db)
    fixed = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if fixed < now:
        fixed += timedelta(days=1)
    monkeypatch.setattr(automation, "server_now", lambda c: fixed)
    assert automation.prepare_daily(db) == 2
    assert automation.prepare_daily(db) == 0
    kinds = sorted(r["kind"] for r in db.execute("SELECT kind FROM papers_v2").fetchall())
    assert kinds == ["objective", "subjective"]
    assert db.execute("SELECT count(*) AS n FROM training_sessions").fetchone()["n"] == 0


def test_the_daily_variable_may_still_name_a_retired_paper_or_one_new_paper():
    from app import automation

    both = automation.daily_blueprints({})
    assert [b.specification for b in both] == ["xiaohan-obj-150-v5", "xiaohan-subj-150-v5"]
    assert [b.specification for b in automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": "xiaohan-60q-300pt-v4"})] == [b.specification for b in both]
    assert [b.specification for b in automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": "xiaohan-subj-150-v5"})] == ["xiaohan-subj-150-v5"]
    with pytest.raises(ValueError):
        automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": "something-else"})


# ---- 3. existing questions move to the new points, none is retired --------------------------------------------------------

V4 = v5.V4_POINTS


@pytest.mark.parametrize("points,total", [([3, 3], 5), ([2, 2, 2], 5), ([4], 3), ([4], 2), ([1, 1, 2], 2), ([3, 3, 3, 3], 15),
                                          ([6, 6, 6], 25), ([2, 2, 2, 3, 3, 3, 3], 25), ([5, 1], 5), ([1, 1, 1, 1, 1, 1, 12], 25)])
def test_rubric_points_scale_in_proportion_to_halves_and_add_up_exactly(points, total):
    scaled = scoring_rescale.scale_points(points, total)
    assert sum(scaled) == total
    assert all(p >= 0.5 and (p * 2) == int(p * 2) for p in scaled)
    assert len(scaled) == len(points)
    # nobody ends up more than a point and a half from their exact share
    assert all(abs(p - o * total / sum(points)) <= 1.5 for p, o in zip(scaled, points))


def old_scale(db, question_factory, passing_audit, subject, kind, rubric_points=None):
    qid = add_question(db, question_factory, passing_audit, subject, kind, points=V4[kind])
    if rubric_points:
        db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
        db.execute("UPDATE reviewed_questions SET content=jsonb_set(content,'{rubric}',%s) WHERE id=%s",
                   (Jsonb([dict(description=f"要点{i}", points=p, knowledge_tags=[]) for i, p in enumerate(rubric_points)]), qid))
        db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")
        content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
        db.execute("UPDATE auto_question_states SET content_sha256=%s WHERE question_id=%s", (content_sha(content), qid))
    return qid


def test_every_old_scale_question_is_converted_not_retired(db, question_factory, passing_audit, monkeypatch):
    from app import auto_quality, source_policy

    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda content: True)
    monkeypatch.setattr("app.auto_generation.pinned_science_evidence", lambda content: True)
    uid = ensure_user(db, "rescale")
    ids = {}
    layout = {"single_choice": None, "fill_blank": None, "term": [3, 3], "short_answer": [4, 4, 4], "essay": [3, 3, 3, 3, 3, 3]}
    for subject in v5.SUBJECTS:
        for kind, rubric_points in layout.items():
            ids[(subject, kind)] = [old_scale(db, question_factory, passing_audit, subject, kind, rubric_points) for _ in range(3)]
    everything = [q for qs in ids.values() for q in qs]
    monkeypatch.setattr(auto_quality, "ready", lambda c, qid: qid in everything)
    before = {q: db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (q,)).fetchone()["content"] for q in everything}
    stock_before = scoring_rescale.stock_by_type(db, V4)["rescale"]
    assert all(n == 3 for per in stock_before.values() for n in per.values())

    report = scoring_rescale.run(db)
    assert report["rescaled"] == len(everything) == 30 and report["invalidated"] == 0
    assert report["by_type"] == {"single_choice": 6, "fill_blank": 6, "term": 6, "short_answer": 6, "essay": 6}
    # nothing is retired, withdrawn or made unready
    assert db.execute("SELECT count(*) AS n FROM auto_question_states WHERE status='retired'").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM auto_question_states WHERE status='ready'").fetchone()["n"] == 30
    assert db.execute("SELECT count(*) AS n FROM reviewed_questions WHERE status='published'").fetchone()["n"] == 30
    # same stock per type before and after (counted at the old and at the new points)
    assert report["stock_before"]["rescale"] == stock_before
    assert report["stock_after"]["rescale"] == stock_before
    assert scoring_rescale.stock_by_type(db, v5.POINTS)["rescale"] == stock_before
    for (subject, kind), qs in ids.items():
        for qid in qs:
            after = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
            was = before[qid]
            assert after["max_points"] == v5.POINTS[kind]
            assert sum(c["points"] for c in after["rubric"]) == v5.POINTS[kind]
            assert [c["description"] for c in after["rubric"]] == [c["description"] for c in was["rubric"]]
            for field in ("stem", "answer", "accepted_answers", "options", "evidence", "knowledge_tags", "subject", "question_type"):
                assert after[field] == was[field], field
            Question.model_validate(after)                  # a valid question under the new scale
            if after.get("blanks"):
                assert sum(b["points"] for b in after["blanks"]) == v5.POINTS[kind]
            # the review bound to the content as reviewed still matches
            assert content_sha(scoring_rescale.read_in_original_scoring(db, qid, after)) == content_sha(was)
            assert qid in scoring_rescale.rescaled_ids(db, [qid])
    # fixed examples: term [3,3] -> 5; short answer [4,4,4] -> 15; fill-in's single point -> 2; single choice -> 3
    rubric_of = lambda q: [c["points"] for c in db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (q,)).fetchone()["content"]["rubric"]]
    assert rubric_of(ids[("biochemistry", "term")][0]) == [2.5, 2.5]
    assert rubric_of(ids[("biochemistry", "short_answer")][0]) == [5, 5, 5]
    assert rubric_of(ids[("biochemistry", "fill_blank")][0]) == [2]
    assert rubric_of(ids[("biochemistry", "single_choice")][0]) == [3]
    assert sum(rubric_of(ids[("biochemistry", "essay")][0])) == 25
    # run again: nothing left to do, nothing changes
    assert scoring_rescale.run(db) == {"rescaled": 0}
    assert db.execute("SELECT value FROM legacy_reuse_state WHERE key=%s", (scoring_rescale.STATE_KEY,)).fetchone()["value"]["rescaled"] == 30


def test_a_rescaled_question_is_on_the_new_paper_with_the_new_points(db, question_factory, passing_audit, monkeypatch):
    from app import auto_quality, source_policy

    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda content: True)
    monkeypatch.setattr("app.auto_generation.pinned_science_evidence", lambda content: True)
    ids = []
    for subject in v5.SUBJECTS:
        for kind, count in v5.OBJECTIVE.COUNTS.items():
            ids += [old_scale(db, question_factory, passing_audit, subject, kind) for _ in range(count)]
    monkeypatch.setattr(auto_quality, "ready", lambda c, qid: qid in ids)
    # at the old scale none of them fits the new paper
    uid = ensure_user(db, "old-to-new")
    with pytest.raises(quiz_v2.BankShortage):
        quiz_v2.create_paper(db, uid, blueprint(v5.OBJECTIVE), day=date(2026, 12, 4))
    scoring_rescale.run(db)
    made = quiz_v2.create_paper(db, uid, blueprint(v5.OBJECTIVE), day=date(2026, 12, 4))
    assert made["count"] == 60 and made["max_points"] == 150
    shown = quiz_v2.load_paper(db, uid, made["paper_id"])
    assert training_sessions.start(db, uid, made["paper_id"])["status"] == "in_progress"
    shown = quiz_v2.load_paper(db, uid, made["paper_id"])
    assert {q["scoring_note"] for q in shown["questions"]} == {"分值已按比例换算"}
    assert sum(q["max_points"] for q in shown["questions"]) == 150


def test_work_in_flight_at_the_old_maximum_is_not_lost(db, question_factory, passing_audit, monkeypatch):
    """A question that a request written at the old scale hands in a moment later is converted, not retired; a request that has not
    started is asked again at the new scale."""
    from app import auto_quality

    uid = ensure_user(db, "inflight")
    pending = db.execute("INSERT INTO ai_jobs(user_id,job_key,kind,settings_snapshot,payload) VALUES (%s,'g-old','generation','{}',%s) RETURNING id",
                         (uid, Jsonb({"subject": "biochemistry", "question_type": "term", "max_points": 6}))).fetchone()["id"]
    current = db.execute("INSERT INTO ai_jobs(user_id,job_key,kind,settings_snapshot,payload) VALUES (%s,'g-new','generation','{}',%s) RETURNING id",
                         (uid, Jsonb({"subject": "biochemistry", "question_type": "term", "max_points": 5}))).fetchone()["id"]
    late = old_scale(db, question_factory, passing_audit, "biochemistry", "term", [3, 3])
    scoring_rescale.run(db)
    states = {r["id"]: (r["state"], r["error_code"]) for r in db.execute("SELECT id,state,error_code FROM ai_jobs WHERE id=ANY(%s)", ([pending, current],)).fetchall()}
    assert states[pending][1] == "superseded_scoring" and states[current][0] != "blocked"
    auto_quality.retire_old_scoring(db)
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (late,)).fetchone()["status"] == "ready"


# ---- 4. old mixed papers stay readable, statistics keep the kinds apart ---------------------------------------------------

def day_of(n):
    return date(2026, 11, 1) + timedelta(days=n)


def test_old_mixed_papers_stay_readable_and_are_labelled(db, question_factory, passing_audit, monkeypatch):
    from app import paper_review

    uid = ensure_user(db, "legacy-v4")
    made = paper(db, uid, day_of(0), [item(points=4, got=4), item(qtype="term", points=6, got=3)], spec="xiaohan-60q-300pt-v4")
    loaded = quiz_v2.load_paper(db, uid, made["paper_id"])
    assert loaded["mode"] == "reviewed_60" and loaded["kind"] == "mixed" and loaded["kind_label"] == "旧版混合卷"
    review = paper_review.summary(db, uid, made["paper_id"])
    assert review["specification_name"].startswith("旧版混合卷") and review["score"] == 7 and review["max_points"] == 10
    cards = learning_stats.history(db, uid)
    assert [c["kind"] for c in cards["papers"]] == ["mixed"] and cards["papers"][0]["kind_label"] == "旧版混合卷"


def test_scores_and_history_are_kept_apart_by_kind(db):
    uid = ensure_user(db, "kinds")
    today = learning_stats.today_cn(db)
    obj = lambda n, got: paper(db, uid, today - timedelta(days=n), [item(points=3, got=got), item(qtype="fill_blank", points=2, got=2)], spec="xiaohan-obj-150-v5")
    subj = lambda n, got: paper(db, uid, today - timedelta(days=n), [item(qtype="term", points=5, got=got), item(qtype="essay", points=25, got=10)], spec="xiaohan-subj-150-v5")
    mixed = lambda n: paper(db, uid, today - timedelta(days=n), [item(points=4, got=4)], spec="xiaohan-60q-300pt-v4")
    obj(3, 3); obj(2, 0); subj(3, 5); subj(1, 1); mixed(4)
    o = learning_stats.overview(db, uid)
    by = o["last7_by_kind"]
    assert by["objective"]["papers"] == 2 and by["objective"]["max_points"] == 5
    assert by["objective"]["avg_rate"] == round((5 / 5 + 2 / 5) / 2, 4)
    assert by["subjective"]["papers"] == 2 and by["subjective"]["max_points"] == 30
    assert by["subjective"]["avg_rate"] == round((15 / 30 + 11 / 30) / 2, 4)
    assert by["mixed"]["papers"] == 1 and by["mixed"]["max_points"] == 4
    # the old field is the old mixed papers' only
    assert o["last7"]["papers"] == 1 and o["last7"]["max_points"] == 4
    # per-question rates do not care which paper the question was on: objective = single choice + fill-in, in every paper
    assert o["objective_rate"] == round((3 + 2 + 0 + 2 + 4) / (3 + 2 + 3 + 2 + 4), 4)
    profile = learning_stats.profile(db, uid)
    assert sorted({t["kind"] for t in profile["trend"]}) == ["mixed", "objective", "subjective"]
    assert all(t["kind"] == learning_stats.paper_kind(t["spec"]) for t in profile["trend"])
    history = learning_stats.history(db, uid)
    assert {c["kind"]: c["kind_label"] for c in history["papers"]} == {"objective": "客观题卷", "subjective": "主观题卷", "mixed": "旧版混合卷"}
    assert {k["kind"]: k["count"] for k in history["kinds"]} == {"objective": 2, "subjective": 2, "mixed": 1}
    only = learning_stats.history(db, uid, "subjective")
    assert len(only["papers"]) == 2 and {c["kind"] for c in only["papers"]} == {"subjective"}
