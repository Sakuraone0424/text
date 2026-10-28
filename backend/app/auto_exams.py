"""AI text checks of transcription compilations, never official-paper certification."""

from typing import Literal
from pydantic import Field, StrictBool, StrictInt
from psycopg.types.json import Jsonb
from app.question_quality import StrictModel
from app.candidate_review import snapshot_unit
from app.ai_jobs import enqueue


class Transcript(StrictModel):
    year: StrictInt = Field(ge=1998, le=2026)
    question_type: Literal[
        "single_choice", "fill_blank", "term", "short_answer", "essay", "unknown"
    ]
    complete_stem: str = Field(min_length=5, max_length=12000)
    unit_ids: list[StrictInt] = Field(min_length=1, max_length=100)
    blanks: list[str] = Field(max_length=100)
    subquestions: list[str] = Field(max_length=50)
    source_level: Literal["transcription"]
    original_verified: StrictBool
    official_answer_verified: StrictBool
    reference_answer_present: StrictBool
    missing_content: list[str] = Field(max_length=30)
    concerns: list[str] = Field(max_length=30)
    rationale: str = Field(min_length=30, max_length=5000)


def plan(conn, uid):
    if (
        conn.execute(
            "SELECT count(*) AS n FROM ai_jobs WHERE kind='exam_analysis' AND state IN ('pending','running','retrying')"
        ).fetchone()["n"]
        >= 2
    ):
        return
    row = conn.execute(
        "SELECT * FROM evidence_review_work w WHERE algorithm='evidence-work/1.6' AND kind='exam_question' AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.payload->>'exam_work_key'=w.work_key) ORDER BY priority DESC,work_key LIMIT 1"
    ).fetchone()
    if not row:
        return
    units = [snapshot_unit(conn, x) for x in row["source_unit_ids"]]
    if any(u["source_kind"] != "exam_transcription" for u in units):
        return
    enqueue(
        conn,
        uid,
        "exam-analysis:" + row["work_key"],
        "exam_analysis",
        dict(
            exam_work_key=row["work_key"],
            evidence=units,
            declared_year=row["evidence"]["year"],
            known_concerns=row["evidence"].get("concerns", []),
            schema=Transcript.model_json_schema(),
            instructions="Inspect the actual OCR transcription units, not an official original. Resolve question boundaries, numbering, subquestions, blanks, type, missing formulas/images/tables and reference-answer presence. Keep year tied to the supplied structural year; ambiguous year must be flagged. Never certify an official answer or original. Return exact concatenated source text only for complete_stem; do not reconstruct missing text.",
        ),
    )


def accept(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_transcript_call_required")
    p = job["payload"]
    r = Transcript.model_validate(job["result"]["response"])
    current = [snapshot_unit(conn, u["unit_id"]) for u in p["evidence"]]
    if (
        current != p["evidence"]
        or r.year != p["declared_year"]
        or r.original_verified
        or r.official_answer_verified
    ):
        raise ValueError("transcription_provenance_not_official")
    selected = [u["text"] for u in current if u["unit_id"] in r.unit_ids]
    if set(r.unit_ids) - {
        u["unit_id"] for u in current
    } or r.complete_stem not in "\n".join(selected):
        raise ValueError("invented_transcription_text")
    u = current[0]
    relation = {
        **r.model_dump(),
        "label": "AI自动核验",
        "policy_version": "auto-transcription/1",
        "observed_frequency_only": True,
        "official_frequency_eligible": False,
        "coverage_bias_disclosed": True,
        "source_flags": sorted(
            {flag for unit in current for flag in unit["quality_flags"]}
        ),
    }
    status = (
        "candidate"
        if r.concerns
        or r.missing_content
        or r.question_type == "unknown"
        or relation["source_flags"]
        or p.get("known_concerns")
        else "ready"
    )
    conn.execute(
        "INSERT INTO auto_knowledge_events(unit_id,unit_sha256,job_id,relation,status,run_key) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            u["unit_id"],
            u["unit_sha256"],
            job["id"],
            Jsonb(relation),
            status,
            "transcription:" + str(job["id"]),
        ),
    )


def observed(conn):
    rows = conn.execute(
        "SELECT * FROM auto_knowledge_events WHERE relation->>'policy_version'='auto-transcription/1' ORDER BY id"
    ).fetchall()
    accepted = []
    for r in rows:
        try:
            u = snapshot_unit(conn, r["unit_id"])
        except ValueError:
            continue
        if u["unit_sha256"] == r["unit_sha256"] and r["status"] == "ready":
            accepted.append(r["relation"])
    return dict(
        text_checked_transcription_questions=len(accepted),
        official_original_verified=0,
        official_probability_estimate=None,
        coverage_limitations="OCR compilation coverage and missing years are not true examination probabilities",
    )
