"""Unverified lexical candidates linking real exam style to locked OCR chapters.

NO scientific inference: literal text overlap is a review queue, never a
textbook answer, validated fact, or proof that a topic was officially examined.
Never emit question wording, textbook paragraphs, solutions or local paths.
"""
import argparse
import re
import unicodedata
from collections import Counter, defaultdict

from app.collection_seed import LOCK, preflight
from app.source_policy import COLLECTION
from app.sources import prepare_source
from app.transcript_analysis import candidates, STYLE_SOURCE_BLOCKERS
from app.exam_style_readiness import BOUNDARY_ISSUES
from app.training_spec_v2 import SUBJECTS, COUNTS
from app.source_intake import private_json

# A report cannot call a source style-safe if the actual generator rejects it.
# Share the exact upstream risk classes rather than maintaining a stale copy.
REFUSE = STYLE_SOURCE_BLOCKERS | BOUNDARY_ISSUES
GENERIC = {"生物化学", "细胞生物学", "蛋白质", "核酸", "细胞", "生物", "基础",
           "第一章", "引言", "概论", "复习题", "习题", "研究方法", "概述"}


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def science_terms(heading):
    """Accept only explicit heading terms; do not invent synonyms or definitions."""
    if not isinstance(heading, str):
        return []
    heading = re.sub(r"^第[一二三四五六七八九十百零〇\d]+章\s*", "", heading)
    heading = re.sub(r"^\d+(?:\.\d+)*[.、\s]*", "", heading).strip()
    heading = re.sub(r"\s*\d{1,4}$", "", heading).strip()
    if len(heading) > 70:
        return []
    # A complete Chinese heading is more reliable than arbitrary character
    # n-grams. A label must have >=4 CJK characters to avoid near-universal
    # matches such as 酶, 代谢, 核酸 or RNA fragments.
    terms = []
    parts = re.split(r"[()（）:：,，;；/／、\[\]【】]", heading)
    for part in parts:
        part = part.strip().strip("·.-—")
        if not part or part in GENERIC:
            continue
        chinese = sum("\u4e00" <= c <= "\u9fff" for c in part)
        if chinese >= 4 and len(part) <= 42:
            terms.append(part)
        # Exact scientific acronyms, not broad English keywords.
        for acronym in re.findall(r"(?<![A-Za-z])[A-Z][A-Z0-9-]{2,15}(?![A-Za-z])", part):
            if acronym not in {"TEST", "ONLY"}:
                terms.append(acronym)
    return sorted(set(terms))


def report(directory, *, lock_path=LOCK):
    all_files = preflight(directory, lock_path=lock_path)
    by_key = {item["key"]: item for item in all_files}
    labels = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    unsafe_headings = Counter()
    for source in COLLECTION["files"]:
        if source["kind"] != "textbook":
            continue
        item = by_key[source["key"]]
        data = prepare_source(item["path"], item["metadata"])
        for unit in data["rows"]:
            chapter = unit.get("chapter")
            if not chapter or chapter in ("unclassified", "unknown"):
                continue
            for heading in (chapter, unit.get("section") or ""):
                for word in science_terms(heading):
                    flags = set(unit.get("quality_flags") or [])
                    if flags & {"corrupt_ocr", "untranscribed_image"}:
                        unsafe_headings[word] += 1
                        continue
                    labels[source["subject"]][chapter][word].add(
                        (source["key"], unit["ocr_global_page"])
                    )
    bucket = defaultdict(lambda: Counter())
    chapter_hits = defaultdict(lambda: Counter())
    for source in COLLECTION["files"]:
        if source["kind"] != "exam_transcription":
            continue
        item = by_key[source["key"]]
        data = prepare_source(item["path"], item["metadata"])
        rows = [dict(u, id=n) for n, u in enumerate(data["rows"], 1)]
        source_item = source
        for question in candidates(rows, source_item):
            subject = source["subject"]
            kind = question["question_type"]
            if (
                kind not in COUNTS or
                question["question_number"] is None
            ):
                continue
            key = (subject, kind)
            bucket[key]["observed_candidate_starts"] += 1
            risks = set(question["concerns"] or [])
            if question["year"] is None or risks & REFUSE:
                bucket[key]["blocked_before_topic_linkage"] += 1
                continue
            bucket[key]["parser_safe_style_starts"] += 1
            qtext = normalized(question["transcription"])
            matches = []
            for chapter, words in labels[subject].items():
                matched = [w for w in words if normalized(w) in qtext]
                if matched:
                    matches.append(chapter)
                    chapter_hits[(subject, kind)][chapter] += 1
            if matches:
                bucket[key]["lexically_linked_question_starts"] += 1
                bucket[key]["candidate_chapter_links"] += len(matches)
            else:
                bucket[key]["no_conservative_literal_topic_match"] += 1
    buckets = []
    for subject in SUBJECTS:
        for kind in COUNTS:
            k = (subject, kind)
            counts = bucket[k]
            buckets.append({
                "subject": subject,
                "question_type": kind,
                **{key: counts[key] for key in (
                    "observed_candidate_starts",
                    "blocked_before_topic_linkage",
                    "parser_safe_style_starts",
                    "lexically_linked_question_starts",
                    "candidate_chapter_links",
                    "no_conservative_literal_topic_match",
                )},
                "most_frequent_literal_heading_matches": [
                    {"chapter_heading": chapter[:180], "question_starts": count}
                    for chapter, count in chapter_hits[k].most_common(12)
                ],
                "scientifically_supported_matches": 0,
            })
    return {
        "collection_version": COLLECTION["version"],
        "scope": "literal_heading_overlap_candidate_only_not_scientific_evidence",
        "six_pinned_sources_checked": len(all_files) == 6,
        "independent_ai_science_review_performed": False,
        "independent_ai_answer_review_performed": False,
        "chapter_linkage_fully_verified": False,
        "question_answers_or_rubrics_generated": 0,
        "scientifically_supported_matches_claimed": 0,
        "unsafe_source_heading_occurrences_discarded": sum(unsafe_headings.values()),
        "candidate_terms_per_subject": {
            subject: len(set(w for words in labels[subject].values() for w in words))
            for subject in SUBJECTS
        },
        "buckets": buckets,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    private_json(args.report, report(args.directory))
    print("conservative_chapter_exam_lexical_candidates_ready_for_review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
