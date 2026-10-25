CREATE TABLE IF NOT EXISTS grading_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    reviewer TEXT NOT NULL,
    record JSONB NOT NULL,
    graded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(attempt_id,question_id) REFERENCES answers_v2(attempt_id,question_id)
);
CREATE TABLE IF NOT EXISTS handwriting_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    image_id BIGINT NOT NULL REFERENCES answer_images(id),
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE TRIGGER grading_event_guard BEFORE UPDATE OR DELETE ON grading_events
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE OR REPLACE TRIGGER handwriting_event_guard BEFORE UPDATE OR DELETE ON handwriting_events
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
