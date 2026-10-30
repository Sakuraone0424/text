"""First-pass textbook draft tests; no AI events or publication."""
import pytest
from app import qa_editorial_first_draft as editorial


class FixtureConnection:
    def __init__(self, *, source_count=1, unverified_style=False):
        self.source_count = source_count
        self.unverified_style = unverified_style
        self.query_log = []
    def execute(self, statement, params=None):
        self.query_log.append((statement, params))
        if "SELECT u.id FROM source_units u" in statement:
            return type("R", (), {"fetchall": lambda s: [
                {"id": 9} for _ in range(self.source_count)
            ]})()
        if "SELECT t.unit_id,t.year" in statement:
            return type("R", (), {"fetchall": lambda s: [{
                "unit_id": 101, "year": 2024,
                "quality_flags": [], "concerns": (
                    ["question_boundary_unconfirmed"] if self.unverified_style else []
                ),
            }]})()
        if "SELECT status FROM reviewed_questions" in statement:
            return type("R", (), {"fetchone": lambda s: {"status": "draft"}})()
        raise AssertionError("unexpected SQL")


def _snapshot(conn, unit_id):
    assert unit_id == 9
    return {
        "file_id": editorial.BOOK_ID,
        "source_sha256": "a"*64,
        "source_kind": "textbook",
        "subject": "cell_biology",
        "quality_flags": [], "ocr_page": 174, "chapter": "第八章 细胞骨架",
        "unit_id": unit_id,
        "text": "微管蛋白发挥 GTP 酶活性，将所结合的 GTP 水解为 GDP，调节微管组装。",
    }


def test_original_first_draft_has_literal_source_and_no_approval(monkeypatch):
    monkeypatch.setattr(editorial, "snapshot_unit", _snapshot)
    monkeypatch.setattr(editorial, "pinned_source_bytes", lambda *args: True)
    monkeypatch.setattr(editorial, "from_database", lambda conn, unit: {
        "textbook": "细胞生物学（第五版）",
        "authors": "丁明孝等",
        "edition": "第五版",
    })
    packet, error = editorial.prepare(FixtureConnection())
    assert error is None
    question = packet["question"]
    assert question["answer"] == "GDP"
    assert question["max_points"] == 2
    assert question["evidence"][0]["locator"] == "OCR:174:UNIT:9"
    assert question["evidence"][0]["excerpt"] == editorial.QUOTED_ANCHOR
    assert packet["ai_analysis"]["independent_human_review"] is False
    assert packet["style_sources"][0]["year"] == 2024


def test_missing_or_ambiguous_original_quote_cannot_make_draft():
    draft, reason = editorial.prepare(FixtureConnection(source_count=0))
    assert draft is None and reason == "literal_textbook_anchor_not_unique_clean_unit"
    draft, reason = editorial.prepare(FixtureConnection(source_count=2))
    assert draft is None and reason == "literal_textbook_anchor_not_unique_clean_unit"


def test_bad_exam_boundary_refuses_stylistic_claim(monkeypatch):
    monkeypatch.setattr(editorial, "snapshot_unit", _snapshot)
    monkeypatch.setattr(editorial, "pinned_source_bytes", lambda *args: True)
    draft, reason = editorial.prepare(FixtureConnection(unverified_style=True))
    assert draft is None
    assert reason == "verified_exam_transcription_style_not_available"


def test_pending_draft_persisted_and_reused_without_publication(monkeypatch):
    calls = []
    fake = FixtureConnection()
    def fake_persist(conn, packet):
        calls.append(packet["candidate_key"])
        return 18
    result = editorial.seed(fake, packet_builder=lambda conn: (
        {"candidate_key": "case", "question": {"answer": "GDP"}}, None
    ), persister=fake_persist)
    assert calls == ["case"]
    assert result["draft_question_id"] == 18
    assert result["published"] is False
    assert result["independent_science_approved"] is False


def test_unexpected_published_record_rejected(monkeypatch):
    class Published(FixtureConnection):
        def execute(self,statement,params=None):
            if "SELECT status FROM reviewed_questions" in statement:
                return type("R", (), {"fetchone":lambda s:{"status":"published"}})()
            return super().execute(statement,params)
    with pytest.raises(ValueError,match="unexpectedly_published"):
        editorial.seed(Published(),packet_builder=lambda conn:({"question":{}},None),
                       persister=lambda conn,p:19)
