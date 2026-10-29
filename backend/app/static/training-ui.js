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
    const roots = {
      mistakes: "mistakes-content",
      stats: "stats-content",
      profile: "profile-content",
    };
    if (roots[view]) byId(roots[view]).append(byId("learning"));
    else byId("learning").hidden = true;
    if (roots[view]) byId("learning").hidden = false;
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
    byId("current-points").textContent = `${question.max_points}分`;
    byId("previous-question").disabled = position === 0;
    byId("next-question").textContent =
      position === questions.length - 1 ? "检查答题卡" : "下一题 →";
    const marked = !!latest.drafts[question.id]?.flagged;
    byId("toggle-mark").setAttribute("aria-pressed", String(marked));
    byId("toggle-mark").textContent = marked ? "★ 已标记" : "☆ 标记";
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
    let count = 0,
      marked = 0;
    questions.forEach((question, index) => {
      const button = grid.children[index],
        draft = latest.drafts[question.id],
        done = answered(draft),
        flag = !!draft?.flagged;
      count += Number(done);
      marked += Number(flag);
      button.classList.toggle("answered", done);
      button.classList.toggle("marked", flag);
      if (position === index) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.setAttribute(
        "aria-label",
        `第${index + 1}题，${done ? "已作答" : "未作答"}${flag ? "，已标记" : ""}${position === index ? "，当前题" : ""}`,
      );
    });
    byId("answer-progress").value = count;
    byId("map-progress").textContent = `${count}/${questions.length || 28}`;
    byId("map-summary").textContent =
      `已答${count}题 · 未答${Math.max(0, questions.length - count)}题 · 已标记${marked}题`;
  }
  function state(next) {
    const previousPaper = latest.paper?.paper_id;
    latest = next;
    const timing = latest.paper?.timing;
    byId("paper-state").textContent = timing
      ? {
          not_started: "尚未开始",
          in_progress: "正在作答",
          submitted: "已提交",
        }[timing.status] || "状态待确认"
      : "状态待载入";
    byId("paper-state").classList.toggle(
      "green-pill",
      timing?.status === "submitted",
    );
    byId("prepare").firstChild.textContent = active()
      ? "继续本次考试 "
      : timing?.status === "submitted"
        ? "查看本次结果 "
        : "开始今日考试 ";
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
  }
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
  function subjectBars(root, subjects) {
    root.replaceChildren();
    Object.entries(labels).forEach(([subject, label]) => {
      const data = subjects[subject] || { points: 0, max: 0, pending: 0 };
      const row = make("div", undefined, root, "subject-row"),
        heading = make("div", undefined, row, "subject-label");
      make("span", label, heading);
      make(
        "span",
        data.max > 0
          ? `${data.points} / ${data.max}分 · ${Math.round((data.points / data.max) * 100)}%`
          : "暂无已判分记录",
        heading,
      );
      const track = make("div", undefined, row, "data-track");
      track.setAttribute("role", "img");
      track.setAttribute(
        "aria-label",
        `${label}：${data.max > 0 ? `已判分${data.points}/${data.max}分` : "暂无已判分数据"}；待批${data.pending}题`,
      );
      const fill = make(
        "div",
        undefined,
        track,
        `data-fill ${subject === "cell_biology" ? "cell" : ""}`,
      );
      fill.style.width = `${data.max > 0 ? Math.min(100, Math.max(0, (data.points / data.max) * 100)) : 0}%`;
      if (data.pending)
        make(
          "small",
          `${data.pending}题待批，未计入得分率`,
          row,
          "card-footnote",
        );
    });
  }
  function trend(root, history) {
    const rows = history
      .filter((row) => Number.isFinite(row.score))
      .slice(0, 10)
      .reverse();
    root.replaceChildren();
    if (rows.length < 2) {
      root.className = "empty-state";
      make("span", "↗", root, "empty-icon").setAttribute(
        "aria-hidden",
        "true",
      );
      make("strong", "让进步留下轨迹", root);
      make(
        "p",
        rows.length
          ? "已有1份完整判分记录，再完成一份即可查看真实趋势。"
          : "完成考试后查看成绩趋势。待批改成绩不绘入曲线。",
        root,
      );
      return;
    }
    root.className = "";
    const ns = "http://www.w3.org/2000/svg",
      svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 360 180");
    svg.setAttribute("role", "img");
    svg.classList.add("chart");
    const description = rows
      .map((r) => `${r.paper_date}：${r.score}/150分`)
      .join("；");
    const title = document.createElementNS(ns, "title");
    title.textContent = `真实完整判分成绩：${description}`;
    svg.append(title);
    const points = rows.map((r, i) => [
      32 + (i * 304) / (rows.length - 1),
      148 - (r.score / 150) * 116,
    ]);
    [0, 75, 150].forEach((value) => {
      const y = 148 - (value / 150) * 116,
        line = document.createElementNS(ns, "line");
      Object.entries({
        x1: 32,
        x2: 344,
        y1: y,
        y2: y,
        stroke: "#e7ebf0",
      }).forEach(([k, v]) => line.setAttribute(k, v));
      svg.append(line);
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", "0");
      text.setAttribute("y", String(y + 4));
      text.textContent = String(value);
      svg.append(text);
    });
    const polyline = document.createElementNS(ns, "polyline");
    Object.entries({
      points: points.map((p) => p.join(",")).join(" "),
      fill: "none",
      stroke: "#2563eb",
      "stroke-width": 2.5,
      "stroke-linejoin": "round",
    }).forEach(([k, v]) => polyline.setAttribute(k, v));
    svg.append(polyline);
    points.forEach(([x, y]) => {
      const circle = document.createElementNS(ns, "circle");
      Object.entries({
        cx: x,
        cy: y,
        r: 4,
        fill: "#fff",
        stroke: "#2563eb",
        "stroke-width": 2,
      }).forEach(([k, v]) => circle.setAttribute(k, v));
      svg.append(circle);
    });
    root.append(svg);
    make("p", description, root, "chart-description");
    make(
      "p",
      "仅含正式28题、150分试卷的完整判分成绩；待批与旧制60分记录不混算。",
      root,
      "card-footnote",
    );
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
    const results = await Promise.allSettled([
      read("/v2/history"),
      read("/v2/stats/weekly"),
      read("/v2/memory/due"),
    ]);
    if (results[0].status === "fulfilled") {
      const records = results[0].value.training;
      byId("metric-submitted").textContent = String(records.length);
      byId("metric-graded").textContent = String(
        records.filter((r) => Number.isFinite(r.score)).length,
      );
      const pending = records.filter((r) => r.score === null).length;
      byId("overview-note").textContent = records.length
        ? `${pending}份试卷待完成批改；提交不等于完整判分。统计范围为最近100份正式档案。`
        : "尚无提交记录。完成第一份考试，开始积累你的学习轨迹。";
      trend(byId("score-trend"), records);
    } else
      byId("overview-note").textContent =
        "历史记录暂时读取失败，未将未知数据当作0。";
    if (results[1].status === "fulfilled") {
      const subjects = {},
        chapters = results[1].value.chapters;
      chapters.forEach((row) => {
        const total = (subjects[row.subject] ||= {
          points: 0,
          max: 0,
          pending: 0,
        });
        total.points += Number(row.graded_points || 0);
        total.max += Number(row.graded_max_points || 0);
        total.pending += Number(row.pending || 0);
      });
      subjectBars(byId("subject-performance"), subjects);
      const weak = chapters
        .filter(
          (r) => Number(r.graded_max_points) > Number(r.graded_points || 0),
        )
        .sort(
          (a, b) =>
            b.graded_max_points -
            b.graded_points -
            (a.graded_max_points - a.graded_points),
        )
        .slice(0, 4);
      if (weak.length) {
        const root = byId("weak-chapters");
        root.replaceChildren();
        root.className = "weak-list";
        weak.forEach((row) => {
          const button = make("button", undefined, root, "weak-chip"),
            label = make("span", row.chapter || "未标注章节", button);
          make("small", `${labels[row.subject]} · 近7天已判分失分`, label);
          make(
            "b",
            `${row.graded_max_points - (row.graded_points || 0)}分待巩固 →`,
            button,
          );
          button.onclick = () => byId("weekly").click();
        });
      }
    } else
      byId("subject-performance").textContent =
        "科目统计暂时不可用，请稍后重试。";
    if (results[2].status === "fulfilled") {
      const count = results[2].value.items.length;
      byId("metric-due").textContent = String(count);
      byId("review-summary").textContent = count
        ? `当前有${count}道错题到期，复习后更新实际FSRS安排。`
        : "当前没有到期复习，完成练习后会根据真实错题建立卡片。";
    } else
      byId("review-summary").textContent = "到期卡片读取失败，暂不显示数量。";
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
    const summary = make("div", undefined, undefined, "result-analytics"),
      grade = make("section", undefined, summary, "card grade-card");
    const graded = data.answers.filter((a) => a.points !== null).length;
    const ns = "http://www.w3.org/2000/svg",
      ring = document.createElementNS(ns, "svg");
    ring.setAttribute("viewBox", "0 0 100 100");
    ring.classList.add("grade-ring");
    ring.setAttribute("role", "img");
    ring.setAttribute(
      "aria-label",
      `已判分${graded}/28题，待批${data.pending}题`,
    );
    const total = document.createElementNS(ns, "circle"),
      done = document.createElementNS(ns, "circle");
    [total, done].forEach((circle) => {
      Object.entries({
        cx: 50,
        cy: 50,
        r: 40,
        fill: "none",
        "stroke-width": 6,
      }).forEach(([k, v]) => circle.setAttribute(k, v));
      ring.append(circle);
    });
    total.setAttribute("stroke", "#edf1f5");
    done.setAttribute("stroke", "#2563eb");
    done.setAttribute("stroke-dasharray", `${(graded / 28) * 251.3} 251.3`);
    done.setAttribute("transform", "rotate(-90 50 50)");
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", "50");
    text.setAttribute("y", "49");
    text.setAttribute("text-anchor", "middle");
    text.textContent = `${graded}/28`;
    ring.append(text);
    const caption = document.createElementNS(ns, "text");
    caption.setAttribute("x", "50");
    caption.setAttribute("y", "64");
    caption.setAttribute("text-anchor", "middle");
    caption.classList.add("small-text");
    caption.textContent = "已判分题目";
    ring.append(caption);
    grade.append(ring);
    const info = make("div", undefined, grade);
    make("span", data.pending ? "待批改" : "批改完成", info, "pill blue-pill");
    make(
      "div",
      data.score === null
        ? `${data.pending}题待批改`
        : `${data.score} / ${data.max_points}分`,
      info,
      "grade-title",
    );
    make(
      "p",
      data.score === null
        ? `已评分得分 ${data.graded_points}；完整总分尚未确定。`
        : "本卷所有题目均已判分。",
      info,
      "grade-caption",
    );
    const subject = make("section", undefined, summary, "card");
    make("h2", "学科表现", subject);
    const bars = make("div", undefined, subject);
    const scores = {};
    data.answers.forEach((answer) => {
      const score = (scores[answer.subject] ||= {
        points: 0,
        max: 0,
        pending: 0,
      });
      if (answer.points === null) score.pending++;
      else {
        score.points += answer.points;
        score.max += answer.max_points;
      }
    });
    subjectBars(bars, scores);
    make(
      "p",
      "得分率仅含已判分题目，未答0分计入，待批题目排除。",
      subject,
      "card-footnote",
    );
    const coverage = make("section", undefined, summary, "card");
    make("h2", "知识章节分布", coverage);
    const chapters = new Map();
    data.answers.forEach((answer) => {
      new Set(answer.evidence.map((e) => e.chapter).filter(Boolean)).forEach(
        (chapter) => chapters.set(chapter, (chapters.get(chapter) || 0) + 1),
      );
    });
    Array.from(chapters)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .forEach(([chapter, count]) => {
        const row = make("div", undefined, coverage, "subject-row"),
          heading = make("div", undefined, row, "subject-label");
        make("span", chapter, heading);
        make("span", `${count}题涉及`, heading);
        const track = make("div", undefined, row, "data-track"),
          fill = make("div", undefined, track, "data-fill");
        fill.style.width = `${(count / data.answers.length) * 100}%`;
      });
    make(
      "p",
      "按每题引用的章节去重计数；一题可覆盖多个章节。这是本卷覆盖分布，不是掌握率。",
      coverage,
      "card-footnote",
    );
    const reflection = make("section", undefined, summary, "card");
    make("h2", "错题与复盘", reflection);
    const isAnswered = (answer) => {
      try {
        return answered(JSON.parse(answer.answer));
      } catch {
        return !!answer.answer?.trim();
      }
    };
    const wrong = data.answers.filter(
      (a) => a.points !== null && a.points < a.max_points && isAnswered(a),
    );
    const blank = data.answers.filter((a) => !isAnswered(a)).length;
    make("div", `${wrong.length}道已作答失分题`, reflection, "grade-title");
    make(
      "p",
      `另有${blank}道未答题、${data.pending}道待批题，未混入已作答错题。`,
      reflection,
      "grade-caption",
    );
    make(
      "p",
      "暂无可汇总的错因分类；请在具体题目中记录真实复盘原因，不从分数推测错因。",
      reflection,
      "card-footnote",
    );
    const review = make(
      "button",
      "查看实际FSRS复习安排 →",
      reflection,
      "text-button",
    );
    review.onclick = () => byId("memory").click();
    byId("result").prepend(summary);
    byId("result")
      .querySelectorAll(":scope > article")
      .forEach((article, index) => {
        const answer = data.answers[index];
        if (!answer) return;
        const details = make("details"),
          title = make(
            "summary",
            undefined,
            details,
            "result-question-summary",
          );
        make(
          "span",
          `第${index + 1}题 · ${labels[answer.subject]} · 查看作答与解析`,
          title,
        );
        make(
          "span",
          answer.points === null
            ? "待批改"
            : `${answer.points}/${answer.max_points}分`,
          title,
        );
        const body = make("div", undefined, details);
        while (article.firstChild) body.append(article.firstChild);
        article.append(details);
      });
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
        `${labels[row.subject]} · ${row.answers}条作答记录 · ${row.pending}题待批`,
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
  function confirmSubmit() {
    const dialog = byId("submit-dialog");
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
  ["memory", "weekly", "history", "insights", "archive"].forEach((id) =>
    byId(id).addEventListener("click", () =>
      showView(
        id === "memory" ? "mistakes" : id === "archive" ? "profile" : "stats",
      ),
    ),
  );
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
  byId("today-label").textContent =
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "long",
      day: "numeric",
      weekday: "long",
    }).format(new Date()) + " · 学习工作台";
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
    confirmSubmit,
    actions: (value) => {
      actions = value;
    },
    showView,
  };
  showView("home");
})();
