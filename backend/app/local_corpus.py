"""Private filesystem corpus audit and pending literal mappings; never marks science reviewed."""

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
from app import sources
from app.source_policy import handoff
from app.source_intake import private_json
from app.transcript_analysis import candidates


def private_jsonl(path, rows):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def reference(file, row):
    return {
        "file_id": file["file_id"],
        "source_sha256": file["sha256"],
        "ordinal": row["ordinal"],
        "ocr_page": row["ocr_global_page"],
        "local_unit_id": f"{file['sha256']}:{row['ordinal']}",
    }


def year_report(file):
    data = list(candidates(file["rows"], file))
    lookup = {r["ordinal"]: r for r in file["rows"]}
    grouped = defaultdict(list)
    for c in data:
        grouped[c["year"]].append(c)
    report = []
    questions = []
    for year in range(file["year_start"], file["year_end"] + 1):
        rows = grouped[year]
        starts = [c for c in rows if c["question_number"]]
        segments = defaultdict(list)
        for c in rows:
            if c["analysis"]["year_heading_unit_id"] is not None:
                segments[c["analysis"]["year_heading_unit_id"]].append(c)
        paper_reports = []
        for seg, items in segments.items():
            sections = []
            active = None
            for c in items:
                role = c["analysis"]["role"]
                if role == "section_heading":
                    active = {
                        "unit_id": c["unit_id"],
                        "type": c["question_type"],
                        "declared_questions": c["analysis"]["declared_questions"],
                        "numbers": [],
                    }
                    sections.append(active)
                elif role == "question_start" and active is not None:
                    active["numbers"].append(c["question_number"])
            for section in sections:
                nums = [int(n) for n in section["numbers"] if n.isdecimal()]
                section["candidate_questions"] = len(nums)
                section["missing_numbers"] = (
                    sorted(set(range(1, max(nums) + 1)) - set(nums)) if nums else []
                )
                section["repeated_numbers"] = [
                    n for n, count in Counter(nums).items() if count > 1
                ]
                section["declared_count_matches"] = (
                    len(nums) == section["declared_questions"]
                    if section["declared_questions"] is not None
                    else None
                )
            paper_reports.append(
                {
                    "year_heading_ordinal": seg,
                    "sections": sections,
                    "manual_completeness_review_required": True,
                }
            )
        report.append(
            {
                "year": year,
                "year_heading_segments": len(segments),
                "source_paragraphs": len(rows),
                "candidate_questions": len(starts),
                "question_types": dict(
                    Counter(c["question_type"] or "unknown" for c in starts)
                ),
                "word_numbered_candidates": sum(
                    c["analysis"]["word_numbering"] is not None for c in starts
                ),
                "unknown_type_candidates": sum(
                    c["question_type"] is None for c in starts
                ),
                "unconfirmed_boundaries": sum(
                    "question_boundary_unconfirmed" in c["concerns"] for c in rows
                ),
                "untranscribed_images": sum(
                    "untranscribed_image" in c["concerns"] for c in rows
                ),
                "reference_answer_markers": sum(
                    c["analysis"]["role"] == "answer" for c in rows
                ),
                "official_answer_evidence_verified": False,
                "original_exam_verified": False,
                "trusted_frequency_sample": False,
                "source_level": "ocr_transcription" if rows else "not_established",
                "papers": paper_reports,
                "priority_2023_2026": year >= 2023,
            }
        )
        for start in starts:
            members = [
                c
                for c in rows
                if c["analysis"]["parent_question_unit_id"] == start["unit_id"]
                and c["analysis"]["role"]
                not in ("year_heading", "section_heading", "editorial_note", "answer")
            ]
            questions.append(
                {
                    **reference(file, lookup[start["unit_id"]]),
                    "year": year,
                    "question_number": start["question_number"],
                    "question_type": start["question_type"],
                    "year_heading_ordinal": start["analysis"]["year_heading_unit_id"],
                    "text": "\n".join(c["transcription"] for c in members),
                    "unit_ordinals": [c["unit_id"] for c in members],
                    "concerns": sorted(
                        {flag for c in members for flag in c["concerns"]}
                    ),
                    "status": "pending",
                }
            )
    return {
        "years": report,
        "unassigned_year_paragraphs": len(grouped[None]),
        "question_count_meaning": "structural_candidates_not_verified_complete_original_questions",
    }, questions


TERM = re.compile(
    r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9α-ωΑ-Ω\-]{1,24})\s*[（(]([A-Za-z][A-Za-z0-9 ,\-\'′]{1,90})[)）]"
)


def mapping_proposals(files, questions):
    occurrences = defaultdict(list)
    proposals = []
    for file in files:
        if file["kind"] != "textbook":
            continue
        for row in file["rows"]:
            if row["chapter"] == "unclassified" or row["section"] == "contents":
                continue
            terms = []
            for match in TERM.finditer(row["text"]):
                terms.append((match[1], match.start(1), "explicit_parenthetical_term"))
                english = match[2].strip()
                if len(english) <= 50:
                    terms.append(
                        (english, match.start(2), "explicit_parenthetical_term")
                    )
            if (
                row["section"]
                and len(row["section"]) <= 120
                and row["section"] in row["text"]
            ):
                terms.append(
                    (
                        row["section"],
                        row["text"].index(row["section"]),
                        "literal_section_heading",
                    )
                )
            seen = set()
            for term, start, method in terms:
                if term in seen:
                    continue
                seen.add(term)
                ref = reference(file, row)
                evidence = {
                    **ref,
                    "quote": row["text"][max(0, start - 40) : start + len(term) + 120],
                    "chapter": row["chapter"],
                    "risk_flags": row["quality_flags"],
                    "reviewed": False,
                }
                proposal = {
                    "source": ref,
                    "target": None,
                    "subject": file["subject"],
                    "knowledge_label": term,
                    "method": method,
                    "evidence": evidence,
                    "status": "pending",
                }
                proposals.append(proposal)
                occurrences[(file["subject"], term)].append(evidence)
    by_subject = defaultdict(lambda: defaultdict(list))
    for subject, term in occurrences:
        by_subject[subject][term[0]].append(term)
    for chars in by_subject.values():
        for words in chars.values():
            words.sort(key=len, reverse=True)
    examfiles = {f["file_id"]: f for f in files if f["kind"] != "textbook"}
    for question in questions:
        file = examfiles[question["file_id"]]
        text = question["text"]
        matches = set()
        occupied = []
        for start, char in enumerate(text):
            if any(a <= start < b for a, b in occupied):
                continue
            for term in by_subject[file["subject"]].get(char, []):
                if not text.startswith(term, start):
                    continue
                end = start + len(term)
                if term.isascii() and (
                    (start and text[start - 1].isascii() and text[start - 1].isalnum())
                    or (end < len(text) and text[end].isascii() and text[end].isalnum())
                ):
                    continue
                matches.add(term)
                occupied.append((start, end))
                break
        for term in sorted(matches):
            refs = occurrences[(file["subject"], term)]
            # A bounded review queue, with full occurrence counts exposed; never automatic scientific linkage.
            for target in refs[:3]:
                proposals.append(
                    {
                        "source": {
                            k: question[k]
                            for k in (
                                "file_id",
                                "source_sha256",
                                "ordinal",
                                "ocr_page",
                                "local_unit_id",
                            )
                        },
                        "target": target,
                        "subject": file["subject"],
                        "knowledge_label": term,
                        "method": "literal_transcript_term_match",
                        "evidence": {
                            "literal_match": term,
                            "textbook_reference": target,
                            "total_matching_textbook_occurrences": len(refs),
                            "review_queue_cap": 3,
                            "semantic_entailment_verified": False,
                        },
                        "status": "pending",
                    }
                )
    return proposals


def audit(root, destination):
    root = Path(root)
    destination = Path(destination)
    if (
        destination.resolve().is_relative_to(Path(__file__).resolve().parents[2])
        or destination.exists()
    ):
        raise ValueError("Use a new private audit directory outside the Git checkout")
    verification = json.loads((root / "handoff-verification.json").read_text())
    expected = {f["file_id"]: f for f in verification["files"]}
    setup = handoff(root / "files")
    files = []
    reports = []
    questions = []
    destination.mkdir(mode=0o700, parents=True)
    config = destination / "config"
    config.mkdir(mode=0o700)
    for item in setup["files"]:
        metadata = {
            **item["metadata"],
            "expected_sha256": expected[item["file_id"]]["sha256"],
            "handoff_manifest_sha256": verification["manifest_sha256"],
            "handoff_archive_sha256": verification["archive_sha256"],
        }
        prepared = sources.prepare_source(item["local_path"], metadata)
        rows = [{**r, "ordinal": n} for n, r in enumerate(prepared["rows"], 1)]
        file = {
            **item,
            "rows": rows,
            "sha256": prepared["sha256"],
            "metadata": metadata,
        }
        files.append(file)
        private_json(config / (item["key"] + ".json"), metadata)
        private_jsonl(
            destination / (item["key"] + "-units.jsonl"),
            [
                {
                    **reference(file, r),
                    "chapter": r["chapter"],
                    "section": r["section"],
                    "text": r["text"],
                    "quality_flags": r["quality_flags"],
                    "status": "pending",
                }
                for r in rows
            ],
        )
        chapters = defaultdict(lambda: {"units": 0, "pages": set(), "risk_units": 0})
        for r in rows:
            group = chapters[r["chapter"]]
            group["units"] += 1
            if r["ocr_global_page"]:
                group["pages"].add(r["ocr_global_page"])
            group["risk_units"] += bool(r["quality_flags"])
        for group in chapters.values():
            group["pages"] = sorted(group["pages"])
            group["human_reviewed"] = 0
            group["science_reviewed"] = 0
        text = "\n".join(r["text"] for r in rows)
        report = {
            "key": item["key"],
            "file_id": item["file_id"],
            "source_sha256": prepared["sha256"],
            "manifest_match": True,
            "file_read": True,
            "preflight_passed": True,
            "database_imported": False,
            "human_reviewed": 0,
            "science_reviewed": 0,
            "published_questions": 0,
            "encoding": "UTF-8" if item["kind"] == "textbook" else "DOCX XML",
            "ocr_pages": len(prepared["pages"]),
            "units": len(rows),
            "characters": len(text),
            "replacement_characters": text.count("\ufffd"),
            "null_characters": text.count("\x00"),
            "empty_text_pages": [
                p["ocr_global_page"]
                for p in prepared["pages"]
                if not p["text_characters"]
            ],
            "declared_blank_pages": sorted(
                {
                    r["ocr_global_page"]
                    for r in rows
                    if r["ocr_global_page"] is not None
                    and re.fullmatch(r"[（(\[]?\s*空白页\s*[）)\]]?", r["text"])
                }
            ),
            "quality_flags": dict(
                Counter(flag for r in rows for flag in r["quality_flags"])
            ),
            "chapters": dict(chapters),
            "pdf_physical_pages_verified": False,
            "printed_pages_verified": False,
        }
        if item["kind"] == "exam_transcription":
            report["exam_analysis"], qs = year_report(file)
            questions.extend(qs)
        private_json(destination / (item["key"] + "-audit.json"), report)
        reports.append(report)
    proposals = mapping_proposals(files, questions)
    private_jsonl(destination / "exam-questions-pending.jsonl", questions)
    private_jsonl(destination / "mapping-proposals-pending.jsonl", proposals)
    counts = {
        "files_obtained": 6,
        "sha256_matches": 6,
        "preflight_passed": 6,
        "ocr_pages": sum(r["ocr_pages"] for r in reports),
        "indexed_units_local": sum(r["units"] for r in reports),
        "candidate_questions": len(questions),
        "mapping_proposals": len(proposals),
        "textbook_knowledge_proposals": sum(p["target"] is None for p in proposals),
        "exam_textbook_proposals": sum(p["target"] is not None for p in proposals),
        "database_imported": 0,
        "human_verified": 0,
        "science_verified": 0,
        "approved_mappings": 0,
        "published_questions": 0,
    }
    summary = {
        "counts": counts,
        "files": [
            {
                k: r[k]
                for k in (
                    "key",
                    "file_id",
                    "source_sha256",
                    "ocr_pages",
                    "units",
                    "quality_flags",
                    "database_imported",
                    "human_reviewed",
                    "science_reviewed",
                )
            }
            for r in reports
        ],
        "meaning": "full_file_structural_audit_and_draft_literal_mappings_not_scientific_approval",
    }
    private_json(destination / "summary.json", summary)
    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.destination)["counts"]))
