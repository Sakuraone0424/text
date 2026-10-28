-- Additive automation ledger. No human event is rewritten or relabelled.
CREATE TABLE ai_model_catalog (
 model_id TEXT PRIMARY KEY, metadata JSONB NOT NULL, first_seen TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 last_seen TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), available BOOLEAN NOT NULL DEFAULT true,
 compatibility JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE ai_catalog_events (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, fetched_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), success BOOLEAN NOT NULL, error_code TEXT);
CREATE TABLE ai_settings (user_id BIGINT PRIMARY KEY REFERENCES users(id), model_id TEXT NOT NULL DEFAULT 'deepseek-flash', effort TEXT NOT NULL DEFAULT 'high', auto_discover BOOLEAN NOT NULL DEFAULT true, auto_upgrade BOOLEAN NOT NULL DEFAULT false, daily_usd NUMERIC NOT NULL DEFAULT 0 CHECK(daily_usd>=0), monthly_usd NUMERIC NOT NULL DEFAULT 0 CHECK(monthly_usd>=0), token_limit INTEGER NOT NULL DEFAULT 4096 CHECK(token_limit BETWEEN 128 AND 32768), retry_limit INTEGER NOT NULL DEFAULT 2 CHECK(retry_limit BETWEEN 0 AND 5), max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK(max_concurrency BETWEEN 1 AND 4), stopped BOOLEAN NOT NULL DEFAULT true, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TABLE ai_jobs (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_key TEXT UNIQUE NOT NULL, user_id BIGINT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','succeeded','failed','retrying','blocked')),
 settings_snapshot JSONB NOT NULL, payload JSONB NOT NULL, result JSONB, attempts INTEGER NOT NULL DEFAULT 0,
 reserved_usd NUMERIC NOT NULL DEFAULT 0, actual_usd NUMERIC, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
 lease_token TEXT, lease_until TIMESTAMPTZ, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, next_retry TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ai_call_events (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES ai_jobs(id), model_id TEXT NOT NULL, effort TEXT NOT NULL, model_version TEXT, usage JSONB NOT NULL DEFAULT '{}', outcome TEXT NOT NULL, error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER ai_calls_immutable BEFORE UPDATE OR DELETE ON ai_call_events FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE auto_question_states (question_id BIGINT PRIMARY KEY REFERENCES reviewed_questions(id), status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','checking','repairing','ready','blocked','retired')), content_sha256 TEXT NOT NULL, policy_version TEXT NOT NULL DEFAULT 'auto-evidence/1', reason TEXT, revision_count INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TABLE auto_quality_events (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, question_id BIGINT NOT NULL REFERENCES reviewed_questions(id), content_sha256 TEXT NOT NULL, policy_version TEXT NOT NULL, phase TEXT NOT NULL, run_key TEXT NOT NULL UNIQUE, job_id BIGINT REFERENCES ai_jobs(id), evidence_snapshot JSONB NOT NULL, result JSONB NOT NULL, passed BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER auto_quality_immutable BEFORE UPDATE OR DELETE ON auto_quality_events FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE auto_knowledge_events (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, unit_id BIGINT NOT NULL REFERENCES source_units(id), unit_sha256 TEXT NOT NULL, job_id BIGINT REFERENCES ai_jobs(id), relation JSONB NOT NULL, status TEXT NOT NULL CHECK(status IN ('candidate','ready','blocked','retired')), run_key TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER auto_knowledge_immutable BEFORE UPDATE OR DELETE ON auto_knowledge_events FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE ai_budget_reservations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES ai_jobs(id), attempt INTEGER NOT NULL, usd NUMERIC NOT NULL CHECK(usd>=0), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE(job_id,attempt));

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
            AND (e.phase='identity' OR (e.phase IN ('science','answer') AND j.state='succeeded' AND j.result->>'real_api'='true' AND j.payload->>'content_sha256'=s.content_sha256)))=3)
        THEN RAISE EXCEPTION 'Publication requires complete current automatic quality rounds'; END IF;
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
ALTER TABLE ai_jobs ADD COLUMN dispatched_at TIMESTAMPTZ;
