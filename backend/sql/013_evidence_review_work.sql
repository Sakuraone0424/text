-- Automated work products stay pending, separate from signed source reviews.
CREATE TABLE evidence_review_work (
    work_key TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL CHECK(subject IN ('biochemistry','cell_biology')),
    source_unit_ids BIGINT[] NOT NULL,
    evidence JSONB NOT NULL,
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status='pending'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX evidence_work_queue ON evidence_review_work(kind,priority DESC,work_key);
CREATE TRIGGER evidence_work_immutable BEFORE UPDATE OR DELETE ON evidence_review_work
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
