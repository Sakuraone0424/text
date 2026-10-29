"""Private six-source searchable OCR/DOCX index; no PostgreSQL/AI needed.

All six SHA-256 source identities are locked before any indexing. The *same*
parser as the source importer supplies chapter, OCR page, DOCX paragraph
coordinates and uncertainty flags. Text stays in a private local 0600 SQLite
file; reports contain counts only. This is NOT scientific or QA DB approval.
"""
import argparse
from contextlib import closing
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from app.collection_seed import preflight, LOCK
from app.source_policy import COLLECTION
from app.sources import prepare_source
from app.source_intake import private_json


def _schema(conn):
    conn.executescript("""
    CREATE TABLE sources (
        source_key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
        source_kind TEXT NOT NULL, subject TEXT NOT NULL,
        raw_bytes INTEGER NOT NULL, ocr_pages INTEGER NOT NULL,
        embedded_media_objects INTEGER NOT NULL,
        scientifically_approved INTEGER NOT NULL DEFAULT 0
            CHECK(scientifically_approved=0)
    );
    CREATE TABLE units (
        id INTEGER PRIMARY KEY,
        source_key TEXT NOT NULL REFERENCES sources(source_key),
        ordinal INTEGER NOT NULL, ocr_page INTEGER,
        chapter TEXT, section TEXT,
        text TEXT NOT NULL, text_sha256 TEXT NOT NULL, risk_flags_json TEXT NOT NULL,
        scientifically_approved INTEGER NOT NULL DEFAULT 0
            CHECK(scientifically_approved=0),
        UNIQUE(source_key,ordinal)
    );
    CREATE INDEX source_ocr_location ON units(source_key,ocr_page,ordinal);
    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE VIRTUAL TABLE fulltext
        USING fts5(text,content='units',content_rowid='id',tokenize='trigram');
    """)


def build(directory, target, *, lock_path=LOCK):
    """Fail on source changes or existing index; reopen and verify after commit."""
    items = preflight(directory, lock_path=lock_path)
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError("Private QA index already exists; never overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(target.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    summary = []
    total = 0
    try:
        with closing(sqlite3.connect(temporary)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            _schema(conn)
            for item in items:
                parsed = prepare_source(item["path"], item["metadata"])
                if parsed["sha256"] != item["sha256"]:
                    raise ValueError("Source changed between preflight and indexing")
                media = (
                    item["exam_media_inventory"]["media_objects"]
                    if item["exam_media_inventory"] else 0
                )
                source = next(
                    x for x in COLLECTION["files"] if x["key"] == item["key"]
                )
                conn.execute(
                    "INSERT INTO sources(source_key,source_sha256,source_kind,"
                    "subject,raw_bytes,ocr_pages,embedded_media_objects)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (item["key"],item["sha256"],item["source_kind"],
                     source["subject"],item["bytes"],item["ocr_page_markers"],media),
                )
                counts = Counter()
                for ordinal, unit in enumerate(parsed["rows"], 1):
                    if not unit["text"].strip():
                        raise ValueError("Empty source unit cannot be indexed")
                    counts.update(unit["quality_flags"])
                    conn.execute(
                        "INSERT INTO units(source_key,ordinal,ocr_page,chapter,"
                        "section,text,text_sha256,risk_flags_json)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (
                            item["key"],ordinal,unit["ocr_global_page"],
                            unit["chapter"],unit["section"],unit["text"],
                            sha256(unit["text"].encode("utf-8")).hexdigest(),
                            json.dumps(unit["quality_flags"],ensure_ascii=False),
                        ),
                    )
                total += len(parsed["rows"])
                summary.append({
                    "key":item["key"],"source_sha256":item["sha256"],
                    "ocr_pages":len(parsed["pages"]),"unit_count":len(parsed["rows"]),
                    "media_count":media,
                    "unit_risk_occurrences":dict(sorted(counts.items())),
                })
            conn.execute(
                "INSERT INTO metadata(key,value) VALUES('index_complete','1')"
            )
            conn.execute("INSERT INTO fulltext(fulltext) VALUES('rebuild')")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite precommit integrity check failed")
            conn.commit()
        with closing(sqlite3.connect(f"file:{temporary}?mode=ro",uri=True)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite postcommit integrity check failed")
            persisted = {
                x[0]:(x[1],x[2],x[3],x[4])
                for x in check.execute(
                    "SELECT source_key,source_sha256,ocr_pages,"
                    "embedded_media_objects,raw_bytes FROM sources"
                )
            }
            for item in items:
                media = (
                    item["exam_media_inventory"]["media_objects"]
                    if item["exam_media_inventory"] else 0
                )
                if persisted.get(item["key"]) != (
                    item["sha256"],item["ocr_page_markers"],media,item["bytes"]
                ):
                    raise ValueError("Source index postcommit identity mismatch")
            if check.execute(
                "SELECT value FROM metadata WHERE key='index_complete'"
            ).fetchone() != ("1",):
                raise ValueError("Private source index completion marker missing")
            if check.execute("SELECT COUNT(*) FROM units").fetchone()[0] != total:
                raise ValueError("Source unit index readback mismatch")
        os.link(temporary, target)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status":"six_pinned_sources_private_offline_index",
        "source_files":6,"ocr_pages":sum(x["ocr_pages"] for x in summary),
        "media_objects":sum(x["media_count"] for x in summary),
        "unit_count":total,"postcommit_sqlite_readback_verified":True,
        "postgresql_source_imported":False,"scientific_answers_verified":False,
        "independent_ai_rounds_completed":False,"sources":summary,
    }


def search(index_path, phrase, *, limit=20):
    """Local exact-phrase FTS/trigram retrieval, never certifies the answer."""
    if not isinstance(phrase,str) or not 2 <= len(phrase.strip()) <= 256:
        raise ValueError("Search phrase must contain at least two characters")
    if not 1 <= limit <= 100:
        raise ValueError("Invalid search limit")
    phrase = phrase.strip()
    with closing(sqlite3.connect(f"file:{Path(index_path)}?mode=ro",uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if len(phrase) >= 3:
            sql = (
                "SELECT u.source_key,u.ordinal,u.ocr_page,u.chapter,u.section,"
                "u.text,u.risk_flags_json FROM fulltext f "
                "JOIN units u ON u.id=f.rowid WHERE fulltext MATCH ? "
                "AND instr(lower(u.text),lower(?))>0 ORDER BY u.source_key,u.id LIMIT ?"
            )
            rows = conn.execute(sql, ('"'+phrase.replace('"','""')+'"',phrase,limit))
        else:
            rows = conn.execute(
                "SELECT source_key,ordinal,ocr_page,chapter,section,text,"
                "risk_flags_json FROM units WHERE instr(lower(text),lower(?))>0 "
                "ORDER BY source_key,id LIMIT ?",(phrase,limit)
            )
        return [dict(row) for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True)
    parser.add_argument("--db",required=True)
    parser.add_argument("--report",required=True)
    args = parser.parse_args(argv)
    if Path(args.report).exists():
        parser.error("Private report already exists")
    result = build(args.directory,args.db)
    private_json(args.report,result)
    print("private_six_source_index_postcommit_verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
