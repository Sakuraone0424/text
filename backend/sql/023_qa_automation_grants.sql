-- Independent QA grants never alter historical trial/campaign authorization.
CREATE TABLE qa_automation_grants (
 scope_id TEXT PRIMARY KEY CHECK(length(scope_id)=64),
 user_id BIGINT NOT NULL REFERENCES users(id),
 policy JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER qa_automation_grants_immutable BEFORE UPDATE OR DELETE ON qa_automation_grants
 FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
