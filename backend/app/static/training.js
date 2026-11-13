"use strict";
const $ = (id) => document.getElementById(id);
const names = {
  single_choice: "单选",
  fill_blank: "填空",
  term: "名词解释",
  short_answer: "简答",
  essay: "问答",
};
let paper = null,
  version = 0,
  username = "",
  pending = {},
  localDrafts = {},
  queue = Promise.resolve(),
  deadline = null,
  anchor = 0,
  serverAnchor = 0,
  conflicted = false,
  compositionActive = false,
  renderedResultSignature = "",
  serverConfirmed = false,
  expiryNotified = false,
  startup = null;
const key = () => `quiz:v2:${username}:${paper.paper_id}`;
function emitUI() {
  window.TrainingUI?.state({
    paper,
    drafts: localDrafts,
    pendingCount: Object.keys(pending).length,
    conflicted,
    serverPending: !serverConfirmed,
    expired: !!deadline && estimatedNow() >= deadline,
    message: $("status").textContent,
  });
}
function status(t) {
  $("status").textContent = t;
  emitUI();
}
async function api(path, options = {}) {
  let r;
  try {
    r = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch (error) {
    serverConfirmed = false;
    throw error;
  }
  let value;
  try {
    value = await r.json();
  } catch {
    const error = new Error("服务端响应无法解析，请重新核验。");
    error.status = r.status;
    throw error;
  }
  if (!r.ok) {
    let reason = value.detail;
    if (typeof reason === "string" && reason.startsWith("{")) {
      try { reason = JSON.parse(reason); } catch { /* use original text */ }
    }
    if (reason && reason.code === "insufficient_reviewed_bank") {
      const missing = (reason.shortages || []).filter(x => x.missing > 0);
      const total = missing.reduce((n, x) => n + x.missing, 0);
      reason = `题库还差 ${total} 道，暂时凑不齐一张卷子：` +
        missing.map(x => (x.subject === "biochemistry" ? "生化" : "细胞") +
          (names[x.question_type] || x.question_type) +
          "缺 " + x.missing + " 道").join("；") +
        "。不会用旧题或未核验的题凑数，也不会出半套卷；新题补够后可以再准备。";
    }
    const e = new Error(typeof reason === "string" ? reason : JSON.stringify(reason));
    e.status = r.status;
    if ([401, 403].includes(r.status) && path !== "/auth/login") rejectAccess();
    if (r.status >= 500) serverConfirmed = false;
    throw e;
  }
  return value;
}
function persist() {
  try {
    sessionStorage.setItem("quiz-v2-paper-key", key());
    localStorage.setItem(
      key(),
      JSON.stringify({
        username,
        estimatedServerNow: estimatedNow(),
        pending,
        localDrafts,
        version,
        paper,
        offset: serverAnchor + (performance.now() - anchor) - Date.now(),
      }),
    );
  } catch {
    status("本机存储不可用，请保持联网并确认服务端保存成功。");
  }
}
function timing(t) {
  const previousDeadline = deadline;
  const previousNow = serverAnchor ? estimatedNow() : 0;
  version = t.version;
  deadline = t.deadline ? Date.parse(t.deadline) : null;
  serverAnchor = Math.max(
    Date.parse(t.server_now),
    deadline === previousDeadline ? previousNow : 0,
  );
  anchor = performance.now();
  if (t.status === "submitted") {
    showResult().catch((e) => status(e.message));
    return;
  }
  tick();
}
function tick() {
  if (!deadline) return;
  const remaining = Math.max(
    0,
    Math.ceil((deadline - serverAnchor - (performance.now() - anchor)) / 1000),
  );
  $("clock").textContent =
    `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  window.TrainingUI?.clock(remaining);
  if (remaining) expiryNotified = false;
  if (!remaining && !expiryNotified) {
    expiryNotified = true;
    $("submit").disabled = true;
    emitUI();
    refresh(true).catch(() =>
      status("时间已到。离线内容保留在本机，联网后将以服务端已存答案交卷。"),
    );
  }
}
setInterval(tick, 1000);
async function identity() {
  const me = await api("/auth/me");
  if (typeof me.username !== "string" || !me.username)
    throw new Error("用户身份响应无效。");
  if (username && username !== me.username) {
    rejectAccess("账号已变化，旧答卷已保留，不会混入当前账户。");
  }
  username = me.username;
  sessionStorage.setItem("quiz-v2-user", username);
  $("login").hidden = true;
  window.TrainingUI?.signedIn(username);
}
$("login").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: $("password").value }),
    });
    $("password").value = "";
    await bootstrap(true);
    window.dispatchEvent(new Event("signed-in")); // the page that asked for the sign-in loads itself now
    status("已登录"); // shown once, right after signing in; clears itself after 2 s, above the bottom nav
  } catch (e) {
    // 429 already carries the Chinese lock message from the server
    status(e.status === 401 ? "密码不正确，请重新输入。" : e.message);
  }
};
async function prepare(stay = false, kind = "objective") {
  if (startup) await startup;
  await identity();
  const saved = cachedPaper(username);
  // the cached paper is only reused for the same kind of paper: the objective and the subjective paper are separate papers
  if (saved && (saved.paper.kind || "mixed") === kind) {
    try {
      paper = await api(`/v2/papers/${saved.paper.paper_id}`);
      serverConfirmed = true;
      sessionStorage.removeItem("quiz-v2-auth-blocked");
      await restore(!stay);
    } catch (error) {
      if (!recoverable(error)) throw error;
      await restoreOffline(saved);
    }
    return;
  }
  const offered = await api("/v2/training-specification");
  // the isolated legacy test fixture answers with one old paper's specification instead of the two daily papers
  const specification = offered.papers ? offered.papers.find((p) => p.kind === kind) : offered;
  if (!specification) throw new Error("服务端没有这套卷子的规格。");
  paper = await api("/v2/papers", {
    method: "POST",
    body: JSON.stringify({
      specification: specification.specification,
      quotas: specification.quotas,
      ...(specification.points_by_type && offered.papers ? { points: specification.points_by_type, duration_seconds: specification.duration_seconds } : {}),
    }),
  });
  serverConfirmed = true;
  sessionStorage.removeItem("quiz-v2-auth-blocked");
  await restore(!stay);
}
// A prepared paper is metadata only. Never merge pre-start local answers or photos.
async function clearUnstartedCache() {
  if (paper?.timing.status !== "not_started") return;
  paper.questions = [];
  paper.timing.drafts = {};
  localDrafts = {};
  pending = {};
  conflicted = false;
  $("conflict").hidden = true;
  window.TrainingUI?.parkMarkButton();
  $("questions").replaceChildren();
  $("result").replaceChildren();
  try {
    localStorage.removeItem(key());
    sessionStorage.removeItem(`quiz-ui-question:${username}:${paper.paper_id}`);
  } catch {
    // Rendering and write guards still fail closed when storage is unavailable.
  }
  persist();
  await discardPaperPhotos();
}
async function discardPaperPhotos() {
  for (const job of await photoStore("getAll")) {
    if (job.user === username && job.paper === paper.paper_id)
      await photoStore("delete", job.key);
  }
}
async function restore(show = true) {
  const saved = cachedPaper(username, paper.paper_id);
  if (
    saved?.paper.timing.status === "in_progress" &&
    paper.timing.status === "not_started"
  ) {
    rejectAccess("服务端与本机开始状态不一致，草稿保留，请重新核验。", false);
    return;
  }
  if (
    saved?.paper.timing.status === "in_progress" &&
    paper.timing.status === "in_progress" &&
    (saved.paper.timing.started_at !== paper.timing.started_at ||
      saved.paper.timing.deadline !== paper.timing.deadline)
  ) {
    rejectAccess("服务端计时身份不匹配，草稿保留，请重新核验。", false);
    return;
  }
  await clearUnstartedCache();
  version = paper.timing.version;
  localDrafts = { ...paper.timing.drafts };
  pending = {};
  try {
    const stored = saved;
    if (stored?.paper?.timing.status === "not_started")
      await discardPaperPhotos();
    if (
      stored?.paper?.timing.status === "in_progress" &&
      stored.paper.timing.started_at === paper.timing.started_at &&
      stored.paper.timing.deadline === paper.timing.deadline &&
      paper.timing.status === "in_progress"
    ) {
      pending = stored.pending || {};
      localDrafts = { ...localDrafts, ...pending };
      if (Object.keys(pending).length && stored.version !== version) {
        if (
          Object.entries(pending).every(
            ([id, value]) =>
              JSON.stringify({
                text: value.text || "",
                blanks: value.blanks || [],
                image_ids: value.image_ids || [],
                flagged: !!value.flagged,
              }) ===
              JSON.stringify({
                text: paper.timing.drafts[id]?.text || "",
                blanks: paper.timing.drafts[id]?.blanks || [],
                image_ids: paper.timing.drafts[id]?.image_ids || [],
                flagged: !!paper.timing.drafts[id]?.flagged,
              }),
          )
        )
          pending = {};
        else showConflict();
      }
    }
  } catch {
    status("本机暂存读取失败，服务端答案仍可恢复。");
  }
  if (saved && paper.timing.status === "submitted") {
    pending = saved.pending;
    localDrafts = { ...localDrafts, ...pending };
  }
  if (
    saved?.paper.timing.status === "in_progress" &&
    paper.timing.status === "in_progress"
  ) {
    // A refresh or delayed response must never move the same exam's estimated clock backwards.
    serverAnchor = Math.max(
      serverAnchor ? estimatedNow() : 0,
      Date.now() + saved.offset,
      saved.estimatedServerNow || 0,
    );
    anchor = performance.now();
    deadline = Date.parse(paper.timing.deadline);
  }
  timing(paper.timing);
  render();
  if (show) window.TrainingUI?.showView("exam");
  $("start").hidden = paper.timing.status !== "not_started";
  $("submit").hidden = paper.timing.status !== "in_progress";
  if (!conflicted) {
    await flush();
    await retryPhotos().catch((e) => status(e.message));
  }
  persist();
}
// Make another paper (the other kind of today's papers, or an earlier one) the one on the exam page. What was typed into the
// paper that is open is saved first; its clock keeps running on the server, independent of this one.
async function openPaper(paperId, show = true) {
  if (startup) await startup;
  await identity();
  if (paper?.paper_id === paperId) {
    if (show) window.TrainingUI?.showView("exam");
    return;
  }
  if (paper && paper.timing?.status === "in_progress") await flush();
  paper = await api(`/v2/papers/${paperId}`);
  serverConfirmed = true;
  sessionStorage.removeItem("quiz-v2-auth-blocked");
  await restore(show);
}
window.TodayPaper = {
  prepare: (kind) => prepare(true, kind).catch((e) => status(e.message)),   // prepares today's paper of that kind and stays on the page
  open: (paperId, show) => openPaper(paperId, show).catch((e) => status(e.message)),
  start: () => $("start").click(),
  review: (items) => reviewSession(items),
};
$("start").onclick = async () => {
  try {
    $("start").disabled = true;
    if (!serverConfirmed) await reconnect();
    if (!serverConfirmed || !paper) throw new Error("请联网核验后再确认开始。");
    await clearUnstartedCache();
    const started = await api(`/v2/papers/${paper.paper_id}/start`, {
      method: "POST",
    });
    if (!["in_progress", "submitted"].includes(started.timing?.status))
      throw new Error("服务端尚未确认开始，请联网后重试。");
    paper = started;
    serverConfirmed = true;
    $("start-dialog")?.close();
    await restore();
    status(`计时已开始。答题自动保存；上传图片也计入${Math.round((paper.duration_seconds || 3600) / 60)}分钟。`);
  } catch (e) {
    status(e.message);
  } finally {
    $("start").disabled = false;
  }
};
function change(id, update) {
  if (!editable()) return;
  localDrafts[id] = {
    text: "",
    blanks: [],
    image_ids: [],
    ...localDrafts[id],
    ...update,
  };
  pending[id] = localDrafts[id];
  persist();
  renderProgress();
  if (!compositionActive)
    queue = queue.then(flush).catch((e) => status(e.message));
}
function showConflict() {
  conflicted = true;
  $("conflict").hidden = false;
  status("版本冲突：自动保存已暂停，避免覆盖另一台设备的较新答案。");
}
async function flush() {
  if (
    !paper ||
    conflicted ||
    compositionActive ||
    !serverConfirmed ||
    !editable()
  )
    return;
  for (const id of Object.keys(pending)) {
    const value = pending[id];
    try {
      const t = await api(`/v2/papers/${paper.paper_id}/drafts/${id}`, {
        method: "PUT",
        body: JSON.stringify({ expected_version: version, value }),
      });
      version = t.version;
      paper.timing = t;
      serverAnchor = Date.parse(t.server_now);
      anchor = performance.now();
      if (!t.accepted) {
        paper.timing = t;
        status("已截止，迟到的内容未计入答卷。");
        await showResult();
        return;
      }
      if (pending[id] === value) delete pending[id];
      persist();
      status("已保存");
    } catch (e) {
      if (e.status === 409) {
        showConflict();
        return;
      }
      status("暂时离线：文字答案已保存在本机，联网后继续同步。");
      return;
    }
  }
}
$("remote").onclick = async () => {
  pending = {};
  conflicted = false;
  $("conflict").hidden = true;
  paper = await api(`/v2/papers/${paper.paper_id}`);
  localStorage.removeItem(key());
  await restore();
};
$("local").onclick = async () => {
  paper = await api(`/v2/papers/${paper.paper_id}`);
  version = paper.timing.version;
  conflicted = false;
  $("conflict").hidden = true;
  persist();
  await flush();
};

async function refresh(force = false) {
  if (
    !paper ||
    compositionActive ||
    (!force &&
      document.activeElement?.matches(
        "#questions input, #questions textarea, #result input, #result textarea, #result select",
      ))
  )
    return;
  const fresh = await api(`/v2/papers/${paper.paper_id}`);
  if (fresh.paper_id !== paper.paper_id) throw new Error("试卷身份不匹配。");
  serverConfirmed = true;
  sessionStorage.removeItem("quiz-v2-auth-blocked");
  if (fresh.timing.status !== "in_progress") {
    paper = fresh;
    await restore(false);
    return;
  }
  if (
    fresh.timing.started_at !== paper.timing.started_at ||
    fresh.timing.deadline !== paper.timing.deadline
  ) {
    rejectAccess("服务端计时身份已变化，草稿保留，请重新核验。");
    return;
  }
  if (fresh.timing.version !== version && Object.keys(pending).length) {
    const normalized = (v) =>
      JSON.stringify({
        text: v?.text || "",
        blanks: v?.blanks || [],
        image_ids: v?.image_ids || [],
        flagged: !!v?.flagged,
      });
    if (
      Object.entries(pending).every(
        ([id, v]) => normalized(v) === normalized(fresh.timing.drafts[id]),
      )
    ) {
      pending = {};
      conflicted = false;
      $("conflict").hidden = true;
    } else {
      showConflict();
      return;
    }
  }
  if (fresh.timing.version < version) return;
  const changed =
    fresh.timing.version !== version ||
    fresh.timing.status !== paper.timing.status;
  paper = fresh;
  await clearUnstartedCache();
  if (changed && !Object.keys(pending).length) {
    localDrafts = { ...fresh.timing.drafts };
    render();
  }
  timing(fresh.timing);
  emitUI();
}
window.addEventListener("pageshow", () => refresh().catch(() => {}));
function element(tag, text, parent) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (parent) parent.append(el);
  return el;
}
function renderProgress() {
  const reported = new Set(paper?.reported_question_ids || []);
  const done = Object.entries(localDrafts).filter(
    ([id, v]) =>
      !reported.has(Number(id)) &&
      (v.text?.trim() || v.blanks?.some((x) => x.trim()) || v.image_ids?.length),
  ).length;
  const total = (paper?.count || 60) - reported.size;
  $("progress").textContent =
    `已答 ${done} / ${total} · 剩余 ${total - done}` +
    (reported.size ? ` · 已反馈${reported.size}题不计分` : "") +
    (paper?.includes_reused ? " · 含复用题" : "") + (paper?.relaxed_rules?.length ? " · 组卷规则已放宽" : "");
  emitUI();
}
function render() {
  window.TrainingUI?.parkMarkButton();
  $("questions").replaceChildren();
  if (paper?.timing.status !== "in_progress") {
    renderProgress();
    return;
  }
  for (const q of paper.questions) {
    const article = element("article", undefined, $("questions"));
    article.dataset.id = q.id;
    article.className = "question-card";
    const value = localDrafts[q.id] || {};
    if (value.flagged) article.classList.add("flagged");
    element(
      "h2",
      `第${q.position}题${q.is_retest ? " · FSRS复测" : q.review_knowledge_tags?.length ? " · 知识点复测变式" : ""}${q.reused ? " · 复用题" : ""}`,
      article,
    ).className = "question-no";
    const stem = element("p", q.stem, article);
    stem.className = "question-stem";
    stem.tabIndex = -1;
    if (q.question_type === "single_choice") {
      const choices = element("div", undefined, article);
      choices.setAttribute("role", "radiogroup");
      choices.setAttribute("aria-label", `第${q.position}题单选选项`);
      for (const [option, label] of Object.entries(q.options)) {
        const b = element("button", undefined, choices);
        element("span", `${option}.`, b).className = "option-letter";
        element("span", label, b);
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(value.text === option));
        b.className = "option" + (value.text === option ? " chosen" : "");
        b.onclick = () => {
          article.querySelectorAll(".option").forEach((x) => {
            x.classList.remove("chosen");
            x.setAttribute("aria-checked", "false");
          });
          b.classList.add("chosen");
          b.setAttribute("aria-checked", "true");
          change(q.id, { text: option });
        };
      }
    } else if (q.question_type === "fill_blank" && q.blank_count) {
      for (let i = 0; i < q.blank_count; i++) {
        const label = element("label", `${"①②③"[i]} 第${i + 1}空`, article);
        const input = element("input", undefined, label);
        input.value = value.blanks?.[i] || "";
        input.setAttribute("aria-label", `第${q.position}题第${i + 1}空`);
        input.oninput = () => {
          const answers = [
            ...(localDrafts[q.id]?.blanks || Array(q.blank_count).fill("")),
          ];
          answers[i] = input.value;
          change(q.id, { blanks: answers });
        };
      }
    } else {
      const input = element(
        q.question_type === "fill_blank" ? "input" : "textarea",
        undefined,
        article,
      );
      input.value = value.text || "";
      input.dataset.kind = q.question_type; // textarea height grows with the answer's size
      input.setAttribute("aria-label", `第${q.position}题答案`);
      input.oninput = () => change(q.id, { text: input.value });
      if (q.question_type !== "fill_blank" && paper.photo_answers_enabled) {
        for (const capture of [true, false]) {
          const label = element(
            "label",
            capture ? "拍照上传" : "从相册选图",
            article,
          );
          const file = element("input", undefined, label);
          file.type = "file";
          file.accept = "image/jpeg,image/png,image/webp";
          if (capture) file.setAttribute("capture", "environment");
          file.onchange = () =>
            upload(q.id, file.files[0], article).catch((e) =>
              status(e.message),
            );
        }
        for (const id of value.image_ids || []) {
          const image = element("img", undefined, article);
          image.src = `/v2/images/${id}`;
          image.alt = "已上传的答题照片";
        }
      }
    }
    const flag = element(
      "button",
      value.flagged ? "取消标记" : "标记此题",
      article,
    );
    flag.className = "question-flag";
    flag.onclick = () => {
      const flagged = !localDrafts[q.id]?.flagged;
      change(q.id, { flagged });
      article.classList.toggle("flagged", flagged);
      flag.textContent = flagged ? "取消标记" : "标记此题";
    };
    const isReported = (paper.reported_question_ids || []).includes(q.id);
    const report = element(
      "button",
      isReported ? "已反馈 · 本卷不计分" : "这题有问题",
      article,
    );
    report.type = "button";
    report.className = "question-report";
    report.disabled = isReported;
    if (isReported) article.classList.add("reported");
    report.onclick = () => reportQuestion(q.id, report, article);
  }
  renderProgress();
}
const REPORT_CONFIRM =
  "反馈后，这道题以后不会再出现，本卷也不计这道题的分。确定反馈吗？";
async function reportQuestion(questionId, button, article) {
  if (!confirm(REPORT_CONFIRM)) return;
  try {
    await api(`/v2/papers/${paper.paper_id}/questions/${questionId}/report`, {
      method: "POST",
      body: JSON.stringify({ note: "" }),
    });
  } catch (error) {
    status(error.message);
    return;
  }
  paper.reported_question_ids = [
    ...new Set([...(paper.reported_question_ids || []), questionId]),
  ];
  persist();
  button.textContent = "已反馈 · 本卷不计分";
  button.disabled = true;
  article?.classList.add("reported");
  renderProgress();
  status("已记录：这道题以后不再出现，本卷不计分。");
}
async function sendImage(id, file, article, jobKey) {
  if (!editable()) throw new Error("尚未开始或已截止，不能上传图片。");
  if (!serverConfirmed)
    throw new Error("离线暂存，服务端状态待确认，请联网核验后同步图片。");
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) throw new Error("图片不能超过8MiB");
  if (!navigator.onLine)
    throw new Error("照片已在本机暂存，联网后在截止前重试；计时继续。");
  await flush();
  if (conflicted || Object.keys(pending).length)
    throw new Error("请先完成文字同步或处理冲突，再上传图片");
  status("上传中，计时继续");
  const r = await fetch(
    `/v2/papers/${paper.paper_id}/images/${id}?expected_version=${version}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": file.type },
      body: file,
    },
  );
  const t = await r.json();
  if (!r.ok) {
    if ([401, 403].includes(r.status)) rejectAccess();
    if (r.status >= 500) serverConfirmed = false;
    const error = new Error(t.detail || "上传失败");
    error.status = r.status;
    throw error;
  }
  paper.timing = t;
  timing(t);
  if (!t.accepted) throw new Error("已截止，图片未计入答卷");
  localDrafts[id] = t.drafts[id];
  persist();
  const img = element("img", undefined, article);
  img.src = `/v2/images/${t.image_id}`;
  img.alt = "已上传答题照片";
  await photoStore("delete", jobKey);
  status("图片已保存；手写内容在确认后批改。");
}
let submitting = false;
// While the paper is on its way the answers and the button are frozen ("提交中"); a failure gives them back and says why.
function freezeForSubmit(on) {
  const button = $("submit");
  const questions = $("questions");
  if (on) {
    button.dataset.label = button.textContent;
    button.textContent = "提交中…";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    questions.dataset.wasInert = questions.inert ? "1" : "";
    questions.inert = true;
    document.body.classList.add("is-submitting");
    status("提交中，请稍候……");
  } else {
    button.textContent = button.dataset.label || "交卷";
    button.disabled = false;
    button.removeAttribute("aria-busy");
    questions.inert = questions.dataset.wasInert === "1";
    document.body.classList.remove("is-submitting");
  }
}
$("submit").onclick = async () => {
  if (submitting) return;
  const approved = window.TrainingUI
    ? await window.TrainingUI.confirmSubmit()
    : confirm("确认统一交卷？未答题按0分，已答主观题由AI判分。");
  if (!approved || submitting) return;
  submitting = true;
  freezeForSubmit(true);
  let done = false;
  try {
    if (!serverConfirmed) await reconnect();
    if (!serverConfirmed || !paper)
      throw new Error("请联网核验后交卷，本机草稿仍保留。");
    await flush();
    if (conflicted || Object.keys(pending).length)
      throw new Error("有未同步答案，请先联网完成保存或处理冲突");
    await api(`/v2/papers/${paper.paper_id}/submit`, {
      method: "POST",
      body: JSON.stringify({ expected_version: version, answers: {} }),
    });
    done = true; // accepted: the paper is submitted and cannot be edited any more
    // a short tick (about 450 ms) plays over the page while the result loads underneath, then the result is there
    await Promise.all([window.Motion?.done?.() ?? Promise.resolve(), showResult()]);
  } catch (e) {
    status(done ? `已交卷，但成绩暂时读不出来：${e.message}` : `交卷失败：${e.message}。答案已保留，可以再次点交卷（重复提交不会重复计分）。`);
  } finally {
    submitting = false;
    freezeForSubmit(false); // after a success the answers are gone from the page (the result replaces them) and the button is hidden
  }
};
async function showResult() {
  if (!paper) return;
  deadline = null;
  paper.timing.status = "submitted";
  persist();
  $("start").hidden = true;
  $("submit").hidden = true;
  const result = await api(`/v2/papers/${paper.paper_id}/result`);
  const signature = JSON.stringify(result);
  window.TrainingUI?.parkMarkButton();
  $("questions").replaceChildren();
  if (signature === renderedResultSignature && $("result").childElementCount)
    return;
  renderedResultSignature = signature;
  $("result").replaceChildren();
  const excludedCount = result.excluded?.question_ids?.length || 0;
  if (result.relaxed_rules?.length) element("p", relaxedText(result.relaxed_rules), $("result")).className = "notice";
  if (result.includes_reused) element("p", "本卷含复用题：新题不足时，用14天前出现过的已审核旧题补齐。", $("result")).className = "notice";
  element(
    "h2",
    (result.score === null
      ? (result.objective_max ? `客观题 ${result.objective_points} / ${result.objective_max} 分 · 主观题判分中` : `判分中 · 已判 ${result.graded_points} 分，${result.pending} 题待判`)
      : `成绩 ${result.score} / ${result.max_points}`) +
      (excludedCount
        ? `（原满分 ${result.nominal_max_points}，已扣除 ${excludedCount} 道反馈题共 ${result.excluded.points} 分）`
        : ""),
    $("result"),
  );
  if (result.grading_late?.count) {
    const late = element("p", `有 ${result.grading_late.count} 题交卷后超过 ${result.grading_late.minutes} 分钟仍未判完，可点题目旁的「重新判分」。`, $("result"));
    late.className = "notice late-grading";
    late.setAttribute("role", "status");
  }
  let number = 0;
  for (const answer of result.answers) {
    number += 1;
    const article = element("article", undefined, $("result"));
    article.className = "result-answer" + (answer.excluded ? " reported" : "");
    article.dataset.qid = answer.question_id;
    element("p", answer.stem, article);
    if (answer.question_type === "single_choice" && answer.options) {
      for (const [letter, label] of Object.entries(answer.options).sort(([a], [b]) => a.localeCompare(b)))
        element("p", letter + ". " + label, article);
    }
    try {
      for (const id of JSON.parse(answer.answer).image_ids || []) {
        const image = element("img", undefined, article);
        image.src = `/v2/images/${id}`;
        image.alt = "已提交的原始手写答卷";
      }
    } catch {}
    element(
      "h3",
      `第 ${number} 题 · ${answer.excluded ? "已反馈 · 不计分" : answer.points === null ? "待判分" : answer.points + " / " + answer.max_points + " 分"}`,
      article,
    );
    if (!answer.excluded && answer.points !== null) {
      // any scored answer can be changed by hand; a manual score says so (and the AI's original stays viewable in the sheet)
      if (answer.graded_by === "手动判分" || answer.graded_by === "已手动调整") element("span", answer.graded_by, article).className = "grader-tag";
      const change = element("button", "修改分数", article);
      change.type = "button";
      change.className = "secondary manual-one";
      change.onclick = () => window.Manual?.open({ paperId: result.paper_id, questionId: answer.question_id, onDone: () => showResult().catch((e) => status(e.message)) });
    }
    if (!answer.excluded && answer.points === null) {
      const waiting = element("p", "判分中……", article);
      waiting.className = "grading-status";
      waiting.dataset.qid = answer.question_id;
      waiting.setAttribute("role", "status");
      const byHand = element("button", "手动判分", article);
      byHand.type = "button";
      byHand.className = "secondary manual-one";
      byHand.onclick = () => window.Manual?.open({ paperId: result.paper_id, questionId: answer.question_id, onDone: () => showResult().catch((e) => status(e.message)) });
      const again = element("button", "重新判分", article);
      again.type = "button";
      again.className = "secondary regrade-one";
      again.onclick = async () => {
        again.disabled = true;
        try {
          await api(`/v2/papers/${result.paper_id}/regrade/${answer.question_id}`, { method: "POST" });
          waiting.textContent = "已重新排队，等待 AI 判分";
          status("已重新排队判分。");
        } catch (error) {
          status(error.message);
          again.disabled = false;
        }
      };
    }
    let response;
    try {
      response = JSON.parse(answer.answer);
    } catch {
      response = { text: answer.answer };
    }
    const ownAnswer = response.text ||
      (response.blanks || []).join("；") ||
      (response.image_ids?.length ? "手写图片" : "未作答");
    const ownChoice = answer.question_type === "single_choice"
      ? answer.options?.[ownAnswer.toUpperCase()]
      : null;
    element("p", "你的作答：" + ownAnswer +
      (ownChoice ? ". " + ownChoice : ""), article);
    element("p", "参考答案：" +
      (answer.solution_display || answer.solution), article);
    const scored = answer.feedback?.criteria;
    if (scored?.length) {
      scored.forEach((c, i) => {
        const line = element(
          "p",
          `评分点 ${i + 1}（${c.description}）：${c.points} / ${c.max_points} 分`,
          article,
        );
        line.className = c.points < c.max_points ? "criterion lost" : "criterion";
        const why = answer.feedback.criterion_reasons?.[i];
        if (why) element("small", "理由：" + why, article).className = "criterion-reason";
      });
    } else if (!["single_choice", "fill_blank"].includes(answer.question_type)) {
      for (const criterion of answer.rubric)
        element(
          "p",
          `评分点（${criterion.points}分）：${criterion.description}`,
          article,
        );
    }
    if (!answer.excluded) {
      const report = element("button", "这题有问题", article);
      report.type = "button";
      report.className = "question-report";
      report.onclick = async () => {
        await reportQuestion(answer.question_id, report, article);
        renderedResultSignature = "";
        showResult().catch((e) => status(e.message));
      };
    }
    if (answer.feedback) {
      element(
        "p",
        answer.feedback.comment ||
          "逐空得分：" + (answer.feedback.blank_points || []).join(" / "),
        article,
      );
      for (const [field, label] of [
        ["concept_errors", "概念错误"],
        ["imprecisions", "表述问题"],
        ["suggestions", "改进建议"],
      ])
        for (const item of answer.feedback[field] || [])
          element("p", label + "：" + item, article);
    }
    if (answer.points !== null && answer.points < answer.max_points) {
      const reflect = element("details", undefined, article);
      reflect.className = "reflection";
      element("summary", "写复盘（可选）", reflect);
      const label = element("label", "复盘原因", reflect);
      const choice = element("select", undefined, label);
      for (const [category, text] of Object.entries({
        concept_unclear: "概念不清",
        memory_omission: "记忆遗漏",
        misread_question: "审题错误",
        mechanism_error: "机制理解错误",
        incomplete_expression: "表述不完整",
        calculation_error: "计算错误",
        weak_chapter_links: "章节联系不足",
      })) {
        const option = element("option", text, choice);
        option.value = category;
      }
      const note = element("input", undefined, reflect);
      note.placeholder = "补充你的复盘说明";
      note.maxLength = 2000;
      const record = element("button", "保存复盘", reflect);
      record.onclick = async () => {
        try {
          await api(
            `/v2/papers/${paper.paper_id}/answers/${answer.question_id}/reflection`,
            {
              method: "POST",
              body: JSON.stringify({
                event_key: crypto.randomUUID(),
                category: choice.value,
                note: note.value || choice.selectedOptions[0].textContent,
              }),
            },
          );
          record.disabled = true;
          record.textContent = "复盘已保存";
        } catch (e) {
          status(e.message);
        }
      };
    }
    if (answer.evidence?.length) {
      const sources = element("details", undefined, article);
      sources.className = "evidence";
      element("summary", "教材依据：" + [...new Set(answer.evidence.map((e) => e.chapter))].join("、"), sources);
      for (const evidence of answer.evidence) element("blockquote", evidence.excerpt, sources);
    }
  }
  const wholePaper = element("button", "查看整卷", $("result"));
  wholePaper.type = "button";
  wholePaper.className = "secondary full-width";
  wholePaper.id = "view-whole-paper";
  wholePaper.onclick = () => { location.hash = "#/review/p" + paper.paper_id; };
  window.TrainingUI?.result(result);
  startGradingActivity(paper.paper_id);
  status("已交卷。选择题、填空题已出分；名词解释和简答由AI判分，判完自动刷新，判不了的显示原因，不会直接记0分。");
}

async function showVerificationStatus(paperId) {
  const old = $("verification-status");
  if (old) old.remove();
  const root = element("section", undefined, $("result"));
  root.id = "verification-status";
  root.className = "card verification-status";
  element("h2", "本卷教材与答案核验状态", root);
  const summary = element("p", "正在读取真实核验记录……", root);
  const detail = element("details", undefined, root);
  element("summary", "逐题核验记录", detail);
  try {
    const result = await api("/v2/papers/" + paperId + "/verification");
    if (!root.isConnected || paper?.paper_id !== paperId) return;
    const excerpt = result.items.filter(q => q.ocr_quote_matches_stored_text).length;
    const ai = result.items.filter(q => q.independent_ai_review).length;
    const audits = result.items.filter(q => q.review_audit_record_matches_current_question).length;
    const bibliography = result.items.filter(q => q.book_metadata_pending).length;
    summary.textContent = "OCR摘录匹配 " + excerpt + "/" + result.count +
      "；独立AI双轮复核 " + ai + "/" + result.count +
      "；审题记录匹配 " + audits + "/" + result.count +
      "；书目信息待核查 " + bibliography + "题。";
    element("p", "这些记录不代表已对照纸质原书逐页人工审核，也不代表真题官方答案核实。", root);
    for (const entry of result.items) {
      element("p", "第" + entry.position + "题： " +
        (entry.ocr_quote_matches_stored_text ? "OCR摘录字段匹配" : "OCR摘录匹配未确认") + " · " +
        (entry.independent_ai_review ? "独立AI证据复核通过" :
          entry.review_audit_record_matches_current_question ? "存在匹配的审题记录" : "科学复核状态未确认") +
        (entry.book_metadata_pending ? " · 书目信息待核查" : ""), detail);
    }
  } catch (error) {
    summary.textContent = "暂时无法核验本卷出处：" + error.message;
  }
}

let gradingActivityTimer = null;
function startGradingActivity(paperId) {
  if (gradingActivityTimer) clearInterval(gradingActivityTimer);
  gradingActivityTimer = null;
  const existing = $("grading-activity");
  if (existing) existing.remove();
  const section = element("section", undefined, $("result"));
  section.id = "grading-activity";
  section.className = "card grading-activity";
  section.setAttribute("aria-label", "本卷实时批改活动");
  element("h2", "判分进度", section);
  const hint = element("p", "正在读取服务端批改状态……", section);
  hint.setAttribute("role", "status");
  const events = element("div", undefined, section);
  const refreshScores = element("button", "刷新成绩与解析", section);
  refreshScores.type = "button";
  refreshScores.onclick = () => showResult().catch((e) => status(e.message));
  const regradeAll = element("button", "整卷重新判分", section);
  regradeAll.type = "button";
  regradeAll.className = "secondary";
  regradeAll.hidden = true;
  regradeAll.onclick = async () => {
    regradeAll.disabled = true;
    try {
      await api(`/v2/papers/${paperId}/regrade`, { method: "POST" });
      status("未判完的题已重新排队。");
      poll();
    } catch (error) {
      status(error.message);
    } finally {
      regradeAll.disabled = false;
    }
  };
  const lateNote = element("p", undefined, section);
  lateNote.className = "notice";
  lateNote.hidden = true;
  let busy = false;
  let priorGraded = null;
  const labels = {
    pending: "等待处理", retrying: "等待重试", running: "正在判分",
    succeeded: "AI结果已返回，等待写入", blocked: "暂时无法判分",
    cancelled: "已取消", pending_automatic_queue: "尚未进入判分队列",
    graded: "已判分", excluded: "已反馈 · 不计分"
  };
  const reasons = {
    waiting_to_be_queued: "等待进入判分队列",
    queued_for_ai_grading: "排队等待AI判分",
    ai_grading_in_progress: "AI判分中",
    ai_result_being_applied: "判分结果写入中",
    reassessing: "结果不确定，正在自动复判一次",
    uncertain_after_second_assessment: "复判后仍无法确定，暂不计分（不会记0分）",
    ai_budget_exhausted: "本月 AI 总上限已用完，下月恢复后继续判分",
    ai_grading_stopped: "AI判分已暂停",
    answer_too_long_for_automatic_grading: "作答过长，无法自动判分",
    photo_answers_not_supported: "照片作答不支持自动判分",
    ai_grading_failed: "判分失败，会自动重试",
    submit_auto_grading_disabled: "自动判分尚未开启",
    ai_worker_disabled: "自动判分服务未启动",
    owner_ai_settings_stopped: "AI判分已暂停",
    private_data_authorization_required: "尚未授权发送教材段落",
    provider_authorization_required: "尚未授权调用AI",
  };
  const reasonText = (row) =>
    (row.pending_text || reasons[row.pending_reason] || labels[row.state] || "待判分") +
    (row.pending_detail ? "：" + row.pending_detail : "");
  async function poll() {
    if (busy || paper?.paper_id !== paperId || document.visibilityState !== "visible") return;
    busy = true;
    try {
      const data = await api("/v2/papers/" + paperId + "/grading-activity");
      if (!section.isConnected) {
        if (gradingActivityTimer) clearInterval(gradingActivityTimer);
        gradingActivityTimer = null;
        return;
      }
      hint.textContent = data.pending
        ? "已判分 " + data.graded + "/" + data.total + " 题，待判分 " + data.pending + " 题。" +
          (data.auto_queue_available ? "" : "自动判分尚未启用或授权不足；不会假装已发送AI任务。")
        : "全部 " + data.total + " 题已判分。";
      refreshScores.hidden = !data.pending;
      regradeAll.hidden = !data.pending;
      lateNote.hidden = !data.late?.count;
      if (data.late?.count) lateNote.textContent = `有 ${data.late.count} 题交卷后超过 ${data.late.minutes} 分钟仍未判完。可点「整卷重新判分」重新排队。`;
      events.replaceChildren();
      for (const row of data.questions) {
        const mark = document.querySelector(
          `.grading-status[data-qid="${row.question_id}"]`,
        );
        if (mark && row.points === null && !row.excluded) mark.textContent = reasonText(row);
      }
      for (const row of data.questions.filter((q) =>
        q.points === null && !q.excluded
      ).slice(0, 60)) {
        element("p", "第" + row.position + "题 · " + reasonText(row), events);
      }
      const gained = priorGraded !== null && data.graded > priorGraded;
      priorGraded = data.graded;
      if (gained) {
        // Read the actual revised result; do not invent a full score while
        // individual grading jobs remain pending.
        showResult().catch((error) => status(error.message));
        return;
      }
      if (data.complete && gradingActivityTimer) {
        clearInterval(gradingActivityTimer);
        gradingActivityTimer = null;
      }
    } catch (error) {
      hint.textContent = "批改状态暂时无法读取：" + error.message;
    } finally {
      busy = false;
    }
  }
  poll();
  gradingActivityTimer = setInterval(poll, 5000);
}
// Identity confirmation, paper reachability and local recovery are separate checks.
const recoverable = (error) =>
  error instanceof TypeError ||
  error?.name === "AbortError" ||
  error?.status >= 500;
const estimatedNow = () => serverAnchor + (performance.now() - anchor);
const editable = () =>
  paper?.timing.status === "in_progress" &&
  deadline !== null &&
  estimatedNow() < deadline;
function rejectAccess(
  message = "登录状态失效，请重新登录。本机草稿仍然保留。",
  saveCurrent = true,
) {
  if (paper && saveCurrent) persist();
  sessionStorage.setItem("quiz-v2-auth-blocked", "1");
  serverConfirmed = false;
  paper = null;
  pending = {};
  localDrafts = {};
  deadline = null;
  window.TrainingUI?.parkMarkButton();
  $("questions").replaceChildren();
  $("result").replaceChildren();
  $("login").hidden = false;
  $("start").hidden = true;
  $("submit").hidden = true;
  status(message);
}
// Any page's data layer saw a 401: the one sign-in form of the app opens, the page stays where it is.
window.addEventListener("auth-required", () => {
  if ($("login").hidden) rejectAccess("登录状态失效，请重新登录。", false);
});
function cachedPaper(user, paperId = null) {
  try {
    const pointer = paperId
      ? `quiz:v2:${user}:${paperId}`
      : sessionStorage.getItem("quiz-v2-paper-key");
    if (!pointer) return null;
    const saved = JSON.parse(localStorage.getItem(pointer) || "null");
    if (!saved) return null;
    const p = saved.paper,
      t = p?.timing;
    if (
      !user ||
      pointer !== `quiz:v2:${user}:${p?.paper_id}` ||
      (saved.username !== undefined && saved.username !== user)
    )
      throw new Error("本机答卷属于其他用户，已保留且不会载入当前账户。");
    const object = (v) => !!v && typeof v === "object" && !Array.isArray(v);
    if (
      !Number.isSafeInteger(p.paper_id) ||
      p.paper_id <= 0 ||
      ![14, 28, 60].includes(p.count) ||
      ![150, 300].includes(p.max_points) ||
      !Number.isSafeInteger(p.duration_seconds) ||
      p.duration_seconds < 60 ||
      p.duration_seconds > 14400 ||
      !["not_started", "in_progress", "submitted"].includes(t?.status) ||
      !Number.isSafeInteger(saved.version) ||
      saved.version < 0 ||
      !object(saved.localDrafts) ||
      !object(saved.pending) ||
      (saved.estimatedServerNow !== undefined &&
        !Number.isFinite(saved.estimatedServerNow))
    )
      throw new Error("本机缓存结构无效，未恢复可编辑答卷，请联网核验。");
    if (t.status === "in_progress") {
      const start = Date.parse(t.started_at),
        end = Date.parse(t.deadline);
      if (
        !Number.isFinite(start) ||
        end - start !== 3600000 ||
        !Number.isFinite(Date.parse(t.server_now)) ||
        !Number.isFinite(saved.offset) ||
        !Array.isArray(p.questions) ||
        p.questions.length !== p.count
      )
        throw new Error("本机缓存计时信息无效，请联网核验。");
      const ids = new Set();
      for (const q of p.questions) {
        if (
          !Number.isSafeInteger(q.id) ||
          ids.has(q.id) ||
          typeof q.stem !== "string" ||
          !names[q.question_type] ||
          !["biochemistry", "cell_biology"].includes(q.subject) ||
          !Number.isSafeInteger(q.position) ||
          q.position < 1 ||
          q.position > p.count ||
          !Number.isSafeInteger(q.max_points) ||
          q.max_points <= 0 ||
          !object(q.options)
        )
          throw new Error("本机缓存题目无效，请联网核验。");
        ids.add(q.id);
      }
      for (const entries of [saved.localDrafts, saved.pending]) {
        for (const [id, value] of Object.entries(entries)) {
          if (
            !ids.has(Number(id)) ||
            !object(value) ||
            (value.text !== undefined &&
              (typeof value.text !== "string" || value.text.length > 20000)) ||
            (value.blanks !== undefined &&
              (!Array.isArray(value.blanks) ||
                value.blanks.length > 3 ||
                value.blanks.some(
                  (x) => typeof x !== "string" || x.length > 2000,
                ))) ||
            (value.image_ids !== undefined &&
              (!Array.isArray(value.image_ids) ||
                value.image_ids.length > 5 ||
                value.image_ids.some(
                  (x) => !Number.isSafeInteger(x) || x <= 0,
                ))) ||
            (value.flagged !== undefined && typeof value.flagged !== "boolean")
          )
            throw new Error("本机缓存答案无效，请联网核验。");
        }
      }
    }
    return saved;
  } catch (error) {
    status(
      error instanceof SyntaxError
        ? "本机缓存无法解析，原数据保留，请联网核验。"
        : error.message,
    );
    return null;
  }
}
async function restoreOffline(saved) {
  if (sessionStorage.getItem("quiz-v2-auth-blocked")) return;
  paper = saved.paper;
  serverConfirmed = false;
  version = saved.version;
  localDrafts = saved.localDrafts;
  pending = saved.pending;
  conflicted = false;
  $("conflict").hidden = true;
  serverAnchor = Math.max(
    Date.now() + saved.offset,
    saved.estimatedServerNow || Date.parse(paper.timing.server_now),
  );
  anchor = performance.now();
  deadline =
    paper.timing.status === "in_progress"
      ? Date.parse(paper.timing.deadline)
      : null;
  await clearUnstartedCache();
  window.TrainingUI?.cachedIdentity(username);
  render();
  window.TrainingUI?.showView("exam");
  $("login").hidden = true;
  $("start").hidden = paper.timing.status !== "not_started";
  $("submit").hidden = paper.timing.status !== "in_progress";
  status(
    paper.timing.status === "not_started"
      ? "尚未开始：请联网核验后确认开始，计时前内容不可作答。"
      : paper.timing.status === "submitted"
        ? "试卷已提交，请联网读回成绩；本机暂存仍保留，不可继续作答。"
        : "离线暂存、服务端状态待确认。剩余时间为本地估算，联网核验后才能同步。",
  );
  tick();
}
async function bootstrap(loggedIn = false) {
  if (loggedIn) sessionStorage.removeItem("quiz-v2-auth-blocked");
  let identityKnown = false;
  try {
    await identity();
    identityKnown = true;
  } catch (error) {
    if (!recoverable(error)) {
      status(error.message);
      return;
    }
    username = sessionStorage.getItem("quiz-v2-user") || "";
  }
  const saved = cachedPaper(username);
  if (!saved) {
    // No "logged in" toast on page switches or refreshes; the login form itself says it when needed.
    if (!identityKnown && !sessionStorage.getItem("quiz-v2-paper-key")) status("请先登录。");
    return;
  }
  if (identityKnown) {
    try {
      const fresh = await api(`/v2/papers/${saved.paper.paper_id}`);
      if (fresh.paper_id !== saved.paper.paper_id)
        throw new Error("试卷身份不匹配，请重新核验。");
      paper = fresh;
      serverConfirmed = true;
      sessionStorage.removeItem("quiz-v2-auth-blocked");
      await restore(false);
      if (paper && !conflicted)
        status(
          paper.timing.status === "in_progress"
            ? "已核验并恢复答卷，原截止时间保持不变。"
            : paper.timing.status === "not_started"
              ? "试卷尚未开始，请确认后再计时。"
              : "试卷已提交，正在读回成绩。",
        );
      return;
    } catch (error) {
      if (!recoverable(error)) {
        status(error.message);
        return;
      }
    }
  }
  await restoreOffline(saved);
}
let reconnecting = null;
function reconnect() {
  if (reconnecting) return reconnecting;
  reconnecting = (async () => {
    await identity();
    if (!paper) {
      await bootstrap();
      return;
    }
    await refresh(true);
    if (!serverConfirmed || conflicted) return;
    await flush();
    await retryPhotos();
  })().finally(() => {
    reconnecting = null;
  });
  return reconnecting;
}

startup = bootstrap()
  .catch((error) => status(error.message))
  .finally(() => {
    startup = null;
  });
// Retry reachability even if the OS still reports online or no "online" event fires.
setInterval(() => {
  (serverConfirmed ? refresh() : reconnect()).catch((error) => {
    if (paper && recoverable(error))
      status("离线暂存、服务端状态待确认。联网后自动核验。");
  });
}, 15000);
window.addEventListener("online", () => {
  queue = queue.then(reconnect).catch((error) => status(error.message));
});

if ("serviceWorker" in navigator)
  navigator.serviceWorker
    .register("/training-sw.js")
    .catch(() => status("离线页面缓存不可用，文字和照片暂存仍会尝试保留。"));
function photoStore(operation, value) {
  return new Promise((resolve, reject) => {
    const opened = indexedDB.open("xiaohan-private-answer-photos", 1);
    opened.onupgradeneeded = () =>
      opened.result.createObjectStore("photos", { keyPath: "key" });
    opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const db = opened.result;
      const tx = db.transaction(
        "photos",
        operation === "getAll" ? "readonly" : "readwrite",
      );
      const store = tx.objectStore("photos");
      const req =
        operation === "put"
          ? store.put(value)
          : operation === "delete"
            ? store.delete(value)
            : store.getAll();
      let result;
      req.onsuccess = () => {
        result = req.result;
      };
      tx.oncomplete = () => {
        db.close();
        resolve(result);
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    };
  });
}
async function upload(id, file, article) {
  if (!editable()) throw new Error("请先确认开始考试。");
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) throw new Error("图片不能超过8MiB");
  const jobKey = key() + ":" + id + ":" + crypto.randomUUID();
  await photoStore("put", {
    key: jobKey,
    user: username,
    paper: paper.paper_id,
    id,
    startedAt: paper.timing.started_at,
    file,
  });
  await queue;
  try {
    await sendImage(id, file, article, jobKey);
  } catch (e) {
    status(e.message + " 照片原件仍保留在本机待同步区。");
  }
}
async function retryPhotos() {
  if (!editable() || !serverConfirmed) return;
  for (const job of await photoStore("getAll")) {
    if (
      job.user !== username ||
      job.paper !== paper.paper_id ||
      (job.startedAt && job.startedAt !== paper.timing.started_at)
    )
      continue;
    const article = document.querySelector(`article[data-id="${job.id}"]`);
    if (article) await sendImage(job.id, job.file, article, job.key);
  }
}

$("memory").onclick = async () => {
  try {
    if (paper?.timing.status === "in_progress" && deadline)
      throw new Error("请先完成当前限时训练，再进行错题复测。");
    const result = await api("/v2/memory/due");
    reviewSession(result.items);
  } catch (e) {
    learningError($("memory").onclick, e, "到期复测");
  }
};

/* Review one question at a time: first the question (a choice to pick, a blank to fill, or just recall), then the answer and a
   rating. The rating follows what you did: a wrong choice or blank can only be 忘记 / 困难, a right one 记得 / 轻松; with
   nothing to check (recall questions) all four are open. After the rating the next question comes by itself; the last
   one ends with a short summary. */
const NORMALIZE = (text) => String(text || "").replace(/[\s\u3000，。、；;,.]/g, "").toLowerCase();
function relaxedText(rules) {
  const what = { chapter_cap: "同一章节题数不超过该科 25%", difficulty_mix: "难度搭配约 3:5:2（基础:核心:拔高）" };
  const subject = { biochemistry: "生化", cell_biology: "细胞" };
  const parts = rules.map((r) => { const [k, s] = r.split(":"); return `${subject[s] || s}：${what[k] || k}`; });
  return "题库题目不够，本卷放宽了组卷规则——" + parts.join("；") + "。";
}

function reviewSession(items) {
  const root = $("learning");
  root.dataset.owner = location.hash || "#/mistakes";
  const tally = { done: 0, right: 0, wrong: 0, recall: 0 };
  const total = items.length;
  const finish = () => {
    root.replaceChildren();
    element("h2", "本次复习完成", root);
    const card = element("div", undefined, root);
    card.className = "card";
    element("p", `共复习 ${tally.done} 道：答对 ${tally.right} 道，答错 ${tally.wrong} 道，自评 ${tally.recall} 道。`, card);
    element("p", "下次复习时间已按你的评价更新。", card).className = "note";
    const again = element("button", "再看一遍到期的题", card);
    again.type = "button";
    again.className = "secondary";
    again.onclick = () => $("memory").onclick();
  };
  // The answer of a card is fetched while the learner is still thinking (and the next card's right after), so 「查看答案」 shows it at
  // once instead of waiting for the network; the rating is sent in the background and the next card appears immediately.
  const solutions = {};
  const solutionOf = (card) =>
    (solutions[card.question_id] ||= api("/v2/memory/" + card.question_id + "/solution").catch((error) => {
      delete solutions[card.question_id];
      throw error;
    }));
  const show = (index) => {
    if (index >= total) return finish();
    const card = items[index];
    solutionOf(card).catch(() => {});
    if (items[index + 1]) solutionOf(items[index + 1]).catch(() => {});
    root.replaceChildren();
    const head = element("div", undefined, root);
    head.className = "card-heading";
    element("h2", "到期复测", head);
    element("span", `第 ${index + 1} / ${total} 道`, head).className = "muted small";
    const bar = element("progress", undefined, root);
    bar.max = total;
    bar.value = index;
    bar.setAttribute("aria-label", "复习进度");
    const article = element("article", undefined, root);
    article.className = "card review-card";
    element("p", card.stem, article).className = "question-stem";
    const choice = card.question_type === "single_choice";
    const blank = card.question_type === "fill_blank";
    const options = choice && card.options && typeof card.options === "object"
      ? Object.entries(card.options).sort(([a], [b]) => a.localeCompare(b))
      : [];
    let selected = null;
    let typed = null;
    const optionButtons = [];
    if (choice && !options.length) element("p", "旧卡片缺少选项，不能完整复测。请联系维护者核查题目快照。", article);
    for (const [letter, text] of options) {
      const button = element("button", letter + ". " + text, article);
      button.type = "button";
      button.className = "review-choice";
      button.setAttribute("aria-pressed", "false");
      button.onclick = () => {
        selected = letter;
        for (const item of optionButtons) item.setAttribute("aria-pressed", String(item === button));
        reveal.disabled = false;
      };
      optionButtons.push(button);
    }
    if (blank) {
      typed = element("input", undefined, article);
      typed.type = "text";
      typed.placeholder = "先写下你的答案";
      typed.autocomplete = "off";
      typed.className = "review-input";
    }
    const reveal = element("button", choice || blank ? "查看答案" : "先回忆，再查看答案", article);
    reveal.type = "button";
    reveal.className = "primary";
    reveal.disabled = choice && options.length > 0; // pick first
    reveal.onclick = async () => {
      reveal.disabled = true;
      let solution;
      try {
        solution = await solutionOf(card);
      } catch (e) {
        reveal.disabled = false;
        status(e.message);
        return;
      }
      const answerDisplay = solution.answer_display || solution.answer;
      for (const button of optionButtons) button.disabled = true;
      if (typed) typed.disabled = true;
      let outcome = "recall";
      if (choice && selected !== null) outcome = selected === solution.answer ? "right" : "wrong";
      if (blank && typed.value.trim()) outcome = NORMALIZE(typed.value) === NORMALIZE(solution.answer) ? "right" : "unsure";
      const box = element("div", undefined, article);
      box.className = "review-answer";
      if (outcome === "right") element("p", "答对了", box).className = "verdict ok";
      if (outcome === "wrong") element("p", "答错了，正确答案如下", box).className = "verdict bad";
      if (outcome === "unsure") element("p", "和标准答案写法不完全一样，请对照后自己评价", box).className = "verdict";
      element("p", "正确答案：" + answerDisplay, box).className = "answer-line";
      const criteria = (solution.rubric || []).filter((item) => !choice && item.description).map(
        (item) => "评分核对点（" + item.points + "分）：" + item.description
      );
      for (const line of criteria) element("p", line, box).className = "note";
      if (solution.evidence?.length) {
        const detail = element("details", undefined, box);
        element("summary", "教材依据", detail);
        for (const entry of solution.evidence) {
          element("p", entry.textbook + " · " + entry.chapter, detail);
          element("blockquote", entry.excerpt, detail);
        }
      }
      element("p", "你觉得这道题", box).className = "note";
      const row = element("div", undefined, box);
      row.className = "rating-row";
      // what each rating may be, given what happened
      // (a typed answer that differs from the key may still be a fair paraphrase: 轻松 is closed, the rest is the learner's call)
      const allowed = outcome === "wrong" ? [1, 2] : outcome === "right" ? [3, 4] : outcome === "unsure" ? [1, 2, 3] : [1, 2, 3, 4];
      for (const [rating, label] of [[1, "忘记"], [2, "困难"], [3, "记得"], [4, "轻松"]]) {
        const button = element("button", label, row);
        button.type = "button";
        button.disabled = !allowed.includes(rating);
        button.onclick = () => {
          row.querySelectorAll("button").forEach((b) => (b.disabled = true));
          const body = JSON.stringify({ rating, expected_version: card.version, event_key: crypto.randomUUID(), ...(card.early ? { early: true } : {}) });
          const send = () => api("/v2/memory/" + card.question_id + "/review", { method: "POST", body });
          // saved in the background (the same event key makes a retry harmless); the next card does not wait for it
          send().catch(() => send()).catch((e) => status("这道题的复习结果没有保存成功：" + e.message + "。请稍后再复习一次。"));
          tally.done += 1;
          if (outcome === "right") tally.right += 1;
          else if (outcome === "wrong") tally.wrong += 1;
          else tally.recall += 1;
          show(index + 1);
        };
      }
      box.scrollIntoView?.({ block: "nearest" });
    };
  };
  if (!total) {
    root.replaceChildren();
    element("h2", "到期复测", root);
    element("p", "今天没有到期的题，明天再来。", root);
    return;
  }
  show(0);
}
// A learning sub-page that cannot load says why, inside the page, with a retry button (never a bare toast, never the sign-in form).
function learningError(handler, error, heading) {
  const root = $("learning");
  root.replaceChildren();
  if (heading) element("h2", heading, root);
  const card = element("div", undefined, root);
  card.className = "error-card";
  card.setAttribute("role", "alert");
  element("p", `${error.message || error}。已有的数据没有被改动。`, card);
  const retry = element("button", "重试", card);
  retry.type = "button";
  retry.className = "secondary";
  retry.onclick = handler;
  status(error.message || String(error));
}
$("source-status").onclick = async () => {
  const root = $("learning");
  root.replaceChildren();
  element("h2", "教材与60题题库核验", root);
  element("p", "下列数据直接从系统数据库读取；不会把原文件已下载、OCR可检索或AI任务排队误写成已审核。", root);
  const endpoints = [
    ["/v2/quality/six-source-readiness", "六份教材与真题文件"],
    ["/v2/quality/genuine-exam-style-readiness", "真实真题题型与风格依据"],
    ["/v2/quality/60q-bank-readiness", "60题最低库存与120题储备"],
  ];
  const answers = await Promise.allSettled(endpoints.map(([path]) => api(path)));
  const display = {biochemistry: "生物化学", cell_biology: "细胞生物学"};
  const typeName = {single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答"};
  answers.forEach((answer, index) => {
    const section = element("section", undefined, root);
    element("h3", endpoints[index][1], section);
    if (answer.status !== "fulfilled") {
      element("p", "暂时无法读取该部分：" + answer.reason.message, section);
      const again = element("button", "重试", section);
      again.type = "button";
      again.className = "secondary";
      again.onclick = () => $("source-status").onclick();
      return;
    }
    const value = answer.value;
    if (index === 0) {
      element("p", "真实文件已入库 " + value.imported_files + "/" + value.expected_files +
        " · 字节/页标完整性通过 " + value.integrity_passed_files + "/" + value.expected_files,
        section);
      for (const file of value.files) {
        const issue = file.issues.length ? " · 需检查：" + file.issues.join("、") : "";
        const figures = file.embedded_media_objects
          ? " · 内嵌图像 " + file.embedded_media_objects + " 个（图像内容未独立核验）"
          : "";
        element("p", file.filename + " · " + file.status + issue + figures, section);
      }
    } else if (index === 1) {
      element("p", "你提供的两份试题汇编作为真实历年真题。这里只核实能否从DOCX可靠提取题目，不审核真题身份。", section);
      for (const bucket of value.buckets) {
        element("p", (display[bucket.subject] || bucket.subject) + " · " +
          (typeName[bucket.question_type] || bucket.question_type) +
          "：可引用风格题 " + bucket.safe_style_anchors +
          "，待处理图像/解析问题 " + bucket.blocked_by_parsing_or_images, section);
      }
    } else {
      const min = value.buckets.reduce((n, x) => n + x.eligible_upper_bound, 0);
      const shortages = value.buckets.reduce((n, x) => n + x.shortage_at_least, 0);
      const reserve = value.buckets.reduce((n, x) => n + x.two_day_reserve_shortage_at_least, 0);
      element("p", "经当前AI双轮审查且符合新版分值、可抽取的候选上限 " +
        min + " 题；当天至少还缺 " + shortages +
        " 题，两天储备目标至少还缺 " + reserve + " 题。", section);
      for (const bucket of value.buckets) {
        element("p", (display[bucket.subject] || bucket.subject) + " · " +
          (typeName[bucket.question_type] || bucket.question_type) +
          "：可用上限 " + bucket.eligible_upper_bound +
          "/" + bucket.required + "，两天目标 " + bucket.two_day_reserve_target, section);
      }
      element("p", "以上数量仍需经过跨题提示、相似题及章节覆盖的最终抽卷检查；不保证已经能生成完整60题。", section);
    }
  });
};
$("weekly").onclick = async () => {
  try {
    const result = await api("/v2/stats/weekly");
    $("learning").replaceChildren();
    element("h2", "近7天章节情况", $("learning"));
    for (const row of result.chapters)
      element(
        "p",
        `${row.chapter}：${row.graded_points || 0}/${row.graded_max_points || 0}分，${row.pending}题待判分`,
        $("learning"),
      );
    window.TrainingUI?.weekly(result.chapters);
  } catch (e) {
    learningError($("weekly").onclick, e, "近7天章节情况");
  }
};
$("archive").onclick = async () => {
  try {
    renderArchive(await api("/v2/archive/status"));
  } catch (e) {
    learningError($("archive").onclick, e, "Notion归档状态");
  }
};

const ARCHIVE_STEPS = [
  "在 iPhone 的 Safari 里打开 notion.so/my-integrations，登录后点「新集成」（New integration）。",
  "类型选「内部集成」（Internal），名称随意（如「研习空间」），选你的工作区，点「提交」。",
  "在集成页面里点「显示」并复制「内部集成密钥」（以 secret_ 或 ntn_ 开头）。别发给任何人。",
  "在 Notion App 里新建一个空白页面（如「学习档案」）。点右上角「…」→「连接」（Connections）→ 选刚创建的集成并确认。",
  "还是这个页面：点「分享」→「复制链接」。链接最后面那串 32 位字母数字（问号之前）就是页面 ID；整条链接也可以直接用。",
  "在 Railway（网页版，Safari 可用）打开本项目的服务 → Variables，新增 NOTION_TOKEN（密钥）和 NOTION_PARENT_PAGE_ID（页面 ID），可选 PUBLIC_BASE_URL（本网站地址，用于成绩页链接）。保存后等它重新部署。",
  "回到这里：先点「测试连接」，两项都通过后打开上面的开关，再点「立即同步」。第一次同步会在那个页面下自动创建「训练记录」和「错题本」两个数据库。",
];

function renderArchive(info) {
  const root = $("learning");
  root.replaceChildren();
  element("h2", "Notion归档状态", root);
  const card = element("section", undefined, root);
  card.className = "card archive-card";
  const tiles = element("div", undefined, card);
  tiles.className = "tiles";
  const tile = (label, value, warn) => {
    const box = element("div", undefined, tiles);
    box.className = "tile";
    element("span", label, box).className = "label";
    const b = element("b", value, box);
    if (warn) b.className = "warn";
  };
  const when = (iso) =>
    iso ? new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "还没有";
  tile("服务器配置", info.configured ? "已配置" : "未配置", !info.configured);
  tile("同步开关", info.consented ? "已打开" : "已关闭");
  tile("上次同步", when(info.last_synced_at));
  tile("成功", String(info.success));
  tile("失败", String(info.failed), info.failed > 0);
  tile("待同步", String(info.pending));
  const label = element("label", undefined, card);
  label.className = "check-row";
  const box = element("input", undefined, label);
  box.type = "checkbox";
  box.setAttribute("role", "switch");
  element("span", info.consented ? "同步已打开：交卷并判完分后自动同步" : "同步已关闭：不会向 Notion 发送任何数据", label);
  box.checked = info.consented;
  box.onchange = async () => {
    box.disabled = true;
    try {
      await api("/v2/archive/consent", { method: "PUT", body: JSON.stringify({ enabled: box.checked }) });
      renderArchive(await api("/v2/archive/status"));
    } catch (e) {
      box.checked = !box.checked;
      box.disabled = false;
      status(e.message);
    }
  };
  if (!info.configured) {
    const note = element("p", "服务器还没有设置 NOTION_TOKEN 和 NOTION_PARENT_PAGE_ID。按下面的「设置步骤」完成后，这里会显示「已配置」。", card);
    note.className = "note warn";
  }
  const actions = element("div", undefined, card);
  actions.className = "button-row";
  const result = element("p", "", card);
  result.className = "note";
  result.setAttribute("role", "status");
  const test = element("button", "测试连接", actions);
  test.className = "secondary";
  test.type = "button";
  test.onclick = async () => {
    test.disabled = true;
    result.textContent = "正在检查密钥和页面权限…（只读，不写任何数据）";
    try {
      const r = await api("/v2/archive/test", { method: "POST", body: "{}" });
      result.textContent = r.ok ? "连接正常：密钥有效，页面已分享给集成。" : "连接失败：" + r.message;
    } catch (e) {
      result.textContent = "检查失败：" + e.message;
    }
    test.disabled = false;
  };
  const sync = element("button", "立即同步", actions);
  sync.className = "primary";
  sync.type = "button";
  sync.disabled = !info.consented || !info.configured;
  sync.onclick = async () => {
    sync.disabled = true;
    result.textContent = "正在同步，最多几十秒…";
    try {
      const r = await api("/v2/archive/sync", { method: "POST", body: "{}" });
      renderArchive(r);
      $("learning").querySelector("[role=status]").textContent = r.failed ? `这次有 ${r.failed} 项失败，原因见下面的记录。` : r.done ? `已同步 ${r.done} 套试卷。` : "没有需要同步的新内容。";
    } catch (e) {
      result.textContent = ARCHIVE_ERRORS[e.message] || "同步没有开始：" + e.message;
      sync.disabled = false;
    }
  };
  const history = element("section", undefined, root);
  history.className = "card";
  element("h3", "最近 10 条同步记录", history);
  if (!info.recent.length) element("p", "还没有同步记录", history).className = "note";
  const list = element("ul", undefined, history);
  list.className = "sync-list";
  for (const e of info.recent) {
    const li = element("li", undefined, list);
    element("span", undefined, li).className = "dot " + (e.ok ? "succeeded" : "blocked");
    element("span", e.ok ? `同步成功${e.detail && e.detail.mistakes !== undefined ? `（1 套试卷、${e.detail.mistakes} 道错题）` : ""}` : "同步失败：" + e.message, li).className = "kind";
    element("span", when(e.at), li).className = "meta";
  }
  const help = element("details", undefined, root);
  help.className = "card fold";
  element("summary", "设置步骤（在 iPhone 的 Safari 上完成）", help);
  const steps = element("ol", undefined, help);
  steps.className = "steps";
  for (const text of ARCHIVE_STEPS) element("li", text, steps);
  element("p", "隐私：密钥只放在服务器环境变量里，不会显示在这里，也不会写进日志。同步的是你自己的成绩和错题；不发送整段教材原文，只写「依据：教材第 X 章 · 第 Y 页」。", help).className = "note";
}

const ARCHIVE_ERRORS = {
  sync_switched_off: "同步开关是关闭的，请先打开开关。",
  not_configured: "服务器还没有配置 Notion 密钥和页面 ID。",
};

function renderAnalysis(analysis, parent) {
  if (!analysis || !analysis.selected) {
    element("p", "历史试卷未记录动态命题依据，不补造旧记录。", parent);
    return;
  }
  const details = element("details", undefined, parent);
  element("summary", "本卷命题依据（训练优先级，不是考试概率）", details);
  const stages = {
    cold_start: "冷启动",
    accumulating: "数据积累",
    stable: "稳定提升",
    pre_exam: "考前强化",
    mixed: "分学科学习阶段",
  };
  element(
    "p",
    `${analysis.algorithm_version} · ${stages[analysis.stage] || analysis.stage}`,
    details,
  );
  if (analysis.legacy_unmapped_answers)
    element(
      "p",
      `另有${analysis.legacy_unmapped_answers}条历史作答尚未完成可核验知识点映射，未用于本次掌握估计。`,
      details,
    );
  const shown = new Set();
  for (const selected of analysis.selected) {
    for (const [tag, basis] of Object.entries(selected.basis)) {
      const key = selected.subject + ":" + tag;
      if (shown.has(key)) continue;
      shown.add(key);
      const yearText = basis.exam_years.length
        ? `${basis.exam_years.join("、")}年，${basis.exam_distinct_questions}道独立原题`
        : "可用真题统计不足";
      element(
        "p",
        `${tag.replace(/^chapter:/, "章节：")}：${yearText}；最近${basis.recent_answer_samples}条有效作答中${basis.recent_lost_samples}条失分；${basis.review_due ? "有到期复习" : basis.review_near_due ? "接近复习时间" : "未标记为到期复习"}。${basis.degraded.length || basis.complete_year_sample < 3 ? "部分数据不足，保留覆盖探索；缺失年份不计作未考查。" : ""}`,
        details,
      );
    }
  }
}
$("history").onclick = async () => {
  try {
    if (deadline) throw new Error("请先完成当前限时训练，再查看历史档案。");
    const [data, cards] = await Promise.all([api("/v2/history"), api("/v2/stats/history")]);
    $("learning").replaceChildren();
    element("h2", "历史试卷", $("learning"));
    // a mini trend on top (one handed-in paper is enough; a paper still being graded counts with its objective part)
    await window.Insights?.historyChart($("learning"));
    const names = { biochemistry: "生化", cell_biology: "细胞" };
    // filter by kind of paper (客观 / 主观 / 旧版混合卷); the cards always say which kind they are
    const filter = element("div", undefined, $("learning"));
    filter.className = "chips history-filter";
    filter.setAttribute("role", "group");
    filter.setAttribute("aria-label", "按卷型筛选");
    const list = element("div", undefined, $("learning"));
    list.className = "history-list";
    let chosen = "";
    const drawFilter = () => {
      filter.replaceChildren();
      const options = [["", "全部", cards.papers.length], ...(cards.kinds || []).filter((k) => k.count).map((k) => [k.kind, k.label, k.count])];
      for (const [value, label, count] of options) {
        const chip = element("button", `${label} ${count}`, filter);
        chip.type = "button";
        chip.className = "chip" + (chosen === value ? " active" : "");
        chip.dataset.kind = value;
        chip.setAttribute("aria-pressed", String(chosen === value));
        chip.onclick = () => {
          chosen = value;
          drawFilter();
          drawList();
        };
      }
    };
    const drawList = () => {
      list.replaceChildren();
      const shown = cards.papers.filter((item) => !chosen || item.kind === chosen);
      if (!shown.length) element("p", cards.papers.length ? "这一类卷子还没有交过的记录。" : "还没有交过的试卷。交卷后会出现在这里。", list).className = "note";
      for (const item of shown) {
        // the whole card is the way into the paper's review; no separate button
        const card = element("a", undefined, list);
        card.className = "card history-card tappable";
        card.href = "#/review/p" + item.paper_id;
        card.dataset.kind = item.kind;
        const top = element("div", undefined, card);
        top.className = "hc-top";
        element("span", item.date.slice(5).replace("-", " 月 ") + " 日", top).className = "hc-date";
        element("span", item.kind_label + (item.spec === "xiaohan-150-60-v1" ? " · 28 题" : ""), top).className = "pill hc-kind";
        const score = element("p", undefined, card);
        score.className = "hc-score";
        if (item.graded) {
          element("strong", String(item.points), score);
          element("span", ` / ${item.max_points} 分`, score);
        } else if (item.objective_max) {
          element("strong", String(item.objective_points), score);
          element("span", ` / ${item.objective_max} 分 · 客观题`, score);
          element("span", "主观题判分中", score).className = "pill hc-pending";
        } else {
          element("strong", "—", score);
          element("span", "判分中", score).className = "pill hc-pending";
        }
        const meta = element("p", undefined, card);
        meta.className = "hc-meta";
        if (item.graded) {
          const parts = Object.entries(item.subjects).map(([sub, v]) => `${names[sub] || sub} ${v.points}/${v.max}`);
          if (parts.length) element("span", parts.join(" · "), meta);
        }
        if (item.seconds) element("span", `用时 ${Math.round(item.seconds / 60)} 分钟`, meta);
        element("span", `错题 ${item.mistakes}`, meta);
        if (item.consolidate) element("span", `待巩固 ${item.consolidate}`, meta);
        element("span", "›", card).className = "chev";
      }
    };
    drawFilter();
    drawList();
    for (const item of data.legacy) {
      const article = element(
        "article",
        `历史记录 · ${item.date} · 原成绩${item.score}/60`,
        $("learning"),
      );
      const button = element("button", "查看只读原卷", article);
      button.onclick = async () => {
        try {
          const old = await api(`/legacy/papers/${item.paper_id}`);
          const answers = Object.fromEntries(
            old.answers.map((a) => [a.question_id, a]),
          );
          for (const q of old.questions) {
            const answer = answers[q.id];
            element(
              "p",
              `${q.stem} · 原作答：${answer?.selected_answer || "未作答"} · ${answer ? (answer.is_correct ? "原记录正确" : "原记录错误") : "无批改记录"}`,
              article,
            );
          }
          button.disabled = true;
        } catch (e) {
          status(e.message);
        }
      };
    }
  } catch (e) {
    learningError($("history").onclick, e, "历史试卷");
  }
};

$("insights").onclick = async () => {
  try {
    const [reports, readiness] = await Promise.all([api("/v2/model/insights"), api("/v2/model/insights/status").catch(() => null)]);
    const root = $("learning");
    root.replaceChildren();
    if (!reports.length) {
      // a normal empty state (thin icon + one short sentence), not a system notice
      const line = element("div", undefined, root);
      line.className = "empty-line";
      line.innerHTML = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/></svg>';
      // say what is missing (and when it generates by itself), not just that there is nothing
      element("span", readiness?.text || "判分完成后自动生成学习建议", line);
    }
    for (const report of reports) {
      element(
        "h3",
        report.kind === "study_plan" ? "复习建议" : "错题解析",
        root,
      );
      element("p", report.advice.summary, root);
      for (const item of report.advice.items) {
        element(
          "p",
          "题目 " + item.question_id + " · " + item.knowledge_tags.join(" / "),
          root,
        );
        element("p", item.explanation, root);
        element("blockquote", item.excerpt, root);
        element("p", item.next_action, root);
      }
      element("p", report.advice.limitations.join("；"), root);
      element(
        "small",
        "AI自动核验 · " +
          report.model_id +
          " / " +
          report.effort +
          "；不改写历史成绩或FSRS时间",
        root,
      );
    }
  } catch (error) {
    learningError($("insights").onclick, error, "学习建议");
  }
};

// Keep IME composition local; send only after the final composition input commits.
document.addEventListener("compositionstart", (event) => {
  if (event.target.matches("#questions input, #questions textarea"))
    compositionActive = true;
});
document.addEventListener("compositionend", (event) => {
  if (!event.target.matches("#questions input, #questions textarea")) return;
  compositionActive = false;
  setTimeout(() => {
    queue = queue.then(flush).catch((error) => status(error.message));
  }, 0);
});
function addSeconds(id, gained) {
  if (!Number.isFinite(gained) || gained <= 0) return;
  change(id, { seconds: Math.min(7200, (localDrafts[id]?.seconds || 0) + gained) });
}
window.TrainingUI?.actions({ flush, addSeconds });

// Every script of the shell has now hooked up its pages (training.js is the last one loaded): the page the URL names
// (#/profile/archive, #/stats/weekly, #/progress ...) can draw itself. Sent earlier, the sub-pages of this file had no
// click handler yet and a deep link or a restored home-screen app showed an empty page.
window.dispatchEvent(new Event("shell-ready"));
