"""Small real API admission probes; no private corpus, no inferred capabilities."""

from datetime import datetime, timezone
from psycopg.types.json import Jsonb
import re
from importlib.metadata import version, PackageNotFoundError
from app import model_router as m
from app.ai_jobs import content_sha


def enqueue_probe(conn, uid, model, effort):
    m.authorized()
    if effort not in m.EFFORTS:
        raise ValueError("unsupported_effort")
    row = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s AND available=true", (model,)
    ).fetchone()
    if not row:
        raise ValueError("model_not_in_actual_catalog")
    requirement = (
        row["metadata"]
        .get("client_requirements", {})
        .get("deepseek-harness", {})
        .get("min_version")
    )
    if requirement:

        def parts(v):
            found = re.fullmatch(
                r"(\d+)\.(\d+)\.(\d+)(?:-?(alpha|a|beta|b|rc)\.?(\d+))?", v
            )
            if not found:
                raise ValueError("unsupported_client_version_declaration")
            return (
                *map(int, found.group(1, 2, 3)),
                {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2, None: 3}[found[4]],
                int(found[5] or 0),
            )

        try:
            installed = version("deepseek-harness-sdk")
        except PackageNotFoundError:
            raise ValueError("harness_not_installed")
        if parts(installed) < parts(requirement):
            raise ValueError("harness_upgrade_required")
    from app.cny_billing import current_price

    s = dict(m.settings(conn, uid))
    s.update(
        model_id=model,
        effort=effort,
        capabilities={},
        price_snapshot=current_price(conn, model),
    )
    for k in ("daily_cny", "monthly_cny", "task_cny", "trial_cny"):
        s[k] = str(s[k])
    s.pop("updated_at", None)
    m.cost_bound(s)
    payload = dict(
        instructions='Return exactly JSON {"check":2} for 1+1. No tool calls, explanation or other text.',
        catalog_metadata=row["metadata"],
    )
    key = (
        "compatibility:"
        + str(uid)
        + ":"
        + model
        + ":"
        + effort
        + ":"
        + content_sha(row["metadata"])
        + ":"
        + s["price_snapshot"]["version"]
        + ":"
        + str(s["token_limit"])
    )
    return conn.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES(%s,%s,'compatibility',%s,%s) ON CONFLICT(job_key) DO UPDATE SET job_key=EXCLUDED.job_key RETURNING *",
        (key, uid, Jsonb(s), Jsonb(payload)),
    ).fetchone()


def accept(conn, job):
    if job["settings_snapshot"].get("review_campaign", {}).get("policy", {}).get("transport") == "responses_json_schema":
        from app.responses_review import accept_probe
        return accept_probe(conn, job)
    if (
        job["state"] != "succeeded"
        or job["result"].get("real_api") is not True
        or job["result"].get("response") != {"check": 2}
    ):
        raise ValueError("real_compatibility_probe_failed")
    s = job["settings_snapshot"]
    row = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s FOR UPDATE", (s["model_id"],)
    ).fetchone()
    if (
        not row
        or not row["available"]
        or row["metadata"] != job["payload"]["catalog_metadata"]
    ):
        raise ValueError("catalog_changed_since_probe")
    cap = row["compatibility"]
    efforts = set(cap.get("efforts", []))
    efforts.add(s["effort"])
    cap.update(
        real_api_verified=True,
        efforts=sorted(efforts),
        compatibility_checked_at=datetime.now(timezone.utc).isoformat(),
        probe_job_id=job["id"],
        model_version=job["result"].get("model_version"),
        quality_benchmark_passed=False,
        max_tested_output_tokens=s["token_limit"],
    )
    conn.execute(
        "UPDATE ai_model_catalog SET compatibility=%s WHERE model_id=%s",
        (Jsonb(cap), s["model_id"]),
    )
    return cap


def auto_upgrade(conn, uid):
    s = m.settings(conn, uid)
    if not s["auto_upgrade"] or s["stopped"]:
        return None
    for row in conn.execute(
        "SELECT * FROM ai_model_catalog WHERE available=true AND compatibility->>'real_api_verified'='true' AND compatibility->>'quality_benchmark_passed'='true' ORDER BY first_seen DESC"
    ).fetchall():
        if s["effort"] not in row["compatibility"].get("efforts", []):
            continue
        current = conn.execute(
            "SELECT first_seen FROM ai_model_catalog WHERE model_id=%s",
            (s["model_id"],),
        ).fetchone()
        if current and row["first_seen"] <= current["first_seen"]:
            continue
        from app.cny_billing import current_price

        try:
            bound = m.cost_bound(
                {
                    **dict(s),
                    "model_id": row["model_id"],
                    "price_snapshot": current_price(conn, row["model_id"]),
                }
            )
        except ValueError:
            continue
        limits = [s["daily_cny"], s["monthly_cny"], s["task_cny"]] + (
            [s["trial_cny"]] if s["trial_active"] else []
        )
        if bound > min(limits):
            continue
        return m.configure(conn, uid, row["model_id"], s["effort"])
    return None
