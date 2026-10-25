-- Additive metadata only. No historical question, paper, answer or score is rewritten.
CREATE TABLE IF NOT EXISTS exam_scopes (
    scope_key TEXT PRIMARY KEY,
    subject TEXT NOT NULL CHECK(subject IN ('biochemistry','cell_biology')),
    paper_code TEXT NOT NULL,
    syllabus TEXT NOT NULL,
    source_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS scope_key TEXT REFERENCES exam_scopes(scope_key);
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'unclassified' CHECK(source_kind IN ('real_exam','simulation','transcription','unclassified'));
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS mapping_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS tag_contributions JSONB NOT NULL DEFAULT '{}';
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS primary_tags JSONB NOT NULL DEFAULT '[]';
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS cognitive_level TEXT CHECK(cognitive_level IN ('core','mechanism','application'));
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS canonical_item_id TEXT;
ALTER TABLE exam_records ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
CREATE TABLE IF NOT EXISTS exam_samples (
    scope_key TEXT NOT NULL REFERENCES exam_scopes(scope_key),
    year INTEGER NOT NULL CHECK(year BETWEEN 1900 AND 2100),
    source_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    expected_questions INTEGER NOT NULL CHECK(expected_questions>0),
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(scope_key,year)
);
CREATE TABLE IF NOT EXISTS knowledge_dependencies (
    subject TEXT NOT NULL,
    knowledge_tag TEXT NOT NULL,
    prerequisite_tag TEXT NOT NULL,
    source_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK(knowledge_tag<>prerequisite_tag),
    PRIMARY KEY(subject,knowledge_tag,prerequisite_tag)
);
CREATE TABLE IF NOT EXISTS selection_policies (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    config JSONB NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS selection_policy_assignments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    policy_id BIGINT NOT NULL REFERENCES selection_policies(id),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS selection_runs (
    paper_id BIGINT PRIMARY KEY REFERENCES papers_v2(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    seed TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    policy_id BIGINT REFERENCES selection_policies(id),
    as_of TIMESTAMPTZ NOT NULL,
    config JSONB NOT NULL,
    context JSONB NOT NULL,
    candidates JSONB NOT NULL,
    decisions JSONB NOT NULL
);
CREATE OR REPLACE TRIGGER selection_run_guard BEFORE UPDATE OR DELETE ON selection_runs
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE OR REPLACE TRIGGER selection_policy_guard BEFORE UPDATE OR DELETE ON selection_policies
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE OR REPLACE TRIGGER exam_scope_guard BEFORE UPDATE OR DELETE ON exam_scopes
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE OR REPLACE TRIGGER exam_sample_guard BEFORE UPDATE OR DELETE ON exam_samples
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE OR REPLACE TRIGGER selection_assignment_guard BEFORE UPDATE OR DELETE ON selection_policy_assignments
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE IF NOT EXISTS exam_annotations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id BIGINT NOT NULL REFERENCES exam_records(id),
    metadata JSONB NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE TRIGGER exam_annotation_guard BEFORE UPDATE OR DELETE ON exam_annotations
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE INDEX IF NOT EXISTS idx_exam_annotation_asof ON exam_annotations(record_id,registered_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_exam_scope_year ON exam_records(scope_key,year,indexed_at);
CREATE INDEX IF NOT EXISTS idx_policy_assignment_asof ON selection_policy_assignments(user_id,assigned_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_grading_available ON grading_events(attempt_id,question_id,graded_at DESC);
CREATE INDEX IF NOT EXISTS idx_fsrs_replay ON fsrs_events(user_id,question_id,reviewed_at DESC,id DESC);
