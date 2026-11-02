"""Where model calls are wasted: why finished work was thrown away, how each reason is handled now, and a
no-model replay of recent failures through the current rules (nothing is written, no model is called).

    python -m app.waste_report stats  [--hours 24]
    python -m app.waste_report replay [--hours 24] [--limit 300]
"""

import json
import sys
from collections import Counter, defaultdict

# How each reason is handled by the current rules (shown next to its count).
HANDLING = {
    "evidence_identity_or_quote_mismatch": "引用先按 6 层规则匹配并替换成教材原文；仍对不上则带段落原文修复一次，再不行作废",
    "invented_or_unmatched_quote": "引用先按 6 层规则匹配；对不上则作废",
    "rubric_points_mismatch": "修复一次（只改评分要点分值），再不行作废",
    "duplicate_choice_options": "修复一次，再不行作废",
    "incomplete_choice_or_key": "选项键、答案写法先自动统一；仍不对则修复一次",
    "blank_marker_count": "单个空的各种写法先自动统一成 ____；空的数量不是一个则修复一次",
    "blank_count_or_points_mismatch": "修复一次，再不行作废",
    "rubric_item_count_out_of_range": "修复一次，再不行作废",
    "insufficient_independent_scoring_criteria": "修复一次，再不行作废",
    "canonical_fill_answer_missing": "标准答案自动补进可接受答案",
    "missing_accepted_answers": "不能自动修正（没有任何可接受答案），直接作废",
    "rubric_tag_mismatch": "评分要点的标签自动并入题目标签",
    "invalid_versioned_points": "满分写错但评分要点合计等于规格分值时自动改正；否则作废",
    "unexpected_options": "非单选题多余的选项自动删除",
    "unexpected_accepted_answers": "非填空题多余的可接受答案自动删除",
    "empty_choice_option": "不能自动修正，直接作废",
    "invalid_blank_representation": "不能自动修正，直接作废",
    "output_schema_violation": "按具体字段区分：能自动修正的先修正，评分/选项/空的问题修复一次",
    "invalid_response_json": "完整 JSON 被代码块或说明文字包住时自动提取；否则按原样重新出题",
    "repair_still_invalid": "已修复过一次仍不合格，作废",
    "repair_requested": "已转入修复（不算作废）",
    "review_reformat_requested": "审核答案格式不对：已重新请求一次审核（不算题目不通过）",
    "review_format_failed": "审核两次格式都不对：判为审核失败（不是题目的问题，不修复也不作废）",
    "invented_quote": "审核里的引用按同样的 6 层规则匹配；仍对不上才算",
    "unresolved_quality_findings": "审核发现问题：修复一次后重新审核",
    "second_correct_option": "审核发现第二个正确选项：修复一次后重新审核",
}


def _since(hours):
    return "clock_timestamp()-%s*interval '1 hour'"


def blocked_stats(conn, hours=24):
    """Counts of the reasons finished work was thrown away (or sent to repair) in the last `hours`, most frequent first."""
    rows = conn.execute(
        "SELECT kind, state, COALESCE(result->'dispatch_failure'->>'code', error_code, 'unspecified') AS code,"
        " result->'dispatch_failure'->'errors' AS errors, count(*) AS n FROM ai_jobs "
        "WHERE COALESCE(finished_at,started_at,created_at)>" + _since(hours) +
        " AND (state='blocked' OR result->'dispatch_failure' IS NOT NULL) AND COALESCE(error_code,'')<>'superseded_scoring' "
        "GROUP BY 1,2,3,4",
        (hours,),
    ).fetchall()
    jobs = Counter()
    fields = defaultdict(Counter)
    for r in rows:
        stage = {"generation": "出题", "repair": "修复", "quality_review": "审核"}.get(r["kind"], r["kind"])
        jobs[(stage, r["code"])] += r["n"]
        for e in r["errors"] or []:
            reason = e.get("reason") or e.get("type")
            if reason:
                fields[(stage, r["code"])][f"{e.get('field')}:{reason}"] += r["n"]
    states = Counter()
    for r in conn.execute(
        "SELECT reason, count(*) AS n FROM auto_question_states WHERE status IN ('blocked','retired') AND reason IS NOT NULL "
        "AND updated_at>" + _since(hours) + " GROUP BY 1",
        (hours,),
    ).fetchall():
        for part in str(r["reason"]).replace("discarded:", "").split(";"):
            if part:
                states[part] += r["n"]
    items = [
        dict(stage=stage, code=code, count=n, handling=HANDLING.get(code, "按原规则处理"),
             fields=[dict(field=f, count=c) for f, c in fields[(stage, code)].most_common(6)])
        for (stage, code), n in jobs.most_common()
    ]
    items += [dict(stage="题目被挡", code=code, count=n, handling=HANDLING.get(code, "按原规则处理"), fields=[]) for code, n in states.most_common()]
    items.sort(key=lambda i: -i["count"])
    fixed = Counter()
    layers = Counter()
    for r in conn.execute(
        "SELECT result->'normalization' AS norm FROM ai_jobs WHERE result->'normalization' IS NOT NULL "
        "AND COALESCE(finished_at,started_at,created_at)>" + _since(hours),
        (hours,),
    ).fetchall():
        for name in (r["norm"] or {}).get("fixes", []):
            fixed[name] += 1
        for rec in (r["norm"] or {}).get("evidence", []):
            if rec.get("matched"):
                layers[rec.get("layer_name") or rec.get("layer")] += 1
    return dict(hours=hours, blocked=items, auto_fixed=dict(fixed.most_common()), evidence_layers=dict(layers))


def replay(conn, hours=24, limit=300, samples_per_layer=3):
    """Re-run the matching and the format rules on recently failed drafts. No model call, nothing written."""
    from app import auto_generation as gen
    from app.evidence_match import LAYER_NAMES, locate

    rows = conn.execute(
        "SELECT id, payload, result, COALESCE(result->'dispatch_failure'->>'code', error_code) AS code FROM ai_jobs "
        "WHERE kind='generation' AND COALESCE(payload->>'origin','')<>'past_exam' AND result ? 'response' "
        "AND COALESCE(finished_at,started_at,created_at)>" + _since(hours) +
        " AND (state='blocked' OR result->'dispatch_failure' IS NOT NULL) ORDER BY id DESC LIMIT %s",
        (hours, limit),
    ).fetchall()
    before = Counter(r["code"] or "unspecified" for r in rows)
    outcome = Counter()
    layer_counts = Counter()
    samples = defaultdict(list)
    detail = []
    for r in rows:
        p, result = r["payload"], r["result"]
        response = result["response"]
        raw = response.get("question", response) if isinstance(response, dict) else response
        entries = raw.get("evidence", []) if isinstance(raw, dict) else []
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            cited = e.get("source_unit_id")
            m = locate(e.get("excerpt"), p["evidence"], cited if type(cited) is int else None)
            if m.ok:
                layer_counts[LAYER_NAMES[m.layer]] += 1
                if m.layer >= 2 and len(samples[m.layer]) < samples_per_layer:
                    samples[m.layer].append(dict(job_id=r["id"], ai_quote=e.get("excerpt"), database_text=[x.text for x in m.pieces]))
        try:
            _, _, notes = gen.prepare(p, response, bool(result.get("json_extracted")))
            state = "ok_now"
        except gen.RepairNeeded as exc:
            state, notes = "repair_now", dict(errors=exc.errors)
        except Exception as exc:  # still not usable: say why, in codes
            from app.dispatch_codes import safe_dispatch_failure
            state, notes = "still_blocked", safe_dispatch_failure(exc)
        outcome[state] += 1
        detail.append(dict(job_id=r["id"], was=r["code"], now=state, notes=notes))
    return dict(
        hours=hours, considered=len(rows), was=dict(before.most_common()), now=dict(outcome),
        quote_layers={k: layer_counts[k] for k in LAYER_NAMES.values() if layer_counts[k]},
        samples={LAYER_NAMES[k]: v for k, v in sorted(samples.items())}, jobs=detail,
    )


def main(argv=None):
    from app.db import get_db

    argv = list(sys.argv[1:] if argv is None else argv)
    what = argv[0] if argv else "stats"
    hours = int(argv[argv.index("--hours") + 1]) if "--hours" in argv else 24
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 300
    with get_db() as conn:
        out = blocked_stats(conn, hours) if what == "stats" else replay(conn, hours, limit)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
