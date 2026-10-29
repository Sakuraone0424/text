"""Recover *review-only* candidates omitted from unnumbered past-exam sections.

A historical exam paragraph without a true question number must NOT be given
an invented one or silently counted as independently authenticated original.
Only the exact user-locked 2017 biochemistry transcript is currently
structurally scoped here: six unnumbered term lines plus four short answers.
All ten remain pending original-text, boundary and scientific review.
"""
from hashlib import sha256
import json

from app.transcript_analysis import candidates

SCOPES = {
    ("biochemistry_exams", 2017): {
        "term": 6,
        "short_answer": 4,
    },
}


def review_candidates(rows, item, *, source_sha256):
    key = item["key"]
    scope = SCOPES.get((key, 2017))
    if scope is None:
        return {
            "source_key": key, "year": 2017,
            "scope": "not_whitelisted", "candidates": [],
        }
    if len(source_sha256) != 64:
        raise ValueError("Source original hash required")
    numbered = [
        dict(row, id=row.get("id", row.get("ordinal", n)))
        for n, row in enumerate(rows, 1)
    ]
    selected = []
    counts = {kind: 0 for kind in scope}
    for row in candidates(numbered, item):
        if row["year"] != 2017 or row["question_type"] not in scope:
            continue
        if row["analysis"]["role"] != "unassigned" or row["question_number"] is not None:
            continue
        text = row["transcription"].strip()
        if not text or len(text) > 800:
            raise ValueError("Ambiguous unnumbered 2017 exam paragraph")
        flags = [
            f for f in row["concerns"] if f not in (
                "question_boundary_unconfirmed",
            )
        ]
        if flags:
            raise ValueError("2017 unnumbered candidate has unresolved source flags")
        counts[row["question_type"]] += 1
        selected.append({
            "source_key": key, "source_sha256": source_sha256,
            "year": 2017, "question_type": row["question_type"],
            "unit_id": row["unit_id"],
            "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "text_length": len(text),
            "original_question_number": None,
            "candidate_status": "pending_original_question_boundary_review",
            "usable_for_generation_style": False,
            "scientific_answer_verified": False,
        })
    if counts != scope:
        raise ValueError(
            "2017 biochemistry transcript no longer matches locked 6+4 "
            "unnumbered structure; do not fabricate exam questions"
        )
    if len({x["unit_id"] for x in selected}) != len(selected):
        raise ValueError("Duplicate source units in unnumbered question review")
    return {
        "source_key": key,
        "source_sha256": source_sha256,
        "year": 2017,
        "term_candidate_lines": counts["term"],
        "short_answer_candidate_lines": counts["short_answer"],
        "distinct_source_units": len(selected),
        "status": "pending_original_verification_not_certified_question_counts",
        "candidates": selected,
    }


def main(argv=None):
    import argparse
    from pathlib import Path
    from app.collection_seed import preflight
    from app.source_intake import private_json
    from app.sources import prepare_source

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if Path(args.output).exists():
        parser.error("Review report exists: refusing overwrite")
    records = preflight(args.directory)
    exam = next(x for x in records if x["key"] == "biochemistry_exams")
    prepared = prepare_source(exam["path"], exam["metadata"])
    report = review_candidates(
        prepared["rows"], {
            "key": "biochemistry_exams",
            "year_start": 2004, "year_end": 2026,
        }, source_sha256=exam["sha256"]
    )
    private_json(args.output, report)
    print("2017_biochemistry_unnumbered_6plus4_pending_original_review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
