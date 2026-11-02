/* Data pages: home dashboard, "my learning data", mistake list + short practice, result extras.
   Numbers come from the read-only /v2/stats/* and /v2/mistakes endpoints; nothing is invented:
   no data -> an empty state that says when it will fill. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const C = window.Charts;
  const SUBJECT = { biochemistry: "生物化学", cell_biology: "细胞生物学" };
  const SHORT = { biochemistry: "生化", cell_biology: "细胞" };
  const COLOR = { biochemistry: C.COLORS.biochemistry, cell_biology: C.COLORS.cell_biology };
  const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
  const TYPES = Object.keys(TYPE);
  const el = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(Math.round(v * 10) / 10);
  // Reads go through the shared data layer: a 401 means "sign in" (never a different page) and every successful
  // read is kept, so a page seen before can be drawn from the kept copy while a fresh one is on its way.
  let cachedOnly = false;
  async function read(path) {
    if (cachedOnly) {
      const kept = Data.peek(path);
      if (!kept) throw new Error("no kept copy");
      return kept.data;
    }
    const data = await Data.fetchJSON(path);
    Data.store(path, data);
    return data;
  }
  /* Draw from what was seen before at once (if every read has a kept copy), then draw again from the fresh reads. */
  async function swrLoad(loader, paths) {
    if (paths.every((path) => Data.peek(path))) {
      cachedOnly = true;
      try {
        await loader();
      } catch {
        /* the fresh load below will report */
      } finally {
        cachedOnly = false;
      }
    }
    await loader();
  }
  const send = (path, body) => Data.fetchJSON(path, { method: "POST", body: JSON.stringify(body) });
  const dateLabel = (iso) => {
    const d = new Date(iso + "T00:00:00");
    return d.getMonth() + 1 + "/" + d.getDate();
  };
  const hours = (seconds) => {
    const minutes = Math.round(seconds / 60);
    return minutes >= 60 ? Math.floor(minutes / 60) + " 小时 " + (minutes % 60) + " 分" : minutes + " 分钟";
  };
  const PRACTICE_REASON = {
    no_weak_chapters_yet: "还没有足够的已判分记录来找出薄弱章节（每章至少 3 题）。",
    no_practice_questions_in_these_chapters: "这些章节暂时没有可练的已入库客观题；题库补充后再试。",
    subject_and_chapter_required: "请选择科目和章节。",
  };

  /* ---------------- home ---------------- */
  /* One-time "add to home screen" tip (iPhone / iPad Safari, not yet installed). */
  function a2hsHint() {
    const standalone = navigator.standalone === true || window.matchMedia?.("(display-mode: standalone)").matches;
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    let dismissed = false;
    try {
      dismissed = localStorage.getItem("a2hs-hint-v1") === "1";
    } catch {
      /* private mode: show the tip every time rather than never */
    }
    $("a2hs-hint").hidden = !(ios && !standalone && !dismissed);
    $("a2hs-dismiss").onclick = () => {
      $("a2hs-hint").hidden = true;
      try {
        localStorage.setItem("a2hs-hint-v1", "1");
      } catch {
        /* nothing to remember it with */
      }
    };
  }
  const EMPTY_ICON = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg>';
  const emptyLine = (root, text) => {
    root.replaceChildren();
    const line = el("div", undefined, root, "empty-line");
    line.innerHTML = EMPTY_ICON;
    el("span", text, line);
  };
  const clock = (iso) => {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
    const today = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric" }).format(new Date());
    const same = today === `${parts.month}/${parts.day}`;
    return (same ? "" : `${parts.month} 月 ${parts.day} 日 `) + `${parts.hour}:${parts.minute}`;
  };
  /* Honest estimate: a clock time or "about N hours" from the real off-peak admission rate, else "estimating". */
  function etaText(bank) {
    if (bank.complete) return "题库已够一张卷子";
    const paused = bank.budget && bank.budget.daily_exhausted ? "今日 AI 额度已用完，明天 0 点（北京时间）自动继续；" : "";
    if (bank.eta_state === "slow") return paused + "预计需要 2 天以上";
    if (bank.eta_state !== "estimated" || !bank.eta_at) return paused ? paused.replace(/；$/, "") : "正在估算";
    const hours = bank.eta_hours < 1 ? "不到 1 小时" : `约 ${Math.round(bank.eta_hours)} 小时`;
    return `${paused}预计${hours}，${clock(bank.eta_at)} 前可开始`;
  }
  /* While the bank is short and no paper is prepared the start button is grey and says why. */
  let lock = null;
  function applyLock() {
    const button = $("prepare");
    if (!lock || window.__hasPaper?.()) {
      button.dataset.locked = "0";
      return;
    }
    button.dataset.locked = "1";
    button.disabled = !lock.complete;
    button.setAttribute("aria-disabled", String(!lock.complete));
    button.firstChild.textContent = lock.complete ? "开始" : `题库还差 ${lock.missing} 道，暂不能开始`;
    $("paper-state").textContent = lock.complete ? "已就绪" : "题库准备中";
    $("paper-state").classList.toggle("green-pill", lock.complete);
    $("prepare-note").hidden = !lock.complete;
  }
  async function loadHomeNow() {
    a2hsHint();
    const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", weekday: "short",
    }).formatToParts(new Date()).map((p) => [p.type, p.value]));
    $("today-label").textContent = `${parts.month} 月 ${parts.day} 日 · ${parts.weekday}`;
    const [overview, spend] = await Promise.allSettled([read("/v2/stats/overview"), read("/v2/model/spend")]);
    const banner = $("quota-banner");
    if (spend.status === "fulfilled" && spend.value.message) {
      banner.textContent = spend.value.message;
      banner.hidden = false;
    } else banner.hidden = true;
    if (overview.status !== "fulfilled") {
      $("stat-avg-note").textContent = "统计暂时读取失败，未将未知数据当作0。";
      return;
    }
    const o = overview.value;
    $("days-left").textContent = o.days_left > 0 ? String(o.days_left) : "0";
    $("exam-label").textContent = o.days_left > 0 ? "距 12 月 19 日初试" : "12 月 19 日初试已到";
    $("streak-days").textContent = String(o.streak.current);
    $("streak-days").classList.toggle("zero", o.streak.current === 0);
    const today = o.today_paper;
    const result = $("today-result");
    result.hidden = !(today.state === "submitted");
    if (today.state === "submitted")
      result.textContent = today.points !== undefined ? `今日 ${today.points} / ${today.max} 分` : "今日已交卷，等待判分完成";
    // bank readiness: tag, progress line and the start button
    const bank = o.tomorrow;
    const showBank = today.state === "none";
    $("bank-block").hidden = !showBank;
    $("bank-ready").textContent = String(bank.ready);
    $("bank-needed").textContent = String(bank.needed);
    $("bank-bar").style.width = Math.min(100, bank.needed ? (bank.ready / bank.needed) * 100 : 0) + "%";
    $("bank-eta").textContent = etaText(bank);
    lock = today.state === "none" ? bank : null;
    applyLock();
    const none = (id, value, noteId, empty, shown) => {
      $(id).textContent = value === null || value === undefined ? "—" : shown;
      $(id).classList.toggle("none", value === null || value === undefined);
      $(noteId).textContent = value === null || value === undefined ? empty : "";
    };
    const last7 = o.last7;
    none("stat-avg", last7.avg_points, "stat-avg-note", "做完首套后显示", String(Math.round(last7.avg_points)));
    const note = $("stat-avg-note");
    note.className = "";
    if (last7.avg_points !== null) {
      if (last7.delta_points === null) note.textContent = `满分 ${last7.max_points}`;
      else {
        note.textContent = `较上周 ${sign(last7.delta_points)}`;
        note.className = last7.delta_points >= 0 ? "up" : "down";
      }
    }
    none("stat-objective", o.objective_rate, "stat-objective-note", "做完首套后显示", pct(o.objective_rate));
    if (o.objective_rate !== null) $("stat-objective-note").textContent = "单选 + 填空";
    none("stat-subjective", o.subjective_rate, "stat-subjective-note", "判分后显示", pct(o.subjective_rate));
    if (o.subjective_rate !== null) $("stat-subjective-note").textContent = "AI 判分";
    weak($("weak-chapters"), o.weak_chapters);
    $("weak-practice").hidden = !o.weak_chapters.length;
    const due = o.reviews.due_now;
    $("metric-due").textContent = String(due);
    $("quick-review-line").textContent = due ? `今日到期 ${due} 道` : "今日没有到期";
    $("quick-progress-line").replaceChildren(document.createTextNode("已入库 "));
    el("b", `${bank.ready}/${bank.needed}`, $("quick-progress-line"), bank.complete ? "" : "attention").id = "tomorrow-ready";
  }
  function weak(root, chapters) {
    root.replaceChildren();
    if (!chapters.length) {
      emptyLine(root, "做完首套后，按章节正确率列出最需要补的 3 章");
      return;
    }
    chapters.forEach((c) => {
      const row = el("div", undefined, root, "weak-row");
      C.progress(row, {
        label: c.chapter, text: `${SHORT[c.subject]} · ${pct(c.rate)} · ${c.answers} 题`, value: c.rate, max: 1,
        color: c.rate < 0.5 ? C.COLORS.cell_biology : C.COLORS.biochemistry,
      });
    });
  }

  /* ---------------- my learning data ---------------- */
  let trendMode = "rate";
  async function loadProfileNow() {
    const root = $("profile-data");
    let data;
    try {
      data = await read("/v2/stats/profile");
    } catch (error) {
      root.replaceChildren();
      el("p", error.message + "。未将未知数据当作0。", root, "notice");
      return;
    }
    root.replaceChildren();
    const card = () => el("section", undefined, root, "card data-card");
    // a) four overview cells, divided by thin vertical lines
    const a = el("section", undefined, root, "card");
    a.setAttribute("aria-label", "概览");
    const cells = el("div", undefined, a, "overview-cells");
    [
      [String(data.summary.papers), "完成卷数", false],
      [String(data.summary.answered), "做题数", false],
      [data.summary.total_seconds ? hoursShort(data.summary.total_seconds) : "—", "累计用时", false],
      [String(data.summary.streak_current), "连续天数", data.summary.streak_current > 0],
    ].forEach(([value, label, warm]) => {
      const cell = el("div", undefined, cells);
      el("strong", value, cell, "num" + (warm ? " warm" : ""));
      el("span", label, cell);
    });
    // b) trend: title left, score / rate capsule right
    const b = card();
    const bHead = el("div", undefined, b, "head-line");
    el("h2", "成绩趋势", bHead);
    const toggle = el("div", undefined, bHead, "segmented");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "趋势显示方式");
    const chart = el("div", undefined, b);
    const draw = () => trend(chart, data.trend);
    [["points", "分数"], ["rate", "得分率"]].forEach(([mode, label]) => {
      const button = el("button", label, toggle);
      button.type = "button";
      button.setAttribute("aria-pressed", String(trendMode === mode));
      button.onclick = () => {
        trendMode = mode;
        toggle.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === button)));
        draw();
      };
    });
    draw();
    // c) type rates
    const c = card();
    typeRates(el("div", undefined, c), data.type_rates);
    // d) calendar
    const e = card();
    el("h2", "刷题日历", e);
    const days = data.calendar.filter((x) => x.answered > 0).length;
    const total = data.calendar.reduce((s, x) => s + x.answered, 0);
    C.heatmap(el("div", undefined, e), {
      title: "刷题日历", hideTitle: true,
      cells: data.calendar.map((x) => ({ day: x.day, today: x.day === data.today, value: x.answered / 60, note: `做题 ${x.answered} 道${x.rate === null ? "" : "，得分率 " + pct(x.rate)}` })),
      conclusion: total ? `最近 8 周有 ${days} 天做了题，共 ${total} 道。` : "",
      empty: "做完首套后显示每天的做题量",
    });
    // e) chapters: subject switch on the right
    const d = card();
    chapters(d, data.chapters);
    // f) timing per type
    const f = card();
    timing(f, data.timing);
    // g) reviews
    const g = card();
    reviews(g, data.reviews);
  }
  const hoursShort = (seconds) => {
    const h = seconds / 3600;
    return h >= 1 ? (Math.round(h * 10) / 10) + "h" : Math.round(seconds / 60) + "m";
  };
  function trend(root, rows) {
    if (!rows.length) {
      C.line(root, { title: "成绩趋势", hideTitle: true, empty: "做完首套后显示成绩趋势" });
      return;
    }
    const latest = rows[rows.length - 1];
    const use = trendMode === "points" ? rows.filter((r) => r.spec === latest.spec) : rows;
    const label = (r) => dateLabel(r.date);
    const subjectSeries = (subject) => ({
      name: SHORT[subject], color: COLOR[subject],
      points: use.map((r) => {
        const s = r.subjects[subject];
        return { label: label(r), value: s ? (trendMode === "points" ? s.points : s.rate) : null, note: s ? `${s.points}/${s.max}分` : undefined };
      }),
    });
    const series = [subjectSeries("biochemistry"), subjectSeries("cell_biology"), {
      name: "两科总分", color: C.COLORS.total,
      points: use.map((r) => ({ label: label(r), value: trendMode === "points" ? r.points : r.rate, note: `${r.points}/${r.max}分` })),
    }];
    const last7 = rows.slice(-7), prev7 = rows.slice(-14, -7);
    const mean = (list, subject) => {
      const v = list.map((r) => r.subjects[subject]?.rate).filter((x) => x !== null && x !== undefined);
      return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
    };
    const parts = ["biochemistry", "cell_biology"].map((s) => {
      const now = mean(last7, s), before = mean(prev7, s);
      return now === null ? null : `${SHORT[s]}近 ${last7.length} 次平均得分率 ${pct(now)}` + (before === null ? "" : `，比前 ${prev7.length} 次 ${sign((now - before) * 100)} 个百分点`);
    }).filter(Boolean);
    const sameSpec = trendMode === "points" && use.length !== rows.length ? `分数模式只显示与最近一套同规格（满分 ${latest.max}）的 ${use.length} 套；` : "";
    C.line(root, {
      title: "成绩趋势", hideTitle: true, unit: trendMode === "points" ? "分" : "得分率", series,
      yMin: 0, yMax: trendMode === "points" ? latest.max : 1,
      format: trendMode === "points" ? C.num : (v) => Math.round(v * 100) + "%",
      conclusion: sameSpec + (parts.length ? parts.join("；") + "。" : "再完成一套就能看到变化。"),
    });
  }
  function typeRates(root, rows) {
    const items = TYPES.filter((t) => rows.some((r) => r.question_type === t)).map((t) => {
      const sub = ["biochemistry", "cell_biology"].map((s) => ({ name: SHORT[s], color: COLOR[s], value: rows.find((r) => r.subject === s && r.question_type === t)?.rate ?? null }));
      return { label: TYPE[t], sub, value: null, valueText: sub.map((x) => `${x.name} ${pct(x.value)}`).join(" · "), max: 1, type: t };
    });
    const flat = rows.filter((r) => r.rate !== null).sort((a, b) => a.rate - b.rate)[0];
    C.hbars(root, {
      title: "题型得分率", unit: "按题型 · 两科对比", items, format: pct,
      conclusion: flat ? `${TYPE[flat.question_type]}题得分率最低：${SHORT[flat.subject]} ${pct(flat.rate)}。` : "",
      empty: "完成第一套卷子后显示", emptyHint: "已判分的题按题型汇总，两科分组对比。",
    });
  }
  function chapters(card, rows) {
    const head = el("div", undefined, card, "head-line");
    el("h2", "章节掌握情况", head);
    const subjects = Object.keys(SUBJECT).filter((k) => rows.some((r) => r.subject === k));
    const box = el("div", undefined, card, "chapter-list");
    if (!rows.length) {
      emptyLine(box, "做完首套后，按章节显示正确率");
      return;
    }
    let current = subjects[0];
    const draw = () => {
      box.replaceChildren();
      rows.filter((r) => r.subject === current).forEach((r) => {
        const row = el("button", undefined, box, "chapter-row");
        row.type = "button";
        row.setAttribute("aria-label", `${SUBJECT[current]} ${r.chapter}，正确率 ${pct(r.rate)}，${r.answers} 题。点按只练这一章`);
        C.progress(el("div", undefined, row), {
          label: r.chapter, text: pct(r.rate), value: r.rate, max: 1,
          color: r.rate < 0.5 ? C.COLORS.cell_biology : C.COLORS.biochemistry,
        });
        row.onclick = () => chapterPractice(current, r.chapter);
      });
    };
    const toggle = el("div", undefined, head, "segmented");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "科目");
    subjects.forEach((subject) => {
      const button = el("button", SHORT[subject], toggle);
      button.type = "button";
      button.setAttribute("aria-pressed", String(subject === current));
      button.onclick = () => {
        current = subject;
        toggle.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === button)));
        draw();
      };
    });
    draw();
    el("p", "点任意章节，只练这一章", card, "card-footnote");
  }
  function timing(card, t) {
    el("h2", "各题型平均每题用时", card);
    const items = t.by_type.map((x) => ({
      label: { single_choice: "单选", fill_blank: "填空", term: "名解", short_answer: "简答", essay: "问答" }[x.question_type],
      value: x.avg_seconds, color: x.avg_seconds > x.suggested_seconds ? C.COLORS.cell_biology : C.COLORS.biochemistry,
      note: `建议 ${x.suggested_seconds} 秒${x.avg_seconds > x.suggested_seconds ? "，超出" : ""}`,
    }));
    const over = t.by_type.filter((x) => x.avg_seconds > x.suggested_seconds).sort((a, b) => b.avg_seconds / b.suggested_seconds - a.avg_seconds / a.suggested_seconds)[0];
    const mmss = (v) => `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}`;
    C.columns(el("div", undefined, card), {
      title: "各题型平均每题用时", hideTitle: true, unit: "每题", items, format: (v) => mmss(v),
      conclusion: !t.by_type.length ? "" : over
        ? `${TYPE[over.question_type]}平均每题 ${mmss(over.avg_seconds)}，超出按分值分配的建议用时（${mmss(over.suggested_seconds)}），可以适当压缩。`
        : "各题型都在按分值分配的建议用时内。",
      empty: "做完首套后显示每题用时", 
    });
  }
  function reviews(card, r) {
    el("h2", "错题复习", card);
    const grid = el("div", undefined, card, "num-grid");
    [[String(r.due_now), "今日到期"], [String(r.mastered), "已掌握（复习后连续答对）"]].forEach(([value, label]) => {
      const cell = el("div", undefined, grid);
      el("strong", value, cell);
      el("span", label, cell);
    });
    const next = r.next_7_days;
    const total = next.reduce((s, x) => s + x.count, 0);
    C.columns(el("div", undefined, card), {
      title: "未来 7 天到期", unit: "道", items: next.map((x, i) => ({ label: i === 0 ? "今天" : dateLabel(x.day), value: x.count, color: i === 0 ? C.COLORS.cell_biology : C.COLORS.biochemistry })),
      conclusion: total ? `未来 7 天共 ${total} 道到期，今天 ${next[0].count} 道。` : "",
      empty: "还没有复习卡片", emptyHint: "答错的题会自动进入复习安排。",
    });
  }

  /* ---------------- mistakes + practice ---------------- */
  const filter = { subject: "", question_type: "", chapter: "", offset: 0 };
  let facets = { subject: [], question_type: [], chapter: [] };
  let facetsLoaded = false;
  async function loadMistakes(append = false) {
    if (!append) filter.offset = 0;
    const query = new URLSearchParams();
    ["subject", "question_type", "chapter"].forEach((k) => filter[k] && query.set(k, filter[k]));
    query.set("limit", "20");
    query.set("offset", String(filter.offset));
    let data;
    try {
      data = await read("/v2/mistakes?" + query);
    } catch (error) {
      $("mistake-count").textContent = error.message;
      return;
    }
    if (!facetsLoaded) {
      facetsLoaded = true;
      facets = data.facets;
      drawFilters();
    }
    $("mistake-count").textContent = `共 ${data.total} 道`;
    const list = $("mistake-list");
    if (!append) list.replaceChildren();
    data.items.forEach((item) => {
      const li = el("li", undefined, list);
      const top = el("div", undefined, li, "mistake-top");
      el("span", [SHORT[item.subject], TYPE[item.question_type], item.chapter].filter(Boolean).join(" · "), top);
      el("span", `错 ${item.wrong_times} 次`, top, "times" + (item.wrong_times >= 2 ? " hot" : ""));
      el("span", item.stem || "（题干不可用）", li, "mistake-stem");
    });
    $("mistake-empty").hidden = data.total > 0;
    $("mistake-empty").querySelector("span").textContent = facetsLoaded && facets.subject.length ? "没有符合条件的错题" : "还没有错题，判分后失分的题会出现在这里";
    filter.offset += data.items.length;
    $("mistake-more").hidden = filter.offset >= data.total;
    $("chapter-practice").hidden = !(filter.subject && filter.chapter);
  }
  function chips(root, values, current, onPick) {
    root.replaceChildren();
    [["", "全部"], ...values].forEach(([value, label]) => {
      const button = el("button", label, root);
      button.type = "button";
      button.setAttribute("aria-pressed", String(current === value));
      button.onclick = () => onPick(value);
    });
  }
  function drawFilters() {
    // The full set of choices is always shown, even with no mistakes yet; counts are added where there are any.
    const count = (list, value) => list.find((f) => f.value === value)?.count;
    const label = (name, n) => (n ? `${name} ${n}` : name);
    chips($("filter-subject"), Object.keys(SHORT).map((v) => [v, label(SHORT[v], count(facets.subject, v))]), filter.subject, (v) => {
      filter.subject = v;
      filter.chapter = "";
      drawFilters();
      loadMistakes();
    });
    chips($("filter-type"), TYPES.map((v) => [v, label(TYPE[v], count(facets.question_type, v))]), filter.question_type, (v) => {
      filter.question_type = v;
      drawFilters();
      loadMistakes();
    });
    const select = $("filter-chapter");
    select.replaceChildren();
    el("option", "全部章节", select).value = "";
    facets.chapter.filter((f) => !filter.subject || f.subject === filter.subject).forEach((f) => {
      const option = el("option", `${SHORT[f.subject]} · ${f.value}（${f.count}）`, select);
      option.value = f.value;
      option.dataset.subject = f.subject;
    });
    select.value = filter.chapter;
  }
  /* The key card: how many reviews are due today, and the next 7 days as tiny bars. */
  async function loadDue() {
    let o;
    try {
      o = await read("/v2/stats/overview");
    } catch {
      return;
    }
    const n = o.reviews.due_now;
    $("due-number").textContent = String(n);
    $("due-number").classList.toggle("zero", n === 0);
    const memory = $("memory");
    memory.disabled = n === 0;
    memory.setAttribute("aria-disabled", String(n === 0));
    memory.textContent = n ? `开始复习 ${n} 道` : "今日没有到期复习";
    const box = $("due-mini");
    box.replaceChildren();
    const days = o.reviews.next_7_days;
    const top = Math.max(1, ...days.map((d) => d.count));
    days.forEach((d, i) => {
      const bar = el("i", undefined, box, i === 0 ? "today" : "");
      // zero days draw nothing but the baseline (the container's bottom line); other days at least 4px tall
      bar.style.height = d.count ? Math.max(4, Math.round((d.count / top) * 34)) + "px" : "0";
      bar.title = `${i === 0 ? "今天" : dateLabel(d.day)}：${d.count} 道`;
    });
    box.setAttribute("aria-label", "未来 7 天每天到期：" + days.map((d, i) => `${i === 0 ? "今天" : dateLabel(d.day)} ${d.count} 道`).join("，"));
    $("due-note").textContent = days.reduce((s2, d) => s2 + d.count, 0) ? "" : "答错的题会自动进入复习安排";
  }
  const overviewCharts = () => {};
  $("filter-chapter").onchange = (event) => {
    const option = event.target.selectedOptions[0];
    filter.chapter = event.target.value;
    if (filter.chapter && option?.dataset.subject) filter.subject = option.dataset.subject;
    drawFilters();
    loadMistakes();
  };
  $("mistake-more").onclick = () => loadMistakes(true);
  $("chapter-practice").onclick = () => chapterPractice(filter.subject, filter.chapter);
  drawFilters(); // the full set of choices is there before (and without) any data

  /* short practice: objective questions only, checked at once, not part of the daily paper */
  async function startPractice(body) {
    window.TrainingUI?.showView("mistakes");
    const panel = $("practice-panel");
    panel.hidden = false;
    panel.replaceChildren();
    el("p", "正在准备练习…", panel, "muted");
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    let set;
    try {
      set = await send("/v2/practice", body);
    } catch (error) {
      panel.replaceChildren();
      el("h2", "暂时无法开始练习", panel);
      el("p", PRACTICE_REASON[error.message] || error.message, panel, "muted");
      return;
    }
    runPractice(panel, set);
  }
  const chapterPractice = (subject, chapter) => startPractice({ mode: "chapter", subject, chapter, size: 10 });
  function runPractice(panel, set) {
    let index = 0, correct = 0;
    const questions = set.questions;
    const finish = () => {
      panel.replaceChildren();
      el("h2", "练习完成", panel);
      el("p", `${questions.length} 道里答对 ${correct} 道。`, panel, "grade-title");
      el("p", "练习成绩不计入每日成绩，但会更新章节正确率。", panel, "muted");
      const again = el("button", "再练一套薄弱题", panel, "primary");
      again.onclick = () => startPractice({ mode: "weak", size: 10 });
      const close = el("button", "关闭", panel, "secondary");
      close.onclick = () => {
        panel.hidden = true;
        facets = null;
        loadMistakes();
      };
    };
    const show = () => {
      if (index >= questions.length) return finish();
      const q = questions[index];
      const started = Date.now();
      panel.replaceChildren();
      const head = el("div", undefined, panel, "card-heading");
      el("h2", `练习 ${index + 1} / ${questions.length}`, head);
      el("span", `${SHORT[q.subject]} · ${TYPE[q.question_type]} · ${q.max_points} 分`, head, "pill");
      el("p", "不计入每日成绩", panel, "muted small");
      el("p", q.stem, panel, "question-stem");
      let chosen = "";
      const fields = [];
      if (q.question_type === "single_choice") {
        const box = el("div", undefined, panel);
        Object.entries(q.options).forEach(([letter, text]) => {
          const option = el("button", undefined, box, "option");
          option.type = "button";
          el("span", letter, option, "option-letter");
          el("span", text, option);
          option.onclick = () => {
            chosen = letter;
            box.querySelectorAll(".option").forEach((x) => x.classList.toggle("chosen", x === option));
          };
        });
      } else {
        for (let i = 0; i < q.blank_count; i++) {
          const input = el("input", undefined, panel);
          input.setAttribute("aria-label", q.blank_count > 1 ? `第${i + 1}空` : "答案");
          input.placeholder = q.blank_count > 1 ? `第${i + 1}空` : "填写答案";
          fields.push(input);
        }
      }
      const submit = el("button", "提交答案", panel, "primary");
      const feedback = el("div", undefined, panel);
      submit.onclick = async () => {
        submit.disabled = true;
        const seconds = Math.min(7200, Math.round((Date.now() - started) / 1000));
        const body = q.question_type === "single_choice" ? { question_id: q.id, text: chosen, seconds } : q.blank_count > 1 ? { question_id: q.id, blanks: fields.map((f) => f.value), seconds } : { question_id: q.id, text: fields[0].value, seconds };
        try {
          const r = await send(`/v2/practice/${set.set_id}/answer`, body);
          if (r.correct) correct++;
          feedback.className = "practice-feedback " + (r.correct ? "ok" : "bad");
          el("strong", r.correct ? "回答正确" : `未答对（${r.points}/${r.max_points} 分）`, feedback);
          el("p", "参考答案：" + [r.answer, ...r.accepted_answers.filter((x) => x !== r.answer)].join(" / "), feedback);
          r.why.forEach((w) => el("p", "得分点：" + w, feedback, "muted small"));
          const next = el("button", index + 1 >= questions.length ? "查看结果" : "下一题", panel, "primary");
          next.onclick = () => {
            index++;
            show();
          };
          next.focus();
        } catch (error) {
          submit.disabled = false;
          feedback.className = "practice-feedback bad";
          feedback.textContent = error.message;
        }
      };
    };
    show();
  }
  $("weak-practice").onclick = () => startPractice({ mode: "weak", size: 10 });

  /* ---------------- result extras ---------------- */
  async function resultExtras(data, { key, types }) {
    const counted = data.answers.filter((a) => !a.excluded && a.points !== null);
    const sums = (list) => list.reduce((s2, a) => [s2[0] + a.points, s2[1] + a.max_points], [0, 0]);
    // key card: the total (never a guess while grading is running), the comparison, one card per subject
    const line = el("div", undefined, key, "total-line");
    const total = el("div", undefined, line);
    el("span", "总分", total, "stat-label");
    const big = el("p", undefined, total, "total-score");
    big.append(data.score === null ? String(data.graded_points) : String(data.score));
    el("small", data.score === null ? `/ ${data.max_points}（判分中）` : `/ ${data.max_points}`, big);
    const compare = el("div", undefined, line, "compare");
    const split = el("div", undefined, key, "score-split");
    Object.keys(SUBJECT).forEach((subject) => {
      const s2 = data.subject_scores?.[subject];
      const box = el("div", undefined, split, subject === "biochemistry" ? "bio" : "cell");
      el("span", SUBJECT[subject], box);
      const value = el("strong", s2 ? String(s2.score === null ? s2.graded_points : s2.score) : "—", box);
      if (s2) el("small", ` / ${s2.max_points}`, value);
      const bar = el("div", undefined, box, "thin-bar");
      const fill = el("i", undefined, bar);
      fill.style.width = s2 && s2.max_points ? Math.min(100, ((s2.score === null ? s2.graded_points : s2.score) / s2.max_points) * 100) + "%" : "0%";
      fill.style.background = COLOR[subject];
    });
    const status = el("p", undefined, key, "overtime-note ok");
    // types card: grouped two-colour bars, subject legend, one conclusion
    const rows = TYPES.filter((t) => counted.some((a) => a.question_type === t)).map((t) => {
      const sub = Object.keys(SUBJECT).map((subject) => {
        const [p, m] = sums(counted.filter((a) => a.question_type === t && a.subject === subject));
        return { name: SHORT[subject], color: COLOR[subject], value: m ? p / m : null };
      });
      return { label: TYPE[t], sub, value: null, valueText: sub.map((x) => `${x.name} ${pct(x.value)}`).join(" · "), max: 1 };
    });
    const lowest = rows.flatMap((r) => r.sub.filter((x) => x.value !== null).map((x) => ({ label: r.label, ...x }))).sort((a, b) => a.value - b.value)[0];
    const typeBox = el("div", undefined, types);
    C.hbars(typeBox, {
      title: "各题型得分率", unit: "本卷", items: rows, format: pct,
      conclusion: lowest ? `${lowest.label}得分率最低：${lowest.name} ${pct(lowest.value)}。` : "",
      empty: "判分完成后显示各题型得分率",
    });
    if (rows.length) {
      const legend = el("div", undefined, typeBox, "chart-legend");
      Object.keys(SUBJECT).forEach((subject) => {
        const item = el("span", undefined, legend);
        el("i", undefined, item).style.background = COLOR[subject];
        item.append(SUBJECT[subject]);
      });
    }
    try {
      const [profile, timing] = await Promise.all([read("/v2/stats/profile"), read(`/v2/papers/${data.paper_id}/timing`)]);
      const own = profile.trend.find((r) => r.paper_id === data.paper_id);
      const before = profile.trend.filter((r) => r.paper_id !== data.paper_id && (!own || r.date <= own.date)).slice(-7);
      if (own && before.length) {
        const avg = before.reduce((s2, r) => s2 + r.rate, 0) / before.length;
        const diff = (own.rate - avg) * 100;
        compare.append(`比近 ${before.length} 次平均`);
        el("span", `${sign(diff)}`, compare, "delta " + (diff >= 0 ? "up" : "down"));
      } else compare.textContent = own ? "第一套已判分的卷子" : "判分完成后对比近 7 次";
      const over = timing.types.filter((t) => t.over);
      const spent = profile.timing.papers.find((p) => p.paper_id === data.paper_id);
      const parts = [];
      if (spent) parts.push(`用时 ${Math.floor(spent.seconds / 60)} 分 ${spent.seconds % 60} 秒`);
      parts.push(data.pending ? `${data.pending} 题待判分` : "全部已判分");
      if (over.length) parts.push("用时偏长：" + over.map((t) => `${TYPE[t.question_type]}（${Math.round(t.seconds / 60)} 分钟，按分值约 ${Math.round(t.budget_seconds / 60)} 分钟）`).join("；"));
      status.textContent = parts.join(" · ");
      if (over.length) status.className = "overtime-note";
    } catch {
      status.textContent = data.pending ? `${data.pending} 题待判分` : "全部已判分";
    }
  }

  /* ---------------- history extras ---------------- */
  async function historyChart(root) {
    try {
      const profile = await read("/v2/stats/profile");
      const box = el("div", undefined, root, "card");
      trendMode = "rate";
      trend(el("div", undefined, box), profile.trend);
      const seconds = new Map(profile.timing.papers.map((p) => [p.paper_id, p.seconds]));
      return new Map(profile.trend.map((r) => [r.paper_id, { ...r, seconds: seconds.get(r.paper_id) }]));
    } catch {
      return new Map();
    }
  }

  const loadHome = () => swrLoad(loadHomeNow, ["/v2/stats/overview"]);
  const loadProfile = () => swrLoad(loadProfileNow, ["/v2/stats/profile"]);
  function onView(view) {
    if (view === "profile") loadProfile();
    if (view === "mistakes") {
      facetsLoaded = false; // counts refresh; the chosen filters stay as they were
      loadMistakes();
      loadDue();
    }
  }

  window.Insights = { applyLock, loadHome, loadProfile, onView, resultExtras, historyChart, startPractice, chapterPractice };
})();
