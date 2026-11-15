"""Paper rules: one chapter at most 25% of a subject, difficulty mix about 3:5:2, same concept once; relaxed and said when the bank is short."""
import random
from collections import Counter

import pytest

from app import selection

QUOTAS = {"core": 9, "mechanism": 15, "application": 6}


def row(i, chapter, difficulty="core", kind="single_choice"):
    return {"id": i, "content": {"subject": "biochemistry", "question_type": kind, "difficulty": difficulty,
                                 "evidence": [{"chapter": chapter}], "stem": f"s{i}", "knowledge_tags": []}}


class Policy:
    coverage_strength = 0.0           # no soft chapter spreading: only the hard rules are under test
    chapter_share = 1.0
    exploration = 1.0
    type_adjustment = 0.0
    cognitive_adjustment = 0.0


@pytest.fixture(autouse=True)
def flat_scores(monkeypatch):
    monkeypatch.setattr(selection, "candidate_score", lambda q, context, policy: 1.0)


def choose(pool, count, chapters=None, diff=None, relaxed=None, seed="a", total=30):
    return selection.pick(list(pool), count, chapters if chapters is not None else Counter(), {}, Policy(), seed, "biochemistry", "single_choice",
                          [], None, difficulty_counts=diff if diff is not None else Counter(), relaxed=relaxed if relaxed is not None else set(),
                          subject_total=total)


def test_quotas_are_3_5_2_of_the_subjects_thirty():
    assert selection.difficulty_quotas(30) == QUOTAS and sum(selection.difficulty_quotas(7).values()) == 7
    assert selection.chapter_cap(30) == 7


def test_one_chapter_never_exceeds_a_quarter_of_the_subject_when_other_chapters_exist():
    pool = [row(i, "第一章") for i in range(40)] + [row(100 + i, f"第{i}章") for i in range(2, 30)]
    for seed in "abcdefgh":
        chapters, relaxed = Counter(), set()
        out = choose(pool, 30, chapters, relaxed=relaxed, seed=seed)
        assert chapters[("biochemistry", "第一章")] <= 7 and len(out) == 30 and not any(r.startswith("chapter") for r in relaxed)


def test_the_difficulty_mix_follows_3_5_2_when_the_bank_has_enough_of_each():
    pool = [row(i, f"c{i % 10}", ["core", "mechanism", "application"][i % 3]) for i in range(150)]
    diff, relaxed = Counter(), set()
    choose(pool, 30, diff=diff, relaxed=relaxed)
    got = {k: diff[("biochemistry", k)] for k in QUOTAS}
    assert got == QUOTAS and not relaxed


def test_the_rules_are_relaxed_and_reported_when_the_bank_cannot_keep_them():
    pool = [row(i, "第一章") for i in range(10)]                     # one chapter only, one difficulty only
    chapters, diff, relaxed = Counter(), Counter(), set()
    out = choose(pool, 10, chapters, diff, relaxed)
    assert len(out) == 10                                            # the paper is still made
    assert relaxed == {"chapter_cap:biochemistry", "difficulty_mix:biochemistry"}


def test_only_the_difficulty_mix_is_relaxed_when_chapters_are_fine():
    pool = [row(i, f"c{i}") for i in range(30)]                      # 30 chapters but every question is "core"
    relaxed = set()
    choose(pool, 30, relaxed=relaxed)
    assert relaxed == {"difficulty_mix:biochemistry"}


def test_a_paper_carries_the_note_and_the_page_shows_it(db, bank, blueprint):
    from pathlib import Path
    from app import quiz_v2
    from app.common import ensure_user

    paper = quiz_v2.create_paper(db, ensure_user(db, "rules-learner"), blueprint)
    assert paper["relaxed_rules"]            # the fixture bank has one chapter and one difficulty: both rules had to give
    assert set(paper["relaxed_rules"]) <= {f"{k}:{s}" for k in ("chapter_cap", "difficulty_mix") for s in ("biochemistry", "cell_biology")}
    js = (Path(selection.__file__).parent / "static/training.js").read_text()
    assert "组卷规则" in js and "relaxed_rules" in js
