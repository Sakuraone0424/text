-- Additive only: never alter legacy questions, papers, attempts or answers.
CREATE TABLE IF NOT EXISTS reviewed_questions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS question_audits (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    reviewer TEXT NOT NULL CHECK (length(trim(reviewer)) > 0),
    content_snapshot JSONB NOT NULL,
    checks JSONB NOT NULL,
    issues JSONB NOT NULL,
    duplicate_metrics JSONB NOT NULL,
    passed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS papers_v2 (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    paper_date DATE NOT NULL,
    blueprint JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, paper_date)
);
CREATE TABLE IF NOT EXISTS paper_items_v2 (
    paper_id BIGINT NOT NULL REFERENCES papers_v2(id),
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    position SMALLINT NOT NULL CHECK (position BETWEEN 1 AND 28),
    snapshot JSONB NOT NULL,
    PRIMARY KEY(paper_id, question_id),
    UNIQUE(paper_id, position)
);
CREATE TABLE IF NOT EXISTS attempts_v2 (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paper_id BIGINT NOT NULL UNIQUE REFERENCES papers_v2(id),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalized_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS answers_v2 (
    attempt_id BIGINT NOT NULL REFERENCES attempts_v2(id),
    question_id BIGINT NOT NULL,
    answer TEXT NOT NULL,
    points INTEGER CHECK (points >= 0),
    feedback JSONB,
    graded_by TEXT,
    PRIMARY KEY(attempt_id, question_id)
);
CREATE TABLE IF NOT EXISTS reviews_v2 (
    attempt_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    review_stage SMALLINT NOT NULL CHECK (review_stage IN (1,3,7)),
    due_date DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY(attempt_id, question_id, review_stage),
    FOREIGN KEY(attempt_id, question_id) REFERENCES answers_v2(attempt_id, question_id)
);
CREATE OR REPLACE FUNCTION protect_reviewed_question() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'New questions must be drafts';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.content IS DISTINCT FROM OLD.content THEN
        RAISE EXCEPTION 'Question content is immutable; create a new revision';
    END IF;
    IF NEW.status = 'published' AND NOT EXISTS (
        SELECT 1 FROM question_audits a WHERE a.question_id = NEW.id
        AND a.content_snapshot = NEW.content AND a.passed
        AND a.issues = '[]'::jsonb
        AND a.id = (SELECT MAX(id) FROM question_audits WHERE question_id = NEW.id)
    ) THEN
        RAISE EXCEPTION 'Publication requires a passing current audit';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER reviewed_question_guard
BEFORE INSERT OR UPDATE ON reviewed_questions
FOR EACH ROW EXECUTE FUNCTION protect_reviewed_question();
CREATE OR REPLACE FUNCTION protect_paper_item_v2() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Paper snapshots are immutable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM reviewed_questions q WHERE q.id = NEW.question_id
                   AND q.status = 'published' AND q.content = NEW.snapshot) THEN
        RAISE EXCEPTION 'Paper item requires a published question snapshot';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER paper_item_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON paper_items_v2
FOR EACH ROW EXECUTE FUNCTION protect_paper_item_v2();
CREATE INDEX IF NOT EXISTS idx_papers_v2_user ON papers_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_audits_question ON question_audits(question_id, id);
-- Approved single-choice exports keep the 60-question API, without certifying old seeds.
CREATE TABLE IF NOT EXISTS legacy_question_publications (
    question_id BIGINT PRIMARY KEY REFERENCES questions(id),
    reviewed_question_id BIGINT NOT NULL UNIQUE REFERENCES reviewed_questions(id)
);
CREATE OR REPLACE FUNCTION immutable_question_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Audits are append-only; submit a new audit';
END $$;
CREATE OR REPLACE TRIGGER question_audit_guard BEFORE UPDATE OR DELETE ON question_audits
FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();
CREATE OR REPLACE FUNCTION protect_answer_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    maximum INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Submitted answers cannot be deleted';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.answer IS DISTINCT FROM OLD.answer OR NEW.attempt_id <> OLD.attempt_id
        OR NEW.question_id <> OLD.question_id OR OLD.points IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Submitted answers and completed grades are immutable';
    END IF;
    SELECT (i.snapshot->>'max_points')::integer INTO maximum
    FROM attempts_v2 a JOIN paper_items_v2 i ON i.paper_id=a.paper_id
    WHERE a.id=NEW.attempt_id AND i.question_id=NEW.question_id;
    IF maximum IS NULL OR NEW.points > maximum THEN
        RAISE EXCEPTION 'Answer must belong to paper and score must fit snapshot rubric';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER answer_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON answers_v2
FOR EACH ROW EXECUTE FUNCTION protect_answer_v2();

CREATE OR REPLACE FUNCTION protect_paper_v2() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Paper ownership, date and blueprint are immutable';
END $$;
CREATE OR REPLACE TRIGGER paper_v2_guard BEFORE UPDATE OR DELETE ON papers_v2
FOR EACH ROW EXECUTE FUNCTION protect_paper_v2();
