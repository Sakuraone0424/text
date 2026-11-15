-- 错题判定：主观题按得分率分 错题 / 待巩固 / 掌握（分界线可在设置里调），单选填空仍是对错二分。
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS mistake_below DOUBLE PRECISION NOT NULL DEFAULT 0.60;
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS consolidate_below DOUBLE PRECISION NOT NULL DEFAULT 0.85;
ALTER TABLE ai_settings DROP CONSTRAINT IF EXISTS ai_settings_mistake_lines;
ALTER TABLE ai_settings ADD CONSTRAINT ai_settings_mistake_lines CHECK (mistake_below > 0 AND mistake_below <= consolidate_below AND consolidate_below <= 1);
-- a review card says why it exists: a mistake (rated Again), "to consolidate" (rated Hard), or no longer one (history kept)
ALTER TABLE fsrs_cards ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'mistake';
ALTER TABLE fsrs_cards DROP CONSTRAINT IF EXISTS fsrs_cards_status_check;
ALTER TABLE fsrs_cards ADD CONSTRAINT fsrs_cards_status_check CHECK (status IN ('mistake','consolidate','mastered'));
