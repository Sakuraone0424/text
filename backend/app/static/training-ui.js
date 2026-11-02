/* Presentation only: existing training.js owns answers, timing, sync and submission. */
"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const labels = { biochemistry: "生物化学", cell_biology: "细胞生物学" };
  let latest = { paper: null, drafts: {}, pendingCount: 0, conflicted: false },
    position = 0,
    view = "home",
    actions = {},
    account = null,
    lastLoad = 0,
    resultPaper = null;
  const make = (tag, text, parent, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    if (parent) parent.append(node);
    return node;
  };
  const typeNames = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
  /* Time spent per question: counted only while that question is on screen, the exam is running and the
     page is visible. It is saved with the draft (seconds) and copied into answer_timings at submit. */
  let dwell = { id: null, since: 0 },
    flushing = false;
  function flushDwell(restart = false) {
    if (flushing) return; // saving a draft re-renders the UI, which must not count time again
    flushing = true;
    try {
      if (dwell.id !== null && dwell.since) {
        const gained = Math.round((Date.now() - dwell.since) / 1000);
        if (gained > 0 && active()) actions.addSeconds?.(dwell.id, gained);
      }
      dwell.since = restart && dwell.id !== null && !document.hidden && active() && view === "exam" ? Date.now() : 0;
    } finally {
      flushing = false;
    }
  }
  function startDwell(id) {
    if (dwell.id === id) {
      if (!dwell.since && !document.hidden && view === "exam") dwell.since = Date.now();
      return; // the same question being re-rendered keeps its running timer
    }
    flushDwell();
    dwell = { id, since: !document.hidden && view === "exam" ? Date.now() : 0 };
  }
  setInterval(() => flushDwell(true), 20000);
  document.addEventListener("visibilitychange", () => (document.hidden ? flushDwell() : flushDwell(true)));
  window.addEventListener("pagehide", () => flushDwell());
  const answered = (draft) =>
    !!(
      draft?.text?.trim() ||
      draft?.blanks?.some((s) => s.trim()) ||
      draft?.image_ids?.length
    );
  const active = () => latest.paper?.timing.status === "in_progress";
  const storageKey = () =>
    `quiz-ui-question:${account}:${latest.paper?.paper_id}`;
  function showView(next) {
    view = next;
    document.querySelectorAll("[data-page]").forEach((node) => {
      node.hidden = node.dataset.page !== view;
    });
    document.querySelectorAll("[data-view]").forEach((node) => {
      if (node.dataset.view === view) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    document.body.classList.toggle("focus-mode", view === "exam" && active());
    byId("focus-actions").hidden = view !== "exam" || !active();
    byId("question-tags").hidden = view !== "exam" || !active();
    if (view !== "exam") flushDwell();
    const roots = {
      mistakes: "mistakes-content",
      stats: "stats-content",
      profile: "profile-content",
    };
    if (roots[view]) byId(roots[view]).append(byId("learning"));
    else byId("learning").hidden = true;
    if (roots[view]) byId("learning").hidden = false;
    window.Insights?.onView(view);
  }
  function navigate(index, focus = true) {
    const questions = active() ? latest.paper.questions || [] : [];
    if (!active() || !questions.length) return;
    position = Number.isFinite(index)
      ? Math.max(0, Math.min(Math.trunc(index), questions.length - 1))
      : 0;
    try {
      sessionStorage.setItem(storageKey(), String(position));
    } catch {
      /* Navigation must work without storage. */
    }
    const question = questions[position];
    document.querySelectorAll("#questions > article").forEach((article) => {
      article.hidden = Number(article.dataset.id) !== question.id;
    });
    byId("current-number").textContent = String(position + 1);
    byId("current-subject").textContent =
      labels[question.subject] || question.subject;
    byId("current-points").textContent = `${typeNames[question.question_type] || ""} · ${question.max_points}分`;
    byId("previous-question").disabled = position === 0;
    byId("next-question").textContent =
      position === questions.length - 1 ? "检查答题卡" : "下一题";
    const marked = !!latest.drafts[question.id]?.flagged;
    byId("toggle-mark").setAttribute("aria-pressed", String(marked));
    byId("toggle-mark").textContent = marked ? "★ 已标记稍后看" : "☆ 标记稍后看";
    // "标记稍后看" and "这题有问题" sit together in one row under the question card
    const article = document.querySelector(`#questions article[data-id="${question.id}"]`);
    if (article) {
      let row = article.querySelector(":scope > .secondary-actions");
      if (!row) {
        row = make("div", undefined, article, "secondary-actions");
        const report = article.querySelector(".question-report");
        if (report) row.append(report);
      }
      row.prepend(byId("toggle-mark"));
    }
    startDwell(question.id);
    updateMap();
    if (focus) {
      const stem = document.querySelector(
        `#questions article[data-id="${question.id}"] .question-stem`,
      );
      stem?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }
  function updateMap() {
    const questions = active() ? latest.paper.questions || [] : [];
    const grid = byId("question-grid");
    if (
      grid.children.length !== questions.length ||
      grid.dataset.paper !== String(latest.paper?.paper_id)
    ) {
      grid.replaceChildren();
      grid.dataset.paper = String(latest.paper?.paper_id);
      questions.forEach((question, index) => {
        const button = make("button", String(index + 1), grid);
        button.type = "button";
        button.dataset.questionId = question.id;
        button.onclick = () => {
          byId("question-dialog").close();
          showView("exam");
          navigate(index);
        };
      });
    }
    const reported = new Set(latest.paper?.reported_question_ids || []);
    let count = 0,
      marked = 0;
    questions.forEach((question, index) => {
      const button = grid.children[index],
        draft = latest.drafts[question.id],
        isReported = reported.has(question.id),
        done = answered(draft) && !isReported,
        flag = !!draft?.flagged;
      count += Number(done);
      marked += Number(flag);
      button.classList.toggle("answered", done);
      button.classList.toggle("marked", flag);
      button.classList.toggle("reported", isReported);
      if (position === index) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.setAttribute(
        "aria-label",
        `第${index + 1}题，${isReported ? "已反馈不计分" : done ? "已作答" : "未作答"}${flag ? "，已标记" : ""}${position === index ? "，当前题" : ""}`,
      );
    });
    const counted = (questions.length || latest.paper?.count || 60) - reported.size;
    byId("answer-progress").max = counted;
    byId("answer-progress").value = count;
    byId("map-progress").textContent = `${count}/${counted}`;
    byId("map-summary").textContent =
      `已答${count}题 · 未答${Math.max(0, counted - count)}题 · 已标记${marked}题` +
      (reported.size ? ` · 已反馈${reported.size}题（不计分）` : "");
  }
  function state(next) {
    const previousPaper = latest.paper?.paper_id;
    latest = next;
    const timing = latest.paper?.timing;
    const count = latest.paper?.count || 60;
    const totalIndicator = document.querySelector("#current-number + .muted");
    if (totalIndicator) totalIndicator.textContent = " / " + count;
    byId("paper-state").textContent = timing
      ? {
          not_started: "尚未开始",
          in_progress: "正在作答",
          submitted: "已提交",
        }[timing.status] || "状态待确认"
      : "尚未准备";
    byId("paper-state").classList.toggle(
      "green-pill",
      timing?.status === "submitted",
    );
    byId("prepare").firstChild.textContent = active()
      ? "继续作答"
      : timing?.status === "submitted"
        ? "查看成绩"
        : "开始";
    byId("prepare-note").textContent = active()
      ? "考试计时继续，已存答案不会丢失"
      : "先准备试卷，确认后才开始60分钟计时";
    byId("exam-intro").hidden = !!timing && timing.status !== "not_started";
    byId("intro-state").textContent =
      timing?.status === "not_started"
        ? "试卷已准备，尚未开始计时。请确认可以连续作答60分钟。"
        : "请先准备试卷，再确认开始计时。";
    document.querySelector("[data-prepare-proxy]").hidden = !!latest.paper;
    byId("result").hidden = timing?.status !== "submitted";
    byId("result-bar").hidden = timing?.status !== "submitted";
    byId("questions").hidden = !active();
    byId("questions").inert = !active() || !!latest.expired;
    byId("submit").disabled = !!latest.expired || !!latest.serverPending;
    byId("toggle-mark").disabled = !!latest.expired;
    byId("focus-toolbar").hidden = !active();
    if (!active()) {
      byId("question-dialog").close();
      byId("question-grid").replaceChildren();
    }
    if (previousPaper !== latest.paper?.paper_id) {
      byId("result").replaceChildren();
      position = 0;
      try {
        position = Number(sessionStorage.getItem(storageKey()) || 0);
      } catch {
        /* Local storage is optional. */
      }
    }
    if (latest.paper?.questions?.length) navigate(position, false);
    byId("save-state").textContent = latest.conflicted
      ? "版本冲突 · 同步已暂停"
      : latest.serverPending && active()
        ? "离线暂存 · 服务端状态待确认"
        : latest.pendingCount
          ? `本机暂存 · ${latest.pendingCount}题待同步`
          : timing?.status === "in_progress"
            ? "已与服务端同步"
            : "尚未开始";
    const photoPending = active() && next.message?.includes("照片原件仍保留");
    if (active() && next.message?.includes("上传中"))
      byId("save-state").textContent = "图片上传中 · 计时继续";
    if (photoPending)
      byId("save-state").textContent = "图片待同步 · 本机原件保留";
    byId("save-state").classList.toggle(
      "sync-warning",
      !!(
        latest.conflicted ||
        latest.pendingCount ||
        latest.serverPending ||
        photoPending
      ),
    );
    if (view === "exam") showView("exam");
    window.Insights?.applyLock?.(); // a short bank keeps the start button grey until a paper exists
  }
  window.__hasPaper = () => !!latest.paper;
  function rendered(next) {
    state(next);
  }
  function clock(remaining) {
    byId("focus-toolbar").classList.toggle(
      "time-warning",
      remaining <= 600 && remaining > 120,
    );
    byId("focus-toolbar").classList.toggle("time-critical", remaining <= 120);
  }
  async function read(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`读取失败（${response.status}）`);
    return response.json();
  }
  async function loadOverview() {
    await window.Insights?.loadHome();
  }
  function signedIn(user) {
    const changed = account !== user;
    account = user;
    if (changed || Date.now() - lastLoad > 30000) {
      lastLoad = Date.now();
      void loadOverview();
    }
  }
  function result(data) {
    if (resultPaper !== data.paper_id || view === "exam") showView("exam");
    resultPaper = data.paper_id;
    document.body.classList.remove("focus-mode");
    byId("focus-actions").hidden = true;
    byId("exam-intro").hidden = true;
    byId("result-bar").hidden = false;
    const day = latest.paper?.date ? new Date(latest.paper.date + "T00:00:00") : null;
    byId("result-title").textContent = day ? `${day.getMonth() + 1} 月 ${day.getDate()} 日训练卷` : "训练卷成绩";
    const summary = make("div", undefined, undefined, "result-analytics");
    const key = make("section", undefined, summary, "card key-card result-key");
    const types = make("section", undefined, summary, "card data-card");
    const pending = data.pending;
    if (pending) {
      // Grading is still running: say how far it is (never a made-up total).
      const graded = data.answers.filter((a) => a.points !== null || a.excluded).length;
      const progress = make("section", undefined, summary, "card grade-card");
      progress.setAttribute("role", "group");
      progress.setAttribute("aria-label", `已判分${graded}/${data.answers.length}题，待判分${pending}题`);
      const info = make("div", undefined, progress);
      make("span", "判分中", info, "pill blue-pill");
      make("div", `${pending}题待判分`, info, "grade-title");
      make("p", `已判分 ${graded}/${data.answers.length} 题，已得 ${data.graded_points} 分；完整总分尚未确定。`, info, "grade-caption");
    }
    void window.Insights?.resultExtras(data, { key, types });
    // the per-question details, in one card with an "only the lost ones" switch
    const detail = make("section", undefined, undefined, "card detail-card");
    const head = make("div", undefined, detail, "card-heading");
    make("h2", "逐题详情", head);
    const toggle = make("div", undefined, head, "detail-toggle");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "逐题详情筛选");
    const holder = make("div", undefined, detail, "detail-list");
    const modes = [["all", "全部"], ["lost", "只看失分题"]];
    const apply = (mode) => {
      toggle.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mode === mode)));
      holder.querySelectorAll(":scope > article").forEach((article) => {
        const answer = data.answers[Number(article.dataset.index)];
        article.hidden = mode === "lost" && !(answer && !answer.excluded && (answer.points === null || answer.points < answer.max_points));
      });
    };
    modes.forEach(([mode, label]) => {
      const button = make("button", label, toggle);
      button.type = "button";
      button.dataset.mode = mode;
      button.onclick = () => apply(mode);
    });
    byId("result").prepend(summary);
    byId("result")
      .querySelectorAll(":scope > article")
      .forEach((article, index) => {
        const answer = data.answers[index];
        article.dataset.index = String(index);
        if (answer) {
          const details = make("details"),
            title = make("summary", undefined, details, "result-question-summary");
          make("span", `第${index + 1}题 · ${labels[answer.subject]} · ${typeNames[answer.question_type] || ""}`, title);
          make(
            "span",
            answer.excluded ? "已反馈 · 不计分" : answer.points === null ? "待判分" : `${answer.points}/${answer.max_points}分`,
            title,
          );
          const body = make("div", undefined, details);
          while (article.firstChild) body.append(article.firstChild);
          article.append(details);
        }
        holder.append(article);
      });
    summary.after(detail);
    apply("all");
    lastLoad = 0;
    if (account) signedIn(account);
  }
  function weekly(chapters) {
    const root = byId("learning");
    root.replaceChildren();
    make("h2", "近7天章节统计", root);
    make(
      "p",
      "按已判分得分和已判分满分统计；未答0分计入，待批题不计入。",
      root,
      "analysis-note",
    );
    if (!chapters.length) {
      const empty = make("section", undefined, root, "card empty-state");
      make("strong", "还没有可分析的答卷", empty);
      make("p", "完成考试后，这里会出现真实章节表现。", empty);
      return;
    }
    chapters.forEach((row) => {
      const card = make("section", undefined, root, "card subject-row");
      make("h3", row.chapter || "未标注章节", card);
      make(
        "p",
        `${labels[row.subject]} · ${row.answers}条作答记录 · ${row.pending}题待判分`,
        card,
        "analysis-note",
      );
      const data = {
        points: Number(row.graded_points || 0),
        max: Number(row.graded_max_points || 0),
        pending: Number(row.pending || 0),
      };
      const heading = make("div", undefined, card, "subject-label");
      make("span", "已判分表现", heading);
      make(
        "span",
        data.max
          ? `${data.points}/${data.max}分 · ${Math.round((data.points / data.max) * 100)}%`
          : "暂无已判分数据",
        heading,
      );
      const track = make("div", undefined, card, "data-track"),
        fill = make("div", undefined, track, "data-fill");
      fill.style.width = `${data.max ? Math.min(100, (data.points / data.max) * 100) : 0}%`;
    });
  }
  // Unanswered (not reported) questions and those marked "look again", in paper order.
  function submitCheck() {
    const questions = latest.paper?.questions || [];
    const reported = new Set(latest.paper?.reported_question_ids || []);
    const open = [],
      marked = [];
    questions.forEach((question, index) => {
      if (reported.has(question.id)) return;
      const draft = latest.drafts[question.id];
      if (!answered(draft)) open.push(index);
      if (draft?.flagged) marked.push(index);
    });
    return { open, marked };
  }
  function confirmSubmit() {
    const dialog = byId("submit-dialog");
    const check = submitCheck();
    const warning = byId("submit-warning"),
      jump = byId("jump-unanswered");
    const parts = [];
    if (check.open.length) parts.push(`还有 ${check.open.length} 题未答`);
    if (check.marked.length) parts.push(`${check.marked.length} 题标记稍后看`);
    warning.hidden = !parts.length;
    warning.textContent = parts.join("、");
    jump.hidden = !check.open.length;
    jump.onclick = () => {
      dialog.close();
      showView("exam");
      navigate(check.open[0]);
    };
    byId("submit-summary").textContent =
      byId("map-summary").textContent +
      (latest.pendingCount
        ? `；${latest.pendingCount}题待同步，交卷前将尝试保存。`
        : "；已同步草稿将在交卷时提交。");
    dialog.showModal();
    return new Promise((resolve) => {
      let accepted = false;
      byId("confirm-submit").onclick = () => {
        accepted = true;
        dialog.close();
      };
      byId("cancel-submit").onclick = () => dialog.close();
      dialog.addEventListener("close", () => resolve(accepted), { once: true });
    });
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.onclick = () => showView(button.dataset.view);
  });
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.onclick = () => byId(button.dataset.action).click();
  });
  document.querySelector("[data-prepare-proxy]").onclick = () =>
    byId("prepare").click();
  ["memory", "weekly", "history", "insights", "archive", "source-status"].forEach((id) =>
    byId(id).addEventListener("click", () =>
      showView(
        id === "memory" ? "mistakes" : id === "archive" ? "profile" : "stats",
      ),
    ),
  );
  // Messages appear as a short toast at the top, not as a permanent status line on the page.
  const statusEl = byId("status");
  let statusTimer = 0;
  new MutationObserver(() => {
    if (!statusEl.textContent.trim()) return statusEl.classList.remove("show");
    statusEl.classList.add("show");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => statusEl.classList.remove("show"), statusEl.textContent.trim() === "已登录" ? 2000 : 5000);
  }).observe(statusEl, { childList: true, characterData: true, subtree: true });
  byId("result-back").onclick = () => showView("home");
  byId("stats-back").onclick = () => showView("profile");
  byId("leave-focus").onclick = () => {
    showView("home");
    byId("status").textContent = "已返回概览，考试计时仍在继续。";
  };
  byId("previous-question").onclick = () => navigate(position - 1);
  byId("next-question").onclick = () =>
    position >= (latest.paper?.questions.length || 0) - 1
      ? byId("question-dialog").showModal()
      : navigate(position + 1);
  byId("toggle-mark").onclick = () =>
    document
      .querySelector("#questions article:not([hidden]) .question-flag")
      ?.click();
  byId("question-map").onclick = () => byId("question-dialog").showModal();
  byId("map-submit").onclick = () => {
    byId("question-dialog").close();
    byId("submit").click();
  };
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.onclick = () => byId(button.dataset.close).close();
  });
  document.addEventListener("keydown", (event) => {
    const option = event.target.closest?.(".option");
    if (
      !option ||
      !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)
    )
      return;
    event.preventDefault();
    const options = Array.from(
      option.parentElement.querySelectorAll(".option"),
    );
    const next =
      options[
        (options.indexOf(option) +
          (["ArrowUp", "ArrowLeft"].includes(event.key)
            ? options.length - 1
            : 1)) %
          options.length
      ];
    next.focus();
    next.click();
  });
  const connection = () => {
    byId("connection-state").classList.toggle("offline", !navigator.onLine);
    byId("connection-state").lastChild.textContent = navigator.onLine
      ? "在线"
      : "离线暂存";
  };
  window.addEventListener("online", connection);
  window.addEventListener("offline", connection);
  connection();
  if (window.visualViewport) {
    const viewport = () =>
      document.body.classList.toggle(
        "keyboard-open",
        window.innerHeight - window.visualViewport.height > 150,
      );
    window.visualViewport.addEventListener("resize", viewport);
  }
  byId("logout").onclick = async () => {
    if (
      active() &&
      !confirm("退出后草稿仍保留，但考试计时不会暂停。确认退出？")
    )
      return;
    await actions.flush?.();
    if (latest.pendingCount || latest.conflicted) {
      byId("status").textContent = "请先完成同步或处理版本冲突，再退出。";
      return;
    }
    const response = await fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    if (!response.ok) {
      byId("status").textContent = "退出失败，请联网后重试。";
      return;
    }
    sessionStorage.removeItem("quiz-v2-user");
    sessionStorage.removeItem("quiz-v2-paper-key");
    location.reload();
  };
  window.TrainingUI = {
    state,
    rendered,
    clock,
    signedIn,
    cachedIdentity: (user) => {
      account = user;
    },
    result,
    weekly,
    // the mark button lives inside the shown question; park it before the questions are rebuilt
    parkMarkButton: () => document.querySelector(".focus-secondary")?.append(byId("toggle-mark")),
    confirmSubmit,
    actions: (value) => {
      actions = value;
    },
    showView,
  };
  showView("home");
})();
