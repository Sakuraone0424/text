-- Additive indexes and Codex-assisted assessments; never publication approvals.
CREATE TABLE source_supplements (
    supplement_key TEXT PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_documents(id),
    source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
    parent_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    position TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('omml','image')),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    payload BYTEA NOT NULL,
    search_text TEXT NOT NULL,
    context JSONB NOT NULL,
    scientific_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(scientific_status='unreviewed'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(document_id,source_sha256,position,kind,payload_sha256)
);
CREATE INDEX source_supplement_search ON source_supplements USING GIN(to_tsvector('simple',search_text));
CREATE TRIGGER source_supplements_immutable BEFORE UPDATE OR DELETE ON source_supplements
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE candidate_science_assessments (
    assessment_key TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidate_review_packets(candidate_key),
    revision_sha256 TEXT NOT NULL,
    assessor TEXT NOT NULL CHECK(assessor='Codex AI-assisted'),
    conclusion TEXT NOT NULL CHECK(conclusion IN ('pass','revise','uncertain','reject')),
    analysis JSONB NOT NULL,
    assessment_sha256 TEXT NOT NULL CHECK(length(assessment_sha256)=64),
    formal_gate_passed BOOLEAN NOT NULL DEFAULT false CHECK(NOT formal_gate_passed),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER candidate_science_assessments_immutable BEFORE UPDATE OR DELETE ON candidate_science_assessments
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE source_text_annotations (
    annotation_key TEXT PRIMARY KEY,
    unit_id BIGINT NOT NULL REFERENCES source_units(id),
    unit_sha256 TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('type_analysis','quantity_conflict','duplicate_classification','supplement_question_link')),
    analysis JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER source_text_annotations_immutable BEFORE UPDATE OR DELETE ON source_text_annotations
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
