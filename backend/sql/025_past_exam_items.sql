-- Past-exam questions imported verbatim from the locked exam transcriptions.
-- One row per distinct stem; the same stem repeated across years is collapsed
-- into the latest year and every year it appeared in is kept in `years`.
CREATE TABLE past_exam_items (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject TEXT NOT NULL CHECK(subject IN ('biochemistry','cell_biology')),
    question_type TEXT NOT NULL CHECK(question_type IN ('single_choice','fill_blank','term','short_answer')),
    stem TEXT NOT NULL,
    options JSONB NOT NULL DEFAULT '{}',
    stem_hash TEXT NOT NULL,
    document_id BIGINT NOT NULL REFERENCES source_documents(id),
    unit_id BIGINT NOT NULL REFERENCES source_units(id),
    question_number TEXT,
    year INTEGER,
    years JSONB NOT NULL DEFAULT '[]',
    occurrences JSONB NOT NULL DEFAULT '[]',
    stem_normalized BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'imported' CHECK(status IN ('imported','queued','packet_created','blocked')),
    blocked_reason TEXT,
    job_id BIGINT REFERENCES ai_jobs(id),
    question_id BIGINT REFERENCES reviewed_questions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(subject, question_type, stem_hash),
    CHECK((status = 'blocked') = (blocked_reason IS NOT NULL))
);
CREATE INDEX past_exam_items_status ON past_exam_items(status, question_type);

-- Where every bank question came from. `concept_keys` are deterministic
-- identifiers of the knowledge point (cited textbook paragraph, concept name):
-- paper assembly never puts two questions sharing a key on the same paper.
CREATE TABLE question_origins (
    question_id BIGINT PRIMARY KEY REFERENCES reviewed_questions(id),
    origin TEXT NOT NULL CHECK(origin IN ('past_exam','generated')),
    past_exam_item_id BIGINT REFERENCES past_exam_items(id),
    concept_keys JSONB NOT NULL DEFAULT '[]',
    source_ref JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK((origin = 'past_exam') = (past_exam_item_id IS NOT NULL))
);
