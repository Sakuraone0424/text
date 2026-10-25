CREATE TABLE IF NOT EXISTS answer_reflections (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('concept_unclear','memory_omission','misread_question','mechanism_error','incomplete_expression','calculation_error','weak_chapter_links')),
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(attempt_id,question_id,event_key),
    FOREIGN KEY(attempt_id,question_id) REFERENCES answers_v2(attempt_id,question_id)
);
CREATE OR REPLACE TRIGGER reflection_guard BEFORE UPDATE OR DELETE ON answer_reflections
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
