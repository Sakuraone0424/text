-- 手动判分：用户可以给任何一道题手动打分或修改分数；AI 原始分数另存可查。
CREATE TABLE IF NOT EXISTS manual_grades (
    attempt_id BIGINT NOT NULL REFERENCES attempts_v2(id),
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    points INTEGER,                                  -- the manual score now in force (NULL after "hand back to the AI")
    criterion_points JSONB,
    note TEXT CHECK (note IS NULL OR length(note) <= 500),
    ai_points INTEGER,                               -- what the AI (or the original grader) gave before the first manual change
    ai_feedback JSONB,
    ai_graded_by TEXT,
    released_at TIMESTAMPTZ,                         -- handed back to the AI
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (attempt_id, question_id)
);
-- a score may be changed only by the manual grading code, which announces itself with app.manual_grade (transaction-local)
CREATE OR REPLACE FUNCTION protect_answer_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    maximum INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Submitted answers cannot be deleted';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.answer IS DISTINCT FROM OLD.answer OR NEW.attempt_id <> OLD.attempt_id
        OR NEW.question_id <> OLD.question_id
        OR (OLD.points IS NOT NULL AND COALESCE(current_setting('app.manual_grade', true), '') <> '1')
    ) THEN
        RAISE EXCEPTION 'Submitted answers and completed grades are immutable';
    END IF;
    SELECT (i.snapshot->>'max_points')::integer INTO maximum
    FROM attempts_v2 a JOIN paper_items_v2 i ON i.paper_id=a.paper_id
    WHERE a.id=NEW.attempt_id AND i.question_id=NEW.question_id;
    IF maximum IS NULL OR NEW.points > maximum THEN
        RAISE EXCEPTION 'Answer must belong to paper and score must fit snapshot rubric';
    END IF;
    RETURN NEW;
END $$;
