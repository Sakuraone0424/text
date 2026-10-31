# Independent six-source QA import — reproducible acceptance runbook

**Status as of 2026-10-29:** independent Railway project is created, PostgreSQL 17 service+volume are STAGED only; **no database is running; no source bytes have been imported or read back from QA**. Do not conflate a GitHub synthetic test or offline OCR scan with a real DB acceptance.

## Isolated, private Railway resources

- Project `ustc-exam-ocr-qa` — `0ff7ff18-e746-42db-8eb2-b515306fa473`
- Environment `qa` — `246e3b1c-811b-4e02-ba42-89cb3e19f2cb`
- Staged service `Postgres` — `5499828a-1a90-4cb3-bc48-a74aff127b31`
- Image pinned to `ghcr.io/railwayapp-templates/postgres-ssl:17`, matching the existing production PostgreSQL major version.
- Service and dedicated volume are **staged**; Railway `accept-deploy` has NOT been called. Launching a service can incur charges and is outside this preparatory step.
- Existing production project `amiable-vibrancy`: `c6028313-e1f4-43cc-a63d-b7ff474a3a24`; **never use its database, service, volume or credentials**.

## Pre-deployment integrity requirements

1. Ensure a physically independent, explicitly authorized QA PostgreSQL endpoint exists. Do not place the six sources in public GitHub, application logs or documentation.
2. Obtain private six-source directory from the owner-supplied QA ZIP; its structure is defined by `backend/app/ocr_collection.json`.
3. Confirm `SOURCE_STAGING_DATABASE_URL` targets the independent QA instance. Provide `DATABASE_URL` / `PRODUCTION_DATABASE_URL` only in a safe configuration that allows the protection code to reject a shared endpoint. No secrets in CLI arguments.
4. Avoid running the import from a Railway production runtime. Schema name must match `source_staging_*`.

## Commands **after** authorized QA Postgres activation

All commands are from the `backend/` directory. Example output location must be private and initially absent:

```bash
# Read-only file preflight. No database writes or provider requests.
python -m app.collection_seed \
  --directory /private/locked-six-sources \
  --report /private/audits/preflight.json

# Fresh-schema, versioned-migration and six-file import in ONE transaction.
# Uses SOURCE_STAGING_DATABASE_URL and refuses production/shared known endpoints.
python -m app.collection_seed \
  --directory /private/locked-six-sources \
  --write-isolated --initialize-schema --target staging \
  --schema source_staging_ocr_20261029 \
  --report /private/audits/qa-import-postcommit.json

# Independent, read-only integrity check on the committed QA schema.
python -m app.release_source_gate \
  --schema source_staging_ocr_20261029 --target staging \
  --report /private/audits/qa-six-source-readback.json
```

The `--initialize-schema` mode must fail if the schema already exists, and its CREATE SCHEMA / SQL migrations / six imports must commit or roll back together. A success report is written only **after** this transaction commits and an independent READ-ONLY connection checks every current file SHA and index again.

## Non-negotiable acceptance fields

- Source count `6/6` with byte-for-byte locked SHA-256 and source-index verification.
- OCR markers `654/654 + 587/587 + 289/289 + 385/385 = 1915`; no unexpected missing markers or blank-page flags.
- Original exam DOCX media `27 + 4 = 31`, including WDP and EMF as **unverified image content**.
- Eight genuine source exam-style buckets; report missing real type/year coverage rather than making up examples.
- No questions become published merely by importing material. Actual scientifically approved `60/120` inventory must be reported separately.
- No production database reads/writes or user history mutations are authorized by these commands.

**Reminder:** The QA project and staged service do not, by themselves, prove any database import. Real acceptance requires a running isolated instance, byte readback, then authorized double-AI review and 60q end-to-end tests.
