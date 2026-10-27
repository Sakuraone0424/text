"""Small real API admission probes; no private corpus, no inferred capabilities."""

from psycopg.types.json import Jsonb
from urllib.parse import urlsplit
import re
from importlib.metadata import version, PackageNotFoundError
from app import model_router as m
from app.ai_jobs import content_sha


def enqueue_probe(conn, uid, model, effort, price_ceiling):
    m.authorized()
    if effort not in m.EFFORTS:
        raise ValueError("unsupported_effort")
    row = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s AND available=true", (model,)
    ).fetchone()
    if not row:
        raise ValueError("model_not_in_actual_catalog")
    if not price_ceiling.get("source", "").startswith("https://api-docs.deepseek.com/"):
        raise ValueError("official_price_reference_required")
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
    parsed = urlsplit(price_ceiling["source"])
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("price_reference_must_not_contain_credentials")
    s = dict(m.settings(conn, uid))
    s.update(
        model_id=model,
        effort=effort,
        capabilities=dict(prices=price_ceiling),
    )
    for k in ("daily_usd", "monthly_usd"):
        s[k] = str(s[k])
    s.pop("updated_at", None)
    m.cost_bound(s)
    # A price ceiling is user-supplied budget input, not a claim that /models returned prices.
    s["capabilities"]["price_mode"] = "user_declared_upper_bound"
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
        + content_sha(price_ceiling)
        + ":"
        + str(s["token_limit"])
    )
    return conn.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES(%s,%s,'compatibility',%s,%s) ON CONFLICT(job_key) DO UPDATE SET job_key=EXCLUDED.job_key RETURNING *",
        (key, uid, Jsonb(s), Jsonb(payload)),
    ).fetchone()


def accept(conn, job):
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
        prices=s["capabilities"]["prices"],
        price_mode="user_declared_upper_bound",
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
        bound = m.cost_bound({**dict(s), "capabilities": row["compatibility"]})
        if bound > min(s["daily_usd"], s["monthly_usd"]):
            continue
        return m.configure(conn, uid, row["model_id"], s["effort"])
    return None
