-- 练习中心：新的专项练习种类（按题型、错题重做、随机快练、真题按年份）和可选限时。
ALTER TABLE practice_sets DROP CONSTRAINT IF EXISTS practice_sets_mode_check;
ALTER TABLE practice_sets ADD CONSTRAINT practice_sets_mode_check CHECK (mode IN ('chapter','weak','type','retry','quick','year'));
ALTER TABLE practice_sets ADD COLUMN IF NOT EXISTS detail TEXT;                 -- the chosen question type or year
ALTER TABLE practice_sets ADD COLUMN IF NOT EXISTS time_limit_seconds INTEGER CHECK (time_limit_seconds IS NULL OR time_limit_seconds BETWEEN 60 AND 7200);
