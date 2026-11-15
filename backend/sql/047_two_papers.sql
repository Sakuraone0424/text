-- Two independent daily papers (objective + subjective) and proportionally rescaled question points.

-- 1. A learner may have one paper of each kind per day. The kind comes from the paper's specification; papers made before
--    this change are "mixed" (the old 60-question paper) and stay exactly as they are.
ALTER TABLE papers_v2 ADD COLUMN IF NOT EXISTS kind TEXT GENERATED ALWAYS AS (
    CASE blueprint->>'specification'
        WHEN 'xiaohan-obj-150-v5' THEN 'objective'
        WHEN 'xiaohan-subj-150-v5' THEN 'subjective'
        ELSE 'mixed' END) STORED;
ALTER TABLE papers_v2 DROP CONSTRAINT IF EXISTS papers_v2_user_id_paper_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS papers_v2_user_day_kind ON papers_v2(user_id, paper_date, kind);

-- 2. Scores may be halves (a rubric point can be 0.5 after rescaling).
ALTER TABLE answers_v2 ALTER COLUMN points TYPE DOUBLE PRECISION;
ALTER TABLE practice_answers ALTER COLUMN points TYPE DOUBLE PRECISION;
ALTER TABLE manual_grades ALTER COLUMN points TYPE DOUBLE PRECISION;
ALTER TABLE manual_grades ALTER COLUMN ai_points TYPE DOUBLE PRECISION;

-- A paper's time limit may differ from one hour (the subjective paper's is configurable): the deadline must still lie after the start,
-- at most four hours away.
ALTER TABLE training_sessions DROP CONSTRAINT IF EXISTS training_sessions_check;
ALTER TABLE training_sessions ADD CONSTRAINT training_sessions_deadline_check
    CHECK (deadline > started_at AND deadline <= started_at + INTERVAL '4 hours');

-- 3. What a question's scoring was before it was rescaled (so its earlier quality review stays valid: the review is bound to
--    the content as it was reviewed).
CREATE TABLE IF NOT EXISTS question_scoring_originals (
    question_id BIGINT PRIMARY KEY REFERENCES reviewed_questions(id),
    original JSONB NOT NULL,              -- {"max_points": n, "rubric": [points...], "blanks": [points...]}
    rescaled_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 4. Content stays immutable, except that the rescaling code (announcing itself with app.rescale_scoring) may change the
--    scoring fields of a question. Nothing else about it may differ.
CREATE OR REPLACE FUNCTION protect_reviewed_question() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'New questions must be drafts';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.content IS DISTINCT FROM OLD.content THEN
        IF COALESCE(current_setting('app.rescale_scoring', true), '') = '1'
           AND NEW.status IS NOT DISTINCT FROM OLD.status
           AND (NEW.content - 'max_points' - 'rubric' - 'blanks') = (OLD.content - 'max_points' - 'rubric' - 'blanks') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Question content is immutable; create a new revision';
    END IF;
    IF NEW.status = 'published' AND EXISTS(SELECT 1 FROM auto_question_states WHERE question_id=NEW.id) THEN
        IF NOT EXISTS(SELECT 1 FROM auto_question_states s WHERE s.question_id=NEW.id AND s.status='ready'
          AND (SELECT count(DISTINCT e.phase) FROM auto_quality_events e LEFT JOIN ai_jobs j ON j.id=e.job_id
            WHERE e.question_id=NEW.id AND e.content_sha256=s.content_sha256 AND e.policy_version=s.policy_version AND e.passed
            AND e.id=(SELECT max(e2.id) FROM auto_quality_events e2 WHERE e2.question_id=e.question_id AND e2.phase=e.phase AND e2.content_sha256=e.content_sha256 AND e2.policy_version=e.policy_version)
            AND (e.phase='identity' OR (e.phase='review' AND j.kind='quality_review' AND j.state='succeeded' AND j.result->>'real_api'='true' AND j.payload->>'content_sha256'=s.content_sha256)))=2)
        THEN RAISE EXCEPTION 'Publication requires the current program checks and one passed independent review'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.status = 'published' AND NOT EXISTS (
        SELECT 1 FROM question_audits a WHERE a.question_id = NEW.id
        AND a.content_snapshot = NEW.content AND a.passed
        AND a.issues = '[]'::jsonb
        AND a.id = (SELECT MAX(id) FROM question_audits WHERE question_id = NEW.id)
    ) THEN
        RAISE EXCEPTION 'Publication requires a passing current audit';
    END IF;
    RETURN NEW;
END $$;
