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
        # Set only by a person after the quality comparison (approve_benchmark); a probe never changes it.
        quality_benchmark_passed=cap.get("quality_benchmark_passed") is True,
        max_tested_output_tokens=s["token_limit"],
    )
    conn.execute(
        "UPDATE ai_model_catalog SET compatibility=%s WHERE model_id=%s",
        (Jsonb(cap), s["model_id"]),
    )
    return cap


def required_efforts(settings):
    """Every thinking level the pipeline will ask a model for: the settings level (grading),
    the writing level (off) and the review level (low)."""
    from app import cost_control

    levels = {settings["effort"]}
    for kind in ("generation", "repair", "quality_review", "grading"):
        levels.add(cost_control.effort_for(kind) or settings["effort"])
    return levels


def approve_benchmark(conn, model_id, approved=True, note=""):
    """A PERSON records that the quality comparison passed for this model (step 8.5).

    Nothing automatic calls this. It does not select the model; it only allows
    auto-upgrade to consider it.
    """
    row = conn.execute("SELECT * FROM ai_model_catalog WHERE model_id=%s FOR UPDATE", (model_id,)).fetchone()
    if not row or not row["available"]:
        raise ValueError("model_not_in_actual_catalog")
    cap = dict(row["compatibility"])
    if approved and cap.get("real_api_verified") is not True:
        raise ValueError("model_has_no_compatibility_probe_yet")
    cap.update(
        quality_benchmark_passed=bool(approved),
        quality_benchmark_recorded_at=datetime.now(timezone.utc).isoformat(),
        quality_benchmark_note=str(note)[:500],
    )
    conn.execute("UPDATE ai_model_catalog SET compatibility=%s WHERE model_id=%s", (Jsonb(cap), model_id))
    return cap


def auto_upgrade(conn, uid):
    """Allow a newer model only after a person approved its quality AND it is proven usable.

    Returns a status dict (never silent): {"status": "off"|"none"|"waiting"|"switched", ...}.
    The switch itself happens only when every level the pipeline uses has been probed on the
    candidate, its CNY price is currently verified and its worst-case cost fits every budget.
    Missing probes are queued here (synthetic arithmetic only, tiny cost).
    """
    s = m.settings(conn, uid)
    if not s["auto_upgrade"]:
        return dict(status="off")
    if s["stopped"]:
        return dict(status="off", reason="automation_stopped")
    current = conn.execute(
        "SELECT first_seen FROM ai_model_catalog WHERE model_id=%s", (s["model_id"],)
    ).fetchone()
    needed = required_efforts(s)
    waiting = []
    for row in conn.execute(
        "SELECT * FROM ai_model_catalog WHERE available=true AND compatibility->>'quality_benchmark_passed'='true' "
        "AND model_id<>%s ORDER BY first_seen DESC",
        (s["model_id"],),
    ).fetchall():
        if current and row["first_seen"] <= current["first_seen"]:
            continue
        from app.cny_billing import current_price

        try:
            price = current_price(conn, row["model_id"])
            bound = m.cost_bound({**dict(s), "model_id": row["model_id"], "price_snapshot": price})
        except ValueError:
            waiting.append(dict(model=row["model_id"], reason="price_not_verified"))
            continue
        limits = [s["daily_cny"], s["monthly_cny"], s["task_cny"]] + ([s["trial_cny"]] if s["trial_active"] else [])
        if bound > min(limits):
            waiting.append(dict(model=row["model_id"], reason="budget_too_small"))
            continue
        cap = row["compatibility"]
        missing = sorted(needed - set(cap.get("efforts", [])))
        if cap.get("real_api_verified") is not True:
            missing = sorted(needed)
        if missing:
            queued = []
            for effort in missing:
                try:
                    enqueue_probe(conn, uid, row["model_id"], effort)
                    queued.append(effort)
                except ValueError as exc:
                    waiting.append(dict(model=row["model_id"], reason="probe_not_queued:" + str(exc)))
            waiting.append(dict(model=row["model_id"], reason="waiting_for_probes", efforts=missing, queued=queued))
            continue
        m.configure(conn, uid, row["model_id"], s["effort"])
        return dict(status="switched", model=row["model_id"])
    return dict(status="waiting", candidates=waiting) if waiting else dict(status="none")
