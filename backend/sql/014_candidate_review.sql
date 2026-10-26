-- Draft previews and human decisions never publish or approve source units.
CREATE TABLE candidate_review_packets (
    candidate_key TEXT PRIMARY KEY,
    batch_key TEXT NOT NULL,
    question_id BIGINT NOT NULL UNIQUE REFERENCES reviewed_questions(id),
    revision_sha256 TEXT NOT NULL CHECK(revision_sha256 ~ '^[0-9a-f]{64}$'),
    question_snapshot JSONB NOT NULL,
    ai_analysis JSONB NOT NULL,
    evidence_snapshot JSONB NOT NULL,
    style_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER candidate_packets_immutable BEFORE UPDATE OR DELETE ON candidate_review_packets
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE candidate_human_decisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidate_review_packets(candidate_key),
    revision_sha256 TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('approve_candidate','return','revise')),
    checks JSONB NOT NULL,
    note TEXT NOT NULL CHECK(length(note)>=20),
    human_attestation BOOLEAN NOT NULL CHECK(human_attestation),
    request_id UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER candidate_decisions_immutable BEFORE UPDATE OR DELETE ON candidate_human_decisions
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
