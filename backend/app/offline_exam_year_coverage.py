"""Year-scoped candidate coverage of the user's two pinned exam DOCX sources.

Candidates are extracted from the user's transcriptions, NOT authenticated
against official original papers. Counts are candidate starts, not certified
questions. No question text is exported in this metadata report.
"""
import argparse
from collections import Counter
from pathlib import Path

from app.collection_seed import LOCK, preflight
from app.source_intake import private_json
from app.source_policy import COLLECTION
from app.sources import prepare_source
from app.transcript_analysis import candidates, STYLE_SOURCE_BLOCKERS
from app.exam_style_readiness import BOUNDARY_ISSUES


REFUSE = STYLE_SOURCE_BLOCKERS | BOUNDARY_ISSUES
TYPES = ("single_choice", "fill_blank", "term", "short_answer")


def report(directory, *, lock_path=LOCK):
    items = preflight(directory, lock_path=lock_path)
    source_items = {s["key"]: s for s in COLLECTION["files"]}
    results = []
    total_media = 0
    for item in items:
        source = source_items[item["key"]]
        if source["kind"] != "exam_transcription":
            continue
        parsed = prepare_source(item["path"], item["metadata"])
        if parsed["sha256"] != item["sha256"]:
            raise ValueError("Exam source changed during year coverage export")
        start, end = source["year_start"], source["year_end"]
        years = {
            year: dict(
                subject=source["subject"],year=year,
                transcript_units=0,
                candidate_question_starts=0,
                parser_safe_style_candidates=0,
                blocked_candidate_starts=0,
                candidate_types={kind:0 for kind in TYPES},
                parser_safe_types={kind:0 for kind in TYPES},
                official_original_independently_verified=False,
                scientific_answers_verified=False,
            ) for year in range(start,end+1)
        }
        for unit in parsed["rows"]:
            y=unit.get("year")
            if y in years:
                years[y]["transcript_units"]+=1
        rows=[dict(u,id=n) for n,u in enumerate(parsed["rows"],1)]
        unknown_year_candidates=0
        unknown_type_candidates=0
        for q in candidates(rows,source):
            if q.get("question_number") is None:
                continue
            year=q.get("year")
            if year not in years:
                unknown_year_candidates+=1
                continue
            target=years[year]
            target["candidate_question_starts"]+=1
            kind=q.get("question_type")
            if kind not in TYPES:
                unknown_type_candidates+=1
            else:
                target["candidate_types"][kind]+=1
            risks=set(q.get("concerns") or [])
            if kind not in TYPES or risks & REFUSE:
                target["blocked_candidate_starts"]+=1
            else:
                target["parser_safe_style_candidates"]+=1
                target["parser_safe_types"][kind]+=1
        total_media+=item["exam_media_inventory"]["media_objects"]
        for row in years.values():
            row["transcription_presence"]=(
                "indexed_year_has_text_unverified_completeness"
                if row["transcript_units"] else "year_not_identified_in_transcription"
            )
        results.extend(years.values())
    return {
        "scope":"six_pinned_source_exam_year_parser_candidates_not_official_authentication",
        "source_files_hash_verified":len(items),
        "expected_year_rows":len(results),
        "years_with_text":sum(x["transcript_units"]>0 for x in results),
        "years_without_identified_transcription":[
            {"subject":x["subject"],"year":x["year"]}
            for x in results if not x["transcript_units"]
        ],
        "parser_candidate_starts":sum(x["candidate_question_starts"] for x in results),
        "parser_safe_style_starts":sum(x["parser_safe_style_candidates"] for x in results),
        "unassigned_year_candidate_starts":unknown_year_candidates,
        "unrecognized_question_type_starts":unknown_type_candidates,
        "embedded_media_awaiting_review":total_media,
        "official_originals_independently_verified":False,
        "answer_keys_scientifically_verified":False,
        "question_counts_finalized":False,
        "years":results,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True)
    parser.add_argument("--report",required=True)
    args=parser.parse_args(argv)
    if Path(args.report).exists():
        parser.error("Year audit report already exists")
    private_json(args.report, report(args.directory))
    print("pinned_exam_year_candidate_coverage_pending_original_verification")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
