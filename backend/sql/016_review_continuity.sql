-- Unsigned opinions are distinct from immutable human decisions.
CREATE TABLE reviewer_note_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reviewer TEXT NOT NULL,
    candidate_key TEXT NOT NULL REFERENCES candidate_review_packets(candidate_key),
    revision_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>0),
    note TEXT NOT NULL CHECK(length(note)<=5000),
    checks JSONB NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('editing','submitted')),
    base_decision_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(reviewer,candidate_key,version)
);
CREATE TRIGGER reviewer_notes_immutable BEFORE UPDATE OR DELETE ON reviewer_note_versions
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
ALTER TABLE candidate_human_decisions ADD COLUMN note_draft_id BIGINT REFERENCES reviewer_note_versions(id);
CREATE TABLE reviewer_positions (
    reviewer TEXT PRIMARY KEY,
    candidate_key TEXT REFERENCES candidate_review_packets(candidate_key),
    subject_filter TEXT NOT NULL DEFAULT '',
    type_filter TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
-- Runtime tokens are stored only as digests and excluded from all backup data.
CREATE TABLE reviewer_sessions (
    token_sha256 TEXT PRIMARY KEY,
    reviewer TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
