"""One official SDK process per bounded task; server database credentials excluded."""

import json
import os
from pathlib import Path
import subprocess
import sys
import signal
from app.model_router import authorized


def prepare_private_home(root, job_id):
    """Never place response/session evidence in the public source checkout."""
    root = Path(root).resolve()
    checkout = Path(__file__).resolve().parents[2]
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


def execute(job):
    authorized(private=job["kind"] != "compatibility")
    from app.limited_trial import execution_guard

    execution_guard(job)  # Require the approved frozen scope before launching SDK.
    home = prepare_private_home(
        os.environ.get("AI_PRIVATE_HOME", "/tmp/ustc-harness-private"),
        job["id"],
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
    )
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(
        DSH_HOME=str(home),
        HOME=str(home),
        DSH_PRIMARY_RUNTIME="",
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        OTEL_SDK_DISABLED="true",
    )
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
            out, _ = process.communicate(json.dumps(job, default=str), timeout=150)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        returncode = process.returncode
    if returncode:
        raise RuntimeError("harness_task_failed")
    if len(out.encode()) > 131072:
        raise ValueError("oversized_harness_response")
    result = json.loads(out)
    if result.get("provider_error"):
        from app.ai_jobs import ProviderFailure

        raise ProviderFailure(result.get("http_status"), usage=result.get("usage"), diagnostic=result.get("diagnostic"))
    return result
