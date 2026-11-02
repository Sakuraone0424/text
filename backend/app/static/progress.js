"use strict";
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
  model_delisted_or_catalog_changed: "模型目录变化",
  superseded_scoring: "旧分值已停用",
  invalid_or_uncertain_result: "结果无效或不确定",
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
const reasonText = (code) => REASON[code] || REASON[String(code).split(":")[0]] || "原因未归类";

function duration(hours) {
  if (hours < 1) return "不到 1 小时";
  if (hours < 48) return "约 " + Math.round(hours) + " 小时";
  return "约 " + (hours / 24).toFixed(1) + " 天";
}

function paper(report) {
  const p = report.next_paper;
  const main = $("paper-main"), note = $("paper-note");
  main.className = "";
  if (p.complete) {
    main.textContent = "已经够了";
    note.textContent = "每个科目 × 题型的已入库题数都达到整卷所需。";
  } else if (p.eta_hours !== null) {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "预计" + duration(p.eta_hours) + "。按最近 " + p.approved_last_hours + " 小时入库 " + p.approved_in_window + " 题估算，实际受低峰时段和审核通过率影响。";
  } else {
    main.textContent = "还差 " + p.missing_questions + " 题";
    note.textContent = "最近 " + p.approved_last_hours + " 小时没有新题入库，暂时无法估算时间。";
  }
}

function status(report) {
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
  if (w.stopped_because) parts.push("停止原因：" + reasonText(w.stopped_because));
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
  C().progress($("batch-progress"), { label: "已入库", text: `${t.stocked} / ${t.target}`, value: t.stocked, max: t.target, color: t.stocked >= t.target ? "#15803D" : "#2563EB" });
  const today = $("admitted-today");
  today.hidden = false;
  today.textContent = "今日入库 " + t.admitted_today + " 道";
  const r = report.review_today;
  $("review-rate").textContent = r.rate === null ? "—" : `${Math.round(r.rate * 100)}%`;
  $("review-rate").title = r.rate === null ? "今天还没有审核" : `${r.passed}/${r.reviewed}`;
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

function bank(report) {
  lastReport = report;
  document.querySelectorAll("[data-subject]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.subject === subject)));
  const root = $("bank");
  root.replaceChildren();
  const label = report.target_kind === "first_batch" ? "首批目标" : "两天储备";
  for (const key of Object.keys(SUBJECT)) {
    const group = el("div", undefined, root, "group");
    group.hidden = key !== subject;
    el("p", SUBJECT[key], group, "subject");
    group.querySelector(".subject").hidden = true; // the segmented switch already names the subject
    for (const b of report.buckets.filter((x) => x.subject === key)) {
      const reached = b.stocked >= b.target;
      const row = el("div", undefined, group, "row");
      const head = el("div", undefined, row, "head");
      el("span", TYPE[b.question_type], head, "name");
      const count = el("span", undefined, head, "count");
      count.append(el("b", String(b.stocked)), ` / ${b.target} ${label}`);
      if (reached) el("span", "已达" + label, count, "reached");
      const bar = el("div", undefined, row, "bar" + (reached ? " full" : ""));
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", SUBJECT[key] + TYPE[b.question_type] + "已入库 " + b.stocked + "，" + label + " " + b.target);
      el("i", undefined, bar).style.width = Math.min(100, b.target ? (b.stocked / b.target) * 100 : 0) + "%";
      const notes = el("p", undefined, row, "notes");
      const bits = [`今日入库 ${b.admitted_today} 道`];
      if (b.generating) bits.push(`出题中 ${b.generating}`);
      if (b.reviewing) bits.push(`审核中 ${b.reviewing}`);
      if (b.queued) bits.push(`排队 ${b.queued}`);
      notes.append(bits.join(" · "));
      if (b.blocked) el("span", ` · 被挡住 ${b.blocked}`, notes, "attn");
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
    refLines: [{ value: stop, label: "出题停止线 ¥" + Math.round(stop), color: Charts.COLORS.cell_biology }, { value: cap, label: "上限 ¥" + Math.round(cap), color: "#B91C1C" }],
    conclusion: items.length ? `本月累计 ${yuan(running)}，距出题停止线还有 ${yuan(Math.max(0, stop - Number(s.month_counted_cny)))}。` : "",
    empty: "本月还没有花费", emptyHint: "出题、审核、判分产生费用后，每天一根柱子。",
  });
}

function jobTag(job) {
  if (job.state === "running") return ["进行中", "running"];
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
    const title = job.label || (KIND[job.kind] || job.kind) + (job.question_type ? " · " + (SUBJECT[job.subject] || "") + (TYPE[job.question_type] || "") : "");
    el("span", title, li, "kind");
    const [tagText, tagClass] = jobTag(job);
    el("span", tagText, li, "state " + tagClass);
    const bits = [fmtTime(job.at)];
    if (job.state === "retrying" && job.next_retry) bits.push("下次重试 " + fmtTime(job.next_retry));
    if (job.outcome && job.outcome.status === "blocked") bits.push(reasonText(job.outcome.reason || "unspecified"));
    else if (job.outcome && job.outcome.status !== "ready") bits.push("题目状态：" + job.outcome.status);
    if (job.reason) bits.push(reasonText(job.reason));
    el("span", bits.join(" · "), li, "meta");
    // Reason codes and the cancel button live only in the opened details; the row says it in Chinese.
    const cancellable = ["pending", "retrying", "running"].includes(job.state);
    if (job.detail || job.reason || job.outcome?.reason || cancellable) {
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
    const response = await fetch("/v2/model/jobs/" + job.id + "/cancel", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error("取消失败（" + response.status + "）");
    await load();
  } catch (error) {
    button.disabled = false;
    $("notice").hidden = false;
    $("notice").textContent = error.message;
  }
}

async function load() {
  try {
    const response = await fetch("/v2/model/progress", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) {
      $("notice").hidden = false;
      $("content").hidden = true;
      $("notice").textContent = "需要登录。请先回到首页登录，再打开本页。";
      $("updated").textContent = "";
      return;
    }
    if (!response.ok) throw new Error("读取失败（" + response.status + "）");
    const report = await response.json();
    $("notice").hidden = true;
    $("content").hidden = false;
    batch(report);
    paper(report);
    status(report);
    bank(report);
    spend(report);
    jobs(report);
    $("updated").textContent = "每 30 秒自动刷新 · 更新于 " + fmtTime(report.generated_at);
  } catch (error) {
    $("notice").hidden = false;
    $("notice").textContent = error.message + "。稍后会自动重试。";
  }
}
load();
setInterval(() => {
  if (!document.hidden) load();
}, 30000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});
