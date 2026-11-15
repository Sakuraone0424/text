-- Progress page push: any change that the question-writing progress shows sends one "something changed" notification.
-- The payload is only "<schema>:<what>" (no question text, no numbers); the API process listens, rebuilds its cached
-- report once and tells every open page. Identical notifications in one transaction are delivered once by PostgreSQL.
CREATE OR REPLACE FUNCTION notify_progress_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('progress_update', current_schema() || ':' || TG_ARGV[0]);
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS progress_notify_jobs ON ai_jobs;
CREATE TRIGGER progress_notify_jobs AFTER INSERT OR UPDATE OF state ON ai_jobs
FOR EACH STATEMENT EXECUTE FUNCTION notify_progress_changed('job');

DROP TRIGGER IF EXISTS progress_notify_bank ON auto_question_states;
CREATE TRIGGER progress_notify_bank AFTER INSERT OR UPDATE OF status ON auto_question_states
FOR EACH STATEMENT EXECUTE FUNCTION notify_progress_changed('bank');

DROP TRIGGER IF EXISTS progress_notify_worker ON worker_heartbeats;
CREATE TRIGGER progress_notify_worker AFTER INSERT ON worker_heartbeats
FOR EACH STATEMENT EXECUTE FUNCTION notify_progress_changed('worker');
