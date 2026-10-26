"use strict";
const $ = (id) => document.getElementById(id);
const labels = {
  single_choice: "单选",
  fill_blank: "填空",
  term: "名词解释",
  short_answer: "简答",
  essay: "论述",
  biochemistry: "生化",
  cell_biology: "细胞",
  approve_candidate: "候选审核通过（未发布）",
  return: "退回",
  revise: "要求修改",
  pending_human_review: "等待人工确认",
};
const checks = {
  scientific_correctness: "科学机制与适用条件",
  exam_style: "题型与转录风格依据",
  uniqueness: "单选唯一性／答案明确性",
  type_fit: "题型与分值适配",
  duplication: "题目重复及概念重叠",
  ocr_excerpt: "逐字核对所引用OCR摘录",
  answer_rubric: "答案与每一评分点",
  knowledge_mapping: "学科与知识点关联",
  transcription_level: "确认转录未冒充官方原题或考频",
};
let session = null,
  items = [],
  current = null,
  backup = null;
let opinion = null,
  opening = 0,
  signing = false,
  localKeys = new Set();
function text(tag, value, parent, cls) {
  const e = document.createElement(tag);
  e.textContent = value;
  if (cls) e.className = cls;
  parent.appendChild(e);
  return e;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(session ? { "x-review-csrf": session.csrf } : {}),
      ...options.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    let data = await response.json().catch(() => ({ detail: "服务暂不可用" }));
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function formatTime(value) {
  const d = new Date(
    String(value)
      .replace(" ", "T")
      .replace(/(\.\d{3})\d+/, "$1"),
  );
  return Number.isNaN(d.getTime())
    ? "时间待核"
    : d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) +
        "（北京时间）";
}
function message(value) {
  $("message").textContent = value;
}
async function enter() {
  session = await api("/api/session");
  $("identity").textContent = `审核人：${session.reviewer}`;
  $("login").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
  await load();
  const position = await api("/api/position");
  $("subject").value = position.subject_filter;
  $("type").value = position.type_filter;
  await refreshLocalKeys();
  if (
    position.candidate_key &&
    items.some((i) => i.candidate_key === position.candidate_key)
  )
    await open(position.candidate_key);
}
async function load() {
  const data = await api("/api/candidates");
  items = data.candidates;
  $("summary").textContent =
    `AI辅助草稿 ${data.ai_assisted} · 等待处理 ${data.pending} · 人工候选通过 ${data.human_candidate_approved} · 正式合格 ${data.formal_qualified}`;
  $("summary").textContent +=
    " · 教材科学审核 " + data.textbook_science_reviewed_units;
  const progress = $("type-progress");
  progress.replaceChildren();
  (data.by_subject_type || []).forEach((r) =>
    text(
      "p",
      `${labels[r.subject]} · ${labels[r.type]}：候选${r.total}，人工通过${r.approved}，未处理${r.untouched}，退回${r.returned}，需修改${r.revision_requested}，正式合格${r.formal_qualified}/${r.required}`,
      progress,
    ),
  );
  list();
}
function list() {
  const root = $("list");
  root.replaceChildren();
  for (const row of items.filter(
    (r) =>
      (!$("subject").value || r.subject === $("subject").value) &&
      (!$("type").value || r.type === $("type").value),
  )) {
    const b = text(
      "button",
      `${labels[row.subject]} · ${labels[row.type]} · ${row.candidate_key} · ${labels[row.action] || "待审"}${localKeys.has(row.candidate_key) ? " · 本机待同步" : ""}`,
      root,
    );
    b.type = "button";
    b.setAttribute(
      "aria-current",
      String(current && current.candidate_key === row.candidate_key),
    );
    b.onclick = () => open(row.candidate_key).catch((e) => message(e.message));
  }
}
async function open(key) {
  if (signing && key !== current?.candidate_key)
    throw new Error("正在保存人工决定，请稍后切换");
  const sequence = ++opening;
  if (opinion && opinion.dirty) {
    await opinion.persist().catch(() => {});
    await opinion
      .flush()
      .catch((e) => message("上一题意见未覆盖，已保留加密草稿。" + e.message));
  }
  const packet = await api("/api/candidates/" + encodeURIComponent(key));
  if (sequence !== opening) return;
  current = packet;
  opinion = null;
  const q = current.question_snapshot,
    root = $("detail");
  root.replaceChildren();
  text(
    "h2",
    `${labels[q.subject]} · ${labels[q.question_type]} · ${q.max_points}分`,
    root,
  );
  text(
    "p",
    `${key} · ${labels[current.status]} · 版本 ${current.revision_sha256.slice(0, 16)}`,
    root,
  );
  text("pre", q.stem, root);
  for (const [k, v] of Object.entries(q.options)) text("p", `${k}. ${v}`, root);
  if (current.superseded_by) {
    text("p", "此版本已被新版替代，旧审核不自动继承。", root, "warning");
    const latest = text("button", "打开最新版本", root);
    latest.onclick = () =>
      open(current.superseded_by).catch((e) => message(e.message));
  }
  text("h3", "参考答案", root);
  text("pre", q.answer, root);
  if (q.blanks.length)
    q.blanks.forEach((b, i) =>
      text(
        "p",
        `第${i + 1}空（${b.points}分）：${b.accepted_answers.join(" / ")}`,
        root,
      ),
    );
  text("h3", "逐项评分", root);
  q.rubric.forEach((r, i) =>
    text("p", `${i + 1}. ${r.description}（${r.points}分）`, root),
  );
  text("p", `知识点：${q.knowledge_tags.join("、")}`, root);
  text("h3", "教材证据", root);
  for (const [i, e] of current.evidence_snapshot.entries()) {
    const d = document.createElement("details");
    d.open = true;
    root.append(d);
    text(
      "summary",
      `${q.evidence[i].textbook} · ${e.chapter} · OCR ${e.ocr_page} · 单元 ${e.unit_id}`,
      d,
    );
    text("p", `${e.file_id} · SHA ${e.source_sha256}`, d);
    text("p", e.section, d);
    text("pre", q.evidence[i].excerpt, d);
    text(
      "p",
      `来源风险：${e.quality_flags.join("、") || "规则未报告风险，仍需人工核对"}`,
      d,
    );
    const full = document.createElement("details");
    d.append(full);
    text("summary", "查看完整索引单元", full);
    text("pre", e.text, full);
    const contextButton = text("button", "查看前后索引段落", full);
    contextButton.type = "button";
    contextButton.onclick = async () => {
      try {
        const context = await api("/api/sources/" + e.unit_id + "/context");
        contextButton.disabled = true;
        context.units.forEach((u) => {
          text("p", `OCR ${u.ocr_page} · 单元 ${u.unit_id}`, full);
          text("pre", u.text, full);
        });
      } catch (error) {
        message(error.message);
      }
    };
  }
  for (const context of current.style_context || []) {
    const d = document.createElement("details");
    root.append(d);
    text(
      "summary",
      `完整转录候选上下文 · 单元 ${context.unit_ids.join("、")}`,
      d,
    );
    text("pre", context.text, d);
    text("p", "结构分组待审，未核验原卷。" + context.concerns.join("、"), d);
  }
  for (const e of current.style_snapshot) {
    const d = document.createElement("details");
    root.append(d);
    text(
      "summary",
      `转录风格/考点线索 · ${e.year || "年份待核"} · 单元${e.unit_id}`,
      d,
    );
    text("p", "transcription：未核验独立原卷，不属于高可信历史考频", d);
    text("p", e.relationship, d);
    text("p", `${e.file_id} · SHA ${e.source_sha256} · ${e.section}`, d);
    text("pre", e.text, d);
  }
  const analysis = current.ai_analysis;
  text("h3", "AI辅助分析与待核事项", root);
  for (const [k, v] of Object.entries(analysis.axes))
    text("p", `${checks[k]}：${v}`, root);
  text("pre", analysis.uncertainties.join("\n"), root, "warning");
  text("h3", "与其他候选题的重复线索", root);
  text("p", "以下仅为文字相似或共同考点提示，不代表已完成唯一性审核。", root);
  for (const related of current.related_candidates || []) {
    const b = text(
      "button",
      `${related.candidate_key} · 文字相似${Math.round(related.similarity * 100)}%${related.common_tags.length ? " · 共同考点：" + related.common_tags.join("、") : ""} · ${related.stem}`,
      root,
    );
    b.type = "button";
    b.onclick = () =>
      open(related.candidate_key).catch((e) => message(e.message));
  }
  text("h3", "人工逐项确认", root);
  const confirm = text("div", "", root, "checks");
  for (const k of session.checks) {
    const label = document.createElement("label"),
      input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.check = k;
    label.append(input, document.createTextNode(checks[k]));
    confirm.append(label);
  }
  const attest = document.createElement("label");
  attest.innerHTML = '<input type="checkbox" id="attest">';
  attest.append(
    document.createTextNode(
      "我本人已审阅此版本，提交的是人工判断，理解候选通过不等于正式发布。",
    ),
  );
  root.append(attest);
  const noteLabel = text("label", "审核依据与修改意见（至少20字）", root);
  const note = document.createElement("textarea");
  note.id = "note";
  note.maxLength = 5000;
  noteLabel.append(note);
  const status = text("p", "意见尚未暂存；暂存不会签署审核。", root);
  status.id = "note-status";
  status.setAttribute("role", "status");
  const conflicts = text("div", "", root, "warning");
  conflicts.id = "note-conflict";
  conflicts.hidden = true;
  const draftButton = text("button", "保存草稿（不签署）", root);
  draftButton.type = "button";
  draftButton.onclick = () => opinion.flush().catch((e) => message(e.message));
  const actions = text("div", "", root, "actions");
  for (const [action, label] of [
    ["approve_candidate", "通过候选审核"],
    ["return", "退回"],
    ["revise", "要求修改"],
  ]) {
    const button = text("button", label, actions);
    button.type = "button";
    button.onclick = () => save(action).catch((e) => message(e.message));
  }
  text("h3", "操作记录", root);
  current.decisions.forEach((d) =>
    text(
      "p",
      `${formatTime(d.created_at)} · ${d.reviewer} · ${labels[d.action]} · ${d.note}`,
      root,
    ),
  );
  const movement = text("div", "", root, "actions");
  for (const [direction, label] of [
    [-1, "上一题"],
    [1, "下一题"],
  ]) {
    const b = text("button", label, movement);
    b.type = "button";
    b.onclick = () => move(direction).catch((e) => message(e.message));
  }
  const reviewer = session.reviewer;
  opinion = new ReviewOpinion(
    reviewer,
    current,
    (path, options) => {
      if (session?.reviewer !== reviewer)
        throw new Error("审核账号已改变，请重新登录");
      return api(path, options);
    },
    root,
  );
  await opinion.start();
  await remember();
  list();
}
async function save(action) {
  if (current.superseded_by) throw new Error("请打开最新版进行审核");
  if (!$("attest").checked) throw new Error("需要审核人本人确认");
  const checked = Object.fromEntries(
    [...document.querySelectorAll("[data-check]")].map((e) => [
      e.dataset.check,
      e.checked,
    ]),
  );
  if (action === "approve_candidate" && !Object.values(checked).every(Boolean))
    throw new Error("通过前请逐项完成核对");
  if ($("note").value.trim().length < 20)
    throw new Error("请写明至少20字的审核依据或修改意见");
  const target = current,
    controller = opinion;
  signing = true;
  document
    .querySelectorAll("#detail input,#detail textarea")
    .forEach((e) => (e.disabled = true));
  document
    .querySelectorAll(".actions button")
    .forEach((b) => (b.disabled = true));
  try {
    await controller.flush();
    if (current !== target || !$("attest").checked)
      throw new Error("请在当前题目重新确认本人签署");
    await api(
      "/api/candidates/" +
        encodeURIComponent(target.candidate_key) +
        "/decision",
      {
        method: "POST",
        body: JSON.stringify({
          action,
          revision_sha256: target.revision_sha256,
          expected_last_decision_id: target.last_decision_id,
          expected_note_version: controller.version,
          checks: checked,
          note: $("note").value.trim(),
          human_attestation: true,
          request_id: crypto.randomUUID(),
        }),
      },
    );
    message("已保存人工记录；题目仍未发布。");
    controller.dirty = false;
    await load();
    await open(current.candidate_key);
  } finally {
    signing = false;
    document
      .querySelectorAll("#detail input,#detail textarea")
      .forEach((e) => (e.disabled = false));
    document
      .querySelectorAll(".actions button")
      .forEach((b) => (b.disabled = false));
  }
}
$("login").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const f = new FormData(e.target);
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(f)),
    });
    e.target.reset();
    await enter();
    message("已进入私有审核。");
  } catch (err) {
    message(err.message);
  }
};
$("logout").onclick = async () => {
  try {
    if (opinion && opinion.dirty) {
      await opinion.persist();
      await opinion.flush();
    }
    await api("/api/logout", { method: "POST" });
    session = null;
    opinion = null;
    location.reload();
  } catch (e) {
    message(e.message);
  }
};
$("subject").onchange = () => {
  list();
  remember().catch(() => {});
};
$("type").onchange = () => {
  list();
  remember().catch(() => {});
};
$("preview").onclick = async () => {
  try {
    if (signing) return;
    if (opinion?.dirty) {
      await opinion.persist();
      await opinion.flush().catch(() => {});
    }
    const data = await api("/api/preview");
    const root = $("detail");
    opinion = null;
    root.replaceChildren();
    text("h2", data.label, root);
    text(
      "p",
      "28题 · 150分 · 60分钟规格，仅预览，不启动训练或计时",
      root,
      "warning",
    );
    (data.review_warnings || []).forEach((w) => text("p", w, root, "warning"));
    data.items.forEach((r, i) => {
      const b = text("button", `${i + 1}. ${r.question_snapshot.stem}`, root);
      b.onclick = () => open(r.candidate_key).catch((e) => message(e.message));
    });
  } catch (e) {
    message(e.message);
  }
};
$("backups").onclick = async () => {
  try {
    backup = await api("/api/backup");
    $("backup-panel").hidden = false;
    $("backup-info").textContent =
      `${backup.filename} · ${backup.bytes}字节 · SHA-256 ${backup.sha256} · ${formatTime(backup.snapshot_at)}`;
    $("backup-panel").scrollIntoView();
  } catch (e) {
    message(e.message);
  }
};
$("verify-file").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file || !backup) return;
  if (file.size > 200 * 1024 * 1024) {
    $("verify-result").textContent = "文件过大，请使用桌面校验工具。";
    return;
  }
  $("verify-result").textContent = "正在校验…";
  try {
    const hash = await crypto.subtle.digest(
      "SHA-256",
      await file.arrayBuffer(),
    );
    const hex = [...new Uint8Array(hash)]
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    $("verify-result").textContent =
      hex === backup.sha256
        ? "SHA-256一致，文件交接完整。"
        : "校验不一致，请重新下载，勿恢复。";
  } catch (e) {
    $("verify-result").textContent = "此浏览器需安全HTTPS上下文进行校验。";
  }
};
enter().catch(() => message("请先登录审核账号。"));

async function remember() {
  if (!session) return;
  await api("/api/position", {
    method: "POST",
    keepalive: true,
    body: JSON.stringify({
      candidate_key: current?.candidate_key || null,
      subject_filter: $("subject").value,
      type_filter: $("type").value,
    }),
  });
}
async function refreshLocalKeys() {
  if (!session) return;
  localKeys = new Set(
    await ReviewVault.pending(session.reviewer).catch(() => []),
  );
  list();
}
async function move(direction) {
  const filtered = items.filter(
    (r) =>
      (!$("subject").value || r.subject === $("subject").value) &&
      (!$("type").value || r.type === $("type").value),
  );
  const at = filtered.findIndex(
    (r) => r.candidate_key === current?.candidate_key,
  );
  const next = filtered[at + direction];
  if (next) await open(next.candidate_key);
  else message("已到当前列表边界。");
}
window.addEventListener("review-opinion-buffer", () =>
  refreshLocalKeys().catch(() => {}),
);
window.addEventListener("online", () => opinion?.flush().catch(() => {}));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && opinion?.dirty) {
    opinion.persist().catch(() => {});
    opinion.flush().catch(() => {});
  }
});
window.addEventListener("beforeunload", (event) => {
  if (opinion?.dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});

$("refresh-backup").onclick = async () => {
  const button = $("refresh-backup");
  button.disabled = true;
  message("正在创建一致性快照，完成后可下载…");
  try {
    backup = await api("/api/backup/refresh", { method: "POST" });
    $("backup-info").textContent =
      `${backup.filename} · ${backup.bytes}字节 · SHA-256 ${backup.sha256} · ${formatTime(backup.snapshot_at)}`;
    message("最新备份已准备好，旧备份保留。");
  } catch (e) {
    message(e.message);
  } finally {
    button.disabled = false;
  }
};
