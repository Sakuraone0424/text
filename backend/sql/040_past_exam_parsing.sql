-- Past-exam import, wider and better explained:
--   blocked_detail     why an item was skipped, finer than blocked_reason (which keeps its old codes)
--   source_blank_*     an item split out of a multi-blank fill-in question: which blank of how many, the original stem and the
--                      reference answer of the blank that was kept
--   table_layout       the source paragraph sat in a DOCX table only for layout (no table content is needed to answer)
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS blocked_detail TEXT;
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS source_blank_index SMALLINT;
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS source_blank_count SMALLINT;
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS source_stem TEXT;
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS source_answer TEXT;
ALTER TABLE past_exam_items ADD COLUMN IF NOT EXISTS table_layout BOOLEAN NOT NULL DEFAULT false;
