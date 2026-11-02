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
  async function read(path) {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("读取失败（" + response.status + "）");
    return response.json();
  }
  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败（" + response.status + "）");
    return data;
  }
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
  async function loadHome() {
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
    $("exam-label").textContent = o.days_left > 0 ? "距 12 月 21 日初试" : "12 月 21 日初试已到";
    $("streak-days").textContent = String(o.streak.current);
    const today = o.today_paper;
    const result = $("today-result");
    result.hidden = !(today.state === "submitted");
    if (today.state === "submitted")
      result.textContent = today.points !== undefined ? `今日 ${today.points} / ${today.max} 分` : "今日已交卷，等待判分完成";
    const last7 = o.last7;
    $("stat-avg").textContent = last7.avg_points === null ? "—" : String(Math.round(last7.avg_points));
    const note = $("stat-avg-note");
    note.className = "";
    if (last7.avg_points === null) note.textContent = "完成第一套卷子后显示";
    else if (last7.delta_points === null) note.textContent = `满分 ${last7.max_points} · 共 ${last7.papers} 套`;
    else {
      note.textContent = `较上周 ${sign(last7.delta_points)} 分`;
      note.className = last7.delta_points >= 0 ? "up" : "down";
    }
    $("stat-objective").textContent = pct(o.objective_rate);
    $("stat-objective-note").textContent = o.objective_rate === null ? "完成第一套卷子后显示" : "单选 + 填空，近 7 次";
    $("stat-subjective").textContent = pct(o.subjective_rate);
    $("stat-subjective-note").textContent = o.subjective_rate === null ? "判分完成后显示" : "名词解释 + 简答 + 问答";
    weak($("weak-chapters"), o.weak_chapters);
    $("weak-practice").hidden = !o.weak_chapters.length;
    $("metric-due").textContent = String(o.reviews.due_now);
    $("tomorrow-ready").textContent = `${o.tomorrow.ready}/${o.tomorrow.needed}`;
  }
  function weak(root, chapters) {
    root.replaceChildren();
    if (!chapters.length) {
      const box = el("div", undefined, root, "empty-state");
      el("strong", "完成第一套卷子后显示", box);
      el("p", "每章至少有 3 道已判分题，才会列入薄弱章节。", box);
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
  async function loadProfile() {
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
    // a) overview numbers
    const a = card();
    el("h2", "概览", a);
    const grid = el("div", undefined, a, "num-grid");
    [
      [String(data.summary.papers), "累计完成卷数"],
      [String(data.summary.answered), "累计做题数"],
      [data.summary.total_seconds ? hours(data.summary.total_seconds) : "—", "累计用时"],
      [`${data.summary.streak_current} / ${data.summary.streak_longest}`, "当前 / 最长连续天数"],
    ].forEach(([value, label]) => {
      const cell = el("div", undefined, grid);
      el("strong", value, cell);
      el("span", label, cell);
    });
    // b) trend
    const b = card();
    const toggle = el("div", undefined, b, "segmented");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "趋势显示方式");
    const chart = el("div", undefined, b);
    const draw = () => trend(chart, data.trend);
    [["rate", "得分率"], ["points", "分数"]].forEach(([mode, label]) => {
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
    // d) chapters
    const d = card();
    chapters(d, data.chapters);
    // e) calendar
    const e = card();
    const days = data.calendar.filter((x) => x.answered > 0).length;
    const total = data.calendar.reduce((s, x) => s + x.answered, 0);
    C.heatmap(el("div", undefined, e), {
      title: "刷题日历", unit: "最近 8 周",
      cells: data.calendar.map((x) => ({ day: x.day, value: x.answered / 60, note: `做题 ${x.answered} 道${x.rate === null ? "" : "，得分率 " + pct(x.rate)}` })),
      conclusion: total ? `最近 8 周有 ${days} 天做了题，共 ${total} 道。` : "",
      empty: "完成第一套卷子后显示", emptyHint: "每天一格，颜色越深当天做题越多。",
    });
    // f) timing
    const f = card();
    timing(f, data.timing);
    // g) reviews
    const g = card();
    reviews(g, data.reviews);
  }
  function trend(root, rows) {
    if (!rows.length) {
      C.line(root, { title: "成绩趋势", empty: "完成第一套卷子后显示", emptyHint: "每套判分完成的卷子会在这里留下一个点。" });
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
      title: "成绩趋势", unit: trendMode === "points" ? "分" : "得分率", series,
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
    el("h2", "章节掌握情况", card);
    el("p", "按正确率从低到高；点开一章，只练这一章。", card, "muted small");
    if (!rows.length) {
      const box = el("div", undefined, card, "chart-empty");
      el("strong", "完成第一套卷子后显示", box);
      el("p", "每章按已判分的题和短练习计算正确率。", box);
      return;
    }
    Object.keys(SUBJECT).forEach((subject) => {
      const list = rows.filter((r) => r.subject === subject);
      if (!list.length) return;
      el("h3", SUBJECT[subject], card);
      const box = el("div", undefined, card, "chapter-list");
      list.forEach((r) => {
        const row = el("button", undefined, box, "chapter-row");
        row.type = "button";
        row.setAttribute("aria-label", `${SUBJECT[subject]} ${r.chapter}，正确率 ${pct(r.rate)}，${r.answers} 题。点按只练这一章`);
        C.progress(el("div", undefined, row), {
          label: r.chapter, text: `${pct(r.rate)} · ${r.answers} 题`, value: r.rate, max: 1,
          color: r.rate < 0.5 ? C.COLORS.cell_biology : COLOR[subject],
        });
        row.onclick = () => chapterPractice(subject, r.chapter);
      });
    });
  }
  function timing(card, t) {
    el("h2", "用时分析", card);
    const papers = t.papers.map((p) => ({ label: dateLabel(p.date), value: Math.round(p.seconds / 60), color: p.seconds > 3600 ? C.COLORS.cell_biology : C.COLORS.biochemistry, note: p.seconds > 3600 ? "超过 60 分钟" : undefined }));
    const last = papers[papers.length - 1];
    C.columns(el("div", undefined, card), {
      title: "每套卷子实际用时", unit: "分钟", items: papers, refLines: [{ value: 60, label: "60 分钟" }], max: 70,
      conclusion: last ? `最近一套用时 ${last.value} 分钟。` : "",
      empty: "完成第一套卷子后显示", emptyHint: "统计从开始作答到交卷的实际时间。",
    });
    const items = t.by_type.map((x) => ({ label: TYPE[x.question_type], value: x.avg_seconds, valueText: `${Math.round(x.avg_seconds / 6) / 10} 分钟 · ${x.questions} 题`, color: C.COLORS.biochemistry, max: Math.max(...t.by_type.map((y) => y.avg_seconds)) }));
    const slow = [...t.by_type].sort((a, b) => b.avg_seconds - a.avg_seconds)[0];
    C.hbars(el("div", undefined, card), {
      title: "各题型平均每题用时", unit: "每题", items, format: (v) => Math.round(v) + " 秒",
      conclusion: slow ? `${TYPE[slow.question_type]}平均每题 ${Math.round(slow.avg_seconds / 6) / 10} 分钟，用时最长。` : "",
      empty: "完成带用时记录的卷子后显示", emptyHint: "答题时会记录每题停留的时间，交卷后汇总到这里。",
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
  let facets = null;
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
    if (!facets) {
      facets = data.facets;
      drawFilters();
      overviewCharts(data.facets);
    }
    $("mistake-count").textContent = `共 ${data.total} 道`;
    const list = $("mistake-list");
    if (!append) list.replaceChildren();
    data.items.forEach((item) => {
      const li = el("li", undefined, list);
      el("span", item.stem || "（题干不可用）", li);
      const meta = el("div", undefined, li, "mistake-meta");
      el("span", SHORT[item.subject], meta);
      el("span", TYPE[item.question_type], meta);
      if (item.chapter) el("span", item.chapter, meta);
      el("span", `−${item.max_points - item.points} 分`, meta, "lost");
      el("span", dateLabel(item.date), meta);
    });
    $("mistake-empty").hidden = data.total > 0;
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
    chips($("filter-subject"), facets.subject.map((f) => [f.value, `${SHORT[f.value]} ${f.count}`]), filter.subject, (v) => {
      filter.subject = v;
      filter.chapter = "";
      drawFilters();
      loadMistakes();
    });
    chips($("filter-type"), facets.question_type.map((f) => [f.value, `${TYPE[f.value]} ${f.count}`]), filter.question_type, (v) => {
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
  function overviewCharts(f) {
    const max = Math.max(1, ...f.question_type.map((x) => x.count));
    const total = f.question_type.reduce((s, x) => s + x.count, 0);
    const top = [...f.question_type].sort((a, b) => b.count - a.count)[0];
    C.hbars($("mistake-type-chart"), {
      title: "各题型错题数", unit: "道",
      items: f.question_type.map((x) => ({ label: TYPE[x.value], value: x.count, max, color: C.COLORS.biochemistry })),
      format: (v) => v + " 道",
      conclusion: top ? `共 ${total} 道错题，${TYPE[top.value]}最多（${top.count} 道）。` : "",
      empty: "还没有错题", emptyHint: "完成并判分一套卷子后，失分的题会出现在这里。",
    });
    read("/v2/stats/profile").then((p) => {
      const next = p.reviews.next_7_days, sum = next.reduce((s, x) => s + x.count, 0);
      C.columns($("due-chart"), {
        title: "未来 7 天到期分布", unit: "道",
        items: next.map((x, i) => ({ label: i === 0 ? "今天" : dateLabel(x.day), value: x.count, color: i === 0 ? C.COLORS.cell_biology : C.COLORS.biochemistry })),
        conclusion: sum ? `未来 7 天共 ${sum} 道到期，今天 ${next[0].count} 道。` : "",
        empty: "还没有复习卡片", emptyHint: "答错的题会自动进入复习安排。",
      });
    }).catch(() => {});
  }
  $("filter-chapter").onchange = (event) => {
    const option = event.target.selectedOptions[0];
    filter.chapter = event.target.value;
    if (filter.chapter && option?.dataset.subject) filter.subject = option.dataset.subject;
    drawFilters();
    loadMistakes();
  };
  $("mistake-more").onclick = () => loadMistakes(true);
  $("chapter-practice").onclick = () => chapterPractice(filter.subject, filter.chapter);

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
  async function resultExtras(data, root) {
    const counted = data.answers.filter((a) => !a.excluded && a.points !== null);
    const sums = (list) => list.reduce((s, a) => [s[0] + a.points, s[1] + a.max_points], [0, 0]);
    const split = el("div", undefined, root, "score-split");
    Object.keys(SUBJECT).forEach((subject) => {
      const s = data.subject_scores?.[subject];
      const box = el("div", undefined, split, subject === "biochemistry" ? "bio" : "cell");
      el("span", SUBJECT[subject], box);
      el("strong", s ? String(s.score === null ? s.graded_points : s.score) : "—", box);
      el("span", s ? (s.score === null ? `已得分 · 判分中（满分 ${s.max_points}）` : `满分 ${s.max_points} 分`) : "", box, "sub");
    });
    const rows = TYPES.filter((t) => counted.some((a) => a.question_type === t)).map((t) => {
      const [p, m] = sums(counted.filter((a) => a.question_type === t));
      return { label: TYPE[t], value: m ? p / m : null, valueText: `${p}/${m} · ${pct(m ? p / m : null)}`, max: 1, color: C.COLORS.biochemistry };
    });
    const typeBox = el("div", undefined, root);
    const worst = [...rows].filter((r) => r.value !== null).sort((a, b) => a.value - b.value)[0];
    C.hbars(typeBox, {
      title: "题型得分率", unit: "本卷", items: rows, format: pct,
      conclusion: worst ? `本卷${worst.label}得分率最低（${pct(worst.value)}）。` : "",
      empty: "判分完成后显示", emptyHint: "已判分的题按题型汇总。",
    });
    const compare = el("p", undefined, root, "compare");
    const timingBox = el("p", undefined, root, "overtime-note");
    try {
      const [profile, timing] = await Promise.all([read("/v2/stats/profile"), read(`/v2/papers/${data.paper_id}/timing`)]);
      const own = profile.trend.find((r) => r.paper_id === data.paper_id);
      const before = profile.trend.filter((r) => r.paper_id !== data.paper_id && (!own || r.date <= own.date)).slice(-7);
      if (own && before.length) {
        const avg = before.reduce((s, r) => s + r.rate, 0) / before.length;
        const diff = (own.rate - avg) * 100;
        compare.textContent = "";
        el("span", `与此前 ${before.length} 套平均（${pct(avg)}）相比：`, compare);
        el("span", `${sign(diff)} 个百分点`, compare, "delta " + (diff >= 0 ? "up" : "down"));
      } else if (own) compare.textContent = "这是第一套已判分的卷子，之后会和近 7 套平均对比。";
      else compare.textContent = "判分完成后，会和近 7 套平均对比。";
      const over = timing.types.filter((t) => t.over);
      timingBox.textContent = !timing.recorded ? "这一套没有记录到每题用时。" : over.length ? "用时偏长：" + over.map((t) => `${TYPE[t.question_type]}（${Math.round(t.seconds / 60)} 分钟，按分值约 ${Math.round(t.budget_seconds / 60)} 分钟）`).join("；") + "。" : "各题型用时都在按分值分配的时间内。";
      timingBox.className = "overtime-note" + (over.length ? "" : " ok");
    } catch {
      compare.textContent = "";
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

  function onView(view) {
    if (view === "profile") loadProfile();
    if (view === "mistakes") {
      facets = null;
      loadMistakes();
    }
  }

  window.Insights = { loadHome, loadProfile, onView, resultExtras, historyChart, startPractice, chapterPractice };
})();
