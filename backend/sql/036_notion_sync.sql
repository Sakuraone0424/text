-- Notion sync: two databases (训练记录, 错题本) under one parent page, filled from the learner's own results.
-- notion_state keeps the database ids; one job per submitted attempt; one row per question already filed in 错题本.
CREATE TABLE IF NOT EXISTS notion_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS notion_sync_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    attempt_id BIGINT NOT NULL UNIQUE REFERENCES attempts_v2(id),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_code TEXT,
    training_page_id TEXT,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS notion_sync_jobs_due ON notion_sync_jobs(state, next_retry);
CREATE TABLE IF NOT EXISTS notion_mistake_pages (
    user_id BIGINT NOT NULL REFERENCES users(id),
    question_id BIGINT NOT NULL,
    page_id TEXT NOT NULL,
    PRIMARY KEY(user_id, question_id)
);
CREATE TABLE IF NOT EXISTS notion_sync_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    job_id BIGINT,
    at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    ok BOOLEAN NOT NULL,
    code TEXT,
    detail JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS notion_sync_events_user ON notion_sync_events(user_id, id DESC);
