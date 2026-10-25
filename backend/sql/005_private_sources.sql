CREATE TABLE IF NOT EXISTS source_documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id TEXT NOT NULL,
    title TEXT NOT NULL,
    edition TEXT NOT NULL,
    subject TEXT NOT NULL CHECK(subject IN ('biochemistry','cell_biology')),
    sha256 TEXT NOT NULL,
    original_sha256 TEXT,
    original_content BYTEA,
    metadata JSONB NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(file_id,sha256)
);
CREATE TABLE IF NOT EXISTS source_units (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_documents(id),
    ordinal INTEGER NOT NULL,
    chapter TEXT NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL,
    pdf_page INTEGER,
    printed_page TEXT,
    ocr_page INTEGER,
    volume INTEGER,
    tags JSONB NOT NULL DEFAULT '[]',
    quality_flags JSONB NOT NULL DEFAULT '[]',
    verified BOOLEAN NOT NULL DEFAULT false,
    reviewer TEXT,
    verification_note TEXT,
    UNIQUE(document_id,ordinal)
);
CREATE INDEX IF NOT EXISTS source_text_search ON source_units USING gin(to_tsvector('simple',text));
CREATE TABLE IF NOT EXISTS exam_records (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_unit_id BIGINT NOT NULL REFERENCES source_units(id),
    original_question_id TEXT NOT NULL,
    subject TEXT NOT NULL CHECK(subject IN ('biochemistry','cell_biology')),
    year INTEGER NOT NULL CHECK(year BETWEEN 1900 AND 2100),
    question_type TEXT NOT NULL,
    stem TEXT NOT NULL,
    options JSONB NOT NULL DEFAULT '{}',
    knowledge_tags JSONB NOT NULL DEFAULT '[]',
    category TEXT NOT NULL,
    answer TEXT,
    official_answer_evidence_unit BIGINT REFERENCES source_units(id),
    answer_kind TEXT NOT NULL CHECK(answer_kind IN ('official','source_reference','personal_reference','missing')),
    rubric JSONB NOT NULL DEFAULT '[]',
    textbook_units JSONB NOT NULL DEFAULT '[]',
    original_verified BOOLEAN NOT NULL DEFAULT false,
    complete_paper BOOLEAN NOT NULL DEFAULT false,
    canonical_key TEXT NOT NULL,
    UNIQUE(source_unit_id,original_question_id)
);
CREATE TABLE IF NOT EXISTS knowledge_links (
    unit_id BIGINT NOT NULL REFERENCES source_units(id),
    tag TEXT NOT NULL,
    PRIMARY KEY(unit_id,tag)
);
