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


# Reasons that happened once and cannot recur (a rule change retired the work): shown apart, folded on the page.
ONE_TIME = ("superseded_scoring", "lease_expired", "worker_restarted", "released_on_shutdown", "model_delisted_or_catalog_changed")

ISSUE_CATEGORY = {
    "independent_answer_conflict": "答案错误或题目有歧义（独立作答与标准答案不一致）",
    "answer_key_not_confirmed_correct": "答案错误（标准答案没被证实正确）",
    "second_correct_option": "第二个正确选项",
    "option_verdicts_inconsistent": "第二个正确选项（选项判定前后矛盾）",
    "rubric_not_fully_evidenced": "评分要点没有教材依据",
    "knowledge_mapping_not_evidenced": "知识点缺少教材依据映射",
    "invented_quote": "引用不符",
    "answer_leaked_in_stem": "题干泄露答案",
    "duplicate_ready_question": "与已入库的题重复",
    "missing_question_image": "题干引用了看不到的图",
    "unresolved_source_risk": "教材 OCR 风险没说明",
    "calculation_trace_required": "计算题缺计算过程",
    "deterministic_calculation_failed": "计算结果不对",
    "invalid_criterion_identity": "评分要点编号无效",
    "past_exam_stem_changed": "真题题干被改动",
}
AXIS_CATEGORY = {
    "uniqueness": "题目有歧义",
    "scientific_correctness": "科学性问题（事实或答案错误）",
    "exam_style": "不符合考试风格",
    "type_fit": "题型不符",
    "duplication": "与已有题目重复",
}


def _bucket(row_subject, row_type):
    return f"{row_subject or '?'}/{row_type or '?'}"


def review_failures(conn, hours=24, samples=2):
    """Why reviews did not pass, by question type and by problem kind, with two samples each (stem summary + reason)."""
    rows = conn.execute(
        "SELECT ev.question_id, ev.passed, ev.result, q.content->>'subject' AS subject, q.content->>'question_type' AS qtype, "
        "left(q.content->>'stem', 60) AS stem FROM auto_quality_events ev JOIN reviewed_questions q ON q.id=ev.question_id "
        "WHERE ev.phase='review' AND ev.created_at>" + _since(hours),
        (hours,),
    ).fetchall()
    per_bucket = defaultdict(lambda: dict(reviewed=0, passed=0, failed=0, categories=Counter()))
    categories = defaultdict(lambda: dict(count=0, by_bucket=Counter(), samples=[]))
    for r in rows:
        bucket = per_bucket[_bucket(r["subject"], r["qtype"])]
        bucket["reviewed"] += 1
        if r["passed"]:
            bucket["passed"] += 1
            continue
        bucket["failed"] += 1
        result = r["result"] or {}
        finding = result.get("finding") or {}
        cats = []
        for issue in result.get("issues", []):
            if issue == "unresolved_quality_findings":
                failed_axes = [a for a, ok in (finding.get("checks") or {}).items() if not ok]
                cats += [AXIS_CATEGORY.get(a, a) for a in failed_axes] or ["审核发现未解决的问题（未指明维度）"]
            else:
                cats.append(ISSUE_CATEGORY.get(issue, issue))
        reason = (finding.get("unresolved") or [None])[0] or finding.get("rationale") or ""
        for cat in dict.fromkeys(cats):
            bucket["categories"][cat] += 1
            entry = categories[cat]
            entry["count"] += 1
            entry["by_bucket"][_bucket(r["subject"], r["qtype"])] += 1
            if len(entry["samples"]) < samples:
                entry["samples"].append(dict(question_id=r["question_id"], stem=r["stem"], reason=str(reason)[:160]))
    return dict(
        by_type={k: dict(reviewed=v["reviewed"], passed=v["passed"], failed=v["failed"],
                         pass_rate=round(v["passed"] / v["reviewed"], 3) if v["reviewed"] else None,
                         categories=dict(v["categories"].most_common())) for k, v in sorted(per_bucket.items())},
        categories=[dict(category=c, count=e["count"], by_type=dict(e["by_bucket"]), samples=e["samples"])
                    for c, e in sorted(categories.items(), key=lambda kv: -kv[1]["count"])],
    )


def funnel(conn, hours=24):
    """Per subject x type in the window: writing calls, format blocked, sent to repair, reviewed (passed / not), in the bank."""
    out = defaultdict(lambda: Counter())
    for r in conn.execute(
        "SELECT payload->>'subject' AS s, payload->>'question_type' AS t, state, "
        "result->'dispatch_failure'->>'code' AS code, count(*) AS n FROM ai_jobs WHERE kind='generation' "
        "AND COALESCE(finished_at,started_at,created_at)>" + _since(hours) + " AND state IN ('succeeded','blocked') GROUP BY 1,2,3,4",
        (hours,),
    ).fetchall():
        c = out[_bucket(r["s"], r["t"])]
        c["written"] += r["n"]
        if r["code"] == "repair_requested":
            c["sent_to_repair"] += r["n"]
        elif r["state"] == "blocked" or r["code"]:
            c["format_blocked"] += r["n"]
    for r in conn.execute(
        "SELECT payload->>'subject' AS s, payload->>'question_type' AS t, state, count(*) AS n FROM ai_jobs "
        "WHERE kind='repair' AND payload ? 'repair_of_job' AND COALESCE(finished_at,started_at,created_at)>" + _since(hours) + " GROUP BY 1,2,3",
        (hours,),
    ).fetchall():
        c = out[_bucket(r["s"], r["t"])]
        c["repairs"] += r["n"]
        if r["state"] == "blocked":
            c["repair_failed"] += r["n"]
    for r in conn.execute(
        "SELECT q.content->>'subject' AS s, q.content->>'question_type' AS t, "
        "count(*) FILTER (WHERE st.status='ready' AND q.status='published') AS in_bank, "
        "count(*) FILTER (WHERE st.status='ready' AND q.status='published' AND st.revision_count>0) AS repaired_then_in_bank "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id WHERE st.updated_at>" + _since(hours) + " GROUP BY 1,2",
        (hours,),
    ).fetchall():
        c = out[_bucket(r["s"], r["t"])]
        c["in_bank"] += r["in_bank"]
        c["repaired_then_in_bank"] += r["repaired_then_in_bank"]
    reviews = review_failures(conn, hours)
    table = []
    for key in sorted(set(out) | set(reviews["by_type"])):
        c, rv = out.get(key, Counter()), reviews["by_type"].get(key, {})
        table.append(dict(bucket=key, written=c["written"], format_blocked=c["format_blocked"], sent_to_repair=c["sent_to_repair"],
                          repair_failed=c["repair_failed"], reviewed=rv.get("reviewed", 0), review_passed=rv.get("passed", 0),
                          review_failed=rv.get("failed", 0), repaired_then_in_bank=c["repaired_then_in_bank"], in_bank=c["in_bank"],
                          review_failure_kinds=rv.get("categories", {})))
    return dict(table=table, review_failure_kinds=reviews["categories"])


def single_choice_steps(conn, hours=24):
    """Step by step for single choice: where do they stop? (counts and the reasons at each step)"""
    steps = []
    gen = conn.execute(
        "SELECT state, COALESCE(result->'dispatch_failure'->>'code', error_code, '-') AS code, count(*) AS n FROM ai_jobs "
        "WHERE kind='generation' AND payload->>'question_type'='single_choice' AND COALESCE(payload->>'origin','')<>'past_exam' "
        "AND COALESCE(finished_at,started_at,created_at)>" + _since(hours) + " GROUP BY 1,2", (hours,)).fetchall()
    steps.append(dict(step="1 出题调用", total=sum(r["n"] for r in gen), outcomes={f"{r['state']}:{r['code']}": r["n"] for r in gen}))
    ident = conn.execute(
        "SELECT ev.passed, COALESCE(ev.result->'issues'->>0,'-') AS issue, count(*) AS n FROM auto_quality_events ev "
        "JOIN reviewed_questions q ON q.id=ev.question_id WHERE ev.phase='identity' AND q.content->>'question_type'='single_choice' "
        "AND ev.created_at>" + _since(hours) + " GROUP BY 1,2", (hours,)).fetchall()
    steps.append(dict(step="2 格式与证据检查（成为候选题）", total=sum(r["n"] for r in ident),
                      outcomes={("通过" if r["passed"] else "不通过:" + r["issue"]): r["n"] for r in ident}))
    jobs = conn.execute(
        "SELECT j.state, COALESCE(j.error_code,'-') AS code, COALESCE(j.result->'dispatch_failure'->>'code','-') AS fail, count(*) AS n "
        "FROM ai_jobs j JOIN reviewed_questions q ON q.id=(j.payload->>'question_id')::bigint "
        "WHERE j.kind='quality_review' AND q.content->>'question_type'='single_choice' AND j.created_at>" + _since(hours) + " GROUP BY 1,2,3",
        (hours,)).fetchall()
    steps.append(dict(step="3 审核调用（任务状态 / 失败代码）", total=sum(r["n"] for r in jobs),
                      outcomes={f"{r['state']}:{r['code']}:{r['fail']}": r["n"] for r in jobs}))
    rv = review_failures(conn, hours)["by_type"]
    total = {k: v for k, v in rv.items() if k.endswith("single_choice")}
    steps.append(dict(step="4 审核结论", total=sum(v["reviewed"] for v in total.values()),
                      outcomes={k: dict(passed=v["passed"], failed=v["failed"], kinds=v["categories"]) for k, v in total.items()}))
    bank = conn.execute(
        "SELECT count(*) AS n FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        "WHERE q.content->>'question_type'='single_choice' AND st.status='ready' AND q.status='published' AND st.updated_at>" + _since(hours),
        (hours,)).fetchone()["n"]
    steps.append(dict(step="5 入库", total=bank, outcomes={}))
    return steps


def filter_report(conn):
    """Textbook paragraphs before and after the usability filter (generation pool needs >= 120 characters)."""
    from app.evidence_filter import reason_unusable
    from app.source_policy import current_source_sql

    out = {}
    for subject in ("biochemistry", "cell_biology"):
        rows = conn.execute(
            "SELECT u.text,u.quality_flags FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
            + current_source_sql("d", kind="textbook") + " AND d.subject=%s", (subject,)).fetchall()
        reasons = Counter(reason_unusable(r["text"], r["quality_flags"] or (), min_chars=120) or "usable" for r in rows)
        out[subject] = dict(before=len(rows), after=reasons["usable"], filtered=dict((k, v) for k, v in reasons.items() if k != "usable"))
    return out


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
        dict(stage=stage, code=code, count=n, handling=HANDLING.get(code, "按原规则处理"), one_time=code.split(":")[0] in ONE_TIME,
             fields=[dict(field=f, count=c) for f, c in fields[(stage, code)].most_common(6)])
        for (stage, code), n in jobs.most_common()
    ]
    items += [dict(stage="题目被挡", code=code, count=n, handling=HANDLING.get(code, "按原规则处理"), one_time=code.split(":")[0] in ONE_TIME, fields=[])
              for code, n in states.most_common()]
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


def full_stats(conn, hours=24):
    """blocked_stats plus the per type funnel, review failure kinds with samples, the single choice steps and the paragraph filter."""
    base = blocked_stats(conn, hours)
    f = funnel(conn, hours)
    return dict(base, funnel=f["table"], review_failure_kinds=f["review_failure_kinds"], single_choice=single_choice_steps(conn, hours), paragraph_filter=filter_report(conn))


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
        out = full_stats(conn, hours) if what == "stats" else replay(conn, hours, limit)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
