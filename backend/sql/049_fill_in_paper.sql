-- The objective paper is now 60 fill-in questions of 2.5 points (xiaohan-obj-fill-150-v6); the earlier objective paper (single choice + fill-in)
-- becomes its own kind, read-only. Scores and maxima may be fractions (2.5 points; 1.25 or 0.83 per blank).

ALTER TABLE papers_v2 DROP COLUMN IF EXISTS kind;
ALTER TABLE papers_v2 ADD COLUMN kind TEXT GENERATED ALWAYS AS (
    CASE blueprint->>'specification'
        WHEN 'xiaohan-obj-fill-150-v6' THEN 'objective'
        WHEN 'xiaohan-obj-150-v5' THEN 'objective_old'
        WHEN 'xiaohan-subj-150-v5' THEN 'subjective'
        ELSE 'mixed' END) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS papers_v2_user_day_kind ON papers_v2(user_id, paper_date, kind);

ALTER TABLE practice_answers ALTER COLUMN max_points TYPE DOUBLE PRECISION;

-- the score check reads the question's maximum as a number, not an integer
CREATE OR REPLACE FUNCTION protect_answer_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    maximum DOUBLE PRECISION;
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
    SELECT (i.snapshot->>'max_points')::double precision INTO maximum
    FROM attempts_v2 a JOIN paper_items_v2 i ON i.paper_id=a.paper_id
    WHERE a.id=NEW.attempt_id AND i.question_id=NEW.question_id;
    IF maximum IS NULL OR NEW.points > maximum + 1e-9 THEN
        RAISE EXCEPTION 'Answer must belong to paper and score must fit snapshot rubric';
    END IF;
    RETURN NEW;
END $$;
