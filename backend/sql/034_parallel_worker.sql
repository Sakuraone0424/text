-- Parallel generation: new learners default to 2 concurrent calls (still 1..4, changed on the model page),
-- and each worker run records how many calls ran at once and how long it took.
ALTER TABLE ai_settings ALTER COLUMN max_concurrency SET DEFAULT 2;
ALTER TABLE worker_heartbeats ADD COLUMN concurrency INTEGER NOT NULL DEFAULT 1;
ALTER TABLE worker_heartbeats ADD COLUMN duration_seconds NUMERIC NOT NULL DEFAULT 0;
