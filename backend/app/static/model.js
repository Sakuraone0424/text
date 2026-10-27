"use strict";
const $ = (id) => document.getElementById(id);
let data;
async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const d = await r.json();
  if (!r.ok)
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail),
    );
  return d;
}
function say(s) {
  $("message").textContent = s;
}
function priceLabel(p) {
  if (!p) return "人民币价格未知或冲突，阻止付费任务";
  const a = p.rules.off_peak,
    b = p.rules.peak;
  return `CNY元/百万Token（空闲/高峰）：缓存命中 ${a.cache_hit}/${b.cache_hit}，未命中 ${a.cache_miss}/${b.cache_miss}，输出 ${a.output}/${b.output}；价格适用版本 ${p.model_version ?? "未知"}`;
}
function options() {
  const row = data.catalog.models.find((m) => m.id === $("model").value);
  $("effort").replaceChildren();
  for (const e of row?.efforts || []) {
    const o = document.createElement("option");
    o.value = e;
    o.textContent =
      {
        off: "Off · 关闭",
        low: "Low · 低",
        high: "High · 高",
        max: "Max · 最高",
      }[e] || e;
    $("effort").append(o);
  }
  if (row?.efforts.includes(data.settings.effort))
    $("effort").value = data.settings.effort;
  $("capabilities").textContent = row
    ? `上下文 ${row.context_window ?? "未知"} · 输出上限 ${row.max_output_tokens ?? "未知"} · 工具 ${row.tools ?? "未知"} · 图片 ${row.vision ?? "未知"} · 价格 ${priceLabel(row.prices)} · 最近兼容性实测 ${row.compatibility_checked_at ?? "未执行"} · ${row.available ? "官方目录在列" : "已下线"} · ${row.compatible ? "兼容性已实测" : "尚未通过真实兼容性检查"}`
    : "尚无真实模型目录，请先配置服务端凭据并刷新。";
  $("save").disabled =
    !row?.compatible || !row?.available || !row?.efforts.length;
}
async function load() {
  data = await api("/v2/model");
  const bill = await api("/v2/model/billing");
  $("billing").textContent =
    `CNY · 今日usage估算 ¥${bill.usage_estimate_cny.day} · 本月usage估算 ¥${bill.usage_estimate_cny.month} · 今日剩余预算 ¥${bill.remaining_daily_cny} · 本月剩余预算 ¥${bill.remaining_monthly_cny} · 累计预留 ¥${bill.reserved_cny.trial} · 未知待对账预留 ¥${bill.unknown_reserved_cny} · 调用尝试 ${bill.call_attempts} · 当前时段 ${bill.period} · Search/Fetch 禁用 · 官方账单 ${bill.invoice_status}`;
  $("billing-history").textContent = JSON.stringify(
    {
      price: bill.price,
      events: bill.events,
    by_task: bill.by_task,
      model_changes: bill.settings_history,
    },
    null,
    2,
  );
  $("task-budget").value = data.settings.task_cny;
  $("trial-budget").value = data.settings.trial_cny;
  $("trial-active").checked = data.settings.trial_active;
  $("login").hidden = true;
  $("controls").hidden = false;
  $("model").replaceChildren();
  for (const m of data.catalog.models) {
    const o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.name + " · " + m.id + (m.available ? "" : "（已下线）");
    $("model").append(o);
  }
  if (data.catalog.models.some((m) => m.id === data.settings.model_id))
    $("model").value = data.settings.model_id;
  $("token-limit").value = data.settings.token_limit;
  $("retry-limit").value = data.settings.retry_limit;
  $("max-concurrency").value = data.settings.max_concurrency;
  $("daily").value = data.settings.daily_cny;
  $("monthly").value = data.settings.monthly_cny;
  $("discover").checked = data.settings.auto_discover;
  $("upgrade").checked = data.settings.auto_upgrade;
  $("stopped").checked = data.settings.stopped;
  options();
  say(
    `当前默认 ${data.settings.model_id} / ${data.settings.effort}。${data.catalog.stale ? "目录可能过期或尚未成功获取。" : ""}`,
  );
}
$("model").onchange = options;
$("login").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/auth/login", { password: $("password").value });
    $("password").value = "";
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("save").onclick = async () => {
  try {
    await api("/v2/model", {
      model_id: $("model").value,
      effort: $("effort").value,
      auto_discover: $("discover").checked,
      auto_upgrade: $("upgrade").checked,
      stopped: $("stopped").checked,
      daily_cny: $("daily").value,
      monthly_cny: $("monthly").value,
    });
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("refresh").onclick = async () => {
  try {
    await api("/v2/model/refresh", {});
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("default").onclick = async () => {
  try {
    await api("/v2/model/command", { command: "/model default" });
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("command").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const d = await api("/v2/model/command", { command: $("text").value });
    await load();
    $("help").textContent = d.help || "设置状态已刷新";
  } catch (e) {
    say(e.message);
  }
};
load().catch(() => say("请登录。密钥只能通过服务端Secret配置。"));

$("limits").onclick = async () => {
  try {
    await api("/v2/model/limits", {
      task_cny: $("task-budget").value,
      trial_cny: $("trial-budget").value,
      trial_active: $("trial-active").checked,
      token_limit: Number($("token-limit").value),
      retry_limit: Number($("retry-limit").value),
      max_concurrency: Number($("max-concurrency").value),
      daily_cny: $("daily").value,
      monthly_cny: $("monthly").value,
      stopped: $("stopped").checked,
    });
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("probe").onclick = async () => {
  try {
    const job = await api("/v2/model/probe", {
      model_id: $("model").value,
      effort: $("probe-effort").value,
    });
    say("探测任务 " + job.id + "：" + job.state + "；排队不代表调用成功。");
  } catch (e) {
    say(e.message);
  }
};

$("backup").onclick = async () => {
  try {
    say("正在生成私有快照，尚未完成下载。");
    const b = await api("/v2/model/backup/refresh", {});
    $("backup-info").textContent = b.bytes + "字节 · SHA-256 " + b.sha256;
    say("快照完成，请下载并保存到自己的文件位置；尚未确认实际保存。");
  } catch (e) {
    say(e.message);
  }
};

$("price-refresh").onclick = async () => {
  try {
    await api("/v2/model/prices/refresh", {});
    await load();
  } catch (e) {
    say(e.message);
  }
};
