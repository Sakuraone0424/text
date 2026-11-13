"use strict";
// Unit tests of the page-free decisions (app/static/logic.js). Run: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../../app/static/logic.js");

const clock = () => "23:00";
const bank = (over) => ({ complete: true, missing: 0, short_cells: [], eta_state: "done", eta_at: null, ...over });

const base = { name: "客观题卷", minutes: 60, known: null, active: false, today: {}, mine: null, lock: null, loaded: true };
const card = (over) => L.cardState({ ...base, ...over }, clock);

test("nothing known yet: a grey placeholder, never 已就绪", () => {
  assert.equal(card({ loaded: false }).kind, "loading");
  assert.equal(card({ lock: null }).kind, "loading");
  assert.notEqual(card({ lock: null }).pill, "已就绪");
});

test("a full bank: prepare, named after the paper", () => {
  const s = card({ lock: bank() });
  assert.deepEqual([s.kind, s.pill, s.label], ["prepare", "已就绪", "准备客观题卷"]);
  assert.equal(card({ name: "主观题卷", lock: bank() }).label, "准备主观题卷");
});

test("one cell short: not ready, the total and the cell are named, with the estimate when there is one", () => {
  const short = bank({ complete: false, missing: 7, short_cells: [{ subject: "cell_biology", question_type: "single_choice", missing: 7 }], eta_state: "estimated", eta_at: "2026-11-02T15:00:00+00:00" });
  const s = card({ lock: short });
  assert.equal(s.kind, "short");
  assert.equal(s.label, "题库还差 7 道，预计 23:00 可开始");
  assert.match(s.reason, /细胞单选缺 7/);
  assert.equal(card({ lock: { ...short, eta_state: "estimating", eta_at: null } }).label, "题库还差 7 道，暂不能开始");
});

test("the missing total is the sum of the short cells (a surplus elsewhere does not hide it)", () => {
  const short = bank({ complete: false, missing: 1, short_cells: [{ subject: "biochemistry", question_type: "term", missing: 2 }, { subject: "cell_biology", question_type: "essay", missing: 1 }] });
  assert.match(card({ lock: short }).label, /^题库还差 3 道/);
});

test("a paper that exists decides by itself: start (with its own minutes), continue with the time left, result", () => {
  assert.equal(card({ known: { status: "not_started", paperId: 5 }, minutes: 90 }).label, "开始（90 分钟）");
  assert.equal(card({ known: { status: "in_progress", paperId: 5 }, active: true, today: { clock: "41:07" } }).label, "继续作答（剩余 41:07）");
  assert.equal(card({ known: { status: "in_progress", paperId: 5 }, active: true, today: { clock: "尚未开始" } }).label, "继续作答（剩余 --:--）");
  assert.equal(card({ known: { status: "in_progress", paperId: 5 } }).label, "继续作答");        // open on another device / page: no clock to show
  assert.equal(card({ known: { status: "submitted", paperId: 5 }, mine: { points: 112, max: 150 } }).label, "查看成绩（112 / 150）");
  assert.equal(card({ known: { status: "in_progress", paperId: 7 } }).paperId, 7);
});

test("handed in, written answers still being graded: the objective part, never a total of 0", () => {
  const known = { status: "submitted", paperId: 5 };
  assert.equal(card({ known, mine: { objective_points: 38, objective_max: 60 } }).label, "查看成绩（客观题 38 / 60）");
  assert.equal(card({ known, mine: {} }).label, "查看成绩（判分中）");
});

test("two papers never share a state: each card is decided from its own inputs", () => {
  const objective = card({ known: { status: "submitted", paperId: 1 }, mine: { points: 120, max: 150 } });
  const subjective = card({ name: "主观题卷", lock: bank({ complete: false, missing: 2, short_cells: [] }) });
  assert.equal(objective.kind, "result");
  assert.equal(subjective.kind, "short");
});

test("when a card is due, in words", () => {
  const md = (d) => "D" + d;
  assert.equal(L.dueText("2026-11-05", "2026-11-05", md), "今天");
  assert.equal(L.dueText("2026-11-01", "2026-11-05", md), "今天");          // overdue is due today
  assert.equal(L.dueText("2026-11-06", "2026-11-05", md), "明天");
  assert.equal(L.dueText("2026-11-09", "2026-11-05", md), "4 天后");
  assert.equal(L.dueText("2026-11-20", "2026-11-05", md), "D2026-11-20");
  assert.equal(L.dueText("2026-12-01", "2026-11-30", md), "明天");          // across a month end
});

test("grading by hand: the quick choices and the stepper stay inside the scoring point", () => {
  assert.deepEqual(L.criterionChoices(1).map((c) => c[0]), ["none", "full"]);
  assert.deepEqual(L.criterionChoices(3).map((c) => c[2]), [0, 1.5, 3]);
  assert.deepEqual(L.criterionChoices(2.5).map((c) => c[2]), [0, 1, 2.5]);      // rescaled questions have half points
  assert.deepEqual(L.criterionChoices(6).map((c) => c[2]), [0, 3, 6]);
  assert.equal(L.criterionKind(0, 3), "none");
  assert.equal(L.criterionKind(2, 3), "part");
  assert.equal(L.criterionKind(3, 3), "full");
  assert.equal(L.step(3, 1, 3), 3);
  assert.equal(L.step(0, -1, 3), 0);
  assert.equal(L.step(1, 1, 3), 2);
});
