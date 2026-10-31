-- Expand the existing reviewed-paper position range without changing historical rows.
-- Every other training invariant (deadline, ownership, uniqueness, immutable
-- snapshots and answers) remains in force. This does NOT enable the 60q UI.
ALTER TABLE paper_items_v2
  DROP CONSTRAINT IF EXISTS paper_items_v2_position_check;

ALTER TABLE paper_items_v2
  ADD CONSTRAINT paper_items_v2_position_check
  CHECK (position BETWEEN 1 AND 60);
