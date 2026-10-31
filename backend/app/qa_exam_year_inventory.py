"""Source-locked year/type inventory from QA PostgreSQL exam candidates.

This validates the user's two exact DOCX transcription versions as read from
the QA database. Numbers are PARSER question starts, never certified official
question counts or answer keys. Includes Word-native numbered questions.
"""
from collections import Counter
import json
import os
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import COLLECTION, pinned_source_bytes
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS, FORMAL_TYPES

EXAM_ITEMS={x["file_id"]:x for x in COLLECTION["files"]
            if x["kind"]=="exam_transcription"}
BOUNDARY_BLOCKERS={"question_boundary_unconfirmed","question_type_unknown",
                   "year_unassigned"}

def summarize(rows):
    years={}
    for item in EXAM_ITEMS.values():
        for y in range(item["year_start"],item["year_end"]+1):
            years[item["key"],y]={
                "source_key":item["key"],"year":y,
                "indexed_transcript_units":0,
                "parsed_question_starts":0,
                "parser_structurally_safe_starts":0,
                "candidate_type_counts":Counter(),
                "candidate_start_blocked":0,
                "official_original_verified":False,
                "scientific_answer_verified":False,
            }
    unknown_year=Counter()
    unrecognized_type=Counter()
    for row in rows:
        item=EXAM_ITEMS.get(row["file_id"])
        if not item or not pinned_source_bytes(row["file_id"],row["source_sha256"]):
            raise ValueError("Exam source not in owner's six original file lock")
        year=row["year"]
        if year is None or not (item["year_start"]<=year<=item["year_end"]):
            unknown_year[item["key"]]+=1
            continue
        dest=years[item["key"],year]
        dest["indexed_transcript_units"]+=1
        if row["question_number"] is None:
            continue
        dest["parsed_question_starts"]+=1
        kind=row["question_type"] or "unknown"
        dest["candidate_type_counts"][kind]+=1
        flags=set(row["concerns"] or [])|set(row["quality_flags"] or [])
        if kind not in FORMAL_TYPES:
            unrecognized_type[item["key"]]+=1
        if kind not in FORMAL_TYPES or flags & (
            set(STYLE_SOURCE_BLOCKERS)|BOUNDARY_BLOCKERS
        ):
            dest["candidate_start_blocked"]+=1
        else:
            # For multiple choice, this excludes science/answer review and
            # does NOT check completeness of A-D continuation source units.
            dest["parser_structurally_safe_starts"]+=1
    result=[]
    for row in years.values():
        row["candidate_type_counts"]=dict(sorted(row["candidate_type_counts"].items()))
        row["transcription_year_present"]=row["indexed_transcript_units"]>0
        row["question_count_certified"]=False
        result.append(row)
    return {
        "scope":"canonical_QA_SQL_user_DOCX_transcription_year_type_candidates",
        "expected_years":len(result),
        "years_with_transcription":sum(y["transcription_year_present"] for y in result),
        "years_missing":[{"source_key":y["source_key"],"year":y["year"]}
                         for y in result if not y["transcription_year_present"]],
        "candidate_numbered_starts":sum(y["parsed_question_starts"] for y in result),
        "parser_structurally_safe_starts":sum(y["parser_structurally_safe_starts"] for y in result),
        "unknown_year_unit_counts":dict(unknown_year),
        "unrecognized_type_start_counts":dict(unrecognized_type),
        "official_original_exams_authenticated":False,
        "scientific_answer_keys_approved":False,
        "question_counts_certified":False,
        "years":result,
    }

def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME","").strip().lower()!="qa":
        raise SystemExit("QA year inventory refused outside QA")
    schema=os.environ.get("QA_IMPORT_SCHEMA","")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("QA year inventory isolated schema required")
    with isolated_db(schema,"staging",readonly=True) as conn:
        rows=conn.execute(
            "SELECT d.file_id,d.sha256 AS source_sha256,"
            "t.year,t.question_type,t.question_number,t.concerns,"
            "s.quality_flags "
            "FROM exam_transcript_candidates t "
            "JOIN source_documents d ON d.id=t.document_id "
            "JOIN source_units s ON s.id=t.unit_id"
        ).fetchall()
        report=summarize(rows)
    if report["expected_years"]!=52:
        raise ValueError("User-locked source transcription year scope changed")
    print("QA_EXAM_YEAR_SUMMARY="+json.dumps(
        {k:v for k,v in report.items() if k!="years"},
        ensure_ascii=False,separators=(",",":"),sort_keys=True
    ),flush=True)
    for item in report["years"]:
        print("QA_EXAM_YEAR_ROW="+json.dumps(
            item,ensure_ascii=False,separators=(",",":"),sort_keys=True
        ),flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
