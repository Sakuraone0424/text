-- Additive only: existing sources, attempts, snapshots and scores are untouched.
CREATE TABLE source_original_parts (
    document_id BIGINT NOT NULL REFERENCES source_documents(id),
    part_number INTEGER NOT NULL CHECK(part_number BETWEEN 1 AND 16),
    file_id TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
    content BYTEA NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 104857600),
    byte_size BIGINT NOT NULL CHECK(byte_size=octet_length(content)),
    pdf_pages INTEGER NOT NULL CHECK(pdf_pages BETWEEN 1 AND 2000),
    page_offset INTEGER NOT NULL CHECK(page_offset>=0),
    PRIMARY KEY(document_id,part_number),
    UNIQUE(document_id,file_id),
    UNIQUE(document_id,page_offset)
);
CREATE TRIGGER original_parts_immutable BEFORE UPDATE OR DELETE ON source_original_parts
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

ALTER TABLE source_units ADD COLUMN ocr_global_page INTEGER CHECK(ocr_global_page>0);
ALTER TABLE source_units ADD COLUMN original_part INTEGER;
ALTER TABLE source_units ADD COLUMN original_page INTEGER CHECK(original_page>0);
ALTER TABLE source_units ADD CONSTRAINT source_unit_original_part_fk
FOREIGN KEY(document_id,original_part) REFERENCES source_original_parts(document_id,part_number);

-- One row per observed OCR page, including empty pages with no source_units.
CREATE TABLE source_pages (
    document_id BIGINT NOT NULL REFERENCES source_documents(id),
    ocr_global_page INTEGER NOT NULL CHECK(ocr_global_page>0),
    volume INTEGER NOT NULL CHECK(volume>0),
    ocr_page INTEGER NOT NULL CHECK(ocr_page>0),
    pdf_page INTEGER NOT NULL CHECK(pdf_page>0),
    printed_page TEXT,
    original_part INTEGER,
    original_page INTEGER CHECK(original_page>0),
    text_characters INTEGER NOT NULL CHECK(text_characters>=0),
    paragraph_count INTEGER NOT NULL CHECK(paragraph_count>=0),
    PRIMARY KEY(document_id,ocr_global_page),
    UNIQUE(document_id,pdf_page),
    FOREIGN KEY(document_id,original_part) REFERENCES source_original_parts(document_id,part_number)
);
