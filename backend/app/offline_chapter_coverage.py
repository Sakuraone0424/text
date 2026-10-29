"""Private, deterministic chapter-to-OCR-page inventory for six locked sources.

This is structural coverage, NOT conceptual correctness or a textbook summary.
Only source-supplied chapter headings, ranges and counts leave the parser; no
paragraph excerpts, original text, model instructions or private local paths.
The official DB importer is not needed for this read-only first-stage audit.
"""
import argparse
from collections import Counter, defaultdict

from app.collection_seed import LOCK, preflight
from app.source_policy import COLLECTION
from app.sources import prepare_source
from app.source_intake import private_json


UNKNOWN = {"unclassified", "unknown", "", "contents"}


def report(directory, *, lock_path=LOCK):
    sources = preflight(directory, lock_path=lock_path)
    books = []
    total_pages = 0
    for item in sources:
        if item["source_kind"] != "textbook":
            continue
        parsed = prepare_source(item["path"], item["metadata"])
        pages = parsed["pages"]
        units = parsed["rows"]
        expected = next(x["expected_pages"] for x in COLLECTION["files"]
                        if x["key"] == item["key"])
        numbers = [x["ocr_global_page"] for x in pages]
        if numbers != list(range(1, expected + 1)):
            raise ValueError("OCR source chapter audit requires contiguous pages")
        total_pages += len(pages)
        by_chapter = defaultdict(list)
        chapter_pages = defaultdict(set)
        page_chapters = defaultdict(set)
        risks = defaultdict(Counter)
        unclassified = []
        for row in units:
            name = str(row.get("chapter") or "").strip()
            no = row.get("ocr_global_page")
            if name in UNKNOWN:
                unclassified.append(no)
                continue
            by_chapter[name].append(row)
            chapter_pages[name].add(no)
            page_chapters[no].add(name)
            risks[name].update(row.get("quality_flags") or [])
        chapters = []
        for name, rows in sorted(by_chapter.items(),
                                 key=lambda x: min(u["ocr_global_page"] for u in x[1])):
            indices = chapter_pages[name]
            chapters.append({
                "chapter_heading": name[:180],
                "first_ocr_page": min(indices),
                "last_ocr_page": max(indices),
                "pages_containing_chapter_units": len(indices),
                "source_paragraph_units": len(rows),
                "unit_risk_flag_counts": dict(sorted(risks[name].items())),
                "scientific_answer_verified": False,
            })
        unmapped_pages = sorted(set(range(1, expected + 1)) - set(page_chapters))
        books.append({
            "key": item["key"],
            "sha256": item["sha256"],
            "ocr_pages_total": len(pages),
            "ocr_unit_count": len(units),
            "mapped_chapter_count": len(chapters),
            "pages_with_any_mapped_chapter": len(page_chapters),
            "unmapped_ocr_pages": unmapped_pages,
            "unmapped_ocr_page_count": len(unmapped_pages),
            "unclassified_paragraph_units": len(unclassified),
            "pages_spanning_multiple_chapters": sorted(
                p for p, names in page_chapters.items() if len(names) > 1
            ),
            "chapters": chapters,
        })
    return {
        "collection_version": COLLECTION["version"],
        "mode": "read_only_same_parser_as_qa_import",
        "all_six_source_sha256_preflight_passed": len(sources) == 6,
        "ocr_pages_total": total_pages,
        "chapter_mapping_is_heuristic": True,
        "unmapped_pages_are_not_assumed_missing_or_invalid": True,
        "scientific_content_verified": False,
        "printed_page_numbers_verified": False,
        "postgresql_readback_verified": False,
        "exam_topic_alignment_verified": False,
        "books": books,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    value = report(args.directory)
    if value["ocr_pages_total"] != 1915:
        raise ValueError("Expected exactly 1915 OCR page markers")
    private_json(args.report, value)
    print("locked_six_source_chapter_coverage_screen_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
