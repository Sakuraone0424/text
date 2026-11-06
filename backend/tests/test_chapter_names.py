"""Past-exam chapter labels ('6 核酸', '2 蛋白质 结构') map to the textbook's chapter names everywhere; unmapped ones become 其他 and are counted."""
import pytest

from app import chapters
from app.chapters import ChapterMap, number_of, words_of, chinese_number


@pytest.mark.parametrize("label,number", [
    ("第六章 核酸", 6), ("第6章 核酸的结构", 6), ("第十二章 糖代谢", 12), ("第二十一章 x", 21), ("6 核酸", 6), ("2 蛋白质 结构", 2),
    ("11、细胞骨架", 11), ("第 3 章", 3), ("核酸", None), ("", None),
])
def test_the_chapter_number_a_label_carries(label, number):
    assert number_of(label) == number


def test_words_without_the_number_and_the_decoration():
    assert words_of("第六章 核酸") == "核酸" and words_of("2 蛋白质 结构") == "蛋白质结构" and words_of("11、细胞骨架") == "细胞骨架"
    assert chinese_number("十") == 10 and chinese_number("十一") == 11 and chinese_number("二十") == 20 and chinese_number("九") == 9


@pytest.fixture
def mapper(db, monkeypatch):
    book = [("第一章 绪论", 1, "绪论"), ("第二章 蛋白质的结构与功能", 2, "蛋白质的结构与功能"), ("第六章 核酸", 6, "核酸"), ("第十二章 糖代谢", 12, "糖代谢")]
    monkeypatch.setattr(chapters, "textbook_chapters", lambda conn, subject: book if subject == "biochemistry" else [("第二章 细胞膜", 2, "细胞膜")])
    return ChapterMap(db)


@pytest.mark.parametrize("raw,expected", [
    ("6 核酸", "第六章 核酸"), ("2 蛋白质 结构", "第二章 蛋白质的结构与功能"), ("12 糖代谢", "第十二章 糖代谢"),
    ("第六章 核酸", "第六章 核酸"),                    # already the textbook's own name: unchanged
    ("第 6 章 核酸", "第六章 核酸"),                    # spacing differs
    ("核酸", "核酸"),                                     # not a past-exam label ("number + words"): left as it is
    ("9 氨基酸", "其他"),                                 # no chapter 9, and no chapter about it
    ("", "其他"),
])
def test_labels_map_to_the_textbook_names(mapper, raw, expected):
    assert mapper.name("biochemistry", raw) == expected


def test_the_same_number_in_the_other_subject_maps_to_that_subjects_chapter(mapper):
    assert mapper.name("cell_biology", "2 膜") == "第二章 细胞膜"


def test_what_cannot_be_mapped_is_counted_not_guessed(mapper):
    for raw in ("9 氨基酸", "9 氨基酸", "30 其它"):
        mapper.name("biochemistry", raw)
    assert mapper.unmapped == {"9 氨基酸": 2, "30 其它": 1}
