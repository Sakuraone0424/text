CREATE TABLE candidate_revision_links (
    old_key TEXT PRIMARY KEY REFERENCES candidate_review_packets(candidate_key),
    new_key TEXT NOT NULL UNIQUE REFERENCES candidate_review_packets(candidate_key),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK(old_key<>new_key)
);
CREATE TRIGGER candidate_revision_links_immutable BEFORE UPDATE OR DELETE ON candidate_revision_links
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
