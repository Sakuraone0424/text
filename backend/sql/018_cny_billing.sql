-- CNY is a new ledger, never a relabel/conversion of unknown historical USD.
ALTER TABLE ai_settings ADD COLUMN daily_cny NUMERIC NOT NULL DEFAULT 0 CHECK(daily_cny>=0), ADD COLUMN monthly_cny NUMERIC NOT NULL DEFAULT 0 CHECK(monthly_cny>=0), ADD COLUMN task_cny NUMERIC NOT NULL DEFAULT 0 CHECK(task_cny>=0), ADD COLUMN trial_cny NUMERIC NOT NULL DEFAULT 0 CHECK(trial_cny>=0), ADD COLUMN trial_active BOOLEAN NOT NULL DEFAULT true, ADD COLUMN billing_currency TEXT NOT NULL DEFAULT 'CNY' CHECK(billing_currency='CNY');
-- Pause any pre-migration activation until the user explicitly approves CNY limits.
UPDATE ai_settings SET stopped=true;
ALTER TABLE ai_jobs ADD COLUMN reserved_cny NUMERIC NOT NULL DEFAULT 0 CHECK(reserved_cny>=0), ADD COLUMN estimated_cny NUMERIC CHECK(estimated_cny>=0), ADD COLUMN billing_status TEXT NOT NULL DEFAULT 'not_called';
ALTER TABLE ai_call_events ADD COLUMN provider_request_confirmed BOOLEAN;
ALTER TABLE ai_jobs DROP CONSTRAINT ai_jobs_state_check;
ALTER TABLE ai_jobs ADD CONSTRAINT ai_jobs_state_check CHECK(state IN ('pending','running','succeeded','failed','retrying','blocked','cancelled'));
CREATE TABLE ai_cny_prices (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, model_id TEXT NOT NULL, model_version TEXT,
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'), version TEXT NOT NULL UNIQUE,
 valid_from TIMESTAMPTZ NOT NULL, valid_until TIMESTAMPTZ NOT NULL CHECK(valid_until>valid_from),
 official_effective_at TIMESTAMPTZ, source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 verified_at TIMESTAMPTZ NOT NULL, rules JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER cny_prices_immutable BEFORE UPDATE OR DELETE ON ai_cny_prices FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE ai_cny_reservations (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES ai_jobs(id), attempt INTEGER NOT NULL,
 amount_cny NUMERIC NOT NULL CHECK(amount_cny>=0), price_snapshot JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE(job_id,attempt)
);
CREATE TRIGGER cny_reservations_immutable BEFORE UPDATE OR DELETE ON ai_cny_reservations FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE ai_cny_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
 job_id BIGINT NOT NULL REFERENCES ai_jobs(id), attempt INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('usage_estimate','unknown','bill_confirmation','bill_adjustment')),
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'), amount_cny NUMERIC,
 detail JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER cny_events_immutable BEFORE UPDATE OR DELETE ON ai_cny_events FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE ai_settings_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id),
 settings JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER settings_events_immutable BEFORE UPDATE OR DELETE ON ai_settings_events FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE FUNCTION record_ai_settings() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 INSERT INTO ai_settings_events(user_id,settings) VALUES(NEW.user_id,to_jsonb(NEW)-'daily_usd'-'monthly_usd'); RETURN NEW;
END $$;
CREATE TRIGGER ai_settings_log AFTER INSERT OR UPDATE ON ai_settings FOR EACH ROW EXECUTE FUNCTION record_ai_settings();
-- Historical snapshots/events are preserved verbatim. Legacy USD columns are archived.
CREATE FUNCTION freeze_legacy_usd() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF (TG_TABLE_NAME='ai_settings' AND (to_jsonb(NEW)->'daily_usd' IS DISTINCT FROM to_jsonb(OLD)->'daily_usd' OR to_jsonb(NEW)->'monthly_usd' IS DISTINCT FROM to_jsonb(OLD)->'monthly_usd')) OR
 (TG_TABLE_NAME='ai_jobs' AND (to_jsonb(NEW)->'reserved_usd' IS DISTINCT FROM to_jsonb(OLD)->'reserved_usd' OR to_jsonb(NEW)->'actual_usd' IS DISTINCT FROM to_jsonb(OLD)->'actual_usd')) THEN RAISE EXCEPTION 'Legacy USD fields are read-only; no implicit currency conversion'; END IF; RETURN NEW;
END $$;
CREATE TRIGGER legacy_settings_currency BEFORE UPDATE ON ai_settings FOR EACH ROW EXECUTE FUNCTION freeze_legacy_usd();
CREATE TRIGGER legacy_jobs_currency BEFORE UPDATE ON ai_jobs FOR EACH ROW EXECUTE FUNCTION freeze_legacy_usd();

CREATE TRIGGER legacy_usd_reservations_immutable BEFORE UPDATE OR DELETE ON ai_budget_reservations FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE ai_cny_price_resolutions (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, model_id TEXT NOT NULL,
 price_id BIGINT NOT NULL REFERENCES ai_cny_prices(id), evidence_sha256 TEXT NOT NULL,
 basis TEXT NOT NULL CHECK(length(basis)>=40), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER cny_price_resolution_immutable BEFORE UPDATE OR DELETE ON ai_cny_price_resolutions FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
