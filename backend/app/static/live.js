"use strict";
/* Live refresh shared by the question-writing progress page and the home progress card.
   - While the page is visible: server-sent events ("有更新") fetch the new numbers at once; if the stream is down the page
     polls every 5 seconds instead (and, with the stream up, still checks every 30 seconds in case an event was missed).
   - While the page is hidden (another tab, locked screen): nothing runs. Coming back refreshes once immediately.
   - Pull down at the top of a page to refresh by hand. `stamp()` gives the "HH:MM:SS" shown as 更新于. */
(() => {
  const POLL_MS = 5000;
  const SAFETY_MS = 30000;
  const views = new Map(); // name -> {visible(), load(), last}
  let source = null;
  let connected = false;
  let nextOpenAt = 0;       // after a failed stream, wait before trying again (the poll covers the gap)
  let timer = null;
  let pending = null;

  const stamp = (date = new Date()) =>
    new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);

  const active = () => !document.hidden && [...views.values()].some((v) => v.visible());

  async function run(view) {
    view.last = Date.now();
    try {
      await view.load();
    } catch {
      /* the view shows its own error */
    }
  }
  function refreshVisible(force = false) {
    for (const view of views.values()) {
      if (!document.hidden && view.visible() && (force || Date.now() - (view.last || 0) >= 500)) run(view);
    }
  }
  function onUpdate() {
    clearTimeout(pending); // several events in a burst become one fetch
    pending = setTimeout(() => refreshVisible(true), 250);
  }

  function open() {
    if (source || typeof EventSource === "undefined" || Date.now() < nextOpenAt) return;
    try {
      source = new EventSource("/v2/model/progress/events");
    } catch {
      source = null;
      return;
    }
    source.addEventListener("hello", () => {
      connected = true;
      // whatever happened while the stream was down (the stream reconnects every few seconds by design: a view that was
      // refreshed a moment ago does not need it again)
      for (const view of views.values()) {
        if (!document.hidden && view.visible() && Date.now() - (view.last || 0) >= 4000) run(view);
      }
    });
    source.addEventListener("update", onUpdate);
    source.onerror = () => {
      connected = false; // the browser retries by itself; until then the 5 s poll covers it
      if (source && source.readyState === 2) {
        close(); // some browsers give up for good on the first failure: try again in 15 s, not on every tick
        nextOpenAt = Date.now() + 15000;
      }
    };
  }
  function close() {
    if (source) source.close();
    source = null;
    connected = false;
  }

  function tick() {
    if (!active()) {
      close(); // hidden: no stream, no polling
      return;
    }
    open();
    const wait = connected ? SAFETY_MS : POLL_MS;
    for (const view of views.values()) {
      if (!document.hidden && view.visible() && Date.now() - (view.last || 0) >= wait - 250) run(view);
    }
  }

  function register(name, { visible, load }) {
    views.set(name, { visible, load, last: 0 });
    if (!timer) timer = setInterval(tick, 500);   // a finer grid than the 5 s period, so a poll is never a whole second late
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) close();
    else {
      refreshVisible(true); // back in the foreground: refresh once, now
      tick();
    }
  });
  window.addEventListener("hashchange", () => setTimeout(tick, 0));

  /* Pull down from the top of `view` to refresh, the iOS way: the content follows the finger with damping (the further, the
     harder), a small round indicator fills as you pull and says 松开刷新 at the threshold; on release the indicator spins and the
     content settles at the refresh position; when done the indicator fades and the content returns. The last data stays on
     screen the whole time. One implementation for every page that refreshes. */
  let ptrNode = null;
  function indicator() {
    if (ptrNode) return ptrNode;
    const wrap = document.createElement("div");
    wrap.className = "ptr";
    wrap.setAttribute("aria-hidden", "true");
    wrap.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"></circle></svg>';
    const label = document.createElement("div");
    label.className = "ptr-label";
    label.setAttribute("aria-live", "polite");
    document.body.append(wrap, label);
    ptrNode = { wrap, label, circle: wrap.querySelector("circle") };
    return ptrNode;
  }
  const PTR_READY = 54;      // damped distance (px) at which releasing refreshes (about the 70 px of finger travel it always took)
  const PTR_HOLD = 52;       // where the content rests while refreshing
  const PTR_MAX = 120;
  const damp = (raw) => PTR_MAX * (1 - Math.exp(-raw / PTR_MAX));      // rubber band: ever harder to pull
  function pullToRefresh(view, onRefresh) {
    const scroller = () => view.closest(".workspace, main.page") || document.scrollingElement;
    const reduce = () => window.Motion?.reduced?.() || false;
    let startY = null;
    let moved = 0;
    let busy = false;
    const show = (d) => {
      const ptr = indicator();
      view.style.transform = d ? `translateY(${d}px)` : "";
      ptr.wrap.style.opacity = String(Math.min(1, d / 36));
      ptr.wrap.style.transform = `translateY(${Math.max(0, d - 40) * 0.4}px)`;
      ptr.circle.style.strokeDashoffset = String(1 - Math.min(1, d / PTR_READY));
      ptr.label.style.opacity = d >= 28 ? "1" : "0";
      ptr.label.textContent = d >= PTR_READY ? "松开刷新" : "下拉刷新";
    };
    const settle = (to, ms) => {
      view.style.transition = reduce() ? "none" : `transform ${ms}ms ${window.Motion?.EASE_OUT || "ease-out"}`;
      view.style.transform = to ? `translateY(${to}px)` : "";
      setTimeout(() => { view.style.transition = ""; }, ms + 20);
    };
    view.addEventListener("touchstart", (e) => {
      startY = !busy && scroller().scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
      moved = 0;
      view.style.transition = "";
    }, { passive: true });
    view.addEventListener("touchmove", (e) => {
      if (startY === null) return;
      const raw = e.touches[0].clientY - startY;
      if (raw > 4 && scroller().scrollTop <= 0) {
        if (e.cancelable) e.preventDefault();          // no native rubber band on top of ours
        moved = damp(raw);
        show(moved);
      } else if (raw <= 0 && moved) {
        moved = 0;
        show(0);
      }
    }, { passive: false });
    const finish = async () => {
      const go = startY !== null && moved >= PTR_READY;
      const was = moved;
      startY = null;
      moved = 0;
      const ptr = indicator();
      if (!go) {
        if (was) {
          settle(0, 200);
          ptr.wrap.style.opacity = "0";
          ptr.label.style.opacity = "0";
        }
        return;
      }
      busy = true;
      settle(PTR_HOLD, 180);
      ptr.circle.style.strokeDashoffset = "0";
      ptr.wrap.classList.add("spin");
      ptr.label.textContent = "正在刷新";
      window.Motion?.topline?.start();
      try {
        await Promise.all([onRefresh(), new Promise((r) => setTimeout(r, reduce() ? 0 : 450))]);   // (no blink on a fast answer)
      } finally {
        window.Motion?.topline?.done();
        ptr.wrap.classList.remove("spin");
        ptr.wrap.style.opacity = "0";
        ptr.label.style.opacity = "0";
        settle(0, 220);
        busy = false;
      }
    };
    view.addEventListener("touchend", finish);
    view.addEventListener("touchcancel", () => {
      if (moved) {
        settle(0, 200);
        indicator().wrap.style.opacity = "0";
        indicator().label.style.opacity = "0";
      }
      startY = null;
      moved = 0;
    });
  }

  // a view that has just loaded itself by hand counts as refreshed (no second read a tick later)
  const touch = (name) => { const v = views.get(name); if (v) v.last = Date.now(); };

  window.Live = { register, touch, pullToRefresh, stamp, POLL_MS, state: () => ({ connected, streaming: !!source, views: views.size }) };
})();
