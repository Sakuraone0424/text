"""One official SDK process per bounded task; server database credentials excluded."""

import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import shutil
from app.model_router import authorized


def prepare_private_home(root, job_id):
    """Never place response/session evidence in the public source checkout."""
    root = Path(root).resolve()
    code_root = Path(__file__).resolve().parents[1]
    # Source checkout: repo/backend/app. Container: /app/app. Never treat
    # filesystem / as the checkout just because the image is shallow.
    checkout = next((p for p in (code_root, *code_root.parents)
                     if (p / ".git").exists()), code_root)
    if root.is_relative_to(checkout):
        raise ValueError("private_evidence_outside_checkout_required")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    home = root / ("job-" + str(job_id))
    if home.is_symlink():
        raise ValueError("private_evidence_symlink_rejected")
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    return home


def prune_runtime_cache(home):
    """Remove reproducible SDK workspace/cache; preserve raw provider receipts."""
    home = Path(home)
    if home.is_symlink() or not home.is_dir():
        return
    for child in home.iterdir():
        if child.name in ("provider-receipt", "task-audit.json"):
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def execute(job):
    authorized(private=job["kind"] != "compatibility")
    from app.limited_trial import execution_guard

    execution_guard(job)  # Require the approved frozen scope before launching SDK.
    home = prepare_private_home(
        os.environ.get("AI_PRIVATE_HOME", "/tmp/ustc-harness-private"),
        str(job["id"]) + "-attempt-" + str(job["attempts"]),
    )
    allowed = (
        "PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_TRIAL_SCOPE_ID",
        "DEEPSEEK_REVIEW_SCOPE_ID",
        "DEEPSEEK_AUTOMATION_SCOPE_ID",
        "QA_AUTOMATION_SIGNING_KEY",
        "QA_AUTOMATION_ENABLED",
    )
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(
        DSH_HOME=str(home),
        HOME=str(home),
        DSH_PRIMARY_RUNTIME="",
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        OTEL_SDK_DISABLED="true",
    )
    try:
        with subprocess.Popen(
            [sys.executable, "-m", "app.harness_runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=home,
            start_new_session=True,
        ) as process:
            try:
                out, _ = process.communicate(
                    json.dumps(job, default=str),
                    timeout=420 if ("review_campaign" in job["settings_snapshot"] or "qa_automation" in job["settings_snapshot"]) else 150,
                )
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            returncode = process.returncode
    finally:
        try:
            prune_runtime_cache(home)
        except OSError:
            pass  # Never lose a completed provider result to cache housekeeping.
    if returncode:
        raise RuntimeError("harness_task_failed")
    if len(out.encode()) > (1048576 if ("review_campaign" in job["settings_snapshot"] or "qa_automation" in job["settings_snapshot"]) else 131072):
        raise ValueError("oversized_harness_response")
    result = json.loads(out)
    if result.get("provider_error"):
        from app.ai_jobs import ProviderFailure

        raise ProviderFailure(result.get("http_status"), usage=result.get("usage"), diagnostic=result.get("diagnostic"))
    return result
