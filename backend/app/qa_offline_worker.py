"""No-model QA maintenance loop for the isolated USTC exam staging service.

Only deterministic bookkeeping: source byte checks, migration consistency and
pending historical-exam review work. It neither calls a model API nor
certifies textbook science, original exam answers, OCR/media fidelity,
question quality or publication.
"""
import json
import os
import time

from app.qa_harness_tick import qa_db, pinned_worker
from app.source_readiness import audit as source_audit
from app.qa_exam_worklist_seed import ensure as seed_exam_pending

FORBIDDEN_ON = (
    "DEEPSEEK_API_AUTHORIZED",
    "AI_AUTOMATION_ENABLED",
    "QA_AUTOMATION_ENABLED",
    "QA_HARNESS_EXECUTION_ENABLED",
)


def offline_authorized(env=None):
    env = os.environ if env is None else env
    if not pinned_worker(env):
        raise PermissionError("offline_worker_not_in_isolated_qa_service")
    if any(env.get(name, "").lower() in {"1", "true", "yes", "on"} for name in FORBIDDEN_ON):
        raise PermissionError("offline_worker_refuses_model_enabled_environment")


def cycle():
    """Make real, idempotent pending-only progress without billable APIs."""
    offline_authorized()
    from migrate import apply_migrations
    with qa_db() as conn:
        apply_migrations(conn)
        state = source_audit(conn)
        if state.get("imported_files") != 6 or not state.get(
            "all_six_byte_integrity_checks_passed"
        ):
            raise PermissionError("offline_worker_six_source_integrity_failed")
        result = seed_exam_pending(conn)
        return {
            "source_files": 6,
            "algorithm": result["algorithm"],
            "pending_exam_work_added": result["new"],
            "existing_exam_review_work": result["already_present"],
            "model_calls": 0,
            "scientific_approvals": 0,
        }


def main():
    # Never auto-resume old DeepSeek jobs even if somebody flips an enable flag.
    offline_authorized()
    while True:
        try:
            print("QA_OFFLINE_WORKER=" + json.dumps(
                cycle(), ensure_ascii=False, sort_keys=True
            ), flush=True)
        except Exception as exc:
            # No book passages, private records, credentials or API receipts in logs.
            print("QA_OFFLINE_ERROR=" + type(exc).__name__, flush=True)
        time.sleep(3600)


if __name__ == "__main__":
    main()
