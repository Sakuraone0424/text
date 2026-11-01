"""Bounded supplementation from current AI-verified knowledge, never quota padding."""

import json
import random
import re
from collections import defaultdict

from pydantic import Field, StrictInt

from app.training_spec_v2 import SUBJECTS, COUNTS, POINTS
from app.source_policy import (
    current_source_sql, FILES, pinned_source_bytes, pinned_science_evidence
)
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES, StrictModel
from app.exam_authoring import instructions as exam_instructions, issues as exam_issues
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS
from app.source_bibliography import from_database as source_bibliography
from app.ai_jobs import enqueue, content_sha
from app import past_exam_import

STARTUP_RESERVE_DAYS = 2  # 60 questions/day, 120 verified candidates per user
# A paragraph already written from is worth this fraction of a fresh one.
USED_PARAGRAPH_WEIGHT = 0.2
ANCHOR_PARAGRAPH_MIN_CHARS = 120
ANCHOR_PARAGRAPH_MAX_CHARS = 3000
GAP_ATTEMPTS_PER_BUCKET = 8


class DistractorCheck(StrictModel):
    unit_id: StrictInt = Field(ge=1)
    excerpt: str = Field(min_length=5, max_length=2000)
    why_wrong: str = Field(min_length=10, max_length=1000)


class ChoiceDraft(StrictModel):
    """Single-choice output: the question plus textbook proof for each distractor."""

    question: Question
    distractor_checks: dict[str, DistractorCheck]


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


def verified_literal_anchor(concept, unit):
    """Extract only a dual-checked exact literal quote from the same locked unit."""
    relation = concept.get("relation")
    if not isinstance(relation, dict):
        return None
    quote = relation.get("review_excerpt")
    if (
        not isinstance(quote, str)
        or not 5 <= len(quote) <= 2000
        or quote not in unit["text"]
        or relation.get("file_id") != unit["file_id"]
        or relation.get("file_sha256") != unit["source_sha256"]
        or relation.get("chapter") != unit["chapter"]
        or relation.get("subject") != unit["subject"]
    ):
        return None
    return quote


def generation_payload_within_limit(payload):
    """Skip overlarge genuine-source packets rather than starve every plan tick."""
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()) <= 24000


def bounded_textbook_evidence(conn, primary, kind=None):
    """The anchor paragraph plus nearby clean ones of the same book and chapter.

    Hard ceiling for every question type: at most MAX_EVIDENCE_UNITS paragraphs
    and MAX_EVIDENCE_CHARS characters. The model never receives a whole book.
    Proximity is context, never scientific approval; integrity is checked again
    during scientific review.
    """
    if primary["chapter"] in ("", "unclassified", "contents"):
        return [primary]
    candidates = conn.execute(
        "SELECT u.id FROM source_units u "
        "JOIN source_units anchor ON anchor.id=%s "
        "WHERE u.document_id=anchor.document_id AND u.chapter=anchor.chapter "
        "AND u.id<>anchor.id AND u.quality_flags='[]'::jsonb "
        "AND length(u.text) BETWEEN 80 AND 4000 "
        "ORDER BY abs(u.ordinal-anchor.ordinal),u.ordinal,u.id LIMIT 8",
        (primary["unit_id"],),
    ).fetchall()
    snapshots = [primary]
    for item in candidates:
        if len(snapshots) >= past_exam_import.MAX_EVIDENCE_UNITS:
            break
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
        if (
            sum(len(v["text"]) for v in snapshots) + len(other["text"])
            > past_exam_import.MAX_EVIDENCE_CHARS
        ):
            continue
        snapshots.append(other)
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


def generation_attempts(conn, uid):
    """Question-writing requests so far per (subject, type); past-exam jobs excluded."""
    return {
        (r["subject"], r["question_type"]): r["n"]
        for r in conn.execute(
            "SELECT payload->>'subject' AS subject,"
            "payload->>'question_type' AS question_type,count(*) AS n "
            "FROM ai_jobs WHERE user_id=%s AND kind='generation' "
            "AND COALESCE(payload->>'origin','')<>'past_exam' GROUP BY 1,2", (uid,),
        ).fetchall()
    }


def eligible_reserve_count(conn, uid, subject, kind):
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
    return eligible_verified_reserve(conn, eligible)


def assemble_payload(conn, subject, kind, primary, anchor_quote, style_concept):
    """Everything ONE question-writing request carries; None if it cannot be built safely."""
    if len(primary["text"]) > past_exam_import.MAX_EVIDENCE_CHARS:
        return None
    # Bootstrap directly from the user's genuine past-exam transcript, rather
    # than requiring an earlier hand-created candidate packet. Question
    # boundaries must be recognized and text/image risks resolved before using
    # an item as style.
    style = select_exam_style(conn, subject, kind, style_concept)
    if not style:
        return None
    try:
        style_units = [snapshot_unit(conn, sid) for sid in style["style_unit_ids"]]
    except ValueError:
        return None
    if any(
        st["source_kind"] != "exam_transcription"
        or st["subject"] != subject
        or set(st["quality_flags"]) & STYLE_SOURCE_BLOCKERS
        # A newer/overwritten DOCX with the same Drive ID must not become an AI
        # style anchor merely by being current in SQL; only the exact
        # user-locked genuine-exam bytes are allowed.
        or not pinned_source_bytes(st["file_id"], st["source_sha256"])
        for st in style_units
    ):
        return None
    try:
        book = source_bibliography(conn, primary)
    except ValueError:
        # Unknown author/edition or a changed source checksum must never be
        # replaced by an invented bibliographic label.
        return None
    payload = dict(
        subject=subject,
        question_type=kind,
        max_points=POINTS[kind],
        evidence=bounded_textbook_evidence(conn, primary, kind),
        anchor_exact_excerpt=anchor_quote,
        style=style_units,
        style_year=style["year"],
        style_overlap=style["lexical_topic_overlap"],
        source_bibliography=book,
        schema=(ChoiceDraft if kind == "single_choice" else Question).model_json_schema(),
        instructions=exam_instructions(kind, POINTS[kind])
        + " For at least one citation, copy anchor_exact_excerpt EXACTLY "
        "into its evidence.excerpt field with identical characters, "
        "punctuation and spacing; do not paraphrase or retype it. "
        "Other excerpt strings, if present, must be literal substrings "
        "of their matching evidence unit. Never use this anchor as "
        "proof for any answer or rubric claim it does not support.",
    )
    # A source/style packet can exceed the job admission limit. Skip it and
    # try another genuine evidence unit rather than retrying the same
    # ValueError on every planner tick.
    return payload if generation_payload_within_limit(payload) else None


def plan(conn, uid):
    existing = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' AND COALESCE(payload->>'origin','')<>'past_exam' AND state IN ('pending','running','retrying')"
    ).fetchone()["n"]
    if existing >= 2:
        return
    for subject, kind, required in balanced_generation_buckets(generation_attempts(conn, uid)):
        count = eligible_reserve_count(conn, uid, subject, kind)
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
                or u["quality_flags"]
                or u["subject"] != subject
                or not pinned_source_bytes(u["file_id"], u["source_sha256"])
            ):
                # Validate scientific evidence BEFORE an authorized,
                # possibly billable model task is enqueued. A genuine-exam
                # DOCX is style only, never scientific answer evidence.
                continue
            anchor_quote = verified_literal_anchor(concept, u)
            if anchor_quote is None:
                continue
            key = f"supplement:{subject}:{kind}:{concept['unit_id']}:{concept['unit_sha256']}"
            if conn.execute(
                "SELECT 1 FROM ai_jobs WHERE job_key=%s", (key,)
            ).fetchone():
                continue
            payload = assemble_payload(conn, subject, kind, u, anchor_quote, concept)
            if payload is None:
                continue
            enqueue(conn, uid, key, "generation", payload)
            return


# --------------------------------------------------------------------------
# Gap-driven planning: no corpus-wide sweep, one paragraph per request
# --------------------------------------------------------------------------


def _count_by_bucket(conn, sql, params=()):
    return {(r["s"], r["t"]): r["n"] for r in conn.execute(sql, params).fetchall()}


def bucket_gaps(conn, uid):
    """Buckets still short of the two-day reserve, in fair-attempt order.

    Supply already on its way is deducted so the planner never over-orders:
    questions being reviewed or repaired, unfinished writing requests, and for
    term/short answer the past-exam items not yet turned into questions.
    """
    pipeline = _count_by_bucket(
        conn,
        "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,count(*) AS n "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        "WHERE st.status IN ('candidate','checking','repairing') GROUP BY 1,2",
    )
    inflight = _count_by_bucket(
        conn,
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,count(*) AS n FROM ai_jobs "
        "WHERE kind='generation' AND COALESCE(payload->>'origin','')<>'past_exam' "
        "AND dispatched_at IS NULL AND state IN ('pending','running','retrying','succeeded') GROUP BY 1,2",
    )
    past = _count_by_bucket(
        conn,
        "SELECT subject AS s,question_type AS t,count(*) AS n FROM past_exam_items "
        "WHERE status IN ('imported','queued') AND question_type IN ('term','short_answer') GROUP BY 1,2",
    )
    gaps = []
    for subject, kind, required in balanced_generation_buckets(generation_attempts(conn, uid)):
        key = (subject, kind)
        gap = (
            required * STARTUP_RESERVE_DAYS
            - eligible_reserve_count(conn, uid, subject, kind)
            - pipeline.get(key, 0) - inflight.get(key, 0) - past.get(key, 0)
        )
        if gap > 0:
            gaps.append((subject, kind, gap))
    return gaps


def paragraph_pool(conn, subject):
    """Clean, pinned textbook paragraphs a question may be written from."""
    pinned = {}
    pool = []
    for row in conn.execute(
        "SELECT u.id,u.chapter,d.file_id,d.sha256 FROM source_units u "
        "JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql("d", kind="textbook")
        + " AND d.subject=%s AND u.quality_flags='[]'::jsonb "
        "AND u.chapter NOT IN ('','unclassified','contents') AND length(u.text) BETWEEN %s AND %s "
        "ORDER BY u.id",
        (subject, ANCHOR_PARAGRAPH_MIN_CHARS, ANCHOR_PARAGRAPH_MAX_CHARS),
    ).fetchall():
        ok = pinned.setdefault((row["file_id"], row["sha256"]), pinned_source_bytes(row["file_id"], row["sha256"]))
        if ok:
            pool.append(dict(unit_id=row["id"], chapter_key=f"{row['file_id']}|{row['chapter']}"))
    return pool


def used_paragraphs(conn):
    """How many questions were already written from each paragraph."""
    return {
        r["unit"]: r["n"]
        for r in conn.execute(
            "SELECT unit,count(*) AS n FROM ("
            " SELECT (payload->'evidence'->0->>'unit_id')::bigint AS unit FROM ai_jobs "
            " WHERE kind='generation' AND COALESCE(payload->>'origin','')<>'past_exam'"
            " UNION ALL"
            " SELECT substring(k from 6)::bigint FROM question_origins o,"
            " jsonb_array_elements_text(o.concept_keys) k WHERE o.origin='past_exam' AND k LIKE 'unit:%'"
            ") t WHERE unit IS NOT NULL GROUP BY unit"
        ).fetchall()
    }


def chapter_weights(pool, frequency, used):
    """Chapter weight = exam frequency, discounted by what it already supplied.

    Frequently examined chapters come first; as questions accumulate from a
    chapter its weight falls, so coverage spreads instead of repeating one area.
    """
    by_chapter = defaultdict(list)
    for item in pool:
        by_chapter[item["chapter_key"]].append(item["unit_id"])
    weights = {
        ck: (1 + frequency.get(ck, 0)) / (1 + sum(used.get(u, 0) for u in units))
        for ck, units in by_chapter.items()
    }
    return by_chapter, weights


def pick_paragraph(by_chapter, weights, used, rng, skip=()):
    """Weighted choice: chapter by examined frequency, then a less-used paragraph."""
    chapters = sorted(c for c, units in by_chapter.items() if any(u not in skip for u in units))
    if not chapters:
        return None
    chapter = rng.choices(chapters, weights=[weights[c] for c in chapters])[0]
    units = sorted(u for u in by_chapter[chapter] if u not in skip)
    return rng.choices(units, weights=[USED_PARAGRAPH_WEIGHT ** used.get(u, 0) for u in units])[0]


def anchor_sentence(text):
    """The longest sentence-sized literal span of a paragraph (never rewritten)."""
    parts = [x.strip() for x in re.split(r"(?<=[。！？；;])|\n", text) if x]
    parts = [x for x in parts if 20 <= len(x) <= 300]
    return max(parts, key=len) if parts else None


def _enqueue_gap(conn, uid, subject, kind):
    if not select_exam_style(conn, subject, kind):
        return None  # no clean genuine past question of this type to imitate
    pool = paragraph_pool(conn, subject)
    if not pool:
        return None
    used = used_paragraphs(conn)
    by_chapter, weights = chapter_weights(pool, past_exam_import.chapter_frequency(conn, subject), used)
    prior = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE user_id=%s AND kind='generation' "
        "AND payload->>'subject'=%s AND payload->>'question_type'=%s",
        (uid, subject, kind),
    ).fetchone()["n"]
    rng = random.Random(f"gap:{uid}:{subject}:{kind}:{prior}")
    skip = set()
    for _ in range(GAP_ATTEMPTS_PER_BUCKET):
        unit_id = pick_paragraph(by_chapter, weights, used, rng, skip)
        if unit_id is None:
            return None
        skip.add(unit_id)
        try:
            primary = snapshot_unit(conn, unit_id)
        except ValueError:
            continue
        anchor = anchor_sentence(primary["text"])
        if (
            anchor is None
            or primary["source_kind"] != "textbook"
            or primary["subject"] != subject
            or primary["quality_flags"]
            or not pinned_source_bytes(primary["file_id"], primary["source_sha256"])
        ):
            continue
        key = f"gap:{subject}:{kind}:{unit_id}:{primary['unit_sha256'][:16]}"
        if conn.execute("SELECT 1 FROM ai_jobs WHERE job_key=%s", (key,)).fetchone():
            continue
        payload = assemble_payload(
            conn, subject, kind, primary, anchor,
            {"unit_id": unit_id, "relation": {"name": primary["chapter"]}},
        )
        if payload is not None:
            return enqueue(conn, uid, key, "generation", payload)
    return None


def plan_gap(conn, uid):
    """Queue one question-writing request for the largest uncovered bucket.

    Independent of AI_FULL_CORPUS_ENABLED: nothing here reads the corpus
    through a model. Each request carries one weighted-random paragraph and a
    few neighbours (hard caps in past_exam_import), nothing more.
    """
    active = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' "
        "AND COALESCE(payload->>'origin','')<>'past_exam' AND state IN ('pending','running','retrying')"
    ).fetchone()["n"]
    if active >= 2:
        return None
    past_exam_import.index_item_chapters(conn)
    for subject, kind, _ in bucket_gaps(conn, uid):
        job = _enqueue_gap(conn, uid, subject, kind)
        if job:
            return job
    return None


def repair_source_identity_from_locked_units(raw_question, units):
    """Correct citation *coordinates* from immutable source snapshots only.

    The model is not an authority on document hashes, OCR page indices, or
    paragraph IDs. Repair those clerical coordinates only when the model's
    proposed literal excerpt is already present in the exact requested unit.
    Never invent/replace excerpt text, change a source unit ID, or alter the
    question's answer, stem, rubric, or scientific claims. Invalid evidence
    must still fail independent review.
    """
    if not isinstance(raw_question, dict):
        return raw_question
    entries = raw_question.get("evidence")
    if not isinstance(entries, list):
        return raw_question
    units_by_id = {u["unit_id"]: u for u in units}
    fixed = []
    for evidence in entries:
        if not isinstance(evidence, dict):
            return raw_question
        unit_id = evidence.get("source_unit_id")
        unit = units_by_id.get(unit_id) if type(unit_id) is int else None
        excerpt = evidence.get("excerpt")
        if (
            unit is None
            or unit.get("source_kind") != "textbook"
            or unit.get("quality_flags")
            or not isinstance(excerpt, str)
            or not excerpt.strip()
            or excerpt.strip() not in unit.get("text", "")
        ):
            # Do not try to repair invented text or unknown/stale evidence.
            return raw_question
        fixed.append({
            **evidence,
            "file_id": unit["file_id"],
            "source_sha256": unit["source_sha256"],
            "ocr_page": unit["ocr_page"],
            "paragraph_id": unit["unit_id"],
            "chapter": unit["chapter"],
            "locator": f"OCR:{unit['ocr_page']}:UNIT:{unit_id}",
            "excerpt": excerpt.strip(),
        })
    return {**raw_question, "evidence": fixed}


def drop_unlisted_criterion_tags(raw_question):
    """Remove ONLY unsupported rubric tag references; never repair science.

    The original model response remains in its immutable job receipt.
    Criterion wording, points, answers and source excerpts are unchanged.
    Missing/invalid top-level tags or rubric syntax still fail validation.
    """
    if not isinstance(raw_question, dict):
        return raw_question
    top = raw_question.get("knowledge_tags")
    rubric = raw_question.get("rubric")
    if not isinstance(top, list) or not all(isinstance(x, str) for x in top):
        return raw_question
    if not isinstance(rubric, list):
        return raw_question
    allowed = set(top)
    fixed = []
    changed = False
    for item in rubric:
        if not isinstance(item, dict):
            return raw_question
        tags = item.get("knowledge_tags", [])
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            return raw_question
        good = [tag for tag in tags if tag in allowed]
        if good != tags:
            changed = True
        fixed.append({**item, "knowledge_tags": good})
    return {**raw_question, "rubric": fixed} if changed else raw_question


def attach_distractor_proof(q, response, payload):
    """Single choice: every wrong option needs a verbatim textbook refutation.

    The refuting excerpts become part of the question's cited evidence, so the
    independent reviewers receive exactly the text that is claimed to refute
    each distractor.
    """
    raw = response.get("distractor_checks") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("distractor_checks_required")
    checks = {k: DistractorCheck.model_validate(v) for k, v in raw.items()}
    if set(checks) != set("ABCD") - {q.answer}:
        raise ValueError("distractor_checks_must_cover_every_wrong_option")
    units = {u["unit_id"]: u for u in payload["evidence"]}
    evidence = [e.model_dump() for e in q.evidence]
    have = {(e["source_unit_id"], e["excerpt"]) for e in evidence}
    for letter in sorted(checks):
        check = checks[letter]
        unit = units.get(check.unit_id)
        if unit is None:
            raise ValueError("unrequested_source")
        if check.excerpt not in unit["text"]:
            raise ValueError("invented_or_unmatched_quote")
        if (check.unit_id, check.excerpt) not in have:
            have.add((check.unit_id, check.excerpt))
            evidence.append(past_exam_import.evidence_record(unit, payload["source_bibliography"], check.excerpt))
    return Question.model_validate({**q.model_dump(), "evidence": evidence}), checks


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
    raw_question = drop_unlisted_criterion_tags(raw_question)
    raw_question = repair_source_identity_from_locked_units(raw_question, p["evidence"])
    q = Question.model_validate(raw_question)
    if (q.subject, q.question_type, q.max_points) != (
        p["subject"],
        p["question_type"],
        p["max_points"],
    ):
        raise ValueError("generated_quota_mismatch")
    distractors = None
    if q.question_type == "single_choice":
        q, distractors = attach_distractor_proof(q, response, p)
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
                    # Only paragraphs the question actually cites; the model may
                    # legitimately use fewer than the neighbours it was given.
                    unit_ids=sorted({e.source_unit_id for e in q.evidence}),
                    explanation="Exact retrieved evidence attached; independent answer round must verify every rubric criterion",
                )
                for _ in q.rubric
            ],
        ),
    )
    if distractors:
        payload["ai_analysis"]["distractor_evidence"] = {
            letter: check.model_dump() for letter, check in sorted(distractors.items())
        }
    qid = create_packet(conn, payload)
    past_exam_import.mark_origin(
        conn, qid, past_exam_import.ORIGIN_GENERATED,
        concept_keys=[f"unit:{p['evidence'][0]['unit_id']}"],
        source_ref=dict(style_year=p["style_year"]),
    )
    return qid
