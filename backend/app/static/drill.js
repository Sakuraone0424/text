"use strict";
/* The practice answer page: its own full-screen page (top bar: back + 「专项练习 2 / 5」 + progress; bottom bar: 上一题 / 下一题),
   no bottom navigation, the page is exactly the visible part when the keyboard is up. Leaving asks first and returns to the practice
   centre where it was; finishing opens the practice review (the practice's result page). Questions and answers are the practice
   centre's own: a set drawn there, each answer posted at once. */
(function () {
  const $ = (id) => document.getElementById(id);
  const SHORT = { biochemistry: "生化", cell_biology: "细胞" };
  const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };
  const WRITTEN = ["term", "short_answer", "essay"];
  const h = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  let set = null;            // { set_id, questions, time_limit_seconds }
  let index = 0;
  let done = {};             // question id -> { value, result }
  let timer = null;
  let started = 0;
  let returnScroll = 0;

  async function send(path, body) {
    const response = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    let value = null;
    try { value = await response.json(); } catch { /* none */ }
    if (!response.ok) {
      const detail = value && value.detail;
      throw new Error(typeof detail === "string" ? detail : "操作失败（" + response.status + "）");
    }
    return value;
  }
  const scroller = () => document.querySelector(".workspace");

  /* start a set: draw it, then go to its page (the centre's scroll position is kept for the way back) */
  async function start(body, onError) {
    returnScroll = scroller()?.scrollTop || 0;
    let drawn;
    try {
      drawn = await send("/v2/practice", body);
    } catch (error) {
      if (onError) onError(error);
      return;
    }
    set = drawn;
    index = 0;
    done = {};
    started = Date.now();
    location.hash = "#/drill/" + drawn.set_id;
  }

  function finish() {
    clearInterval(timer);
    const id = set.set_id;
    set = null;
    location.hash = "#/review/s" + id;       // the practice's result page
  }

  function confirmLeave() {
    const dialog = h("dialog", undefined, document.body, "start-dialog sheet");
    h("h2", "退出专项练习？", dialog);
    h("p", "已提交的题会保留在这套练习里，没做的题不计分。", dialog);
    const row = h("div", undefined, dialog, "button-row");
    const stay = h("button", "继续做题", row, "primary");
    stay.type = "button";
    const leave = h("button", "退出", row, "secondary");
    leave.type = "button";
    stay.onclick = () => dialog.close();
    leave.onclick = () => {
      dialog.close();
      clearInterval(timer);
      set = null;
      location.hash = "#/practice";
      setTimeout(() => { const s = scroller(); if (s) s.scrollTop = returnScroll; }, 60);
    };
    dialog.addEventListener("close", () => setTimeout(() => dialog.remove(), 260));
    dialog.showModal();
  }

  function clock(remaining) {
    const m = Math.floor(remaining / 60), s = remaining % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function draw() {
    const root = $("drill-view");
    root.replaceChildren();
    if (!set) return;
    const q = set.questions[index];
    const answered = done[q.id];
    const count = set.questions.length;
    const top = h("div", undefined, root, "drill-top");
    const back = h("button", undefined, top, "icon-button");
    back.type = "button";
    back.setAttribute("aria-label", "退出专项练习");
    back.innerHTML = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';
    back.onclick = confirmLeave;
    const title = h("div", undefined, top, "drill-title");
    h("b", `专项练习 ${index + 1} / ${count}`, title);
    const sub = h("span", `${SHORT[q.subject]} · ${TYPE[q.question_type]} · ${q.max_points} 分`, title);
    if (set.time_limit_seconds) {
      const left = Math.max(0, set.time_limit_seconds - Math.round((Date.now() - started) / 1000));
      sub.textContent += " · 剩余 " + clock(left);
    }
    const bar = h("progress", undefined, root, "drill-progress");
    bar.max = count;
    bar.value = Object.keys(done).length;
    bar.setAttribute("aria-label", "已作答进度");
    const body = h("div", undefined, root, "drill-body");
    h("p", "不计入每日成绩", body, "muted small");
    h("p", q.stem, body, "question-stem");
    let chosen = answered ? answered.value.text || "" : "";
    const fields = [];
    const questionStart = Date.now();
    const submit = () => root.querySelector(".drill-main");
    const refresh = () => {
      const main = submit();
      if (main && !answered) main.disabled = !(chosen || fields.some((f) => f.value.trim()));
    };
    if (q.question_type === "single_choice") {
      const box = h("div", undefined, body);
      Object.entries(q.options).sort(([a], [b]) => a.localeCompare(b)).forEach(([letter, text]) => {
        const option = h("button", undefined, box, "option");
        option.type = "button";
        h("span", letter, option, "option-letter");
        h("span", text, option);
        option.classList.toggle("chosen", chosen === letter);
        option.disabled = Boolean(answered);
        option.onclick = () => {
          chosen = letter;
          box.querySelectorAll(".option").forEach((x) => x.classList.toggle("chosen", x === option));
          refresh();
        };
      });
    } else if (WRITTEN.includes(q.question_type)) {
      const area = h("textarea", undefined, body, "drill-answer");
      area.rows = q.question_type === "essay" ? 9 : 5;
      area.setAttribute("aria-label", "作答");
      area.placeholder = "写下你的作答，交卷后由 AI 判分";
      if (answered) { area.value = answered.value.text || ""; area.disabled = true; }
      area.oninput = refresh;
      fields.push(area);
    } else {
      for (let i = 0; i < q.blank_count; i++) {
        const input = h("input", undefined, body, "drill-answer");
        input.setAttribute("aria-label", q.blank_count > 1 ? `第${i + 1}空` : "答案");
        input.placeholder = q.blank_count > 1 ? `第${i + 1}空` : "填写答案";
        if (answered) { input.value = (answered.value.blanks ? answered.value.blanks[i] : answered.value.text) || ""; input.disabled = true; }
        input.oninput = refresh;
        fields.push(input);
      }
    }
    const feedback = h("div", undefined, body);
    const paintFeedback = (r) => {
      feedback.replaceChildren();
      if (r.pending) {
        feedback.className = "practice-feedback";
        h("strong", "已交，AI 判分中", feedback);
        h("p", r.queued ? "分数出来后会显示在这套的成绩页里；低于合格线的会进入错题本。" : "暂时无法发送给 AI 判分，分数会在可判时补上（不会记 0 分）。", feedback, "muted small");
        if (r.answer) h("p", "参考答案：" + r.answer, feedback);
      } else {
        feedback.className = "practice-feedback " + (r.correct ? "ok" : "bad");
        h("strong", r.correct ? "回答正确" : `未答对（${r.points}/${r.max_points} 分）`, feedback);
        h("p", "参考答案：" + [r.answer, ...(r.accepted_answers || []).filter((x) => x !== r.answer)].join(" / "), feedback);
      }
      (r.why || []).forEach((w) => h("p", "得分点：" + w, feedback, "muted small"));
    };
    if (answered) paintFeedback(answered.result);
    const actions = h("div", undefined, root, "drill-actions");
    const prev = h("button", "上一题", actions, "secondary");
    prev.type = "button";
    prev.disabled = index === 0;
    prev.onclick = () => { index -= 1; draw(); };
    const last = index === count - 1;
    const main = h("button", undefined, actions, "primary drill-main");
    main.type = "button";
    if (answered) {
      main.textContent = last ? "查看成绩" : "下一题";
      main.onclick = () => { if (last) finish(); else { index += 1; draw(); } };
    } else {
      main.textContent = "提交答案";
      main.disabled = !(chosen);
      main.onclick = async () => {
        main.disabled = true;
        const seconds = Math.min(7200, Math.max(0, Math.round((Date.now() - questionStart) / 1000)));
        const payload = WRITTEN.includes(q.question_type) ? { question_id: q.id, text: fields[0].value, seconds }
          : q.question_type === "single_choice" ? { question_id: q.id, text: chosen, seconds }
          : q.blank_count > 1 ? { question_id: q.id, blanks: fields.map((f) => f.value), seconds } : { question_id: q.id, text: fields[0].value, seconds };
        try {
          const result = await send(`/v2/practice/${set.set_id}/answer`, payload);
          done[q.id] = { value: payload, result };
          draw();
        } catch (error) {
          main.disabled = false;
          feedback.className = "practice-feedback bad";
          feedback.textContent = error.message;
        }
      };
    }
    if (fields.length && !answered) setTimeout(() => fields[0].focus({ preventScroll: true }), 80);
    refresh();
  }

  /* the router calls this when #/drill/<id> is shown */
  function show(param) {
    if (!set || String(set.set_id) !== String(param || "").replace(/^s/, "")) {
      // a reload or a stale link: the questions are not here any more; the set's own page is the result so far
      if (param) location.hash = "#/practice";
      return;
    }
    clearInterval(timer);
    if (set.time_limit_seconds) timer = setInterval(() => {
      if (!set) { clearInterval(timer); return; }
      if (Date.now() - started >= set.time_limit_seconds * 1000) { finish(); return; }
      const sub = document.querySelector("#drill-view .drill-title span");
      if (sub) sub.textContent = sub.textContent.replace(/剩余 \d\d:\d\d/, "剩余 " + clock(Math.max(0, set.time_limit_seconds - Math.round((Date.now() - started) / 1000))));
    }, 1000);
    draw();
  }
  window.Drill = { start, show, state: () => (set ? { set_id: set.set_id, index } : null) };
})();
