"""Bounded supplementation from current AI-verified knowledge, never quota padding."""

from app.training_spec_v2 import SUBJECTS, COUNTS, POINTS
from app.source_policy import (
    current_source_sql, FILES, pinned_source_bytes, pinned_science_evidence
)
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES
from app.exam_authoring import instructions as exam_instructions, issues as exam_issues
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS
from app.source_bibliography import from_database as source_bibliography
from app.ai_jobs import enqueue, content_sha

STARTUP_RESERVE_DAYS = 2  # 60 questions/day, 120 verified candidates per user


def eligible_verified_reserve(conn, rows):
    """Count only actual accepted six-source science, not stale ready flags.

    A ready status cannot make an unpinned question count toward the two-day
    120-question reserve. Keep this identical to the new-paper source gate.
    """
    from app.auto_quality import ready

    return sum(
        bool(pinned_science_evidence(row["content"]) and ready(conn, row["id"]))
        for row in rows
    )


def complete_choice_style_unit_ids(rows):
    """Only accept a genuine exam stem with consecutive nonempty A/B/C/D choices.

    Word DOCX may split four options across multiple paragraphs. Count neither
    an A-D label list nor a later question's choices as a complete style anchor.
    This validates *structure*, never a historical question's answer/science.
    """
    import re

    if not rows or len(rows) > 8:
        return None
    option = re.compile(r"(?<![A-Za-z0-9])([ABCD])[.、．)）:：]\s*")
    new_numbered_question = re.compile(r"^\s*\d{1,3}[.、．)）]\s*\S")
    next_letter = "ABCD"
    seen = 0
    unit_ids = []
    for index, row in enumerate(rows):
        text = str(row.get("text") or "")
        if (not text.strip()
            or not isinstance(row.get("id"), int)
            or set(row.get("quality_flags") or []) & STYLE_SOURCE_BLOCKERS):
            return None
        # Any new numbered question, year, section or answer ends the scope.
        # The first row, being the stem, may itself have a question number.
        if index and (
            new_numbered_question.match(text)
            or re.match(r"^\s*(?:参考答案|答案(?:及解析)?|解答|解析)\s*[:：]?", text)
            or re.match(r"^\s*(?:19|20)\d{2}\s*年", text)
            or re.match(r"^\s*(?:单项选择题|单选题|填空题|名词解释|简答题|论述题)", text)
        ):
            return None
        markers = list(option.finditer(text))
        if not markers:
            if index == 0:
                unit_ids.append(row["id"])
                continue  # A separate option paragraph may follow the stem.
            return None
        preamble = text[:markers[0].start()].strip()
        if index == 0:
            if not preamble:  # An option list without a question is not a stem.
                return None
        elif preamble:  # Later rows must *start* with their next option.
            return None

        for pos, match in enumerate(markers):
            if seen == 4 or match.group(1) != next_letter[seen]:
                return None
            end = markers[pos + 1].start() if pos + 1 < len(markers) else len(text)
            # A, B, C and D must each have actual text, not four bare labels.
            if not text[match.end():end].strip():
                return None
            seen += 1
        unit_ids.append(row["id"])
        if seen == 4:
            return unit_ids
    return None


def select_exam_style(conn, subject, kind, concept=None):
    """Find a genuine exam-style anchor; lexical overlap is NOT science proof.

    Prefer the same topic only when it appears literally in the old question.
    Otherwise spread stylistic examples across real years so the generator does
    not mimic the identical recent exam item for every topic.
    """
    import re
    import unicodedata

    rows = conn.execute(
        "SELECT t.unit_id AS id,t.document_id,su.ordinal AS start_ordinal,t.year,t.transcription FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units su ON su.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription") +
        " AND d.subject=%s AND t.question_type=%s "
        "AND t.question_number IS NOT NULL AND t.year IS NOT NULL "
        "AND NOT (su.quality_flags ?| ARRAY['corrupt_ocr','untranscribed_image','image_dependent','docx_table_requires_review','complex_table','ocr_uncertain','transcription_gap_reported']) "
        "AND NOT (t.concerns ?| ARRAY['corrupt_ocr','untranscribed_image','image_dependent',"
        "'docx_table_requires_review','complex_table','ocr_uncertain','transcription_gap_reported','question_boundary_unconfirmed','question_type_unknown','year_unassigned']) "
        "ORDER BY t.year DESC,t.id LIMIT 200",
        (subject, kind),
    ).fetchall()
    if not rows:
        return None
    relation = (concept or {}).get("relation") or {}
    terms = [
        str(relation.get(key, "")).strip() for key in ("name", "target")
    ]
    def normal(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())
    anchors = [normal(x) for x in terms if len(normal(x)) >= 2]
    matches = [
        sum(term in normal(row["transcription"]) for term in anchors)
        for row in rows
    ]
    # A topic-lexical match with missing/corrupt choices is *not* a usable
    # exemplar. Fall back to lower-overlap authentic questions rather than
    # returning no style even though a valid alternative exists.
    for score in sorted(set(matches), reverse=True):
        pool = [row for row, value in zip(rows, matches) if value == score]
        idx = (int((concept or {}).get("unit_id", 0)) % len(pool)) if concept else 0
        for offset in range(len(pool)):
            chosen = dict(pool[(idx + offset) % len(pool)])
            if kind == "single_choice":
                chunks = conn.execute(
                    "SELECT id,text,quality_flags FROM source_units "
                    "WHERE document_id=%s AND ordinal >= %s "
                    "AND ordinal < %s + 8 ORDER BY ordinal LIMIT 8",
                    (chosen["document_id"], chosen["start_ordinal"],
                     chosen["start_ordinal"]),
                ).fetchall()
                parts = complete_choice_style_unit_ids(chunks)
                if not parts or parts[0] != chosen["id"]:
                    continue
                chosen["style_unit_ids"] = parts
            else:
                chosen["style_unit_ids"] = [chosen["id"]]
            chosen["lexical_topic_overlap"] = score
            chosen["scope"] = "past_exam_style_not_textbook_answer_evidence"
            return chosen
    return None


def bounded_textbook_evidence(conn, primary, kind):
    """Expand a 9-point mechanism question only with same-book clean units.

    Metadata/byte integrity is checked again during scientific review;
    proximity is contextual evidence, never scientific approval.
    """
    if kind != "short_answer" or primary["chapter"] in ("", "unclassified", "contents"):
        return [primary]
    candidates = conn.execute(
        "SELECT u.id FROM source_units u "
        "JOIN source_units anchor ON anchor.id=%s "
        "WHERE u.document_id=anchor.document_id AND u.chapter=anchor.chapter "
        "AND u.id<>anchor.id AND u.quality_flags='[]'::jsonb "
        "AND length(u.text) BETWEEN 80 AND 4000 "
        "ORDER BY abs(u.ordinal-anchor.ordinal),u.ordinal,u.id LIMIT 4",
        (primary["unit_id"],),
    ).fetchall()
    snapshots = [primary]
    for item in candidates:
        try:
            other = snapshot_unit(conn, item["id"])
        except ValueError:
            continue
        if (
            other["file_id"] != primary["file_id"]
            or other["source_sha256"] != primary["source_sha256"]
            or other["chapter"] != primary["chapter"]
            or other["source_kind"] != "textbook"
            or other["quality_flags"]
            or not pinned_source_bytes(other["file_id"], other["source_sha256"])
        ):
            continue
        if sum(len(v["text"]) for v in snapshots) + len(other["text"]) > 7000:
            continue
        snapshots.append(other)
        if len(snapshots) >= 3:
            break
    return snapshots


def balanced_generation_buckets(attempts):
    """Fairly cover eight quota buckets, without weakening evidence admission.

    An unsuccessful generation still consumes a model request and must count
    toward balancing; it never counts as a scientifically approved question.
    """
    entries = [
        (subject, kind, needed)
        for subject in SUBJECTS for kind, needed in COUNTS.items()
    ]
    return sorted(
        entries,
        key=lambda b: (
            attempts.get((b[0], b[1]), 0) / (b[2] * STARTUP_RESERVE_DAYS),
            attempts.get((b[0], b[1]), 0),
            SUBJECTS.index(b[0]),
            list(COUNTS).index(b[1]),
        ),
    )


def plan(conn, uid):
    existing = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' AND state IN ('pending','running','retrying')"
    ).fetchone()["n"]
    if existing >= 2:
        return
    rows = conn.execute(
        "SELECT payload->>'subject' AS subject,"
        "payload->>'question_type' AS question_type,count(*) AS n "
        "FROM ai_jobs WHERE user_id=%s AND kind='generation' "
        "GROUP BY 1,2", (uid,),
    ).fetchall()
    attempts = {
        (r["subject"], r["question_type"]): r["n"] for r in rows
    }
    for subject, kind, required in balanced_generation_buckets(attempts):
        # A merely "ready" state does not prove current science/answer
        # review, publication or v2 scoring compatibility.
        eligible = conn.execute(
            "SELECT q.id,q.content FROM auto_question_states s "
            "JOIN reviewed_questions q ON q.id=s.question_id "
            "WHERE s.status='ready' AND q.status='published' "
            "AND q.content->>'subject'=%s AND q.content->>'question_type'=%s "
            "AND q.content->>'max_points'=%s AND "
            "(NOT EXISTS(SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id "
            "WHERE i.question_id=q.id AND p.user_id=%s) "
            "OR EXISTS(SELECT 1 FROM fsrs_cards f WHERE f.question_id=q.id "
            "AND f.user_id=%s AND f.due<=clock_timestamp()))",
            (subject, kind, str(POINTS[kind]), uid, uid),
        ).fetchall()
        count = eligible_verified_reserve(conn, eligible)
        if count >= required * STARTUP_RESERVE_DAYS:
            continue
        # Reuse only second-round knowledge, never the 17,128 pending proposals as approved facts.
        concepts = conn.execute(
            "SELECT k.* FROM auto_knowledge_events k JOIN source_units u ON u.id=k.unit_id JOIN source_documents d ON d.id=u.document_id WHERE "
            + current_source_sql()
            + " AND k.status='ready' AND k.relation->>'subject'=%s AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key='supplement:'||%s||':'||%s||':'||k.unit_id::text||':'||k.unit_sha256) ORDER BY k.id LIMIT 30",
            (subject, subject, kind),
        ).fetchall()
        for concept in concepts:
            try:
                u = snapshot_unit(conn, concept["unit_id"])
            except ValueError:
                continue
            if (
                u["unit_sha256"] != concept["unit_sha256"]
                or u["source_kind"] != "textbook"
                or u["subject"] != subject
                or not pinned_source_bytes(u["file_id"], u["source_sha256"])
            ):
                # Validate scientific evidence BEFORE an authorized,
                # possibly billable model task is enqueued. A genuine-exam
                # DOCX is style only, never scientific answer evidence.
                continue
            key = f"supplement:{subject}:{kind}:{concept['unit_id']}:{concept['unit_sha256']}"
            if conn.execute(
                "SELECT 1 FROM ai_jobs WHERE job_key=%s", (key,)
            ).fetchone():
                continue
            # Bootstrap directly from the user's genuine past-exam
            # transcript, rather than requiring an earlier hand-created
            # candidate packet. Question boundaries must be recognized and
            # text/image risks resolved before using an item as style.
            style = select_exam_style(conn, subject, kind, concept)
            if not style:
                continue
            style_units = [snapshot_unit(conn, sid) for sid in style["style_unit_ids"]]
            if any(
                st["source_kind"] != "exam_transcription"
                or st["subject"] != subject
                or set(st["quality_flags"]) & STYLE_SOURCE_BLOCKERS
                or not pinned_source_bytes(st["file_id"], st["source_sha256"])
                for st in style_units
            ):
                continue
            s = style_units[0]
            if not pinned_source_bytes(s["file_id"], s["source_sha256"]):
                # A newer/overwritten DOCX with the same Drive ID must not
                # become an AI style anchor merely by being current in SQL.
                # Only the exact user-locked genuine-exam bytes are allowed.
                continue
            try:
                book = source_bibliography(conn, u)
            except ValueError:
                # Unknown author/edition or a changed source checksum must
                # never be replaced by an invented bibliographic label.
                continue
            enqueue(
                conn,
                uid,
                key,
                "generation",
                dict(
                    subject=subject,
                    question_type=kind,
                    max_points=POINTS[kind],
                    evidence=bounded_textbook_evidence(conn, u, kind),
                    style=style_units,
                    style_year=style["year"],
                    style_overlap=style["lexical_topic_overlap"],
                    source_bibliography=book,
                    schema=Question.model_json_schema(),
                    instructions=exam_instructions(kind, POINTS[kind]),
                ),
            )
            return


def accept(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_generation_call_required")
    p = job["payload"]
    # Revalidate the *source bytes* when a queued model result returns, not
    # only when the job was scheduled. Old pending work must not resurrect
    # an unpinned textbook or genuine-exam style revision.
    if (
        not p.get("evidence")
        or not p.get("style")
        or any(
            not pinned_source_bytes(unit.get("file_id"), unit.get("source_sha256"))
            or unit.get("source_kind") != expected_kind
            or unit.get("subject") != p.get("subject")
            or (
                expected_kind == "exam_transcription"
                and set(unit.get("quality_flags") or []) & STYLE_SOURCE_BLOCKERS
            )
            for group, expected_kind in (
                ("evidence", "textbook"),
                ("style", "exam_transcription"),
            )
            for unit in p[group]
        )
    ):
        raise ValueError("generation_source_not_in_locked_six_file_collection")
    if p.get("question_type") == "single_choice":
        # Do not admit a stale or modified completed job that only carries
        # a historical stem, or stitches options from unrelated DOCX units.
        style_units = p["style"]
        ids = [u.get("unit_id") for u in style_units]
        if (
            not ids
            or not all(type(uid) is int for uid in ids)
            or any(uid != ids[0] + i for i, uid in enumerate(ids))
            or any(
                u.get("file_id") != style_units[0].get("file_id")
                or u.get("source_sha256") != style_units[0].get("source_sha256")
                for u in style_units
            )
            or complete_choice_style_unit_ids([
                {
                    "id": u["unit_id"],
                    "text": u.get("text", ""),
                    "quality_flags": u.get("quality_flags", []),
                }
                for u in style_units
            ]) != ids
        ):
            raise ValueError("historical_single_choice_options_incomplete_or_spliced")
    response = job["result"]["response"]
    # The submitted schema is Question itself, so a direct object is canonical.
    # Retain compatibility with older explicitly wrapped generation responses.
    raw_question = response.get("question", response) if isinstance(response, dict) else response
    q = Question.model_validate(raw_question)
    if (q.subject, q.question_type, q.max_points) != (
        p["subject"],
        p["question_type"],
        p["max_points"],
    ):
        raise ValueError("generated_quota_mismatch")
    structural_issues = exam_issues(q)
    if structural_issues:
        raise ValueError("generated_question_quality_gate_failed:" + ",".join(structural_issues))
    allowed = {u["unit_id"] for u in p["evidence"]}
    bibliography = p["source_bibliography"]
    if any(
        (e.textbook, e.authors, e.edition)
        != (bibliography["textbook"], bibliography["authors"], bibliography["edition"])
        for e in q.evidence
    ):
        raise ValueError("invented_bibliography")
    if not {e.source_unit_id for e in q.evidence} <= allowed:
        raise ValueError("unrequested_source")
    payload = dict(
        candidate_key="auto-generated-"
        + str(job["id"])
        + "-"
        + content_sha(q.model_dump())[:12],
        batch_key="auto-supplement/1",
        question=q.model_dump(),
        style_sources=[
            dict(
                unit_id=u["unit_id"],
                year=p["style_year"],
                relationship=(
                    "Genuine past-exam style with lexical topic overlap; not answer evidence"
                    if p.get("style_overlap", 0) else
                    "Genuine past-exam style of same subject/type; topical similarity unverified"
                ),
            )
            for u in p["style"]
        ],
        ai_analysis=dict(
            status="AI自动核验 / pending independent checks",
            independent_human_review=False,
            axes={
                a: "Generated draft; no previous AI conclusion is treated as independent proof"
                for a in AXES
            },
            uncertainties=["Requires new independent scientific and answer rounds"],
            rubric_evidence=[
                dict(
                    unit_ids=list(allowed),
                    explanation="Exact retrieved evidence attached; independent answer round must verify every rubric criterion",
                )
                for _ in q.rubric
            ],
        ),
    )
    return create_packet(conn, payload)
