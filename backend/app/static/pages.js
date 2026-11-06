"use strict";
/* The pages added in this round, one place for all of them: the practice centre (today's paper card shared with the home card, six
   kinds of practice, recent trainings), the mistake book and a mistake's detail page, and the whole-paper review.
   Numbers come from /v2/practice/overview, /v2/training/recent, /v2/mistakes/book, /v2/mistakes/{id}, /v2/papers/{id}/review and
   /v2/practice/{id}/review; nothing is invented. Reads go through Data.swr: what was seen before is shown at once. */
(() => {
  const $ = (id) => document.getElementById(id);
  const SHORT = { biochemistry: "生化", cell_biology: "细胞" };
  const SUBJECT_COLOR = { biochemistry: "var(--chart-bio)", cell_biology: "var(--chart-cell)" };
  const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
  const TYPES = Object.keys(TYPE);
  const el = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const mmss = (seconds) => (seconds == null ? "" : `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`);
  const md = (iso) => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}月${d.getDate()}日`; };
  const read = (path, render, error) => window.Data.swr(path, { render, error });
  const svgIcon = (d) => {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "ico");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    svg.append(path);
    return svg;
  };
  const failCard = (root, message, retry) => {
    root.replaceChildren();
    const box = el("div", undefined, root, "error-card");
    box.setAttribute("role", "alert");
    el("p", message, box);
    const button = el("button", "重试", box, "secondary");
    button.type = "button";
    button.onclick = retry;
  };

  /* ------------------------------------------------------------------------------------------------------------------
     Today's paper: ONE state, painted on both cards (home and practice), so they can never disagree.
     ------------------------------------------------------------------------------------------------------------------ */
  let loaded = false;
  function todayState() {
    const t = window.TrainingUI?.today?.() || {};
    const lock = window.Insights?.lockState?.();
    const mine = window.Insights?.todayPaper?.();
    if (t.status === "in_progress") return { kind: "continue", label: `继续作答（剩余 ${t.clock && /\d/.test(t.clock) ? t.clock : "--:--"}）`, pill: "正在作答" };
    if (t.status === "submitted") {
      const done = mine && mine.points !== undefined && mine.points !== null;
      return { kind: "result", label: done ? `查看成绩（${mine.points} / ${mine.max}）` : "查看成绩（判分中）", pill: "已提交", green: true };
    }
    if (t.status === "not_started") return { kind: "start", label: "开始（60 分钟）", pill: "已就绪", green: true };
    if (!loaded) return { kind: "loading", label: "开始", pill: "状态待载入" };
    if (lock && !lock.complete) {
      const when = lock.eta_state === "estimated" && lock.eta_at ? `，预计 ${clockText(lock.eta_at)} 可开始` : "，暂不能开始";
      return { kind: "short", label: `题库还差 ${lock.missing} 道${when}`, pill: "题库准备中", reason: "新题还没出够一张卷子，不会用旧题凑。" };
    }
    return { kind: "prepare", label: "准备今日试卷", pill: "可以准备" };
  }
  function clockText(iso) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
    const today = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric" }).format(new Date());
    return (today === `${parts.month}/${parts.day}` ? "" : `${parts.month} 月 ${parts.day} 日 `) + `${parts.hour}:${parts.minute}`;
  }
  let lastKind = null;
  function paintToday() {
    const s = todayState();
    lastKind = s.kind;
    const pairs = [[$("prepare"), $("paper-state"), null], [$("practice-today-btn"), $("practice-paper-state"), $("practice-today-note")]];
    for (const [button, pill, note] of pairs) {
      if (!button) continue;
      const off = s.kind === "loading" || s.kind === "short";
      button.textContent = s.label;
      button.disabled = off;
      if (off) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
      if (s.kind === "loading") button.setAttribute("data-loading", "1");
      else button.removeAttribute("data-loading");
      button.dataset.kind = s.kind;
      if (pill) {
        pill.textContent = s.pill;
        pill.classList.toggle("green-pill", Boolean(s.green));
      }
      if (note) note.textContent = s.reason || "";
    }
  }
  function todayLoaded() {
    loaded = true;
    paintToday();
  }
  async function todayClick() {
    const s = todayState();
    if (s.kind === "prepare") {
      await window.TodayPaper?.prepare();
      // the paper turned out to be sat already (another device, an earlier visit): carry on with it
      const now = todayState().kind;
      if (now === "continue" || now === "result") window.TrainingUI?.showView("exam");
      return undefined;
    }
    if (s.kind === "start") return $("start-dialog").showModal();
    if (s.kind === "continue" || s.kind === "result") return window.TrainingUI?.showView("exam");
    return undefined;
  }
  setInterval(() => { if (lastKind === "continue") paintToday(); }, 1000);          // the remaining time on the button
  $("prepare").onclick = todayClick;
  $("practice-today-btn").onclick = todayClick;
  $("cancel-start")?.addEventListener("click", () => $("start-dialog").close());

  /* ------------------------------------------------------------------------------------------------------------------
     Practice centre
     ------------------------------------------------------------------------------------------------------------------ */
  const KINDS = [
    { mode: "weak", name: "薄弱章节练习", desc: "正确率最低的章节，一套 10 题", icon: "M12 3l9 16H3L12 3zM12 10v4M12 17h.01" },
    { mode: "chapter", name: "按章节练习", desc: "选一章，一套 10 题", icon: "M4 5h16M4 12h16M4 19h10" },
    { mode: "type", name: "按题型专项", desc: "只练单选或填空，一套 10 题", icon: "M5 5h6v6H5zM13 13h6v6h-6zM13 5h6M5 15h6" },
    { mode: "retry", name: "错题重做", desc: "从错题本随机抽 10 道", icon: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5" },
    { mode: "quick", name: "随机快练", desc: "两科混合 10 道客观题，限时可选", icon: "M13 3L5 14h6l-1 7 8-11h-6l1-7z" },
    { mode: "year", name: "真题按年份", desc: "选年份，练该年真题", icon: "M7 3v3M17 3v3M4 8h16M5 5h14v15H5z" },
  ];
  /* a bottom sheet with a list of choices; resolves with the chosen value, or null */
  function choose(title, options, note) {
    return new Promise((resolve) => {
      const dialog = el("dialog", undefined, document.body, "pick-dialog sheet");
      el("h2", title, dialog);
      if (note) el("p", note, dialog, "muted small");
      const list = el("div", undefined, dialog, "pick-list");
      let picked = null;
      options.forEach((o) => {
        const button = el("button", undefined, list, "pick-option");
        button.type = "button";
        button.disabled = Boolean(o.disabled);
        el("span", o.label, button);
        if (o.sub) el("small", o.sub, button);
        button.onclick = () => { picked = o.value; dialog.close(); };
      });
      const cancel = el("button", "取消", dialog, "secondary full-width");
      cancel.type = "button";
      cancel.onclick = () => dialog.close();
      dialog.addEventListener("close", () => { setTimeout(() => dialog.remove(), 260); resolve(picked); });
      dialog.showModal();
    });
  }
  async function pickAndStart(kind, data) {
    const body = { mode: kind.mode, size: 10 };
    if (kind.mode === "chapter") {
      const value = await choose("选一个章节", data.chapter.chapters.map((c) => ({ label: c.chapter, sub: `${SHORT[c.subject]} · 可练 ${c.available} 题`, value: c })));
      if (!value) return;
      Object.assign(body, { subject: value.subject, chapter: value.chapter });
    } else if (kind.mode === "type") {
      const value = await choose("选一个题型", data.type.types.map((t) => ({ label: t.name, sub: t.supported ? `可练 ${t.available} 题` : "需要 AI 判分，暂不能练习", value: t.value, disabled: !t.supported || t.available < 5 })));
      if (!value) return;
      body.question_type = value;
    } else if (kind.mode === "year") {
      const value = await choose("选一个年份", data.year.years.map((y) => ({ label: `${y.year} 年`, sub: `可练 ${y.available} 题`, value: y.year, disabled: y.available < 5 })));
      if (!value) return;
      body.year = value;
    } else if (kind.mode === "quick") {
      const value = await choose("随机快练", [{ label: "不限时", value: 0 }, { label: "限时 10 分钟", value: 600 }, { label: "限时 15 分钟", value: 900 }]);
      if (value === null) return;
      if (value) body.seconds = value;
    }
    window.Insights.startPractice(body);
  }
  function renderPracticeGrid(data) {
    const grid = $("practice-grid");
    grid.replaceChildren();
    KINDS.forEach((kind) => {
      const info = data[kind.mode];
      const button = el("button", undefined, grid, "card practice-card");
      button.type = "button";
      button.dataset.mode = kind.mode;
      const ok = info.ready;
      button.disabled = !ok;
      if (!ok) button.setAttribute("aria-disabled", "true");
      const icon = el("span", undefined, button, "practice-icon");
      icon.append(svgIcon(kind.icon));
      el("b", kind.name, button);
      el("small", kind.desc, button);
      el("span", ok ? `可练 ${info.available} 题` : kind.mode === "retry" ? "还没有可重做的错题" : "题库准备中", button, "practice-count" + (ok ? "" : " off"));
      button.onclick = () => pickAndStart(kind, data);
    });
  }
  function renderRecent(data) {
    const list = $("recent-list");
    list.replaceChildren();
    $("recent-all").hidden = false;
    if (!data.items.length) {
      const empty = el("li", undefined, list, "empty-line");
      el("span", "还没有训练记录。做完今日卷或一套专项练习后会出现在这里。", empty);
      return;
    }
    data.items.forEach((r) => {
      const li = el("li", undefined, list);
      const a = el("a", undefined, li, "recent-row");
      a.href = "#/review/" + (r.kind === "paper" ? "p" : "s") + r.id;
      const left = el("span", undefined, a, "recent-main");
      el("b", r.label, left);
      el("small", `${md(r.date)}${r.seconds ? " · 用时 " + mmss(r.seconds) : ""}`, left);
      el("span", r.points === null ? "判分中" : `${r.points} / ${r.max_points}`, a, "recent-score" + (r.points === null ? " pending" : ""));
      el("span", "›", a, "chev");
    });
  }
  function loadPractice() {
    read("/v2/practice/overview", renderPracticeGrid, (e) => failCard($("practice-grid"), e.message + "。", loadPractice));
    read("/v2/training/recent?limit=5", renderRecent, () => {});
    paintToday();
  }
  $("recent-all").onclick = () => { location.hash = "#/stats/history"; };

  /* ------------------------------------------------------------------------------------------------------------------
     Mistake book
     ------------------------------------------------------------------------------------------------------------------ */
  const view = { mode: "due", subject: "", type: "", chapter: "", open: false };
  let book = null;
  const dueText = (day, today) => {
    const diff = Math.round((new Date(day + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
    return diff <= 0 ? "今天" : diff === 1 ? "明天" : diff < 7 ? `${diff} 天后` : md(day);
  };
  function mistakeCard(item, today) {
    const card = el("a", undefined, null, "mistake-card");
    card.href = "#/mistake/m" + item.question_id;
    card.style.setProperty("--bar", SUBJECT_COLOR[item.subject]);
    const top = el("div", undefined, card, "mc-top");
    el("span", TYPE[item.question_type], top, "mc-type");
    el("span", item.chapter, top, "mc-chapter");
    if (item.wrong_times >= 2) el("span", `错 ${item.wrong_times} 次`, top, "mc-times");
    el("p", item.stem || "（题干不可用）", card, "mc-stem");
    const cmp = el("p", undefined, card, "mc-cmp");
    if (item.subjective) el("span", `得分 ${item.points ?? "—"} / ${item.max_points} 分`, cmp);
    else {
      if (item.your_answer) el("span", `你的答案：${item.your_answer} ✗`, cmp, "mc-wrong");
      if (item.correct) el("span", `正确答案：${item.correct}`, cmp, "mc-right");
    }
    const foot = el("div", undefined, card, "mc-foot");
    el("span", item.group === "mastered" ? "已掌握" : item.group === "consolidate" ? "待巩固" : `下次复习：${dueText(item.due_day, today)}`, foot, "mc-next");
    const dots = el("span", undefined, foot, "mc-dots");
    dots.setAttribute("role", "img");
    dots.setAttribute("aria-label", `复习进度 ${item.dots} / 3`);
    for (let i = 0; i < 3; i += 1) el("i", undefined, dots, i < item.dots ? "on" : "");
    el("span", "›", card, "chev");
    return card;
  }
  function drawBook() {
    if (!book) return;
    const data = book;
    const open = data.items.filter((i) => i.group !== "mastered");
    const count = (subject) => open.filter((i) => i.subject === subject).length;
    // the one-line filter: 全部 · 生化 N · 细胞 N   筛选 ▾
    const subjects = $("filter-subject");
    subjects.replaceChildren();
    [["", `全部 ${open.length}`], ["biochemistry", `生化 ${count("biochemistry")}`], ["cell_biology", `细胞 ${count("cell_biology")}`]].forEach(([value, label]) => {
      const chip = el("button", label, subjects);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(view.subject === value));
      chip.onclick = () => { view.subject = value; view.chapter = ""; drawBook(); };
    });
    const more = $("filter-more");
    more.hidden = !view.open;
    $("filter-toggle").textContent = view.open ? "筛选 ▴" : "筛选 ▾";
    $("filter-toggle").setAttribute("aria-expanded", String(view.open));
    const types = $("filter-type");
    types.replaceChildren();
    [["", "全部题型"], ...TYPES.filter((t) => data.types.some((x) => x.value === t)).map((t) => [t, `${TYPE[t]} ${data.types.find((x) => x.value === t).count}`])].forEach(([value, label]) => {
      const chip = el("button", label, types);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(view.type === value));
      chip.onclick = () => { view.type = value; drawBook(); };
    });
    const select = $("filter-chapter");
    select.replaceChildren();
    el("option", "全部章节", select).value = "";
    data.chapters.filter((c) => !view.subject || c.subject === view.subject).forEach((c) => {
      const option = el("option", `${SHORT[c.subject]} · ${c.value}（${c.count}）`, select);
      option.value = c.value;
    });
    select.value = view.chapter;
    const items = data.items.filter((i) => (!view.subject || i.subject === view.subject) && (!view.type || i.question_type === view.type) && (!view.chapter || i.chapter === view.chapter));
    const root = $("mistake-groups");
    root.replaceChildren();
    $("mistake-empty").hidden = items.length > 0;
    const groups = view.mode === "chapter"
      ? [...new Set(items.map((i) => `${i.subject}|${i.chapter}`))].map((k) => ({ key: k, label: `${SHORT[k.split("|")[0]]} · ${k.split("|")[1]}`, items: items.filter((i) => `${i.subject}|${i.chapter}` === k) }))
      : data.groups.map((g) => ({ key: g.key, label: g.label, items: items.filter((i) => i.group === g.key) }));
    groups.filter((g) => g.items.length).forEach((g) => {
      const section = el(g.key === "mastered" ? "details" : "section", undefined, root, "mistake-group");
      const head = el(g.key === "mastered" ? "summary" : "h3", undefined, section, "mg-head");
      el("span", g.label, head);
      el("span", String(g.items.length), head, "mg-count");
      g.items.forEach((item) => section.append(mistakeCard(item, data.today)));
    });
  }
  function loadMistakes() {
    return read("/v2/mistakes/book", (data) => { book = data; drawBook(); }, (e) => failCard($("mistake-groups"), e.message + "。未将未知数据当作 0。", loadMistakes));
  }
  $("filter-toggle").onclick = () => { view.open = !view.open; drawBook(); };
  $("filter-chapter").onchange = (event) => { view.chapter = event.target.value; drawBook(); };
  $("mistake-mode").querySelectorAll("button").forEach((button) => {
    button.onclick = () => {
      view.mode = button.dataset.mode;
      $("mistake-mode").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      drawBook();
    };
  });

  /* ------------------------------------------------------------------------------------------------------------------
     A mistake in full
     ------------------------------------------------------------------------------------------------------------------ */
  const answerBlock = (parent, label, text, cls) => {
    const box = el("div", undefined, parent, "answer-line " + (cls || ""));
    el("span", label, box, "al-label");
    el("span", text || "（未作答）", box, "al-text");
    return box;
  };
  function drawQuestion(root, d, { history } = {}) {
    const head = el("div", undefined, root, "qd-head");
    el("span", TYPE[d.question_type], head, "mc-type");
    el("span", `${SHORT[d.subject]} · ${d.chapter}`, head, "mc-chapter");
    if (d.points !== null && d.points !== undefined) el("span", `${d.points} / ${d.max_points} 分`, head, "qd-score");
    el("p", d.stem, root, "qd-stem");
    if (d.question_type === "single_choice" && d.options && Object.keys(d.options).length) {
      const list = el("ul", undefined, root, "qd-options");
      Object.entries(d.options).sort(([a], [b]) => a.localeCompare(b)).forEach(([letter, text]) => {
        const mine = d.your_choice === letter, right = d.correct_choice === letter;
        const li = el("li", undefined, list, (right ? "is-right " : "") + (mine && !right ? "is-wrong" : ""));
        el("b", letter, li, "opt-letter");
        el("span", text, li);
        if (mine) el("em", right ? "你的选择 ✓" : "你的选择 ✗", li);
        else if (right) el("em", "正确答案", li);
      });
    } else {
      answerBlock(root, "你的答案", d.your_answer, d.status === "right" ? "ok" : "bad");
      answerBlock(root, "参考答案", d.reference || (d.accepted_answers || []).join(" / "), "ok");
    }
    if (d.question_type === "single_choice") answerBlock(root, "参考答案", d.reference, "ok");
    const items = (d.criteria && d.criteria.length) ? d.criteria : null;
    if (items) {
      const box = el("div", undefined, root, "criteria");
      el("h4", "评分要点", box);
      items.forEach((c, i) => {
        const row = el("div", undefined, box, "criterion-row" + (c.points < c.max_points ? " lost" : ""));
        el("span", `${i + 1}. ${c.description}`, row);
        el("b", `${c.points} / ${c.max_points}`, row);
        if (d.criterion_reasons && d.criterion_reasons[i]) el("small", "理由：" + d.criterion_reasons[i], box, "reason");
      });
    } else if (!["single_choice", "fill_blank"].includes(d.question_type) && (d.rubric || d.scores?.criteria || []).length) {
      const box = el("div", undefined, root, "criteria");
      el("h4", "评分要点", box);
      (d.rubric || []).forEach((c, i) => el("p", `${i + 1}. ${c.description}（${c.points} 分）`, box));
    }
    if (d.scores?.criteria?.length) {
      const box = el("div", undefined, root, "criteria");
      el("h4", "评分要点", box);
      d.scores.criteria.forEach((c, i) => {
        const row = el("div", undefined, box, "criterion-row" + (c.points < c.max_points ? " lost" : ""));
        el("span", `${i + 1}. ${c.description}`, row);
        el("b", `${c.points} / ${c.max_points}`, row);
        if (d.scores.criterion_reasons?.[i]) el("small", "理由：" + d.scores.criterion_reasons[i], box, "reason");
      });
    }
    [["concept_errors", "概念错误"], ["imprecisions", "表述问题"], ["suggestions", "改进建议"]].forEach(([field, label]) => (d[field] || []).forEach((t) => el("p", `${label}：${t}`, root, "qd-note")));
    if (d.comment) el("p", d.comment, root, "qd-note");
    if (d.evidence && d.evidence.length) {
      const box = el("details", undefined, root, "evidence");
      el("summary", "教材依据：" + [...new Set(d.evidence.map((e) => e.chapter))].join("、"), box);
      d.evidence.forEach((e) => { if (e.excerpt) el("blockquote", e.excerpt, box); });
    }
    if (history && history.length) {
      const box = el("div", undefined, root, "history");
      el("h4", "错误历史", box);
      history.forEach((h) => el("p", `${md(h.date)} · ${h.points} / ${h.max_points} 分${h.kind === "practice" ? "（练习）" : ""}`, box));
    }
  }
  let detailId = null;
  function loadMistakeDetail(id) {
    detailId = id;
    const root = $("mistake-detail");
    $("mistake-action").hidden = true;
    return read("/v2/mistakes/" + id, (d) => {
      if (detailId !== id) return;
      root.replaceChildren();
      const card = el("section", undefined, root, "card question-detail");
      drawQuestion(card, d, { history: d.history });
      $("mistake-action").hidden = false;
    }, (e) => failCard(root, e.message + "。", () => loadMistakeDetail(id)));
  }
  $("mistake-review").onclick = async () => {
    try {
      const item = await window.Data.fetchJSON("/v2/memory/" + detailId + "/item");
      window.TrainingUI.go("mistakes");
      window.TodayPaper.review([item]);
    } catch (error) {
      window.alert(error.message);
    }
  };

  /* ------------------------------------------------------------------------------------------------------------------
     Whole-paper review: light summary first, one question in full when it is opened
     ------------------------------------------------------------------------------------------------------------------ */
  const STATUS = { right: "对", wrong: "错", partial: "部分", blank: "未答", pending: "判分中", reported: "已反馈 · 不计分" };
  const rv = { filter: "all", subject: "", type: "" };
  let review = null;
  let reviewKey = null;
  const reviewPath = (key) => (key[0] === "s" ? "/v2/practice/" + key.slice(1) + "/review" : "/v2/papers/" + key.slice(1) + "/review");
  function reviewHeader(root, s) {
    const card = el("section", undefined, root, "card key-card review-head");
    el("p", `${md(s.date)} · ${s.specification_name}`, card, "date-line");
    const total = el("div", undefined, card, "rh-total");
    el("strong", s.pending ? "判分中" : String(s.score ?? s.graded_points), total);
    el("span", ` / ${s.max_points} 分`, total);
    if (s.read_only && s.kind === "paper") el("span", "旧卷 · 只读", total, "pill");
    const subjects = el("div", undefined, card, "rh-subjects");
    Object.entries(s.subject_scores || {}).forEach(([subject, v]) => {
      const box = el("div", undefined, subjects);
      el("span", SHORT[subject], box, "rh-subj");
      el("b", `${v.points} / ${v.max_points}`, box);
    });
    if (s.seconds) {
      const box = el("div", undefined, subjects);
      el("span", "用时", box, "rh-subj");
      el("b", mmss(s.seconds), box);
    }
    if (s.type_rates && s.type_rates.length) {
      const rates = el("div", undefined, card, "rh-rates");
      s.type_rates.forEach((t) => {
        const chip = el("span", undefined, rates, "rate-chip");
        chip.style.setProperty("--dot", SUBJECT_COLOR[t.subject]);
        chip.textContent = `${SHORT[t.subject]}${TYPE[t.question_type]} ${pct(t.rate)}`;
      });
    }
  }
  function matches(item) {
    if (rv.subject && item.subject !== rv.subject) return false;
    if (rv.type && item.question_type !== rv.type) return false;
    if (rv.filter === "wrong") return item.flag === "mistake" || item.status === "wrong" || item.status === "blank";
    if (rv.filter === "consolidate") return item.flag === "consolidate";
    return true;
  }
  function drawReview() {
    if (!review) return;
    const root = $("review-body");
    root.replaceChildren();
    reviewHeader(root, review);
    // filters
    const filters = el("section", undefined, root, "card review-filters");
    const row = (items, current, onPick, label) => {
      const box = el("div", undefined, filters, "chips");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", label);
      items.forEach(([value, text]) => {
        const chip = el("button", text, box);
        chip.type = "button";
        chip.setAttribute("aria-pressed", String(current === value));
        chip.onclick = () => { onPick(value); drawReview(); };
      });
    };
    row([["all", `全部 ${review.items.length}`], ["wrong", "只看错题"], ["consolidate", `只看待巩固 ${review.flags.consolidate}`]], rv.filter, (v) => { rv.filter = v; }, "范围");
    row([["", "两科"], ...Object.keys(SHORT).map((k) => [k, SHORT[k]])], rv.subject, (v) => { rv.subject = v; }, "科目");
    row([["", "全部题型"], ...TYPES.filter((t) => review.items.some((i) => i.question_type === t)).map((t) => [t, TYPE[t]])], rv.type, (v) => { rv.type = v; }, "题型");
    // the answer card: a number per question, coloured by what happened to it
    const nav = el("section", undefined, root, "card answer-nav");
    el("h2", "答题卡", nav);
    const grid = el("div", undefined, nav, "nav-grid");
    review.items.forEach((item) => {
      const cell = el("button", String(item.position), grid, "nav-cell " + item.status + (matches(item) ? "" : " dim"));
      cell.type = "button";
      cell.setAttribute("aria-label", `第${item.position}题，${STATUS[item.status]}`);
      cell.onclick = () => { const target = document.getElementById("rq-" + item.question_id); if (target) { target.scrollIntoView({ block: "start", behavior: "smooth" }); openQuestion(target, item); } };
    });
    const legend = el("p", undefined, nav, "nav-legend");
    [["right", "对"], ["partial", "部分得分"], ["wrong", "错"], ["blank", "未作答"]].forEach(([k, t]) => { const i = el("span", undefined, legend); el("i", undefined, i, "swatch " + k); i.append(t); });
    // the questions: collapsed lines, one opened in full on demand
    const list = el("section", undefined, root, "review-list");
    const shown = review.items.filter(matches);
    if (!shown.length) el("p", "没有符合条件的题。", list, "empty-line");
    shown.forEach((item) => {
      const box = el("article", undefined, list, "review-q " + item.status);
      box.id = "rq-" + item.question_id;
      const head = el("button", undefined, box, "rq-head");
      head.type = "button";
      head.setAttribute("aria-expanded", "false");
      el("b", `第 ${item.position} 题`, head, "rq-no");
      el("span", `${SHORT[item.subject]} · ${TYPE[item.question_type]}`, head, "rq-type");
      el("span", item.status === "reported" ? STATUS.reported : item.points === null ? "判分中" : `${item.points} / ${item.max_points}`, head, "rq-score");
      el("span", item.stem, box, "rq-stem");
      head.onclick = () => openQuestion(box, item);
    });
  }
  async function openQuestion(box, item) {
    const head = box.querySelector(".rq-head");
    const body = box.querySelector(".rq-body");
    if (body) {
      body.hidden = !body.hidden;
      head.setAttribute("aria-expanded", String(!body.hidden));
      return;
    }
    head.setAttribute("aria-expanded", "true");
    const target = el("div", "加载中…", box, "rq-body muted");
    try {
      const d = await window.Data.fetchJSON(reviewPath(reviewKey) + "/" + item.question_id);
      target.replaceChildren();
      target.className = "rq-body";
      if (d.reported) el("p", "已反馈 · 不计分", target, "notice");
      drawQuestion(target, d);
    } catch (error) {
      target.textContent = error.message;
    }
  }
  function loadReview(key) {
    reviewKey = key;
    review = null;
    Object.assign(rv, { filter: "all", subject: "", type: "" });
    const root = $("review-body");
    root.replaceChildren();
    const stack = el("div", undefined, root, "skeleton-stack");
    stack.setAttribute("aria-hidden", "true");
    ["tall", "", ""].forEach((cls) => el("div", undefined, stack, "skeleton card-sk " + cls));
    return read(reviewPath(key), (data) => {
      if (reviewKey !== key) return;
      review = data;
      $("review-title").textContent = data.kind === "practice" ? "练习回看" : "整卷回看";
      drawReview();
    }, (e) => failCard(root, e.message + "。", () => loadReview(key)));
  }

  /* ------------------------------------------------------------------------------------------------------------------ */
  function show(name, param) {
    if (name === "practice") loadPractice();
    else if (name === "mistakes") loadMistakes();
    else if (name === "mistake" && /^m\d+$/.test(param || "")) loadMistakeDetail(Number(param.slice(1)));
    else if (name === "review" && /^[ps]\d+$/.test(param || "")) loadReview(param);
  }
  window.Pages = { show, paintToday, todayLoaded, loadPractice, loadMistakes, loadReview, todayState };
})();
