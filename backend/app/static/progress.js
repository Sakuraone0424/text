"use strict";
(() => {
const $ = (id) => document.getElementById(id);
const SUBJECT = { biochemistry: "生物化学", cell_biology: "细胞生物学" };
const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
const KIND = {
  generation: "出题", quality_review: "审核", repair: "修复", grading: "判分", compatibility: "兼容性探测",
  science_review: "科学审核", answer_review: "答案审核", knowledge_extraction: "知识提取",
  textbook_analysis: "教材分析", exam_analysis: "真题分析", explanation: "讲解", study_plan: "学习计划",
};
const STATE = {
  pending: "排队", retrying: "重试", running: "进行中", succeeded: "完成", failed: "失败", blocked: "被挡住", cancelled: "已取消",
};
const REASON = {
  waiting_for_off_peak: "等待低峰时段",
  budget_exceeded: "额度不足",
  automation_stopped: "已停止AI任务",
  verified_cny_price_required: "价格未核实",
  superseded_scoring: "旧分值已停用（规则变更，一次性）",
  worker_restarted: "后台服务重启时被中断，已重新排队",
  released_on_shutdown: "服务重启前还没发出请求，已退回队列",
  output_truncated: "模型输出被长度上限截断（不是格式问题）",
  provider_output_truncated: "模型输出被长度上限截断（不是格式问题）",
  provider_incomplete_model_response: "模型回应不完整",
  provider_invalid_response_json: "模型回应不是有效的 JSON",
  provider_sdk_exception: "调用模型时出现异常",
  WorkerInterrupted: "运行结束时调用还没返回，已重新排队",
  provider_http_429: "DeepSeek 限流，自动等待重试",
  provider_http_500: "DeepSeek 繁忙，自动等待重试",
  provider_http_502: "DeepSeek 繁忙，自动等待重试",
  provider_http_503: "DeepSeek 繁忙，自动等待重试",
  provider_http_504: "DeepSeek 繁忙，自动等待重试",
  TimeoutExpired: "调用超时，自动等待重试",
  TimeoutError: "调用超时，自动等待重试",
  ReadTimeout: "调用超时，自动等待重试",
  ConnectTimeout: "连接超时，自动等待重试",
  ConnectError: "连接 DeepSeek 失败，自动等待重试",
  ReadError: "连接中断，自动等待重试",
  RemoteProtocolError: "连接中断，自动等待重试",
  ConnectionError: "连接中断，自动等待重试",
  ConnectionResetError: "连接中断，自动等待重试",
  unspecified: "未注明",
  model_delisted_or_catalog_changed: "原模型已下架或目录变化",
  model_delisted_no_replacement: "原模型已下架，当前没有可用的替代模型（有了会自动重排）",
  replacement_effort_not_probed: "替代模型这一档思考强度还没探测通过（通过后自动重排）",
  replacement_unusable: "替代模型暂不可用（通过探测和价格核实后自动重排）",
  cancelled_by_owner: "已手动取消",
  invalid_or_uncertain_result: "结果无效或不确定（旧记录，没有更具体的原因）",
  output_schema_violation: "模型输出不符合题目格式",
  generated_question_quality_gate_failed: "出的题没通过自动质量检查",
  evidence_identity_or_quote_mismatch: "引用的教材原文与数据库里的原文不一致",
  rubric_source_analysis_missing: "评分要点缺少教材依据说明",
  style_transcription_missing: "缺少真题风格依据",
  rubric_tag_mismatch: "评分要点的知识标签不属于本题",
  rubric_points_mismatch: "评分要点分值合计不等于本题分值",
  invalid_versioned_points: "本题分值不是当前规格的分值",
  incomplete_choice_or_key: "选择题选项或答案不完整",
  empty_choice_option: "有空的选项",
  duplicate_choice_options: "选项有重复",
  unexpected_options: "非选择题带了选项",
  invalid_blank_representation: "填空的空位写法不对",
  blank_count_or_points_mismatch: "空的个数或分值对不上",
  missing_accepted_answers: "填空缺少可接受答案",
  canonical_fill_answer_missing: "标准答案不在可接受答案里",
  blank_marker_count: "填空题必须恰好一个空",
  unexpected_accepted_answers: "非填空题带了可接受答案",
  invalid_isbn_checksum: "教材 ISBN 校验不通过",
  invalid_result: "无法识别的结果",
  unknown_question_type: "题型未知",
  unresolved_placeholder: "题干或答案里有占位符",
  unavailable_figure_reference: "题干引用了看不到的图",
  answer_disclosed_in_stem: "答案已经写在题干里",
  textbook_wording_in_stem: "题干是阅读理解式的（根据教材/本章/文献/图表……）",
  unhelpful_all_or_none_distractor: "选项里有「以上都对/都不对」",
  missing_unique_answer_rationale: "缺少唯一答案的理由",
  missing_accepted_answer_set: "缺少可接受答案集合",
  model_answer_too_terse_for_subjective_marking: "参考答案太简短，不够判分",
  insufficient_independent_scoring_criteria: "评分要点不足两条",
  unresolved_quality_findings: "审核发现未解决的问题",
  rubric_not_fully_evidenced: "有评分要点没有教材依据",
  question_version_changed: "审核期间题目被改动",
  evidence_version_changed: "审核期间教材依据被改动",
  completed_actual_model_call_required: "没有真实完成的模型调用",
  reliable_provider_usage_required: "缺少可靠的用量记录",
  repair_requested: "草稿有可修复的问题，已转入修复一次（不算作废）",
  repair_still_invalid: "修复一次后仍不合格，已作废",
  review_reformat_requested: "审核答案的格式不对，已重新请求一次审核（不算题目不通过）",
  review_format_failed: "审核两次格式都不对，判为审核失败（不是题目的问题）",
  evidence_quote_unmatched: "引用在提供的教材段落里找不到",
  rubric_item_count_out_of_range: "评分要点数量不在规定范围",
  rubric_missing: "缺少评分要点",
  repair_unfixed: "修复的版本仍有同样的问题",
  // review and pipeline reasons (kept in sync by tests/test_progress_view.py: every code the backend can raise has a line here)
  term_not_in_textbook: "教材中未找到该名词",
  insufficient_textbook_evidence: "教材里没有足够依据，模型判定无法作答",
  invented_quote: "审核引用的原文在教材段落里找不到",
  invented_or_unmatched_quote: "引用的原文与教材段落对不上",
  unrequested_source: "引用了没有提供的教材段落",
  past_exam_item_changed: "真题条目在出题期间被改动",
  past_exam_stem_changed: "真题题干被改动",
  past_exam_source_not_locked: "真题来源文件没有锁定",
  evidence_exceeds_hard_cap: "引用的教材超过 4 段的上限，审核无法发出",
  evidence_payload_too_large: "请求内容太大，装不下教材段落",
  bounded_input_exceeded: "请求内容超过大小上限",
  payload_exceeds_hard_cap: "请求内容超过 24000 字节上限",
  no_textbook_evidence: "教材中找不到相关段落",
  textbook_or_exam_source_unavailable: "教材或真题来源文件暂不可用",
  model_call_failed: "模型调用失败，没有拿到结果",
  invalid_repair_result: "修复的结果无效",
  invalid_response_json: "模型回应不是有效的 JSON",
  lease_expired: "任务超时被收回，已重新排队",
  stale_lease: "任务已被收回",
  oversized_result: "模型回答超过大小上限",
  unsupported_dispatch_kind: "不支持的任务类型",
  unsupported_automatic_job_kind: "不支持的自动任务类型",
  unknown_ai_task: "未知的 AI 任务",
  unknown_execution_scope: "未知的执行范围",
  unknown_review_output_contract: "未知的审核格式版本",
  review_answer_format: "审核答案的格式不对",
  option_checks_incomplete: "审核没有逐项检查 A–D 四个选项",
  review_failed: "审核失败",
  review_job_phase_mismatch: "审核任务阶段对不上",
  stored_review_job_required: "找不到已保存的审核任务",
  not_in_complete_checking_state: "题目不在可审核的状态",
  not_a_past_exam_job: "不是真题答案任务",
  answer_key_not_confirmed_correct: "审核没能确认标准答案正确",
  independent_answer_conflict: "独立作答与标准答案不一致",
  second_correct_option: "发现第二个正确选项",
  option_verdicts_inconsistent: "选项判定前后矛盾",
  answer_leaked_in_stem: "题干泄露了答案",
  duplicate_ready_question: "与已入库的题重复",
  missing_question_image: "题干引用了看不到的图",
  unresolved_source_risk: "教材 OCR 风险没有说明",
  calculation_trace_required: "计算题缺计算过程",
  deterministic_calculation_failed: "计算结果不对",
  invalid_criterion_identity: "评分要点编号无效",
  knowledge_mapping_not_evidenced: "知识点缺少教材依据",
  uniqueness: "题目有歧义",
  scientific_correctness: "科学性问题（事实或答案错误）",
  exam_style: "不符合考试风格",
  type_fit: "题型不符",
  duplication: "与已有题目重复",
  question_not_found: "找不到这道题",
  candidate_packet_required: "缺少候选题资料",
  superseded_candidate: "候选题已被新版本取代",
  source_or_candidate_superseded: "教材或候选题已被新版本取代",
  unlocated_or_empty_evidence: "引用没有定位到教材原文",
  ocr_locator_mismatch_no_pdf_claims_allowed: "OCR 页码对不上，不能引用 PDF 页",
  synthetic_source_cannot_be_ready: "合成来源的题不能入库",
  source_file_integrity_failed: "来源文件校验不通过",
  unresolvable_ocr_or_image_dependency: "依赖无法识别的 OCR 或图片",
  transcription_style_mismatch: "真题风格依据与题目不符",
  style_evidence_required: "缺少真题风格依据",
  repair_limit: "修复次数已用完",
  repair_did_not_change_content: "修复没有改动内容",
  repair_must_preserve_spec_and_explain_changes: "修复必须保持规格并说明改动",
  repair_must_preserve_past_exam_stem: "修复不能改动真题题干",
  repair_source_job_missing: "找不到被修复的出题任务",
  automatic_new_revision: "自动生成了新版本",
  answer_changed: "答案被改动",
  actual_generation_call_required: "没有真实完成的出题调用",
  actual_grading_call_required: "没有真实完成的判分调用",
  actual_advice_call_required: "没有真实完成的建议调用",
  actual_knowledge_call_required: "没有真实完成的知识提取调用",
  actual_transcript_call_required: "没有真实完成的转写调用",
  generated_quota_mismatch: "出的题数与配额不符",
  generation_source_not_in_locked_six_file_collection: "出题来源不在锁定的六份文件里",
  historical_score_must_not_be_overwritten: "不能覆盖历史成绩",
  historical_single_choice_options_incomplete_or_spliced: "历史单选题的选项不完整",
  incomplete_concept_review: "概念审核不完整",
  incomplete_or_stale_rounds: "审核轮次不完整或已过期",
  distractor_checks_must_cover_every_wrong_option: "干扰项检查没有覆盖每个错误选项",
  distractor_checks_required: "缺少干扰项检查",
  invalid_generation_points: "出题分值无效",
  invalid_rubric_scores: "评分要点分值无效",
  unsupported_generation_type: "不支持的出题题型",
  invented_bibliography: "教材出处是编造的",
  invented_confirmation_quote: "确认引用是编造的",
  invented_learning_history_or_mapping: "学习记录或映射是编造的",
  invented_or_stale_advice_evidence: "建议的依据是编造的或已过期",
  invented_transcription_text: "转写文字是编造的",
  stale_knowledge_source: "知识来源已过期",
  uncertain_knowledge: "知识点不确定",
  unfounded_grading_knowledge_mapping: "判分的知识点映射没有依据",
  unfounded_knowledge_relation: "知识点关系没有依据",
  uncertain_score_requires_independent_reassessment: "分数不确定，需要独立复评",
  cny_model_price_required: "该模型没有核实过的人民币价格",
  cny_price_expired_or_not_effective: "价格已过期或尚未生效",
  cny_reservation_required: "缺少费用预留",
  conflicting_cny_price_versions: "人民币价格版本冲突",
  invalid_cny_amount: "金额无效",
  invalid_paid_tool_usage: "付费工具用量无效",
  invoice_evidence_required: "缺少账单凭证",
  invoice_reservation_not_found: "找不到账单对应的预留",
  official_cny_price_required: "需要官方人民币价格",
  price_not_found: "找不到价格",
  price_observation_time_invalid: "价格观察时间无效",
  price_resolution_evidence_required: "缺少价格核实凭证",
  price_version_identity_conflict: "价格版本标识冲突",
  unknown_tool_billing_mode: "未知的工具计费方式",
  verified_tool_price_required: "工具价格未核实",
  leased_automatic_attempt_required: "缺少任务租约",
  model_effort_not_compatibility_verified: "该思考强度还没有通过兼容性探测",
  idempotency_conflict: "任务重复提交且内容不同",
  unapproved_automatic_parameters: "模型参数不在批准范围",
  unapproved_compatibility_input: "探测输入不在批准范围",
  payload_required: "缺少任务内容",
  timezone_required: "缺少时区",
  transcription_provenance_not_official: "转写来源不是正式来源",
};

function el(tag, text, parent, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  if (parent) parent.append(node);
  return node;
}
const fmtTime = (iso) =>
  iso
    ? new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
    : "—";
const yuan = (value) => (value === null || value === undefined ? "—" : "¥" + Number(value).toFixed(2));
const BUDGET_STOP = { daily: "今日额度已用完", monthly: "本月额度已用完", task: "单任务额度已用完", trial: "累计试验额度已用完", cap: "本月 AI 额度已用完" };
function stopText(code) {
  const m = /^blocked:budget_exceeded(?::(\w+))?$/.exec(String(code));
  if (m) return BUDGET_STOP[m[1]] || "额度已用完";
  const known = { "blocked:automation_stopped": "AI 任务已停止", no_claimable_job: "队列里没有可领取的任务", max_concurrency_reached: "并发名额已用满", run_time_budget: "已到本次运行的时间上限" };
  return known[code] || reasonText(code);
}
const VALUE_ERROR = {
  payload_exceeds_hard_cap: "请求内容超过 24000 字节上限", evidence_exceeds_hard_cap: "引用的教材超过 4 段或 5000 字的上限",
  oversized_harness_response: "模型回答超过大小上限", oversized_result: "模型回答超过大小上限", stale_lease: "任务已被收回",
  unapproved_wire_text: "发出的内容与批准的提示不一致", unapproved_automatic_parameters: "模型参数不在批准范围",
};
const reasonText = (code) =>
  String(code).startsWith("ValueError:")
    ? "内部检查没通过：" + (VALUE_ERROR[String(code).slice(11)] || String(code).slice(11))
    : REASON[code] || REASON[String(code).split(":")[0]] || String(code);
const knownReason = (code) => !!(REASON[code] || REASON[String(code).split(":")[0]] || String(code).startsWith("ValueError:"));

function duration(hours) {
  if (hours < 1) return "不到 1 小时";
  if (hours < 48) return "约 " + Math.round(hours) + " 小时";
  return "约 " + (hours / 24).toFixed(1) + " 天";
}

function paper(report) {
  const p = report.next_paper;
  const main = $("paper-main"), note = $("paper-note");
  main.className = "";
  if (p.budget && p.budget.daily_exhausted && !p.complete) {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "今日 AI 额度已用完，明天 0 点（北京时间）自动继续" + (p.eta_hours !== null ? "；预计" + duration(p.eta_hours) + "。" : "。");
    return;
  }
  if (p.complete) {
    main.textContent = "已经够了";
    note.textContent = "每个科目 × 题型的已入库题数都达到整卷所需。";
  } else if (p.eta_hours !== null) {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "预计" + duration(p.eta_hours) + "。按最近 " + p.approved_last_hours + " 小时入库 " + p.approved_in_window + " 题（约每个低峰小时 " + p.per_off_peak_hour + " 题）和接下来的低峰时长估算。";
  } else if (p.eta_state === "slow") {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "按最近的低峰入库速度，预计需要 2 天以上。";
  } else {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "正在估算：最近 " + p.approved_last_hours + " 小时的低峰入库记录还不够。";
  }
}

// "DeepSeek 繁忙，自动等待重试": its own line, apart from real failures, and it goes away by itself when the platform recovers
function platformLine(report) {
  const p = report.platform;
  const line = $("platform-note");
  line.hidden = !(p && p.busy);
  if (line.hidden) return;
  const next = p.next_retry_at ? "，最早 " + fmtTime(p.next_retry_at) + " 再试" : "";
  const last = p.last_run ? `。上一轮 503 × ${p.last_run.busy_503} · 429 × ${p.last_run.busy_429} · 超时 × ${p.last_run.timeouts}` : "";
  line.textContent = `DeepSeek 繁忙，自动等待重试：${p.waiting_jobs} 个任务在等待（不算失败，不占重试次数）${next}${last}。`;
}

// The price page of the provider could not be read: new writing stops when the prices in force run out, so say so early.
const PRICE_WHY = {
  official_price_layout_changed: "官方价格页的版面变了", official_prices_missing: "价格页里找不到价格表", price_model_identity_invalid: "价格页里的模型名不认识",
  official_cny_price_units_unknown: "价格单位不是「元」", tool_policy_needs_reverification: "价格页里联网工具的计费说明变了",
  official_period_policy_needs_reverification: "价格页里的高峰/低峰时段说明变了", http_403: "价格页拒绝了访问（403）", http_404: "价格页地址不存在（404）",
};
function priceLine(report) {
  const p = report.prices;
  const line = $("price-note");
  line.hidden = !(p && (!p.ok || p.blocking));
  if (line.hidden) return;
  const why = PRICE_WHY[p.code] || (p.code ? "原因代码 " + p.code : "原因未记录");
  line.textContent = p.blocking
    ? `价格已过期，出题已被暂停：最近 ${p.failures} 次刷新失败（${why}）。`
    : `价格刷新失败（${why}），已连续 ${p.failures} 次；现有价格还能用约 ${Math.max(0, p.hours_left)} 小时，之后出题会被暂停。`;
}

function paceLine(report) {
  const p = report.pace;
  const line = $("pace-note");
  if (!line) return;
  line.hidden = !p;
  if (!p) return;
  const yuan = (v) => "¥" + Number(v).toFixed(2);
  const day = (iso) => { const d = new Date(iso + "T00:00:00"); return (d.getMonth() + 1) + " 月 " + d.getDate() + " 日"; };
  if (p.enabled === false) {
    // the day-by-day split is off: writing and review are bound by the month's limit and by the gap, grading by the total cap
    line.textContent = `本月出题已用 ${yuan(p.month_writing_used_cny)} / 上限 ${yuan(p.month_writing_limit_cny)}；判分预留还剩 ${yuan(p.grading_reserve_left_cny)}，判分只受本月总上限约束。`;
    line.classList.toggle("warn", Number(p.month_writing_used_cny) >= Number(p.month_writing_limit_cny));
    return;
  }
  const used = `今日出题额度 已用 ${yuan(p.used_cny)} / 可用 ${yuan(p.allowed_cny)}` + (p.exception ? "（明天的卷子还凑不齐，今天可多用 50%）" : "");
  const month = p.lasts_to_month_end ? "按当前速度，本月额度够用到月底。" : `按当前速度，本月额度可用到 ${day(p.runs_out_on)}。`;
  line.textContent = used + (p.exceeded ? "，今天的额度已用完，出题与审核停到次日 0 点。" : "。") + month;
  line.classList.toggle("warn", Boolean(p.exceeded || !p.lasts_to_month_end));
}

function status(report) {
  priceLine(report);
  paceLine(report);
  platformLine(report);
  const period = report.period;
  $("period").textContent = period.is_peak ? "高峰·暂缓" : "低峰·出题中";
  $("period").title = period.is_peak ? fmtTime(period.next_off_peak_at) + " 起低峰" : "下个高峰 " + fmtTime(period.next_peak_at);
  const w = report.worker;
  const note = $("worker-note");
  note.className = "note";
  const cell = $("worker");
  cell.className = "";
  if (!w.last_run_at) {
    cell.textContent = "没有记录";
    cell.classList.add("warn");
    note.textContent = "出题程序（定时任务）还没有留下运行记录：请检查它是否已部署并启用。";
    note.classList.add("warn");
    return;
  }
  cell.textContent = w.minutes_ago < 1 ? "刚刚运行" : w.minutes_ago + " 分钟前";
  const outcome = w.outcome === "ok" ? "正常" : w.outcome === "disabled" ? "未启用" : "出错（" + w.outcome.replace("error:", "") + "）";
  const parts = ["上次 " + fmtTime(w.last_run_at) + " · " + outcome, "调用 " + w.calls + " 次", "同时最多 " + (w.concurrency || 1) + " 个", "用时 " + Math.round(w.duration_seconds || 0) + " 秒", "费用 " + yuan(w.cost_cny)];
  if (w.stopped_because) parts.push("停止原因：" + stopText(w.stopped_because));
  if (w.busy_503 || w.busy_429 || w.timeouts) parts.push(`上一轮 503 × ${w.busy_503} · 429 × ${w.busy_429} · 超时 × ${w.timeouts}`);
  if (w.effective_concurrency && w.effective_concurrency < (w.concurrency || 1)) parts.push(`并发已降到 ${w.effective_concurrency}`);
  note.textContent = parts.join(" · ") + (w.stale ? "。已超过 15 分钟没有运行（正常每 5 分钟一次）。" : "。");
  if (w.stale || w.outcome !== "ok") {
    note.classList.add("warn");
    cell.classList.add("warn");
  }
}

function batch(report) {
  const t = report.totals;
  const first = report.target_kind === "first_batch";
  $("h-batch").textContent = (first ? "首批目标" : "两天储备") + "已入库";
  $("batch-number").textContent = t.stocked;
  $("batch-target").textContent = " / " + t.target;
  $("batch-number").parentElement.classList.toggle("done", t.stocked >= t.target);
  C().progress($("batch-progress"), { label: "已入库", text: `${t.stocked} / ${t.target}`, value: t.stocked, max: t.target, color: t.stocked >= t.target ? "var(--up)" : "var(--primary)" });
  const today = $("admitted-today");
  today.hidden = false;
  today.textContent = "今日入库 " + t.admitted_today + " 道";
  // today's review pass rate as a ring: orange below 50%, grey while nothing has been reviewed yet
  const r = report.review_today;
  const ring = $("rate-ring");
  const share = r.rate === null ? 0 : r.rate;
  $("review-rate").textContent = r.rate === null ? "—" : `${Math.round(r.rate * 100)}%`;
  $("review-rate").title = r.rate === null ? "今天还没有审核" : `${r.passed}/${r.reviewed}`;
  ring.className = "ring" + (r.rate === null ? " none" : r.rate < 0.5 ? " low" : "") + (share === 0 ? " empty" : "");
  ring.setAttribute("aria-label", r.rate === null ? "今日审核通过率：今天还没有审核" : `今日审核通过率 ${Math.round(r.rate * 100)}%（${r.passed}/${r.reviewed}）${r.rate < 0.5 ? "，低于 50%" : ""}`);
  $("ring-arc").style.strokeDasharray = `${Math.round(share * 100)} 100`;
}
const C = () => window.Charts;

function reasonList(root, reasons) {
  const box = el("details", undefined, root, "reasons-detail");
  el("summary", "查看原因", box);
  Object.entries(reasons).forEach(([code, n]) => {
    const line = el("p", undefined, box, "reason-line");
    line.append(`${reasonText(code)} ×${n} `);
    el("code", code, line);
  });
}

let subject = "biochemistry";
let lastReport = null;
document.querySelectorAll("[data-subject]").forEach((button) => {
  button.onclick = () => {
    subject = button.dataset.subject;
    if (lastReport) bank(lastReport);
  };
});

function chip(list, text, cls) {
  const li = el("li", text, list, "tag" + (cls ? " " + cls : ""));
  return li;
}
// blocked work is "a lot" when it is at least 5 items, or as many as everything stocked: those tags turn orange
const manyBlocked = (b) => b.blocked >= 5 || (b.blocked > 0 && b.blocked >= b.stocked);

function bank(report) {
  lastReport = report;
  document.querySelectorAll("[data-subject]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.subject === subject)));
  const root = $("bank");
  root.replaceChildren();
  const label = report.target_kind === "first_batch" ? "首批目标" : "两天储备";
  $("bank-label").textContent = `已入库 / 目标（${label}）`;
  for (const key of Object.keys(SUBJECT)) {
    const group = el("div", undefined, root, "group");
    group.hidden = key !== subject;
    for (const b of report.buckets.filter((x) => x.subject === key)) {
      const reached = b.stocked >= b.target;
      const row = el("div", undefined, group, "row");
      const head = el("div", undefined, row, "head");
      el("span", TYPE[b.question_type], head, "name");
      const count = el("span", undefined, head, "count");
      count.append(el("b", String(b.stocked)), ` / ${b.target}`);
      if (reached) el("span", "已达标", count, "reached");
      count.title = label;
      const bar = el("div", undefined, row, "bar" + (reached ? " full" : ""));
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", SUBJECT[key] + TYPE[b.question_type] + "已入库 " + b.stocked + "，" + label + " " + b.target);
      el("i", undefined, bar).style.width = Math.min(100, b.target ? (b.stocked / b.target) * 100 : 0) + "%";
      const tags = el("ul", undefined, row, "tags notes");
      chip(tags, `今日 +${b.admitted_today}`, b.admitted_today ? "good" : "");
      if (b.generating) chip(tags, `出题中 ${b.generating}`);
      if (b.reviewing) chip(tags, `审核中 ${b.reviewing}`);
      if (b.queued) chip(tags, `排队 ${b.queued}`);
      if (b.deferred) chip(tags, `已超额、暂缓 ${b.deferred}`);
      if (b.blocked) chip(tags, `被挡住 ${b.blocked}`, manyBlocked(b) ? "attn" : "");
      if (Object.keys(b.blocked_reasons).length) reasonList(row, b.blocked_reasons);
    }
  }
  const legacy = $("legacy");
  legacy.hidden = !report.legacy.count;
  if (report.legacy.count) {
    $("legacy-summary").textContent = `历史记录（旧规格）${report.legacy.count} 条，点开查看明细`;
    const list = $("legacy-list");
    list.replaceChildren();
    report.legacy.reasons.forEach((r) => {
      const li = el("li", undefined, list);
      li.append(`${reasonText(r.code)} ×${r.count} `);
      el("code", r.code, li);
    });
  }
}

let funnelType = "all";
/* Who wrote today's questions: per executor, tasks done, the share that passed the program checks, and the average time. */
const EXECUTOR = { deepseek: "DeepSeek", claude: "Claude（订阅）", "claude-subscription": "Claude（订阅）" };
const ROUTE = { deepseek: "DeepSeek", claude: "Claude", claude_first: "Claude 优先" };
function executors(report) {
  const data = report.executors || { today: {}, routing: {} };
  const root = $("executors");
  root.replaceChildren();
  const route = data.routing || {};
  const rebuild = data.rebuild, rebuildNote = $("rebuild-note");
  if (rebuildNote) {
    // the bank is rebuilt by the Claude Routine only: said at the top, with what it really delivers per night
    rebuildNote.hidden = !(rebuild && rebuild.active);
    if (rebuild && rebuild.active) {
      rebuildNote.textContent = "题库由 Claude 定时任务补充，" + (rebuild.per_night ? `预计每晚可补 ${rebuild.per_night} 道` : "还没有 Claude 出题记录，第一晚跑通后显示每晚可补多少道");
    }
  }
  const waiting = data.waiting, notice = $("claude-waiting");
  if (notice) {
    // tasks that only a Routine may take: said plainly at the top of the page, with the time DeepSeek takes them over
    notice.hidden = !waiting;
    if (waiting) {
      notice.textContent = `${waiting.count} 个任务在等 Claude 定时任务领取` +
        (waiting.hand_over_hours === null || waiting.hand_over_hours === undefined ? "（不会自动转给 DeepSeek）" : `，${waiting.hand_over_hours} 小时后转给 DeepSeek`) +
        (waiting.oldest_hours >= 1 ? `。最早的已等 ${waiting.oldest_hours} 小时` : "");
    }
  }
  $("executors-note").textContent = route.generation ? `出题：${ROUTE[route.generation] || route.generation}` + (route.generation === "claude_first" ? `（${route.fallback_hours} 小时没人领就交给 DeepSeek）` : "") + `；审核：${ROUTE[route.review] || route.review}；判分：${ROUTE[route.grading] || route.grading}` : "";
  for (const key of ["claude-subscription", "deepseek"]) {
    const d = (data.today || {})[key] || { done: 0, passed: 0, pass_rate: null, avg_seconds: null };
    const li = el("li", undefined, root);
    el("b", EXECUTOR[key], li);
    li.append(` 今日完成 ${d.done}` + (d.pass_rate === null ? "" : ` · 程序检查通过 ${Math.round(d.pass_rate * 100)}%`) + (d.avg_seconds === null ? "" : ` · 平均 ${Math.round(d.avg_seconds)} 秒`));
  }
}

function funnel(report) {
  const data = report.funnel || {};
  $("funnel-types").replaceChildren();
  const names = [["all", "全部"], ...Object.keys(TYPE).map((k) => [k, TYPE[k]])];
  for (const [key, name] of names) {
    const button = el("button", name, $("funnel-types"));
    button.type = "button";
    button.dataset.type = key;
    button.setAttribute("aria-pressed", String(key === funnelType));
    button.onclick = () => {
      funnelType = key;
      funnel(report);
    };
  }
  const root = $("funnel");
  root.replaceChildren();
  const d = data[funnelType] || { written: 0, format_passed: 0, review_passed: 0, in_bank: 0 };
  const steps = [["出题", d.written], ["格式通过", d.format_passed], ["审核通过", d.review_passed], ["入库", d.in_bank]];
  const top = Math.max(1, ...steps.map((x) => x[1]));
  steps.forEach(([name, value], i) => {
    const row = el("div", undefined, root, "funnel-row");
    el("span", name, row);
    const track = el("div", undefined, row, "track");
    el("i", undefined, track).style.width = (value / top) * 100 + "%";
    const num = el("b", String(value), row);
    if (i > 0 && steps[i - 1][1]) el("small", Math.round((value / steps[i - 1][1]) * 100) + "%", num);
  });
  if (!d.written) el("p", "今天还没有出题", root, "note");
  else el("p", "百分比是相对上一步。一道题被修复后重新审核，会在审核通过一栏里只算一次。", root, "note");
}

function waste(report) {
  const root = $("waste");
  root.replaceChildren();
  const w = report.waste;
  if (!w || (!w.blocked.length && !(w.one_time || []).length)) {
    el("p", "近 24 小时没有被挡的任务", root, "note");
    return;
  }
  // known reasons keep their own bar; reasons we cannot translate are merged into "其他" (raw codes inside)
  const known = w.blocked.filter((i) => knownReason(i.code));
  const unknown = w.blocked.filter((i) => !knownReason(i.code));
  const total = w.total || w.blocked.reduce((n, i) => n + i.count, 0) || 1;
  const top = Math.max(1, ...w.blocked.map((i) => i.count));
  const bar = (item, name, other) => {
    const row = el("details", undefined, root, "waste-row" + (other ? " other" : ""));
    const head = el("summary", undefined, row);
    el("span", name, head, "waste-name");
    const count = el("span", undefined, head, "waste-count");
    count.append(el("b", item.count + " 次"), ` · ${Math.round((item.count / total) * 100)}%`);
    el("div", undefined, head, "wbar").append(Object.assign(el("i"), { style: `width:${(item.count / top) * 100}%` }));
    return row;
  };
  for (const item of known) {
    const row = bar(item, reasonText(item.code));
    el("p", item.stage + " · " + item.handling, row, "reason-line");
    item.fields.forEach((f) => el("p", reasonText(f.field.split(":").pop()) + " ×" + f.count, row, "reason-line"));
    el("code", item.code, el("p", undefined, row, "reason-line"));
  }
  if (unknown.length) {
    const merged = { count: unknown.reduce((n, i) => n + i.count, 0) };
    const row = bar(merged, "其他", true);
    unknown.forEach((item) => {
      const line = el("p", undefined, row, "reason-line");
      line.append(`${item.stage} · ×${item.count} `);
      el("code", item.code, line);
    });
  }
  if ((w.one_time || []).length) {
    // one-off reasons (a rule change retired the work, a restart): kept apart and folded
    const once = el("details", undefined, root, "waste-row once");
    el("summary", `一次性原因（不会再出现）· ${w.one_time.reduce((n, i) => n + i.count, 0)} 次`, once);
    w.one_time.forEach((item) => el("p", `${reasonText(item.code)} ×${item.count}`, once, "reason-line"));
  }
  const fixed = Object.entries(w.auto_fixed || {});
  if (fixed.length) el("p", "近 24 小时自动修正：" + fixed.map(([k, n]) => `${FIXES[k] || k} ${n}`).join("、"), root, "note");
  const layers = Object.entries(w.evidence_layers || {});
  if (layers.length) el("p", "引用匹配：" + layers.map(([k, n]) => `${k} ${n}`).join("、"), root, "note");
}
const FIXES = {
  json_extracted: "提取 JSON", numbers_to_int: "数值转整数", removed_accepted_answers: "删多余可接受答案", removed_options: "删多余选项",
  empty_accepted_answers_removed: "删空答案", choice_keys_normalized: "选项键统一", choice_prefix_removed: "去选项前缀", answer_letter_normalized: "答案字母统一",
  blank_marker_unified: "空格写法统一", fill_rubric_merged: "单空填空评分要点合并", fill_answer_added_to_accepted: "标准答案补入", rubric_tags_dropped: "删多余标签", difficulty_mapped: "难度映射", max_points_set_to_spec: "满分改规格值",
};

function spend(report) {
  const s = report.spend;
  $("spend-today").textContent = yuan(s.today_cny);
  $("spend-month").textContent = yuan(s.month_counted_cny) + " / " + yuan(s.month_cap_cny);
  $("spend-month").title = "本月已花 " + yuan(s.month_cny) + "，计入上限 " + yuan(s.month_counted_cny);
  const per = s.cost_per_admitted_question_cny.month;
  $("spend-per").textContent = per ? yuan(per) : "—";
  $("spend-per").title = per ? "本月入库 " + s.admitted_questions.month + " 题" : "本月还没有入库题目";
  $("spend-daily-left").textContent = s.remaining_daily_cny === null || s.remaining_daily_cny === undefined ? "未设上限" : yuan(s.remaining_daily_cny);
  $("spend-left").textContent = yuan(s.remaining_writing_review_cny);
  $("spend-grading").textContent = yuan(s.remaining_grading_cny);
  $("spend-note").textContent = s.message || "";
  const ledger = report.ledger || {};
  const today = (ledger.breakdown || {}).today;
  const split = $("spend-split");
  split.replaceChildren();
  if (today) {
    const parts = [
      ["actual", "实际", Number(today.settled.counted_cny) + Number(today.reconciled.counted_cny)],
      ["reserved", "预留", Number(today.running.counted_cny)],
      ["estimated", "估算", Number(today.estimated.counted_cny)],
    ];
    const sum = parts.reduce((n, p) => n + p[2], 0);
    const bar = el("div", undefined, split, "split");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", "今日费用 " + yuan(sum) + "：" + parts.map((p) => p[1] + " " + yuan(p[2])).join("，"));
    parts.forEach(([cls, , value]) => {
      if (value > 0) el("i", undefined, bar, cls).style.width = (value / sum) * 100 + "%";
    });
    const legend = el("ul", undefined, split, "spend-legend");
    parts.forEach(([cls, name, value]) => {
      const li = el("li", undefined, legend);
      el("i", undefined, li, "swatch").style.background = `var(--${cls === "actual" ? "primary" : cls === "reserved" ? "accent" : "muted"})`;
      li.append(`今日${name} `, el("b", yuan(value)));
    });
  }
  const month = (ledger.breakdown || {}).month;
  if (month) {
    const actual = Number(month.settled.counted_cny) + Number(month.reconciled.counted_cny);
    $("ledger-actual").textContent = yuan(actual);
    $("ledger-reserved").textContent = yuan(month.running.counted_cny);
    const checked = ledger.reconciliation && ledger.reconciliation.last_checked_at;
    $("ledger-checked").textContent = checked ? fmtTime(checked) : "还没有";
    const est = Number(month.estimated.counted_cny);
    const corr = ledger.reconciliation && ledger.reconciliation.last_correction;
    $("ledger-note").textContent =
      (month.estimated.count ? `另有 ${month.estimated.count} 笔没拿到用量的调用按近期中位数估算 ${yuan(est)}，等对账后修正。` : "") +
      (corr ? ` 上次修正：${fmtTime(corr.at)}，多算的 ${yuan(corr.difference_cny)} 已退回额度。` : "");
  }
  // cumulative spend this month by day, against the writing/review stop line and the monthly cap
  const days = s.daily || [];
  let running = 0;
  const items = days.map((d) => {
    running += Number(d.cny);
    return { label: d.day.slice(8).replace(/^0/, "") + "日", value: Math.round(running * 100) / 100, note: "当日 " + yuan(d.cny) };
  });
  const cap = Number(s.month_cap_cny), stop = Number(s.writing_review_limit_cny);
  Charts.columns($("spend-chart"), {
    title: "本月累计花费", hideTitle: true, unit: "元", items, max: Math.max(cap, running), format: (v) => "¥" + Math.round(v),
    refLines: [{ value: stop, label: "出题停止线 ¥" + Math.round(stop), color: Charts.COLORS.cell_biology }, { value: cap, label: "上限 ¥" + Math.round(cap), color: Charts.COLORS.danger }],
    conclusion: items.length ? `本月累计 ${yuan(running)}，距出题停止线还有 ${yuan(Math.max(0, stop - Number(s.month_counted_cny)))}。` : "",
    empty: "本月还没有花费", emptyHint: "出题、审核、判分产生费用后，每天一根柱子。",
  });
}

function jobTag(job) {
  if (job.state === "running") return ["进行中", "running"];
  // DeepSeek busy or unreachable: the task is simply waiting its turn again, which is not a failure and not counted as a try
  if (job.platform_busy) return ["DeepSeek 繁忙，自动等待重试", "pending"];
  if (job.state === "pending" || job.state === "retrying") return ["排队·第" + (job.attempts + 1) + "次", "pending"];
  if (job.outcome && job.outcome.status === "ready") return ["通过，已入库", "succeeded"];
  if (job.outcome && job.outcome.status === "blocked") return ["未通过", "blocked"];
  if (job.awaiting_dispatch) return ["完成·待入库", "pending"];
  if (job.state === "succeeded") return ["完成", "succeeded"];
  if (job.state === "cancelled") return ["已取消", "pending"];
  return [STATE[job.state] || job.state, job.state];
}

function jobs(report) {
  const root = $("jobs");
  root.replaceChildren();
  if (!report.recent_jobs.length) {
    el("li", "还没有任务记录", root, "empty");
    return;
  }
  for (const job of report.recent_jobs) {
    const li = el("li", undefined, root);
    const [tagText, tagClass] = jobTag(job);
    el("span", undefined, li, "dot " + tagClass).setAttribute("aria-hidden", "true");
    const title = job.label || (KIND[job.kind] || job.kind) + (job.question_type ? " · " + (SUBJECT[job.subject] || "") + (TYPE[job.question_type] || "") : "");
    el("span", title, li, "kind");
    if (job.executor === "claude") el("span", "Claude", li, "executor-tag");
    el("span", tagText, li, "state " + tagClass);
    const bits = [fmtTime(job.at)];
    if (job.state === "retrying" && job.next_retry) bits.push("下次重试 " + fmtTime(job.next_retry));
    if (job.outcome && job.outcome.status === "blocked") bits.push(reasonText(job.outcome.reason || "unspecified"));
    else if (job.outcome && job.outcome.status !== "ready") bits.push("题目状态：" + job.outcome.status);
    if (job.reason) bits.push(reasonText(job.reason));
    el("span", bits.join(" · "), li, "meta");
    // Reason codes and the cancel button live only in the opened details; the row says it in Chinese.
    const cancellable = ["pending", "retrying", "running"].includes(job.state);
    if (job.detail || job.reason || job.outcome?.reason || cancellable || (job.notes && job.notes.length)) {
      const more = el("details", undefined, li, "meta detail");
      el("summary", "详情", more);
      if (job.detail) {
        job.detail.split("；").forEach((part) => {
          const [field, reason] = part.split(":");
          const line = el("p", undefined, more, "reason-line");
          line.append(reason ? `${field}（${reasonText(reason)}） ` : `${reasonText(field)} `);
          el("code", part, line);
        });
      }
      [job.reason, job.outcome?.reason].filter(Boolean).forEach((code) => {
        const line = el("p", undefined, more, "reason-line");
        line.append(reasonText(code) + " ");
        el("code", code, line);
      });
      (job.notes || []).forEach((line) => el("p", line, more, "reason-line"));
      el("p", `任务 ${job.id} · 已尝试 ${job.attempts} 次`, more, "reason-line");
      if (cancellable) {
        const button = el("button", "取消任务", more, "cancel");
        button.type = "button";
        button.setAttribute("aria-label", "取消任务 " + job.id);
        button.onclick = () => cancelJob(job, button);
      }
    }
  }
}

async function cancelJob(job, button) {
  const running = job.state === "running";
  const text = running
    ? "这个任务正在运行，已经发出的请求无法撤回，仍可能产生费用。确定取消吗？"
    : "确定取消这个任务吗？取消后不会再执行。";
  if (!confirm(text)) return;
  button.disabled = true;
  try {
    await Data.fetchJSON("/v2/model/jobs/" + job.id + "/cancel", { method: "POST", body: "{}" });
    Data.invalidate("/v2/model/progress");
    await load(true);
  } catch (error) {
    button.disabled = false;
    if (error.name !== "AuthRequired") showError(error.message);
  }
}

function showError(message, hadCache) {
  $("pv-error-text").textContent = message + (hadCache ? "（先显示上次的内容）" : "");
  $("pv-error").hidden = false;
  if (!hadCache) {
    $("pv-skeleton").hidden = true;
    $("pv-content").hidden = true;
  }
}

const drawn = {};
function gradingCard(g) {
  const card = $("grading-card");
  card.hidden = !g || !g.pending;
  if (card.hidden) return;
  $("grading-note").textContent = g.late
    ? `有 ${g.late} 题交卷后超过 ${g.minutes} 分钟仍未判完，请检查下面的原因，或重新判分。`
    : `共 ${g.pending} 题等待判分（不受高峰暂停影响，会自动处理）。`;
  card.classList.toggle("late-grading", Boolean(g.late));
  const list = $("grading-reasons");
  list.replaceChildren();
  for (const r of g.by_reason) {
    const li = document.createElement("li");
    li.textContent = `${r.text}：${r.count} 题`;
    list.append(li);
  }
  const box = $("grading-papers");
  box.replaceChildren();
  for (const p of g.papers.slice(0, 5)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "secondary";
    const [, month, day] = (p.date || "").split("-");
    b.textContent = `${month ? `${Number(month)} 月 ${Number(day)} 日卷` : "这套卷"} · 重新判分（${p.count} 题）`;
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await fetch(`/v2/papers/${p.paper_id}/regrade`, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: "{}" });
        b.textContent = r.ok ? "已重新排队" : `失败（${r.status}）`;
      } catch (e) {
        b.textContent = "网络错误";
        b.disabled = false;
      }
    };
    box.append(b);
  }
}

function render(report, meta) {
  $("pv-skeleton").hidden = true;
  $("pv-error").hidden = true;
  $("pv-content").hidden = false;
  // Each section is redrawn only when ITS numbers changed (it reads the fields listed here). A page refreshed every 5 s would
  // otherwise close the details you opened and replace the buttons under your finger. The sections that show the time
  // (the next paper's estimate, the worker's "x minutes ago") are cheap and always redrawn.
  const same = (name, ...fields) => {
    const key = JSON.stringify(fields.map((f) => report[f]));
    if (drawn[name] === key) return true;
    drawn[name] = key;
    return false;
  };
  if (!same("batch", "totals", "target_kind", "review_today")) batch(report);
  paper(report);
  if (!same("grading", "grading")) gradingCard(report.grading);
  status(report);
  if (!same("bank", "buckets", "legacy", "target_kind")) bank(report);
  if (!same("funnel", "funnel")) funnel(report);
  if (!same("executors", "executors")) executors(report);
  if (!same("spend", "spend", "ledger")) spend(report);
  if (!same("waste", "waste")) waste(report);
  if (!same("jobs", "recent_jobs")) jobs(report);
  paintStamp(meta && meta.stale);
}
let lastShown = null;
// "更新于 HH:MM:SS" is when this page last got numbers from the server; it also says how the page is being kept fresh
function paintStamp(stale) {
  if (!lastShown) {
    if (stale) $("updated").textContent = "上次的内容，正在更新…";
    return;
  }
  const how = window.Live?.state().connected ? "实时推送中" : "每 5 秒刷新";
  $("updated").textContent = (stale ? "上次的内容，正在更新… · " : "") + "更新于 " + window.Live.stamp(lastShown) + " · " + how;
}

let loading = false;
let again = false;
async function load(fresh = false) {
  if (loading) {
    if (fresh) again = true; // a forced refresh (after cancelling a task, a pull down) is never lost behind a background poll
    return;
  }
  loading = true;
  try {
    await Data.swr("/v2/model/progress", {
      render,
      fetcher: async () => {
        const report = await Data.fetchJSON("/v2/model/progress" + (fresh ? "?fresh=1" : ""));
        lastShown = new Date(); // every successful read moves the time, even when the numbers did not change
        paintStamp(false);
        return report;
      },
      error: (error, meta) => showError(error.message, meta.hadCache),
    });
  } finally {
    loading = false;
    if (again) {
      again = false;
      load(true);
    }
  }
}

const visible = () => !$("progress-view").hidden && !document.hidden;
$("pv-retry").onclick = () => {
  $("pv-error").hidden = true;
  $("pv-skeleton").hidden = !Data.peek("/v2/model/progress");
  load(true);
};
// The page keeps itself fresh (live.js): pushed updates, a 5 s poll when the stream is down, nothing while hidden.
// A refresh reads the shared server copy; only "cancel a task" asks for a rebuild (?fresh=1).
window.Live.register("progress", { visible, load: () => load(false) });
window.Live.pullToRefresh($("progress-view"), () => load(false));
setInterval(() => paintStamp(false), 1000);
window.ProgressView = { show: () => { window.Live.touch("progress"); return load(); }, reload: () => { window.Live.touch("progress"); return load(true); } };
})();
