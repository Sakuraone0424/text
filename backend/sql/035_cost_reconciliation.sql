-- Balance snapshots from DeepSeek's balance endpoint, per-attempt cost adjustments made after reconciling against the
-- real balance, and a readable log of every correction. All three are insert-only.
CREATE TABLE ai_balance_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    taken_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    source TEXT NOT NULL CHECK (source IN ('hourly', 'midnight', 'manual')),
    currency TEXT NOT NULL DEFAULT 'CNY',
    total_balance NUMERIC NOT NULL CHECK (total_balance >= 0),
    granted_balance NUMERIC,
    topped_up_balance NUMERIC
);
CREATE INDEX ai_balance_snapshots_at ON ai_balance_snapshots(taken_at DESC);
CREATE TRIGGER balance_snapshots_immutable BEFORE UPDATE OR DELETE ON ai_balance_snapshots FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE ai_cny_adjustments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES ai_jobs(id),
    attempt INTEGER NOT NULL,
    amount_cny NUMERIC NOT NULL CHECK (amount_cny >= 0),   -- what this attempt really cost, as reconciled
    reason TEXT NOT NULL,
    reconciliation_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ai_cny_adjustments_attempt ON ai_cny_adjustments(job_id, attempt, id DESC);
CREATE TRIGGER cny_adjustments_immutable BEFORE UPDATE OR DELETE ON ai_cny_adjustments FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

CREATE TABLE ai_cost_corrections (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    corrected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    source TEXT NOT NULL,
    interval_start TIMESTAMPTZ,
    interval_end TIMESTAMPTZ,
    balance_before NUMERIC,
    balance_after NUMERIC,
    actual_spent_cny NUMERIC,           -- the balance's real decrease over the interval
    settled_known_cny NUMERIC,          -- attempts with provider usage (kept as they are)
    counted_before_cny NUMERIC,         -- what the ledger counted for the uncertain attempts
    counted_after_cny NUMERIC,          -- what it counts after the correction
    difference_cny NUMERIC,             -- before - after: the amount given back
    attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
CREATE TRIGGER cost_corrections_immutable BEFORE UPDATE OR DELETE ON ai_cost_corrections FOR EACH ROW EXECUTE FUNCTION immutable_question_audit();

-- Which worker process holds a lease: after a restart the next run can hand back what the old process held.
ALTER TABLE ai_jobs ADD COLUMN lease_run TEXT;
