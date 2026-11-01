-- One independent review round (plus the free program checks) replaces the
-- two-round gate. Publication of an automatically checked question now needs
-- the current 'identity' (program checks) event AND the current 'review'
-- event of a really completed model call. Older two-round events (policy
-- auto-evidence/1) no longer qualify. Manually audited questions keep the
-- audit path unchanged.
ALTER TABLE auto_question_states ALTER COLUMN policy_version SET DEFAULT 'auto-evidence/2';

CREATE OR REPLACE FUNCTION protect_reviewed_question() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'New questions must be drafts';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.content IS DISTINCT FROM OLD.content THEN
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
