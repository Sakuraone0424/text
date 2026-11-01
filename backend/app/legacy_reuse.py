"""Reuse of two old study files without trusting either of them.

1. 120 old topic proposals (single choice / fill blank) become ordinary
   `generated` candidates: each excerpt is re-bound to the ONE database
   paragraph that contains it verbatim, identity fields come from the database,
   and the old status / review fields are ignored. They then go through the full
   two independent AI review rounds like any other candidate.
2. The all-year index (1908 unreviewed old questions) is used ONLY for entries
   whose stem matches a past-exam item exactly, and only as hints:
   - textbook page numbers raise the search score of paragraphs on those pages,
     but only for a book whose pages were measured against the database;
   - the explanation is passed to the answer-writing request as an UNREVIEWED
     draft. The review rounds never receive it.
   Photo / scanned sources and unmatched entries are never used.

Both files carry textbook excerpts: they are read in place (and, in deployment,
only from the image's data directory), never copied elsewhere and never logged.
Everything here reports counts and reason codes only.
"""

import json
from collections import Counter
from pathlib import Path

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import past_exam_import as pei
from app.auto_generation import select_exam_style
from app.candidate_review import create_packet, snapshot_unit
from app.exam_authoring import issues as exam_issues
from app.question_quality import AXES, Question
from app.source_bibliography import from_database as source_bibliography
from app.source_policy import FILES, current_source_sql, pinned_source_bytes

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROPOSALS_FILE = DATA_DIR / "120_versioned_book_anchored_topic_proposals_NOT_ADMITTED.json"
INDEX_FILE = DATA_DIR / "USTC_all_year_master_candidate_index_v1.json"

# Printed / scanned / photographed study aids are not text-checked material.
USABLE_SOURCE_KINDS = frozenset({"二手历年真题汇编DOCX", "辅导资料PDF文字版"})
SUBJECTS = {"生化": "biochemistry", "细胞": "cell_biology"}
SECTION_TYPES = (("名词解释", "term"), ("填空", "fill_blank"), ("简答", "short_answer"), ("选择", "single_choice"))
BOOK_KEYS = (
    ("高分笔记", "biochemistry_notes"),
    ("上册", "biochemistry_upper"),
    ("下册", "biochemistry_lower"),
    ("细胞生物学", "cell_biology"),
)
FILE_ID = {f["key"]: f["file_id"] for f in FILES.values()}
PAGE_FIELDS = (
    "book_body_pages_verified",
    "primary_textbook_body_pages_verified",
    "subject_matched_primary_textbook_body_pages_verified",
)

PROPOSALS_PER_TICK = 10
PROPOSAL_ATTEMPTS_PER_TICK = 60
MAX_SUMMARY_CHARS = 300
MAX_EXPLANATION_CHARS = 1500
MIN_CALIBRATION_SAMPLES = 30
MAX_CALIBRATION_ITEMS = 150
MIN_AGREEMENT_SHARE = 0.25
MIN_PEAK_TO_BACKGROUND = 4

# Permanent outcomes are recorded; transient ones are retried on a later tick.
TRANSIENT = frozenset({"style_anchor_missing", "bibliography_unavailable"})

def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# 1. Old topic proposals -> ordinary generated candidates
# --------------------------------------------------------------------------


def bind_excerpt(conn, subject, file_id, excerpt):
    """(unit_id, None) only for exactly one verbatim hit; else (None, reason)."""
    rows = conn.execute(
        "SELECT u.id,d.file_id FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql("d", kind="textbook")
        + " AND d.subject=%s AND strpos(u.text,%s)>0 ORDER BY u.id LIMIT 2",
        (subject, excerpt),
    ).fetchall()
    if not rows:
        return None, "evidence_not_found"
    if len(rows) > 1:
        return None, "evidence_ambiguous"
    if rows[0]["file_id"] != file_id:
        return None, "evidence_book_mismatch"
    return rows[0]["id"], None


def _has_textbook(conn, subject):
    return bool(
        conn.execute(
            "SELECT 1 FROM source_documents d WHERE "
            + current_source_sql("d", kind="textbook")
            + " AND d.subject=%s LIMIT 1",
            (subject,),
        ).fetchone()
    )


def _build_candidate(conn, record):
    """(question_id, None) or (None, reason code). Reads nothing but identity-free fields."""
    old = record["question"]
    subject, kind = old["subject"], old["question_type"]
    bound, evidence = [], []
    for item in old["evidence"]:
        uid, reason = bind_excerpt(conn, subject, item.get("file_id"), item.get("excerpt", ""))
        if reason:
            return None, reason
        try:
            unit = snapshot_unit(conn, uid)
        except ValueError:
            return None, "evidence_not_found"
        if (
            unit["source_kind"] != "textbook"
            or unit["quality_flags"]
            or not pinned_source_bytes(unit["file_id"], unit["source_sha256"])
        ):
            return None, "source_unit_not_clean"
        try:
            bibliography = source_bibliography(conn, unit)
        except ValueError:
            return None, "bibliography_unavailable"
        if all(b["unit_id"] != uid for b in bound):
            bound.append(unit)
        evidence.append(pei.evidence_record(unit, bibliography, item["excerpt"]))
    try:
        question = Question.model_validate(
            dict(
                subject=subject,
                question_type=kind,
                stem=old["stem"],
                options=old.get("options", {}),
                answer=old["answer"],
                accepted_answers=old.get("accepted_answers", []),
                blanks=old.get("blanks", []),
                evidence=evidence,
                rubric=old["rubric"],
                knowledge_tags=old.get("knowledge_tags", []),
                difficulty=old.get("difficulty", "core"),
                max_points=old["max_points"],
            )
        )
    except (ValidationError, KeyError, TypeError):
        return None, "invalid_question"
    if exam_issues(question):
        return None, "quality_gate_failed"
    primary = bound[0]
    style = select_exam_style(
        conn, subject, kind, {"unit_id": primary["unit_id"], "relation": {"name": primary["chapter"]}}
    )
    if not style:
        return None, "style_anchor_missing"
    try:
        with conn.transaction():
            qid = create_packet(
                conn,
                dict(
                    candidate_key=f"legacy-proposal-{record['draft_id']}-{pei.content_sha(question.model_dump())[:12]}",
                    batch_key="legacy-proposal/1",
                    question=question.model_dump(),
                    style_sources=[
                        dict(
                            unit_id=sid,
                            year=style["year"],
                            relationship="Genuine past-exam style of the same subject and type; not answer evidence",
                        )
                        for sid in style["style_unit_ids"]
                    ],
                    ai_analysis=dict(
                        status="AI自动核验 / pending independent checks",
                        independent_human_review=False,
                        axes={a: "Old proposal re-bound by exact text; no earlier conclusion is reused" for a in AXES},
                        uncertainties=[
                            "Old proposal: every answer and scoring point must be verified afresh by both independent rounds"
                        ],
                        rubric_evidence=[
                            dict(
                                unit_ids=[u["unit_id"] for u in bound],
                                explanation="Re-bound to the exact textbook paragraph(s); the independent answer round must verify this point",
                            )
                            for _ in question.rubric
                        ],
                    ),
                ),
            )
    except ValueError:
        return None, "packet_rejected"
    pei.mark_origin(
        conn, qid, pei.ORIGIN_GENERATED,
        concept_keys=[f"unit:{primary['unit_id']}"],
        source_ref=dict(legacy_draft_id=record["draft_id"]),
    )
    return qid, None


def import_proposals(conn, path=None):
    """Re-bind a batch of not-yet-processed proposals. Returns counts only."""
    records = _read(path or PROPOSALS_FILE)
    result = Counter()
    if not isinstance(records, list):
        return result
    done = {r["draft_id"] for r in conn.execute("SELECT draft_id FROM legacy_proposals").fetchall()}
    attempts = recorded = 0
    for record in records:
        if recorded >= PROPOSALS_PER_TICK or attempts >= PROPOSAL_ATTEMPTS_PER_TICK:
            break
        draft_id = record.get("draft_id") if isinstance(record, dict) else None
        if not isinstance(draft_id, str) or draft_id in done:
            continue
        question = record.get("question") if isinstance(record.get("question"), dict) else {}
        subject, kind = question.get("subject"), question.get("question_type")
        if subject not in SUBJECTS.values() or kind not in ("single_choice", "fill_blank") or not _has_textbook(conn, subject):
            continue  # nothing to bind to yet; try again once the sources are loaded
        attempts += 1
        qid, reason = _build_candidate(conn, record)
        if reason in TRANSIENT:
            result["deferred"] += 1
            continue
        conn.execute(
            "INSERT INTO legacy_proposals(draft_id,subject,question_type,status,blocked_reason,question_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (draft_id, subject, kind, "blocked" if reason else "imported", reason, qid),
        )
        recorded += 1
        result[reason or "imported"] += 1
    return result


# --------------------------------------------------------------------------
# 2. Old master index -> page hints and an unreviewed reference draft
# --------------------------------------------------------------------------


def section_kind(section):
    return next((kind for key, kind in SECTION_TYPES if key in (section or "")), None)


def book_key(source):
    return next((key for marker, key in BOOK_KEYS if marker in (source or "")), None)


def usable_records(index):
    """Entries from text-checked sources, free of the index's own problem flags."""
    for record in (index or {}).get("records", []):
        subject = SUBJECTS.get(record.get("subject"))
        kind = section_kind(record.get("section"))
        audit = record.get("source_audit_20261018") or {}
        if (
            record.get("source_kind") not in USABLE_SOURCE_KINDS
            or record.get("issues")
            or audit.get("possible_truncation_or_wording_issue")
            or not subject
            or not kind
            or not str(record.get("stem") or "").strip()
        ):
            continue
        review = record.get("independent_detailed_review_2026_10_17") or {}
        pages = {}
        for field in PAGE_FIELDS:
            for entry in record.get(field) or []:
                key = book_key(entry.get("source"))
                if key:
                    slot = pages.setdefault(key, {"pdf_pages": set(), "printed_pages": set()})
                    slot["pdf_pages"].update(p for p in entry.get("pdf_pages", []) if type(p) is int)
                    slot["printed_pages"].update(p for p in entry.get("printed_pages", []) if type(p) is int)
        yield dict(
            id=str(record.get("id")),
            subject=subject,
            kind=kind,
            year=record.get("year") if type(record.get("year")) is int else None,
            stem=str(record["stem"]),
            summary=str(review.get("answer_summary") or "")[:MAX_SUMMARY_CHARS],
            explanation=str(review.get("detailed_explanation") or "")[:MAX_EXPLANATION_CHARS],
            pages=pages,
        )


def _item_hash(kind, stem):
    stem = pei.strip_number(stem)
    options = None
    if kind == "single_choice":
        parsed = pei.parse_choice(stem)
        if parsed is None:
            return None
        stem, options = parsed
    return pei.stem_hash(kind, stem, options)


def import_hints(conn, path=None):
    """Keep hints only for entries whose stem equals a past-exam item's. Counts only."""
    index = _read(path or INDEX_FILE)
    if index is None:
        return Counter()
    items = {
        (r["subject"], r["question_type"], r["stem_hash"]): r
        for r in conn.execute("SELECT id,subject,question_type,stem_hash,year FROM past_exam_items").fetchall()
    }
    matched = {}
    seen = Counter()
    for rec in usable_records(index):
        seen["usable"] += 1
        digest = _item_hash(rec["kind"], rec["stem"])
        item = items.get((rec["subject"], rec["kind"], digest)) if digest else None
        if item:
            matched.setdefault(item["id"], (item, []))[1].append(rec)
    for item_id, (item, recs) in matched.items():
        best = max(recs, key=lambda r: (r["explanation"] != "", r["year"] == item["year"], r["year"] or 0))
        pages = {}
        for rec in recs:
            for key, slot in rec["pages"].items():
                merged = pages.setdefault(key, {"pdf_pages": set(), "printed_pages": set()})
                merged["pdf_pages"] |= slot["pdf_pages"]
                merged["printed_pages"] |= slot["printed_pages"]
        conn.execute(
            "INSERT INTO legacy_hints(item_id,record_id,record_year,pages,answer_summary,explanation) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(item_id) DO UPDATE SET record_id=EXCLUDED.record_id,"
            "record_year=EXCLUDED.record_year,pages=EXCLUDED.pages,answer_summary=EXCLUDED.answer_summary,"
            "explanation=EXCLUDED.explanation",
            (
                item_id, best["id"], best["year"],
                Jsonb([
                    dict(file_key=k, pdf_pages=sorted(v["pdf_pages"]), printed_pages=sorted(v["printed_pages"]))
                    for k, v in sorted(pages.items())
                ]),
                best["summary"] or None, best["explanation"] or None,
            ),
        )
    seen["matched_items"] = len(matched)
    return seen


OFFSET_RANGE = range(-60, 61)
PEAK_WINDOW = 5


def _peak(diff_sets, min_samples):
    """(offset, hits, samples) when one offset clearly stands out, else None.

    A topic lists several consecutive pages, so a constant offset d shows up as
    "ocr_page - d is one of the listed pages" for many items, with neighbouring
    offsets elevated too. Real alignment: a strong peak far above the
    background of offsets well away from it. Misaligned pages: no peak.
    """
    n = len(diff_sets)
    counts = Counter(d for diffs in diff_sets for d in diffs if d in OFFSET_RANGE)
    if n < min_samples or not counts:
        return None
    best, peak = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0]), kv[0]))
    far = [counts.get(d, 0) for d in OFFSET_RANGE if abs(d - best) > PEAK_WINDOW]
    background = sum(far) / len(far)
    if peak >= MIN_AGREEMENT_SHARE * n and peak >= MIN_PEAK_TO_BACKGROUND * max(background, 1):
        return best, peak, n
    return None


def calibrate_pages(conn, min_samples=MIN_CALIBRATION_SAMPLES, max_items=MAX_CALIBRATION_ITEMS):
    """Measure whether index pages line up with the database's OCR pages.

    For each textbook, take the matched items whose clear best text match lies
    in that book and check, against the database itself, whether the index's
    page numbers are a constant offset away from the OCR page of that match.
    Only a book that passes is ever given page hints.
    """
    result = {}
    rows = conn.execute(
        "SELECT h.pages,i.subject,i.stem,i.options FROM legacy_hints h JOIN past_exam_items i ON i.id=h.item_id ORDER BY h.item_id"
    ).fetchall()
    for key, file_id in sorted(FILE_ID.items()):
        diffs = {"pdf_pages": [], "printed_pages": []}
        for row in rows:
            entry = next((e for e in row["pages"] if e["file_key"] == key), None)
            if entry is None or len(diffs["pdf_pages"]) >= max_items:
                continue
            ranked = pei.ranked_paragraphs(conn, row["subject"], row["stem"] + " " + " ".join(row["options"].values()))
            if not ranked or ranked[0][3] != file_id or ranked[0][4] is None:
                continue
            if len(ranked) > 1 and ranked[0][0] < 1.25 * ranked[1][0]:
                continue  # not a clear best match: useless as a measurement
            for column in diffs:
                diffs[column].append({ranked[0][4] - p for p in entry[column]})
        chosen = None
        for column, sets in diffs.items():
            found = _peak([d for d in sets if d], min_samples)
            if found and (chosen is None or found[1] / found[2] > chosen[1][1] / chosen[1][2]):
                chosen = (column, found)
        samples = len(diffs["pdf_pages"])
        column, (offset, agreeing, _) = chosen if chosen else (None, (None, 0, samples))
        conn.execute(
            "INSERT INTO legacy_page_calibration(file_key,page_column,page_offset,samples,agreeing,verified) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(file_key) DO UPDATE SET page_column=EXCLUDED.page_column,"
            "page_offset=EXCLUDED.page_offset,samples=EXCLUDED.samples,agreeing=EXCLUDED.agreeing,"
            "verified=EXCLUDED.verified,computed_at=clock_timestamp()",
            (key, column, offset, samples, agreeing, chosen is not None),
        )
        result[key] = chosen is not None
    return result


def page_hints(conn, item_id):
    """{file_id: {OCR pages}} for this item, only from books whose pages were verified."""
    row = conn.execute("SELECT pages FROM legacy_hints WHERE item_id=%s", (item_id,)).fetchone()
    if not row:
        return {}
    verified = {
        r["file_key"]: r
        for r in conn.execute("SELECT * FROM legacy_page_calibration WHERE verified").fetchall()
    }
    hints = {}
    for entry in row["pages"]:
        cal = verified.get(entry["file_key"])
        if cal and entry.get(cal["page_column"]):
            hints.setdefault(FILE_ID[entry["file_key"]], set()).update(
                p + cal["page_offset"] for p in entry[cal["page_column"]]
            )
    return hints


def reference_draft(conn, item_id):
    """The unreviewed explanation for answer writing; never given to a review round."""
    row = conn.execute(
        "SELECT answer_summary,explanation FROM legacy_hints WHERE item_id=%s", (item_id,)
    ).fetchone()
    if not row or not (row["answer_summary"] or row["explanation"]):
        return None
    return dict(
        status="UNREVIEWED_old_study_aid_may_be_wrong",
        answer_summary=row["answer_summary"] or "",
        explanation=row["explanation"] or "",
    )


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------


def _state(conn, key):
    row = conn.execute("SELECT value FROM legacy_reuse_state WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key, value):
    conn.execute(
        "INSERT INTO legacy_reuse_state(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE "
        "SET value=EXCLUDED.value,updated_at=clock_timestamp()",
        (key, Jsonb(value)),
    )


def plan(conn, uid=None):
    """One tick of reuse work; safe to call repeatedly. Counts only, no content."""
    if pei.pending_documents(conn):
        pei.import_items(conn)
    summary = {"proposals": dict(import_proposals(conn))}
    items = conn.execute("SELECT count(*) AS n FROM past_exam_items").fetchone()["n"]
    if items and _state(conn, "hints_for_items") != items and INDEX_FILE.exists():
        summary["hints"] = dict(import_hints(conn))
        _set_state(conn, "hints_for_items", items)
        _set_state(conn, "calibrated_for_items", None)
    hinted = conn.execute("SELECT count(*) AS n FROM legacy_hints").fetchone()["n"]
    if hinted and _state(conn, "calibrated_for_items") != items:
        summary["calibration"] = calibrate_pages(conn)
        _set_state(conn, "calibrated_for_items", items)
    return summary
