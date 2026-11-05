-- 薄弱章节优先出题：用户可在设置里关闭（默认开启）。
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS weak_focus BOOLEAN NOT NULL DEFAULT TRUE;
