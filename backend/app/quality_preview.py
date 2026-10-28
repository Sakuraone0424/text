"""Offline structural preview only. Cannot create formal quality events."""

import json
from app.auto_quality import Finding
from app.question_quality import AXES


def validate(raw, question, refs, phase):
    result = dict(
        real_api=False, formal_approval=False, local_checks_passed=False, issues=[]
    )
    try:
        finding = Finding.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        result["issues"] = ["invalid_json_or_schema"]
        return result
    bound = {u["unit_id"]: u["text"] for u in refs}
    sent_excerpts = {}
    for cited in question["evidence"]:
        sent_excerpts.setdefault(cited["source_unit_id"], []).append(cited["excerpt"])
    if (
        not finding.passed
        or finding.unresolved
        or set(finding.checks) != set(AXES)
        or not all(finding.checks.values())
    ):
        result["issues"].append("rejected_or_unresolved")
    if (
        phase == "science"
        and question["question_type"] == "single_choice"
        and finding.independent_choice != question["answer"]
    ):
        result["issues"].append("independent_answer_conflict")
    if any(
        c.unit_id not in bound or not any(
            c.excerpt in excerpt for excerpt in sent_excerpts.get(c.unit_id, [])
        )
        for c in finding.claims
    ):
        result["issues"].append("quote_tampered")
    if phase == "answer" and {
        i for c in finding.claims for i in c.criterion_indices
    } != set(range(len(question["rubric"]))):
        result["issues"].append("incomplete_rubric")
    result["local_checks_passed"] = not result["issues"]
    return result
