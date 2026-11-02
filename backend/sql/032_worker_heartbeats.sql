-- One row per AI worker run, so the progress page can say when the worker last ran and how it ended.
-- Numbers and codes only: no question text, no textbook text, no secrets.
CREATE TABLE worker_heartbeats (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    outcome TEXT NOT NULL,            -- ok | disabled | error:<ExceptionType>
    calls INTEGER NOT NULL DEFAULT 0,
    cost_cny NUMERIC NOT NULL DEFAULT 0,
    stopped_because TEXT,             -- why the run made fewer calls than allowed (code)
    queue JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX worker_heartbeats_finished ON worker_heartbeats(finished_at DESC);
