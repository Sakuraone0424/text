"""Pending-only, exact-literal links from original exam transcriptions to textbooks.

A shared technical term is a search hint, NOT a scientific relation, textbook
answer, authenticated exam item or approved review. No model API is imported.
Only six SHA-locked active sources are eligible; store provenance, not passages.
"""
from collections import Counter, defaultdict
from hashlib import sha256
import re

from app.source_policy import current_source_sql, pinned_source_bytes
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS
from app.exam_style_readiness import BOUNDARY_ISSUES

VERSION = "qa-exam-textbook-lexical/1"
# Search vocabulary only: each term must literally appear in BOTH cited units.
# Never infer synonyms, mechanisms, causal links or answer correctness.
TERMS = (
    "三羧酸循环", "糖异生", "糖酵解", "戊糖磷酸途径", "氧化磷酸化",
    "脂肪酸", "β-氧化", "柠檬酸合酶", "氨基酸", "核糖体", "核苷酸",
    "蛋白质折叠", "蛋白酶体", "泛素", "转录因子", "基因表达",
    "DNA复制", "DNA修复", "RNA聚合酶", "信号肽", "核定位信号",
    "内质网", "高尔基体", "溶酶体", "过氧化物酶体", "线粒体",
    "微管", "微丝", "细胞周期", "凋亡", "细胞骨架", "细胞膜",
    "胆固醇", "细胞连接", "减数分裂", "有丝分裂", "蛋白激酶",
    "SRP", "ATP合酶", "NADPH", "GTP酶",
)
# ASCII word boundaries stop e.g. SRP from matching a longer Latin identifier.
MATCHERS = {
    term: re.compile(
        (r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")
        if term[0].isascii() and term[0].isalpha()
        else re.escape(term),
        re.IGNORECASE,
    )
    for term in TERMS
}
BLOCKERS = set(STYLE_SOURCE_BLOCKERS) | set(BOUNDARY_ISSUES)

DDL = """
CREATE TABLE IF NOT EXISTS qa_exam_textbook_lexical_candidates (
    exam_unit_id bigint NOT NULL REFERENCES source_units(id),
    textbook_unit_id bigint NOT NULL REFERENCES source_units(id),
    literal_term text NOT NULL,
    algorithm text NOT NULL CHECK (algorithm='qa-exam-textbook-lexical/1'),
    subject text NOT NULL,
    exam_year integer NOT NULL CHECK (exam_year BETWEEN 1998 AND 2026),
    exam_source_sha256 text NOT NULL,
    textbook_source_sha256 text NOT NULL,
    exam_text_sha256 text NOT NULL,
    textbook_text_sha256 text NOT NULL,
    scientific_verified boolean NOT NULL DEFAULT false CHECK (scientific_verified=false),
    review_state text NOT NULL DEFAULT 'pending' CHECK (review_state='pending'),
    PRIMARY KEY (exam_unit_id,textbook_unit_id,literal_term,algorithm)
);
CREATE INDEX IF NOT EXISTS qa_exam_textbook_lexical_by_year
    ON qa_exam_textbook_lexical_candidates(subject,exam_year,literal_term);
"""


def terms_in(text):
    return {term for term, pattern in MATCHERS.items() if pattern.search(text or "")}


def build(books, exams, *, per_term_limit=3, per_exam_limit=12):
    """Bounded lexical candidate construction, never scientific endorsement."""
    if not (1 <= per_term_limit <= 10 and 1 <= per_exam_limit <= 40):
        raise ValueError("qa_lexical_link_bounds_invalid")
    by_term = defaultdict(list)
    for book in books:
        if not pinned_source_bytes(book["file_id"], book["source_sha256"]):
            raise ValueError("qa_unlocked_textbook")
        if book["quality_flags"] or book["ocr_page"] is None:
            continue
        for term in terms_in(book["text"]):
            by_term[(book["subject"], term)].append(book)
    # Prefer different chapters rather than three adjacent paragraphs.
    choices = {}
    for key, matches in by_term.items():
        seen = set()
        selected = []
        for unit in matches:
            if unit["chapter"] in ("unclassified", "unknown", "contents", ""):
                continue
            if unit["chapter"] in seen:
                continue
            seen.add(unit["chapter"])
            selected.append(unit)
            if len(selected) >= per_term_limit:
                break
        choices[key] = selected
    rows = []
    linked = set()
    eligible = set()
    blocked = Counter()
    for q in exams:
        if not pinned_source_bytes(q["file_id"], q["source_sha256"]):
            raise ValueError("qa_unlocked_exam_transcription")
        if q["question_number"] is None or q["year"] is None:
            blocked["unresolved_question_start_or_year"] += 1
            continue
        if q["quality_flags"] or set(q["concerns"] or []) & BLOCKERS:
            blocked["unverified_or_damaged_transcription"] += 1
            continue
        if not 1998 <= q["year"] <= 2026:
            blocked["unrecognized_exam_year"] += 1
            continue
        eligible.add(q["unit_id"])
        for term in sorted(terms_in(q["transcription"])):
            for b in choices.get((q["subject"], term), []):
                if len([r for r in rows[-per_exam_limit:] if r[0] == q["unit_id"]]) >= per_exam_limit:
                    break
                rows.append((
                    q["unit_id"], b["unit_id"], term, VERSION,
                    q["subject"], q["year"], q["source_sha256"],
                    b["source_sha256"],
                    sha256(q["transcription"].encode("utf-8")).hexdigest(),
                    sha256(b["text"].encode("utf-8")).hexdigest(),
                ))
                linked.add(q["unit_id"])
    if len(set((r[0], r[1], r[2]) for r in rows)) != len(rows):
        raise ValueError("qa_lexical_duplicate_candidate")
    return rows, {
        "eligible_exam_question_starts": len(eligible),
        "lexically_linked_question_starts": len(linked),
        "unlinked_eligible_question_starts": len(eligible - linked),
        "candidate_links": len(rows),
        "blocked_review_reasons": dict(sorted(blocked.items())),
        "scientifically_verified_links": 0,
    }


def seed(conn):
    books = conn.execute(
        "SELECT u.id AS unit_id,d.file_id,d.sha256 AS source_sha256,"
        "d.subject,u.chapter,u.text,u.quality_flags,"
        "u.ocr_global_page AS ocr_page "
        "FROM source_units u JOIN source_documents d ON d.id=u.document_id "
        "WHERE " + current_source_sql("d", kind="textbook") + " ORDER BY u.id"
    ).fetchall()
    exams = conn.execute(
        "SELECT t.unit_id,d.file_id,d.sha256 AS source_sha256,d.subject,"
        "t.year,t.question_number,t.transcription,t.concerns,u.quality_flags "
        "FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units u ON u.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription") +
        " ORDER BY t.unit_id"
    ).fetchall()
    rows, report = build(books, exams)
    conn.execute(DDL)
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO qa_exam_textbook_lexical_candidates("
                "exam_unit_id,textbook_unit_id,literal_term,algorithm,subject,"
                "exam_year,exam_source_sha256,textbook_source_sha256,"
                "exam_text_sha256,textbook_text_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", rows,
            )
    stored = conn.execute(
        "SELECT exam_unit_id,textbook_unit_id,literal_term,algorithm,subject,"
        "exam_year,exam_source_sha256,textbook_source_sha256,"
        "exam_text_sha256,textbook_text_sha256 "
        "FROM qa_exam_textbook_lexical_candidates WHERE algorithm=%s",
        (VERSION,),
    ).fetchall()
    stored_rows = {tuple(row.values()) for row in stored}
    if len(stored_rows) != len(stored) or stored_rows != set(rows):
        raise ValueError("qa_lexical_sql_provenance_mismatch")
    return report
