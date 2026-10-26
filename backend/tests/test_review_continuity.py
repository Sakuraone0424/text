"""Synthetic-only continuity and private access tests."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by parameter name

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from psycopg.errors import RaiseException
from app import review_app, candidate_review as svc, review_notes
from test_candidate_review import packet, reviewer, decision  # noqa: F401


def test_opinion_history_conflicts_and_signing(db, reviewer, packet):
    key = packet["candidate_key"]
    url = "/api/candidates/" + key
    row = reviewer.get(url).json()
    body = dict(
        revision_sha256=row["revision_sha256"],
        expected_version=0,
        expected_last_decision_id=0,
        note="Synthetic unsigned analysis with evidence, awaiting real attestation",
        checks={k: True for k in svc.CHECKS},
    )
    saved = reviewer.post(url + "/note", json=body).json()
    assert saved["version"] == 1
    assert reviewer.post(url + "/note", json=body).status_code == 409
    assert review_notes.latest(db, key, "other-account")["version"] == 0
    assert (
        db.execute("SELECT count(*) AS n FROM candidate_human_decisions").fetchone()[
            "n"
        ]
        == 0
    )
    with pytest.raises(RaiseException), db.transaction():
        db.execute("UPDATE reviewer_note_versions SET note='changed'")
    signed = decision(row, expected_note_version=0, note=body["note"])
    assert reviewer.post(url + "/decision", json=signed).status_code == 409
    signed.update(expected_note_version=1, human_attestation=False)
    assert reviewer.post(url + "/decision", json=signed).status_code == 409
    signed["human_attestation"] = True
    assert reviewer.post(url + "/decision", json=signed).status_code == 200
    assert (
        db.execute("SELECT note_draft_id FROM candidate_human_decisions").fetchone()[
            "note_draft_id"
        ]
        == saved["id"]
    )
    assert reviewer.get(url + "/note").json()["state"] == "submitted"
    assert reviewer.get("/api/candidates").json()["formal_qualified"] == 0


def test_position_and_revoked_cookie(reviewer, packet):
    body = dict(
        candidate_key=packet["candidate_key"],
        subject_filter="biochemistry",
        type_filter="single_choice",
    )
    assert reviewer.post("/api/position", json=body).status_code == 200
    assert (
        reviewer.get("/api/position").json()["candidate_key"] == packet["candidate_key"]
    )
    token = reviewer.cookies.get(review_app.COOKIE)
    assert reviewer.post("/api/logout").status_code == 200
    reviewer.cookies.set(review_app.COOKIE, token)
    assert reviewer.get("/api/session").status_code == 401


@pytest.mark.parametrize(
    "change,status",
    [
        ({"CANDIDATE_PRIVATE_UNTIL": ""}, 503),
        ({"CANDIDATE_COOKIE_SECURE": "0"}, 503),
        ({"CANDIDATE_PRIVATE_ORIGIN": "https://private.example/path"}, 503),
        ({"CANDIDATE_PRIVATE_UNTIL": "2020-01-01T00:00:00Z"}, 403),
        ({"CANDIDATE_PRIVATE_ORIGIN": "https://other.example"}, 403),
        ({}, 200),
    ],
)
def test_private_https_fails_closed(monkeypatch, change, status):
    values = dict(
        CANDIDATE_NETWORK_PROFILE="private_https",
        CANDIDATE_PRIVATE_ORIGIN="https://private.example",
        CANDIDATE_COOKIE_SECURE="1",
        CANDIDATE_PRIVATE_UNTIL=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    )
    for k, v in {**values, **change}.items():
        monkeypatch.setenv(k, v)
    with TestClient(review_app.app, base_url="https://private.example") as c:
        assert c.get("/").status_code == status
        assert c.get("http://private.example/").status_code in (403, 503)


def test_backup_integrity_and_range(reviewer, tmp_path, monkeypatch):
    import hashlib, json

    file = tmp_path / "test.zip"
    file.write_bytes(b"synthetic-private-backup")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            dict(
                filename=file.name,
                bytes=file.stat().st_size,
                sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            )
        )
    )
    monkeypatch.setenv("CANDIDATE_BACKUP_INDEX", str(index))
    assert (
        reviewer.get("/api/backup/download", headers={"Range": "bytes=0-8"}).status_code
        == 206
    )
    file.write_bytes(b"altered-backup")
    assert reviewer.get("/api/backup/download").status_code == 409
