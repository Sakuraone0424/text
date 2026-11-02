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
const reasonText = (code) => REASON[code] || code;

function duration(hours) {
  if (hours < 1) return "不到 1 小时";
  if (hours < 48) return "约 " + Math.round(hours) + " 小时";
  return "约 " + (hours / 24).toFixed(1) + " 天";
}

function paper(report) {
  const p = report.next_paper;
  if (p.complete) {
    $("paper-main").textContent = "题库已经够一张完整卷子";
    $("paper-note").textContent = "每个科目 × 题型的已入库题数都达到整卷所需。";
  } else if (p.eta_hours !== null) {
    $("paper-main").textContent = "还差 " + p.missing_questions + " 题，预计 " + duration(p.eta_hours);
    $("paper-note").textContent =
      "按最近 " + p.approved_last_hours + " 小时入库 " + p.approved_in_window + " 题（约每小时 " + p.per_hour + " 题）估算，实际受低峰时段和审核通过率影响。";
  } else {
    $("paper-main").textContent = "还差 " + p.missing_questions + " 题，暂时无法估算时间";
    $("paper-note").textContent = "最近 " + p.approved_last_hours + " 小时没有新题入库，没有足够记录可估算。";
  }
}

function status(report) {
  const period = report.period;
  $("period").textContent = period.is_peak
    ? "高峰 · 出题暂缓，" + fmtTime(period.next_off_peak_at) + " 起低峰"
    : "低峰 · 可出题，下个高峰 " + fmtTime(period.next_peak_at);
  const w = report.worker;
  const note = $("worker-note");
  note.className = "muted";
  if (!w.last_run_at) {
    $("worker").textContent = "还没有运行记录";
    note.textContent = "出题程序（定时任务）还没有留下运行记录：请检查它是否已部署并启用。";
    note.classList.add("warn");
    return;
  }
  const outcome = w.outcome === "ok" ? "正常" : w.outcome === "disabled" ? "未启用" : "出错（" + w.outcome.replace("error:", "") + "）";
  $("worker").textContent = fmtTime(w.last_run_at) + " · " + outcome;
  const parts = ["调用 " + w.calls + " 次", "费用 " + yuan(w.cost_cny)];
  if (w.stopped_because) parts.push("停止原因：" + reasonText(w.stopped_because));
  note.textContent = parts.join(" · ") + (w.stale ? "。已超过 " + w.minutes_ago + " 分钟没有运行（正常每 5 分钟一次）。" : "。");
  if (w.stale || w.outcome !== "ok") note.classList.add("warn");
}

function bank(report) {
  const root = $("bank");
  root.replaceChildren();
  for (const subject of Object.keys(SUBJECT)) {
    el("p", SUBJECT[subject], root, "subject");
    for (const b of report.buckets.filter((x) => x.subject === subject)) {
      const row = el("div", undefined, root, "row");
      const head = el("div", undefined, row, "head");
      el("span", TYPE[b.question_type], head, "name");
      el("span", b.stocked + " / " + b.target, head, "count");
      const bar = el("div", undefined, row, "bar" + (b.stocked >= b.target ? " full" : ""));
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", SUBJECT[subject] + TYPE[b.question_type] + "已入库 " + b.stocked + "，目标 " + b.target);
      el("i", undefined, bar).style.width = Math.min(100, b.target ? (b.stocked / b.target) * 100 : 0) + "%";
      const chips = el("div", undefined, row, "chips");
      el("span", "排队中 " + b.queued, chips, "chip");
      el("span", "出题中 " + b.generating, chips, "chip");
      el("span", "审核中 " + b.reviewing, chips, "chip review");
      el("span", "被挡住 " + b.blocked, chips, "chip" + (b.blocked ? " blocked" : ""));
      const codes = Object.entries(b.blocked_reasons);
      if (codes.length) el("p", codes.map(([code, n]) => reasonText(code) + " ×" + n).join("；"), row, "reasons");
    }
  }
}

function spend(report) {
  const s = report.spend;
  $("spend-today").textContent = yuan(s.today_cny);
  $("spend-month").textContent = yuan(s.month_cny) + "（计入上限 " + yuan(s.month_counted_cny) + " / " + yuan(s.month_cap_cny) + "）";
  const per = s.cost_per_admitted_question_cny.month;
  $("spend-per").textContent = per
    ? yuan(per) + "（本月入库 " + s.admitted_questions.month + " 题）"
    : "本月还没有入库题目";
  $("spend-left").textContent = yuan(s.remaining_writing_review_cny);
  $("spend-grading").textContent = yuan(s.remaining_grading_cny);
  $("spend-note").textContent = s.message || "";
}

function jobs(report) {
  const root = $("jobs");
  root.replaceChildren();
  if (!report.recent_jobs.length) {
    el("li", "还没有任务记录。", root);
    return;
  }
  for (const job of report.recent_jobs) {
    const li = el("li", undefined, root);
    const title = (KIND[job.kind] || job.kind) + (job.question_type ? " · " + (SUBJECT[job.subject] || "") + (TYPE[job.question_type] || "") : "");
    el("span", title, li, "kind");
    el("span", STATE[job.state] || job.state, li, "state " + job.state);
    el("span", fmtTime(job.at) + (job.reason ? " · " + reasonText(job.reason) : ""), li, "meta");
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
