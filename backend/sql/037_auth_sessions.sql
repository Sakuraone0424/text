-- Sign-in hardening: revoked sessions (log out ends THIS session) and failed-login lockout per source.
CREATE TABLE IF NOT EXISTS revoked_sessions (
    sid TEXT PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS login_failures (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS login_failures_source ON login_failures(source, at DESC);
CREATE TABLE IF NOT EXISTS login_locks (
    source TEXT PRIMARY KEY,
    locked_until TIMESTAMPTZ NOT NULL
);
