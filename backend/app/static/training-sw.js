"use strict";
const CACHE = "xiaohan-training-shell-v19-one-shell";
const ASSETS = [
  "/",
  "/training",
  "/static/training.js",
  "/static/app.css",
  "/static/tokens.css",
  "/static/shell.js",
  "/static/training.css",
  "/static/views.css",
  "/static/motion.css",
  "/static/motion.js",
  "/static/pages.css",
  "/static/pages.js",
  "/static/logic.js",
  "/static/manual.js",
  "/static/drill.js",
  "/static/data.js",
  "/static/live.js",
  "/static/progress.js",
  "/static/model.js",
  "/static/compare.js",
  "/static/charts.js",
  "/static/insights.js",
  "/manifest.webmanifest",
  "/static/training-ui.js",
];
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(ASSETS))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (k) => k.startsWith("xiaohan-training-shell-") && k !== CACHE,
            )
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (event) => {
  const u = new URL(event.request.url);
  if (
    u.origin !== self.location.origin ||
    !ASSETS.includes(u.pathname) ||
    event.request.method !== "GET"
  )
    return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request)),
  );
});
