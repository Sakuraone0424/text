"use strict";
const $ = (id) => document.getElementById(id);
const names = {
  single_choice: "单选",
  fill_blank: "填空",
  term: "名词解释",
  short_answer: "简答",
  essay: "论述",
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
    const e = new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail),
    );
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
  } catch (e) {
    status(e.message);
  }
};
async function prepare() {
  if (startup) await startup;
  await identity();
  const saved = cachedPaper(username);
  if (saved) {
    try {
      paper = await api(`/v2/papers/${saved.paper.paper_id}`);
      serverConfirmed = true;
      sessionStorage.removeItem("quiz-v2-auth-blocked");
      await restore();
    } catch (error) {
      if (!recoverable(error)) throw error;
      await restoreOffline(saved);
    }
    return;
  }
  const specification = await api("/v2/training-specification");
  paper = await api("/v2/papers", {
    method: "POST",
    body: JSON.stringify({
      specification: specification.specification,
      quotas: specification.quotas,
    }),
  });
  serverConfirmed = true;
  sessionStorage.removeItem("quiz-v2-auth-blocked");
  await restore();
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
$("prepare").onclick = () => prepare().catch((e) => status(e.message));
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
    await restore();
    status("计时已开始。答题自动保存；上传图片也计入60分钟。");
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
      status("已保存至服务端");
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
  const done = Object.values(localDrafts).filter(
    (v) =>
      v.text?.trim() || v.blanks?.some((x) => x.trim()) || v.image_ids?.length,
  ).length;
  $("progress").textContent = `已答 ${done} / ${paper?.count || 60} · 剩余 ${(paper?.count || 60) - done}`;
  emitUI();
}
function render() {
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
      `${q.position}. ${q.subject === "biochemistry" ? "生化与分子生物学" : "细胞生物学"} · ${names[q.question_type]} · ${q.max_points}分${q.is_retest ? " · FSRS复测" : q.review_knowledge_tags?.length ? " · 知识点复测变式" : ""}`,
      article,
    );
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
        const label = element("label", `第${i + 1}空`, article);
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
      input.setAttribute("aria-label", `第${q.position}题答案`);
      input.oninput = () => change(q.id, { text: input.value });
      if (q.question_type !== "fill_blank") {
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
  }
  renderProgress();
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
$("submit").onclick = async () => {
  const approved = window.TrainingUI
    ? await window.TrainingUI.confirmSubmit()
    : confirm("确认统一交卷？未答题按0分，已答主观题待批改。");
  if (!approved) return;
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
    await showResult();
  } catch (e) {
    status(e.message);
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
  $("questions").replaceChildren();
  if (signature === renderedResultSignature && $("result").childElementCount)
    return;
  renderedResultSignature = signature;
  $("result").replaceChildren();
  element(
    "h2",
    result.score === null
      ? `待批改 ${result.pending} 题 · 已评分得分 ${result.graded_points}`
      : `成绩 ${result.score} / ${result.max_points}`,
    $("result"),
  );
  renderAnalysis(result.selection_analysis, $("result"));
  for (const answer of result.answers) {
    const article = element("article", undefined, $("result"));
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
      `题目 ${answer.question_id} · ${answer.points === null ? "待批改" : answer.points + "分"}`,
      article,
    );
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
    for (const criterion of answer.rubric)
      element(
        "p",
        `评分点（${criterion.points}分）：${criterion.description}`,
        article,
      );
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
      const label = element("label", "复盘原因", article);
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
      const note = element("input", undefined, article);
      note.placeholder = "补充你的复盘说明";
      note.maxLength = 2000;
      const record = element("button", "保存复盘", article);
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
    for (const evidence of answer.evidence) {
      const bibliographicPending = /未核查|待核查/.test(
        (evidence.authors || "") + " " + (evidence.edition || "")
      );
      element(
        "p",
        "教材依据：" + evidence.textbook + " · " + evidence.chapter +
          " · " + evidence.locator,
        article,
      );
      if (bibliographicPending)
        element("p", "书目信息尚未核验；OCR定位不等于纸书页码。", article);
      element("blockquote", evidence.excerpt, article);
    }
  }
  window.TrainingUI?.result(result);
  startGradingActivity(paper.paper_id);
  showVerificationStatus(paper.paper_id);
  status("已交卷。照片识别不清时须人工确认，不会直接判零。");
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
    const ai = result.items.filter(q => q.independent_ai_science_and_answer_review).length;
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
        (entry.independent_ai_science_and_answer_review ? "独立AI证据复核通过" :
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
  element("h2", "实时批改活动", section);
  const hint = element("p", "正在读取服务端批改状态……", section);
  hint.setAttribute("role", "status");
  const events = element("div", undefined, section);
  const refreshScores = element("button", "刷新成绩与解析", section);
  refreshScores.type = "button";
  refreshScores.onclick = () => showResult().catch((e) => status(e.message));
  let busy = false;
  const labels = {
    pending: "等待处理", retrying: "等待重试", running: "正在批改",
    succeeded: "模型响应已返回，等待评分写入", blocked: "需要人工处理或重新授权",
    cancelled: "已取消", pending_manual_or_queue: "未进入自动队列／待人工复核",
    graded: "已判分"
  };
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
      hint.textContent = "已判分 " + data.graded + "/" + data.total +
        " 题，待批 " + data.pending + " 题。" +
        (data.auto_queue_available ? "自动批改需独立工作进程执行。" :
          "自动批改尚未启用或授权不足；不会假装已发送AI任务。");
      events.replaceChildren();
      for (const row of data.questions.filter((q) =>
        q.state !== "graded" || q.reviewer_kind === "AI"
      ).slice(0, 60)) {
        const item = element("p", "第" + row.position + "题 · " +
          (labels[row.state] || row.state) +
          (row.points === null ? "" : " · " + row.points + "分"), events);
        if (row.error_code) item.textContent += " · " + row.error_code;
      }
      if (data.complete && gradingActivityTimer) {
        clearInterval(gradingActivityTimer);
        gradingActivityTimer = null;
        hint.textContent += " 全部完成，点击“刷新成绩与解析”查看最终成绩。";
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
  $("questions").replaceChildren();
  $("result").replaceChildren();
  $("login").hidden = false;
  $("start").hidden = true;
  $("submit").hidden = true;
  status(message);
}
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
      ![28, 60].includes(p.count) ||
      p.max_points !== 150 ||
      p.duration_seconds !== 3600 ||
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
    if (!sessionStorage.getItem("quiz-v2-paper-key"))
      status(identityKnown ? "已登录，可准备今日试卷。" : "请先登录。");
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
    $("learning").replaceChildren();
    element("h2", "到期复测", $("learning"));
    if (!result.items.length) element("p", "暂无到期复测", $("learning"));
    for (const card of result.items) {
      const article = element("article", undefined, $("learning"));
      element("p", card.stem, article);
      const choice = card.question_type === "single_choice";
      const options = choice && card.options && typeof card.options === "object"
        ? Object.entries(card.options).sort(([a], [b]) => a.localeCompare(b))
        : [];
      let selected = null;
      const optionButtons = [];
      if (choice && !options.length) {
        element("p", "旧卡片缺少选项，不能完整复测。请联系维护者核查题目快照。", article);
      }
      for (const [letter, text] of options) {
        const button = element("button", letter + ". " + text, article);
        button.type = "button";
        button.className = "review-choice";
        button.setAttribute("aria-pressed", "false");
        button.onclick = () => {
          selected = letter;
          for (const item of optionButtons)
            item.setAttribute("aria-pressed", String(item === button));
        };
        optionButtons.push(button);
      }
      if (choice && options.length)
        element("p", "先独立选择，再查看答案；选择只用于本次复习，不改写原卷。", article);
      const reveal = element("button", "先回忆，再查看答案", article);
      reveal.onclick = async () => {
        try {
          const solution = await api("/v2/memory/" + card.question_id + "/solution");
          const answerDisplay = solution.answer_display || solution.answer;
          reveal.disabled = true;
          for (const button of optionButtons) button.disabled = true;
          if (choice && selected !== null) {
            const picked = options.find(([letter]) => letter === selected);
            element("p", "本次选择：" + selected + ". " + (picked ? picked[1] : ""), article);
            element("p", selected === solution.answer ? "本次选择正确" : "本次选择需要订正", article);
          }
          element("p", "正确答案：" + answerDisplay, article);
          const criteria = (solution.rubric || []).map(
            (item) => "评分核对点（" + item.points + "分）：" + item.description
          );
          for (const line of criteria) element("p", line, article);
          const evidence = (solution.evidence || []).map((entry) =>
            entry.textbook + " · " + entry.chapter + " · " + entry.locator +
            "：" + entry.excerpt
          );
          if (solution.evidence?.length) {
            const detail = element("details", undefined, article);
            element("summary", "教材依据与出处核验状态", detail);
            for (const entry of solution.evidence) {
              const unknown = /未核查|待核查/.test(
                (entry.authors || "") + " " + (entry.edition || "")
              );
              element("p", entry.textbook + " · " + entry.chapter +
                " · " + entry.locator + (unknown ? " · 书目信息待核查" : ""), detail);
              element("blockquote", entry.excerpt, detail);
            }
          }
          const copy = element("button", "复制正确答案、评分点及教材依据", article);
          copy.onclick = async () => {
            try {
              await navigator.clipboard.writeText([
                card.stem, ...options.map(([letter, text]) => letter + ". " + text),
                "正确答案：" + answerDisplay, ...criteria, ...evidence
              ].join("\n"));
              status("已复制完整复习内容。");
            } catch {
              status("复制未成功，请长按正确答案或教材依据选择文本。");
            }
          };
          for (const [rating, label] of [
            [1, "忘记"],
            [2, "困难"],
            [3, "记得"],
            [4, "轻松"],
          ]) {
            const button = element("button", label, article);
            button.onclick = async () => {
              try {
                await api("/v2/memory/" + card.question_id + "/review", {
                  method: "POST",
                  body: JSON.stringify({
                    rating,
                    expected_version: card.version,
                    event_key: crypto.randomUUID(),
                  }),
                });
                article.replaceChildren();
                element("p", "复习反馈已保存，下次复测时间已更新。", article);
              } catch (e) {
                status(e.message);
              }
            };
          }
        } catch (e) {
          status(e.message);
        }
      };
    }
  } catch (e) {
    status(e.message);
  }
};
$("weekly").onclick = async () => {
  try {
    const result = await api("/v2/stats/weekly");
    $("learning").replaceChildren();
    element("h2", "近7天章节情况", $("learning"));
    for (const row of result.chapters)
      element(
        "p",
        `${row.chapter}：${row.graded_points || 0}/${row.graded_max_points || 0}分，${row.pending}题待批改`,
        $("learning"),
      );
    window.TrainingUI?.weekly(result.chapters);
  } catch (e) {
    status(e.message);
  }
};
$("archive").onclick = async () => {
  try {
    const result = await api("/v2/archive/status");
    $("learning").replaceChildren();
    element("h2", "Notion归档状态", $("learning"));
    element(
      "p",
      result.configured
        ? "私有归档已配置"
        : "Notion尚未配置，未向外部发送答题数据",
      $("learning"),
    );
    const label = element(
      "label",
      "允许向已配置的私有Notion归档 ",
      $("learning"),
    );
    const box = element("input", undefined, label);
    box.type = "checkbox";
    box.checked = result.consented;
    box.onchange = async () => {
      try {
        await api("/v2/archive/consent", {
          method: "PUT",
          body: JSON.stringify({ enabled: box.checked }),
        });
        status(
          box.checked
            ? "已记录归档许可，配置启用后自动同步。"
            : "已停止后续归档任务。",
        );
      } catch (e) {
        status(e.message);
      }
    };
    const states = {
      pending: "等待同步",
      synced: "写入成功，等待读回验证",
      verified: "读回验证成功",
      failed: "同步失败，将重试",
    };
    for (const job of result.jobs)
      element(
        "p",
        `${states[job.state]} · 重试 ${job.attempts} 次`,
        $("learning"),
      );
  } catch (e) {
    status(e.message);
  }
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
    const data = await api("/v2/history");
    $("learning").replaceChildren();
    element("h2", "历史只读档案", $("learning"));
    for (const item of data.training) {
      const article = element(
        "article",
        `${item.mode === "reviewed_60" ? 60 : 28}题 · ${item.paper_date} · ${item.score === null ? "待批改" : item.score + "分"}`,
        $("learning"),
      );
      const button = element("button", "查看已提交结果", article);
      button.onclick = async () => {
        try {
          const result = await api(`/v2/papers/${item.paper_id}/result`);
          element(
            "p",
            result.score === null
              ? `待批改${result.pending}题`
              : `得分${result.score}/${result.max_points}`,
            article,
          );
          renderAnalysis(result.selection_analysis, article);
        } catch (e) {
          status(e.message);
        }
      };
    }
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
    status(e.message);
  }
};

$("insights").onclick = async () => {
  try {
    const reports = await api("/v2/model/insights");
    const root = $("learning");
    root.replaceChildren();
    if (!reports.length)
      element("p", "尚无已完成的AI学习建议；不会编造成绩或薄弱点。", root);
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
    status(error.message);
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
window.TrainingUI?.actions({ flush });
