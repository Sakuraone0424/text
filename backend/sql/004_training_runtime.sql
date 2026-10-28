-- Existing papers, scores, questions and applied migrations are untouched.
CREATE TABLE IF NOT EXISTS training_sessions (
    paper_id BIGINT PRIMARY KEY REFERENCES papers_v2(id),
    started_at TIMESTAMPTZ NOT NULL,
    deadline TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    submitted_at TIMESTAMPTZ,
    CHECK (deadline = started_at + INTERVAL '60 minutes')
);
CREATE TABLE IF NOT EXISTS answer_drafts (
    paper_id BIGINT NOT NULL REFERENCES training_sessions(paper_id),
    question_id BIGINT NOT NULL,
    value JSONB NOT NULL,
    PRIMARY KEY(paper_id,question_id),
    FOREIGN KEY(paper_id,question_id) REFERENCES paper_items_v2(paper_id,question_id)
);
CREATE TABLE IF NOT EXISTS answer_images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paper_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    media_type TEXT NOT NULL,
    content BYTEA NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 8388608),
    sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(paper_id,question_id) REFERENCES paper_items_v2(paper_id,question_id)
);
CREATE TABLE IF NOT EXISTS handwriting_transcriptions (
    image_id BIGINT PRIMARY KEY REFERENCES answer_images(id),
    transcription TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    confirmed BOOLEAN NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS fsrs_cards (
    user_id BIGINT NOT NULL REFERENCES users(id),
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    card JSONB NOT NULL,
    due TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,question_id)
);
CREATE TABLE IF NOT EXISTS fsrs_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 4),
    before_card JSONB NOT NULL,
    after_card JSONB NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL,
    UNIQUE(user_id,event_key),
    FOREIGN KEY(user_id,question_id) REFERENCES fsrs_cards(user_id,question_id)
);
CREATE TABLE IF NOT EXISTS archive_consent (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    enabled BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS archive_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id BIGINT NOT NULL UNIQUE REFERENCES attempts_v2(id),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','synced','verified','failed')),
    payload JSONB NOT NULL,
    digest TEXT NOT NULL,
    remote_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    error TEXT,
    verified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_training_deadline ON training_sessions(deadline) WHERE submitted_at IS NULL;
ALTER TABLE paper_items_v2 ADD COLUMN IF NOT EXISTS is_retest BOOLEAN NOT NULL DEFAULT false;
