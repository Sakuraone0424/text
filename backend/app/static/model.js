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
    ? `上下文 ${row.context_window ?? "未知"} · 输出上限 ${row.max_output_tokens ?? "未知"} · 工具 ${row.tools ?? "未知"} · 图片 ${row.vision ?? "未知"} · 价格 ${row.prices ? JSON.stringify(row.prices) : "未知，任务将阻止"} · ${row.available ? "官方目录在列" : "已下线"} · ${row.compatible ? "兼容性已实测" : "尚未通过真实兼容性检查"}`
    : "尚无真实模型目录，请先配置服务端凭据并刷新。";
  $("save").disabled =
    !row?.compatible || !row?.available || !row?.efforts.length;
}
async function load() {
  data = await api("/v2/model");
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
  $("daily").value = data.settings.daily_usd;
  $("monthly").value = data.settings.monthly_usd;
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
      daily_usd: Number($("daily").value),
      monthly_usd: Number($("monthly").value),
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
      token_limit: Number($("token-limit").value),
      retry_limit: Number($("retry-limit").value),
      max_concurrency: Number($("max-concurrency").value),
      daily_usd: Number($("daily").value),
      monthly_usd: Number($("monthly").value),
      stopped: $("stopped").checked,
    });
    await load();
  } catch (e) {
    say(e.message);
  }
};
$("probe").onclick = async () => {
  try {
    if (!$("in-price").value || !$("out-price").value)
      throw Error("请先据官方价格填写明确上界，不得猜测。");
    const job = await api("/v2/model/probe", {
      model_id: $("model").value,
      effort: $("probe-effort").value,
      input_per_million: Number($("in-price").value),
      output_per_million: Number($("out-price").value),
      source: $("price-source").value,
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
