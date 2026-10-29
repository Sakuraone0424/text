"""Conservative exam-authorship gate for newly AI-generated candidates.

Deterministic checks can reject structurally poor questions, but CANNOT prove
scientific truth, textbook support, or that only one option is correct.
Independent evidence-bound science/answer review remains mandatory.
"""

import re

TYPE_GUIDANCE = {
    "single_choice": (
        "Write one precise, scientifically constrained stem with exactly four plausible "
        "A-D alternatives of parallel grammatical form. Distractors should represent "
        "a specific misconception, not implausible filler; vary mechanisms, conditions "
        "or closely related terms. Only ONE alternative can be correct under the stated "
        "context; check each distractor against evidence. Do not use 'all of the above', "
        "'none of the above', deliberately ambiguous negations or answer-letter hints. "
        "Return canonical answer as A/B/C/D; rubric must justify the unique correct claim."
    ),
    "fill_blank": (
        "Place an explicit ____ at the exact missing concept; test a key term, causal "
        "step, molecule, enzyme, localization, condition or mechanism, not a trivial word. "
        "For multiple blanks independently allocate points, include accepted synonyms "
        "and exact scientific nomenclature (case, units, acronyms) where genuinely "
        "equivalent; never let the stem disclose the answer. No artificial multi-blank "
        "padding. Canonical answer must be included in accepted_answers."
    ),
    "term": (
        "Ask for a genuine named biochemical/cell-biological concept. Give a precise "
        "definition with essential molecular components, defining mechanism or function "
        "and boundaries distinguishing close concepts. Rubric must identify independent "
        "gradable content; do not award points just for repeating the term, verbosity "
        "or copying a neighboring sentence."
    ),
    "short_answer": (
        "Ask one focused mechanism/process/comparison with explicit scope, conditions "
        "and reasoning requirements. The model answer should state ordered causal "
        "steps, key molecular participants, regulation where relevant, and a justified "
        "result. Split rubric into independent point-bearing steps; avoid vague prompts "
        "such as 'discuss briefly' without identifying required content. Answer must "
        "be achievable from the supplied textbook excerpts alone."
    ),
    "essay": (
        "Only for historical v1 compatibility: require a bounded, multi-part mechanism "
        "or integrative comparison, explicit grading criteria and exact textual evidence. "
        "The new 60-question specification DOES NOT permit essays."
    ),
}

_COMMON = (
    "Produce a challenging NEW training question grounded in the user-provided genuine "
    "USTC past-exam question corpus for exam style, while keeping new questions "
    "distinguishable from copied past questions. Scientific claims and ALL scoring criteria "
    "must be recoverable from provided textbook evidence units; no outside facts, "
    "fabricated bibliographic authors or page numbers. The OCR locator identifies "
    "an OCR unit, not necessarily a printed page. Use verified past-paper source "
    "only as a STYLE example, not as evidence for scientific correctness. "
    "No OCR artifacts, vague pronoun referents, wrong taxonomy, missing figures, "
    "circular definitions, answer leakage, or identical distractors. "
    "Return one complete Question JSON object with exact evidence identities, "
    "knowledge_tags and a criterion-by-criterion rubric. "
    "When evidence is insufficient, decline generation rather than invent an answer. "
    "Independent science and answer reviews will still be required."
)

_UNANSWERABLE = re.compile(
    r"待补充|答案待定|暂无答案|无法核实|TODO|PLACEHOLDER|示例题目|测试题目",
    re.IGNORECASE,
)
_MISSING_ASSET = re.compile(r"如图所示|见下图|根据下图|见图\s*[0-9一二三四五六]*")
_UNHELPFUL_CHOICE = re.compile(
    r"^(以上(都|均|全部)|上述(都|均)|都(正确|不正确)|均(正确|错误)|"
    r"全部(正确|错误)|都不是|以上都不是|无法判断|信息不足)$"
)


def instructions(kind, points):
    if kind not in TYPE_GUIDANCE:
        raise ValueError("unsupported_generation_type")
    if type(points) is not int or points <= 0:
        raise ValueError("invalid_generation_points")
    return _COMMON + " " + TYPE_GUIDANCE[kind] + (
        f" This question has EXACTLY {points} points; rubric criterion points "
        "must add up to that amount."
    )


def issues(question):
    """Quality flags for new draft candidates; scientific checks are separate."""
    q = question.model_dump() if hasattr(question, "model_dump") else question
    kind = q.get("question_type")
    stem = q.get("stem", "")
    answer = q.get("answer", "")
    rubric = q.get("rubric") or []
    problems = []
    if kind not in TYPE_GUIDANCE:
        problems.append("unknown_question_type")
    if _UNANSWERABLE.search(stem + " " + answer):
        problems.append("unresolved_placeholder")
    if _MISSING_ASSET.search(stem):
        problems.append("unavailable_figure_reference")
    if kind == "single_choice":
        if any(_UNHELPFUL_CHOICE.fullmatch(str(value).strip()) for value in q.get("options", {}).values()):
            problems.append("unhelpful_all_or_none_distractor")
        if len(rubric) == 0:
            problems.append("missing_unique_answer_rationale")
    elif kind == "fill_blank":
        if not q.get("blanks") and not q.get("accepted_answers"):
            problems.append("missing_accepted_answer_set")
    elif kind in ("term", "short_answer", "essay"):
        min_len = 16 if kind == "term" else 32
        if len(re.sub(r"\s+", "", answer)) < min_len:
            problems.append("model_answer_too_terse_for_subjective_marking")
        if len(rubric) < 2:
            problems.append("insufficient_independent_scoring_criteria")
    return problems
