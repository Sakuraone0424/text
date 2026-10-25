"use strict";
const $ = (id) => document.getElementById(id);
const counts = {
  single_choice: 3,
  fill_blank: 3,
  term: 4,
  short_answer: 3,
  essay: 1,
};
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
  conflicted = false;
const key = () => `quiz:v2:${username}:${paper.paper_id}`;
function status(t) {
  $("status").textContent = t;
}
async function api(path, options = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  let value = await r.json();
  if (!r.ok) {
    const e = new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail),
    );
    e.status = r.status;
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
  version = t.version;
  deadline = t.deadline ? Date.parse(t.deadline) : null;
  serverAnchor = Date.parse(t.server_now);
  anchor = performance.now();
  if (t.status === "submitted") {
    showResult();
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
  if (!remaining) {
    $("submit").disabled = true;
    refresh().catch(() =>
      status("时间已到。离线内容保留在本机，联网后将以服务端已存答案交卷。"),
    );
  }
}
setInterval(tick, 1000);
async function identity() {
  const me = await api("/auth/me");
  username = me.username;
  sessionStorage.setItem("quiz-v2-user", username);
  $("login").hidden = true;
}
$("login").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: $("password").value }),
    });
    $("password").value = "";
    await identity();
    status("已登录，准备试卷后再点击开始。");
  } catch (e) {
    status(e.message);
  }
};
async function prepare() {
  await identity();
  paper = await api("/v2/papers", {
    method: "POST",
    body: JSON.stringify({
      specification: "xiaohan-150-60-v1",
      quotas: { biochemistry: counts, cell_biology: counts },
    }),
  });
  await restore();
}
async function restore() {
  version = paper.timing.version;
  localDrafts = { ...paper.timing.drafts };
  pending = {};
  try {
    const stored = JSON.parse(localStorage.getItem(key()) || "null");
    if (stored) {
      pending = stored.pending || {};
      localDrafts = { ...localDrafts, ...stored.localDrafts };
      if (Object.keys(pending).length && stored.version !== version)
        showConflict();
    }
  } catch {
    status("本机暂存读取失败，服务端答案仍可恢复。");
  }
  timing(paper.timing);
  render();
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
    paper = await api(`/v2/papers/${paper.paper_id}/start`, { method: "POST" });
    await restore();
    status("计时已开始。答题自动保存；上传图片也计入60分钟。");
  } catch (e) {
    status(e.message);
  }
};
function change(id, update) {
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
  queue = queue.then(flush).catch((e) => status(e.message));
}
function showConflict() {
  conflicted = true;
  $("conflict").hidden = false;
  status("版本冲突：自动保存已暂停，避免覆盖另一台设备的较新答案。");
}
async function flush() {
  if (!paper || conflicted || paper.timing.status !== "in_progress") return;
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

async function refresh() {
  if (!paper) return;
  const fresh = await api(`/v2/papers/${paper.paper_id}`);
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
  const changed = fresh.timing.version !== version;
  paper = fresh;
  if (changed && !Object.keys(pending).length) {
    localDrafts = { ...fresh.timing.drafts };
    render();
  }
  timing(fresh.timing);
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
  $("progress").textContent = `已答 ${done} / 28 · 剩余 ${28 - done}`;
}
function render() {
  $("questions").replaceChildren();
  for (const q of paper.questions) {
    const article = element("article", undefined, $("questions"));
    article.dataset.id = q.id;
    const value = localDrafts[q.id] || {};
    if (value.flagged) article.classList.add("flagged");
    element(
      "h2",
      `${q.position}. ${q.subject === "biochemistry" ? "生化与分子生物学" : "细胞生物学"} · ${names[q.question_type]} · ${q.max_points}分${q.is_retest ? " · FSRS复测" : ""}`,
      article,
    );
    element("p", q.stem, article);
    if (q.question_type === "single_choice") {
      for (const [option, label] of Object.entries(q.options)) {
        const b = element("button", `${option}. ${label}`, article);
        b.className = "option" + (value.text === option ? " chosen" : "");
        b.onclick = () => {
          article
            .querySelectorAll(".option")
            .forEach((x) => x.classList.remove("chosen"));
          b.classList.add("chosen");
          change(q.id, { text: option });
        };
      }
    } else if (q.question_type === "fill_blank" && q.blank_count) {
      for (let i = 0; i < q.blank_count; i++) {
        const label = element("label", `第${i + 1}空`, article);
        const input = element("input", undefined, label);
        input.value = value.blanks?.[i] || "";
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
  if (!r.ok) throw new Error(t.detail || "上传失败");
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
  if (!confirm("确认统一交卷？未答题按0分，已答主观题待批改。")) return;
  try {
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
  $("start").hidden = true;
  $("submit").hidden = true;
  const result = await api(`/v2/papers/${paper.paper_id}/result`);
  $("questions").replaceChildren();
  $("result").replaceChildren();
  element(
    "h2",
    result.score === null
      ? `待批改 ${result.pending} 题 · 已评分得分 ${result.graded_points}`
      : `成绩 ${result.score} / ${result.max_points}`,
    $("result"),
  );
  for (const answer of result.answers) {
    const article = element("article", undefined, $("result"));
    element("p", answer.stem, article);
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
    element(
      "p",
      "你的作答：" +
        (response.text ||
          (response.blanks || []).join("；") ||
          (response.image_ids?.length ? "手写图片" : "未作答")),
      article,
    );
    element("p", "参考答案：" + answer.solution, article);
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
      element(
        "p",
        `${evidence.textbook} · ${evidence.edition} · ${evidence.chapter} · ${evidence.locator}`,
        article,
      );
      element("blockquote", evidence.excerpt, article);
    }
  }
  status("已交卷。照片识别不清时须人工确认，不会直接判零。");
}
identity()
  .then(() => status("已登录，可准备今日试卷。"))
  .catch(() => {
    if (!navigator.onLine) {
      try {
        const stored = JSON.parse(
          localStorage.getItem(sessionStorage.getItem("quiz-v2-paper-key")) ||
            "null",
        );
        if (stored?.paper) {
          username = sessionStorage.getItem("quiz-v2-user");
          paper = stored.paper;
          pending = stored.pending;
          localDrafts = stored.localDrafts;
          version = stored.version;
          serverAnchor = Date.now() + stored.offset;
          anchor = performance.now();
          deadline = Date.parse(paper.timing.deadline);
          render();
          $("login").hidden = true;
          $("submit").hidden = false;
          status(
            "离线恢复：本机暂存已保留，截止前联网才能计入答卷。显示的剩余时间为估算。",
          );
          return;
        }
      } catch {}
    }
    status("请先登录。");
  });
// A visible session also follows changes made on another device.
setInterval(() => refresh().catch(() => {}), 15000);

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
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) throw new Error("图片不能超过8MiB");
  const jobKey = key() + ":" + id + ":" + crypto.randomUUID();
  await photoStore("put", {
    key: jobKey,
    user: username,
    paper: paper.paper_id,
    id,
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
  if (!paper || !navigator.onLine) return;
  for (const job of await photoStore("getAll")) {
    if (job.user !== username || job.paper !== paper.paper_id) continue;
    const article = document.querySelector(`article[data-id="${job.id}"]`);
    if (article) await sendImage(job.id, job.file, article, job.key);
  }
}
window.addEventListener("online", () => {
  queue = queue
    .then(async () => {
      await refresh();
      if (!conflicted) {
        await flush();
        await retryPhotos();
      }
    })
    .catch((e) => status(e.message));
});

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
      const reveal = element("button", "先回忆，再查看答案", article);
      reveal.onclick = async () => {
        const solution = await api(`/v2/memory/${card.question_id}/solution`);
        element("p", solution.answer, article);
        reveal.disabled = true;
        for (const [rating, label] of [
          [1, "忘记"],
          [2, "困难"],
          [3, "记得"],
          [4, "轻松"],
        ]) {
          const button = element("button", label, article);
          button.onclick = async () => {
            try {
              await api(`/v2/memory/${card.question_id}/review`, {
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
