"""Offline validation of an executor's nonsecret Railway receipt; never deploys."""

from app.deployment_target import TARGET


def check(receipt, head, phase="push"):
    issues = []
    for kind, value in TARGET.items():
        if receipt.get(kind.lower() + "_id") != value:
            issues.append("target_" + kind.lower() + "_mismatch")
    for name in ("autodeploy_enabled", "pr_previews_enabled"):
        if receipt.get(name) is not False:
            issues.append(name + "_not_confirmed_off")
    if phase == "deploy":
        if receipt.get("github_sha") != head or receipt.get("staged_sha") != head:
            issues.append("source_sha_not_synchronized")
        if receipt.get("patch_status") != "STAGED":
            issues.append("patch_not_staged")
        if receipt.get("ai_flags") != {
            k: "0"
            for k in (
                "AI_AUTOMATION_ENABLED",
                "DEEPSEEK_API_AUTHORIZED",
                "DEEPSEEK_PRIVATE_DATA_AUTHORIZED",
            )
        }:
            issues.append("ai_not_disabled")
        if (
            not receipt.get("workspace_usage_complete")
            or receipt.get("workspace_projected_usd") is None
        ):
            issues.append("workspace_cost_unknown")
        else:
            from decimal import Decimal

            amount = Decimal(str(receipt["workspace_projected_usd"]))
            if not amount.is_finite() or amount < 0 or amount > 5:
                issues.append("workspace_cost_outside_target")
        for field in (
            "paid_start_authorized",
            "private_transfer_authorized",
            "restore_authorized",
            "target_empty_verified",
            "backup_handoff_confirmed",
        ):
            if receipt.get(field) is not True:
                issues.append(field + "_missing")
    elif phase != "push":
        raise ValueError("unknown_preflight_phase")
    return dict(
        ready=not issues,
        issues=issues,
        phase=phase,
        live_railway_query=False,
        mutation_executed=False,
    )


def main():
    import argparse, json
    from pathlib import Path

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--receipt", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--phase", choices=["push", "deploy"], default="push")
    a = p.parse_args()
    result = check(json.loads(Path(a.receipt).read_text()), a.head, a.phase)
    print(json.dumps(result))
    raise SystemExit(0 if result["ready"] else 2)


if __name__ == "__main__":
    main()
