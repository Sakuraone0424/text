-- Per-question time spent (seconds the learner actually looked at the question), copied from the draft at submit.
CREATE TABLE answer_timings (
    attempt_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    seconds INTEGER NOT NULL CHECK (seconds BETWEEN 0 AND 7200),
    PRIMARY KEY (attempt_id, question_id),
    FOREIGN KEY (attempt_id, question_id) REFERENCES answers_v2(attempt_id, question_id)
);

-- Short chapter / weak-point practice. Deliberately separate from papers: it never counts toward the
-- daily paper, its scores or history trends. Objective questions only, so each answer is checked at once.
CREATE TABLE practice_sets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    mode TEXT NOT NULL CHECK (mode IN ('chapter', 'weak')),
    subject TEXT,
    chapter TEXT,
    question_ids BIGINT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX practice_sets_user ON practice_sets(user_id, created_at DESC);
CREATE TABLE practice_answers (
    set_id BIGINT NOT NULL REFERENCES practice_sets(id),
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    answer JSONB NOT NULL,
    points INTEGER NOT NULL CHECK (points >= 0),
    max_points INTEGER NOT NULL CHECK (max_points > 0),
    seconds INTEGER CHECK (seconds BETWEEN 0 AND 7200),
    answered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (set_id, question_id)
);
