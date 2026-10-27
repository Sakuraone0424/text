"""Official-only discovery, immutable task settings, fail-closed capability admission."""

from datetime import datetime, timezone
import os
from decimal import Decimal
import httpx
from psycopg.types.json import Jsonb

EFFORTS = ("off", "low", "high", "max")
TASKS = (
    "textbook_analysis",
    "knowledge_extraction",
    "exam_analysis",
    "generation",
    "science_review",
    "answer_review",
    "repair",
    "grading",
    "explanation",
    "study_plan",
    "compatibility",
)


def authorized(private=False):
    if os.environ.get("DEEPSEEK_API_AUTHORIZED") != "1":
        raise ValueError("deepseek_api_authorization_required")
    if private and os.environ.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED") != "1":
        raise ValueError("private_excerpt_authorization_required")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("deepseek_secret_not_configured")


def fetch_official():
    authorized()
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.get(
            "https://api.deepseek.com/models",
            headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]},
        )
        if response.status_code != 200:
            raise ValueError("official_catalog_http_" + str(response.status_code))
        return response.json()


def refresh(conn, fetch=fetch_official):
    try:
        payload = fetch()
        entries = payload["data"]
        if not entries or not isinstance(entries, list) or len(entries) > 500:
            raise ValueError("empty_or_invalid_catalog")
        if any(
            not isinstance(e, dict)
            or not isinstance(e.get("id"), str)
            or not 1 <= len(e["id"]) <= 200
            for e in entries
        ):
            raise ValueError("invalid_model_identity")
        ids = [e["id"] for e in entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_model_identity")
        for e in entries:
            # Capability changes invalidate previously successful compatibility checks.
            conn.execute(
                "INSERT INTO ai_model_catalog(model_id,metadata) VALUES (%s,%s) ON CONFLICT(model_id) DO UPDATE SET metadata=EXCLUDED.metadata,last_seen=clock_timestamp(),available=true,compatibility=CASE WHEN ai_model_catalog.metadata=EXCLUDED.metadata THEN ai_model_catalog.compatibility ELSE '{}'::jsonb END",
                (e["id"], Jsonb(e)),
            )
        conn.execute(
            "UPDATE ai_model_catalog SET available=false WHERE NOT(model_id=ANY(%s))",
            (ids,),
        )
        conn.execute("INSERT INTO ai_catalog_events(success) VALUES(true)")
    except (ValueError, KeyError, TypeError, httpx.HTTPError):
        conn.execute(
            "INSERT INTO ai_catalog_events(success,error_code) VALUES(false,'catalog_unavailable')"
        )
    return catalog(conn)


def catalog(conn):
    latest = conn.execute(
        "SELECT * FROM ai_catalog_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    stale = (
        not latest
        or not latest["success"]
        or (datetime.now(timezone.utc) - latest["fetched_at"]).total_seconds() > 3600
    )
    models = []
    for row in conn.execute(
        "SELECT * FROM ai_model_catalog ORDER BY first_seen,model_id"
    ).fetchall():
        raw = row["metadata"]
        cap = row["compatibility"]
        models.append(
            dict(
                id=row["model_id"],
                name=raw.get("name", raw.get("display_name", row["model_id"])),
                context_window=raw.get("context_window"),
                max_output_tokens=raw.get("max_output_tokens"),
                input_modalities=raw.get("input_modalities"),
                tools=cap.get("tools"),
                vision=cap.get("vision"),
                efforts=cap.get("efforts", []),
                prices=cap.get("prices"),
                price_mode=cap.get("price_mode"),
                reported_prices=raw.get("pricing", raw.get("prices")),
                available=row["available"],
                compatible=cap.get("real_api_verified", False),
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
            )
        )
    return dict(
        models=models,
        stale=stale,
        last_fetch=latest,
        default_model="deepseek-flash",
        default_effort="high",
        auto_switch_performed=False,
    )


def settings(conn, user_id):
    conn.execute(
        "INSERT INTO ai_settings(user_id) VALUES(%s) ON CONFLICT DO NOTHING", (user_id,)
    )
    return conn.execute(
        "SELECT * FROM ai_settings WHERE user_id=%s", (user_id,)
    ).fetchone()


def configure(conn, user_id, model, effort, **options):
    settings(conn, user_id)
    if effort not in EFFORTS:
        raise ValueError("unsupported_effort")
    row = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s", (model,)
    ).fetchone()
    if not row or not row["available"]:
        raise ValueError("model_not_available_in_official_catalog")
    cap = row["compatibility"]
    if not cap.get("real_api_verified") or effort not in cap.get("efforts", []):
        raise ValueError("model_effort_not_compatibility_verified")
    allowed = {
        "auto_discover",
        "auto_upgrade",
        "daily_usd",
        "monthly_usd",
        "token_limit",
        "retry_limit",
        "max_concurrency",
        "stopped",
    }
    if set(options) - allowed:
        raise ValueError("unknown_setting")
    from psycopg import sql

    values = {"model_id": model, "effort": effort, **options}
    conn.execute(
        sql.SQL(
            "UPDATE ai_settings SET {},updated_at=clock_timestamp() WHERE user_id=%s"
        ).format(
            sql.SQL(",").join(
                sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values
            )
        ),
        [*values.values(), user_id],
    )
    return settings(conn, user_id)


def snapshot(conn, user_id):
    s = dict(settings(conn, user_id))
    row = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s", (s["model_id"],)
    ).fetchone()
    if (
        not row
        or not row["available"]
        or not row["compatibility"].get("real_api_verified")
        or s["effort"] not in row["compatibility"].get("efforts", [])
    ):
        raise ValueError("model_not_admitted")
    cap = row["compatibility"]
    verified_limit = cap.get("max_tested_output_tokens")
    if verified_limit is None or s["token_limit"] > verified_limit:
        raise ValueError("output_limit_requires_compatibility_probe")
    declared = row["metadata"].get("max_output_tokens")
    if isinstance(declared, int) and s["token_limit"] > declared:
        raise ValueError("output_limit_exceeds_official_catalog")
    s["capabilities"] = row["compatibility"]
    s["official_metadata"] = row["metadata"]
    for k in ("daily_usd", "monthly_usd"):
        s[k] = str(s[k])
    s.pop("updated_at", None)
    return s


def wire_thinking(effort):
    if effort not in EFFORTS:
        raise ValueError("unsupported_effort")
    return {
        "thinking": {"type": "disabled" if effort == "off" else "enabled"},
        **({} if effort == "off" else {"reasoning_effort": effort}),
    }


def cost_bound(s):
    prices = s["capabilities"].get("prices") or {}
    if not prices.get("source") or any(
        k not in prices for k in ("input_per_million", "output_per_million")
    ):
        raise ValueError("verified_price_required")
    rates = [
        Decimal(str(prices[k])) for k in ("input_per_million", "output_per_million")
    ]
    if any(not x.is_finite() or x <= 0 for x in rates):
        raise ValueError("invalid_price")
    # Fixed maximum input bytes bounds text tokens; no image requests admitted here.
    return (Decimal(32000) * rates[0] + Decimal(s["token_limit"]) * rates[1]) / Decimal(
        1000000
    )
