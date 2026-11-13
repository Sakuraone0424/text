"use strict";
/* 我的 → 出题对比 (blind comparison of the two question writers) and 模型与费用 → 出题执行方 (who writes / reviews / grades).
   Numbers come from /v2/compare and /v2/model/executors; the compare page is told nothing about who wrote what until every pair is rated. */
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  const call = (path, method = "GET", body) => Data.fetchJSON(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
  const SUBJECT = { biochemistry: "生化", cell_biology: "细胞" };
  let openSet = null;

  /* ---------------- the comparison ---------------- */
  function questionCard(parent, q) {
    const box = el("div", undefined, parent, "cmp-question");
    el("p", q.stem, box, "cmp-stem");
    if (Object.keys(q.options || {}).length) {
      const list = el("ul", undefined, box, "plain-list");
      Object.entries(q.options).sort().forEach(([k, v]) => el("li", `${k}. ${v}`, list));
    }
    el("p", "参考答案：" + q.answer + (q.accepted_answers?.length ? "（也可：" + q.accepted_answers.join("、") + "）" : ""), box, "cmp-answer");
    const rubric = el("ul", undefined, box, "plain-list cmp-rubric");
    q.rubric.forEach((r) => el("li", `${r.description}（${r.points} 分）`, rubric));
    const more = el("details", undefined, box, "meta detail");
    el("summary", "教材依据", more);
    (q.evidence || []).forEach((e) => el("p", `${e.chapter || ""}：${e.excerpt || ""}`, more, "reason-line"));
  }

  function ratingForm(parent, setId, position, side, label, rating, locked) {
    const form = el("form", undefined, parent, "cmp-rating");
    form.dataset.side = side;
    const fields = [["realism", "像真题程度"], ["correct", "答案正确"], ["rubric", "评分要点合理"]];
    for (const [name, text] of fields) {
      const row = el("label", undefined, form, "cmp-field");
      el("span", text, row);
      const select = el("select", undefined, row);
      select.name = name;
      select.setAttribute("aria-label", `${label}：${text}`);
      el("option", "—", select).value = "";
      for (let n = 1; n <= 5; n++) el("option", String(n), select).value = String(n);
      select.value = rating ? String(rating[name]) : "";
      select.disabled = locked;
    }
    const note = el("input", undefined, form, "cmp-note");
    note.name = "note";
    note.type = "text";
    note.maxLength = 300;
    note.placeholder = "一句话备注（可不写）";
    note.setAttribute("aria-label", `${label}：备注`);
    note.value = rating?.note || "";
    note.disabled = locked;
    const save = el("button", rating ? "已评分，可修改" : "保存评分", form, "secondary");
    save.type = "submit";
    save.disabled = locked;
    form.onsubmit = async (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(form).entries());
      if (!v.realism || !v.correct || !v.rubric) {
        save.textContent = "三项都要打分（1–5）";
        return;
      }
      try {
        await call(`/v2/compare/${setId}/rate`, "POST", { position, side, realism: +v.realism, correct: +v.correct, rubric: +v.rubric, note: v.note || null });
        await showSet(setId);
      } catch (error) {
        save.textContent = "保存失败：" + error.message;
      }
    };
  }

  function reveal(root, data) {
    const box = el("section", undefined, root, "card cmp-reveal");
    el("h2", "揭晓", box);
    const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
    const num = (v) => (v === null || v === undefined ? "—" : v);
    const table = el("table", undefined, box, "cmp-table");
    const head = el("tr", undefined, table);
    ["", "出题数", "程序检查通过", "审核通过", "像真题", "答案正确", "要点合理"].forEach((t) => el("th", t, head));
    for (const key of ["claude", "deepseek"]) {
      const e = data.executors[key];
      const row = el("tr", undefined, table);
      el("th", e.name, row);
      [e.tasks, pct(e.program_check_rate), pct(e.review_rate) + (e.review_pending ? `（${e.review_pending} 道审核中）` : ""), num(e.realism), num(e.correct), num(e.rubric)].forEach((v) => el("td", String(v), row));
    }
    const lines = el("ul", undefined, box, "plain-list");
    data.pairs.forEach((p) => el("li", `第 ${p.position} 对（${TYPE[p.question_type] || ""}）：甲 = ${p.A}，乙 = ${p.B}`, lines));
    el("p", "两边的题都由 DeepSeek 统一审核；通过审核的题已正常入库。", box, "note");
  }

  async function showSet(id) {
    openSet = id;
    const root = $("compare-set");
    let data;
    try {
      data = await call(`/v2/compare/${id}`);
    } catch (e) {
      root.replaceChildren();
      el("p", "读取失败：" + e.message, root, "error-card");
      return;
    }
    root.replaceChildren();
    const waiting = data.waiting ? `还有 ${data.waiting} 对在等两边出题完成。` : "";
    el("p", `共 ${data.pairs.length} 对。${waiting}两道题只标「甲」「乙」，评完所有可比较的题才会揭晓是谁出的。`, root, "note");
    for (const pair of data.pairs) {
      const card = el("section", undefined, root, "card cmp-pair");
      card.dataset.position = String(pair.position);
      el("h2", `第 ${pair.position} 对 · ${SUBJECT[pair.subject] || ""}${TYPE[pair.question_type] || ""}`, card);
      if (pair.status === "waiting") {
        el("p", "两边还没都出完，稍后再来看。", card, "muted");
        continue;
      }
      if (pair.status === "one_side_only") {
        el("p", "这一对只有一边出成了题，不能比较；揭晓时会算进通过率。", card, "muted");
        continue;
      }
      const grid = el("div", undefined, card, "cmp-grid");
      for (const s of pair.sides) {
        const col = el("div", undefined, grid, "cmp-side");
        el("h3", s.label, col);
        questionCard(col, s.question);
        ratingForm(col, id, pair.position, s.side, s.label, s.rating, false);
      }
    }
    if (data.reveal) reveal(root, data.reveal);
  }

  async function show() {
    const root = $("compare-list");
    root.replaceChildren();
    let list;
    try {
      list = (await call("/v2/compare")).sets;
    } catch (e) {
      el("p", "读取失败：" + e.message, root, "error-card");
      return;
    }
    if (!list.length) el("p", "还没有对比。点「开始一次对比」会从当前缺口里抽 10 份相同的出题任务，同一份内容分别交给 DeepSeek 和 Claude 定时任务。", root, "muted");
    for (const s of list) {
      const b = el("button", `第 ${s.set_id} 次 · ${s.pairs} 对 · ${s.complete ? "已揭晓" : s.waiting ? `${s.waiting} 对未出完` : "待评分"}`, root, "secondary cmp-set-button");
      b.type = "button";
      b.onclick = () => showSet(s.set_id);
    }
    if (openSet) showSet(openSet);
    else if (list.length) showSet(list[0].set_id);
  }

  const startButton = $("compare-start");
  if (startButton) {
    startButton.onclick = async () => {
      startButton.disabled = true;
      try {
        const r = await call("/v2/compare", "POST", {});
        $("compare-note").textContent = r.pairs ? `已排出 ${r.pairs} 对任务` + (r.skipped ? `（${r.skipped} 个题型当前没有缺口或写不出，已跳过）` : "") + "。等两边出完后在这里评分。" : "当前没有可抽的缺口，没有排任务。";
        openSet = r.pairs ? r.set_id : openSet;
        await show();
      } catch (e) {
        $("compare-note").textContent = "没有排成：" + e.message;
      } finally {
        startButton.disabled = false;
      }
    };
  }

  /* ---------------- who does which kind of task ---------------- */
  const MODES = [["deepseek", "DeepSeek"], ["claude", "Claude"], ["claude_first", "Claude 优先（超时转 DeepSeek）"]];
  async function showExecutors() {
    const root = $("executor-form");
    if (!root) return;
    let data;
    try {
      data = await call("/v2/model/executors");
    } catch (e) {
      $("executor-note").textContent = "读取失败：" + e.message;
      return;
    }
    root.replaceChildren();
    for (const [key, label] of [["generation", "出题"], ["review", "审核"], ["grading", "判分"]]) {
      const row = el("label", undefined, root, "field");
      el("span", label, row, "label");
      const select = el("select", undefined, row);
      select.id = "executor-" + key;
      MODES.forEach(([value, text]) => (el("option", text, select).value = value));
      select.value = data.routing[key];
    }
    const hours = el("label", undefined, root, "field");
    el("span", "Claude 优先：多少小时没人领就交给 DeepSeek", hours, "label");
    const input = el("input", undefined, hours);
    input.id = "executor-hours";
    input.type = "number";
    input.min = "1";
    input.max = "72";
    input.value = String(data.routing.fallback_hours);
    const save = el("button", "保存执行方设置", root, "primary");
    save.id = "executor-save";
    save.type = "button";
    save.onclick = async () => {
      try {
        await call("/v2/model/executors", "PUT", {
          generation: $("executor-generation").value, review: $("executor-review").value, grading: $("executor-grading").value,
          fallback_hours: parseInt($("executor-hours").value, 10),
        });
        $("executor-note").textContent = "已保存。";
      } catch (e) {
        $("executor-note").textContent = "保存失败：" + e.message;
      }
    };
    $("executor-note").textContent = (data.token_configured ? "外部执行接口已启用。" : "还没有设置 EXTERNAL_EXECUTOR_TOKEN，Claude 定时任务暂时领不到任务。") +
      (data.grading_trigger_configured ? " 判分触发已配置。" : "");
  }

  window.CompareView = { show };
  window.ExecutorSettings = { show: showExecutors };
})();
