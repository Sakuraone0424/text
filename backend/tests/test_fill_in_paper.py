"""The all-fill-in objective paper and fill-ins with up to three blanks: scoring, grading, classification, consistency, import."""
from fractions import Fraction

import pytest

from app import auto_fixes, memory, training_sessions as sessions, training_spec_v5 as v5
from app.past_exam_import import PastExamAnswer, _classify
from app.question_quality import Question


def fill(blanks, *, points=2.5, stem=None, rubric=None, **extra):
    n = len(blanks)
    share = points / n if n else points
    return dict(
        subject="biochemistry", question_type="fill_blank", max_points=points,
        stem=stem or " 与 ".join(["____"] * n) + " 是糖酵解的关键产物" if n > 1 else "糖酵解的终产物是____",
        options={}, answer="；".join(b[0] for b in blanks) if n > 1 else "丙酮酸",
        accepted_answers=[] if n > 1 else ["丙酮酸"],
        blanks=[dict(accepted_answers=b, points=share) for b in blanks] if n > 1 else [],
        evidence=[dict(textbook="生物化学", authors="作者", edition="第四版", chapter="第五章", locator="p1", excerpt="糖酵解产生丙酮酸")],
        rubric=rubric or [dict(description=f"第{i + 1}空", points=share, knowledge_tags=[]) for i in range(n if n > 1 else 1)],
        knowledge_tags=[], difficulty="core", **extra,
    )


# ---- the specification -----------------------------------------------------------------------------------------------

def test_the_objective_paper_is_sixty_two_and_a_half_point_fill_ins():
    spec = v5.OBJECTIVE
    assert spec.SPECIFICATION == "xiaohan-obj-fill-150-v6" and spec.TYPES == ("fill_blank",)
    assert spec.COUNTS == {"fill_blank": 30} and spec.POINTS["fill_blank"] == 2.5
    assert spec.TOTAL_QUESTIONS == 60 and spec.TOTAL_POINTS == 150 and spec.POINTS_PER_SUBJECT == 75 and spec.DURATION_SECONDS == 3600
    assert v5.OBJECTIVE_V5.RETIRED and v5.KIND_OF[v5.OBJECTIVE_V5.SPECIFICATION] == "objective_old"   # the earlier paper is read-only
    assert v5.RESERVE["fill_blank"] == 60 and v5.RESERVE["term"] == 8 and v5.COUNTS["term"] == 4


# ---- equal split, exact totals -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("blanks", [1, 2, 3])
@pytest.mark.parametrize("correct", [0, 1, 2, 3])
def test_a_blank_pays_an_equal_share_and_the_total_is_exact(blanks, correct):
    if correct > blanks:
        return
    got = v5.blanks_total(2.5, correct, blanks)
    assert got == float(Fraction(5, 2) * correct / blanks)
    assert v5.blanks_total(2.5, blanks, blanks) == 2.5                       # all blanks right: exactly the question's points


def test_sixty_questions_of_three_blanks_still_add_up_to_exactly_150():
    total = sum(v5.blanks_total(2.5, 3, 3) for _ in range(60))
    assert total == 150.0
    assert sum(v5.blanks_total(2.5, 2, 3) + v5.blanks_total(2.5, 1, 3) for _ in range(20)) == pytest.approx(20 * 2.5)   # 2/3 + 1/3 of each


def test_every_blank_is_worth_the_same_whatever_its_count():
    assert v5.blank_points(2.5, 2) == 1.25 and v5.blank_points(2.5, 3) == pytest.approx(2.5 / 3)


# ---- grading one blank at a time -------------------------------------------------------------------------------------------

def test_each_blank_is_graded_against_its_own_accepted_answers():
    q = fill([["丙酮酸", "pyruvate"], ["ATP"], ["NADH", "还原型辅酶I"]])
    assert q["blanks"][0]["points"] == pytest.approx(2.5 / 3)
    got = sessions.grade_blanks(q, ["pyruvate", "ADP", "NADH"])
    assert got["right"] == [True, False, True]
    assert got["points"] == pytest.approx(2.5 * 2 / 3)
    assert got["blank_points"] == [pytest.approx(2.5 / 3), 0, pytest.approx(2.5 / 3)]
    assert sessions.grade_blanks(q, ["丙酮酸", "ATP", "NADH"])["points"] == 2.5     # exactly the full score
    assert sessions.grade_blanks(q, ["", "", ""])["points"] == 0
    assert sessions.grade_blanks(q, ["丙酮酸"])["right"] == [True, False, False]       # a blank not supplied is wrong, never an error


# ---- right / to consolidate / mistake ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("blanks,right,expected", [
    (2, 2, "mastered"), (2, 1, "consolidate"), (2, 0, "mistake"),
    (3, 3, "mastered"), (3, 2, "consolidate"), (3, 1, "mistake"), (3, 0, "mistake"),
    (1, 1, "mastered"), (1, 0, "mistake"),
])
def test_classification_of_a_fill_in_by_its_blanks(blanks, right, expected):
    points = v5.blanks_total(2.5, right, blanks)
    assert memory.classify("fill_blank", points, 2.5) == expected


# ---- the question's own consistency ---------------------------------------------------------------------------------------------

def test_stem_blanks_rubric_items_and_answers_must_agree():
    ok = fill([["a"], ["b"]])
    Question.model_validate(ok)
    wrong_stem = dict(ok, stem="只有一个空 ____")
    with pytest.raises(ValueError):
        Question.model_validate(wrong_stem)
    one_rubric = dict(ok, rubric=[dict(description="合并", points=2.5, knowledge_tags=[])])
    with pytest.raises(ValueError, match="own scoring point"):
        Question.model_validate(one_rubric)
    four = fill([["a"], ["b"], ["c"], ["d"]])
    with pytest.raises(ValueError):
        Question.model_validate(four)                                              # at most three blanks


def test_autofix_puts_a_models_rounded_blank_points_right_and_repairs_nothing_else():
    q = fill([["a"], ["b"], ["c"]])
    for b in q["blanks"]:
        b["points"] = 0.83
    for c in q["rubric"]:
        c["points"] = 0.83
    fixed, names = auto_fixes.autofix(q, spec_points=2.5)
    assert "blank_points_equalised" in names
    assert all(b["points"] == pytest.approx(2.5 / 3) for b in fixed["blanks"]) and all(c["points"] == pytest.approx(2.5 / 3) for c in fixed["rubric"])
    Question.model_validate(fixed)
    # a stem with two blanks but three listed blanks is not guessed at: it goes to the repair
    broken = fill([["a"], ["b"], ["c"]], stem="只有 ____ 和 ____ 两个空")
    fixed, names = auto_fixes.autofix(broken, spec_points=2.5)
    assert "blank_points_equalised" not in names
    with pytest.raises(ValueError):
        Question.model_validate(fixed)


# ---- past-exam fill-ins ---------------------------------------------------------------------------------------------------------

def row(**kw):
    return dict(year=2023, question_number="3", concerns=[], question_type="fill_blank", **kw)


def test_a_real_two_or_three_blank_fill_in_is_imported_whole_and_more_than_three_are_still_split():
    for stem, n in (("糖酵解的产物是____和____。", 2), ("三羧酸循环产生____、____和____。", 3)):
        reason, out, options, normalized, extra = _classify(row(), stem)
        assert reason is None and extra.get("multi_blank") == n and out.count("____") == n and "split" not in extra
    four = "糖酵解的第一步由____催化，第二步由____催化，第三步由____催化，第四步由____催化。"
    reason, out, _, _, extra = _classify(row(), four, answers={"3": "己糖激酶；磷酸果糖激酶；醛缩酶；丙糖磷酸异构酶"})
    assert extra.get("split") and len(extra["split"]) == 4 or reason          # more than three: split into single blanks (or skipped)
    assert "multi_blank" not in extra


def test_the_answer_schema_carries_one_accepted_list_per_blank():
    parsed = PastExamAnswer.model_validate(dict(supported=True, blanks=[["丙酮酸"], ["ATP", "三磷酸腺苷"]], answer="丙酮酸；ATP", rubric=[]))
    assert parsed.blanks == [["丙酮酸"], ["ATP", "三磷酸腺苷"]]


# ---- the review shows every blank ---------------------------------------------------------------------------------------------------

def test_the_review_lists_each_blank_with_what_it_earned():
    from app.mistake_book import blank_rows

    q = fill([["丙酮酸"], ["ATP", "三磷酸腺苷"], ["NADH"]])
    rows = blank_rows(q, '{"blanks": ["丙酮酸", "ADP", ""]}', {"blank_points": [2.5 / 3, 0, 0]})
    assert [(r["index"], r["yours"], r["correct"]) for r in rows] == [(1, "丙酮酸", "丙酮酸"), (2, "ADP", "ATP"), (3, "", "NADH")]
    assert rows[0]["points"] == pytest.approx(2.5 / 3) and rows[1]["points"] == 0 and rows[0]["max_points"] == pytest.approx(2.5 / 3)
    assert rows[1]["accepted"] == ["ATP", "三磷酸腺苷"]
    assert blank_rows(fill([["a"]]), '{"text": "a"}', {}) is None           # a one-blank question has no blank table
