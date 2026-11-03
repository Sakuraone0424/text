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
    if (source || typeof EventSource === "undefined") return;
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
      if (source && source.readyState === 2) close();
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
      if (!document.hidden && view.visible() && Date.now() - (view.last || 0) >= wait - 200) run(view);
    }
  }

  function register(name, { visible, load }) {
    views.set(name, { visible, load, last: 0 });
    if (!timer) timer = setInterval(tick, 1000);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) close();
    else {
      refreshVisible(true); // back in the foreground: refresh once, now
      tick();
    }
  });
  window.addEventListener("hashchange", () => setTimeout(tick, 0));

  /* Pull down from the top of `view` to refresh. */
  function pullToRefresh(view, onRefresh) {
    const scroller = () => view.closest(".workspace, main.page") || document.scrollingElement;
    const hint = document.createElement("div");
    hint.className = "pull-hint";
    hint.setAttribute("aria-live", "polite");
    hint.hidden = true;
    view.prepend(hint);
    let startY = null;
    let pulled = 0;
    view.addEventListener("touchstart", (e) => {
      startY = scroller().scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
      pulled = 0;
    }, { passive: true });
    view.addEventListener("touchmove", (e) => {
      if (startY === null) return;
      pulled = e.touches[0].clientY - startY;
      if (pulled > 8 && scroller().scrollTop <= 0) {
        hint.hidden = false;
        hint.textContent = pulled > 70 ? "松开刷新" : "下拉刷新";
      } else hint.hidden = true;
    }, { passive: true });
    const finish = async () => {
      const go = startY !== null && pulled > 70;
      startY = null;
      if (!go) {
        hint.hidden = true;
        return;
      }
      hint.hidden = false;
      hint.textContent = "更新中…";
      try {
        await onRefresh();
      } finally {
        hint.hidden = true;
      }
    };
    view.addEventListener("touchend", finish);
    view.addEventListener("touchcancel", () => { startY = null; hint.hidden = true; });
    return hint;
  }

  window.Live = { register, pullToRefresh, stamp, POLL_MS, state: () => ({ connected, streaming: !!source, views: views.size }) };
})();
