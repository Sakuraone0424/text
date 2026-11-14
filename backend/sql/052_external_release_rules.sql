-- Why a Routine put a task back (shown on the progress page), and the rules text a lease refers to instead of repeating it in every task.
ALTER TABLE external_leases ADD COLUMN IF NOT EXISTS release_reason TEXT;
CREATE TABLE IF NOT EXISTS external_rules (
    rules_id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    instructions TEXT NOT NULL,
    schema JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
