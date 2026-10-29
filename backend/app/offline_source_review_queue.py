"""Private six-file OCR/media review queue. Never approves source science.

Run against the six locked local originals. All output is metadata-only; no
textbook passages, exam stems, original images, or private filesystem paths.
The queue tracks unresolved findings; it is not scientific verification.
"""
import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from app.collection_seed import LOCK, preflight
from app.source_intake import private_json
from app.sources import prepare_source


def _create(conn):
    conn.executescript("""
    CREATE TABLE original_sources (
        source_key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
        ocr_pages INTEGER NOT NULL, text_units INTEGER NOT NULL,
        embedded_media INTEGER NOT NULL
    );
    CREATE TABLE review_issues (
        issue_key TEXT PRIMARY KEY, source_key TEXT NOT NULL
            REFERENCES original_sources(source_key),
        source_sha256 TEXT NOT NULL,
        unit_ordinal INTEGER, ocr_page INTEGER,
        unit_sha256 TEXT, media_member TEXT, media_sha256 TEXT,
        risk_flag TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status='pending'),
        CHECK(
            (unit_ordinal IS NOT NULL AND unit_sha256 IS NOT NULL
             AND media_member IS NULL AND media_sha256 IS NULL)
            OR
            (unit_ordinal IS NULL AND unit_sha256 IS NULL
             AND media_member IS NOT NULL AND media_sha256 IS NOT NULL)
        )
    );
    CREATE INDEX issue_locator ON review_issues(source_key,ocr_page,unit_ordinal);
    CREATE INDEX issue_risk_type ON review_issues(risk_flag);
    CREATE TABLE review_events (
        issue_key TEXT NOT NULL REFERENCES review_issues(issue_key),
        reviewer_id TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('source_visual','scientific')),
        verdict TEXT NOT NULL CHECK(verdict IN ('pass','reject')),
        original_checked INTEGER NOT NULL CHECK(original_checked IN (0,1)),
        reasoning TEXT NOT NULL CHECK(length(trim(reasoning))>=30),
        PRIMARY KEY(issue_key,reviewer_id),
        UNIQUE(issue_key,phase)
    );
    CREATE VIEW review_progress AS
        SELECT i.issue_key,i.source_key,i.risk_flag,
        CASE WHEN EXISTS (
            SELECT 1 FROM review_events r
            WHERE r.issue_key=i.issue_key AND r.verdict='reject'
        ) THEN 'rejected_requires_repair'
        WHEN (
            SELECT count(*) FROM review_events r
            WHERE r.issue_key=i.issue_key AND r.verdict='pass'
              AND r.original_checked=1
        )=2
        THEN 'two_independent_checks_recorded_not_scientific_approval'
        ELSE 'pending' END AS review_status
        FROM review_issues i;
    """)


def build(directory, output, *, lock_path=LOCK):
    """Rehash all originals, create queue, verify readback before publishing."""
    items = preflight(directory, lock_path=lock_path)
    target = Path(output)
    if target.exists() or target.is_symlink():
        raise FileExistsError("Review queue already exists; refuse overwrite")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = target.with_name(target.name + ".partial")
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    counts = Counter()
    per_source = {}
    try:
        with closing(sqlite3.connect(partial)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            _create(conn)
            for item in items:
                data = prepare_source(item["path"], item["metadata"])
                if data["sha256"] != item["sha256"]:
                    raise ValueError("Locked original changed during review export")
                media = (
                    item["exam_media_inventory"]["media"]
                    if item["exam_media_inventory"] else []
                )
                conn.execute(
                    "INSERT INTO original_sources VALUES(?,?,?,?,?)",
                    (item["key"], item["sha256"], len(data["pages"]),
                     len(data["rows"]), len(media)),
                )
                for n, unit in enumerate(data["rows"], start=1):
                    checksum = sha256(unit["text"].encode("utf-8")).hexdigest()
                    for risk in sorted(set(unit["quality_flags"])):
                        conn.execute(
                            "INSERT INTO review_issues("
                            "issue_key,source_key,source_sha256,unit_ordinal,"
                            "ocr_page,unit_sha256,risk_flag) VALUES(?,?,?,?,?,?,?)",
                            (f'{item["key"]}:{n}:{risk}', item["key"],
                             item["sha256"], n, unit["ocr_global_page"],
                             checksum, risk),
                        )
                        counts[risk] += 1
                for image in media:
                    conn.execute(
                        "INSERT INTO review_issues("
                        "issue_key,source_key,source_sha256,media_member,"
                        "media_sha256,risk_flag) VALUES(?,?,?,?,?,?)",
                        (f'{item["key"]}:media:{image["member"]}',
                         item["key"],item["sha256"],image["member"],
                         image["sha256"],"unverified_embedded_media"),
                    )
                    counts["unverified_embedded_media"] += 1
                per_source[item["key"]] = conn.execute(
                    "SELECT count(*) FROM review_issues WHERE source_key=?",
                    (item["key"],),
                ).fetchone()[0]
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Private issue queue integrity failed")
            conn.commit()
        with closing(sqlite3.connect(f"file:{partial}?mode=ro", uri=True)) as check:
            check.row_factory = sqlite3.Row
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Private queue postcommit integrity failed")
            recorded = {
                x["source_key"]: x for x in
                check.execute("SELECT * FROM original_sources")
            }
            for item in items:
                row = recorded.get(item["key"])
                media = (
                    len(item["exam_media_inventory"]["media"])
                    if item["exam_media_inventory"] else 0
                )
                if row is None or (
                    row["source_sha256"],row["ocr_pages"],
                    row["text_units"],row["embedded_media"]
                ) != (
                    item["sha256"],item["ocr_page_markers"],
                    item["ocr_units"],media
                ):
                    raise ValueError("Postcommit queue source identity mismatch")
            issues = check.execute(
                "SELECT count(*) FROM review_issues"
            ).fetchone()[0]
            pending = check.execute(
                "SELECT count(*) FROM review_progress WHERE review_status='pending'"
            ).fetchone()[0]
            if issues != sum(counts.values()) or issues != pending:
                raise ValueError("Review queue has incorrectly approved issues")
            if check.execute("SELECT count(*) FROM review_events").fetchone()[0]:
                raise ValueError("Synthetic verification cannot approve sources")
        # Hard-link publication refuses to replace a concurrently created file.
        os.link(partial, target)
        partial.unlink()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {
        "source_files": len(items),
        "original_sha256_verified": True,
        "ocr_page_markers": sum(x["ocr_page_markers"] for x in items),
        "text_units": sum(x["ocr_units"] for x in items),
        "embedded_media": sum(
            len(x["exam_media_inventory"]["media"])
            if x["exam_media_inventory"] else 0 for x in items
        ),
        "pending_review_issue_count": sum(counts.values()),
        "pending_by_risk": dict(sorted(counts.items())),
        "pending_by_source": dict(sorted(per_source.items())),
        "postcommit_queue_readback_verified": True,
        "scientifically_verified": False,
        "qa_postgresql_imported": False,
        "independent_ai_reviews_completed": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    if Path(args.report).exists():
        parser.error("Report already exists")
    result = build(args.directory, args.output)
    private_json(args.report, result)
    print("six_source_pending_review_issues_readback_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
