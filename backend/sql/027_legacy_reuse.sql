-- Reuse of old study material (step 3.5). Nothing stored here is evidence,
-- an answer key or an approval; every question still passes the normal two
-- independent AI review rounds.

-- Outcome of re-binding each old topic proposal to the database. Only the id
-- and a reason code are kept; no old review state is carried over.
CREATE TABLE legacy_proposals (
    draft_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    question_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('imported','blocked')),
    blocked_reason TEXT,
    question_id BIGINT REFERENCES reviewed_questions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK((status = 'blocked') = (blocked_reason IS NOT NULL)),
    CHECK((status = 'imported') = (question_id IS NOT NULL))
);

-- Per past-exam item: textbook pages and an UNREVIEWED explanation from the
-- old master index, kept only for items whose stem matched exactly.
CREATE TABLE legacy_hints (
    item_id BIGINT PRIMARY KEY REFERENCES past_exam_items(id),
    record_id TEXT NOT NULL,
    record_year INTEGER,
    pages JSONB NOT NULL DEFAULT '[]',
    answer_summary TEXT,
    explanation TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Whether the index's page numbers line up with the database's OCR pages for
-- a textbook, measured against the database itself. Pages are used only where
-- `verified`.
CREATE TABLE legacy_page_calibration (
    file_key TEXT PRIMARY KEY,
    page_column TEXT,
    page_offset INTEGER,
    samples INTEGER NOT NULL,
    agreeing INTEGER NOT NULL,
    verified BOOLEAN NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE legacy_reuse_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
