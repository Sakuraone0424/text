"""Read-only disclosure of the proof actually recorded for submitted questions.

OCR excerpt identity, AI review events and bibliography verification are distinct.
Never claim physical-book review, expert review, or official past-paper proof from
an OCR quote or an automatic status flag.
"""


def report(conn, user_id, paper_id):
    paper = conn.execute(
        "SELECT p.id FROM papers_v2 p JOIN attempts_v2 a ON a.paper_id=p.id "
        "WHERE p.id=%s AND p.user_id=%s",
        (paper_id, user_id),
    ).fetchone()
    if not paper:
        raise LookupError("Submitted paper not found")
    items = conn.execute(
        "SELECT i.position,i.question_id,i.snapshot,q.content AS current_content,"
        "q.status AS publication_status,s.status AS ai_status "
        "FROM paper_items_v2 i "
        "JOIN reviewed_questions q ON q.id=i.question_id "
        "LEFT JOIN auto_question_states s ON s.question_id=q.id "
        "WHERE i.paper_id=%s ORDER BY i.position",
        (paper_id,),
    ).fetchall()
    from app.auto_quality import ready

    results = []
    for item in items:
        snapshot = item["snapshot"]
        refs = snapshot.get("evidence") or []
        quote_matches = []
        bibliography_pending = False
        for e in refs:
            if any(word in str(e.get("authors", "")) + " " + str(e.get("edition", ""))
                   for word in ("待核查", "未核查")):
                bibliography_pending = True
            unit_id = e.get("source_unit_id")
            if type(unit_id) is not int:
                quote_matches.append(False)
                continue
            unit = conn.execute(
                "SELECT u.text,u.chapter,u.ocr_global_page,d.sha256 AS document_sha,"
                "d.file_id AS document_file_id "
                "FROM source_units u JOIN source_documents d ON d.id=u.document_id "
                "WHERE u.id=%s", (unit_id,)
            ).fetchone()
            quote_matches.append(bool(
                unit
                and isinstance(e.get("excerpt"), str)
                and bool(e["excerpt"])
                and e["excerpt"] in unit["text"]
                and e.get("chapter") == unit["chapter"]
                and (not e.get("file_id") or e["file_id"] == unit["document_file_id"])
                and (not e.get("source_sha256") or e["source_sha256"] == unit["document_sha"])
                and (not e.get("ocr_page") or e["ocr_page"] == unit["ocr_global_page"])
            ))
        audit = conn.execute(
            "SELECT passed,content_snapshot FROM question_audits WHERE question_id=%s "
            "ORDER BY id DESC LIMIT 1", (item["question_id"],)
        ).fetchone()
        audit_match = bool(
            audit and audit["passed"]
            and audit["content_snapshot"] == item["current_content"]
        )
        automatic = bool(item["ai_status"] == "ready" and ready(conn, item["question_id"]))
        results.append({
            "position": item["position"],
            "question_id": item["question_id"],
            "ocr_excerpt_recorded": bool(refs),
            "ocr_quote_matches_stored_text": bool(refs) and all(quote_matches),
            "book_metadata_pending": bibliography_pending,
            "independent_ai_science_and_answer_review": automatic,
            "review_audit_record_matches_current_question": audit_match,
            "printed_original_book_page_verified": None,
            "official_exam_original_verified": None,
            "verification_scope": (
                "independent_AI_evidence_rounds"
                if automatic else
                "review_record_only"
                if audit_match else "not_established"
            ),
        })
    return {
        "paper_id": paper_id,
        "count": len(results),
        "note": (
            "Matching OCR extracts and independent AI review do not establish "
            "human verification against every printed original or official exam key."
        ),
        "items": results,
    }
