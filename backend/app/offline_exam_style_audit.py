"""Offline, privacy-preserving exam-style audit of the six locked real files.

Uses exactly the same OCR/DOCX parser and question-boundary classifier as the
database importer, but needs no database, provider, or manual reviewer.
Only counts and year metadata are written; no copyrighted question wording,
private filesystem paths, original DOCX bytes, or answer keys leave the tool.
"""
import argparse
from collections import defaultdict

from app.collection_seed import LOCK, preflight
from app.source_intake import private_json
from app.source_policy import COLLECTION
from app.sources import prepare_source
from app.transcript_analysis import candidates
from app.exam_style_readiness import BOUNDARY_ISSUES, UNRESOLVED_IMAGE
from app.training_spec_v2 import SUBJECTS, COUNTS


def report(directory, *, lock_path=LOCK):
    six = preflight(directory, lock_path=lock_path)
    counts = defaultdict(int)
    safe = defaultdict(int)
    years = defaultdict(set)
    unsafe_reasons = defaultdict(lambda: defaultdict(int))
    sources = []
    for entry in six:
        if entry["source_kind"] != "exam_transcription":
            continue
        prepared = prepare_source(entry["path"], entry["metadata"])
        units = [dict(row, id=i, ordinal=i) for i, row in enumerate(prepared["rows"], 1)]
        item = next(x for x in COLLECTION["files"] if x["key"] == entry["key"])
        parsed = list(candidates(units, item))
        candidate_count = 0
        for row in parsed:
            subject = item["subject"]
            kind = row["question_type"]
            if (
                subject not in SUBJECTS or kind not in COUNTS
                or row["question_number"] is None
            ):
                continue
            candidate_count += 1
            key = (subject, kind)
            counts[key] += 1
            problems = (
                set(row["concerns"] or []) | set(
                    units[row["unit_id"] - 1]["quality_flags"] or []
                )
            ) & (UNRESOLVED_IMAGE | BOUNDARY_ISSUES)
            if row["year"] is None:
                problems.add("year_unassigned")
            if problems:
                for issue in problems:
                    unsafe_reasons[key][issue] += 1
            else:
                safe[key] += 1
                years[key].add(row["year"])
        media = entry["exam_media_inventory"]
        sources.append({
            "key": entry["key"],
            "sha256": entry["sha256"],
            "candidate_question_starts_in_four_types": candidate_count,
            "parsed_document_units": len(units),
            "media_objects": media["media_objects"],
            "referenced_media_objects": media["referenced_media_objects"],
            "unreferenced_media_objects": len(media["unreferenced_media_objects"]),
            "media_formats": media["media_formats"],
            "images_scientifically_verified": False,
        })
    buckets = [
        {
            "subject": subject,
            "question_type": kind,
            "observed_question_starts": counts[(subject, kind)],
            "safe_style_anchors": safe[(subject, kind)],
            "blocked_by_parsing_or_images": counts[(subject, kind)] - safe[(subject, kind)],
            "years_with_safe_anchors": sorted(years[(subject, kind)]),
            "risk_causes_overlapping": dict(sorted(unsafe_reasons[(subject, kind)].items())),
            "automatic_generation_has_same_type_style": safe[(subject, kind)] > 0,
        }
        for subject in SUBJECTS for kind in COUNTS
    ]
    return {
        "collection_version": COLLECTION["version"],
        "mode": "offline_read_only_same_parser_as_database_intake",
        "all_six_locked_sha256_verified": len(six) == 6,
        "observed_ocr_page_markers": sum(x["ocr_page_markers"] for x in six),
        "genuine_past_exam_source_status": "user_provided_genuine_past_exams",
        "scientific_answer_verification": False,
        "independent_second_ai_review": False,
        "database_import_verified": False,
        "question_stock_120_verified": False,
        "ready_for_same_type_style": all(x["safe_style_anchors"] for x in buckets),
        "missing_same_type_style": [
            {"subject": x["subject"], "question_type": x["question_type"]}
            for x in buckets if not x["safe_style_anchors"]
        ],
        "sources": sources,
        "buckets": buckets,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    result = report(args.directory)
    private_json(args.report, result)
    print("offline_six_source_exam_style_audit_completed")
    return 0 if result["all_six_locked_sha256_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
