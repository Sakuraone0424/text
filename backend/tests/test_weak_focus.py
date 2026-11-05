"""Weak chapters (< 60% correct, after at least 2 graded papers) are chosen twice as often; every chapter keeps a minimum."""
import random
from collections import Counter

import pytest

from app import auto_generation as gen, learning_stats, model_router
from test_ai_automation import setup_ai  # noqa: F401


def table(*rows):
    return [dict(subject="biochemistry", chapter=c, rate=r, answers=a) for c, r, a in rows]


@pytest.fixture
def results(monkeypatch):
    state = dict(papers=2, table=table(("第一章", 0.4, 6), ("第二章", 0.9, 6), ("第三章", 0.55, 2)))
    monkeypatch.setattr(learning_stats, "graded_rows", lambda conn, uid: [])
    monkeypatch.setattr(learning_stats, "practice_rows", lambda conn, uid: [])
    monkeypatch.setattr(learning_stats, "papers", lambda rows: [object()] * state["papers"])
    monkeypatch.setattr(learning_stats, "chapter_table", lambda rows, practice: state["table"])
    return state


def test_weak_chapters_need_two_graded_papers(db, setup_ai, results):
    results["papers"] = 1
    assert gen.weak_chapter_names(db, setup_ai, "biochemistry") == set()
    results["papers"] = 2
    assert gen.weak_chapter_names(db, setup_ai, "biochemistry") == {"第一章"}      # 第三章 has too few answers to say anything


def test_only_chapters_below_60_percent_of_that_subject_are_weak(db, setup_ai, results):
    results["table"] += [dict(subject="cell_biology", chapter="第九章", rate=0.1, answers=9), dict(subject="biochemistry", chapter="第四章", rate=0.6, answers=9)]
    assert gen.weak_chapter_names(db, setup_ai, "biochemistry") == {"第一章"}


def test_the_switch_in_the_settings_turns_it_off(db, setup_ai, results):
    assert model_router.settings(db, setup_ai)["weak_focus"] is True              # on by default
    model_router.configure(db, setup_ai, "deepseek-flash", "high", verify=False, weak_focus=False)
    assert gen.weak_chapter_names(db, setup_ai, "biochemistry") == set()
    model_router.configure(db, setup_ai, "deepseek-flash", "high", verify=False, weak_focus=True)
    assert gen.weak_chapter_names(db, setup_ai, "biochemistry") == {"第一章"}


def test_weak_chapters_count_double_and_the_others_keep_their_weight():
    weights = {"f|第一章": 1.0, "f|第二章": 1.0, "f|第三章": 1.0}
    out = gen.apply_weak_focus(weights, {"第一章"})
    assert out["f|第一章"] == 2.0 and out["f|第二章"] == 1.0 and out["f|第三章"] == 1.0
    assert gen.apply_weak_focus(weights, set()) == weights


def test_every_chapter_keeps_a_minimum_even_when_its_weight_has_fallen():
    weights = {"f|第一章": 8.0, "f|第二章": 0.001, "f|第三章": 1.0}
    out = gen.apply_weak_focus(weights, set())
    floor = gen.WEAK_FLOOR * sum(weights.values()) / 3
    assert out["f|第二章"] == pytest.approx(floor) and out["f|第二章"] > 0.5


def test_the_chosen_paragraphs_lean_toward_the_weak_chapter():
    by_chapter = {"f|第一章": [1], "f|第二章": [2], "f|第三章": [3]}
    weights = gen.apply_weak_focus({k: 1.0 for k in by_chapter}, {"第一章"})
    rng = random.Random(7)
    picks = Counter(gen.pick_paragraph(by_chapter, weights, {}, rng) for _ in range(4000))
    assert 1.7 < picks[1] / picks[2] < 2.4 and picks[2] > 800 and picks[3] > 800         # about 2 : 1 : 1, nobody starved


def test_the_model_page_saves_the_switch():
    from pathlib import Path
    from app import model_api

    root = Path(model_api.__file__).parent / "static"
    assert 'id="weak-focus"' in (root / "training.html").read_text() and "weak_focus" in (root / "model.js").read_text()
    assert "weak_focus" in model_api.SaveAll.model_fields
