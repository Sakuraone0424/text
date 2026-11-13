-- The worker runs as a fresh process each time, so "this question was verified a few minutes ago" cannot live in the process's memory:
-- it is kept with the question's state. (Not part of the question's content; changing it never touches updated_at.)
ALTER TABLE auto_question_states ADD COLUMN IF NOT EXISTS stale_checked_at TIMESTAMPTZ;
ALTER TABLE auto_question_states ADD COLUMN IF NOT EXISTS stale_checked_sha TEXT;
