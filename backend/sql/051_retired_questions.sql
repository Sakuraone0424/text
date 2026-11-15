-- Questions the owner took out of use (the bank was judged too weak and is being written again). Soft: nothing is deleted; what a question
-- was before is kept so it can be put back (by subject and type) from the 模型与费用 page.
CREATE TABLE IF NOT EXISTS question_retirements (
    question_id BIGINT PRIMARY KEY REFERENCES reviewed_questions(id),
    reason TEXT NOT NULL,
    prev_question_status TEXT NOT NULL,
    prev_state_status TEXT,
    prev_state_reason TEXT,
    retired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    restored_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS question_retirements_open ON question_retirements(question_id) WHERE restored_at IS NULL;
