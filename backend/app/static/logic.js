"use strict";
/* Decisions with no page in them: what they are given goes in, what the page should show comes out. They are the same
   functions the pages call (pages.js, manual.js) and the ones the unit tests call (tests/js, run by `node --test`). */
(function (root) {
  const SHORT = { biochemistry: "生化", cell_biology: "细胞" };
  const TYPE = { single_choice: "单选", fill_blank: "填空", term: "名词解释", short_answer: "简答", essay: "问答" };

  /* One of today's two papers (客观题卷 / 主观题卷): one state for its home card and its practice card.
     name    the paper's name ("客观题卷")           minutes  its time limit
     known   { status: "in_progress" | "submitted" | "not_started", paperId } once a paper of this kind exists (what the exam page
             showed last, else what the overview says), else null
     active  this kind's paper is the one open on the exam page   today  { clock } the exam page's clock text
     mine    today's paper of this kind as the overview knows it ({ points, max, objective_points, objective_max }), or null
     lock    the bank for this kind ({ complete, missing, short_cells, eta_state, eta_at }) while no paper of it exists, else null
     loaded  the overview has answered at least once
     clock   (iso) => "HH:MM" text for the estimate */
  function cardState({ name, minutes, known, active, today, mine, lock, loaded }, clock) {
    const t = today || {};
    if (known && known.status === "in_progress") {
      const left = active && t.clock && /\d/.test(t.clock) ? t.clock : "--:--";
      return { kind: "continue", label: active ? `继续作答（剩余 ${left}）` : "继续作答", pill: "正在作答", paperId: known.paperId };
    }
    if (known && known.status === "submitted") {
      const done = mine && mine.points !== undefined && mine.points !== null;
      return { kind: "result", label: done ? `查看成绩（${mine.points} / ${mine.max}）` : mine && mine.objective_max ? `查看成绩（客观题 ${mine.objective_points} / ${mine.objective_max}）` : "查看成绩（判分中）", pill: "已提交", green: true, paperId: known.paperId };
    }
    if (known && known.status === "not_started") return { kind: "start", label: `开始（${minutes} 分钟）`, pill: "尚未开始", green: true, paperId: known.paperId };
    // never "已就绪" before the state is actually known: the bank (no paper yet) or the paper itself
    if (!loaded || !lock) return { kind: "loading", label: "开始", pill: "状态待载入" };
    if (!lock.complete) {
      const when = lock.eta_state === "estimated" && lock.eta_at ? `，预计 ${clock(lock.eta_at)} 可开始` : "，暂不能开始";
      const cells = (lock.short_cells || []).map((c) => `${SHORT[c.subject]}${TYPE[c.question_type]}缺 ${c.missing}`).join("、");
      const missing = (lock.short_cells || []).reduce((n, c) => n + c.missing, 0) || lock.missing;
      return { kind: "short", label: `题库还差 ${missing} 道${when}`, pill: "题库准备中", reason: `每科每个题型都要凑够一张卷子的新题，不会用旧题凑。${cells ? "还差：" + cells + "。" : ""}` };
    }
    return { kind: "prepare", label: `准备${name}`, pill: "已就绪" };
  }

  /* When a card is next due, in words: 今天 / 明天 / N 天后 / the date. */
  function dueText(day, today, md) {
    const diff = Math.round((new Date(day + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
    return diff <= 0 ? "今天" : diff === 1 ? "明天" : diff < 7 ? `${diff} 天后` : md(day);
  }

  /* Grading by hand: the quick choices for one scoring point worth `points` ([kind, text, score]); a 1-point item has no "part". */
  function criterionChoices(points) {
    return points > 1 ? [["none", "不得分", 0], ["part", "部分", Math.floor(points) / 2], ["full", "得分", points]] : [["none", "不得分", 0], ["full", "得分", points]];
  }
  /* ... and which of them a score lights up. */
  function criterionKind(value, points) {
    return value <= 0 ? "none" : value >= points ? "full" : "part";
  }
  /* A step of the − / + buttons never leaves 0..points. */
  function step(value, by, points) {
    return Math.max(0, Math.min(points, value + by));
  }

  const api = { cardState, dueText, criterionChoices, criterionKind, step };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Logic = api;
})(typeof window !== "undefined" ? window : globalThis);
