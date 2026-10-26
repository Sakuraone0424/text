"""Synthetic-only reviewer actions, in rollback schemas; never clicks the real bank."""

from contextlib import contextmanager
from copy import deepcopy
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from psycopg.errors import RaiseException
from app import sources, candidate_review as svc, review_app
from test_ocr_only import material


@pytest.fixture
def packet(db, tmp_path):
    path, meta = material(
        tmp_path, content="Synthetic kinase evidence for a synthetic test only"
    )
    doc = sources.import_source(db, path, meta)
    unit = db.execute(
        "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal LIMIT 1",
        (doc,),
    ).fetchone()
    exam, emeta = material(tmp_path, "biochemistry_exams")
    examid = sources.import_source(db, exam, emeta)
    style = db.execute(
        "SELECT id FROM source_units WHERE document_id=%s AND text LIKE '1. Synthetic%%' ORDER BY ordinal LIMIT 1",
        (examid,),
    ).fetchone()["id"]
    checksum = db.execute(
        "SELECT sha256 FROM source_documents WHERE id=%s", (doc,)
    ).fetchone()["sha256"]
    return dict(
        candidate_key="synthetic-first-1",
        batch_key="synthetic-only",
        question=dict(
            subject="biochemistry",
            question_type="single_choice",
            stem="Synthetic question: which test-only option matches the synthetic evidence?",
            options=dict(
                A="Synthetic kinase",
                B="Synthetic other",
                C="Synthetic third",
                D="Synthetic fourth",
            ),
            answer="A",
            max_points=2,
            knowledge_tags=["synthetic/kinase"],
            rubric=[
                dict(
                    description="Select synthetic A for 2 points; other answers zero",
                    points=2,
                    knowledge_tags=["synthetic/kinase"],
                )
            ],
            evidence=[
                dict(
                    file_id=meta["file_id"],
                    source_sha256=checksum,
                    ocr_page=1,
                    paragraph_id=unit["id"],
                    source_unit_id=unit["id"],
                    textbook="Synthetic",
                    authors="Test",
                    edition="Test",
                    chapter=unit["chapter"],
                    locator=f"OCR:1:UNIT:{unit['id']}",
                    excerpt=unit["text"],
                )
            ],
        ),
        ai_analysis=dict(
            status="AI-assisted / pending human review",
            independent_human_review=False,
            axes={k: "Synthetic pending analysis only" for k in svc.AXES},
            uncertainties=["Synthetic evidence is not a real textbook verification"],
            rubric_evidence=[
                dict(
                    unit_ids=[unit["id"]],
                    explanation="Synthetic score point bound to test evidence only",
                )
            ],
        ),
        style_sources=[
            dict(
                unit_id=style,
                year=2023,
                relationship="Synthetic transcription format example only",
            )
        ],
    )


@pytest.fixture
def reviewer(db, packet, monkeypatch):
    svc.create_packet(db, packet)

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(review_app, "db", test_db)
    monkeypatch.setenv(
        "CANDIDATE_REVIEW_USERS",
        json.dumps({"test-reviewer": review_app.password_hash("synthetic-password")}),
    )
    monkeypatch.setenv(
        "CANDIDATE_SESSION_SECRET", "synthetic-session-secret-at-least-thirty-two"
    )
    monkeypatch.setenv("CANDIDATE_COOKIE_SECURE", "0")
    review_app.attempts.clear()
    with TestClient(review_app.app) as client:
        assert (
            client.post(
                "/api/login",
                json=dict(username="test-reviewer", password="synthetic-password"),
            ).status_code
            == 200
        )
        client.headers["x-review-csrf"] = client.get("/api/session").json()["csrf"]
        yield client


def decision(packet, **changes):
    payload = dict(
        revision_sha256=packet["revision_sha256"],
        expected_last_decision_id=0,
        action="approve_candidate",
        checks={k: True for k in svc.CHECKS},
        note="Synthetic human action only within a rollback test schema",
        human_attestation=True,
        request_id=str(uuid4()),
    )
    return {**payload, **changes}


def test_packet_requires_exact_evidence_and_is_immutable_idempotent(db, packet):
    first = svc.create_packet(db, packet)
    assert svc.create_packet(db, packet) == first
    row = svc.inspect(db, packet["candidate_key"])
    assert not row["formal_published"] and row["status"] == "pending_human_review"
    bad = deepcopy(packet)
    bad["question"]["evidence"][0]["excerpt"] = "invented private textbook quote"
    with pytest.raises(ValueError, match="quote"):
        svc.create_packet(db, bad)
    changed = deepcopy(packet)
    changed["question"]["stem"] += " Modified."
    with pytest.raises(ValueError, match="immutable"):
        svc.create_packet(db, changed)
    with pytest.raises(RaiseException), db.transaction():
        db.execute("UPDATE candidate_review_packets SET batch_key='changed'")


def test_ai_claims_or_missing_rubric_evidence_cannot_be_imported(db, packet):
    bad = deepcopy(packet)
    bad["ai_analysis"]["independent_human_review"] = True
    with pytest.raises(ValueError, match="pending AI"):
        svc.create_packet(db, bad)
    bad = deepcopy(packet)
    bad["ai_analysis"]["rubric_evidence"] = []
    with pytest.raises(ValueError, match="every scoring"):
        svc.create_packet(db, bad)


def test_real_human_action_is_separate_from_publication_and_source_approval(
    db, reviewer, packet
):
    row = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    payload = decision(row)
    first = reviewer.post(
        "/api/candidates/" + packet["candidate_key"] + "/decision", json=payload
    )
    assert first.status_code == 200 and first.json()["reviewer"] == "test-reviewer"
    again = reviewer.post(
        "/api/candidates/" + packet["candidate_key"] + "/decision", json=payload
    )
    assert again.json()["id"] == first.json()["id"]
    assert reviewer.get("/api/candidates").json()["human_candidate_approved"] == 1
    assert reviewer.get("/api/candidates").json()["formal_qualified"] == 0
    assert (
        db.execute(
            "SELECT count(*) AS n FROM reviewed_questions WHERE status='published'"
        ).fetchone()["n"]
        == 0
    )
    assert (
        db.execute("SELECT count(*) AS n FROM source_review_events").fetchone()["n"]
        == 0
    )
    assert db.execute("SELECT count(*) AS n FROM question_audits").fetchone()["n"] == 0
    assert (
        reviewer.post(
            "/api/candidates/" + packet["candidate_key"] + "/publish", json={}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("action", ["return", "revise"])
def test_return_or_revision_leaves_draft_pending(reviewer, packet, action):
    row = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    response = reviewer.post(
        "/api/candidates/" + packet["candidate_key"] + "/decision",
        json=decision(row, action=action, checks={k: False for k in svc.CHECKS}),
    )
    assert response.status_code == 200
    assert reviewer.get("/api/candidates").json()["pending"] == 1


def test_no_auth_no_csrf_no_attestation_no_passing_checks(reviewer, packet):
    with TestClient(review_app.app) as anonymous:
        for path in ["/api/candidates", "/api/preview", "/api/backup/download"]:
            assert anonymous.get(path).status_code == 401
    row = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    url = "/api/candidates/" + packet["candidate_key"] + "/decision"
    assert (
        reviewer.post(
            url, json=decision(row), headers={"x-review-csrf": "invalid"}
        ).status_code
        == 403
    )
    assert (
        reviewer.post(url, json=decision(row, human_attestation=False)).status_code
        == 409
    )
    assert (
        reviewer.post(
            url, json=decision(row, checks={k: False for k in svc.CHECKS})
        ).status_code
        == 409
    )
    assert (
        reviewer.post(url, json={**decision(row), "reviewer": "spoofed"}).status_code
        == 422
    )
    assert (
        reviewer.post(
            url, json=decision(row), headers={"origin": "https://untrusted.example"}
        ).status_code
        == 403
    )


def test_stale_concurrent_save_and_idempotency_conflict_rejected(reviewer, packet):
    row = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    body = decision(row)
    url = "/api/candidates/" + packet["candidate_key"] + "/decision"
    assert reviewer.post(url, json=body).status_code == 200
    assert reviewer.post(url, json=decision(row, action="revise")).status_code == 409
    assert (
        reviewer.post(
            url,
            json={
                **body,
                "note": "A different synthetic note with the same idempotency key",
            },
        ).status_code
        == 409
    )


def test_new_revision_never_inherits_approval_or_publishes_old_packet(
    db, reviewer, packet
):
    from app.quiz_v2 import publish_question

    original = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    assert (
        reviewer.post(
            "/api/candidates/" + packet["candidate_key"] + "/decision",
            json=decision(original),
        ).status_code
        == 200
    )
    revised = deepcopy(packet)
    revised.update(
        candidate_key="synthetic-first-2", supersedes=packet["candidate_key"]
    )
    revised["question"]["stem"] += " Clearer revised wording."
    qid = svc.create_packet(db, revised)
    assert svc.create_packet(db, revised) == qid
    stats = svc.summary(db)
    assert (
        stats["ai_assisted"] == 1
        and stats["total_ai_revision_packets"] == 2
        and stats["human_candidate_approved"] == 0
    )
    assert svc.inspect(db, revised["candidate_key"])["decisions"] == []
    assert (
        svc.inspect(db, packet["candidate_key"])["superseded_by"]
        == revised["candidate_key"]
    )
    with pytest.raises(ValueError, match="superseded"):
        publish_question(db, original["question_id"])
    assert (
        reviewer.post(
            "/api/candidates/" + packet["candidate_key"] + "/decision",
            json=decision(original, action="revise"),
        ).status_code
        == 409
    )


def test_modified_question_or_superseded_source_blocks_review(
    db, reviewer, packet, tmp_path
):
    row = reviewer.get("/api/candidates/" + packet["candidate_key"]).json()
    path, meta = material(
        tmp_path,
        content="A newer synthetic source version that must not inherit review",
    )
    sources.import_source(db, path, meta)
    assert (
        reviewer.post(
            "/api/candidates/" + packet["candidate_key"] + "/decision",
            json=decision(row),
        ).status_code
        == 409
    )


def test_preview_requires_all_formal_quotas_without_creating_papers(db, reviewer):
    assert reviewer.get("/api/preview").status_code == 409
    assert db.execute("SELECT count(*) AS n FROM papers_v2").fetchone()["n"] == 0


def test_source_context_stays_in_same_version_and_is_private(db, reviewer, packet):
    uid = packet["question"]["evidence"][0]["source_unit_id"]
    result = reviewer.get(f"/api/sources/{uid}/context")
    assert result.status_code == 200
    assert {r["source_sha256"] for r in result.json()["units"]} == {
        packet["question"]["evidence"][0]["source_sha256"]
    }
    assert result.json()["units"][0]["unit_id"] == uid
    with TestClient(review_app.app) as anonymous:
        assert anonymous.get(f"/api/sources/{uid}/context").status_code == 401


def test_complete_quota_preview_is_read_only(db, reviewer, packet):
    from app.question_quality import Question
    from app.training_spec import COUNTS, SUBJECTS, POINTS
    from psycopg.types.json import Jsonb

    # This fixture checks preview arithmetic/side effects only, not source approval.
    for subject in SUBJECTS:
        for kind, count in COUNTS.items():
            for i in range(count):
                q = deepcopy(packet["question"])
                q.update(
                    subject=subject,
                    question_type=kind,
                    max_points=POINTS[kind],
                    options={},
                    accepted_answers=[],
                    stem=f"Synthetic {subject} {kind} preview {i}",
                    answer="Synthetic reference",
                )
                if kind == "single_choice":
                    q.update(
                        options=dict(
                            A="Synthetic A",
                            B="Synthetic B",
                            C="Synthetic C",
                            D="Synthetic D",
                        ),
                        answer="A",
                    )
                if kind == "fill_blank":
                    q.update(
                        stem=q["stem"] + " ____",
                        accepted_answers=["Synthetic reference"],
                    )
                q["rubric"] = [
                    dict(
                        description="Synthetic scoring criterion only",
                        points=POINTS[kind],
                        knowledge_tags=q["knowledge_tags"],
                    )
                ]
                q = Question.model_validate(q).model_dump()
                qid = db.execute(
                    "INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id",
                    (Jsonb(q),),
                ).fetchone()["id"]
                db.execute(
                    "INSERT INTO candidate_review_packets(candidate_key,batch_key,question_id,revision_sha256,question_snapshot,ai_analysis,evidence_snapshot,style_snapshot) VALUES (%s,'synthetic-preview',%s,%s,%s,'{}','[]','[]')",
                    (f"preview-{subject}-{kind}-{i}", qid, "a" * 64, Jsonb(q)),
                )
    response = reviewer.get("/api/preview")
    assert response.status_code == 200
    result = response.json()
    assert (
        len(result["items"]) == 28
        and result["points"] == 150
        and result["duration_seconds"] == 3600
        and not result["published"]
    )
    assert db.execute("SELECT count(*) AS n FROM papers_v2").fetchone()["n"] == 0
    assert (
        db.execute("SELECT count(*) AS n FROM candidate_human_decisions").fetchone()[
            "n"
        ]
        == 0
    )


def test_backup_hash_and_private_path_rejection(tmp_path, monkeypatch):
    from app.review_backup import checksum, export
    import hashlib

    file = tmp_path / "synthetic.bin"
    file.write_bytes(b"synthetic" * 200000)
    assert checksum(file) == hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="new private"):
        export(tmp_path, "source_staging_test", "/not-used", "synthetic")
    with pytest.raises(ValueError, match="schema"):
        export(tmp_path / "unused", "public", "/not-used", "synthetic")
    monkeypatch.delenv("SOURCE_STAGING_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Explicit nonproduction"):
        export(tmp_path / "unused", "source_staging_test", "/not-used", "synthetic")


def test_authenticated_backup_refresh_retains_old_snapshot(
    reviewer, tmp_path, monkeypatch
):
    from app import review_backup

    root = tmp_path / "private-backups"
    root.mkdir()
    pointer = root / "current.json"
    old = root / "old.zip"
    old.write_bytes(b"synthetic old backup")
    monkeypatch.setenv("CANDIDATE_BACKUP_ROOT", str(root))
    monkeypatch.setenv("CANDIDATE_BACKUP_POINTER", str(pointer))
    monkeypatch.setenv("CANDIDATE_PG_BIN", "/synthetic/not-used")
    monkeypatch.setenv("CANDIDATE_REVIEW_SCHEMA", "source_staging_synthetic")
    monkeypatch.setenv("CANDIDATE_REVIEW_DATABASE_NAME", "synthetic")
    calls = []

    def fake_export(destination, schema, pg_bin, database):
        calls.append((schema, database))
        destination.mkdir()
        (destination / "new.zip").write_bytes(b"synthetic new backup")
        info = dict(
            filename="new.zip",
            sha256=review_backup.checksum(destination / "new.zip"),
            bytes=len(b"synthetic new backup"),
            snapshot_at="2026-10-26T00:00:00+00:00",
            restore_warning="Synthetic only",
        )
        (destination / "download-index.json").write_text(json.dumps(info))
        return info

    monkeypatch.setattr(review_backup, "export", fake_export)
    assert (
        reviewer.post(
            "/api/backup/refresh", headers={"x-review-csrf": "invalid"}
        ).status_code
        == 403
    )
    assert not calls
    assert reviewer.post("/api/backup/refresh").status_code == 200
    assert calls == [("source_staging_synthetic", "synthetic")]
    assert old.read_bytes() == b"synthetic old backup"
    assert reviewer.get("/api/backup/download").content == b"synthetic new backup"


def test_private_backup_uses_allowlist_auth_and_no_store(
    reviewer, tmp_path, monkeypatch
):
    archive = tmp_path / "private-test.zip"
    archive.write_bytes(b"synthetic archive")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            dict(
                filename=archive.name,
                sha256=__import__("hashlib").sha256(archive.read_bytes()).hexdigest(),
                bytes=archive.stat().st_size,
                restore_warning="Synthetic only",
            )
        )
    )
    monkeypatch.setenv("CANDIDATE_BACKUP_INDEX", str(index))
    response = reviewer.get("/api/backup/download")
    assert response.status_code == 200 and response.content == b"synthetic archive"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert reviewer.get("/api/candidates").headers["referrer-policy"] == "no-referrer"


def test_password_verification_and_rate_limit(monkeypatch):
    monkeypatch.setenv("CANDIDATE_REVIEW_USERS", "{}")
    review_app.attempts.clear()
    with TestClient(review_app.app) as c:
        for _ in range(10):
            assert (
                c.post(
                    "/api/login", json={"username": "wrong", "password": "wrong"}
                ).status_code
                == 401
            )
        assert (
            c.post(
                "/api/login", json={"username": "wrong", "password": "wrong"}
            ).status_code
            == 429
        )
