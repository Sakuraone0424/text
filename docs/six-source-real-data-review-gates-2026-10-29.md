# Six-source real-data validation and review queue (2026-10-29)

## Evidence and acceptance boundary

The six user-owned source revisions are SHA-256 locked. Their private offline
recheck found 1,915 OCR page markers, 12,302 text units, and 31 Word media
objects. An **independent offline parser** indexed these units; this is not
equivalent to an authenticated six-document PostgreSQL import. Its parser
parity remains a check to run against the canonical backend importer.

A private review-workbench run on those real originals recorded 3,151
distinct units with risk markers, expanded to 4,414 text-unit risk occurrences
plus 31 DOCX-media objects, i.e. **4,445 unresolved review entries**.
One unit can legitimately have several flagged risks. An earlier subset
of 1,521 entries covered particularly image/structure/table risks; it
does **not** represent the complete risk register. These are **suspected
risks, not confirmed source errors**.

This repository now includes `app.offline_source_review_queue`, using the
**canonical six-source preflight and canonical parser** to emit the complete
private issue ledger. There is no source text, exam stem, answer, original
image or private path in its machine-readable metadata report. No unit or
media item can be automatically marked approved by constructing this ledger.

Run from `backend` on a machine that can access the six original files:

```bash
python -m app.offline_source_review_queue \
  --directory /private/locked-six-sources \
  --output /private/audits/ocr-risk-queue.sqlite \
  --report /private/audits/ocr-risk-audit.json
```

The output is **create-only**. A real run must compare the resulting parser
unit count, risk counts, SHA identities and page/media counts with the
independent private offline audit. Differences require investigation.

## Isolated QA PostgreSQL status

The Railway project `ustc-exam-ocr-qa`, environment `qa`, has an online
PostgreSQL 17 deployment with a persistent volume. **This establishes database
availability, not an import of source bytes.** The current local execution
container cannot resolve its external TCP hostname, so it cannot perform a
direct authenticated ingest or readback. Neither activating a public TCP proxy
nor passing a synthetic database test counts as import completion.

For an authorized, private network-capable QA runner with the locked six
files available locally:

```bash
export SOURCE_STAGING_DATABASE_URL='<qa-only-secret-connection-url>'
python -m app.collection_seed \
  --directory /private/locked-six-sources \
  --write-isolated --initialize-schema \
  --schema source_staging_ocr_20261029 \
  --target staging \
  --report /private/audits/qa-import-readback.json
```

Do not place secrets, source contents, or the resulting private SQLite files
in a public GitHub repository. Use a fresh isolated QA schema; never run this
against production. Confirm the independent read-only postcommit audit and
file identities before recording Stage 1 as complete. Store the 31 media
items and unresolved scientific/visual risks for separate review.

## Stages 2 through 7

- The private pilot generated 11 literal textbook/exam relationship candidates
  with 53 textbook anchors and 38 exam anchors. These **require independent
  semantic/scientific review**, and are not approved knowledge-graph edges.
- Historic exam year/type evidence describes only what the supplied DOCX
  transcription actually contains; it does not independently authenticate
  the university's original exam image.
- New generation and bank admission require pinned textbook evidence,
  exam-style provenance, the exact 150-point/60-minute blueprint, independent
  science and answer rounds, and fresh-source readback.
- A draft or a model response by itself must never increment the
  120-question qualified reserve. Only actual published, two-round-ready
  candidates count.
- The existing production 28-question flow and prior results must remain
  unchanged until real-data QA, the reserve and the end-to-end training
  acceptance gate all pass.

**As of this note:** local evidence review advanced, QA database online;
real six-file PostgreSQL ingest, scientific chapter mapping, verified
120-question reserve, live 60-question learner test, and final production
cutover are **not** yet accepted.
