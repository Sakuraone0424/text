"""Read-only, source-grounded past-exam style-anchor inventory.

The two user-provided exam DOCX files ARE the project's genuine past exams.
This module does not invent missing questions or certify OCR/image conversion.
It counts only classified question starts whose words can actually be cited
by the AI generator. Scientific answers remain textbook-evidence-only.
"""
from collections import defaultdict
from app.source_policy import current_source_sql, pinned_source_bytes
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS
from app.training_spec_v2 import SUBJECTS, COUNTS

UNRESOLVED_IMAGE = set(STYLE_SOURCE_BLOCKERS)  # Includes tables, OCR doubts, figures
BOUNDARY_ISSUES = {"question_boundary_unconfirmed", "question_type_unknown", "year_unassigned"}


def report(conn):
    rows = conn.execute(
        "SELECT d.subject,d.file_id,d.sha256,t.question_type,t.year,t.question_number,"
        "t.concerns,s.quality_flags "
        "FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units s ON s.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription"),
    ).fetchall()
    totals = defaultdict(int)
    usable = defaultdict(int)
    years = defaultdict(set)
    blocked = defaultdict(int)
    for row in rows:
        key = (row["subject"], row["question_type"])
        if row["subject"] not in SUBJECTS or row["question_type"] not in COUNTS:
            continue
        if not row["question_number"]:
            continue  # A section heading or continuation is not another question.
        totals[key] += 1
        reasons = set(row["concerns"] or []) | set(row["quality_flags"] or [])
        if not pinned_source_bytes(row["file_id"], row["sha256"]):
            # Same file ID or a self-consistent newer SQL revision is not
            # evidence that these are the owner's pinned genuine DOCX bytes.
            blocked[key] += 1
            continue
        if not row["year"] or reasons & (UNRESOLVED_IMAGE | BOUNDARY_ISSUES):
            blocked[key] += 1
            continue
        usable[key] += 1
        years[key].add(row["year"])
    buckets = [
        {
            "subject": subject,
            "question_type": kind,
            "observed_question_starts": totals[(subject, kind)],
            "safe_style_anchors": usable[(subject, kind)],
            "blocked_by_parsing_or_images": blocked[(subject, kind)],
            "years_with_safe_anchors": sorted(years[(subject, kind)]),
            "automatic_generation_has_same_type_style": usable[(subject, kind)] > 0,
        }
        for subject in SUBJECTS for kind in COUNTS
    ]
    return {
        "source_status": "user_provided_genuine_past_exams",
        "scope": "parsed_question_starts_only_not_official_answer_verification",
        "ready_for_same_type_style": all(b["safe_style_anchors"] for b in buckets),
        "missing_same_type_style": [
            {"subject": b["subject"], "question_type": b["question_type"]}
            for b in buckets if not b["safe_style_anchors"]
        ],
        "buckets": buckets,
        "note": (
            "No generated questions or assumptions about years missing from the "
            "supplied DOCX are added to these figures. A style anchor does not "
            "by itself verify a scientific answer."
        ),
    }
