/* Small SVG chart kit: line, horizontal bars, columns, progress bar, heat map.
   No libraries, no network: works offline and in iPhone Safari. Every chart has a title, a unit,
   an empty state, a one-sentence conclusion, an aria-label, and tap / long-press / keyboard readout. */
"use strict";
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = {
    biochemistry: "#2563EB",
    cell_biology: "#EA580C",
    total: "#111827",
    grid: "#EEF1F5",
    axis: "#556070",
    good: "#15803D",
    warn: "#C2410C",
    soft: "#DBEAFE",
    track: "#E5E9F0",
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
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, String(v)));
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
      svg("text", { x: x(i), y: H - 8, "text-anchor": "middle", fill: COLORS.axis, "font-size": 11 }, chart).textContent = p.label;
    });
    series.forEach((s) => {
      const pts = s.points.map((p, i) => (p.value === null || p.value === undefined ? null : [x(i), y(p.value), p, i]));
      const path = [];
      pts.forEach((pt) => {
        if (pt) path.push((path.length ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1));
      });
      svg("path", { d: path.join(" "), fill: "none", stroke: s.color, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round" }, chart);
      pts.forEach((pt) => {
        if (!pt) return;
        // a larger invisible hit area for thumbs sits UNDER the visible dot and forwards taps to it
        const hit = svg("circle", { cx: pt[0], cy: pt[1], r: 12, fill: "transparent" }, chart);
        const dot = svg("circle", { cx: pt[0], cy: pt[1], r: 4, fill: "#fff", stroke: s.color, "stroke-width": 2, class: "chart-dot" }, chart);
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
      const head = html("div", undefined, row, "hbar-head");
      html("span", item.label, head, "hbar-label");
      html("span", item.valueText || format(item.value), head, "hbar-value");
      (item.sub || [{ name: item.label, value: item.value, color: item.color }]).forEach((bar) => {
        const track = html("div", undefined, row, "hbar-track");
        const fill = html("i", undefined, track);
        const max = item.max || 1;
        fill.style.width = (bar.value === null || bar.value === undefined ? 0 : Math.max(0, Math.min(100, (bar.value / max) * 100))) + "%";
        fill.style.background = bar.color || item.color || COLORS.biochemistry;
        const text = `${item.label}${item.sub ? " · " + bar.name : ""}：${bar.value === null || bar.value === undefined ? "暂无数据" : format(bar.value)}${item.note ? "（" + item.note + "）" : ""}`;
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
      const rect = svg("rect", { x: cx - bar / 2, y: H - B - h, width: bar, height: h, rx: 3, fill: item.color || COLORS.biochemistry, class: "chart-bar" }, chart);
      bind(rect, f.readout, `${item.label}：${format(item.value)}${spec.unit ? " " + spec.unit : ""}${item.note ? "（" + item.note + "）" : ""}`);
      if (i % step === 0 || i === items.length - 1)
        svg("text", { x: cx, y: H - 8, "text-anchor": "middle", fill: COLORS.axis, "font-size": 11 }, chart).textContent = item.label;
    });
    refs.forEach((ref) => {
      const yy = y(ref.value);
      svg("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: ref.color || COLORS.warn, "stroke-width": 1.4, "stroke-dasharray": "5 4" }, chart);
      svg("text", { x: W - R, y: yy - 4, "text-anchor": "end", fill: ref.color || COLORS.warn, "font-size": 11, "font-weight": 700 }, chart).textContent = ref.label;
    });
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
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${spec.label}：${spec.text || spec.value + " / " + spec.max}`);
    return root;
  }

  /* heatmap({title, cells:[{day,value(0..1),note}], weeks}) : columns are weeks, rows Monday..Sunday */
  function heatmap(root, spec) {
    const cells = spec.cells || [];
    if (!cells.some((c) => c.value > 0)) return empty(root, spec);
    const f = frame(root, { ...spec, readoutHint: "点按某一天查看当天做题量" });
    const weeks = Math.ceil(cells.length / 7);
    const first = new Date(cells[0].day + "T00:00:00");
    const lead = (first.getDay() + 6) % 7; // Monday = 0
    const size = 15,
      gap = 4,
      L = 22;
    const W = L + weeks * (size + gap) + 4,
      H = 7 * (size + gap) + 16;
    const chart = svg("svg", { viewBox: `0 0 ${W + 0} ${H}`, role: "img", class: "chart-svg heat" }, f.body);
    chart.setAttribute("aria-label", `${spec.title}：最近${weeks}周。${spec.conclusion || ""}`);
    svg("title", {}, chart).textContent = spec.title;
    ["一", "三", "五", "日"].forEach((name, k) =>
      svg("text", { x: 0, y: [0, 2, 4, 6][k] * (size + gap) + size - 2, fill: COLORS.axis, "font-size": 10 }, chart).textContent = name,
    );
    cells.forEach((cell, index) => {
      const slot = index + lead,
        col = Math.floor(slot / 7),
        row = slot % 7;
      const level = cell.value > 0 ? Math.max(1, Math.min(4, Math.ceil(cell.value * 4))) : 0; // five levels: 0..4
      const rect = svg("rect", {
        x: L + col * (size + gap), y: row * (size + gap), width: size, height: size, rx: 4,
        fill: level ? COLORS.biochemistry : COLORS.track, "fill-opacity": level ? [0, 0.25, 0.45, 0.7, 1][level] : 1, class: "heat-cell",
        ...(cell.today ? { stroke: COLORS.biochemistry, "stroke-width": 1.5, "stroke-dasharray": "3 2" } : {}),
      }, chart);
      bind(rect, f.readout, `${cell.day}：${cell.note}`);
    });
    const legend = html("div", undefined, f.body, "heat-legend");
    legend.append("少");
    [0, 1, 2, 3, 4].forEach((l) => {
      const chip = html("i", undefined, legend);
      chip.style.background = l ? COLORS.biochemistry : COLORS.track;
      chip.style.opacity = l ? [0, 0.25, 0.45, 0.7, 1][l] : 1;
    });
    legend.append("多");
    return root;
  }

  window.Charts = { COLORS, line, hbars, columns, progress, heatmap, pct, num };
})();
