"use strict";
/* Data layer shared by every view: one fetch wrapper (same cookie, 401 means "sign in", never a different page)
   and stale-while-revalidate reads: what was seen before is shown at once, a fresh copy replaces it a moment later. */
(() => {
  const memory = new Map(); // path -> {data, at}
  let user = "";
  const key = (path) => `swr:${user}:${path}`;
  const MAX_AGE = 24 * 3600 * 1000; // an older stored copy is not shown

  class AuthRequired extends Error {
    constructor() {
      super("auth_required");
      this.name = "AuthRequired";
    }
  }

  function peek(path) {
    if (memory.has(path)) return memory.get(path);
    try {
      const raw = localStorage.getItem(key(path));
      if (!raw) return null;
      const stored = JSON.parse(raw);
      if (Date.now() - stored.at > MAX_AGE) return null;
      memory.set(path, stored);
      return stored;
    } catch {
      return null;
    }
  }

  function store(path, data) {
    const entry = { data, at: Date.now() };
    memory.set(path, entry);
    try {
      localStorage.setItem(key(path), JSON.stringify(entry));
    } catch {
      /* storage full or blocked: the memory copy still works */
    }
  }

  function invalidate(prefix) {
    for (const path of [...memory.keys()]) if (path.startsWith(prefix)) memory.delete(path);
    try {
      for (const k of Object.keys(localStorage)) if (k.startsWith(`swr:${user}:${prefix}`)) localStorage.removeItem(k);
    } catch {
      /* ignore */
    }
  }

  async function fetchJSON(path, options = {}) {
    const started = performance.now();
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      });
    } catch (error) {
      throw new Error("网络不通，请检查连接后重试");
    }
    Data.timings.push({ path: path.split("?")[0], ms: Math.round(performance.now() - started), status: response.status });
    if (Data.timings.length > 200) Data.timings.shift();
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("auth-required"));
      throw new AuthRequired();
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      /* not JSON: the status below says what happened */
    }
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : "请求失败（" + response.status + "）");
    }
    return payload;
  }

  /* swr(path, {render(data, {stale}), error(err, {hadCache}), fetcher}) -> Promise of the fresh data (or null on failure).
     `render` is called with the stored copy first (if any), then with the fresh one when it differs. */
  async function swr(path, { render, error, fetcher } = {}) {
    const cached = peek(path);
    if (cached && render) render(cached.data, { stale: true, at: cached.at });
    try {
      const fresh = await (fetcher ? fetcher() : fetchJSON(path));
      const changed = !cached || JSON.stringify(cached.data) !== JSON.stringify(fresh);
      store(path, fresh);
      if (render && (changed || !cached)) render(fresh, { stale: false, at: Date.now() });
      return fresh;
    } catch (err) {
      if (err instanceof AuthRequired) return null;
      if (error) error(err, { hadCache: !!cached });
      return null;
    }
  }

  const Data = { fetchJSON, swr, peek, store, invalidate, AuthRequired, timings: [], setUser: (name) => { user = name || ""; } };
  window.Data = Data;
})();
