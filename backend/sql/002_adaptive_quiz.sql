BEGIN;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS external_id VARCHAR(80);

CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_external_id
    ON questions(external_id);

COMMIT;
