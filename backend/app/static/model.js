"use strict";
(() => {
const $ = (id) => document.getElementById(id);
let data = null; // last /v2/model reply
let bill = null; // last /v2/model/billing reply
let saved = null; // the form as last saved or loaded: what "unsaved" is measured against

const AuthRequired = Data.AuthRequired;

const api = (path, body) => Data.fetchJSON(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });

let toastTimer = null;
function say(text, ms = 3000) {
  const box = $("message");
  box.textContent = text;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), ms);
}
const yuan = (v) => (v === null || v === undefined || v === "" ? "—" : "¥" + Number(v).toFixed(2));
const when = (iso) =>
  iso ? new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const EFFORT = { off: "Off · 关闭", low: "Low · 低", high: "High · 高", max: "Max · 最高" };
const money = (v) => String(Number(v === "" || v === null || v === undefined ? 0 : v).toFixed(2));

/* ---- the form, as one object, so "unsaved" and "save" use the same data ---- */
function form() {
  return {
    model_id: $("model").value,
    effort: $("effort").value,
    auto_discover: $("discover").checked,
    auto_upgrade: $("upgrade").checked,
    weak_focus: $("weak-focus").checked,
    mistake_below: Number($("mistake-below").value) / 100,
    consolidate_below: Number($("consolidate-below").value) / 100,
    task_cny: money($("task-budget").value),
    trial_cny: money($("trial-budget").value),
    trial_active: $("trial-active").checked,
    daily_cny: money($("daily").value),
    monthly_cny: money($("monthly").value),
    task_limit_enabled: $("task-limit-enabled").checked,
    daily_limit_enabled: $("daily-limit-enabled").checked,
    monthly_limit_enabled: $("monthly-limit-enabled").checked,
    token_limit: Number($("token-limit").value),
    retry_limit: Number($("retry-limit").value),
    max_concurrency: Number($("max-concurrency").value),
  };
}
function fill(s) {
  $("task-budget").value = money(s.task_cny);
  $("trial-budget").value = money(s.trial_cny);
  $("trial-active").checked = !!s.trial_active;
  $("daily").value = money(s.daily_cny);
  $("monthly").value = money(s.monthly_cny);
  for (const k of ["task", "daily", "monthly"]) $(k + "-limit-enabled").checked = s[k + "_limit_enabled"] !== false;
  $("token-limit").value = s.token_limit;
  $("retry-limit").value = s.retry_limit;
  $("max-concurrency").value = String(s.max_concurrency);
  $("discover").checked = !!s.auto_discover;
  $("upgrade").checked = !!s.auto_upgrade;
  $("weak-focus").checked = s.weak_focus !== false;
  $("mistake-below").value = String(Math.round((s.mistake_below ?? 0.6) * 100));
  $("consolidate-below").value = String(Math.round((s.consolidate_below ?? 0.85) * 100));
}
const dirty = () => !!saved && JSON.stringify(form()) !== JSON.stringify(saved);
function refreshDirty() {
  const d = dirty();
  $("dirty-note").textContent = d ? "有未保存的改动" : "没有未保存的改动";
  $("dirty-note").classList.toggle("dirty", d);
  $("save").disabled = !d;
}
window.addEventListener("beforeunload", (event) => {
  if (dirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

/* ---- drawing ---- */
function modelRow() {
  return data.catalog.models.find((m) => m.id === $("model").value);
}
function drawModel() {
  const row = modelRow();
  $("effort").replaceChildren();
  for (const e of row?.efforts || []) {
    const o = document.createElement("option");
    o.value = e;
    o.textContent = EFFORT[e] || e;
    $("effort").append(o);
  }
  if (row?.efforts.includes(data.settings.effort)) $("effort").value = data.settings.effort;
  $("capabilities").textContent = row
    ? `上下文 ${row.context_window ?? "未知"} · 输出上限 ${row.max_output_tokens ?? "未知"} · 工具 ${row.tools ?? "未知"} · 图片 ${row.vision ?? "未知"} · 最近兼容性实测 ${row.compatibility_checked_at ?? "未执行"} · ${row.available ? "官方目录在列" : "已下线"} · ${row.compatible ? "兼容性已实测" : "尚未通过真实兼容性检查"}`
    : "尚无真实模型目录，请先配置服务端凭据并刷新。";
  drawPrice(row);
  drawConflict();
  drawProbes(row);
}
/* Two price versions in force with different rates and the official page unreadable: say which, and let the owner pick one. */
function drawConflict() {
  const card = $("price-confirm-card"), list = $("price-confirm-list");
  list.replaceChildren();
  const versions = data?.price_conflict;
  card.hidden = !versions || !versions.length;
  if (card.hidden) return;
  for (const v of versions) {
    const row = document.createElement("div");
    row.className = "price-version";
    const text = document.createElement("p");
    const o = v.off_peak, p = v.peak;
    text.textContent = `${v.model_id} · 模型版本 ${v.model_version ?? "未知"} · 抓取 ${when(v.created_at)} · 核实 ${when(v.verified_at)}` +
      ` · 低峰 ${o.cache_hit}/${o.cache_miss}/${o.output} · 高峰 ${p.cache_hit}/${p.cache_miss}/${p.output}（元/百万 Token：缓存命中/未命中/输出）`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "small-button";
    button.textContent = "采用这一版";
    button.onclick = async () => {
      try {
        await api("/v2/model/price-confirm", { price_id: v.price_id });
        $("price-confirm-note").textContent = "已确认，其余版本失效。";
        await load(true);
      } catch (e) {
        $("price-confirm-note").textContent = "确认失败：" + (e.message || e);
      }
    };
    row.append(text, button);
    list.append(row);
  }
}
function drawPrice(row) {
  const root = $("price");
  root.replaceChildren();
  const p = row?.prices;
  if (!p) {
    $("price-note").textContent = "人民币价格未知或冲突，付费任务会被挡住。点「刷新价格」重新读取。";
    return;
  }
  const grid = document.createElement("div");
  grid.className = "price-grid";
  const cell = (text, cls) => {
    const span = document.createElement("span");
    span.textContent = text;
    if (cls) span.className = cls;
    grid.append(span);
  };
  cell("元 / 百万 Token", "head");
  cell("低峰", "head num");
  cell("高峰", "head num");
  for (const [key, label] of [["cache_hit", "缓存命中"], ["cache_miss", "缓存未命中"], ["output", "输出"]]) {
    cell(label);
    cell(p.rules.off_peak[key], "num");
    cell(p.rules.peak[key], "num");
  }
  root.append(grid);
  $("price-note").textContent = `核实时间 ${when(p.verified_at)} · 版本 ${p.model_version ?? "未知"}`;
}
function drawProbes(row) {
  const root = $("probes");
  root.replaceChildren();
  const levels = ["off", "low", "high"];
  for (const level of levels) {
    const line = document.createElement("div");
    line.className = "probe";
    const name = document.createElement("span");
    name.textContent = EFFORT[level];
    const tag = document.createElement("span");
    const ok = !!row?.efforts.includes(level);
    tag.className = "tag" + (ok ? " ok" : "");
    tag.textContent = ok ? "已通过" : "未测试";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "small-button";
    button.textContent = "测试";
    button.disabled = !row;
    button.onclick = async () => {
      try {
        const job = await api("/v2/model/probe", { model_id: $("model").value, effort: level });
        tag.className = "tag wait";
        tag.textContent = "已排队";
        say(`测试任务 ${job.id} 已排队（${job.state}）；排队不代表调用成功。`);
      } catch (e) {
        failed(e);
      }
    };
    line.append(name, tag, button);
    root.append(line);
  }
}
function drawUsage() {
  const s = data.settings;
  $("used-day").textContent = `今日已用 ${yuan(bill.usage_estimate_cny.day)} / ${s.daily_limit_enabled === false ? "不限" : yuan(s.daily_cny)}`;
  $("used-month").textContent = `本月已用 ${yuan(bill.usage_estimate_cny.month)} / ${s.monthly_limit_enabled === false ? "不限" : yuan(s.monthly_cny)}`;
  $("used-task").textContent = s.task_limit_enabled === false ? "不限" : "每个任务最多 " + yuan(s.task_cny);
  const tile = (id, text) => ($(id).textContent = text);
  const left = (v) => (v === null || v === undefined ? "未设上限" : yuan(v));
  tile("bl-day", left(bill.remaining_daily_cny));
  tile("bl-month", left(bill.remaining_monthly_cny));
  tile("bl-reserved", yuan(bill.reserved_cny.trial));
  tile("bl-unknown", yuan(bill.unknown_reserved_cny));
  // the part of the month's cap kept for grading: grading is bound by the total cap only, so this is shown on its own
  api("/v2/model/spend").then((sp) => tile("bl-grading", yuan(sp.remaining_grading_cny))).catch(() => {});
  tile("bl-calls", String(bill.call_attempts));
  tile("bl-invoice", bill.invoice_status === "has_invoice_events" ? "已对账" : "未对账");
  $("bl-period").textContent = "当前时段：" + (bill.period === "peak" ? "高峰（价格较高，出题暂缓）" : "低峰");
  // used / limit as two small bars (the limit is the full width; no limit = no bar)
  [["day", bill.usage_estimate_cny.day, s.daily_limit_enabled === false ? null : s.daily_cny],
   ["month", bill.usage_estimate_cny.month, s.monthly_limit_enabled === false ? null : s.monthly_cny]].forEach(([id, used, cap]) => {
    $("usage-" + id + "-text").textContent = `${yuan(used)} / ${cap === null ? "不限" : yuan(cap)}`;
    const root = $("usage-" + id);
    root.replaceChildren();
    if (cap === null || !Number(cap)) return;
    window.Charts.progress(root, { label: id === "day" ? "今日已用" : "本月已用", text: `${yuan(used)} / ${yuan(cap)}`, value: Number(used), max: Number(cap), warnBelowHalf: false,
      color: Number(used) >= Number(cap) * 0.9 ? "var(--accent)" : "var(--primary)" });
  });
  $("billing-history").textContent = JSON.stringify({ price: bill.price, events: bill.events, by_task: bill.by_task, model_changes: bill.settings_history }, null, 2);
}
function drawStop() {
  const stopped = data.settings.stopped;
  $("stop-toggle").textContent = stopped ? "恢复 AI 任务" : "停止 AI 任务";
  $("stop-note").textContent = stopped ? "AI 任务现在是停止状态：不会再出题、审核或判分。" : "停止后不再出题、审核或判分；已经发出的请求无法撤回。";
}

/* ---- loading and saving ---- */
const BILL_FALLBACK = { usage_estimate_cny: { day: null, month: null }, remaining_daily_cny: null, remaining_monthly_cny: null, reserved_cny: { trial: null }, unknown_reserved_cny: null, call_attempts: "—", period: "—", invoice_status: "—", events: [], by_task: [], settings_history: [] };

function paint(d, b, keepForm) {
  data = d;
  bill = b;
  $("mv-skeleton").hidden = true;
  $("mv-error").hidden = true;
  $("controls").hidden = false;
  $("savebar").hidden = false;
  if (!keepForm) {
    $("model").replaceChildren();
    for (const m of data.catalog.models) {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.name + (m.available ? "" : "（已下线）");
      $("model").append(o);
    }
    if (data.catalog.models.some((m) => m.id === data.settings.model_id)) $("model").value = data.settings.model_id;
    fill(data.settings);
    drawModel();
  } else {
    // unsaved typing is never overwritten by a refresh: only the read-only parts are redrawn
    drawPrice(modelRow());
    drawProbes(modelRow());
  }
  drawUsage();
  drawStop();
  if (!keepForm) saved = form();
  refreshDirty();
}

function showError(message, hadCache) {
  $("mv-error-text").textContent = message + (hadCache ? "（先显示上次的内容）" : "");
  $("mv-error").hidden = false;
  if (!hadCache) {
    $("mv-skeleton").hidden = true;
    $("controls").hidden = true;
    $("savebar").hidden = true;
  }
}

let loading = false;
async function load(keepForm = false) {
  if (loading) return;
  loading = true;
  try {
    const cachedData = Data.peek("/v2/model");
    if (cachedData && !data) {
      const cachedBill = Data.peek("/v2/model/billing");
      paint(cachedData.data, cachedBill ? cachedBill.data : BILL_FALLBACK, false);  // what was seen before, at once
    }
    const [d, b] = await Promise.all([
      api("/v2/model"),
      api("/v2/model/billing").catch((e) => {
        if (e instanceof AuthRequired) throw e;
        say("费用账本暂时读不出来：" + e.message);
        return BILL_FALLBACK;
      }),
    ]);
    Data.store("/v2/model", d);
    if (b !== BILL_FALLBACK) Data.store("/v2/model/billing", b);
    paint(d, b, keepForm || (!!data && dirty()));
  } catch (e) {
    if (!(e instanceof AuthRequired)) showError(e.message, !!data);
  } finally {
    loading = false;
  }
}

function failed(error) {
  if (error instanceof AuthRequired) return;
  say(error.message, 5000);
}

$("save").onclick = async () => {
  const body = form();
  $("save").disabled = true;
  try {
    const reply = await api("/v2/model/save", body);
    const s = reply.settings;
    // What the server stored must be what was typed; anything else is shown, never hidden.
    const mismatch = ["max_concurrency", "token_limit", "retry_limit", "daily_cny", "monthly_cny", "task_cny"].filter((k) => String(Number(s[k])) !== String(Number(body[k])));
    if (mismatch.length) throw new Error("保存后读到的值和提交的不一致：" + mismatch.join("、"));
    data.settings = s;
    saved = form(); // what is on screen is what the server now has: nothing is unsaved any more
    refreshDirty();
    await load();
    say("已保存");
  } catch (e) {
    refreshDirty();
    failed(e);
  }
};
$("model").onchange = () => {
  drawModel();
  refreshDirty();
};
document.querySelectorAll("#controls input, #controls select").forEach((node) => {
  if (node.id === "text") return;
  node.addEventListener("input", refreshDirty);
  node.addEventListener("change", refreshDirty);
});
$("effort").addEventListener("change", refreshDirty);
$("price-refresh").onclick = async () => {
  try {
    await api("/v2/model/prices/refresh", {});
    await load(true);
    say("价格已从官方价格页刷新");
  } catch (e) {
    failed(e);
  }
};
$("refresh").onclick = async () => {
  try {
    await api("/v2/model/refresh", {});
    await load();
    say("官方目录已刷新");
  } catch (e) {
    failed(e);
  }
};
$("stop-toggle").onclick = async () => {
  const stop = !data.settings.stopped;
  if (!confirm(stop ? "确定停止所有 AI 任务吗？停止后不会再出题、审核或判分。" : "确定恢复 AI 任务吗？")) return;
  try {
    const reply = await api("/v2/model/stopped", { stopped: stop });
    data.settings = reply.settings;
    drawStop();
    say(stop ? "AI 任务已停止" : "AI 任务已恢复");
  } catch (e) {
    failed(e);
  }
};
$("default").onclick = async () => {
  if (!confirm("确定恢复默认吗？会把模型和思考强度改回默认并暂停 AI 任务，费用上限保持不变。")) return;
  try {
    await api("/v2/model/command", { command: "/model default" });
    await load();
    say("已恢复默认并暂停任务");
  } catch (e) {
    failed(e);
  }
};
$("command").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const reply = await api("/v2/model/command", { command: $("text").value });
    await load();
    $("help").textContent = reply.help || "设置状态已刷新";
  } catch (e) {
    failed(e);
  }
};
$("backup").onclick = async () => {
  try {
    say("正在生成私有快照，尚未完成下载。");
    const b = await api("/v2/model/backup/refresh", {});
    $("backup-info").textContent = b.bytes + " 字节 · SHA-256 " + b.sha256;
    say("快照完成，请下载并保存到自己的文件位置；尚未确认实际保存。", 5000);
  } catch (e) {
    failed(e);
  }
};

$("mv-retry").onclick = () => {
  $("mv-error").hidden = true;
  $("mv-skeleton").hidden = !!data;
  load(!!data);
};
window.Live.pullToRefresh($("model-view"), () => load(true));      // unsaved edits stay
window.ModelView = { show: () => load(!!data), dirty, leave: () => !dirty() || confirm("有未保存的改动，确定离开吗？") };
})();
