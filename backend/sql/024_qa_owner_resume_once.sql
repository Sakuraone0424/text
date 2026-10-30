-- Owner's explicit 2026-10-30 continuation is consumed only once.
-- A future manual pause is not silently undone by a worker restart.
CREATE TABLE qa_automation_resume_consumptions (
 approval_id TEXT PRIMARY KEY,
 scope_id TEXT NOT NULL REFERENCES qa_automation_grants(scope_id),
 user_id BIGINT NOT NULL REFERENCES users(id),
 consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER qa_automation_resume_consumptions_immutable
 BEFORE UPDATE OR DELETE ON qa_automation_resume_consumptions
 FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
