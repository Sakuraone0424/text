-- No rewrite of historical source reviews, papers, answers or scores.
ALTER TABLE source_pages ALTER COLUMN pdf_page DROP NOT NULL;
ALTER TABLE source_pages ADD COLUMN marker_text TEXT;
ALTER TABLE source_pages ADD COLUMN marker_label TEXT;

CREATE TABLE source_versions (
    document_id BIGINT PRIMARY KEY REFERENCES source_documents(id),
    previous_document_id BIGINT REFERENCES source_documents(id),
    collection_version TEXT NOT NULL,
    material_key TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
    source_format TEXT NOT NULL CHECK(source_format IN ('txt','docx')),
    source_content BYTEA NOT NULL CHECK(octet_length(source_content) BETWEEN 1 AND 52428800),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER source_versions_immutable BEFORE UPDATE OR DELETE ON source_versions
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE TABLE source_review_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_id BIGINT NOT NULL REFERENCES source_units(id),
    review_kind TEXT NOT NULL CHECK(review_kind IN ('text','science','uncertainty','exam_style')),
    status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
    unit_sha256 TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX source_review_latest ON source_review_events(unit_id,review_kind,id DESC);
CREATE TRIGGER source_review_append_only BEFORE UPDATE OR DELETE ON source_review_events
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE FUNCTION protect_ocr_evidence() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME='source_documents' THEN
        IF EXISTS(SELECT 1 FROM source_versions WHERE document_id=OLD.id) THEN
            RAISE EXCEPTION 'OCR source versions are immutable; import a new revision';
        END IF;
    ELSE
        IF EXISTS(SELECT 1 FROM source_versions WHERE document_id=OLD.document_id) THEN
            IF TG_OP='DELETE' OR TG_TABLE_NAME='source_pages' THEN
                RAISE EXCEPTION 'OCR evidence identity is immutable';
            END IF;
            IF (to_jsonb(OLD)-ARRAY['verified','reviewer','verification_note']) IS DISTINCT FROM
               (to_jsonb(NEW)-ARRAY['verified','reviewer','verification_note']) THEN
                RAISE EXCEPTION 'OCR evidence text and coordinates are immutable';
            END IF;
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER protect_ocr_document BEFORE UPDATE OR DELETE ON source_documents
FOR EACH ROW EXECUTE FUNCTION protect_ocr_evidence();
CREATE TRIGGER protect_ocr_unit BEFORE UPDATE OR DELETE ON source_units
FOR EACH ROW EXECUTE FUNCTION protect_ocr_evidence();
CREATE TRIGGER protect_ocr_page BEFORE UPDATE OR DELETE ON source_pages
FOR EACH ROW EXECUTE FUNCTION protect_ocr_evidence();

CREATE TABLE exam_transcript_candidates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_versions(document_id),
    unit_id BIGINT NOT NULL REFERENCES source_units(id),
    year INTEGER CHECK(year BETWEEN 1900 AND 2100),
    question_number TEXT,
    question_type TEXT,
    transcription TEXT NOT NULL,
    concerns JSONB NOT NULL DEFAULT '[]',
    source_level TEXT NOT NULL DEFAULT 'ocr_transcription' CHECK(source_level='ocr_transcription'),
    UNIQUE(document_id,unit_id)
);
CREATE TRIGGER exam_transcripts_immutable BEFORE UPDATE OR DELETE ON exam_transcript_candidates
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE source_bibliography_reviews (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_versions(document_id),
    source_sha256 TEXT NOT NULL,
    bibliography JSONB NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER bibliography_review_append_only BEFORE UPDATE OR DELETE ON source_bibliography_reviews
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
