# 2026-10-29 | Source-grounded chapter coverage and exam-topic candidate gate

## Scope and evidence class

**Read-only results from the actual six locked private source files.** The four OCR TXT byte hashes were checked against the repository lock. This chapter audit reproduces the repository's `source_structure.book_layout` heading-selection rules in an independent local check, but **has not yet been compared against a committed QA PostgreSQL import**. The user originals are NOT stored in GitHub.

| Corrected OCR source | Observed chapter groups | OCR pages bearing chapter units | OCR pages outside mapped chapter body |
| --- | ---: | ---: | ---: |
| Biochemistry upper | 13 | 619/654 | 35 |
| Biochemistry lower | 14 | 568/587 | 19 |
| Biochemistry notes | 36 | 287/289 | 2 |
| Cell Biology 5th Ed. | 16 | 369/385 | 16 |
| **Total** | **79** | **1843/1915** | **72** |

**Caution:** the 72 chapter-unmapped OCR pages are primarily candidates for prefaces, covers, indexes or tables of contents, not 72 detected missing pages. All 1915 page markers remain contiguous. Chapter mapping is structural, not the scientific validation of each paragraph, diagram, equation or all definitions.

The checked-in `app.offline_chapter_coverage` CLI now enumerates, for each source-supplied chapter heading, the first/last OCR page, pages containing chapter text, paragraph units, unclassified pages and unit-level OCR risk flag counts. No textbook excerpts or paths are exported.

## Phase 1 topic ↔ genuine historical exam bridge

The new `app.offline_topic_linkage` CLI first checks all **six** pinned originals, then uses `prepare_source` and `transcript_analysis.candidates` to consider only question-start paragraphs with known year/type and no unresolved image/table/OCR uncertainty. It extracts explicit headings from the supplied OCR material, tests only conservative **verbatim** chapter/section phrase overlap, and produces a review queue by subject/type and chapter.

The bridge **never** infers a missing definition, assigns an answer, fabricates a historical exam year or counts a lexical match as scientific support. Unlinked exam questions remain reviewable: absence of an exact heading phrase is NOT evidence of topic absence.

Independent raw-DOCX chapter-title-only overlap screening (a **lower-bound preview**, not the backend parser) found very sparse links on cell-biology material; this reinforces the need to map against finer source sections/paragraph concepts and to perform independent science+answer model rounds before approval. The private local metadata JSON is kept outside GitHub.

## Extra fail-closed risk rule

DOCX can place an essential table, figure or OCR-uncertain continuation in the paragraph **after** the question stem. The `transcript_analysis` classifier now promotes relevant risks from continuation/subquestion/option paragraphs to their parent question candidate, including `docx_table_requires_review`, `complex_table`, `ocr_uncertain`, `transcription_gap_reported`, `image_dependent`, `untranscribed_image` and `corrupt_ocr`.

Both `exam_style_readiness` and `auto_generation.select_exam_style` now exclude these unresolved questions from the safe style pool. A table appearing as a separate Word block no longer makes its preceding stem appear fully readable.

## What has NOT been accepted

- No physically isolated PostgreSQL has been activated; the dedicated QA Railway project holds only staged Postgres 17 + volume changes.
- No six-source post-commit readback and persisted original scientific knowledge graph.
- No 120 source-grounded questions independently approved by two actual AI review rounds.
- No full 60-question / 150-point exam using verified originals.
- No production rollout, no paid AI inference, no original 28-question historical mutations.

**Next:** run the module on private originals in an authorized independent QA environment, compare the chapter and 8-bucket counts with source_documents/source_units/exam_transcript_candidates, resolve chapter-unmapped page types, then build confirmed textbook concept-to-exam links. The locked byte identities and all eight original exam-style buckets remain the sole input basis.
