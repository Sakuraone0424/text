# 2026-10-29 | Six-source offline exam-style evidence (no QA database)

This record is **not** scientific answer acceptance, 120-question stocking, visual recognition or a production release. It documents a read-only preliminary parse of the **two user-designated genuine examination DOCX files**, using their pinned original bytes. No historic exam wording or images are copied into Git.

## Independent local observed four-type candidate starts

The following counts come from a conservative standalone Word paragraph / native numbering / year-section scan of the **actual two DOCX files**, not from mocked source material. It follows the current style-parser classification, but exact parity against the PostgreSQL source-index table is **not yet proven**. The numbers denote potential *readable style anchors*, **not publishable questions**.

| Subject | Single choice | Fill blank | Term explanation | Short answer |
| --- | ---: | ---: | ---: | ---: |
| Biochemistry | 106 | 506 | 230 | 254 |
| Cell biology | 35 | 69 | 268 | 154 |

Real source preview: all eight subject/type buckets have at least one conservatively classified style example. The cell biology selection/fill anchors are older-year examples; do not mislabel them as recent official exam formats. The source documents do not independently contain official answers to every item. Some questions are figure dependent, even when separate paragraphs appear readable.

## Exact source-byte identities

- Biochemistry exam DOCX SHA-256: `6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001`
- Cell biology exam DOCX SHA-256: `63b0eea0b1dbb5a7b6323dcd769792bba60a96ce9d93c6b8bd8c79fd4939ef79`
- Media objects: 27 biochemistry, 4 cell; all 31 have Word drawing relationships. The WDP/EMF formats remain **visually unverified**.

## How to obtain the authoritative, backend-parser counts with no PostgreSQL

Run the added CLI with the existing **PRIVATE** six-file source directory (from the user-provided private QA ZIP) and the checked-in lock:

```bash
cd backend
python -m app.offline_exam_style_audit \
  --directory /private/source-collection \
  --report /private/audits/offline-exam-style.json
```

The command rejects changed/missing source bytes, checks 1915 sequential OCR page markers and both pinned DOCX media counts, invokes the same `prepare_source` + `transcript_analysis.candidates` code used by the SQL importer, and creates a `0600` report containing counts, years, risk categories and hashes **without question excerpts**.

It does **not** populate SQL, run AI or certify any evidence. The definitive T1.5 count should be compared against `exam_style_readiness.report` after the physically separate QA PostgreSQL exists.

**Remaining P0:** isolated source-staging Postgres; SQL migration + all-six byte readback; generation inventory and independent double-AI approval; actual 60q selector on verified originals. Production and old 28q history remain untouched.
