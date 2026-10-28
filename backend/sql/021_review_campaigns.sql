-- User-defined financial limits are optional; historical amounts remain immutable.
ALTER TABLE ai_settings ADD COLUMN task_limit_enabled BOOLEAN NOT NULL DEFAULT true,
 ADD COLUMN daily_limit_enabled BOOLEAN NOT NULL DEFAULT true,
 ADD COLUMN monthly_limit_enabled BOOLEAN NOT NULL DEFAULT true;

CREATE TABLE ai_review_campaigns (
 scope_id TEXT PRIMARY KEY CHECK(length(scope_id)=64),
 user_id BIGINT NOT NULL REFERENCES users(id),
 policy JSONB NOT NULL,
 authorization_sha256 TEXT NOT NULL CHECK(length(authorization_sha256)=64),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER review_campaigns_immutable BEFORE UPDATE OR DELETE ON ai_review_campaigns
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE ai_review_campaign_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 scope_id TEXT NOT NULL REFERENCES ai_review_campaigns(scope_id),
 action TEXT NOT NULL CHECK(action IN ('authorized','activated','paused','completed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER review_campaign_events_immutable BEFORE UPDATE OR DELETE ON ai_review_campaign_events
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
