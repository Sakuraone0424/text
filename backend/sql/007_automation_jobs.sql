CREATE TABLE IF NOT EXISTS automation_jobs (
    job_key TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    target_date DATE NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','prepared','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    paper_id BIGINT REFERENCES papers_v2(id),
    error TEXT
);
