-- Work done outside the website's own worker: a Claude Code scheduled task (Routine) leases queued tasks over an API, runs them on the
-- owner's Claude subscription and submits the result. Its cost is 0 and never enters the DeepSeek ledger.

-- who did a task: NULL = the website's own DeepSeek worker; 'deepseek' / 'claude-subscription' pin a task to one executor
ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS executor TEXT CHECK (executor IS NULL OR executor IN ('deepseek','claude-subscription'));
ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS ext_session TEXT;
ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS ext_model_label TEXT;

CREATE TABLE IF NOT EXISTS external_leases (
    lease_id TEXT PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES ai_jobs(id),
    kind TEXT NOT NULL CHECK (kind IN ('generation','review','grading')),
    session TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','submitted','released','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    model_label TEXT,
    outcome TEXT,                 -- passed / blocked / repair_requested (the program checks' verdict on the submitted result)
    outcome_detail JSONB
);
CREATE INDEX IF NOT EXISTS external_leases_job ON external_leases(job_id, state);
CREATE INDEX IF NOT EXISTS external_leases_open ON external_leases(state, expires_at);

-- which session wrote a bank question (a session never reviews its own questions)
CREATE TABLE IF NOT EXISTS external_question_sessions (
    question_id BIGINT PRIMARY KEY REFERENCES reviewed_questions(id),
    session TEXT NOT NULL,
    job_id BIGINT REFERENCES ai_jobs(id)
);

-- blind comparison of the two executors on identical writing tasks
CREATE TABLE IF NOT EXISTS comparison_sets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    revealed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS comparison_pairs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    set_id BIGINT NOT NULL REFERENCES comparison_sets(id),
    position SMALLINT NOT NULL,
    subject TEXT NOT NULL,
    question_type TEXT NOT NULL,
    deepseek_job BIGINT REFERENCES ai_jobs(id),
    claude_job BIGINT REFERENCES ai_jobs(id),
    a_is TEXT NOT NULL CHECK (a_is IN ('deepseek','claude')),     -- which executor is shown as 甲 (never sent to the page before the reveal)
    UNIQUE (set_id, position)
);
CREATE TABLE IF NOT EXISTS comparison_ratings (
    pair_id BIGINT NOT NULL REFERENCES comparison_pairs(id),
    side TEXT NOT NULL CHECK (side IN ('A','B')),
    realism SMALLINT NOT NULL CHECK (realism BETWEEN 1 AND 5),
    correct SMALLINT NOT NULL CHECK (correct BETWEEN 1 AND 5),
    rubric SMALLINT NOT NULL CHECK (rubric BETWEEN 1 AND 5),
    note TEXT CHECK (note IS NULL OR length(note) <= 300),
    rated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (pair_id, side)
);
