"""One official SDK process per bounded task; server database credentials excluded."""

import json
import os
from pathlib import Path
import subprocess
import sys
import signal
from app.model_router import authorized


def execute(job):
    authorized(private=job["kind"] != "compatibility")
    root = Path(
        os.environ.get("AI_PRIVATE_HOME", "/tmp/ustc-harness-private")
    ).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = root / ("job-" + str(job["id"]))
    home.mkdir(mode=0o700, exist_ok=True)
    allowed = (
        "PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "DEEPSEEK_API_KEY",
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

        raise ProviderFailure(result.get("http_status"))
    return result
