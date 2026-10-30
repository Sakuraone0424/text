"""First real, book-located editorial question draft for isolated QA.

This is ONE original question from an explicit literal sentence of the
owner's locked fifth-edition cell-biology textbook. Do not certify the answer
from OCR alone. No model/API call, no fake independent signatures, no
publication and no 60-question reserve count.
"""
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES
from app.source_bibliography import from_database
from app.source_policy import FILES, current_source_sql, pinned_source_bytes
from app.exam_style_readiness import BOUNDARY_ISSUES
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS

BOOK_ID = next(k for k, f in FILES.items() if f["key"] == "cell_biology")
EXAM_ID = next(k for k, f in FILES.items() if f["key"] == "cell_biology_exams")
QUOTED_ANCHOR = "将所结合的 GTP 水解为 GDP"
STEM = "微管组装过程中，β-微管蛋白将结合的 GTP 水解为____。"
SCORE = 2


def prepare(conn):
    """Return a validated private draft only on an exactly retrieved clean unit."""
    source = conn.execute(
        "SELECT u.id FROM source_units u JOIN source_documents d "
        "ON d.id=u.document_id WHERE d.file_id=%s AND "
        + current_source_sql("d", kind="textbook") +
        " AND u.text LIKE %s AND u.quality_flags='[]'::jsonb "
        "ORDER BY u.id LIMIT 2",
        (BOOK_ID, "%" + QUOTED_ANCHOR + "%"),
    ).fetchall()
    if len(source) != 1:
        return None, "literal_textbook_anchor_not_unique_clean_unit"
    unit = snapshot_unit(conn, source[0]["id"])
    if (
        unit["file_id"] != BOOK_ID
        or unit["source_kind"] != "textbook"
        or unit["subject"] != "cell_biology"
        or not pinned_source_bytes(BOOK_ID, unit["source_sha256"])
        or QUOTED_ANCHOR not in unit["text"]
        or unit["quality_flags"]
        or unit["ocr_page"] is None
        or unit["chapter"] in ("unclassified", "unknown", "contents", "")
    ):
        return None, "literal_textbook_anchor_not_source_locked"
    styles = conn.execute(
        "SELECT t.unit_id,t.year,t.concerns,u.quality_flags "
        "FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units u ON u.id=t.unit_id "
        "WHERE d.file_id=%s AND " + current_source_sql("d", kind="exam_transcription") +
        " AND t.question_type='fill_blank' AND t.question_number IS NOT NULL "
        " AND u.quality_flags='[]'::jsonb "
        "ORDER BY t.year DESC,t.unit_id DESC LIMIT 100",
        (EXAM_ID,),
    ).fetchall()
    allowed_risks = set(STYLE_SOURCE_BLOCKERS) | set(BOUNDARY_ISSUES)
    style = next(
        (s for s in styles if s["year"] is not None and not (set(s["concerns"] or []) & allowed_risks)),
        None,
    )
    if style is None:
        return None, "verified_exam_transcription_style_not_available"
    bibliography = from_database(conn, unit)
    q = Question.model_validate({
        "subject": "cell_biology",
        "question_type": "fill_blank",
        "stem": STEM,
        "answer": "GDP",
        "accepted_answers": ["GDP"],
        "max_points": SCORE,
        "rubric": [{
            "description": "准确填写GTP水解产生的GDP；不把GTP帽误判为GDP帽",
            "points": SCORE,
            "knowledge_tags": ["微管GTP水解"],
        }],
        "knowledge_tags": ["微管GTP水解", "微管动态不稳定性"],
        "difficulty": "core",
        "style_reference_ids": [style["unit_id"]],
        "evidence": [{
            "file_id": unit["file_id"],
            "source_sha256": unit["source_sha256"],
            "ocr_page": unit["ocr_page"],
            "paragraph_id": unit["unit_id"],
            "source_unit_id": unit["unit_id"],
            "textbook": bibliography["textbook"],
            "authors": bibliography["authors"],
            "edition": bibliography["edition"],
            "chapter": unit["chapter"],
            "locator": f"OCR:{unit['ocr_page']}:UNIT:{unit['unit_id']}",
            "excerpt": QUOTED_ANCHOR,
        }],
    }).model_dump()
    return {
        "candidate_key": f"qa-editorial-cell-microtubule-gdp-{unit['unit_id']}-{style['unit_id']}-v1",
        "batch_key": "qa-editorial-no-model/1",
        "question": q,
        "style_sources": [{
            "unit_id": style["unit_id"],
            "year": style["year"],
            "relationship": "Same subject and fill-blank type in real owner transcription; topic similarity and original authentication not certified",
        }],
        "ai_analysis": {
            "status": "Offline editorial / pending independent science and answer review",
            "independent_human_review": False,
            "axes": {
                axis: "First-pass source-located draft; scientific/answer/style acceptance still missing"
                for axis in AXES
            },
            "uncertainties": [
                "Evidence is limited to the six locked OCR/transcription files; scientific review remains pending",
                "No independent science/answer reviewers have approved this candidate",
                "Historical exemplar is same subject/type only; recent matching style not proven",
            ],
            "rubric_evidence": [{
                "unit_ids": [unit["unit_id"]],
                "explanation": "Locked OCR passage directly contains the GDP fact; independent six-file science and answer review still required",
            }],
        },
    }, None


def seed(conn, packet_builder=None, persister=None):
    # Resolve defaults when called, so QA fixtures and future pipeline edits
    # cannot silently keep a stale function object captured at import time.
    packet_builder = prepare if packet_builder is None else packet_builder
    persister = create_packet if persister is None else persister
    packet, reason = packet_builder(conn)
    if packet is None:
        return {
            "status": "blocked",
            "reason": reason,
            "draft_created_or_reused": False,
            "published": False,
            "independent_science_approved": False,
        }
    qid = persister(conn, packet)
    status = conn.execute(
        "SELECT status FROM reviewed_questions WHERE id=%s", (qid,),
    ).fetchone()
    if not status or status["status"] != "draft":
        raise ValueError("offline_editorial_draft_was_unexpectedly_published")
    return {
        "status": "pending_independent_science_and_answer_review",
        "draft_created_or_reused": True,
        "draft_question_id": qid,
        "published": False,
        "independent_science_approved": False,
        "original_model_calls": 0,
    }
