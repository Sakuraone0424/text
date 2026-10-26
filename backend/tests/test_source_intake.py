"""Synthetic format fixtures only, in isolated rollback schemas; no private textbooks."""

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app import sources, source_files as files, source_intake as intake, exams, quiz_v2


def pdf(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(100, 100)
    writer.write(path)
    return path


def fixture(tmp_path, counts=(2, 2), *, physical=None):
    text = tmp_path / "synthetic-ocr.txt"
    text.write_text(
        "\n".join(
            f"===== page-{i:03} =====\n第一章 合成测试\nSynthetic paragraph {i} 年份2024并非真题证明\n"
            for i in range(1, sum(counts) + 1)
        ),
        encoding="utf-8",
    )
    meta = dict(
        file_id="synthetic-format-only",
        title="SYNTHETIC NOT A TEXTBOOK",
        authors="Synthetic test author",
        edition="Synthetic edition",
        subject="cell_biology",
        expected_pages=sum(counts),
    )
    if physical:
        meta["pdf_pages"] = physical
    specs = []
    offset = 0
    for n, count in enumerate(counts, 1):
        path = pdf(tmp_path / f"part-{n}.pdf", count)
        specs.append(
            dict(
                file_id=f"synthetic-part-{n}",
                path=str(path),
                page_offset=offset,
                expected_pages=count,
                sha256=files.sha(path.read_bytes()),
            )
        )
        offset += count
    return text, meta, specs


@pytest.mark.parametrize(
    "marker",
    [
        "===== page-001 =====",
        "===== page-01 =====",
        r"\===== page-001 =====",
        "===== PAGE-001 =====",
    ],
)
def test_actual_ocr_delimiters_are_standalone_pages(marker):
    rows = sources.parse(
        f"{marker}\n第一章 合成\nSynthetic paragraph\n===== page-02 =====\nOther paragraph",
        {"expected_pages": 2},
    )
    assert {r["ocr_page"] for r in rows} == {1, 2}
    assert all(r["ocr_global_page"] == r["pdf_page"] for r in rows)
    assert all("=====" not in r["text"] for r in rows)


def test_actual_385_format_maps_ocr_volumes_physical_parts_and_printed_separately(
    tmp_path,
):
    path, meta, specs = fixture(tmp_path, (192, 193))
    path.write_text(
        "\n".join(
            f"===== page-{i:03} =====\nSynthetic volume {v} page {i}"
            for v, length in enumerate((192, 193), 1)
            for i in range(1, length + 1)
        )
    )
    meta.update(
        volume_offsets=[0, 192],
        volume_pages=[192, 193],
        printed_pages={"193": "187", "385": "379"},
    )
    prepared = sources.prepare_source(path, meta, original_parts=specs)
    report = intake.page_report(prepared)
    assert report["observed_ocr_pages"] == 385 and report["unpaged_paragraphs"] == 0
    assert (
        report["original_pdf_pages"] == 385
        and not report["unmapped_original_physical_pages"]
    )
    second = prepared["pages"][192]
    assert (
        second["ocr_global_page"],
        second["ocr_page"],
        second["volume"],
        second["pdf_page"],
        second["original_part"],
        second["original_page"],
        second["printed_page"],
    ) == (193, 1, 2, 193, 2, 1, "187")
    assert prepared["pages"][-1]["original_page"] == 193
    assert prepared["pages"][0]["printed_page"] is None
    assert len(report["unknown_printed_pages"]) == 383
    assert not report["verified"]


@pytest.mark.parametrize(
    "text,metadata,error",
    [
        ("===== page-0x1 =====\ntext", {}, "Malformed"),
        ("===== page-000 =====\ntext", {}, "one"),
        ("preface\n===== page-001 =====\ntext", {}, "Unpaged"),
        ("===== page-001 =====\ntext\n===== page-001 =====\nother", {}, "reset"),
        (
            "===== page-001 =====\ntext\n===== page-003 =====\nother",
            {"expected_pages": 3},
            "coverage",
        ),
        (
            "===== page-001 =====\ntext\n===== page-001 =====\nother",
            {"volume_offsets": [0, 192], "volume_pages": [192, 193]},
            "before",
        ),
        (
            "===== page-001 =====\ntext",
            {"volume_offsets": [0, 191], "volume_pages": [192, 193]},
            "contiguous",
        ),
        ("===== page-001 =====\ntext", {"pdf_pages": {"2": 1}}, "unobserved"),
        ("===== page-001 =====\ntext", {"printed_pages": {"2": "iii"}}, "unobserved"),
        ("===== page-001 =====\ntext", {"printed_pages": {"1": 1}}, "labels"),
        ("===== page-001 =====\ntext", {"expected_pages": True}, "expected_pages"),
        (
            "===== page-001 =====\ntext\n===== page-002 =====\nother",
            {"pdf_pages": {"2": 1}},
            "same physical",
        ),
    ],
)
def test_bad_page_configuration_is_rejected_before_import(text, metadata, error):
    with pytest.raises(ValueError, match=error):
        sources.parse(text, metadata)


def test_blank_pages_are_retained_in_coverage_report_and_missing_markers_fail(tmp_path):
    path, meta, specs = fixture(tmp_path, (3,))
    path.write_text(
        "===== page-01 =====\nSynthetic\n===== page-02 =====\n\n===== page-03 =====\nFinal"
    )
    report = intake.page_report(
        sources.prepare_source(path, meta, original_parts=specs)
    )
    assert report["blank_ocr_pages"] == [2] and len(report["pages"]) == 3
    assert report["pages"][1]["paragraph_count"] == 0
    path.write_text("all OCR body without any page markers")
    with pytest.raises(ValueError, match="coverage"):
        sources.prepare_source(path, meta, original_parts=specs)


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda s: s[1].update(page_offset=1), "offsets"),
        (lambda s: s[1].update(page_offset=3), "offsets"),
        (lambda s: s[1].update(file_id=s[0]["file_id"]), "unique"),
        (lambda s: s[1].update(expected_pages=3), "actual page"),
        (lambda s: s[1].update(sha256="0" * 64), "checksum"),
    ],
)
def test_bad_original_parts_cannot_be_imported(tmp_path, change, error):
    path, meta, specs = fixture(tmp_path)
    change(specs)
    with pytest.raises(ValueError, match=error):
        sources.prepare_source(path, meta, original_parts=specs)


def test_original_pdf_bytes_format_encryption_count_and_limits(tmp_path, monkeypatch):
    path = tmp_path / "oversize.pdf"
    with path.open("wb") as stream:
        stream.truncate(files.MAX_PART_BYTES + 1)
    with pytest.raises(ValueError, match="limit"):
        files.load_parts(
            [dict(path=path, file_id="oversize", page_offset=0, expected_pages=1)]
        )
    path.write_bytes(b"not a PDF")
    with pytest.raises(ValueError, match="actual PDF"):
        files.load_parts(
            [dict(path=path, file_id="fake", page_offset=0, expected_pages=1)]
        )
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("private")
    writer.write(path)
    with pytest.raises(ValueError, match="encrypted"):
        files.load_parts(
            [dict(path=path, file_id="encrypted", page_offset=0, expected_pages=1)]
        )
    _, _, specs = fixture(tmp_path)
    monkeypatch.setattr(
        files, "MAX_TOTAL_BYTES", sum(Path(s["path"]).stat().st_size for s in specs) - 1
    )
    with pytest.raises(ValueError, match="limit"):
        files.load_parts(specs)
    with pytest.raises(ValueError, match="1–16"):
        files.load_parts(specs * 9)
    monkeypatch.setattr(files, "MAX_PART_PAGES", 1)
    with pytest.raises(ValueError, match="bounded"):
        files.load_parts(specs)


def test_single_original_retains_100_mib_limit_before_allocation(tmp_path):
    path, meta, _ = fixture(tmp_path, (1,))
    original = tmp_path / "huge.pdf"
    with original.open("wb") as stream:
        stream.truncate(100 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="100 MiB"):
        sources.prepare_source(path, meta, original)


def test_mapping_to_part_local_page_handles_front_matter_and_out_of_range(tmp_path):
    path, meta, specs = fixture(tmp_path)
    path.write_text(
        "===== page-01 =====\nSynthetic one\n===== page-02 =====\nSynthetic two"
    )
    meta.update(
        expected_pages=2, pdf_pages={"1": 2, "2": 3}, printed_pages={"2": "i", "3": "1"}
    )
    prepared = sources.prepare_source(path, meta, original_parts=specs)
    assert [
        (p["pdf_page"], p["original_part"], p["original_page"], p["printed_page"])
        for p in prepared["pages"]
    ] == [(2, 1, 2, "i"), (3, 2, 1, "1")]
    assert intake.page_report(prepared)["unmapped_original_physical_pages"] == [1, 4]
    meta["pdf_pages"]["2"] = 5
    meta["printed_pages"] = {}
    with pytest.raises(ValueError, match="outside"):
        sources.prepare_source(path, meta, original_parts=specs)


def test_multipart_original_cannot_be_bypassed_by_fake_metadata(tmp_path):
    path, meta, specs = fixture(tmp_path)
    meta.update(
        original_pdf_pages=999, original_kind="pdf_parts", original_parts_manifest=[]
    )
    prepared = sources.prepare_source(path, meta, original_parts=specs)
    assert prepared["metadata"]["original_pdf_pages"] == 4
    assert prepared["metadata"]["original_parts_manifest"][1]["pdf_pages"] == 2
    with pytest.raises(ValueError, match="never both"):
        sources.prepare_source(path, meta, specs[0]["path"], specs)


def test_multipart_storage_idempotence_attachment_and_unverified_default(db, tmp_path):
    path, meta, specs = fixture(tmp_path)
    doc = sources.import_source(db, path, meta)
    before = db.execute(
        "SELECT id FROM source_units WHERE document_id=%s ORDER BY ordinal", (doc,)
    ).fetchall()
    assert sources.import_source(db, path, meta, original_parts=specs) == doc
    assert sources.import_source(db, path, meta) == doc
    assert sources.import_source(db, path, meta, original_parts=specs) == doc
    assert (
        db.execute(
            "SELECT id FROM source_units WHERE document_id=%s ORDER BY ordinal", (doc,)
        ).fetchall()
        == before
    )
    header = db.execute("SELECT * FROM source_documents WHERE id=%s", (doc,)).fetchone()
    assert header["original_content"] is None
    assert header["original_sha256"] == files.manifest_sha(files.load_parts(specs))
    assert (
        db.execute("SELECT COUNT(*) AS n FROM source_original_parts").fetchone()["n"]
        == 2
    )
    assert db.execute("SELECT COUNT(*) AS n FROM source_pages").fetchone()["n"] == 4
    assert not db.execute("SELECT 1 FROM source_units WHERE verified").fetchone()
    assert (
        db.execute("SELECT COUNT(*) AS n FROM reviewed_questions").fetchone()["n"] == 0
    )
    report = intake.database_report(db, doc)
    assert (
        report["status"] == "integrity_checked_not_content_verified"
        and not report["issues"]
    )
    assert report["knowledge_links"] == 0 and report["verified_units"] == 0
    from psycopg.errors import RaiseException

    with pytest.raises(RaiseException), db.transaction():
        db.execute("UPDATE source_original_parts SET sha256=%s", ("0" * 64,))
    db.execute("UPDATE source_pages SET original_page=1 WHERE ocr_global_page=4")
    assert any(
        r["issue"] == "stored_page_part_mapping_mismatch"
        for r in intake.database_report(db, doc)["issues"]
    )


def test_multipart_evidence_still_requires_review_and_exact_mapping(
    db, tmp_path, question_factory, passing_audit
):
    path, meta, specs = fixture(tmp_path)
    doc = sources.import_source(db, path, meta, original_parts=specs)
    unit = db.execute(
        "SELECT * FROM source_units WHERE document_id=%s AND ocr_global_page=3 ORDER BY ordinal DESC LIMIT 1",
        (doc,),
    ).fetchone()
    question = question_factory("cell_biology")
    data = question.model_dump()
    data["evidence"] = [
        dict(
            source_unit_id=unit["id"],
            textbook=meta["title"],
            authors=meta["authors"],
            edition=meta["edition"],
            isbn=None,
            chapter=unit["chapter"],
            locator="PDF:3",
            excerpt="Synthetic paragraph 3",
        )
    ]
    assert "unverified_or_missing_textbook_unit" in sources.evidence_issues(db, data)
    sources.verify_unit(
        db,
        unit["id"],
        "synthetic-test-reviewer",
        "Synthetic PDF mapping reviewed only inside rollback-only automated test",
    )
    assert set(sources.evidence_issues(db, data)) == {
        "outside_current_six_file_collection_or_superseded",
        "missing_current_textbook_evidence",
    }
    assert set(sources.evidence_issues(db, data, verify_original=False)) == {
        "outside_current_six_file_collection_or_superseded",
        "missing_current_textbook_evidence",
    }
    question = type(question).model_validate(data)
    qid = quiz_v2.create_question(db, question)["id"]
    assert not quiz_v2.review_question(db, qid, passing_audit)["passed"]
    with pytest.raises(ValueError):
        quiz_v2.publish_question(db, qid)
    db.execute("UPDATE source_units SET original_page=2 WHERE id=%s", (unit["id"],))
    assert "textbook_page_mismatch" in sources.evidence_issues(db, data)
    assert "textbook_page_mismatch" in sources.evidence_issues(
        db, data, verify_original=False
    )
    with pytest.raises(ValueError, match="mapping"):
        sources.verify_unit(
            db,
            unit["id"],
            "test",
            "Synthetic mapping failure should block renewed verification",
        )


def test_multipart_original_is_recognized_by_exam_coverage_and_style_gate(
    db, tmp_path, question_factory
):
    path, meta, specs = fixture(tmp_path)
    doc = sources.import_source(db, path, meta, original_parts=specs)
    unit = db.execute(
        "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal DESC LIMIT 1",
        (doc,),
    ).fetchone()
    record = dict(
        source_unit_id=unit["id"],
        original_question_id="synthetic-2024",
        subject="cell_biology",
        year=2024,
        question_type="single_choice",
        stem="Synthetic paragraph 4",
        category="mechanism",
        original_verified=True,
        scope_key="synthetic-parts",
        source_kind="real_exam",
    )
    with pytest.raises(ValueError, match="verified before"):
        exams.add(db, record)
    sources.verify_unit(
        db,
        unit["id"],
        "test",
        "Synthetic original checked inside this isolated rollback test",
    )
    exams.add(db, record)
    assert not next(
        r
        for r in exams.coverage(db)
        if r["subject"] == "cell_biology" and r["year"] == 2024
    )[
        "original_acquired"
    ]  # Optional PDF tool data is outside the active six-file bank.
    assert not any(
        r["trusted"] and r["source_kind"] == "real_exam"
        for r in exams.trusted_records(
            db, db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        )
        if r["source_document_id"] == doc
    )
    sources.verify_unit(
        db,
        unit["id"],
        "test",
        "Synthetic exam original checked only inside this rollback test",
    )
    exams.register_scope(
        db,
        dict(
            scope_key="synthetic-parts",
            subject="cell_biology",
            paper_code="test",
            syllabus="synthetic",
            source_unit_id=unit["id"],
            reviewer="test",
            note="Synthetic applicability only for automated tests",
        ),
    )
    record_id = db.execute(
        "SELECT id FROM exam_records WHERE source_unit_id=%s", (unit["id"],)
    ).fetchone()["id"]
    exams.classify(
        db,
        record_id,
        dict(
            scope_key="synthetic-parts",
            source_kind="real_exam",
            mapping_verified=False,
            tag_contributions={},
            primary_tags=[],
            cognitive_level=None,
            canonical_item_id=None,
        ),
        "test",
        "Synthetic classification only for this rollback test",
    )
    question = question_factory("cell_biology").model_dump()
    question["style_reference_ids"] = [
        db.execute(
            "SELECT id FROM exam_records WHERE source_unit_id=%s", (unit["id"],)
        ).fetchone()["id"]
    ]
    assert exams.style_issues(db, question) == [
        "unverified_or_mismatched_exam_style_source"
    ]


def cli_files(tmp_path):
    path, meta, specs = fixture(tmp_path)
    meta["physical_mapping"] = "identity_confirmed"  # Known synthetic PDFs only.
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps(meta))
    manifest = tmp_path / "parts.json"
    manifest.write_text(
        json.dumps([{**s, "path": Path(s["path"]).name} for s in specs])
    )
    report = tmp_path / "reports" / "coverage.json"
    return path, meta_path, manifest, report


def test_private_preflight_writes_no_body_or_public_data_and_never_requires_db(
    tmp_path, monkeypatch
):
    path, metadata, manifest, report = cli_files(tmp_path)
    monkeypatch.delenv("SOURCE_STAGING_DATABASE_URL", raising=False)
    args = [
        "check",
        str(path),
        "--metadata",
        str(metadata),
        "--original-parts",
        str(manifest),
        "--report",
        str(report),
    ]
    assert intake.main(args) == 0
    value = json.loads(report.read_text())
    assert value["observed_ocr_pages"] == 4 and not value["verified"]
    assert value["year_mentions_not_verified_exam_coverage"] == {"2024": 4}
    assert "Synthetic paragraph" not in report.read_text()
    assert report.stat().st_mode & 0o777 == 0o600
    assert intake.main(args) == 2  # Never overwrite an earlier report.


def test_private_preflight_rejects_missing_pages_without_writing_db(tmp_path):
    path, metadata, manifest, report = cli_files(tmp_path)
    path.write_text("===== page-01 =====\nSynthetic only one page")
    assert (
        intake.main(
            [
                "check",
                str(path),
                "--metadata",
                str(metadata),
                "--original-parts",
                str(manifest),
                "--report",
                str(report),
            ]
        )
        == 2
    )
    assert json.loads(report.read_text())["status"] == "rejected"


@pytest.mark.parametrize(
    "schema,target",
    [
        ("public", "development"),
        ("source_staging_safe;DROP", "staging"),
        ("source_staging_safe", "production"),
    ],
)
def test_private_import_database_target_guards(schema, target, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-use-default")
    with pytest.raises(ValueError, match="forbidden"):
        with intake.isolated_db(schema, target):
            pytest.fail("Unsafe target accepted")


def test_private_import_does_not_use_default_database_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-use-default")
    monkeypatch.delenv("SOURCE_STAGING_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="SOURCE_STAGING_DATABASE_URL"):
        with intake.isolated_db("source_staging_test", "development"):
            pytest.fail("Implicit database target accepted")


def test_cli_import_audit_and_annual_report_remain_unverified(
    db, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    @contextmanager
    def test_target(schema, target, **kwargs):
        assert schema == "source_staging_test" and target == "development"
        with db.transaction():
            yield db

    monkeypatch.setattr(intake, "isolated_db", test_target)
    path, metadata, manifest, report = cli_files(tmp_path)
    target = ["--schema", "source_staging_test", "--target", "development"]
    assert (
        intake.main(
            [
                "import",
                str(path),
                "--metadata",
                str(metadata),
                "--original-parts",
                str(manifest),
                "--report",
                str(report),
            ]
            + target
        )
        == 0
    )
    imported = json.loads(report.read_text())
    assert imported["database_audit"]["verified_units"] == 0
    doc = imported["document_id"]
    audit = tmp_path / "audit.json"
    assert (
        intake.main(
            ["audit", "--document-id", str(doc), "--report", str(audit)] + target
        )
        == 0
    )
    assert not json.loads(audit.read_text())["issues"]
    assert sources.search(db, "Synthetic paragraph 3")[0]["original_part"] == 2
    annual = tmp_path / "years.json"
    assert intake.main(["exam-coverage", "--report", str(annual)] + target) == 0
    years = json.loads(annual.read_text())["years"]
    assert len(years) == 52
    assert all(r["status"] == "original_not_acquired" for r in years)
    assert db.execute("SELECT COUNT(*) AS n FROM papers_v2").fetchone()["n"] == 0
    db.execute(
        "UPDATE source_units SET text='Tampered synthetic paragraph' WHERE document_id=%s",
        (doc,),
    )
    failed = tmp_path / "audit-failed.json"
    assert (
        intake.main(
            ["audit", "--document-id", str(doc), "--report", str(failed)] + target
        )
        == 2
    )
    assert any(
        i["issue"] == "stored_text_or_index_checksum_mismatch"
        for i in json.loads(failed.read_text())["issues"]
    )


def test_cli_import_failure_rolls_back_document_and_parts(db, tmp_path, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def test_target(*args, **kwargs):
        with db.transaction():
            yield db

    monkeypatch.setattr(intake, "isolated_db", test_target)
    path, metadata, manifest, report = cli_files(tmp_path)

    def cannot_write(*args):
        raise OSError("Synthetic report storage failure")

    monkeypatch.setattr(intake, "private_json", cannot_write)
    assert (
        intake.main(
            [
                "import",
                str(path),
                "--metadata",
                str(metadata),
                "--original-parts",
                str(manifest),
                "--report",
                str(report),
                "--schema",
                "source_staging_test",
                "--target",
                "development",
            ]
        )
        == 2
    )
    assert db.execute("SELECT COUNT(*) AS n FROM source_documents").fetchone()["n"] == 0
    assert (
        db.execute("SELECT COUNT(*) AS n FROM source_original_parts").fetchone()["n"]
        == 0
    )


def test_cli_file_change_between_preflight_and_insert_is_rejected(
    db, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    @contextmanager
    def test_target(*args, **kwargs):
        with db.transaction():
            yield db

    monkeypatch.setattr(intake, "isolated_db", test_target)
    path, metadata, manifest, report = cli_files(tmp_path)
    original = sources.import_source

    def changed(*args, **kwargs):
        path.write_text(
            path.read_text().replace("Synthetic paragraph 4", "Different paragraph 4")
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(sources, "import_source", changed)
    assert (
        intake.main(
            [
                "import",
                str(path),
                "--metadata",
                str(metadata),
                "--original-parts",
                str(manifest),
                "--report",
                str(report),
                "--schema",
                "source_staging_test",
                "--target",
                "development",
            ]
        )
        == 2
    )
    assert "changed during import" in json.loads(report.read_text())["error"]
    assert db.execute("SELECT COUNT(*) AS n FROM source_documents").fetchone()["n"] == 0


def test_migration_010_preserves_existing_verified_single_source(raw_db, tmp_path):
    from migrate import apply_migrations, SQL_DIR

    previous = tmp_path / "previous-sql"
    previous.mkdir()
    for path in SQL_DIR.glob("*.sql"):
        if path.name < "010":
            (previous / path.name).write_bytes(path.read_bytes())
    apply_migrations(raw_db, previous)
    content = b"SYNTHETIC OLD ORIGINAL"
    from psycopg.types.json import Jsonb

    doc = raw_db.execute(
        "INSERT INTO source_documents(file_id,title,edition,subject,sha256,original_sha256,original_content,metadata) VALUES ('old','Synthetic','test','biochemistry',%s,%s,%s,%s) RETURNING id",
        (files.sha(content), files.sha(content), content, Jsonb({"authors": "test"})),
    ).fetchone()["id"]
    raw_db.execute(
        "INSERT INTO source_units(document_id,ordinal,chapter,section,text,verified,reviewer) VALUES (%s,1,'Synthetic chapter','','Synthetic old paragraph',true,'test')",
        (doc,),
    )
    header = raw_db.execute(
        "SELECT * FROM source_documents WHERE id=%s", (doc,)
    ).fetchone()
    unit = raw_db.execute(
        "SELECT * FROM source_units WHERE document_id=%s", (doc,)
    ).fetchone()
    apply_migrations(raw_db)
    assert (
        raw_db.execute("SELECT * FROM source_documents WHERE id=%s", (doc,)).fetchone()
        == header
    )
    migrated = raw_db.execute(
        "SELECT * FROM source_units WHERE document_id=%s", (doc,)
    ).fetchone()
    assert all(migrated[k] == v for k, v in unit.items())
    assert migrated["original_part"] is None and migrated["ocr_global_page"] is None
    assert not sources.original_issues(
        raw_db,
        {
            **migrated,
            **{
                k: header[k]
                for k in ("metadata", "original_content", "original_sha256")
            },
        },
    )
    apply_migrations(raw_db)
    assert (
        raw_db.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        == 11
    )


def test_multipart_manifest_change_and_mapped_part_omission_are_detected(db, tmp_path):
    from psycopg.types.json import Jsonb

    path, meta, specs = fixture(tmp_path)
    doc = sources.import_source(db, path, meta, original_parts=specs)
    unit = db.execute(
        "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal DESC LIMIT 1",
        (doc,),
    ).fetchone()
    sources.verify_unit(
        db,
        unit["id"],
        "test",
        "Synthetic part mapping checked only inside this rollback test",
    )
    with pytest.raises(ValueError, match="immutable"):
        sources.import_source(
            db, path, {**meta, "edition": "changed"}, original_parts=specs
        )
    # Owner-level database tampering must not make a stale manifest acceptable.
    metadata = db.execute(
        "SELECT metadata FROM source_documents WHERE id=%s", (doc,)
    ).fetchone()["metadata"]
    metadata["original_parts_manifest"] = metadata["original_parts_manifest"][:1]
    db.execute(
        "UPDATE source_documents SET metadata=%s WHERE id=%s", (Jsonb(metadata), doc)
    )
    row = {
        **unit,
        "metadata": metadata,
        "original_sha256": None,
        "original_content": None,
    }
    assert "missing_or_changed_original" in sources.original_issues(db, row)
    assert "missing_or_changed_original" in sources.original_issues(
        db, row, verify_hash=False
    )


def test_old_source_cli_requires_explicit_isolated_target_and_report(tmp_path):
    import subprocess, sys

    path, metadata, manifest, report = cli_files(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.sources",
            str(path),
            "--metadata",
            str(metadata),
            "--original-parts",
            str(manifest),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert (
        "--schema" in result.stderr
        and "--target" in result.stderr
        and "--report" in result.stderr
    )


def test_explicit_synthetic_intake_can_never_be_verified_as_real_textbook(db, tmp_path):
    path, meta, specs = fixture(tmp_path)
    meta["material_status"] = "synthetic_test"
    doc = sources.import_source(db, path, meta, original_parts=specs)
    unit = db.execute(
        "SELECT id FROM source_units WHERE document_id=%s LIMIT 1", (doc,)
    ).fetchone()["id"]
    with pytest.raises(ValueError, match="Synthetic"):
        sources.verify_unit(
            db,
            unit,
            "test",
            "Synthetic material must never become a real verified source",
        )
    assert not db.execute("SELECT 1 FROM source_units WHERE verified").fetchone()


def test_private_intake_does_not_assume_ocr_and_pdf_page_identity(tmp_path):
    path, metadata, manifest, report = cli_files(tmp_path)
    meta = json.loads(metadata.read_text())
    meta.pop("physical_mapping")
    metadata.write_text(json.dumps(meta))
    assert (
        intake.main(
            [
                "check",
                str(path),
                "--metadata",
                str(metadata),
                "--original-parts",
                str(manifest),
                "--report",
                str(report),
            ]
        )
        == 2
    )
    assert "Confirm OCR-to-physical" in json.loads(report.read_text())["error"]


def test_whole_book_audit_does_not_reread_all_original_bytes_per_paragraph(
    db, tmp_path
):
    path, meta, specs = fixture(tmp_path, (10, 10))
    doc = sources.import_source(db, path, meta, original_parts=specs)

    class BoundOriginalReads:
        byte_queries = 0

        def execute(self, query, *args, **kwargs):
            normalized = " ".join(query.lower().split())
            if "from source_original_parts" in normalized and (
                normalized.startswith("select * ")
                or "content" in normalized.partition(" from ")[0]
            ):
                self.byte_queries += 1
                assert self.byte_queries <= 1, (
                    "Entire original set reread for each paragraph"
                )
            return db.execute(query, *args, **kwargs)

    observed = BoundOriginalReads()
    report = intake.database_report(observed, doc)
    assert not report["issues"] and report["units"] == 20
    assert observed.byte_queries == 1
