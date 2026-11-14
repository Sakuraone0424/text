/* Presentation only: existing training.js owns answers, timing, sync and submission. */
"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const n2 = (v) => (typeof v === "number" ? Math.round(v * 100) / 100 : v);   // points are exact inside, shown with at most two decimals
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
  // Only the content area scrolls (the document does not): remember where each route was left.
  const scroller = () => document.querySelector(".workspace");
  const scrollMemory = {};
  let currentKey = null;
  const PARENT = { progress: "profile", model: "profile", compare: "profile", stats: "profile", mistakes: "home", practice: "home", exam: "practice", profile: "home", review: "stats", mistake: "mistakes", drill: "practice" };
  const NAV_FOR = { stats: "profile", progress: "profile", model: "profile", compare: "profile", exam: "practice", review: "profile", mistake: "mistakes", drill: "practice" };
  // how deep a route is: the four tabs are 0, their sub-pages 1, a sub-page's own sub-page 2
  const TABS = ["home", "practice", "mistakes", "profile"];
  const depthOf = (name, sub) => (TABS.includes(name) ? 0 : 1) + (sub ? 1 : 0);
  const pageOf = (name) => document.querySelector(`[data-page="${name}"]`);
  const PARAM_VIEWS = ["review", "mistake", "drill"];          // their last part is a parameter (p123 = paper 123, m7 = mistake 7), not a sub-page
  const hashSub = (key) => {
    const m = /^#\/([a-z]+)\/([a-z0-9-]+)/.exec(key || "");
    return m && !PARAM_VIEWS.includes(m[1]) ? m[2] : null;
  };
  function renderView(next, key) {
    if (currentKey && scroller()) scrollMemory[currentKey] = scroller().scrollTop;
    const before = { view, key: currentKey };
    view = next;
    currentKey = key || "#/" + next;
    document.body.dataset.view = next; // the install hint belongs to the home page only
    document.querySelectorAll("[data-page]").forEach((node) => {
      node.hidden = node.dataset.page !== view;
    });
    document.querySelectorAll("[data-view]").forEach((node) => {
      if (node.dataset.view === (NAV_FOR[view] || view)) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    document.body.classList.toggle("focus-mode", (view === "exam" && active()) || view === "drill");       // full-screen answering: no bottom navigation
    byId("focus-actions").hidden = view !== "exam" || !active();
    byId("question-tags").hidden = view !== "exam" || !active();
    if (view !== "exam") flushDwell();
    const roots = {
      mistakes: "mistakes-content",
      stats: "stats-content",
      profile: "profile-content",
    };
    // `#learning` holds the content of ONE sub-page (weekly, history, insights, archive, source check, due review). It
    // belongs to the route that opened it: any other route finds it empty, so a sub-page's cards can never show up
    // at the bottom of a main page.
    const learning = byId("learning");
    if (learning.dataset.owner !== currentKey) learning.replaceChildren();
    if (roots[view]) byId(roots[view]).append(learning);
    learning.hidden = !roots[view] || !learning.hasChildNodes();
    window.Insights?.onView(view);
    if (view === "progress") window.ProgressView?.show();
    if (view === "model") {
      window.ModelView?.show();
      window.ExecutorSettings?.show();
    }
    if (view === "compare") window.CompareView?.show();
    if (scroller()) scroller().scrollTop = scrollMemory[currentKey] || 0;
    // motion: a sub-page slides in from the right, going back fades the page below in (the one above slid out first),
    // moving between the four tabs cross-fades. Nothing plays when the same route is merely re-applied.
    if (window.Motion && before.key && before.key !== currentKey && !document.hidden) {
      const from = depthOf(before.view, hashSub(before.key)), to = depthOf(next, hashSub(currentKey));
      const kind = TABS.includes(before.view) && TABS.includes(next) ? "tab" : to > from ? "push" : to < from ? "pop" : "tab";
      window.Motion.enter(pageOf(next), kind);
    }
    window.Motion?.cards(pageOf(next));
  }

  /* ---- routes: one shell, hash URLs, the browser's own back stack ---- */
  const VIEWS = ["home", "practice", "exam", "mistakes", "mistake", "review", "drill", "stats", "profile", "progress", "model", "compare"];
  const SUBS = ["weekly", "history", "insights", "archive", "source-status"];
  let routeChanges = 0;
  let suppressRoute = false;
  let alreadyLoaded = null; // the hash whose page content was just loaded by the click that navigated to it
  const parseHash = () => {
    const m = /^#\/([a-z]+)(?:\/([a-z0-9-]+))?/.exec(location.hash);
    return m ? { view: VIEWS.includes(m[1]) ? m[1] : "home", sub: m[2] || null } : { view: "home", sub: null };
  };
  function apply() {
    const { view: next, sub } = parseHash();
    const key = location.hash || "#/home";
    if (view === "model" && next !== "model" && window.ModelView && !window.ModelView.leave()) {
      history.replaceState(null, "", "#/model"); // unsaved settings: stay
      return;
    }
    routeChanges += 1;
    window.Data?.setUser(account);
    renderView(next, key);
    window.Pages?.show(next, sub);                      // the pages built in pages.js (practice centre, mistake book, review)
    if (sub && SUBS.includes(sub)) {
      byId("learning").dataset.owner = key;
      if (alreadyLoaded === key) alreadyLoaded = null;
      else {
        suppressRoute = true;
        byId(sub).click();
        suppressRoute = false;
      }
    }
  }
  function go(next, sub = null, { replace = false } = {}) {
    const target = "#/" + next + (sub ? "/" + sub : "");
    if (location.hash === target || (!location.hash && target === "#/home")) return apply();
    if (replace) {
      history.replaceState(null, "", target);
      apply();
    } else location.hash = target;
  }
  function backNow() {
    // the previous page of this session if there is one (its scroll position and filters are still there), else its parent
    if (routeChanges > 1 && history.length > 1) history.back();
    else go(PARENT[view] || "home", null, { replace: true });
  }
  // the in-app back button: the navigation is immediate; a picture of the page slides out to the right over the one below
  function back() {
    const node = pageOf(view);
    if (window.Motion && node && !TABS.includes(view)) window.Motion.leave(node, 0);
    backNow();
  }
  window.Motion?.edgeSwipe({
    canGoBack: () => !TABS.includes(view) || !!hashSub(location.hash),
    pageNode: () => pageOf(view),
    back: backNow,
  });
  window.addEventListener("hashchange", apply);
  window.addEventListener("signed-in", apply);
  window.addEventListener("shell-ready", apply);
  const showView = (next) => go(next);
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
      const show = Number(article.dataset.id) === question.id;
      if (show && article.hidden && !window.Motion?.reduced()) {
        // moving between questions: a 120 ms fade-in only (no sliding, no page turn)
        article.classList.remove("motion-fade");
        void article.offsetWidth;
        article.classList.add("motion-fade");
      }
      article.hidden = !show;
    });
    byId("current-number").textContent = String(position + 1);
    byId("current-subject").textContent =
      labels[question.subject] || question.subject;
    byId("current-points").textContent = `${typeNames[question.question_type] || ""} · ${n2(question.max_points)}分` + (question.scoring_note ? ` · ${question.scoring_note}` : "");
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
      scroller()?.scrollTo({ top: 0, behavior: "instant" });
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
  const PAPER_TEXT = {
    objective: {
      name: "客观题卷", spec: (m) => `60 道填空 · 150 分 · ${m} 分钟`, note: "全部是填空，每题 2.5 分（多空题按空平分），交卷后立即出分",
      blueprint: ["每科 30 道填空", "每题 2.5 分", "生物化学、细胞生物学各 75 分"],
    },
    subjective: {
      name: "主观题卷", spec: (m) => `14 题 · 150 分 · ${m} 分钟`, note: "名词解释 5 分、简答 15 分、问答 25 分，AI 判分，也可手动判分",
      blueprint: ["每科 4 道名词解释", "2 道简答", "1 道问答", "生物化学、细胞生物学各 75 分"],
    },
    mixed: {
      name: "旧版混合卷", spec: () => "旧版混合卷，只读", note: "不再生成新卷，已有的卷子仍可回看",
      blueprint: ["旧版规格"],
    },
  };
  function state(next) {
    const previousPaper = latest.paper?.paper_id;
    latest = next;
    const timing = latest.paper?.timing;
    const count = latest.paper?.count || 60;
    const totalIndicator = document.querySelector("#current-number + .muted");
    if (totalIndicator) totalIndicator.textContent = " / " + count;
    if (timing) window.Insights?.startReady?.();       // today's paper exists: its own state is known
    window.Pages?.paintToday();                        // the home card and the practice card show one state, from one place
    const meta = PAPER_TEXT[latest.paper?.kind];
    const minutes = Math.round((timing?.duration_seconds || latest.paper?.duration_seconds || 3600) / 60);
    // the paper on the exam page says what it is (kind, size, points, time) in the intro, the start button and the confirm dialog
    if (meta) {
      byId("intro-title").textContent = `${meta.name}：为这 ${minutes} 分钟，留一点专注。`;
      byId("intro-spec").textContent = `${meta.spec(minutes)}；${meta.note}`;
      const list = byId("intro-blueprint");
      list.replaceChildren(...meta.blueprint.map((text) => Object.assign(document.createElement("span"), { textContent: text })));
      byId("open-start").textContent = `开始（${minutes} 分钟）`;
      byId("start-title").textContent = `开始${meta.name}`;
      byId("start-spec").textContent = meta.spec(minutes);
      byId("start-minutes").textContent = String(minutes);
    }
    const note = document.querySelector("#paper-card-" + (latest.paper?.kind || "") + " .micro-note");
    if (note && active()) note.textContent = "考试计时继续，已存答案不会丢失";
    byId("exam-intro").hidden = !!timing && timing.status !== "not_started";
    byId("intro-state").textContent =
      timing?.status === "not_started"
        ? "试卷已准备好，开始后计时不会暂停。"
        : "请先准备试卷。";
    byId("open-start").hidden = timing?.status !== "not_started";
    document.querySelector("[data-prepare-proxy]").hidden = !!latest.paper;
    byId("result").hidden = timing?.status !== "submitted";
    byId("result-bar").hidden = timing?.status !== "submitted";
    byId("questions").hidden = !active();
    byId("questions").inert = !active() || !!latest.expired || document.body.classList.contains("is-submitting");
    byId("submit").disabled = !!latest.expired || !!latest.serverPending || document.body.classList.contains("is-submitting");
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
            ? "已保存"
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
      make("p", `已判分 ${graded}/${data.answers.length} 题，已得 ${n2(data.graded_points)} 分；完整总分尚未确定。`, info, "grade-caption");
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
            answer.excluded ? "已反馈 · 不计分" : answer.points === null ? "待判分" : `${n2(answer.points)}/${n2(answer.max_points)}分`,
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
          ? `${n2(data.points)}/${n2(data.max)}分 · ${Math.round((data.points / data.max) * 100)}%`
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
  // a sub-page that finishes loading after the user already left its route must not draw itself on the new page
  new MutationObserver(() => {
    const learning = byId("learning");
    if (learning.hasChildNodes() && learning.dataset.owner !== currentKey) learning.replaceChildren();
    learning.hidden = !learning.hasChildNodes();
  }).observe(byId("learning"), { childList: true });
  document.querySelectorAll("button[data-view]").forEach((button) => {
    button.onclick = () => go(button.dataset.view);
  });
  document.querySelectorAll("[data-back]").forEach((button) => {
    button.onclick = back;
  });
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.onclick = () => byId(button.dataset.action).click();
  });
  document.querySelector("[data-prepare-proxy]").onclick = () => go("home");
  byId("open-start").onclick = () => byId("start-dialog").showModal();
  byId("cancel-start").onclick = () => byId("start-dialog").close();
  ["memory", "weekly", "history", "insights", "archive", "source-status"].forEach((id) =>
    byId(id).addEventListener("click", () => {
      if (suppressRoute) return; // a back/forward navigation re-opening this page: the URL is already right
      if (id === "memory") {
        byId("learning").dataset.owner = "#/mistakes";
        return go("mistakes");
      }
      alreadyLoaded = id === "archive" ? "#/profile/archive" : "#/stats/" + id;
      byId("learning").dataset.owner = alreadyLoaded;
      if (id === "archive") go("profile", "archive");
      else go("stats", id);
    }),
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
  byId("result-back").onclick = () => go("home");
  byId("stats-back").onclick = back;
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
  // (the keyboard-open class is kept by shell.js)
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
    go,
    back,
    today: () => ({
      status: latest.paper?.timing?.status || null,
      hasPaper: !!latest.paper,
      clock: byId("clock")?.textContent || "",
      paperId: latest.paper?.paper_id || null,
      kind: latest.paper?.kind || null,
    }),
  };
  apply();
})();
