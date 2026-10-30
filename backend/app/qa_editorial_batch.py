"""Human-readable editorial source checks -> private question drafts (not published).

Input is 12 questions whose literal citations were checked against the owner's
Google Drive OCR textbooks and whose exam links were checked against the
owner's 2024-2026/2017 transcriptions. These are NOT independently approved
science/answer reviews. No model API is used for this work.
"""
import json
from collections import Counter
from pathlib import Path

from app.source_policy import FILES, current_source_sql, pinned_source_bytes
from app.source_bibliography import from_database
from app.question_quality import Question, AXES
from app.candidate_review import snapshot_unit, create_packet
from app.auto_generation import select_exam_style
from app.training_spec_v2 import POINTS

DATA = Path(__file__).parents[1] / "data" / "qa_editorial_12_source_answers_20261030.json"
BOOK_KEYS = {
    "cell": "cell_biology",
    "biou": "biochemistry_upper",
    "biol": "biochemistry_lower",
}
SUBJECTS = {"cell": "cell_biology", "biou": "biochemistry", "biol": "biochemistry"}


def source_facts():
    packet = json.loads(DATA.read_text(encoding="utf-8"))
    questions = packet.get("questions")
    if packet.get("record_count") != 12 or not isinstance(questions, list) or len(questions) != 12:
        raise ValueError("editorial_source_ledger_count_mismatch")
    if len(set(x["id"] for x in questions)) != 12:
        raise ValueError("editorial_duplicate_evidence_id")
    if any(
        item.get("source_key") not in BOOK_KEYS
        or item.get("review_status") != "assistant_source_and_answer_first_pass_checked_not_independent_or_official"
        or item.get("published") is not False
        or item.get("reference_excerpt_verified") is not True
        for item in questions
    ):
        raise ValueError("editorial_source_ledger_fake_approval")
    return questions


def make_question(fact, unit, bibliography, style_id):
    """Create a v2 schema question with no fake audit or human signature."""
    kind = fact["kind"]
    answer = fact["answer"]
    if kind not in POINTS:
        raise ValueError("unsupported_editorial_question_kind")
    blank_count = fact["question"].count("____")
    blanks = []
    accepted = []
    if kind == "fill_blank":
        if blank_count == 1:
            accepted = [answer]
            if fact["id"] == "cell-nls-11":
                accepted.extend(["核定位信号", "NLS", "核定位序列"])
            if fact["id"] == "cell-srp-03":
                accepted.extend(["SRP", "信号识别颗粒"])
        elif blank_count == 2 and fact["id"] == "cell-arp23-02":
            blanks = [{"accepted_answers": ["Arp2"], "points": 1},
                      {"accepted_answers": ["Arp3"], "points": 1}]
        else:
            raise ValueError("unsupported_editorial_blank_count")
    elif blank_count:
        raise ValueError("unexpected_blank_in_editorial_question")
    max_points = POINTS[kind]
    return Question.model_validate({
        "subject": unit["subject"],
        "question_type": kind,
        "stem": fact["question"],
        "answer": answer,
        "accepted_answers": accepted,
        "blanks": blanks,
        "max_points": max_points,
        "rubric": [{
            "description": item["description"],
            "points": item["points"],
            "knowledge_tags": [fact["id"]],
        } for item in fact["rubric_points"]],
        "knowledge_tags": [fact["id"]],
        "difficulty": "core" if kind == "fill_blank" else "mechanism",
        "style_reference_ids": [style_id],
        "evidence": [{
            "file_id": unit["file_id"], "source_sha256": unit["source_sha256"],
            "ocr_page": unit["ocr_page"], "source_unit_id": unit["unit_id"],
            "paragraph_id": unit["unit_id"], "chapter": unit["chapter"],
            "locator": f"OCR:{unit['ocr_page']}:UNIT:{unit['unit_id']}",
            "excerpt": fact["quote"],
            "textbook": bibliography["textbook"],
            "authors": bibliography["authors"], "edition": bibliography["edition"],
        }],
    }).model_dump()


def seed(conn, facts=None, create=create_packet, style_lookup=select_exam_style):
    facts = source_facts() if facts is None else facts
    results = []
    for item in facts:
        key = BOOK_KEYS[item["source_key"]]
        fid = next(k for k, f in FILES.items() if f["key"] == key)
        subject = SUBJECTS[item["source_key"]]
        rows = conn.execute(
            "SELECT u.id FROM source_units u "
            "JOIN source_documents d ON d.id=u.document_id "
            "WHERE d.file_id=%s AND " + current_source_sql("d", kind="textbook") +
            " AND u.text LIKE %s AND u.quality_flags='[]'::jsonb "
            "ORDER BY u.id LIMIT 2",
            (fid, "%" + item["quote"] + "%"),
        ).fetchall()
        if len(rows) != 1:
            results.append({"id":item["id"],"status":"source_not_unique_or_risky"})
            continue
        unit = snapshot_unit(conn, rows[0]["id"])
        if (
            unit["file_id"] != fid
            or unit["source_kind"] != "textbook"
            or unit["subject"] != subject
            or unit["quality_flags"]
            or unit["ocr_page"] != item["ocr_page"]
            or unit["chapter"] in ("", "unknown", "unclassified", "contents")
            or item["quote"] not in unit["text"]
            or not pinned_source_bytes(fid, unit["source_sha256"])
        ):
            results.append({"id":item["id"],"status":"source_identity_or_page_mismatch"})
            continue
        style = style_lookup(conn, subject, item["kind"])
        if not style:
            results.append({"id":item["id"],"status":"no_same_subject_type_exam_style"})
            continue
        style_id = style["id"]
        original_style = snapshot_unit(conn, style_id)
        if (
            original_style["source_kind"] != "exam_transcription"
            or original_style["subject"] != subject
            or not pinned_source_bytes(original_style["file_id"], original_style["source_sha256"])
            or original_style["quality_flags"]
        ):
            results.append({"id":item["id"],"status":"historical_exam_style_not_source_locked"})
            continue
        bibliography = from_database(conn, unit)
        question = make_question(item, unit, bibliography, style_id)
        candidate_key=f"qa-ocr-curated-{item['id']}-{unit['unit_id']}-{style_id}-v1"
        packet = {
            "candidate_key": candidate_key,
            "batch_key":"qa-editorial-first-pass/20261030",
            "question":question,
            "style_sources":[{
                "unit_id":style_id,"year":style["year"],
                "relationship":"Located past-exam type of same subject; not official answer or recent style proof",
            }],
            "ai_analysis":{
                "status":"Offline editorial / pending independent science and answer review",
                "independent_human_review":False,
                "axes":{axis:"Assistant original OCR source and answer first pass; external scientific review pending" for axis in AXES},
                "uncertainties":[
                    "Independent scientific and answer checks not completed",
                    "Owner's transcription may contain original exam memory/OCR omissions",
                ],
                "rubric_evidence":[{
                    "unit_ids":[unit["unit_id"]],
                    "explanation":"Direct textbook excerpt identifies the first-pass answer; original exam style is separately located; additional correctness review pending",
                }],
            },
        }
        qid=create(conn, packet)
        row=conn.execute("SELECT status FROM reviewed_questions WHERE id=%s",(qid,)).fetchone()
        if not row or row["status"]!="draft":
            raise ValueError("curated_editorial_question_not_pending_draft")
        results.append({"id":item["id"],"status":"draft_created_or_reused","qid":qid})
    by_state=Counter(r["status"] for r in results)
    return {
        "reviewed_source_fact_count":len(facts),
        "private_drafts_created_or_reused":by_state["draft_created_or_reused"],
        "blocked_reasons":dict(sorted((k,v) for k,v in by_state.items() if k!="draft_created_or_reused")),
        "all_source_facts_accounted_for":len(results)==len(facts),
        "published_count":0,"independent_scientific_approvals":0,
        "model_calls":0,
        "per_question_status":results,
    }
