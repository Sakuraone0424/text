"use strict";
/* Shared motion helpers (see motion.css). Every page uses these; nothing animates on its own.
   Rules: 150-300 ms, ease-out in / ease-in out, transform + opacity only, instant under prefers-reduced-motion,
   and never anything on the timer or when moving between questions beyond a 120 ms fade. */
(() => {
  const EASE_OUT = "cubic-bezier(0.2, 0.7, 0.2, 1)";
  const EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";
  const mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const reduced = () => mq.matches;
  const $ = (id) => document.getElementById(id);

  /* Web Animations helper: resolves when done; resolves at once when motion is reduced or unsupported. */
  function play(node, frames, options) {
    if (!node || reduced() || typeof node.animate !== "function") return { finished: Promise.resolve(), cancel() {} };
    const animation = node.animate(frames, { fill: "backwards", easing: EASE_OUT, ...options });
    animation.finished.catch(() => {});
    return animation;
  }

  /* ---------- 2. pages ---------- */
  // kind: "push" (a sub-page slides in from the right), "pop" (back: a short fade, the page above has already slid out),
  // "tab" (the four bottom tabs cross-fade)
  function enter(node, kind) {
    if (!node) return;
    if (kind === "push") play(node, [{ transform: "translateX(32px)", opacity: 0 }, { transform: "translateX(0)", opacity: 1 }], { duration: 240 });
    else if (kind === "pop") play(node, [{ opacity: 0.4 }, { opacity: 1 }], { duration: 160 });
    else play(node, [{ opacity: 0 }, { opacity: 1 }], { duration: 180 });
  }
  /* A page leaving to the right (back). Resolves when it is gone; the caller navigates, then calls `done()` of the result. */
  function leave(node, distance) {
    if (!node || reduced() || typeof node.animate !== "function") return Promise.resolve({ cancel() {} });
    const animation = node.animate(
      [{ transform: `translateX(${distance || 0}px)`, opacity: 1 }, { transform: "translateX(100%)", opacity: 0.2 }],
      { duration: 200, easing: EASE_IN, fill: "forwards" },
    );
    return animation.finished.then(() => animation, () => animation);
  }

  /* ---------- 6. cards come in one after the other, first six only, first opening of a page only ---------- */
  const STAGGER_MS = 40;
  const MAX_STAGGERED = 6;
  function cards(page) {
    if (!page || page.dataset.motionDone === "1" || reduced()) {
      if (page) page.dataset.motionDone = "1";
      return;
    }
    page.dataset.motionDone = "1";
    let n = 0;
    const lift = (card) => {
      if (n >= MAX_STAGGERED) return;
      const delay = n * STAGGER_MS;
      n += 1;
      play(card, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 260, delay, fill: "backwards" });
    };
    new Set(page.querySelectorAll(":scope > .card, :scope > section.card, :scope > .stat-row > .card, :scope .hero-card")).forEach(lift);
    // cards a page builds after its data arrives (profile, progress) take the next places while it is new
    const watcher = new MutationObserver((records) => {
      records.forEach((r) => r.addedNodes.forEach((node) => {
        if (node.nodeType === 1 && node.matches?.(".card, section.card, .chart-block")) lift(node);
      }));
    });
    watcher.observe(page, { childList: true, subtree: true });
    setTimeout(() => watcher.disconnect(), 1500);
  }

  /* ---------- 4. numbers roll: from 0 the first time, from the old value to the new one afterwards ---------- */
  const rolling = new WeakMap();
  const lastShown = new Map();      // key -> value, for numbers whose element is rebuilt on every refresh
  function num(node, to, { decimals = 0, suffix = "", prefix = "", duration = 400, key = null } = {}) {
    if (!node) return;
    const finalText = prefix + Number(to).toFixed(decimals) + suffix;
    const seen = key ? lastShown.get(key) : node.dataset.rollValue;
    node.dataset.rollValue = String(to);
    if (key) lastShown.set(key, to);
    if (rolling.has(node)) cancelAnimationFrame(rolling.get(node));
    const from = seen === undefined ? 0 : Number(seen);
    if (reduced() || from === to || !Number.isFinite(from) || !Number.isFinite(to)) {
      node.textContent = finalText;
      delete node.dataset.rolling;
      return;
    }
    const began = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - began) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      node.textContent = prefix + (from + (to - from) * eased).toFixed(decimals) + suffix;
      if (t < 1) rolling.set(node, requestAnimationFrame(step));
      else {
        node.textContent = finalText;
        delete node.dataset.rolling;
        rolling.delete(node);
      }
    };
    node.textContent = prefix + from.toFixed(decimals) + suffix;
    node.dataset.rolling = "1";
    rolling.set(node, requestAnimationFrame(step));
  }

  /* ---------- 5. charts play once, the first time they are in view ---------- */
  const played = new Set();
  function inView(node, callback) {
    if (!("IntersectionObserver" in window)) return callback();
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        observer.disconnect();
        callback();
      }
    }, { threshold: 0.2 });
    observer.observe(node);
  }
  /* axis "x": grows left to right (bars); "y": grows from the bottom (columns); key: one chart plays once per session */
  function grow(node, axis, key, { duration = 420, delay = 0 } = {}) {
    if (!node) return;
    if (key) {
      if (played.has(key)) return;
    }
    if (reduced()) {
      if (key) played.add(key);
      return;
    }
    node.classList.add(axis === "y" ? "motion-grow-y" : "motion-grow-x");
    const from = axis === "y" ? "scaleY(0)" : "scaleX(0)";
    const to = axis === "y" ? "scaleY(1)" : "scaleX(1)";
    node.style.transform = from;              // hidden until it is in view
    inView(node, () => {
      if (key) played.add(key);
      const a = play(node, [{ transform: from }, { transform: to }], { duration, delay, fill: "both" });
      (a.finished || Promise.resolve()).then(() => { node.style.transform = ""; a.cancel?.(); });
    });
  }
  function draw(path, key, duration = 600) {
    if (!path || reduced() || (key && played.has(key)) || !path.getTotalLength) {
      if (key) played.add(key);
      return;
    }
    path.setAttribute("pathLength", "1");
    path.style.strokeDasharray = "1";
    path.style.strokeDashoffset = "1";
    inView(path.ownerSVGElement || path, () => {
      if (key) played.add(key);
      const a = play(path, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration, fill: "both" });
      a.finished.then(() => { path.style.strokeDasharray = ""; path.style.strokeDashoffset = ""; a.cancel?.(); });
    });
  }

  /* ---------- 7. sheets slide down when they close (the stylesheet slides them up when they open) ----------
     The dialog itself closes at once, exactly as before (focus, inert and `close` events all behave the same); a picture of
     it slides down and fades with the backdrop, then disappears. Nothing waits for the animation. */
  if (window.HTMLDialogElement) {
    const nativeClose = HTMLDialogElement.prototype.close;
    HTMLDialogElement.prototype.close = function (value) {
      if (!this.open || reduced() || !this.classList.contains("sheet") || typeof this.animate !== "function") return nativeClose.call(this, value);
      const rect = this.getBoundingClientRect();
      const ghost = this.cloneNode(true);
      ghost.removeAttribute("id");
      ghost.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
      ghost.setAttribute("open", "");
      ghost.setAttribute("inert", "");
      ghost.setAttribute("aria-hidden", "true");
      Object.assign(ghost.style, { position: "fixed", margin: "0", left: rect.left + "px", top: rect.top + "px", width: rect.width + "px", height: rect.height + "px", zIndex: "100", pointerEvents: "none", animation: "none" });
      const shade = document.createElement("div");
      shade.setAttribute("aria-hidden", "true");
      Object.assign(shade.style, { position: "fixed", inset: "0", zIndex: "99", pointerEvents: "none", background: "var(--scrim, rgba(0,0,0,.4))" });
      document.body.append(shade, ghost);
      nativeClose.call(this, value);
      const out = ghost.animate([{ transform: "translateY(0)" }, { transform: "translateY(100%)" }], { duration: 160, easing: EASE_IN, fill: "forwards" });
      const fade = shade.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: EASE_IN, fill: "forwards" });
      Promise.allSettled([out.finished, fade.finished]).then(() => { ghost.remove(); shade.remove(); });
    };
  }

  /* ---------- 9. submit succeeded: a tick draws itself (about 500 ms), then it fades ---------- */
  function done() {
    if (reduced()) return Promise.resolve();
    const box = document.createElement("div");
    box.className = "motion-done";
    box.setAttribute("aria-hidden", "true");
    box.innerHTML = '<svg viewBox="0 0 96 96"><circle cx="48" cy="48" r="38" pathLength="1"></circle><path d="M30 50 L43 63 L68 36" pathLength="1"></path></svg>';
    document.body.append(box);
    const circle = box.querySelector("circle");
    const tick = box.querySelector("path");
    [circle, tick].forEach((p) => { p.style.strokeDasharray = "1"; p.style.strokeDashoffset = "1"; });
    play(box, [{ opacity: 0 }, { opacity: 1 }], { duration: 120, fill: "both" });
    const a = play(circle, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 240, fill: "both" });
    const b = play(tick, [{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 200, delay: 150, fill: "both" });   // done at 350 ms
    return Promise.all([a.finished, b.finished]).then(() => play(box, [{ opacity: 1 }, { opacity: 0 }], { duration: 100, fill: "both", easing: EASE_IN }).finished)
      .finally(() => box.remove());
  }

  /* ---------- refresh line: a hair-thin bar at the top while a background refresh runs ---------- */
  let lineUsers = 0;
  const topline = {
    start() {
      lineUsers += 1;
      let line = $("motion-topline");
      if (!line) {
        line = document.createElement("div");
        line.id = "motion-topline";
        line.setAttribute("aria-hidden", "true");
        line.append(document.createElement("i"));
        document.body.append(line);
      }
      line.classList.add("on");
    },
    done() {
      lineUsers = Math.max(0, lineUsers - 1);
      if (!lineUsers) $("motion-topline")?.classList.remove("on");
    },
  };

  /* ---------- 2. left-edge swipe back, the page follows the finger (home-screen mode has no native swipe) ---------- */
  function edgeSwipe({ canGoBack, pageNode, back }) {
    let startX = null, startY = 0, dx = 0, node = null, startT = 0, locked = false;
    document.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1 || e.touches[0].clientX > 22 || !canGoBack() || document.querySelector("dialog[open]")) return;
      startX = e.touches[0].clientX; startY = e.touches[0].clientY; dx = 0; startT = Date.now(); locked = false;
      node = pageNode();
    }, { passive: true });
    document.addEventListener("touchmove", (e) => {
      if (startX === null || !node) return;
      const x = e.touches[0].clientX - startX, y = e.touches[0].clientY - startY;
      if (!locked) {
        if (Math.abs(y) > Math.abs(x) && Math.abs(y) > 8) { startX = null; return; }   // a vertical scroll, not a swipe
        if (x > 8) locked = true; else return;
      }
      dx = Math.max(0, x);
      node.style.transform = `translateX(${dx}px)`;
      node.style.opacity = String(1 - Math.min(0.5, dx / (window.innerWidth * 2)));
    }, { passive: true });
    const end = async () => {
      if (startX === null || !node) { startX = null; return; }
      const target = node;
      const fast = dx / Math.max(1, Date.now() - startT) > 0.5;
      startX = null;
      if (!locked) return;
      if (dx > window.innerWidth / 3 || (fast && dx > 40)) {
        target.style.transform = ""; target.style.opacity = "";
        const a = await leave(target, dx);
        back();
        setTimeout(() => a.cancel?.(), 60);
      } else {
        target.style.transition = `transform ${reduced() ? 0 : 180}ms ${EASE_OUT}, opacity ${reduced() ? 0 : 180}ms ${EASE_OUT}`;
        target.style.transform = ""; target.style.opacity = "";
        setTimeout(() => { target.style.transition = ""; }, 200);
      }
    };
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", () => { if (node) { node.style.transform = ""; node.style.opacity = ""; } startX = null; });
  }

  window.Motion = { reduced, play, enter, leave, cards, num, grow, draw, inView, done, topline, edgeSwipe, EASE_OUT, EASE_IN, played };
})();
