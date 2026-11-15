-- Textbook chapter each past-exam question is about ("<file_id>|<chapter>").
-- NULL = not indexed yet, '' = no textbook paragraph found. Chapter exam
-- frequency (distinct stems x years they appeared) drives which paragraphs
-- new AI questions are written from.
ALTER TABLE past_exam_items ADD COLUMN chapter_key TEXT;
