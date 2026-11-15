-- The 60q/300pt v4 specification adds the essay (问答/论述) question type.
-- Widen the one CHECK that lists question types; historical rows are untouched.
ALTER TABLE past_exam_items DROP CONSTRAINT IF EXISTS past_exam_items_question_type_check;
ALTER TABLE past_exam_items
  ADD CONSTRAINT past_exam_items_question_type_check
  CHECK (question_type IN ('single_choice','fill_blank','term','short_answer','essay'));
