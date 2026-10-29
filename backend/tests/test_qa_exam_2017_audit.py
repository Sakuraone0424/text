"""QA 2017 biochemistry source status uses exact 6+4 original lines."""
from app.qa_exam_2017_audit import audit


class Cursor(list):
    def fetchone(self):
        return self[0] if self else None
    def fetchall(self):
        return list(self)


class Connection:
    def execute(self, statement, params):
        if "FROM source_documents" in statement:
            return Cursor([{
                "id": 6,
                "sha256": "6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001",
            }])
        if "FROM source_units" in statement:
            texts = [
                "2017年硕士研究生招生考试试题",
                "一、名词解释",
                "G蛋白", "衰减子", "糖异生", "表观遗传学",
                "RNA 剪接", "端粒",
                "二、简答题",
                "简述细胞中氨基酸的主要来源和去向？",
                "简述细胞中脂蛋白的分类和功能？",
                "真核生物与原核生物在翻译上的不同？",
                "葡萄糖-6-磷酸进入糖酵解和磷酸戊糖途径受什么调控？",
                "2016年硕士研究生招生考试试题",
            ]
            return Cursor([
                {"id": i + 11, "ordinal": i + 1, "text": text,
                 "section": "", "chapter": "unclassified",
                 "quality_flags": []}
                for i, text in enumerate(texts)
            ])
        raise AssertionError(statement)


def test_readonly_2017_real_source_metadata_and_null_official_question_numbers():
    data=audit(Connection())
    assert data["term_candidate_lines"] == 6
    assert data["short_answer_candidate_lines"] == 4
    assert data["total_unnumbered_candidate_lines"] == 10
    assert data["all_original_question_numbers_missing"]
    assert len(data["source_unit_ids"])==10
    assert data["official_original_review_completed"] is False
    assert data["scientific_answer_review_completed"] is False
    assert data["eligible_historical_style_anchors_from_these_lines"]==0
