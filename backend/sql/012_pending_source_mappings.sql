-- Draft associations are not authoritative knowledge_links and cannot publish questions.
CREATE TABLE source_mapping_proposals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    target_unit_id BIGINT REFERENCES source_units(id),
    knowledge_label TEXT NOT NULL CHECK(length(knowledge_label) BETWEEN 1 AND 300),
    method TEXT NOT NULL,
    evidence JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status='pending'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE NULLS NOT DISTINCT(source_unit_id,target_unit_id,knowledge_label,method)
);
CREATE TRIGGER mapping_proposals_immutable BEFORE UPDATE OR DELETE ON source_mapping_proposals
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
