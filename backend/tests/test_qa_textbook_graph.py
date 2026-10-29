"""Science is explicitly pending for all textbook evidence graph nodes."""
import pytest
from app.qa_textbook_graph import prepare_rows, COMPILED


def example(uid=1, text="所谓蛋白质定位是指细胞核定位与信号识别。", flags=None):
    return {
        "unit_id":uid,
        "file_id":"1onpEwQsbL3ntkPOU1mFbgQKD2iFQi3n3",
        "source_sha256":"89b226f7c9da53179943375751cacdf873b3693efdb426304a41ed1556e3b013",
        "subject":"biochemistry","chapter":"第一章","ocr_page":3,
        "text":text,"quality_flags":flags or [],
    }


def test_exact_textbook_node_has_original_sha_page_and_candidate_facets():
    nodes,facets,chapters=prepare_rows([example()])
    assert len(nodes)==1
    assert nodes[0][1]=="biochemistry_upper"
    assert nodes[0][6]==3
    assert len(nodes[0][3])==64
    assert ("biochemistry_upper","第一章") in chapters
    assert (1,"definition") in facets
    assert (1,"cellular_localization") in facets
    assert (1,"molecular_structure") not in facets


def test_risk_flag_retained_without_science_promotion():
    nodes,facets,_=prepare_rows([example(flags=["image_dependent"])])
    assert nodes[0][-1]==1
    assert "definition" in {facet for _,facet in facets}


def test_duplicate_unit_identity_refused():
    with pytest.raises(ValueError,match="Duplicate"):
        prepare_rows([example(),example()])


def test_changed_source_version_and_wrong_page_refused():
    altered=example()
    altered["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="unpinned"):
        prepare_rows([altered])
    altered=example()
    altered["ocr_page"]=None
    with pytest.raises(ValueError,match="source OCR page"):
        prepare_rows([altered])


def test_semantic_facets_are_literal_regex_candidates_only():
    assert "definition" in COMPILED
    assert "mechanism" in COMPILED
    assert "sequence_process" in COMPILED
    assert "regulation" in COMPILED
