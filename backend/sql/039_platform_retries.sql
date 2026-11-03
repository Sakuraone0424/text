-- Platform-side trouble (HTTP 429/500/502/503/504, timeouts, broken connections) is not the task's fault:
--   transient_attempts  how many of a job's attempts ended that way; they do not count against the retry limit
--   platform_streak     consecutive such failures, which sets the exponential wait before the next try
ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS transient_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS platform_streak INTEGER NOT NULL DEFAULT 0;
-- Per worker run: how many calls met a busy platform, and the concurrency the run really used (adaptive).
ALTER TABLE worker_heartbeats ADD COLUMN IF NOT EXISTS busy_503 INTEGER NOT NULL DEFAULT 0;
ALTER TABLE worker_heartbeats ADD COLUMN IF NOT EXISTS busy_429 INTEGER NOT NULL DEFAULT 0;
ALTER TABLE worker_heartbeats ADD COLUMN IF NOT EXISTS timeouts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE worker_heartbeats ADD COLUMN IF NOT EXISTS effective_concurrency INTEGER;
