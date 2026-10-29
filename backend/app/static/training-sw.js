"use strict";
const CACHE = "xiaohan-training-shell-v5-analytics";
const ASSETS = [
  "/",
  "/training",
  "/static/training.js",
  "/static/app.css",
  "/static/training.css",
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
