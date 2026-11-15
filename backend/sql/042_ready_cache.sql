-- A remembered answer to "is this question ready (reviewed, evidence pinned, current content)?" so the 5-minute worker run
-- does not re-verify every stocked question each time. Used only for planning counts and the progress page; building a paper
-- still checks the question itself.
CREATE TABLE IF NOT EXISTS question_ready_cache (
    question_id BIGINT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    ok BOOLEAN NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
