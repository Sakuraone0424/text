"""A question that has been used (on a paper or in a practice set) never goes on a daily paper again, nor one with its stem;
no mistakes are mixed into a daily paper; a short bank means no paper and no re-use; stock never counts a used question."""
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from app import auto_generation as gen, cost_control, quiz_v2 as q
from app.common import ensure_user, today_cn
from app.quiz_v2 import create_question, publish_question, review_question


def add_bank(db, blueprint, question_factory, passing_audit, stem_of=None):
    ids = []
    for subject, quotas in blueprint.quotas.items():
        for kind, count in quotas.items():
            for _ in range(count):
                qid = create_question(db, question_factory(subject, kind, stem_of() if stem_of else None))["id"]
                assert review_question(db, qid, passing_audit)["passed"]
                publish_question(db, qid)
                ids.append(qid)
    return ids


@pytest.fixture
def uid(db):
    return ensure_user(db, "used-learner")


def paper_ids(db, paper):
    return [r["question_id"] for r in db.execute("SELECT question_id FROM paper_items_v2 WHERE paper_id=%s", (paper["paper_id"],)).fetchall()]


def test_a_new_paper_never_contains_a_question_from_an_earlier_one(db, uid, bank, blueprint, question_factory, passing_audit):
    first = q.create_paper(db, uid, blueprint, today_cn() - timedelta(days=30))     # long ago: the old 14-day rule would let them back
    second_bank = add_bank(db, blueprint, question_factory, passing_audit)
    second = q.create_paper(db, uid, blueprint, today_cn())
    assert not set(paper_ids(db, first)) & set(paper_ids(db, second))
    assert set(paper_ids(db, second)) <= set(second_bank)


def test_a_short_bank_makes_no_paper_and_old_questions_are_not_used_to_fill_it(db, uid, bank, blueprint):
    q.create_paper(db, uid, blueprint, today_cn() - timedelta(days=60))             # the whole bank is used, two months ago
    with pytest.raises(q.BankShortage) as short:
        q.create_paper(db, uid, blueprint, today_cn())
    assert sum(x["missing"] for x in short.value.shortages) == 28                       # every place is missing; nothing is re-used
    assert db.execute("SELECT count(*) AS n FROM papers_v2 WHERE user_id=%s AND paper_date=%s", (uid, today_cn())).fetchone()["n"] == 0


def test_a_question_with_the_same_stem_as_a_used_one_is_never_chosen(db, uid, bank, blueprint, question_factory, passing_audit):
    first = q.create_paper(db, uid, blueprint, today_cn() - timedelta(days=30))
    used_stems = [r["stem"] for r in db.execute("SELECT snapshot->>'stem' AS stem FROM paper_items_v2 WHERE paper_id=%s", (first["paper_id"],)).fetchall()]
    twin_ids = add_bank(db, blueprint, question_factory, passing_audit)
    # a bank of repeats: the same stems as the used questions (a past-exam entry twice, a variant that kept the stem)
    db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
    for qid, stem in zip(twin_ids, used_stems * 3):
        db.execute("UPDATE reviewed_questions SET content=jsonb_set(content,'{stem}',to_jsonb(%s::text)) WHERE id=%s", (stem, qid))
    db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")
    with pytest.raises(q.BankShortage):
        q.create_paper(db, uid, blueprint, today_cn())
    fresh = add_bank(db, blueprint, question_factory, passing_audit)
    second = q.create_paper(db, uid, blueprint, today_cn())
    assert not set(paper_ids(db, second)) & set(twin_ids) and set(paper_ids(db, second)) <= set(fresh)


def test_a_practice_set_uses_up_its_questions_too(db, uid, bank, blueprint, question_factory, passing_audit):
    second_bank = add_bank(db, blueprint, question_factory, passing_audit)
    taken = second_bank[:5]
    db.execute("INSERT INTO practice_sets(user_id,mode,question_ids) VALUES (%s,'weak',%s)", (uid, taken))
    paper = q.create_paper(db, uid, blueprint, today_cn())
    assert not set(paper_ids(db, paper)) & set(taken)


def test_no_review_card_puts_a_mistake_on_a_daily_paper(db, uid, bank, blueprint, question_factory, passing_audit):
    from app.memory import Card  # noqa: F401  (card shape)
    second_bank = add_bank(db, blueprint, question_factory, passing_audit)
    for qid in second_bank[:10]:                                                        # due cards for questions of the next bank
        db.execute("INSERT INTO fsrs_cards(user_id,question_id,card,due) VALUES (%s,%s,'{}',clock_timestamp()-interval '1 day')", (uid, qid))
    paper = q.create_paper(db, uid, blueprint, today_cn())
    rows = db.execute("SELECT is_retest,is_reused FROM paper_items_v2 WHERE paper_id=%s", (paper["paper_id"],)).fetchall()
    assert rows and not any(r["is_retest"] or r["is_reused"] for r in rows)
    assert not paper["includes_reused"] and all(not x["is_retest"] for x in paper["questions"])


def test_stock_never_counts_a_used_question_again(db, uid, bank, blueprint, question_factory, passing_audit):
    q.create_paper(db, uid, blueprint, today_cn() - timedelta(days=200))                 # the first bank was used 200 days ago
    fresh = add_bank(db, blueprint, question_factory, passing_audit)
    for qid in bank + fresh:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    # the used bank is not stock (it never returns, however long ago it was used); the unused one is
    assert cost_control.bucket_supply(db, "biochemistry", "single_choice") == 3
    assert "practice_sets" in gen.UNUSED_SQL and "14" not in gen.UNUSED_SQL
