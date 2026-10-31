# Private offline six-source full-text QA index

**Status:** the owner-supplied, locked six source files can be indexed using the
same `sources.prepare_source` OCR/DOCX parser as the planned PostgreSQL
importer. The generated SQLite file is **private user content**, not a public
GitHub artifact, a scientific QA certificate, a completed PostgreSQL import
or a published 60-question bank.

## Command

Run from `backend/` in an authorized private filesystem, with the exact six
relative paths specified in `app/ocr_collection.json`. No network, AI
provider or PostgreSQL connection is required:

```bash
python -m app.offline_source_search \
  --directory /private/locked-six-sources \
  --db /private/audits/source-text-index.sqlite \
  --report /private/audits/source-text-index-audit.json
```

The command preflights **all six** original SHA-256 byte identities, 1915
sequential OCR page markers and 27+4 DOCX media objects. A changed/missing
source aborts indexing. SQLite stores individual text units with the original
source key, OCR global page, source-supplied chapter/section, DOCX structural
coordinates, text SHA and unresolved risk flags. An FTS5 trigram search index
supports Chinese technical terms and Latin abbreviations. The corpus source
files are not copied verbatim as raw original DOCX/TXT blobs into the index;
only the extracted source text units enter it.

The output file is created with private 0600 permissions and never overwrites
an existing index; after COMMIT the command reopens it read-only and verifies
source identities, unit counts and `PRAGMA integrity_check`. The separate
JSON report omits private paths and book/exam paragraph excerpts.

A Python application can call:

```python
from app.offline_source_search import search
matches = search(
    "/private/audits/source-text-index.sqlite",
    "草酰乙酸", limit=20
)
for match in matches:
    print(match["source_key"], match["ocr_page"], match["risk_flags_json"])
```

Search hits are **location candidates only**. A retrieved paragraph, an OCR
hash, a matching heading, or a recognized term does not mean that a scientific
claim is correct, that a formula/figure has been transcribed faithfully, that
a question has an answer, or that either independent model review has passed.
Do not let a downstream task set a `scientifically_approved` flag in this
offline index; the schema fixes approval fields to zero. Real science/answer
approval belongs to the separate `auto_quality` proof pipeline.

## Existing temporary local search artifact (not in GitHub)

An earlier independent local extractor indexed **12,302 text blocks** from
the same six locked originals and passed SQLite readback: 1915 OCR markers
and 31 media files were accounted for. That preliminary local cache is useful
for private retrieval but has **not** been shown byte-identical to the
authoritative parser's unit mapping. The checked-in CLI above is the
reproducible parser-aligned successor. Do not claim its exact row count until
it has been run against the actual six originals.

## Stage 1 acceptance still open

- Physically separate Railway QA project `ustc-exam-ocr-qa`, currently
  PostgreSQL 17+volume **staged, not provisioned**.
- Six-source actual PostgreSQL import, commit and independent readback absent.
- Candidate-to-concept mapping currently lexical and not science-verified.
- Two actual independent AI reviews and 120 approved ready questions absent.
- Existing production and historical results remain unchanged.
