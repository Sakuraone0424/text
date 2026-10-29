"""Seed unresolved OCR and original exam media risks in isolated QA PostgreSQL.

This is a metadata-only, idempotent append-only work queue. It records
unresolved issues; never approves scientific statements, original figures or
question answers. The source documents and source_units remain immutable.

Run only after a six-original independent read-only pass, with upload disabled:
    python -m app.qa_seed_pending_review
"""
from collections import Counter
from hashlib import sha256
import json
import os

from app.exam_media_audit import audit_bytes
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import COLLECTION, pinned_source_bytes
from app.source_readiness import audit as source_audit

DDL = """
CREATE TABLE IF NOT EXISTS qa_source_risk_items(
    issue_key text PRIMARY KEY,
    source_key text NOT NULL,
    source_sha256 text NOT NULL,
    source_unit_id bigint,
    source_ocr_page integer,
    text_sha256 text,
    media_member text,
    media_sha256 text,
    risk_flag text NOT NULL,
    introduced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    baseline_state text NOT NULL DEFAULT 'pending'
        CHECK(baseline_state = 'pending'),
    CHECK(
      (source_unit_id IS NOT NULL AND text_sha256 IS NOT NULL
       AND media_member IS NULL AND media_sha256 IS NULL)
      OR
      (source_unit_id IS NULL AND text_sha256 IS NULL
       AND media_member IS NOT NULL AND media_sha256 IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS qa_source_risk_by_source
    ON qa_source_risk_items(source_key,risk_flag,source_ocr_page);
CREATE TABLE IF NOT EXISTS qa_source_risk_events(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_key text NOT NULL REFERENCES qa_source_risk_items(issue_key),
    reviewer text NOT NULL CHECK(length(trim(reviewer)) >= 3),
    review_phase text NOT NULL CHECK(review_phase IN ('original_visual','scientific')),
    verdict text NOT NULL CHECK(verdict IN ('pass','reject')),
    original_checked boolean NOT NULL DEFAULT false,
    note text NOT NULL CHECK(length(trim(note)) >= 30),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(issue_key,reviewer,review_phase)
);
CREATE OR REPLACE VIEW qa_source_risk_progress AS
    SELECT i.issue_key,i.source_key,i.risk_flag,
      CASE
        WHEN EXISTS (SELECT 1 FROM qa_source_risk_events e
                     WHERE e.issue_key=i.issue_key AND e.verdict='reject')
            THEN 'rejected_repair_required'
        WHEN (SELECT count(DISTINCT e.reviewer)
              FROM qa_source_risk_events e
              WHERE e.issue_key=i.issue_key AND e.verdict='pass'
                    AND e.original_checked) >= 2
         AND EXISTS (SELECT 1 FROM qa_source_risk_events e
                     WHERE e.issue_key=i.issue_key
                     AND e.review_phase='original_visual'
                       AND e.verdict='pass' AND e.original_checked)
         AND EXISTS (SELECT 1 FROM qa_source_risk_events e
                     WHERE e.issue_key=i.issue_key
                     AND e.review_phase='scientific'
                       AND e.verdict='pass' AND e.original_checked)
            THEN 'two_distinct_reviews_recorded_not_automatic_science_approval'
        ELSE 'pending' END AS review_state
    FROM qa_source_risk_items i;
"""


def _identity(*fields):
    return sha256("|".join(map(str, fields)).encode("utf-8")).hexdigest()


def build_rows(units, media):
    """Return only provenance fingerprints; not one word of copyrighted text."""
    rows = []
    stats = Counter()
    for unit in units:
        source_key = unit["source_key"]
        digest = unit["source_sha256"]
        if not pinned_source_bytes(unit["file_id"], digest):
            raise ValueError("Unpinned source in QA risk seed")
        for flag in sorted(set(unit["quality_flags"] or [])):
            rows.append((
                _identity("text",digest,unit["unit_id"],flag),
                source_key,digest,unit["unit_id"],unit["ocr_page"],
                sha256(unit["text"].encode("utf-8")).hexdigest(),
                None,None,flag,
            ))
            stats["text"] += 1
    for item in media:
        for entry in item["media"]:
            rows.append((
                _identity("media",item["source_sha256"],entry["member"]),
                item["source_key"],item["source_sha256"],None,None,None,
                entry["member"],entry["sha256"],"unverified_embedded_media",
            ))
            stats["media"] += 1
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("Duplicate fingerprint in source risk review queue")
    return rows, dict(stats)


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_risk_seed_refused_non_qa_environment")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_risk_seed_requires_upload_to_be_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_risk_seed_requires_isolated_schema")
    by_id = {x["file_id"]:x["key"] for x in COLLECTION["files"]}
    with isolated_db(schema,"staging") as conn:
        report = source_audit(conn)
        if (
            report["imported_files"] != 6
            or report["integrity_passed_files"] != 6
            or not report["all_six_byte_integrity_checks_passed"]
        ):
            raise ValueError("Six source readback gate failed before issue seeding")
        documents = conn.execute(
            "SELECT d.file_id,d.sha256 AS source_sha256,"
            "u.id AS unit_id,u.text,u.ocr_global_page AS ocr_page,"
            "u.quality_flags FROM source_units u "
            "JOIN source_documents d ON d.id=u.document_id "
            "WHERE u.quality_flags != '[]'::jsonb "
            "ORDER BY d.file_id,u.id"
        ).fetchall()
        units = [
            {**row,"source_key":by_id[row["file_id"]]}
            for row in documents if row["file_id"] in by_id
        ]
        if len(units) != len(documents):
            raise ValueError("Non-six-source units detected in isolated QA schema")
        media = []
        for item in COLLECTION["files"]:
            if item["kind"] != "exam_transcription":
                continue
            version = conn.execute(
                "SELECT d.sha256 AS source_sha256,v.source_content "
                "FROM source_versions v "
                "JOIN source_documents d ON d.id=v.document_id "
                "WHERE d.file_id=%s",
                (item["file_id"],),
            ).fetchone()
            if not version or not pinned_source_bytes(
                item["file_id"],version["source_sha256"]
            ):
                raise ValueError("DOCX media original not pinned")
            review = audit_bytes(bytes(version["source_content"]))
            if (review["source_sha256"] != version["source_sha256"]
                or review["media_objects"] != (27 if item["key"] == "biochemistry_exams" else 4)):
                raise ValueError("DOCX source image inventory mismatch")
            media.append({
                "source_key":item["key"],
                "source_sha256":version["source_sha256"],
                "media":review["media"],
            })
        rows, stats = build_rows(units,media)
        expected_text = sum(sum(f["risk_flag_counts"].values()) for f in report["files"])
        if (stats.get("text") != expected_text or stats.get("media") != 31):
            raise ValueError("QA source risk queue does not match canonical source audit")
        conn.execute(DDL)
        conn.executemany(
            "INSERT INTO qa_source_risk_items("
            "issue_key,source_key,source_sha256,source_unit_id,"
            "source_ocr_page,text_sha256,media_member,media_sha256,risk_flag"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT(issue_key) DO NOTHING",
            rows,
        )
        status = conn.execute(
            "SELECT count(*) AS total,"
            "count(*) FILTER (WHERE baseline_state='pending') AS pending "
            "FROM qa_source_risk_items"
        ).fetchone()
        event_count = conn.execute(
            "SELECT count(*) AS n FROM qa_source_risk_events"
        ).fetchone()["n"]
        if status["total"] != len(rows) or status["pending"] != len(rows):
            raise ValueError("QA risk queue incomplete or contaminated")
        if event_count:
            raise ValueError("QA automatic seeding cannot fabricate review decisions")
    with isolated_db(schema,"staging",readonly=True) as conn:
        final = conn.execute(
            "SELECT count(*) AS total,"
            "count(*) FILTER (WHERE review_state='pending') AS pending "
            "FROM qa_source_risk_progress"
        ).fetchone()
        if final["total"] != len(rows) or final["pending"] != len(rows):
            raise ValueError("QA risk queue failed independent postcommit readback")
    print("QA_RISK_LEDGER_SQL_READBACK=" + json.dumps({
        "status":"pending_issues_seeded_postcommit_readback_verified",
        "risk_text_occurrences":stats["text"],
        "unreviewed_media_objects":stats["media"],
        "total_pending_issues":len(rows),
        "two_distinct_independent_reviews_completed":0,
        "scientific_resolution_completed":False,
        "original_source_text_logged":False,
    }, separators=(",",":"),sort_keys=True),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
