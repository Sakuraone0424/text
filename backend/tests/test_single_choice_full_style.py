"""A-D option completeness is mandatory before a past paper is used as style."""
from app.auto_generation import complete_choice_style_unit_ids


def unit(uid, text, flags=None):
    return dict(id=uid,text=text,quality_flags=flags or [])


def test_authentic_choice_options_split_after_stem_are_all_attached():
    rows=[
        unit(804,"最小最简单的细胞是______"),
        unit(805,"A.病毒\tB.支原体\tC.细菌\tD.红细胞"),
        unit(806,"扫描电子显微镜可用于______"),
    ]
    assert complete_choice_style_unit_ids(rows)==[804,805]


def test_historical_two_paragraph_options_require_both_paragraph_ids():
    rows=[
        unit(100,"下列哪种说法不正确？"),
        unit(101,"A.甲\tB.乙"),
        unit(102,"C.丙\tD.丁"),
        unit(103,"下一题干"),
    ]
    assert complete_choice_style_unit_ids(rows)==[100,101,102]


def test_no_option_question_stem_is_not_valid_single_choice_style():
    assert complete_choice_style_unit_ids([unit(20,"糖酵解限速酶是____"),unit(21,"下一道填空题")]) is None


def test_next_question_options_cannot_satisfy_missing_option():
    assert complete_choice_style_unit_ids([
        unit(1,"缺少选项的错误题干"),
        unit(2,"下一道题干"),
        unit(3,"A.甲 B.乙 C.丙 D.丁"),
    ]) is None


def test_missing_any_single_choice_option_refuses_styled_generation():
    assert complete_choice_style_unit_ids([
        unit(1,"选择以下正确选项"),unit(2,"A.甲 B.乙"),unit(3,"C.丙")
    ]) is None


def test_visual_or_table_risk_in_option_refuses_entire_question():
    assert complete_choice_style_unit_ids([
        unit(1,"请选择"),unit(2,"A.甲 B.乙 C.丙 D.丁",["image_dependent"])
    ]) is None
    assert complete_choice_style_unit_ids([
        unit(1,"请选择",["corrupt_ocr"]),unit(2,"A.甲 B.乙 C.丙 D.丁")
    ]) is None


def test_choices_co_located_in_one_source_unit_remain_valid():
    assert complete_choice_style_unit_ids([
        unit(10,"1. 以下哪项？\nA.甲\nB.乙\nC.丙\nD.丁"),
    ])==[10]
