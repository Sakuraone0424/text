-- Preserve original OMML and any earlier search rendering; never overwrite either.
CREATE TABLE formula_search_renderings (
    supplement_key TEXT NOT NULL REFERENCES source_supplements(supplement_key),
    renderer_version TEXT NOT NULL,
    search_text TEXT NOT NULL,
    rendering_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(supplement_key,renderer_version)
);
CREATE INDEX formula_rendering_search ON formula_search_renderings USING GIN(to_tsvector('simple',search_text));
CREATE TRIGGER formula_renderings_immutable BEFORE UPDATE OR DELETE ON formula_search_renderings
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
