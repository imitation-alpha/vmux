// vmux service worker — explicit, unauthenticated application-shell cache.
// Live pane state and authenticated traffic stay on the network and in memory.

const CACHE_PREFIX = "vmux-";
// Bump this whenever a shell asset changes so a waiting worker never mutates
// the cache still owned by the active worker.
const CACHE_NAME = "vmux-shell-v29";
const SHELL_KEY = "/index.html";
const NETWORK_TIMEOUT_MS = 3000;

// Keep this list in lockstep with the no-build files referenced by index.html
// and its ES-module graph. sw.js and license/source files are intentionally not
// cached: they are not runtime shell dependencies.
const SHELL_ASSETS = Object.freeze([
  SHELL_KEY,
  "/styles.css",
  "/manifest.webmanifest",
  "/icon.svg",
  "/icon-180.png",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/icons/lucide.svg",
  "/vendor/react.production.min.js",
  "/vendor/react-dom.production.min.js",
  "/vendor/htm.umd.js",
  "/js/core.js",
  "/js/state.js",
  "/js/image-upload.js",
  "/js/agent-state.js",
  "/js/review-drafts.js",
  "/js/agent-ui.js",
  "/js/ui.js",
  "/js/usage.js",
  "/js/settings.js",
  "/js/app.js",
]);
const SHELL_PATHS = new Set(SHELL_ASSETS);

function isLiveEndpoint(pathname) {
  return pathname === "/api" || pathname.startsWith("/api/") ||
    pathname === "/ws" || pathname.startsWith("/ws/");
}

function hasAuthorization(request) {
  return request.headers.has("Authorization");
}

function isCacheableResponse(response) {
  return Boolean(response && response.ok && response.type === "basic");
}

function isTransientServerFailure(response) {
  return Boolean(response && (response.status === 408 || response.status >= 500));
}

async function fetchWithTimeout(request) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), NETWORK_TIMEOUT_MS);
  try {
    return await fetch(request, { cache: "no-store", signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function installShell() {
  // The previous worker cached broad same-origin requests. Remove any legacy
  // credential-bearing keys immediately, even while this update is waiting.
  await purgeSensitiveEntries();
  const cache = await caches.open(CACHE_NAME);
  const requests = SHELL_ASSETS.map((path) => new Request(path, {
    cache: "reload",
    credentials: "same-origin",
  }));
  await cache.addAll(requests);
}

async function purgeSensitiveEntries() {
  const names = (await caches.keys()).filter((name) => name.startsWith(CACHE_PREFIX));
  await Promise.all(names.map(async (name) => {
    const cache = await caches.open(name);
    const requests = await cache.keys();
    await Promise.all(requests.map((request) => {
      const url = new URL(request.url);
      const sensitive = Boolean(url.search) || hasAuthorization(request) || isLiveEndpoint(url.pathname);
      return sensitive ? cache.delete(request) : Promise.resolve(false);
    }));
  }));
}

async function activateShell() {
  const names = await caches.keys();
  await Promise.all(names
    .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
    .map((name) => caches.delete(name)));
  await purgeSensitiveEntries();
  await self.clients.claim();
}

async function navigationResponse(request, mayWriteCache) {
  try {
    const response = await fetchWithTimeout(request);
    if (isTransientServerFailure(response)) throw new Error("transient navigation failure");
    const contentType = response.headers.get("Content-Type") || "";
    if (mayWriteCache && isCacheableResponse(response) && contentType.includes("text/html")) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(SHELL_KEY, response.clone());
    }
    return response;
  } catch (_) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(SHELL_KEY);
    return cached || Response.error();
  }
}

async function shellAssetResponse(request, pathname) {
  try {
    const response = await fetchWithTimeout(request);
    if (isTransientServerFailure(response)) throw new Error("transient shell asset failure");
    if (isCacheableResponse(response)) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(pathname, response.clone());
    }
    return response;
  } catch (_) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(pathname);
    return cached || Response.error();
  }
}

self.addEventListener("install", (event) => {
  // Do not call skipWaiting here. An already-controlled page presents the
  // update first, then asks this worker to activate through ACTIVATE_UPDATE.
  event.waitUntil(installShell());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(activateShell());
});

self.addEventListener("message", (event) => {
  const type = event.data && event.data.type;
  if (type === "ACTIVATE_UPDATE" || type === "SKIP_WAITING") {
    event.waitUntil(self.skipWaiting());
  } else if (type === "PURGE_CREDENTIALS") {
    event.waitUntil(purgeSensitiveEntries());
  }
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || isLiveEndpoint(url.pathname)) return;

  const sensitive = Boolean(url.search) || hasAuthorization(request);
  if (request.mode === "navigate") {
    // A query-bearing setup URL may still use the canonical shell offline, but
    // its URL (and any Authorization header) is never used as a cache key.
    event.respondWith(navigationResponse(request, !sensitive));
    return;
  }

  if (sensitive || !SHELL_PATHS.has(url.pathname)) return;
  event.respondWith(shellAssetResponse(request, url.pathname));
});
