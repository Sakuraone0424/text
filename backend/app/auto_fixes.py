"""Format slips that a program can correct without changing what the question says or asks.

Every correction is recorded by name, the corrected question goes through every check and the independent
review exactly as before, and the model's original answer stays untouched in its job receipt. Nothing here
edits the stem's meaning, the answer's content, the rubric's wording, the evidence or the scientific claims.
"""

import re
import unicodedata

DIFFICULTIES = ("core", "mechanism", "application")
_DIFFICULTY_WORDS = {
    "easy": "core", "basic": "core", "simple": "core", "foundation": "core", "fundamental": "core", "recall": "core",
    "基础": "core", "简单": "core", "容易": "core", "低": "core", "基本": "core", "识记": "core", "记忆": "core",
    "medium": "mechanism", "moderate": "mechanism", "intermediate": "mechanism", "mechanistic": "mechanism",
    "中等": "mechanism", "中": "mechanism", "机制": "mechanism", "机理": "mechanism", "理解": "mechanism",
    "hard": "application", "difficult": "application", "advanced": "application", "applied": "application", "complex": "application",
    "难": "application", "困难": "application", "高": "application", "综合": "application", "应用": "application",
}
FIX_LABELS = {
    "json_extracted": "从代码块或说明文字中提取出 JSON",
    "numbers_to_int": "数值字符串转成整数",
    "removed_accepted_answers": "删除了非填空题多余的可接受答案",
    "removed_options": "删除了非单选题多余的选项",
    "empty_accepted_answers_removed": "删除了可接受答案里的空字符串",
    "choice_keys_normalized": "选项键统一成 A–D",
    "choice_prefix_removed": "去掉了选项内容开头的「A. 」前缀",
    "answer_letter_normalized": "答案统一成选项字母",
    "blank_marker_unified": "填空的空格写法统一成 ____",
    "fill_answer_added_to_accepted": "标准答案补进了可接受答案",
    "rubric_tags_merged": "评分要点的知识标签并入了题目标签",
    "difficulty_mapped": "难度改成了规定的三档之一",
    "max_points_set_to_spec": "满分改成了规格分值（评分要点合计正好等于它）",
}
_LETTER = re.compile(r"^\s*(?:选|答案\s*[:：]?|应选|正确答案\s*[:：]?)?\s*[(（]?([A-Da-d])[)）]?\s*[.、．:：]?\s*$")
_PREFIX = re.compile(r"^\s*[(（]?([A-Da-d])[)）]?\s*[.、．:：)）]\s*")
_BLANK = re.compile(r"[_＿]{2,}|[（(]\s*[_＿]*\s*[)）]")


def _is_int_text(value):
    return isinstance(value, str) and re.fullmatch(r"\s*\d{1,4}\s*", value) is not None


def _norm(text):
    return unicodedata.normalize("NFKC", text).strip()


def autofix(raw, spec_points=None, *, evidence_has_rubric_tags=True):
    """(corrected copy, [fix names]). Anything that is not shaped like a question is returned unchanged."""
    if not isinstance(raw, dict):
        return raw, []
    q = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
    fixes = []

    def note(name):
        if name not in fixes:
            fixes.append(name)

    qtype = q.get("question_type")

    # 4. numbers written as strings
    for holder, key in [(q, "max_points")] + [(c, "points") for c in q.get("rubric", []) if isinstance(c, dict)] + [
        (b, "points") for b in q.get("blanks", []) if isinstance(b, dict)
    ]:
        if _is_int_text(holder.get(key)):
            holder[key] = int(holder[key])
            note("numbers_to_int")
    if isinstance(q.get("style_reference_ids"), list) and any(_is_int_text(x) for x in q["style_reference_ids"]):
        q["style_reference_ids"] = [int(x) if _is_int_text(x) else x for x in q["style_reference_ids"]]
        note("numbers_to_int")

    # 5. fields that do not belong to this type
    if qtype != "fill_blank" and q.get("accepted_answers"):
        q["accepted_answers"] = []
        note("removed_accepted_answers")
    if qtype != "single_choice" and q.get("options"):
        q["options"] = {}
        note("removed_options")

    # 6. empty strings in the accepted answers (keep at least one real value)
    def drop_empty(values):
        if isinstance(values, list) and values and all(isinstance(v, str) for v in values):
            kept = [v for v in values if v.strip()]
            if kept and len(kept) != len(values):
                return kept, True
        return values, False

    q["accepted_answers"], changed = drop_empty(q.get("accepted_answers", []))
    if changed:
        note("empty_accepted_answers_removed")
    for blank in q.get("blanks", []):
        if isinstance(blank, dict):
            blank["accepted_answers"], changed = drop_empty(blank.get("accepted_answers"))
            if changed:
                note("empty_accepted_answers_removed")

    # 3. single choice: keys, prefixes, answer letter
    options = q.get("options")
    if qtype == "single_choice" and isinstance(options, dict) and options and all(isinstance(v, str) for v in options.values()):
        cleaned, ok = {}, True
        for key, value in options.items():
            letter = re.sub(r"[^A-Za-z]", "", str(key)).upper()
            if letter not in ("A", "B", "C", "D") or letter in cleaned:
                ok = False
                break
            cleaned[letter] = value
        if ok and set(cleaned) == set("ABCD"):
            if {str(k) for k in options} != set(cleaned):
                note("choice_keys_normalized")
            for letter, value in list(cleaned.items()):
                m = _PREFIX.match(value)
                if m and m.group(1).upper() == letter and value[m.end():].strip():
                    cleaned[letter] = value[m.end():].strip()
                    note("choice_prefix_removed")
            q["options"] = cleaned
            answer = q.get("answer")
            if isinstance(answer, str) and answer not in cleaned:
                m = _LETTER.match(answer)
                letter = m.group(1).upper() if m else None
                if letter is None:
                    hits = [k for k, v in cleaned.items() if _norm(v) == _norm(answer)]
                    letter = hits[0] if len(hits) == 1 else None
                if letter in cleaned:
                    q["answer"] = letter
                    note("answer_letter_normalized")

    # 2. one blank written many ways -> ____
    stem = q.get("stem")
    if qtype == "fill_blank" and isinstance(stem, str) and not q.get("blanks"):
        found = list(_BLANK.finditer(stem))
        if len(found) == 1 and found[0].group(0) != "____":
            m = found[0]
            q["stem"] = stem[: m.start()] + "____" + stem[m.end():]
            note("blank_marker_unified")

    # 1. the canonical answer must be one of the accepted answers
    answer, accepted = q.get("answer"), q.get("accepted_answers")
    if qtype == "fill_blank" and not q.get("blanks") and isinstance(answer, str) and answer.strip() and isinstance(accepted, list) \
            and all(isinstance(v, str) for v in accepted):
        if _norm(answer) not in {_norm(v) for v in accepted}:
            q["accepted_answers"] = accepted + [answer.strip()]
            note("fill_answer_added_to_accepted")

    # 7. rubric tags that the question does not list are added to the question's tags
    tags, rubric = q.get("knowledge_tags"), q.get("rubric")
    if isinstance(tags, list) and all(isinstance(t, str) for t in tags) and isinstance(rubric, list):
        extra = []
        for item in rubric:
            if isinstance(item, dict) and isinstance(item.get("knowledge_tags"), list):
                for tag in item["knowledge_tags"]:
                    if isinstance(tag, str) and tag not in tags and tag not in extra:
                        extra.append(tag)
        if extra and len(tags) + len(extra) <= 30:
            q["knowledge_tags"] = tags + extra
            note("rubric_tags_merged")

    # 8. difficulty
    difficulty = q.get("difficulty")
    if difficulty is not None and difficulty not in DIFFICULTIES:
        mapped = _DIFFICULTY_WORDS.get(str(difficulty).strip().lower())
        q["difficulty"] = mapped or "core"
        note("difficulty_mapped")

    # 9. max_points written wrongly, but the rubric adds up to the specification's value
    if spec_points is not None and isinstance(rubric, list) and q.get("max_points") != spec_points:
        points = [c.get("points") for c in rubric if isinstance(c, dict)]
        if points and all(type(x) is int for x in points) and sum(points) == spec_points:
            q["max_points"] = spec_points
            note("max_points_set_to_spec")
    return q, fixes
