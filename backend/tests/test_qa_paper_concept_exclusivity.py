"""Regression: only explicitly curated same-concept tags are mutually exclusive."""
from app.quiz_v2 import exclusive_topic_collision


def q(*tags):
    return {"knowledge_tags": list(tags), "stem": "different text", "subject": "cell_biology"}


def test_same_mechanism_across_term_and_fill_never_shares_one_paper():
    a = q("cell-mito-fusion-06", "exclusive:cell/mitochondria_fusion_fission")
    b = q("cell-mito-26-12", "exclusive:cell/mitochondria_fusion_fission")
    assert exclusive_topic_collision(a, b)
    assert exclusive_topic_collision(b, a)


def test_general_chapter_and_shared_page_not_sufficient_to_force_collision():
    a = q("chapter7/mitochondria", "mitochondria")
    b = q("chapter7/mitochondria", "mitochondria")
    assert not exclusive_topic_collision(a,b)


def test_different_exclusive_concepts_can_cooccur():
    assert not exclusive_topic_collision(
        q("exclusive:cell/mitochondria_fusion_fission"),
        q("exclusive:cell/actin_branching")
    )
