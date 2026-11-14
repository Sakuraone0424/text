-- "这题有问题": the learner reported a question. It is never selected for this
-- learner again (any row for user+question), and it does not count toward the
-- score of the paper it was reported in (the (paper, question) row).
CREATE TABLE question_reports (
    paper_id BIGINT NOT NULL REFERENCES papers_v2(id),
    question_id BIGINT NOT NULL REFERENCES reviewed_questions(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (paper_id, question_id)
);
CREATE INDEX question_reports_user_question ON question_reports(user_id, question_id);
