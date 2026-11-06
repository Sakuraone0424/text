-- 专项练习的主观题（名词解释、简答、问答）由 AI 判分：提交后先没有分数，判完再写入。
ALTER TABLE practice_answers ALTER COLUMN points DROP NOT NULL;
ALTER TABLE practice_answers ADD COLUMN IF NOT EXISTS feedback JSONB;
ALTER TABLE practice_answers ADD COLUMN IF NOT EXISTS graded_by TEXT;
