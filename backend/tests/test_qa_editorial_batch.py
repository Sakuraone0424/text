"""QA-only real-corpus editorial question bank: structural and source gates."""
import pytest
from app import qa_editorial_batch as batch


def unit():
    return {
        "file_id":next(fid for fid,item in batch.FILES.items() if item["key"]=="cell_biology"),
        "source_sha256":"a"*64,
        "source_kind":"textbook",
        "subject":"cell_biology",
        "chapter":"第八章 细胞骨架",
        "quality_flags":[],
        "text":"β-微管蛋白将所结合的 GTP 水解为 GDP。",
        "ocr_page":174,
        "unit_id":9,
    }


def bibliography():
    return {"textbook":"细胞生物学第五版", "authors":"丁明孝等", "edition":"第五版"}


def test_all_twelve_first_pass_answers_fit_real_60q_schema():
    facts=batch.source_facts()
    assert len(facts)==12
    assert len({x["id"] for x in facts})==12
    assert all(x["published"] is False for x in facts)
    for fact in facts:
        u=unit()
        u["subject"]=batch.SUBJECTS[fact["source_key"]]
        u["ocr_page"]=fact["ocr_page"]
        u["text"]=fact["quote"]
        q=batch.make_question(fact,u,bibliography(),style_id=101)
        assert q["question_type"]==fact["kind"]
        assert q["max_points"]==batch.POINTS[fact["kind"]]
        assert q["answer"]==fact["answer"]
        assert q["evidence"][0]["excerpt"]==fact["quote"]
        assert q["evidence"][0]["locator"]==f"OCR:{u['ocr_page']}:UNIT:9"


def test_two_blanks_are_individually_scored_not_a_fake_single_fill():
    fact=next(x for x in batch.source_facts() if x["id"]=="cell-arp23-02")
    q=batch.make_question(fact,unit(),bibliography(),101)
    assert q["blanks"]==[
        {"accepted_answers":["Arp2"],"points":1},
        {"accepted_answers":["Arp3"],"points":1}
    ]


def test_private_editorial_draft_persists_without_model_or_publication(monkeypatch):
    fact=batch.source_facts()[0]
    u=unit()
    u["text"]=fact["quote"]
    monkeypatch.setattr(batch,"snapshot_unit",lambda conn,uid:u if uid==9 else {
        **u,"unit_id":101,"source_kind":"exam_transcription","file_id":next(
            fid for fid,info in batch.FILES.items() if info["key"]=="cell_biology_exams"
        )
    })
    monkeypatch.setattr(batch,"pinned_source_bytes",lambda fid,digest:True)
    monkeypatch.setattr(batch,"from_database",lambda conn,unit:bibliography())
    class Conn:
        def execute(self,sql,params=None):
            if sql.startswith("SELECT u.id"):
                return type("R",(),{"fetchall":lambda self:[{"id":9}]})()
            if sql.startswith("SELECT status FROM reviewed_questions"):
                return type("R",(),{"fetchone":lambda self:{"status":"draft"}})()
            raise AssertionError(sql)
    stored=[]
    def persist(conn,payload):
        stored.append(payload)
        return 50
    result=batch.seed(Conn(),facts=[fact],create=persist,
                      style_lookup=lambda conn,s,k:{"id":101,"year":2004})
    assert result["private_drafts_created_or_reused"]==1
    assert result["published_count"]==0
    assert result["model_calls"]==0
    assert stored[0]["question"]["answer"]=="GDP"
    assert stored[0]["ai_analysis"]["independent_human_review"] is False


def test_duplicate_source_units_fail_closed_without_write(monkeypatch):
    fact=batch.source_facts()[0]
    fid=next(fid for fid,data in batch.FILES.items() if data["key"]=="cell_biology")
    class Conn:
        def execute(self,sql,params=None):
            assert params[0]==fid
            return type("R",(),{"fetchall":lambda self:[{"id":1},{"id":2}]})()
    result=batch.seed(Conn(),facts=[fact],create=lambda conn,p:pytest.fail("write should be blocked"))
    assert result["private_drafts_created_or_reused"]==0
    assert result["blocked_reasons"]=={"source_not_unique_or_risky":1}


def test_six_source_science_wording_and_equivalent_activation_grading():
    facts = {fact["id"]: fact for fact in batch.source_facts()}
    activation = facts["biou-activation-07"]
    assert activation["answer"] == "活化能"
    u = unit()
    u["subject"] = "biochemistry"
    u["ocr_page"] = activation["ocr_page"]
    u["text"] = activation["quote"]
    q = batch.make_question(activation, u, bibliography(), 101)
    assert {"活化能", "活化自由能", "活化自由能（ΔG‡）", "ΔG‡"}.issubset(
        set(q["accepted_answers"])
    )
    assert "反应的____" in q["stem"]

    carnitine = facts["biol-carnitine-10"]
    assert "脂酰基跨线粒体内膜" in carnitine["question"]
    assert "脂酰辅酶 A 转运进入" not in carnitine["question"]
    assert "脂酰肉碱" in carnitine["notes"]
    assert carnitine["published"] is False


@pytest.mark.parametrize("fact_id", ["cell-ub-proteasome-05", "cell-mito-fusion-06", "biou-competitive-08"])
def test_every_scoring_checkpoint_has_source_pending_evidence(monkeypatch, fact_id):
    fact = next(f for f in batch.source_facts() if f["id"] == fact_id)
    base = unit()
    base.update(
        file_id=next(fid for fid, meta in batch.FILES.items()
                     if meta["key"] == batch.BOOK_KEYS[fact["source_key"]]),
        subject=batch.SUBJECTS[fact["source_key"]],
        ocr_page=fact["ocr_page"],
        text=fact["quote"],
    )
    exam = {
        **base,
        "unit_id": 101,
        "source_kind": "exam_transcription",
        "file_id": next(fid for fid, meta in batch.FILES.items()
                        if meta["key"] == ("cell_biology_exams"
                                          if fact["source_key"] == "cell"
                                          else "biochemistry_exams")),
    }
    monkeypatch.setattr(batch, "snapshot_unit", lambda conn, uid: base if uid == 9 else exam)
    monkeypatch.setattr(batch, "pinned_source_bytes", lambda *a: True)
    monkeypatch.setattr(batch, "from_database", lambda *a: bibliography())

    class Conn:
        def execute(self, sql, params=None):
            if sql.startswith("SELECT u.id"):
                return type("R", (), {"fetchall": lambda self: [{"id": 9}]})()
            if sql.startswith("SELECT status FROM reviewed_questions"):
                return type("R", (), {"fetchone": lambda self: {"status": "draft"}})()
            raise AssertionError("Unexpected query")

    stored = []
    report = batch.seed(
        Conn(), facts=[fact], create=lambda conn, packet: (stored.append(packet) or 56),
        style_lookup=lambda conn, subject, kind: {"id": 101, "year": 2024},
    )
    assert report["private_drafts_created_or_reused"] == 1
    assert report["independent_scientific_approvals"] == 0
    draft = stored[0]
    assert len(draft["ai_analysis"]["rubric_evidence"]) == len(draft["question"]["rubric"])
    assert all(entry["unit_ids"] == [9] for entry in draft["ai_analysis"]["rubric_evidence"])
    assert draft["ai_analysis"]["independent_human_review"] is False
