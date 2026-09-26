BEGIN;

-- 用户
CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 知识点
CREATE TABLE IF NOT EXISTS topics (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject VARCHAR(30) NOT NULL
        CHECK (subject IN ('biochemistry', 'cell_biology')),
    name VARCHAR(200) NOT NULL,
    UNIQUE (subject, name)
);

-- 题库
CREATE TABLE IF NOT EXISTS questions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic_id BIGINT NOT NULL REFERENCES topics(id),
    stem TEXT NOT NULL,
    options JSONB NOT NULL,
    correct_answer CHAR(1) NOT NULL
        CHECK (correct_answer IN ('A','B','C','D')),
    explanation TEXT NOT NULL,
    difficulty SMALLINT NOT NULL DEFAULT 3
        CHECK (difficulty BETWEEN 1 AND 5),
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 每日试卷
CREATE TABLE IF NOT EXISTS papers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    paper_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, paper_date),
    UNIQUE (id, user_id)
);

-- 试卷题目及排列顺序
CREATE TABLE IF NOT EXISTS paper_questions (
    paper_id BIGINT NOT NULL REFERENCES papers(id),
    question_id BIGINT NOT NULL REFERENCES questions(id),
    position SMALLINT NOT NULL CHECK (position BETWEEN 1 AND 60),
    PRIMARY KEY (paper_id, question_id),
    UNIQUE (paper_id, position)
);

-- 交卷记录
CREATE TABLE IF NOT EXISTS attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paper_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    score SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 60),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (paper_id),
    FOREIGN KEY (paper_id, user_id)
        REFERENCES papers(id, user_id)
);

-- 每题作答详情
CREATE TABLE IF NOT EXISTS answers (
    attempt_id BIGINT NOT NULL REFERENCES attempts(id),
    question_id BIGINT NOT NULL REFERENCES questions(id),
    selected_answer CHAR(1)
        CHECK (selected_answer IN ('A','B','C','D')),
    is_correct BOOLEAN NOT NULL,
    PRIMARY KEY (attempt_id, question_id)
);

-- 错题复习计划
CREATE TABLE IF NOT EXISTS review_schedule (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    question_id BIGINT NOT NULL REFERENCES questions(id),
    review_stage SMALLINT NOT NULL
        CHECK (review_stage IN (1,3,7)),
    due_date DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (user_id, question_id, review_stage, due_date)
);

CREATE INDEX IF NOT EXISTS idx_questions_topic
    ON questions(topic_id);

CREATE INDEX IF NOT EXISTS idx_papers_user_date
    ON papers(user_id, paper_date);

CREATE INDEX IF NOT EXISTS idx_reviews_due
    ON review_schedule(user_id, due_date)
    WHERE completed_at IS NULL;

COMMIT;
