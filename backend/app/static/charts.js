/* Small SVG chart kit: line, horizontal bars, columns, progress bar, heat map.
   No libraries, no network: works offline and in iPhone Safari. Every chart has a title, a unit,
   an empty state, a one-sentence conclusion, an aria-label, and tap / long-press / keyboard readout. */
"use strict";
(() => {
  const NS = "http://www.w3.org/2000/svg";
  // Colours are CSS variables (tokens.css), so every chart follows light / dark with no redraw.
  const COLORS = {
    biochemistry: "var(--chart-bio)",
    cell_biology: "var(--chart-cell)",
    total: "var(--chart-total)",
    grid: "var(--chart-grid)",
    axis: "var(--chart-axis)",
    good: "var(--up)",
    warn: "var(--accent-ink)",
    danger: "var(--danger)",
    soft: "var(--primary-soft)",
    track: "var(--chart-track)",
    surface: "var(--card)",
  };
  const html = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    if (parent) parent.append(node);
    return node;
  };
  const svg = (tag, attrs, parent) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      // a colour given as var(--x) must go through style: presentation attributes do not resolve variables
      if (["fill", "stroke", "stop-color"].includes(k) && String(v).startsWith("var(")) node.style.setProperty(k, String(v));
      else node.setAttribute(k, String(v));
    });
    if (parent) parent.append(node);
    return node;
  };
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));

  /* A card shell: title, unit, body, readout line, conclusion sentence. */
  function frame(root, spec) {
    root.replaceChildren();
    root.classList.add("chart-block");
    if (!spec.hideTitle) {
      const head = html("div", undefined, root, "chart-head");
      html("h3", spec.title, head, "chart-title");
      if (spec.unit) html("span", spec.unit, head, "chart-unit");
    }
    return {
      body: html("div", undefined, root, "chart-body"),
      readout: html("p", spec.readoutHint || "点按数据点查看具体数值", root, "chart-readout"),
      conclusion: html("p", spec.conclusion || "", root, "chart-conclusion"),
    };
  }
  function empty(root, spec) {
    root.replaceChildren();
    root.classList.add("chart-block");
    if (!spec.hideTitle) {
      const head = html("div", undefined, root, "chart-head");
      html("h3", spec.title, head, "chart-title");
    }
    const box = html("div", undefined, root, "chart-empty");
    box.innerHTML = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg>';
    html("strong", spec.empty || "暂无数据", box);
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", spec.title + "：" + (spec.empty || "暂无数据"));
  }
  /* Tap, long-press and keyboard focus all show the value in the readout line. */
  function bind(node, readout, text) {
    node.setAttribute("tabindex", "0");
    node.setAttribute("role", "button");
    node.setAttribute("aria-label", text);
    const show = () => {
      readout.textContent = text;
      readout.classList.add("on");
    };
    node.addEventListener("click", show);
    node.addEventListener("focus", show);
    node.addEventListener("touchstart", show, { passive: true });
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        show();
      }
    });
  }

  /* line({title, unit, series:[{name,color,points:[{label,value,note}]}], yMin, yMax, format, conclusion, empty}) */
  function line(root, spec) {
    const series = (spec.series || []).filter((s) => s.points.some((p) => p.value !== null && p.value !== undefined));
    if (!series.length) return empty(root, spec);
    const f = frame(root, spec);
    const W = 340,
      H = 190,
      L = 34,
      R = 10,
      T = 12,
      B = 30;
    const n = Math.max(...series.map((s) => s.points.length));
    const values = series.flatMap((s) => s.points.map((p) => p.value)).filter((v) => v !== null && v !== undefined);
    const yMin = spec.yMin ?? Math.min(0, ...values),
      yMax = spec.yMax ?? Math.max(1, ...values);
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1));
    const y = (v) => T + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - T - B);
    const format = spec.format || num;
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "chart-svg" }, f.body);
    chart.setAttribute("aria-label", `${spec.title}：${series.map((s) => `${s.name}共${s.points.length}个点`).join("，")}。${spec.conclusion || ""}`);
    svg("title", {}, chart).textContent = spec.title;
    [0, 0.5, 1].forEach((t) => {
      const value = yMin + (yMax - yMin) * t,
        yy = y(value);
      svg("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: COLORS.grid, "stroke-width": 1 }, chart);
      svg("text", { x: L - 6, y: yy + 4, "text-anchor": "end", fill: COLORS.axis, "font-size": 11 }, chart).textContent = format(value);
    });
    const labels = series[0].points;
    const step = Math.max(1, Math.ceil(labels.length / 5));
    labels.forEach((p, i) => {
      if (i % step && i !== labels.length - 1) return;
      // the end labels hang inward so they never leave the chart
      const anchor = labels.length > 1 && i === labels.length - 1 ? "end" : labels.length > 1 && i === 0 && x(i) < L + 14 ? "start" : "middle";
      svg("text", { x: anchor === "end" ? Math.min(x(i) + 4, W) : x(i), y: H - 8, "text-anchor": anchor, fill: COLORS.axis, "font-size": 11 }, chart).textContent = p.label;
    });
    series.forEach((s) => {
      const pts = s.points.map((p, i) => (p.value === null || p.value === undefined ? null : [x(i), y(p.value), p, i]));
      const path = [];
      pts.forEach((pt) => {
        if (pt) path.push((path.length ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1));
      });
      const line = svg("path", { d: path.join(" "), fill: "none", stroke: s.color, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round" }, chart);
      window.Motion?.draw(line, "line:" + spec.title + ":" + s.name);      // drawn left to right, once, when first in view
      pts.forEach((pt) => {
        if (!pt) return;
        // a larger invisible hit area for thumbs sits UNDER the visible dot and forwards taps to it
        const hit = svg("circle", { cx: pt[0], cy: pt[1], r: 12, fill: "transparent" }, chart);
        const dot = svg("circle", { cx: pt[0], cy: pt[1], r: 4, fill: COLORS.surface, stroke: s.color, "stroke-width": 2, class: "chart-dot" }, chart);
        bind(dot, f.readout, `${s.name} ${pt[2].label}：${format(pt[2].value)}${spec.unit ? " " + spec.unit : ""}${pt[2].note ? "（" + pt[2].note + "）" : ""}`);
        hit.addEventListener("click", () => dot.dispatchEvent(new Event("click")));
        hit.addEventListener("touchstart", () => dot.dispatchEvent(new Event("click")), { passive: true });
      });
    });
    if (series.length > 1) {
      const legend = html("div", undefined, f.body, "chart-legend");
      series.forEach((s) => {
        const item = html("span", undefined, legend);
        html("i", undefined, item).style.background = s.color;
        item.append(s.name);
      });
    }
    return root;
  }

  /* hbars({title, unit, items:[{label,value,max,color,note,sub:[{name,value,color}]}], format, conclusion, empty}) */
  function hbars(root, spec) {
    const items = spec.items || [];
    if (!items.length) return empty(root, spec);
    const f = frame(root, { ...spec, readoutHint: spec.readoutHint || "点按条形查看具体数值" });
    const format = spec.format || pct;
    const list = html("div", undefined, f.body, "hbars");
    items.forEach((item) => {
      const row = html("div", undefined, list, "hbar-row");
      const grouped = Boolean(item.sub);
      const head = html("div", undefined, row, "hbar-head");
      html("span", item.label, head, "hbar-label");
      // a grouped item shows each bar's own value on the bar's line; a single bar keeps its value in the heading
      if (!grouped) html("span", item.valueText || format(item.value), head, "hbar-value");
      (item.sub || [{ name: item.label, value: item.value, color: item.color }]).forEach((bar) => {
        const none = bar.value === null || bar.value === undefined;
        const line = html("div", undefined, row, grouped ? "hbar-line sub" : "hbar-line");
        // every bar says whose it is: 「生化」「细胞」 in the subject's colour, the value after it (0% is 0%, no data is 暂无)
        if (grouped) {
          const tag = html("span", bar.name, line, "hbar-name");
          tag.style.setProperty("--dot", bar.color || item.color || COLORS.biochemistry);
        }
        const track = html("div", undefined, line, "hbar-track" + (none ? " none" : ""));
        const fill = html("i", undefined, track);
        const max = item.max || 1;
        const share = none ? 0 : Math.max(0, Math.min(100, (bar.value / max) * 100));
        fill.style.width = (!none && share === 0 ? 0.8 : share) + "%";       // a real 0% keeps a hairline: it is a result, not a missing bar
        fill.style.background = bar.color || item.color || COLORS.biochemistry;
        window.Motion?.grow(fill, "x", "hbar:" + spec.title + ":" + item.label + ":" + bar.name);
        if (grouped) html("span", none ? "暂无" : format(bar.value), line, "hbar-num" + (none ? " none" : ""));
        const count = bar.answers ? `，共 ${bar.answers} 题` : "";
        const text = grouped
          ? `${item.label} · ${bar.name}${none ? "：暂无作答" : "得分率 " + format(bar.value) + count}${item.note ? "（" + item.note + "）" : ""}`
          : `${item.label}：${none ? "暂无数据" : format(bar.value)}${item.note ? "（" + item.note + "）" : ""}`;
        bind(track, f.readout, text);
      });
    });
    return root;
  }

  /* columns({title, unit, items:[{label,value,color,note}], refLines:[{value,label,color}], max, format}) */
  function columns(root, spec) {
    const items = spec.items || [];
    if (!items.length || !items.some((i) => i.value > 0)) return empty(root, spec);
    const f = frame(root, spec);
    const W = 340,
      H = 180,
      L = 34,
      R = 10,
      T = 14,
      B = 28;
    const refs = spec.refLines || [];
    const legendItems = spec.legend || [];
    const top = spec.max ?? Math.max(1, ...items.map((i) => i.value), ...refs.map((r) => r.value));
    const y = (v) => T + (1 - v / top) * (H - T - B);
    const format = spec.format || num;
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "chart-svg" }, f.body);
    chart.setAttribute("aria-label", `${spec.title}：共${items.length}根柱。${spec.conclusion || ""}`);
    svg("title", {}, chart).textContent = spec.title;
    [0, 0.5, 1].forEach((t) => {
      const yy = y(top * t);
      svg("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: COLORS.grid }, chart);
      svg("text", { x: L - 6, y: yy + 4, "text-anchor": "end", fill: COLORS.axis, "font-size": 11 }, chart).textContent = format(top * t);
    });
    const slot = (W - L - R) / items.length;
    const bar = Math.min(26, slot * 0.62);
    const step = Math.max(1, Math.ceil(items.length / 7));
    items.forEach((item, i) => {
      const cx = L + slot * i + slot / 2;
      const h = Math.max(item.value > 0 ? 2 : 0, (H - T - B) * (item.value / top));
      if (item.value === 0) svg("text", { x: cx, y: H - B - 4, "text-anchor": "middle", fill: COLORS.axis, "font-size": 10 }, chart).textContent = "0";   // a real zero is shown, not left blank
      const rect = svg("rect", { x: cx - bar / 2, y: H - B - h, width: bar, height: h, rx: 3, fill: item.color || COLORS.biochemistry, class: "chart-bar" }, chart);
      window.Motion?.grow(rect, "y", "col:" + spec.title + ":" + item.label, { delay: Math.min(i, 6) * 30 });   // grows from the bottom, once
      bind(rect, f.readout, `${item.label}：${format(item.value)}${spec.unit ? " " + spec.unit : ""}${item.note ? "（" + item.note + "）" : ""}`);
      if (i % step === 0 || i === items.length - 1)
        svg("text", { x: cx, y: H - 8, "text-anchor": "middle", fill: COLORS.axis, "font-size": 11 }, chart).textContent = item.label;
    });
    refs.forEach((ref) => {
      const yy = y(ref.value);
      svg("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: ref.color || COLORS.warn, "stroke-width": 1.4, "stroke-dasharray": "5 4" }, chart);
      svg("text", { x: W - R, y: yy - 4, "text-anchor": "end", fill: ref.color || COLORS.warn, "font-size": 11, "font-weight": 700 }, chart).textContent = ref.label;
    });
    // colours that carry a meaning are explained (legend: [{label, color}])
    if (legendItems.length) {
      const legend = html("div", undefined, f.body, "chart-legend");
      legendItems.forEach((l) => {
        const item = html("span", undefined, legend);
        html("i", undefined, item).style.background = l.color;
        item.append(l.label);
      });
    }
    return root;
  }

  /* progress({label, value, max, color, text}) -> a labelled bar */
  function progress(root, spec) {
    root.replaceChildren();
    root.classList.add("progress-line");
    const head = html("div", undefined, root, "hbar-head");
    html("span", spec.label, head, "hbar-label");
    html("span", spec.text || `${spec.value} / ${spec.max}`, head, "hbar-value");
    const track = html("div", undefined, root, "hbar-track");
    const fill = html("i", undefined, track);
    const share = spec.max ? Math.max(0, Math.min(1, spec.value / spec.max)) : 0;
    fill.style.width = share * 100 + "%";
    fill.style.background = spec.color || (share < 0.5 && spec.warnBelowHalf ? COLORS.warn : COLORS.biochemistry);
    window.Motion?.grow(fill, "x", "progress:" + spec.label);
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${spec.label}：${spec.text || spec.value + " / " + spec.max}`);
    return root;
  }

  /* heatmap({title, cells:[{day,today,value(0..1),answered,note}]}) : columns are weeks, rows Monday..Sunday.
     A CSS grid: the cell size is whatever the card's width leaves after the weekday labels and the gaps, so nothing can
     overflow at any width. Month names sit above the week in which a month begins. */
  function heatmap(root, spec) {
    const cells = spec.cells || [];
    if (!cells.some((c) => c.value > 0)) return empty(root, spec);
    const f = frame(root, { ...spec, readoutHint: "点按某一天查看当天做题量" });
    f.readout.hidden = true;                                   // the tip above the tapped day replaces the readout line
    const first = new Date(cells[0].day + "T00:00:00");
    const lead = (first.getDay() + 6) % 7;                      // Monday = 0
    const weeks = Math.ceil((lead + cells.length) / 7);         // (the first and last week are usually partial)
    const wrap = html("div", undefined, f.body, "heat-wrap");
    const grid = html("div", undefined, wrap, "heat-grid");
    grid.style.setProperty("--weeks", String(weeks));
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", `${spec.title}：最近${weeks}周，列是周，行是星期。${spec.conclusion || ""}`);
    const month = (d) => d.getMonth() + 1;
    // month labels: the week column holding the 1st of a month; the first column too when no 1st follows closely
    const labels = new Map();
    cells.forEach((cell, index) => {
      const d = new Date(cell.day + "T00:00:00");
      if (d.getDate() === 1) labels.set(Math.floor((index + lead) / 7), month(d) + "月");
    });
    if (!labels.has(0) && ![...labels.keys()].some((c) => c <= 2)) labels.set(0, month(first) + "月");
    labels.forEach((text, col) => {
      const m = html("span", text, grid, "heat-month");
      m.style.gridColumn = String(col + 2);
      m.style.gridRow = "1";
    });
    ["一", "二", "三", "四", "五", "六", "日"].forEach((name, row) => {
      const w = html("span", name, grid, "heat-day" + (row % 2 === 0 ? "" : " minor"));
      w.style.gridColumn = "1";
      w.style.gridRow = String(row + 2);
    });
    let tip = null;
    const hide = () => { if (tip) { tip.remove(); tip = null; } grid.querySelectorAll(".heat-cell.on").forEach((c) => c.classList.remove("on")); };
    const outside = (e) => { if (!root.isConnected) return document.removeEventListener("pointerdown", outside, true); if (!wrap.contains(e.target) || e.target === wrap || e.target === grid) hide(); };
    document.addEventListener("pointerdown", outside, true);
    const dayText = (cell) => { const d = new Date(cell.day + "T00:00:00"); return `${month(d)}月${d.getDate()}日 · ${cell.answered ?? 0} 道`; };
    cells.forEach((cell, index) => {
      const slot = index + lead;
      const level = cell.value > 0 ? Math.max(1, Math.min(4, Math.ceil(cell.value * 4))) : 0; // five levels: 0..4
      const box = html("button", undefined, grid, "heat-cell" + (cell.today ? " today" : ""));
      box.type = "button";
      box.style.gridColumn = String(Math.floor(slot / 7) + 2);
      box.style.gridRow = String((slot % 7) + 2);
      box.style.background = `var(--heat-${level})`;
      box.setAttribute("aria-label", dayText(cell) + (cell.note ? "，" + cell.note.replace(/^做题 \d+ 道，?/, "") : "") + (cell.today ? "（今天）" : ""));
      box.onclick = () => {
        const same = box.classList.contains("on");
        hide();
        if (same) return;
        box.classList.add("on");
        tip = html("span", dayText(cell), wrap, "heat-tip");
        tip.setAttribute("role", "status");
        const left = box.offsetLeft + box.offsetWidth / 2, top = box.offsetTop;
        tip.style.left = left + "px";
        tip.style.top = top + "px";
        const half = tip.offsetWidth / 2;                       // keep the tip inside the card
        tip.style.left = Math.max(half, Math.min(wrap.clientWidth - half, left)) + "px";
      };
    });
    const legend = html("div", undefined, f.body, "heat-legend");
    legend.append("少");
    [0, 1, 2, 3, 4].forEach((l) => {
      const chip = html("i", undefined, legend);
      chip.style.background = `var(--heat-${l})`;
    });
    legend.append("多");
    return root;
  }

  window.Charts = { COLORS, line, hbars, columns, progress, heatmap, pct, num };
})();
