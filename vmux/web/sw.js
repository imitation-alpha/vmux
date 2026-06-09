// vmux service worker — installable PWA shell cache.
// Strategy: network-first so updates always win; fall back to cache when offline.
// API and websocket traffic is never cached.

const CACHE = "vmux-v12";
const SHELL = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/vendor/react.production.min.js",
  "/vendor/react-dom.production.min.js",
  "/vendor/htm.umd.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;            // never cache POSTs
  if (url.pathname.startsWith("/api") || url.pathname === "/ws") return;  // live data

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res && res.ok && (url.origin === location.origin || SHELL.includes(e.request.url))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html")))
  );
});
