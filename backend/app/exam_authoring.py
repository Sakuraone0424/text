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
        "context. Do not use 'all of the above', 'none of the above', deliberately "
        "ambiguous negations or answer-letter hints. Return canonical answer as A/B/C/D; "
        "rubric must justify the unique correct claim. Return the wrapper object described "
        "by schema: {question: <Question>, distractor_checks: {<each of the three wrong "
        "letters>: {unit_id, excerpt, why_wrong}}}. For EVERY wrong alternative quote, "
        "verbatim, the continuous textbook excerpt from a supplied unit that shows it is "
        "wrong (for a half-true statement, the exact point where it fails) and explain in "
        "why_wrong. EVERY wrong alternative MUST have grounds for refuting it that can be "
        "found in the supplied paragraphs: before you answer, check each of the three wrong "
        "alternatives against the supplied units and keep only those whose refutation you can "
        "quote verbatim. If a distractor cannot be refuted from the supplied units, replace it. "
        "Make certain that no second alternative can also be correct."
    ),
    "fill_blank": (
        "Place an explicit ____ at the exact missing concept; test a key term, causal "
        "step, molecule, enzyme, localization, condition or mechanism, not a trivial word. "
        "For multiple blanks independently allocate points, include accepted synonyms "
        "and exact scientific nomenclature (case, units, acronyms) where genuinely "
        "equivalent; never let the stem disclose the answer. No artificial multi-blank "
        "padding. Canonical answer must be included in accepted_answers. A single-blank question has EXACTLY ONE "
        "rubric item: the answer to that blank, worth the question's whole point value; the rubric must never ask the "
        "student to explain a principle, give a reason or answer a second question."
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
        "A 问答/论述 question, larger than a short answer: ask for a bounded multi-part "
        "mechanism, an integrative comparison or a process with its regulation, with the "
        "parts stated explicitly in the question. The model answer is structured by those "
        "parts and every part is answerable from the supplied textbook excerpts alone. "
        "Give EXACTLY four to six independent rubric items, one per required part or step, "
        "each with its own points; no item may only reward length or restating the question."
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
    "Write every scoring criterion ONLY from what the supplied paragraphs say: no knowledge from outside them. "
    "A criterion may summarise or reword a paragraph (it need not be word for word) but must be supported in meaning "
    "by it. If a paragraph is cut off (for example it ends in the middle of a list such as '④ 受体鸟'), do not write "
    "the cut-off part into any scoring criterion. "
    "STYLE: write a graduate-entrance (考研) knowledge question in the manner of the genuine past exams. It tests the biological "
    "knowledge ITSELF. NEVER write 'according to the textbook / this chapter / this section / the literature / the information provided', "
    "never refer to a figure or table ('图 X', '表 X', '下图'), never ask which statement is 'consistent with the textbook', and never "
    "ask about authors, years or titles of cited papers. Options compare biological facts (mechanisms, structures, functions, "
    "conditions), never the wording of the textbook. The stem must be answerable by someone who knows the subject, without "
    "having read the supplied paragraph. A fill-in's answer must not appear anywhere in its stem. "
    "When evidence is insufficient, decline generation rather than invent an answer. "
    "Independent science and answer reviews will still be required."
)


# These are Pydantic model invariants that a JSON schema alone cannot express.
# The generator must satisfy them, not the dispatcher silently repair them.
_SCHEMA_CONTRACT = (
    " STRICT Question validator requirements: return a single unwrapped JSON "
    "object with the exact Question schema keys and no additional properties "
    "(for single_choice this Question object is the `question` member of the "
    "wrapper described for that type). "
    "Copy subject, question_type and max_points EXACTLY from the input payload. "
    "The rubric must be a nonempty list of criterion objects with integer points "
    "whose sum is exactly max_points, not an example or an estimate. All criterion "
    "knowledge_tags must be subsets of the top-level knowledge_tags; use [] for "
    "both when reliable tagging is unavailable. Each evidence object MUST have "
    "textbook, authors, edition, chapter, locator, excerpt and exact provided "
    "file_id, source_sha256, ocr_page, paragraph_id, source_unit_id; excerpt must "
    "be a verbatim continuous substring of that same supplied evidence.text. "
    "Use the supplied source_bibliography for textbook, authors and edition; "
    "do not guess ISBN or printed pages. Set isbn to null when not supplied. "
    "For single_choice use options with EXACTLY A,B,C,D unique nonempty values, "
    "answer exactly one of A/B/C/D, accepted_answers=[], blanks=[], and rubric "
    "points summing to max_points. For fill_blank use options={}, stem containing exactly "
    "one literal ____ placeholder, canonical answer included verbatim in a "
    "nonempty accepted_answers list (accepted_answers MUST contain the exact text of the answer field itself, "
    "plus any equally correct variants), blanks=[], and rubric points summing to max_points. "
    "For term use options={}, accepted_answers=[], blanks=[], a complete answer "
    "of meaningful length, and at least two independently gradable rubric items "
    "with points summing to max_points. For short_answer and essay use the same empty options, "
    "accepted_answers and blanks, and at least two independent rubric items "
    "with points summing to max_points (an essay has four to six). Every points value must be a JSON integer, not "
    "a string or decimal. Do not write comments, markdown, or wrapper keys. "
    "If evidence does not support the question, mark uncertainty by declining "
    "rather than fabricate missing claims; a declined response is rejected. "
)

# Reading-comprehension wording. A past exam asks about the biology itself; it never says "according to the textbook / this
# chapter / the literature" or asks which statement agrees with the textbook.
_READING_STYLE = re.compile(
    r"根据(?:教材|本章|本节|本书|本文|所给|所提供|上述|下述|以下)|教材(?:中|上|里)?(?:所述|表述|描述|内容|的说法|提到)|"
    r"与(?:教材|文献|原文|上述|本章)[^，。？；]{0,8}(?:一致|相符|相同|不符|吻合)|本章|本节|"
    r"文献(?:信息|资料|中|所)|所提供的(?:文献|材料|信息)|研究(?:了|的是)[^，。]{0,30}[（(]\s*(?:19|20)\d{2}\s*[)）]|"
    r"[A-Z][a-zA-Zö]+\s*等\s*[（(]\s*(?:19|20)\d{2}|表\s*\d+\s*[-－.]?\s*\d*"
)

_UNANSWERABLE = re.compile(
    r"待补充|答案待定|暂无答案|无法核实|TODO|PLACEHOLDER|示例题目|测试题目",
    re.IGNORECASE,
)
_MISSING_ASSET = re.compile(
    r"如图所示|见下图|根据下图|见图\s*[0-9一二三四五六]*|[（(]\s*图\s*[0-9一二三四五六七八九十]|图\s*\d+\s*[-－–]\s*\d+|下图|上图"
)
_UNHELPFUL_CHOICE = re.compile(
    r"^(以上(都|均|全部)|上述(都|均)|都(正确|不正确)|均(正确|错误)|"
    r"全部(正确|错误)|都不是|以上都不是|无法判断|信息不足)$"
)


def instructions(kind, points):
    if kind not in TYPE_GUIDANCE:
        raise ValueError("unsupported_generation_type")
    if type(points) is not int or points <= 0:
        raise ValueError("invalid_generation_points")
    return _COMMON + " " + _SCHEMA_CONTRACT + " " + TYPE_GUIDANCE[kind] + (
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
    if _MISSING_ASSET.search(stem + " " + " ".join(str(v) for v in (q.get("options") or {}).values())):
        problems.append("unavailable_figure_reference")
    if _READING_STYLE.search(stem + " " + " ".join(str(v) for v in (q.get("options") or {}).values())):
        problems.append("textbook_wording_in_stem")
    if kind == "single_choice":
        if any(_UNHELPFUL_CHOICE.fullmatch(str(value).strip()) for value in q.get("options", {}).values()):
            problems.append("unhelpful_all_or_none_distractor")
        if len(rubric) == 0:
            problems.append("missing_unique_answer_rationale")
    elif kind == "fill_blank":
        if not q.get("blanks") and not q.get("accepted_answers"):
            problems.append("missing_accepted_answer_set")
        # The answer written in the question itself is no question: 「位于动粒外侧……动粒的____」 answered 「外」.
        shown = stem.replace("____", "")
        given = {str(a).strip() for a in [answer, *(q.get("accepted_answers") or [])] if str(a).strip()}
        if any(a in shown and not re.fullmatch(r"[A-Za-z0-9]", a) for a in given):
            problems.append("answer_disclosed_in_stem")
    elif kind in ("term", "short_answer", "essay"):
        min_len = 16 if kind == "term" else 32
        if len(re.sub(r"\s+", "", answer)) < min_len:
            problems.append("model_answer_too_terse_for_subjective_marking")
        if len(rubric) < 2:
            problems.append("insufficient_independent_scoring_criteria")
    return problems
