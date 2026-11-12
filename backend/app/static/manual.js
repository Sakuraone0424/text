"use strict";
/* Grading by hand: a bottom sheet with the question, your answer, the reference answer and every scoring point to tick
   (得分 / 部分 / 不得分, or − / + to the exact points, never above that point's value); the total follows. Saved scores count,
   are marked 手动判分 / 已手动调整, keep the AI's original, and can be handed back to the AI. */
(function () {
  const h = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  async function call(path, method, body) {
    const response = await fetch(path, { method: method || "GET", credentials: "same-origin", headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    let value = null;
    try { value = await response.json(); } catch { /* no body */ }
    if (!response.ok) throw new Error((value && typeof value.detail === "string" && value.detail) || `操作失败（${response.status}）`);
    return value;
  }
  async function open({ paperId, questionId, onDone }) {
    let form;
    try {
      form = await call(`/v2/papers/${paperId}/manual/${questionId}`);
    } catch (error) {
      window.alert(error.message);
      return;
    }
    const dialog = h("dialog", undefined, document.body, "manual-dialog sheet");
    dialog.setAttribute("aria-labelledby", "manual-title");
    h("h2", form.points === null ? "手动判分" : "修改分数", dialog).id = "manual-title";
    const scroll = h("div", undefined, dialog, "manual-scroll");
    h("p", form.stem, scroll, "manual-stem");
    const mine = h("div", undefined, scroll, "manual-box");
    h("b", "你的答案", mine);
    h("p", form.your_answer || "（未作答）", mine);
    const ref = h("div", undefined, scroll, "manual-box");
    h("b", "参考答案", ref);
    h("p", form.reference || "—", ref);
    h("h3", "评分要点", scroll);
    const values = form.criteria.map((c, i) => (form.criterion_points ? form.criterion_points[i] : 0));
    const total = h("p", undefined, dialog, "manual-total");
    const rows = [];
    const paint = () => {
      const sum = values.reduce((a, b) => a + b, 0);
      total.textContent = `合计 ${sum} / ${form.max_points} 分`;
      rows.forEach((row, i) => {
        const c = form.criteria[i];
        row.value.textContent = `${values[i]} / ${c.points}`;
        row.minus.disabled = values[i] <= 0;
        row.plus.disabled = values[i] >= c.points;
        row.buttons.forEach(([kind, button]) => {
          const on = kind === "none" ? values[i] === 0 : kind === "full" ? values[i] === c.points : values[i] > 0 && values[i] < c.points;
          button.setAttribute("aria-pressed", String(on));
        });
      });
    };
    form.criteria.forEach((c, i) => {
      const box = h("div", undefined, scroll, "manual-criterion");
      h("p", c.description, box, "manual-desc");
      const line = h("div", undefined, box, "manual-controls");
      const seg = h("div", undefined, line, "segmented manual-seg");
      seg.setAttribute("role", "group");
      const buttons = [];
      const kinds = c.points > 1 ? [["none", "不得分", 0], ["part", "部分", Math.floor(c.points) / 2], ["full", "得分", c.points]] : [["none", "不得分", 0], ["full", "得分", c.points]];
      kinds.forEach(([kind, text, to]) => {
        const b = h("button", text, seg);
        b.type = "button";
        b.onclick = () => { values[i] = to; paint(); };
        buttons.push([kind, b]);
      });
      const step = h("div", undefined, line, "manual-step");
      const minus = h("button", "−", step);
      minus.type = "button";
      minus.setAttribute("aria-label", "减 1 分");
      const value = h("span", undefined, step, "manual-value");
      const plus = h("button", "+", step);
      plus.type = "button";
      plus.setAttribute("aria-label", "加 1 分");
      minus.onclick = () => { values[i] = Math.max(0, values[i] - 1); paint(); };
      plus.onclick = () => { values[i] = Math.min(c.points, values[i] + 1); paint(); };
      rows.push({ value, minus, plus, buttons });
    });
    const note = h("input", undefined, scroll, "manual-note");
    note.type = "text";
    note.maxLength = 500;
    note.placeholder = "备注（一句话，可不写）";
    note.setAttribute("aria-label", "备注");
    note.value = form.note || "";
    if (form.ai_original) {
      const orig = h("details", undefined, scroll, "manual-original");
      h("summary", "查看 AI 原始分数", orig);
      h("p", `${form.ai_original.graded_by || "AI"} 给 ${form.ai_original.points} / ${form.max_points} 分` + (form.ai_original.comment ? "：" + form.ai_original.comment : ""), orig);
    }
    const message = h("p", undefined, dialog, "manual-message");
    message.setAttribute("role", "status");
    const buttons = h("div", undefined, dialog, "button-row");
    const cancel = h("button", "取消", buttons, "secondary");
    cancel.type = "button";
    const save = h("button", "保存", buttons, "primary");
    save.type = "button";
    if (form.label) {
      const back = h("button", "交给 AI 重新判分", dialog, "secondary full-width manual-ai");
      back.type = "button";
      back.onclick = async () => {
        back.disabled = true;
        try {
          await call(`/v2/papers/${paperId}/manual/${questionId}/ai`, "POST");
          dialog.close();
          if (onDone) onDone();
        } catch (error) {
          message.textContent = error.message;
          back.disabled = false;
        }
      };
    }
    cancel.onclick = () => dialog.close();
    save.onclick = async () => {
      save.disabled = true;
      try {
        await call(`/v2/papers/${paperId}/manual/${questionId}`, "PUT", { criterion_points: values, note: note.value.trim() || null });
        dialog.close();
        if (onDone) onDone();
      } catch (error) {
        message.textContent = error.message;
        save.disabled = false;
      }
    };
    dialog.addEventListener("close", () => { setTimeout(() => dialog.remove(), 260); });
    paint();
    dialog.showModal();
  }
  window.Manual = { open };
})();
