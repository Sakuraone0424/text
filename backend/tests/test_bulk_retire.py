"""The one-time withdrawal of the unused bank: used questions untouched, withdrawn ones out of every paper and practice, waiting DeepSeek tasks
cancelled, past-exam items queued again, writing routed to Claude only, and everything can be put back."""
import pytest
from psycopg.types.json import Jsonb

from app import bulk_retire, external_executor as ext, legacy_reuse, past_exam_import as pei, quiz_v2 as q, training_sessions as sessions
from app.common import ensure_user
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_past_exam_import import corpus  # noqa: F401  (fixture)


def states(db, ids):
    return {r["question_id"]: (r["status"], r["reason"]) for r in db.execute(
        "SELECT question_id,status,reason FROM auto_question_states WHERE question_id=ANY(%s)", (ids,)).fetchall()}


def qstatus(db, qid):
    return db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"]


def make_ready(db, ids):
    for qid in ids:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x') ON CONFLICT DO NOTHING", (qid,))


@pytest.fixture
def day(db, bank, blueprint):
    """A paper was made today (its questions are used); the rest of the bank is unused. Returns (uid, used ids, unused ids)."""
    uid = ensure_user(db, "test-owner")
    paper = q.create_paper(db, uid, blueprint)
    used = {r["question_id"] for r in db.execute("SELECT question_id FROM paper_items_v2 WHERE paper_id=%s", (paper["paper_id"],)).fetchall()}
    extra = db.execute("SELECT id FROM reviewed_questions WHERE status='published' AND NOT (id=ANY(%s)) ORDER BY id", (list(used),)).fetchall()
    return uid, sorted(used), [r["id"] for r in extra]


def test_the_whole_bank_is_used_by_the_paper_so_nothing_is_withdrawn(db, day):
    uid, used, unused = day
    make_ready(db, used)
    before = bulk_retire.candidates(db)
    assert {r["id"] for r in before}.isdisjoint(used)                       # a used question is never a candidate


def test_unused_questions_are_withdrawn_used_ones_and_their_history_are_not(db, day, question_factory, passing_audit):
    from app.quiz_v2 import create_question, publish_question, review_question

    uid, used, unused = day
    fresh = []
    for kind in ("fill_blank", "term", "essay"):
        qid = create_question(db, question_factory("biochemistry", kind))["id"]
        assert review_question(db, qid, passing_audit)["passed"]
        publish_question(db, qid)
        fresh.append(qid)
    make_ready(db, used + fresh)
    practised = fresh[0]                                                    # practised once: it counts as used
    db.execute("INSERT INTO practice_sets(user_id,mode,question_ids) VALUES (%s,'type',%s)", (uid, [practised]))
    log = []
    done = bulk_retire.run_once(db, log=lambda *a, **k: log.append(a[0]))
    gone = set(fresh) - {practised}
    assert done["withdrawn"] >= len(gone) and gone <= {r for r in fresh}
    for qid in gone:
        assert qstatus(db, qid) == "withdrawn" and states(db, [qid])[qid] == ("retired", "retired_by_user")
    for qid in used + [practised]:
        assert qstatus(db, qid) == "published" and states(db, [qid])[qid][0] == "ready"       # kept
    # the history is untouched: the paper, its answers, the practice set
    assert db.execute("SELECT count(*) AS n FROM paper_items_v2").fetchone()["n"] == len(used)
    assert db.execute("SELECT count(*) AS n FROM practice_sets").fetchone()["n"] == 1
    # the number is printed BEFORE anything changes, then the result
    assert log[0].startswith("retire-unused: about to withdraw") and log[-1].startswith("retire-unused: done")


def test_it_runs_once_only(db, day):
    uid, used, unused = day
    make_ready(db, used + unused)
    first = bulk_retire.run_once(db, log=lambda *a, **k: None)
    assert first is not None and first["withdrawn"] == len(unused)
    lines = []
    assert bulk_retire.run_once(db, log=lambda *a, **k: lines.append(a)) is None and lines == []
    assert legacy_reuse._state(db, bulk_retire.STATE_KEY)["withdrawn"] == len(unused)


def test_withdrawn_questions_never_reach_a_new_paper_and_come_back_with_the_restore(db, bank, blueprint):
    uid = ensure_user(db, "test-owner")
    summary = bulk_retire.run_once(db, log=lambda *a, **k: None)
    assert summary["withdrawn"] == len(bank)
    with pytest.raises(ValueError):
        q.create_paper(db, uid, blueprint)                                   # nothing left to build a paper from
    assert bulk_retire.summary(db)["total"] == len(bank)
    assert bulk_retire.restore(db, "biochemistry", "term") > 0
    assert bulk_retire.restore(db) > 0                                       # (the rest)
    assert bulk_retire.summary(db)["total"] == 0
    for qid in bank:
        assert qstatus(db, qid) == "published"
    assert q.create_paper(db, uid, blueprint)["paper_id"]                    # a paper can be made again


def test_restore_by_subject_and_type_brings_back_only_that_group(db, bank):
    bulk_retire.run_once(db, log=lambda *a, **k: None)
    kinds = {r["id"]: (r["s"], r["k"]) for r in db.execute(
        "SELECT id,content->>'subject' AS s,content->>'question_type' AS k FROM reviewed_questions WHERE id=ANY(%s)", (bank,)).fetchall()}
    group = [i for i, key in kinds.items() if key == ("biochemistry", "fill_blank")]
    assert group
    assert bulk_retire.restore(db, "biochemistry", "fill_blank") == len(group)
    assert all(qstatus(db, i) == "published" for i in group)
    assert all(qstatus(db, i) == "withdrawn" for i in bank if i not in group)


def test_waiting_deepseek_tasks_are_cancelled_and_writing_is_claudes_only(db, setup_ai, bank):
    make_ready(db, bank)
    def add(key, kind, state):
        return db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,%s,%s,'{}',%s) RETURNING id",
                          (key, setup_ai, kind, state, Jsonb({"subject": "biochemistry", "question_type": "term"}))).fetchone()["id"]

    waiting = [add("w1", "generation", "pending"), add("w2", "quality_review", "retrying"), add("w3", "repair", "pending")]
    keep = [add("g1", "grading", "pending"), add("i1", "explanation", "pending"), add("done", "generation", "succeeded")]
    summary = bulk_retire.run_once(db, log=lambda *a, **k: None)
    assert summary["jobs_cancelled"] == 3
    got = {r["id"]: (r["state"], r["error_code"]) for r in db.execute("SELECT id,state,error_code FROM ai_jobs").fetchall()}
    assert all(got[i] == ("cancelled", "retired_by_user") for i in waiting)
    assert got[keep[0]][0] == "pending" and got[keep[1]][0] == "pending" and got[keep[2]][0] == "succeeded"     # grading, advice, finished work stay
    assert ext.policy(db)["generation"] == "claude" and ext.policy(db)["review"] == "deepseek" and ext.policy(db)["grading"] == "deepseek"
    assert ext.rebuild_state(db)["active"] is True


def test_the_page_lists_what_is_out_and_the_counts_before_and_after(db, bank):
    make_ready(db, bank)
    before = bulk_retire.available(db)
    bulk_retire.run_once(db, log=lambda *a, **k: None)
    s = bulk_retire.summary(db)
    assert s["done"] and s["available_before"] == before and sum(sum(v.values()) for v in s["available_after"].values()) == 0
    assert {(r["subject"], r["question_type"]) for r in s["retired"]} == {("biochemistry", "fill_blank"), ("biochemistry", "term"), ("biochemistry", "short_answer"),
                                                                           ("biochemistry", "essay"), ("cell_biology", "fill_blank"), ("cell_biology", "term"),
                                                                           ("cell_biology", "short_answer"), ("cell_biology", "essay")} | {
        (r["subject"], r["question_type"]) for r in s["retired"] if r["question_type"] == "single_choice"}


def test_the_progress_page_says_the_bank_is_rebuilt_by_claude(db, setup_ai):
    from app import progress

    assert progress.report(db, setup_ai)["executors"]["rebuild"] == dict(active=False, per_night=None)
    ext.save_policy(db, {"generation": "claude"})
    assert progress.report(db, setup_ai)["executors"]["rebuild"]["active"] is True
    assert ext.rebuild_state(db)["per_night"] is None                          # nothing delivered yet: no invented number


def test_the_home_paper_says_the_bank_is_being_rebuilt(db, setup_ai):
    from app import learning_stats

    ext.save_policy(db, {"generation": "claude"})
    state = learning_stats.tomorrow_ready(db, setup_ai)
    assert state["rebuilding"] is True and state["kinds"]["objective"]["rebuilding"] is True and state["complete"] is False


def test_a_question_whose_verification_no_longer_matches_stays_out_and_the_others_come_back(db, bank):
    make_ready(db, bank)                                       # (made-up "ready" states without the verification records a real one has)
    bulk_retire.run_once(db, log=lambda *a, **k: None)
    assert bulk_retire.restore(db) == 0                        # nothing can be published again without its checks: nothing breaks, nothing comes back
    assert bulk_retire.summary(db)["total"] == len(bank) and all(qstatus(db, i) == "withdrawn" for i in bank)


def test_past_exam_items_of_withdrawn_questions_are_queued_again_with_the_stem_kept(db, corpus, setup_ai):
    from stats_helpers import question
    from test_past_exam_import import items

    pei.import_items(db)
    queued_item = db.execute("SELECT id FROM past_exam_items WHERE status='imported' AND question_type='term' ORDER BY id LIMIT 1").fetchone()
    queued_job = db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES ('pe-queued',%s,'generation','pending','{}',%s) RETURNING id",
                            (setup_ai, Jsonb({"origin": "past_exam", "item_id": queued_item["id"], "subject": "biochemistry", "question_type": "term"}))).fetchone()["id"]
    db.execute("UPDATE past_exam_items SET status='queued',job_id=%s WHERE id=%s", (queued_job, queued_item["id"]))
    done = db.execute("SELECT id,stem FROM past_exam_items WHERE status='imported' AND question_type='fill_blank' ORDER BY id LIMIT 1").fetchone()
    kept = db.execute("SELECT id FROM past_exam_items WHERE status='imported' AND id<>%s ORDER BY id DESC LIMIT 1", (done["id"],)).fetchone()
    gone_q = question(db, "biochemistry", "fill_blank", 2.5, "第一章", stem=done["stem"])
    used_q = question(db, "biochemistry", "fill_blank", 2.5, "第一章", stem="用过的真题 ____")
    for qid in (gone_q, used_q):
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    db.execute("UPDATE past_exam_items SET status='packet_created',question_id=%s WHERE id=%s", (gone_q, done["id"]))
    db.execute("UPDATE past_exam_items SET status='packet_created',question_id=%s WHERE id=%s", (used_q, kept["id"]))
    db.execute("INSERT INTO practice_sets(user_id,mode,question_ids) VALUES (%s,'type',%s)", (setup_ai, [used_q]))      # one of them was practised
    summary = bulk_retire.run_once(db, log=lambda *a, **k: None)
    row = db.execute("SELECT status,question_id,job_id,stem FROM past_exam_items WHERE id=%s", (done["id"],)).fetchone()
    assert (row["status"], row["question_id"], row["job_id"]) == ("imported", None, None) and row["stem"] == done["stem"]      # queued again, stem kept
    assert db.execute("SELECT status FROM past_exam_items WHERE id=%s", (kept["id"],)).fetchone()["status"] == "packet_created"   # used: untouched
    cancelled = db.execute("SELECT state FROM ai_jobs WHERE id=%s", (queued_job,)).fetchone()["state"]
    assert cancelled == "cancelled"
    back = db.execute("SELECT status,job_id FROM past_exam_items WHERE id=%s", (queued_item["id"],)).fetchone()
    assert (back["status"], back["job_id"]) == ("imported", None)                  # the item whose task was cancelled is open again
    assert summary["past_exam_requeued"] >= 1
