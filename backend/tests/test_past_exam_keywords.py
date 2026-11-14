"""Past-exam fill-ins: the paragraphs are found from the question's own keywords; an item with no suitable paragraph is not sent to a model;
what the model answers "not supported" is handled as the textbook not supporting it."""
import pytest
from psycopg.types.json import Jsonb

from app import past_exam_import as pei, sources
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_ocr_only import material
from test_past_exam_import import model_result

STEM = "细菌中的____是一种环状DNA，仅包含复制起点基因和两侧的反向重复序列"
RIGHT = ("转座子是细菌中一类可移动的环状DNA元件，其两端为反向重复序列，中间只包含转座酶基因，"
         "它能够在基因组内移动并引起基因突变或重排，是细菌基因组可塑性的重要来源之一，需要认真理解。")
NEXT = "复制起点是DNA复制开始的位点，起始蛋白在此处识别并结合，随后解旋酶装配，复制叉向两侧延伸，形成双向复制，这一过程受到严格调控。"
NOISE = [
    "细菌的细胞壁主要由肽聚糖构成，革兰氏阳性菌的细胞壁较厚，革兰氏阴性菌的细胞壁较薄且含有外膜，抗生素可以作用于细胞壁合成过程，这是临床上重要的一类药物。",
    "环状DNA分子在复制时会形成超螺旋结构，拓扑异构酶负责调节超螺旋状态，以保证复制和转录的顺利进行，这类酶在原核生物与真核生物中均广泛存在。",
    "DNA是遗传信息的载体，由脱氧核糖核苷酸通过磷酸二酯键连接而成，双螺旋结构由沃森和克里克提出，碱基互补配对原则是其结构和功能的基础，这一点非常重要。",
]


def corpus_with(db, tmp_path, monkeypatch, paragraphs):
    path, meta = material(tmp_path, "biochemistry_lower", content="\n\n".join(paragraphs))
    sources.import_source(db, path, meta)
    monkeypatch.setattr(pei, "pinned_source_bytes", lambda file_id, sha: True)
    monkeypatch.setattr(pei, "source_bibliography", lambda conn, unit: {"textbook": "Synthetic", "authors": "Test", "edition": "Test"})


def the_item(db, tmp_path, monkeypatch, key):
    """The stem as a real past-exam item (imported from an exam document)."""
    path, meta = material(tmp_path, "biochemistry_exams", content=["2024年中国科学技术大学生物化学真题", "填空题", "1. " + STEM + "。"])
    sources.import_source(db, path, meta)
    pei.import_items(db)
    return db.execute("SELECT * FROM past_exam_items WHERE question_type='fill_blank' AND status='imported'").fetchone()


def test_the_keywords_are_the_nouns_and_terms_without_the_blank_and_the_connecting_words():
    words = pei.stem_keywords(STEM)
    for wanted in ("反向重复序列", "复制起点", "两侧", "细菌", "环状", "dna"):
        assert wanted in words, wanted
    assert not any("_" in w or w in ("的", "是", "仅", "和") for w in words)
    assert words == sorted(words, key=lambda t: (-len(t), t))                    # longest (most specific) first


def test_the_paragraph_with_several_keywords_comes_first_with_its_neighbours(db, tmp_path, monkeypatch):
    corpus_with(db, tmp_path, monkeypatch, NOISE[:1] + [RIGHT, NEXT] + NOISE[1:])
    units = pei.retrieve_evidence(db, "biochemistry", STEM, blank=True)
    texts = [u["text"] for u in units]
    assert any("反向重复序列" in t and "转座" in t for t in texts)                    # the paragraph that holds the keywords
    assert len(units) <= pei.MAX_EVIDENCE_UNITS and sum(len(t) for t in texts) <= pei.MAX_EVIDENCE_CHARS
    assert any(t.startswith("细菌的细胞壁") for t in texts) or any(t.startswith("复制起点是") for t in texts)   # a neighbour before or after is carried along


def test_paragraphs_that_hold_one_common_word_are_no_evidence(db, tmp_path, monkeypatch):
    corpus_with(db, tmp_path, monkeypatch, NOISE)                                # only "细菌", "环状", "DNA" appear: ordinary words
    assert pei.retrieve_evidence(db, "biochemistry", STEM, blank=True) == []


def test_an_item_with_no_suitable_paragraph_is_not_sent_and_says_so(db, tmp_path, monkeypatch, setup_ai):
    corpus_with(db, tmp_path, monkeypatch, NOISE)
    item = the_item(db, tmp_path, monkeypatch, "x")
    assert pei._enqueue_item(db, setup_ai, item) == "blocked"
    row = db.execute("SELECT status,blocked_reason FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()
    assert (row["status"], row["blocked_reason"]) == ("blocked", "no_textbook_evidence")
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE payload->>'origin'='past_exam'").fetchone()["n"] == 0     # nothing for a Routine to do in vain


def test_an_item_with_a_suitable_paragraph_is_sent_with_the_retrieval_tag(db, tmp_path, monkeypatch, setup_ai):
    corpus_with(db, tmp_path, monkeypatch, [RIGHT, NEXT] + NOISE)
    item = the_item(db, tmp_path, monkeypatch, "x")
    assert pei._enqueue_item(db, setup_ai, item) == "queued"
    job = db.execute("SELECT payload FROM ai_jobs WHERE payload->>'origin'='past_exam'").fetchone()
    assert job["payload"]["retrieval"] == pei.RETRIEVAL_VERSION and any("反向重复序列" in u["text"] for u in job["payload"]["evidence"])


# ---- before / after -------------------------------------------------------------------------------------------------------------------

def test_the_share_of_not_supported_answers_is_counted_before_and_after(db, setup_ai):
    def done(key, retrieval, supported):
        payload = {"origin": "past_exam", "question_type": "fill_blank", **({"retrieval": retrieval} if retrieval else {})}
        db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,result) VALUES (%s,%s,'generation','succeeded','{}',%s,%s)",
                   (key, setup_ai, Jsonb(payload), Jsonb({"real_api": True, "response": {"supported": supported}})))

    for n in range(4):
        done(f"old{n}", None, n == 0)                                           # before: 3 of 4 not supported
    for n in range(5):
        done(f"new{n}", pei.RETRIEVAL_VERSION, n != 0)                          # after: 1 of 5 not supported
    stats = pei.support_stats(db)["fill_blank"]
    assert stats["old"] == dict(answered=4, unsupported=3, rate=0.75) and stats[pei.RETRIEVAL_VERSION] == dict(answered=5, unsupported=1, rate=0.2)


# ---- supported=false ------------------------------------------------------------------------------------------------------------------

def test_a_not_supported_answer_closes_the_item_as_unsupported_and_it_is_not_sent_again(db, tmp_path, monkeypatch, setup_ai):
    from app import auto_worker

    corpus_with(db, tmp_path, monkeypatch, [RIGHT, NEXT] + NOISE)
    item = the_item(db, tmp_path, monkeypatch, "x")
    assert pei._enqueue_item(db, setup_ai, item) == "queued"
    job = db.execute("SELECT * FROM ai_jobs WHERE payload->>'origin'='past_exam'").fetchone()
    result = {"real_api": True, "usage": dict(input_tokens=1, output_tokens=1, cache_read_input_tokens=0),
              "response": dict(supported=False, unsupported_reason="the paragraphs do not name the element")}
    db.execute("UPDATE ai_jobs SET state='succeeded',result=%s,finished_at=clock_timestamp() WHERE id=%s", (Jsonb(result), job["id"]))
    auto_worker.dispatch_completed(db)
    row = db.execute("SELECT status,blocked_reason,question_id FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()
    assert (row["status"], row["blocked_reason"], row["question_id"]) == ("blocked", "insufficient_textbook_evidence", None)       # unsupported, no question
    done = db.execute("SELECT state,error_code,result->'dispatch_failure'->>'code' AS code FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    assert done["code"] == "insufficient_textbook_evidence"                                    # a reason of its own, not a format error
    assert db.execute("SELECT count(*) AS n FROM candidate_review_packets").fetchone()["n"] == 0                                  # nothing entered the bank
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='repair'").fetchone()["n"] == 0                                # no repair requested
    pei.plan(db, setup_ai, batch=5)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE payload->>'origin'='past_exam'").fetchone()["n"] == 1             # not sent a second time
    assert db.execute("SELECT status FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()["status"] == "blocked"


def test_a_not_supported_answer_through_the_routine_interface_is_blocked_and_closes_the_item(db, tmp_path, monkeypatch, setup_ai):
    from app import external_executor as ext

    monkeypatch.setenv("EXECUTOR_GENERATION", "claude")
    corpus_with(db, tmp_path, monkeypatch, [RIGHT, NEXT] + NOISE)
    item = the_item(db, tmp_path, monkeypatch, "x")
    assert pei._enqueue_item(db, setup_ai, item) == "queued"
    lease = ext.lease(db, "generation", 1, "session-aaaaaa")[0]
    out = ext.submit(db, lease["lease_id"], "session-aaaaaa", "claude-test", dict(supported=False, unsupported_reason="not in the paragraphs"))
    assert out["passed"] is False and out["outcome"] == "blocked" and "insufficient_textbook_evidence" in out["reasons"]
    assert db.execute("SELECT blocked_reason FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()["blocked_reason"] == "insufficient_textbook_evidence"
    assert ext.lease(db, "generation", 3, "session-aaaaaa") == []                                  # the item is not offered again

