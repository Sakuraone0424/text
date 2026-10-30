"""The QA graph cannot replace a source citation with same-count wrong data."""
from copy import deepcopy

import pytest

from app.qa_graph_provenance_gate import NODE_KEYS, reconcile


def fixture():
    nodes = [
        (i, "biochemistry_upper", "a" * 64, "b" * 64, "biochemistry",
         "Chapter 1", i, 0)
        for i in range(1, 4)
    ]
    facets = [(1, "definition"), (1, "mechanism"), (2, "comparison")]
    actual_nodes = [
        dict(zip(NODE_KEYS, node), scientific_review_state="pending")
        for node in nodes
    ]
    actual_facets = [
        {"unit_id": uid, "facet": facet,
         "reason": "literal_regex_not_science", "scientific_verified": False}
        for uid, facet in facets
    ]
    return nodes, facets, actual_nodes, actual_facets


def run(sample):
    return reconcile(*sample, required_nodes=3, required_facets=3)


def test_exact_textbook_graph_provenance_passes_without_fake_science():
    report = run(fixture())
    assert report["evidence_nodes"] == 3
    assert report["lexical_facet_candidates"] == 3
    assert report["all_nodes_source_exact"] is True
    assert report["all_lexical_facets_source_exact"] is True
    assert report["full_scientific_knowledge_graph_completed"] is False
    assert report["scientifically_approved_nodes_or_facets"] == 0


@pytest.mark.parametrize("tamper", [
    lambda a: a[2][0].update(source_sha256="f" * 64),
    lambda a: a[2][0].update(source_text_sha256="f" * 64),
    lambda a: a[2][0].update(ocr_page=101),
    lambda a: a[2][0].update(chapter="Chapter 2"),
    lambda a: a[2][0].update(subject="cell_biology"),
    lambda a: a[2][0].update(flagged_risks=1),
    lambda a: a[2].pop(),
    lambda a: a[3][0].update(facet="regulation"),
    lambda a: a[3][0].update(scientific_verified=True),
    lambda a: a[3][0].update(reason="ai_approved"),
    lambda a: a[2][0].update(scientific_review_state="approved"),
    lambda a: a[3].pop(),
])
def test_same_count_or_missing_provenance_fails_closed(tamper):
    data = list(deepcopy(fixture()))
    tamper(data)
    with pytest.raises(ValueError):
        run(data)


def test_expected_inventory_cannot_silently_shrink():
    data = fixture()
    with pytest.raises(ValueError, match="inventory_changed"):
        reconcile(*data)  # production defaults still require 9,655/12,896
