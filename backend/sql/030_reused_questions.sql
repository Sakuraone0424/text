-- A paper may be topped up with questions the learner last saw 14+ days ago
-- when there are not enough new ones; those items are labelled so the paper
-- can say "含复用题".
ALTER TABLE paper_items_v2 ADD COLUMN IF NOT EXISTS is_reused BOOLEAN NOT NULL DEFAULT false;
