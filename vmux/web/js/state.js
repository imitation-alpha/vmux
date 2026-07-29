/**
 * vmux browser state, transport, preferences, and action dispatch.
 *
 * This module deliberately mirrors the existing Python wire contract. It does
 * not invent new endpoints, persist pane output, or put credentials in public
 * state. All terminal data remains in memory.
 */

/** @typedef {"connecting"|"live"|"updating_rest"|"offline"|"unauthorized"|"incompatible"} ConnectionMode */

/**
 * @typedef {Object} CompatibilityState
 * @property {"verified"|"unverified"|"incompatible"} status
 * @property {boolean} verified
 * @property {boolean} blocksActions
 * @property {string|null} serverVersion
 * @property {number|null} protocolVersion
 * @property {number} expectedProtocol
 * @property {string} minimumServerVersion
 * @property {string} reason
 * @property {string} message
 */

/**
 * @typedef {Object} ConnectionState
 * @property {ConnectionMode} mode
 * @property {number|null} lastSuccessAt Epoch milliseconds.
 * @property {Object|null} issue Sanitized issue safe for UI and diagnostics.
 * @property {CompatibilityState} compatibility
 */

/**
 * @typedef {Object} PaneActionState
 * @property {string} paneId
 * @property {string} actionKey
 * @property {string} type
 * @property {"pending"|"success"|"error"} status
 * @property {string} message
 * @property {number} order Monotonic dispatcher order for the action attempt.
 * @property {number} startedAt
 * @property {number|null} finishedAt
 */

/**
 * @typedef {Object} PrefsV2
 * @property {2} version
 * @property {"auto"|"light"|"dark"} theme
 * @property {boolean} glass
 * @property {boolean} ambient
 * @property {boolean} sound
 * @property {boolean} notify
 * @property {boolean} alertErrors
 * @property {"queue"|"active"|"all"|"stats"} defaultFilter
 * @property {"list"|"tree"} view
 * @property {"status"|"name"|"active"|"sent"|"target"} sort
 * @property {Array<Array<string>>} actions
 * @property {Array<string>} snippets
 * @property {boolean} terminalWrap
 */

export const PREFS_KEY = "vmux_prefs";
export const PREFS_VERSION = 2;
export const TOKEN_KEY = "vmux_token";
export const PROTOCOL_VERSION = 1;
export const MINIMUM_SERVER_VERSION = "0.1.0";
export const WEB_CLIENT_VERSION = "0.1.0";

export const CONNECTION_MODES = Object.freeze({
  CONNECTING: "connecting",
  LIVE: "live",
  REST: "updating_rest",
  OFFLINE: "offline",
  UNAUTHORIZED: "unauthorized",
  INCOMPATIBLE: "incompatible",
});

export const CONNECTION_LABELS = Object.freeze({
  connecting: "Connecting",
  live: "Live",
  updating_rest: "Updating via REST",
  offline: "Offline",
  unauthorized: "Unauthorized",
  incompatible: "Incompatible",
});

export const KNOWN_STATUSES = Object.freeze([
  "needs_input", "error", "working", "idle", "offline",
]);
export const KNOWN_KINDS = Object.freeze([
  "claude-code", "codex", "grok", "opencode", "antigravity", "generic", "shell",
]);

export const STATUS_LABELS = Object.freeze({
  needs_input: "Needs input",
  error: "Error",
  working: "Working",
  idle: "Idle",
  offline: "Offline",
  unknown: "Unknown",
});

export const KIND_LABELS = Object.freeze({
  "claude-code": "Claude Code",
  codex: "Codex",
  grok: "Grok",
  opencode: "OpenCode",
  antigravity: "Antigravity",
  generic: "Agent",
  shell: "Shell",
});
export const KIND_LABEL = KIND_LABELS;

export const ORDER = Object.freeze({
  needs_input: 0,
  error: 1,
  working: 2,
  idle: 3,
  offline: 4,
  unknown: 5,
});

export const STATUS_META = Object.freeze({
  needs_input: { label: "Needs input", shortLabel: "Needs you", icon: "circle-alert", tone: "danger" },
  error: { label: "Error", shortLabel: "Error", icon: "triangle-alert", tone: "warning" },
  working: { label: "Working", shortLabel: "Working", icon: "loader-circle", tone: "active" },
  idle: { label: "Idle", shortLabel: "Idle", icon: "circle-check", tone: "success" },
  offline: { label: "Offline", shortLabel: "Offline", icon: "wifi-off", tone: "neutral" },
  unknown: { label: "Unknown", shortLabel: "Unknown", icon: "circle-help", tone: "neutral" },
});

export const FALLBACK_KEYS = Object.freeze([
  "BSpace", "BTab", "C-a", "C-c", "C-d", "C-e", "C-k", "C-l", "C-n", "C-o",
  "C-p", "C-r", "C-u", "C-w", "C-z", "Down", "End", "Enter", "Escape", "Home",
  "Left", "PageDown", "PageUp", "Right", "Space", "Tab", "Up",
]);

export const DEFAULT_ACTIONS = Object.freeze([
  ["CTRL+C", "C-c"], ["ESC", "Escape"], ["TAB", "Tab"],
  ["⇧TAB", "BTab"], ["↵", "Enter"], ["↑", "Up"], ["↓", "Down"],
  ["^R", "C-r"], ["^O", "C-o"], ["^E", "C-e"],
]);
export const DEFAULT_SNIPPETS = Object.freeze(["continue", "yes", "no"]);

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function textValue(value, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function safeStorage(name) {
  try {
    return typeof globalThis !== "undefined" ? globalThis[name] || null : null;
  } catch (error) {
    console.warn(`[vmux] ${name} is unavailable`, error instanceof Error ? error.name : "error");
    return null;
  }
}

function currentWidth() {
  return typeof globalThis.innerWidth === "number" ? globalThis.innerWidth : 1200;
}

function cloneActions(value) {
  if (!Array.isArray(value)) return DEFAULT_ACTIONS.map((pair) => pair.slice());
  const out = [];
  for (const entry of value) {
    if (!Array.isArray(entry) || entry.length < 2) continue;
    const label = textValue(entry[0]).slice(0, 40);
    const key = textValue(entry[1]).slice(0, 40);
    if (label && key) out.push([label, key]);
  }
  if (out.length || value.length === 0) return out;
  return DEFAULT_ACTIONS.map((pair) => pair.slice());
}

function cloneSnippets(value) {
  if (!Array.isArray(value)) return DEFAULT_SNIPPETS.slice();
  return value.filter((item) => typeof item === "string").map((item) => item.slice(0, 2000));
}

function defaultView(width = currentWidth()) {
  return width >= 1200 ? "tree" : "list";
}

/** @returns {PrefsV2} */
export function defaultPrefs(width = currentWidth()) {
  return {
    version: PREFS_VERSION,
    theme: "auto",
    glass: true,
    ambient: false,
    sound: false,
    notify: false,
    alertErrors: false,
    defaultFilter: "queue",
    view: defaultView(width),
    sort: "status",
    actions: DEFAULT_ACTIONS.map((pair) => pair.slice()),
    snippets: DEFAULT_SNIPPETS.slice(),
    terminalWrap: false,
  };
}

/**
 * Upgrade the unversioned `vmux_prefs` object in place conceptually while
 * preserving every recognizable user choice.
 * @param {unknown} raw
 * @param {number} width
 * @returns {PrefsV2}
 */
export function migratePrefs(raw, width = currentWidth()) {
  const source = isObject(raw) ? raw : {};
  const defaults = defaultPrefs(width);
  const theme = source.theme === "system" ? "auto" : source.theme;
  const filterMap = { needs: "queue", working: "active" };
  const mappedFilter = filterMap[source.defaultFilter] || source.defaultFilter;
  const validFilters = new Set(["queue", "active", "all", "stats"]);
  const validSorts = new Set(["status", "name", "active", "sent", "target"]);

  return {
    version: PREFS_VERSION,
    theme: ["auto", "light", "dark"].includes(theme) ? theme : defaults.theme,
    glass: typeof source.glass === "boolean" ? source.glass : defaults.glass,
    ambient: typeof source.ambient === "boolean" ? source.ambient : defaults.ambient,
    sound: typeof source.sound === "boolean" ? source.sound : defaults.sound,
    notify: typeof source.notify === "boolean" ? source.notify : defaults.notify,
    alertErrors: typeof source.alertErrors === "boolean" ? source.alertErrors : defaults.alertErrors,
    defaultFilter: validFilters.has(mappedFilter) ? mappedFilter : defaults.defaultFilter,
    view: source.view === "list" || source.view === "tree" ? source.view : defaults.view,
    sort: validSorts.has(source.sort) ? source.sort : defaults.sort,
    actions: cloneActions(source.actions),
    snippets: cloneSnippets(source.snippets),
    terminalWrap: typeof source.terminalWrap === "boolean" ? source.terminalWrap : false,
  };
}

/** Load, migrate, and persist browser-local preferences. */
export function loadPrefs(width = currentWidth()) {
  const storage = safeStorage("localStorage");
  let raw = null;
  if (storage) {
    try {
      raw = JSON.parse(storage.getItem(PREFS_KEY) || "null");
    } catch (error) {
      console.warn("[vmux] ignoring invalid saved preferences", error instanceof Error ? error.name : "error");
    }
  }
  const prefs = migratePrefs(raw, width);
  if (storage) {
    try {
      storage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (error) {
      console.warn("[vmux] preferences could not be saved", error instanceof Error ? error.name : "error");
    }
  }
  return prefs;
}

/** Persist a complete PrefsV2 value after normalizing it. */
export function savePrefs(value) {
  const prefs = migratePrefs(value);
  const storage = safeStorage("localStorage");
  if (storage) {
    try {
      storage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (error) {
      console.warn("[vmux] preferences could not be saved", error instanceof Error ? error.name : "error");
    }
  }
  return prefs;
}

let ACTIVE_PREFS = loadPrefs();
const PREFS_SUBSCRIBERS = new Set();

function applyPrefsToDocument(prefs) {
  if (typeof globalThis.document === "undefined") return;
  const root = globalThis.document.documentElement;
  const media = typeof globalThis.matchMedia === "function"
    ? globalThis.matchMedia("(prefers-color-scheme: dark)") : null;
  const dark = prefs.theme === "dark" || (prefs.theme === "auto" && Boolean(media && media.matches));
  root.classList.toggle("t-dark", dark);
  root.classList.toggle("t-light", !dark);
  root.classList.toggle("no-glass", !prefs.glass);
  root.classList.toggle("no-ambient", !prefs.ambient);
}

applyPrefsToDocument(ACTIVE_PREFS);
if (typeof globalThis.matchMedia === "function") {
  const colorScheme = globalThis.matchMedia("(prefers-color-scheme: dark)");
  colorScheme.addEventListener?.("change", () => {
    if (ACTIVE_PREFS.theme === "auto") applyPrefsToDocument(ACTIVE_PREFS);
  });
}

/** Merge and broadcast a browser-local preference patch. */
export function setPrefs(patch) {
  ACTIVE_PREFS = savePrefs({ ...ACTIVE_PREFS, ...(isObject(patch) ? patch : {}) });
  applyPrefsToDocument(ACTIVE_PREFS);
  for (const listener of Array.from(PREFS_SUBSCRIBERS)) listener();
  return ACTIVE_PREFS;
}

/** React subscription to PrefsV2. */
export function usePrefs() {
  const ReactRuntime = globalThis.React;
  if (!ReactRuntime || typeof ReactRuntime.useSyncExternalStore !== "function") {
    throw new Error("React 18 useSyncExternalStore is required");
  }
  const subscribe = (listener) => {
    PREFS_SUBSCRIBERS.add(listener);
    return () => PREFS_SUBSCRIBERS.delete(listener);
  };
  return ReactRuntime.useSyncExternalStore(subscribe, () => ACTIVE_PREFS, () => ACTIVE_PREFS);
}

export function normalizeStatus(value) {
  const raw = typeof value === "string" ? value : (isObject(value) ? value.status : "");
  return KNOWN_STATUSES.includes(raw) ? raw : "unknown";
}

export function actionsAllowed(connection, pane = null) {
  if (!connection || ![CONNECTION_MODES.LIVE, CONNECTION_MODES.REST, "rest"].includes(connection.mode)) return false;
  if (connection.compatibility && connection.compatibility.blocksActions) return false;
  if (!pane) return true;
  return normalizeStatus(pane.status) !== "offline"
    && pane.actionable !== false
    && !String(pane.id || "").startsWith("cfg:");
}

function consumeIncomingToken() {
  const storage = safeStorage("localStorage");
  let stored = "";
  try {
    stored = storage ? storage.getItem(TOKEN_KEY) || "" : "";
  } catch (error) {
    console.warn("[vmux] saved credential is unavailable", error instanceof Error ? error.name : "error");
  }

  if (typeof globalThis.location === "undefined") return stored;
  const url = new URL(globalThis.location.href);
  if (!url.searchParams.has("token")) return stored;

  const incoming = url.searchParams.get("token") || "";
  if (incoming && storage) {
    try {
      storage.setItem(TOKEN_KEY, incoming);
      stored = incoming;
    } catch (error) {
      console.warn("[vmux] credential could not be saved", error instanceof Error ? error.name : "error");
    }
  }

  // Delete only the token parameter. Preserve every other query parameter,
  // pathname, and hash while keeping this navigation out of browser history.
  url.searchParams.delete("token");
  if (globalThis.history && typeof globalThis.history.replaceState === "function") {
    const replacement = `${url.pathname}${url.search}${url.hash}`;
    globalThis.history.replaceState(globalThis.history.state, "", replacement);
  }
  return incoming || stored;
}

/** The private bearer used by the default API/store instance. */
export const TOKEN = consumeIncomingToken();

/** Remove old Cache API entries whose request key could retain a credential. */
export async function purgeTokenCacheEntries() {
  if (!globalThis.caches || typeof globalThis.caches.keys !== "function") return 0;
  let removed = 0;
  const cacheNames = await globalThis.caches.keys();
  for (const name of cacheNames) {
    const cache = await globalThis.caches.open(name);
    const requests = await cache.keys();
    for (const request of requests) {
      let hasToken = false;
      try {
        const url = new URL(request.url);
        hasToken = url.searchParams.has("token") || request.headers.has("authorization");
      } catch (error) {
        console.warn("[vmux] skipped an unreadable cache key", error instanceof Error ? error.name : "error");
      }
      if (hasToken && await cache.delete(request)) removed += 1;
    }
  }
  return removed;
}

/** Clear browser credentials and any legacy credential-bearing cache keys. */
export async function clearCredentials({ reload = false } = {}) {
  for (const name of ["localStorage", "sessionStorage"]) {
    const storage = safeStorage(name);
    if (!storage) continue;
    try {
      storage.removeItem(TOKEN_KEY);
    } catch (error) {
      console.warn(`[vmux] ${name} credential could not be cleared`, error instanceof Error ? error.name : "error");
    }
  }
  try {
    await purgeTokenCacheEntries();
  } catch (error) {
    console.warn("[vmux] credential cache cleanup failed", error instanceof Error ? error.name : "error");
  }
  if (globalThis.navigator?.serviceWorker) {
    try {
      const controller = globalThis.navigator.serviceWorker.controller;
      if (controller) controller.postMessage({ type: "PURGE_CREDENTIALS" });
      const registration = await globalThis.navigator.serviceWorker.getRegistration();
      registration?.active?.postMessage({ type: "PURGE_CREDENTIALS" });
      registration?.waiting?.postMessage({ type: "PURGE_CREDENTIALS" });
    } catch (error) {
      console.warn("[vmux] service-worker credential purge failed", error instanceof Error ? error.name : "error");
    }
  }
  if (typeof globalThis.dispatchEvent === "function" && typeof globalThis.Event === "function") {
    globalThis.dispatchEvent(new globalThis.Event("vmux:credentials-cleared"));
  }
  if (reload && typeof globalThis.location !== "undefined") {
    globalThis.location.replace(`${globalThis.location.pathname}${globalThis.location.hash}`);
  }
}

// Start cleanup without blocking first paint. The rejection handler is
// intentional: credential cleanup must not become a silent failure.
if (typeof globalThis.window !== "undefined") {
  void purgeTokenCacheEntries().catch((error) => {
    console.warn("[vmux] legacy credential cache cleanup failed", error instanceof Error ? error.name : "error");
  });
}

function cleanMessage(value, fallback) {
  if (typeof value !== "string") return fallback;
  const clean = value.replace(/[\u0000-\u001f\u007f]+/g, " ").trim().slice(0, 300);
  return clean || fallback;
}

function safeEndpoint(url) {
  try {
    return new URL(url, globalThis.location ? globalThis.location.origin : "http://localhost").pathname;
  } catch (_) {
    return "/api";
  }
}

export class ApiError extends Error {
  constructor(message, {
    category = "api",
    status = 0,
    endpoint = "/api",
    timestamp = Date.now(),
    retryable = false,
    cause = null,
  } = {}) {
    super(cleanMessage(message, "Request failed"));
    this.name = "ApiError";
    this.userMessage = this.message;
    this.category = category;
    this.status = Number.isFinite(status) ? status : 0;
    this.endpoint = safeEndpoint(endpoint);
    this.timestamp = timestamp;
    this.retryable = Boolean(retryable);
    if (cause) this.cause = cause;
  }
}

function categoryForStatus(status) {
  if (status === 401) return "unauthorized";
  if (status === 413) return "too_large";
  if (status === 415) return "unsupported";
  if (status === 507) return "storage";
  if (status === 400 || status === 422) return "validation";
  if (status === 404) return "not_found";
  if (status >= 500) return "server";
  return "http";
}

function apiPath(path, origin) {
  if (typeof path !== "string" || !path) throw new ApiError("Invalid API endpoint", { category: "validation" });
  if (/^https?:/i.test(path) || path.startsWith("//")) {
    const absolute = new URL(path, origin);
    if (absolute.origin !== origin) throw new ApiError("Cross-origin API request blocked", { category: "validation" });
    path = `${absolute.pathname}${absolute.search}`;
  }
  const withPrefix = path.startsWith("/api/") || path === "/api"
    ? path
    : `/api${path.startsWith("/") ? path : `/${path}`}`;
  const url = new URL(withPrefix, origin);
  if (url.searchParams.has("token")) {
    throw new ApiError("Credentials are not allowed in REST query parameters", {
      category: "validation", endpoint: url.pathname,
    });
  }
  return url;
}

/** Create a same-origin authenticated JSON/raw API client with bounded waits. */
export function createApiClient({
  token = TOKEN,
  fetchImpl = globalThis.fetch ? globalThis.fetch.bind(globalThis) : null,
  xhrFactory = globalThis.XMLHttpRequest ? () => new globalThis.XMLHttpRequest() : null,
  origin = globalThis.location ? globalThis.location.origin : "http://localhost",
  defaultTimeoutMs = 10000,
} = {}) {
  function decodeResponse(status, rawText, endpoint) {
    let data = null;
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch (error) {
        // Authentication proxies and generic HTTP error handlers often return
        // plain text or HTML. Only a successful malformed response is a
        // protocol failure; HTTP status remains authoritative for errors.
        if (status >= 200 && status < 300) {
          throw new ApiError("Server returned malformed JSON", {
            category: "protocol", status, endpoint, cause: error,
          });
        }
      }
    }
    if (status < 200 || status >= 300) {
      const detail = isObject(data) ? data.detail : null;
      throw new ApiError(cleanMessage(detail, `Request failed (${status})`), {
        category: categoryForStatus(status),
        status,
        endpoint,
        retryable: status >= 500,
      });
    }
    return data;
  }

  async function request(path, {
    method,
    body,
    rawBody,
    contentType,
    onUploadProgress,
    timeoutMs = defaultTimeoutMs,
    signal,
    headers: extraHeaders,
  } = {}) {
    if (!fetchImpl && !(rawBody !== undefined && xhrFactory)) {
      throw new ApiError("Fetch is unavailable", { category: "network", endpoint: path });
    }
    if (body !== undefined && rawBody !== undefined) {
      throw new ApiError("A request cannot contain both JSON and raw bodies", {
        category: "validation", endpoint: path,
      });
    }
    const url = apiPath(path, origin);
    const endpoint = url.pathname;
    const hasJsonBody = body !== undefined;
    const hasRawBody = rawBody !== undefined;
    const controller = new AbortController();
    let timedOut = false;
    let detachSignal = null;
    if (signal) {
      const abort = () => controller.abort(signal.reason);
      if (signal.aborted) abort();
      else {
        signal.addEventListener("abort", abort, { once: true });
        detachSignal = () => signal.removeEventListener("abort", abort);
      }
    }
    const timer = timeoutMs > 0 ? globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs) : null;
    const headers = new Headers(extraHeaders || {});
    headers.set("Accept", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (hasJsonBody) headers.set("Content-Type", "application/json");
    if (hasRawBody) {
      const declaredType = contentType || rawBody?.type || "application/octet-stream";
      headers.set("Content-Type", declaredType);
    }

    try {
      if (hasRawBody && xhrFactory) {
        return await new Promise((resolve, reject) => {
          const xhr = xhrFactory();
          let settled = false;
          const finish = (callback, value) => {
            if (settled) return;
            settled = true;
            controller.signal.removeEventListener("abort", abort);
            callback(value);
          };
          const abort = () => xhr.abort();
          if (controller.signal.aborted) {
            finish(reject, new ApiError(timedOut ? "Request timed out" : "Request cancelled", {
              category: timedOut ? "timeout" : "cancelled", endpoint, retryable: timedOut,
            }));
            return;
          }
          controller.signal.addEventListener("abort", abort, { once: true });
          xhr.open(method || "POST", url.toString(), true);
          xhr.withCredentials = true;
          headers.forEach((value, name) => xhr.setRequestHeader(name, value));
          if (typeof onUploadProgress === "function") {
            const total = Number(rawBody?.size || 0);
            onUploadProgress({ loaded: 0, total, percent: total ? 0 : null });
            xhr.upload?.addEventListener("progress", (event) => {
              const knownTotal = event.lengthComputable && event.total > 0 ? event.total : total;
              onUploadProgress({
                loaded: event.loaded,
                total: knownTotal,
                percent: knownTotal ? Math.min(100, (event.loaded / knownTotal) * 100) : null,
              });
            });
          }
          xhr.onload = () => {
            try {
              if (typeof onUploadProgress === "function") {
                const total = Number(rawBody?.size || 0);
                onUploadProgress({ loaded: total, total, percent: 100 });
              }
              finish(resolve, decodeResponse(xhr.status, xhr.responseText || "", endpoint));
            } catch (error) {
              finish(reject, error);
            }
          };
          xhr.onerror = () => finish(reject, new ApiError("Network request failed", {
            category: "network", endpoint, retryable: true,
          }));
          xhr.onabort = () => finish(reject, new ApiError(timedOut ? "Request timed out" : "Request cancelled", {
            category: timedOut ? "timeout" : "cancelled", endpoint, retryable: timedOut,
          }));
          try {
            xhr.send(rawBody);
          } catch (error) {
            finish(reject, new ApiError("Network request failed", {
              category: "network", endpoint, retryable: true, cause: error,
            }));
          }
        });
      }
      if (hasRawBody && typeof onUploadProgress === "function") {
        const total = Number(rawBody?.size || 0);
        onUploadProgress({ loaded: 0, total, percent: total ? 0 : null });
      }
      const response = await fetchImpl(url.toString(), {
        method: method || (hasJsonBody || hasRawBody ? "POST" : "GET"),
        headers,
        body: hasRawBody ? rawBody : (hasJsonBody ? JSON.stringify(body) : undefined),
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      const rawText = response.status === 204 ? "" : await response.text();
      if (hasRawBody && typeof onUploadProgress === "function") {
        const total = Number(rawBody?.size || 0);
        onUploadProgress({ loaded: total, total, percent: 100 });
      }
      return decodeResponse(response.status, rawText, endpoint);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (timedOut) {
        throw new ApiError("Request timed out", {
          category: "timeout", endpoint, retryable: true, cause: error,
        });
      }
      if (controller.signal.aborted) {
        throw new ApiError("Request cancelled", { category: "cancelled", endpoint, cause: error });
      }
      throw new ApiError("Network request failed", {
        category: "network", endpoint, retryable: true, cause: error,
      });
    } finally {
      if (timer !== null) globalThis.clearTimeout(timer);
      if (detachSignal) detachSignal();
    }
  }

  return Object.freeze({ request });
}

function parseSemver(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/);
  if (!match) return null;
  return {
    parts: [Number(match[1]), Number(match[2]), Number(match[3])],
    prerelease: match[4] || null,
  };
}

function compareSemver(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left.parts[index] !== right.parts[index]) return left.parts[index] < right.parts[index] ? -1 : 1;
  }
  if (left.prerelease === right.prerelease) return 0;
  if (left.prerelease === null) return 1;
  if (right.prerelease === null) return -1;
  return left.prerelease.localeCompare(right.prerelease, undefined, { numeric: true });
}

/** Evaluate the web-relevant portion of `/api/config` compatibility metadata. */
export function evaluateCompatibility(config, {
  expectedProtocol = PROTOCOL_VERSION,
  minimumServerVersion = MINIMUM_SERVER_VERSION,
} = {}) {
  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const configPresent = config !== null && config !== undefined;
  const hasInfo = isObject(config) && hasOwn(config, "_info");
  const rawInfo = hasInfo ? config._info : null;
  const info = isObject(rawInfo) ? rawInfo : null;
  const hasCompatibility = !!info && hasOwn(info, "compatibility");
  const hasServerVersion = !!info && hasOwn(info, "version");
  const rawCompatibility = hasCompatibility ? info.compatibility : null;
  const rawServerVersion = hasServerVersion ? info.version : null;
  const hasProtocol = isObject(rawCompatibility) && hasOwn(rawCompatibility, "protocol_version");
  const serverVersion = typeof rawServerVersion === "string" && rawServerVersion.trim()
    ? rawServerVersion.trim() : null;
  const protocolValue = hasProtocol ? rawCompatibility.protocol_version : null;
  const base = {
    verified: false,
    blocksActions: false,
    serverVersion,
    protocolVersion: Number.isInteger(protocolValue) ? protocolValue : null,
    expectedProtocol,
    minimumServerVersion,
  };

  // A known mismatch wins even if another metadata field is absent.
  if (isObject(rawCompatibility) && Number.isInteger(protocolValue)
    && protocolValue !== expectedProtocol) {
    return {
      ...base,
      status: "incompatible",
      blocksActions: true,
      reason: "protocol_mismatch",
      message: `Protocol ${protocolValue} is incompatible with this web client (expected ${expectedProtocol}).`,
    };
  }
  if ((configPresent && !isObject(config))
    || (hasInfo && !info)
    || (hasCompatibility && !isObject(rawCompatibility))
    || (hasProtocol && !Number.isInteger(protocolValue))
    || (hasServerVersion && (typeof rawServerVersion !== "string" || !rawServerVersion.trim()))) {
    return {
      ...base,
      status: "incompatible",
      blocksActions: true,
      reason: "metadata_malformed",
      message: "Server compatibility metadata is malformed. Update vmux before sending actions.",
    };
  }
  const parsedMinimum = parseSemver(minimumServerVersion);
  const parsedServer = serverVersion ? parseSemver(serverVersion) : null;
  if (serverVersion && (!parsedServer || !parsedMinimum)) {
    return {
      ...base,
      status: "incompatible",
      blocksActions: true,
      reason: "version_malformed",
      message: "Server version metadata is malformed. Update vmux before sending actions.",
    };
  }
  if (parsedServer && parsedMinimum && compareSemver(parsedServer, parsedMinimum) < 0) {
    return {
      ...base,
      status: "incompatible",
      blocksActions: true,
      reason: "server_too_old",
      message: `vmux server ${serverVersion} is older than the supported ${minimumServerVersion}.`,
    };
  }

  // Missing metadata is the documented legacy condition and is non-blocking.
  if (!info || !hasCompatibility || !hasProtocol || !hasServerVersion) {
    return {
      ...base,
      status: "unverified",
      reason: "metadata_missing",
      message: "Server compatibility is unverified.",
    };
  }
  return {
    ...base,
    status: "verified",
    verified: true,
    reason: "compatible",
    message: "Client and server are compatible.",
  };
}

function normalizeTextArray(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((line) => typeof line === "string");
}

function normalizeMenu(value) {
  if (!Array.isArray(value)) return [];
  const out = [];
  for (const option of value) {
    if (!isObject(option)) continue;
    const key = textValue(option.key);
    const label = textValue(option.label);
    if (!key || !label) continue;
    out.push({
      key,
      label,
      description: textValue(option.description),
      selected: option.selected === true,
      freeform: option.freeform === true,
    });
  }
  return out;
}

function arraysEqual(left, right, itemEqual = (a, b) => a === b) {
  if (left === right) return true;
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (!itemEqual(left[index], right[index])) return false;
  }
  return true;
}

function menuEqual(left, right) {
  return arraysEqual(left, right, (a, b) => (
    a.key === b.key && a.label === b.label && a.description === b.description
      && a.selected === b.selected && a.freeform === b.freeform
  ));
}

function panesEqual(left, right) {
  if (!left || !right) return false;
  const primitives = [
    "id", "target", "name", "kind", "rawKind", "kindLabel", "status", "rawStatus",
    "statusLabel", "title", "question", "updated", "changed", "window", "starred",
    "interacted", "actionable", "configuredOffline",
  ];
  return primitives.every((key) => left[key] === right[key])
    && arraysEqual(left.preview, right.preview)
    && arraysEqual(left.lines, right.lines)
    && menuEqual(left.menu, right.menu);
}

/** Normalize one untrusted PaneState-shaped JSON object for safe rendering. */
export function normalizePane(raw, index = 0) {
  const source = isObject(raw) ? raw : {};
  const suppliedId = textValue(source.id).trim();
  const id = suppliedId || `unknown:${index}`;
  const target = textValue(source.target).trim() || id;
  const rawStatus = textValue(source.status).trim() || "unknown";
  const status = KNOWN_STATUSES.includes(rawStatus) ? rawStatus : "unknown";
  const rawKind = textValue(source.kind).trim() || "generic";
  const kind = KNOWN_KINDS.includes(rawKind) ? rawKind : "generic";
  const lines = normalizeTextArray(source.lines);
  const suppliedPreview = normalizeTextArray(source.preview);
  const preview = suppliedPreview.length
    ? suppliedPreview
    : lines.filter((line) => line.trim()).slice(-6);
  const configuredOffline = id.startsWith("cfg:") || status === "offline";
  return {
    id,
    target,
    name: textValue(source.name).trim() || target,
    kind,
    rawKind: kind === rawKind ? null : rawKind,
    kindLabel: KIND_LABELS[kind] || KIND_LABELS.generic,
    status,
    rawStatus: status === rawStatus ? null : rawStatus,
    statusLabel: STATUS_LABELS[status] || STATUS_LABELS.unknown,
    title: textValue(source.title),
    question: typeof source.question === "string" ? source.question : null,
    menu: normalizeMenu(source.menu),
    preview,
    lines,
    updated: finiteNumber(source.updated, 0),
    changed: source.changed === true,
    window: textValue(source.window),
    starred: source.starred === true,
    interacted: finiteNumber(source.interacted, 0),
    configuredOffline,
    actionable: Boolean(suppliedId) && !configuredOffline,
  };
}

/**
 * Reuse object identities for unchanged panes across full REST/WS snapshots.
 * This is intentionally per-store; no terminal data is persisted globally.
 */
export function createPaneNormalizer() {
  let previous = new Map();
  function normalize(payload) {
    if (!isObject(payload) || !Array.isArray(payload.panes)
      || (payload.type != null && payload.type !== "state")) {
      throw new ApiError("Malformed state snapshot", {
        category: "protocol", endpoint: "/api/state",
      });
    }
    const next = new Map();
    const panes = [];
    payload.panes.forEach((raw, index) => {
      const candidate = normalizePane(raw, index);
      const prior = previous.get(candidate.id);
      const pane = panesEqual(prior, candidate) ? prior : candidate;
      next.set(pane.id, pane);
      panes.push(pane);
    });
    previous = next;
    return panes;
  }
  normalize.reset = () => { previous = new Map(); };
  return normalize;
}

const REST_INTERVAL_MS = 2000;
const OFFLINE_GRACE_MS = 10000;
const WS_RETRY_INITIAL_MS = 500;
const WS_RETRY_MAX_MS = 8000;
const WS_RETRY_JITTER_MS = 300;

function sanitizedIssue(error, defaults = {}) {
  const source = error instanceof ApiError ? error : null;
  return {
    category: source ? source.category : defaults.category || "connection",
    message: cleanMessage(source ? source.message : defaults.message, "Connection failed"),
    endpoint: safeEndpoint(source ? source.endpoint : defaults.endpoint || "/api/state"),
    httpStatus: source ? source.status : finiteNumber(defaults.httpStatus, 0),
    timestamp: source ? source.timestamp : Date.now(),
  };
}

/**
 * Return only approved diagnostic fields. In particular, this never includes
 * a bearer, full URL, query string, pane content, user agent, or usage account.
 */
export function technicalDetails(connection, config, {
  endpoint,
  clientVersion = WEB_CLIENT_VERSION,
  host = globalThis.location ? globalThis.location.host : "",
} = {}) {
  const info = isObject(config) && isObject(config._info) ? config._info : {};
  const compatibility = connection && connection.compatibility
    ? connection.compatibility : evaluateCompatibility(config);
  const issue = connection && connection.issue ? connection.issue : {};
  return {
    host: textValue(host),
    endpoint: safeEndpoint(endpoint || issue.endpoint || "/api/state"),
    httpStatus: finiteNumber(issue.httpStatus, 0),
    clientVersion: textValue(clientVersion, WEB_CLIENT_VERSION),
    serverVersion: compatibility.serverVersion || textValue(info.version) || null,
    protocolVersion: Number.isInteger(compatibility.protocolVersion)
      ? compatibility.protocolVersion : null,
    category: textValue(issue.category, compatibility.reason || "connection"),
    timestamp: new Date(finiteNumber(issue.timestamp, Date.now())).toISOString(),
  };
}

function actionRecordKey(paneId, actionKey) {
  return `${encodeURIComponent(paneId)}::${encodeURIComponent(actionKey)}`;
}

/**
 * Create the in-memory vmux state store and resilient REST/WebSocket transport.
 * Consumers may use `subscribe/getSnapshot` directly or the React hook below.
 */
export function createVmuxStore({
  token = TOKEN,
  apiClient = null,
  WebSocketImpl = globalThis.WebSocket || null,
  origin = globalThis.location ? globalThis.location.origin : "http://localhost",
  now = () => Date.now(),
  random = () => Math.random(),
} = {}) {
  const client = apiClient || createApiClient({ token, origin });
  const normalizeSnapshot = createPaneNormalizer();
  const listeners = new Set();
  const optimisticStars = new Map();
  let configLoaded = false;
  let stateBootstrapped = false;
  let running = false;
  let authBlocked = false;
  let sessionEnded = false;
  let onlineListenersAttached = false;
  let socket = null;
  let socketLastStateAt = 0;
  let socketHasState = false;
  let wsStateGeneration = 0;
  let retryAttempt = 0;
  let retryTimer = null;
  let restTimer = null;
  let restInFlight = null;
  let probeEpoch = 0;
  let watchdogTimer = null;
  let offlineTimer = null;
  let failureSince = 0;
  let degradedIssue = null;

  const initialCompatibility = evaluateCompatibility(null);
  let snapshot = {
    panes: [],
    paneMap: new Map(),
    config: null,
    sid: null,
    connection: {
      mode: CONNECTION_MODES.CONNECTING,
      lastSuccessAt: null,
      issue: null,
      compatibility: initialCompatibility,
    },
    actionStates: {},
    latestActionByPane: {},
    lastEvent: null,
  };

  function publish(patch) {
    snapshot = { ...snapshot, ...patch };
    for (const listener of Array.from(listeners)) {
      try {
        listener();
      } catch (error) {
        console.error("[vmux] state subscriber failed", error);
      }
    }
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function effectiveMode(requested) {
    if (authBlocked) return CONNECTION_MODES.UNAUTHORIZED;
    if (snapshot.connection.compatibility.blocksActions) return CONNECTION_MODES.INCOMPATIBLE;
    return requested;
  }

  function setConnection(mode, { issue = snapshot.connection.issue, lastSuccessAt } = {}) {
    publish({
      connection: {
        ...snapshot.connection,
        mode: effectiveMode(mode),
        issue,
        lastSuccessAt: lastSuccessAt === undefined
          ? snapshot.connection.lastSuccessAt : lastSuccessAt,
      },
    });
  }

  function setCompatibility(compatibility) {
    const requestedMode = compatibility.blocksActions
      ? CONNECTION_MODES.INCOMPATIBLE : snapshot.connection.mode;
    publish({
      connection: {
        ...snapshot.connection,
        compatibility,
        mode: authBlocked ? CONNECTION_MODES.UNAUTHORIZED : requestedMode,
        issue: compatibility.blocksActions ? {
          category: compatibility.reason,
          message: compatibility.message,
          endpoint: "/api/config",
          httpStatus: 0,
          timestamp: now(),
        } : snapshot.connection.issue,
      },
    });
  }

  function clearTimer(name) {
    const timer = {
      retry: retryTimer, rest: restTimer, watchdog: watchdogTimer, offline: offlineTimer,
    }[name];
    if (timer !== null) globalThis.clearTimeout(timer);
    if (name === "retry") retryTimer = null;
    if (name === "rest") restTimer = null;
    if (name === "watchdog") watchdogTimer = null;
    if (name === "offline") offlineTimer = null;
  }

  function closeSocket(code = 1000, reason = "") {
    const active = socket;
    socket = null;
    socketLastStateAt = 0;
    socketHasState = false;
    clearTimer("watchdog");
    if (active) {
      active.onopen = null;
      active.onmessage = null;
      active.onerror = null;
      active.onclose = null;
      try {
        active.close(code, reason);
      } catch (error) {
        console.warn("[vmux] WebSocket could not be closed cleanly", error instanceof Error ? error.name : "error");
      }
    }
  }

  function applyOptimisticStars(panes) {
    return panes.map((pane) => {
      const optimistic = optimisticStars.get(pane.id);
      if (!optimistic) return pane;
      if (pane.starred === optimistic.value) {
        optimisticStars.delete(pane.id);
        return pane;
      }
      return { ...pane, starred: optimistic.value };
    });
  }

  function adoptState(payload) {
    const panes = applyOptimisticStars(normalizeSnapshot(payload));
    publish({ panes, paneMap: new Map(panes.map((pane) => [pane.id, pane])) });
    return panes;
  }

  function cancelOfflineCountdown() {
    failureSince = 0;
    clearTimer("offline");
  }

  function recordStateSuccess(source) {
    cancelOfflineCountdown();
    const timestamp = now();
    if (source === "ws") {
      retryAttempt = 0;
      degradedIssue = null;
      setConnection(CONNECTION_MODES.LIVE, { issue: null, lastSuccessAt: timestamp });
      if (configLoaded) clearTimer("rest");
      else scheduleRest(0);
    } else {
      const liveSocket = socketIsHealthy();
      setConnection(liveSocket ? CONNECTION_MODES.LIVE : CONNECTION_MODES.REST, {
        issue: liveSocket ? null : degradedIssue,
        lastSuccessAt: timestamp,
      });
    }
  }

  function scheduleOffline(issue) {
    if (authBlocked || sessionEnded || !running) return;
    if (!failureSince) failureSince = now();
    const elapsed = Math.max(0, now() - failureSince);
    const remaining = Math.max(0, OFFLINE_GRACE_MS - elapsed);
    if (remaining === 0) {
      clearTimer("offline");
      setConnection(CONNECTION_MODES.OFFLINE, { issue });
      return;
    }
    setConnection(CONNECTION_MODES.CONNECTING, { issue });
    clearTimer("offline");
    offlineTimer = globalThis.setTimeout(() => {
      offlineTimer = null;
      if (!running || authBlocked || sessionEnded || !failureSince) return;
      if (now() - failureSince >= OFFLINE_GRACE_MS) {
        setConnection(CONNECTION_MODES.OFFLINE, { issue });
      }
    }, remaining);
  }

  function noteFailure(error, defaults) {
    const issue = sanitizedIssue(error, defaults);
    degradedIssue = issue;
    scheduleOffline(issue);
  }

  function becomeUnauthorized(error) {
    authBlocked = true;
    clearTimer("retry");
    clearTimer("rest");
    clearTimer("offline");
    closeSocket(1000, "authorization required");
    const issue = sanitizedIssue(error, {
      category: "unauthorized",
      message: "The access token is missing or invalid.",
      endpoint: "/api/state",
      httpStatus: 401,
    });
    setConnection(CONNECTION_MODES.UNAUTHORIZED, { issue });
  }

  function protocolFailure(error) {
    const issue = sanitizedIssue(error, {
      category: "protocol",
      message: "The server returned an incompatible payload.",
      endpoint: "/api/state",
    });
    const compatibility = {
      ...snapshot.connection.compatibility,
      status: "incompatible",
      verified: false,
      blocksActions: true,
      reason: "payload_malformed",
      message: issue.message,
    };
    setCompatibility(compatibility);
  }

  function handleApiError(error) {
    if (error instanceof ApiError && error.category === "unauthorized") {
      becomeUnauthorized(error);
    }
    return error;
  }

  async function request(path, options = {}) {
    const { suppressAuthHandling = false, ...clientOptions } = options;
    try {
      return await client.request(path, clientOptions);
    } catch (error) {
      if (!suppressAuthHandling) handleApiError(error);
      throw error;
    }
  }

  function watchdogDelay() {
    const poll = snapshot.config ? finiteNumber(snapshot.config.poll_interval, 0.7) : 0.7;
    return Math.max(3000, poll * 2000 + 1000);
  }

  function armWatchdog() {
    clearTimer("watchdog");
    if (!running || !socket || socket.readyState !== 1) return;
    const delay = Math.max(0, socketLastStateAt + watchdogDelay() - now());
    watchdogTimer = globalThis.setTimeout(() => {
      watchdogTimer = null;
      if (!running || !socket || now() - socketLastStateAt < watchdogDelay()) {
        armWatchdog();
        return;
      }
      const issue = sanitizedIssue(null, {
        category: "silent_socket",
        message: "Live updates paused; checking the server over REST.",
        endpoint: "/ws",
      });
      degradedIssue = issue;
      scheduleOffline(issue);
      closeSocket(4000, "silent socket");
      scheduleRest(0);
      scheduleSocketRetry();
    }, delay);
  }

  function socketIsHealthy() {
    return Boolean(socket && socket.readyState === 1 && socketHasState
      && socketLastStateAt && now() - socketLastStateAt < watchdogDelay());
  }

  async function probeRest() {
    if (restInFlight) return restInFlight;
    const epoch = probeEpoch;
    let task;
    task = (async () => {
      try {
        if (!configLoaded) {
          const config = await request("/config", { timeoutMs: 10000, suppressAuthHandling: true });
          if (epoch !== probeEpoch || !running) return false;
          if (!isObject(config)) {
            throw new ApiError("Malformed server configuration", {
              category: "protocol", endpoint: "/api/config",
            });
          }
          configLoaded = true;
          const compatibility = evaluateCompatibility(config);
          publish({ config });
          setCompatibility(compatibility);
        }
        const generationBeforeRequest = wsStateGeneration;
        const state = await request("/state", { timeoutMs: 10000, suppressAuthHandling: true });
        if (epoch !== probeEpoch || !running) return false;
        // A WebSocket state that arrived while this request was in flight is
        // newer than the REST payload. Keep that pane snapshot, but still use
        // the successful REST response as a connectivity signal.
        if (!stateBootstrapped || wsStateGeneration === generationBeforeRequest) {
          adoptState(state);
          stateBootstrapped = true;
        }
        recordStateSuccess("rest");
        return true;
      } catch (error) {
        if (epoch !== probeEpoch || !running) return false;
        if (error instanceof ApiError && error.category === "unauthorized") {
          becomeUnauthorized(error);
          return false;
        }
        if (error instanceof ApiError && error.category === "protocol") protocolFailure(error);
        else noteFailure(error, { endpoint: "/api/state" });
        return false;
      } finally {
        if (restInFlight === task) restInFlight = null;
      }
    })();
    restInFlight = task;
    return task;
  }

  async function refreshConfig() {
    try {
      const config = await request("/config", { timeoutMs: 10000, suppressAuthHandling: true });
      if (!running || !isObject(config)) return false;
      configLoaded = true;
      publish({ config });
      setCompatibility(evaluateCompatibility(config));
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.category === "unauthorized") becomeUnauthorized(error);
      else noteFailure(error, { endpoint: "/api/config" });
      return false;
    }
  }

  function scheduleRest(delay = REST_INTERVAL_MS) {
    if (!running || authBlocked || sessionEnded) return;
    clearTimer("rest");
    restTimer = globalThis.setTimeout(async () => {
      restTimer = null;
      await probeRest();
      if (running && !authBlocked && !sessionEnded
        && (!socketIsHealthy() || !configLoaded || !stateBootstrapped)) {
        scheduleRest(REST_INTERVAL_MS);
      }
    }, Math.max(0, delay));
  }

  function scheduleSocketRetry() {
    if (!running || authBlocked || sessionEnded || retryTimer !== null) return;
    const base = Math.min(WS_RETRY_MAX_MS, WS_RETRY_INITIAL_MS * (2 ** retryAttempt));
    retryAttempt += 1;
    const delay = base + Math.max(0, Math.min(WS_RETRY_JITTER_MS, random() * WS_RETRY_JITTER_MS));
    retryTimer = globalThis.setTimeout(() => {
      retryTimer = null;
      connectSocket();
    }, delay);
  }

  function websocketUrl() {
    const url = new URL(origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/ws";
    url.search = "";
    if (token) url.searchParams.set("token", token);
    return url.toString();
  }

  function connectSocket() {
    if (!running || authBlocked || sessionEnded || socket
      || !WebSocketImpl) {
      if (!WebSocketImpl && running) {
        const issue = sanitizedIssue(null, {
          category: "websocket_unavailable",
          message: "WebSocket is unavailable; updating over REST.",
          endpoint: "/ws",
        });
        degradedIssue = issue;
        scheduleRest(0);
      }
      return;
    }

    let nextSocket;
    try {
      nextSocket = new WebSocketImpl(websocketUrl());
    } catch (error) {
      noteFailure(new ApiError("WebSocket connection failed", {
        category: "network", endpoint: "/ws", retryable: true, cause: error,
      }));
      scheduleRest(0);
      scheduleSocketRetry();
      return;
    }
    socket = nextSocket;
    socketHasState = false;
    socketLastStateAt = now();

    nextSocket.onopen = () => {
      if (socket !== nextSocket || !running) return;
      socketLastStateAt = now();
      armWatchdog();
    };
    nextSocket.onmessage = (event) => {
      if (socket !== nextSocket || !running) return;
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        const apiError = new ApiError("Malformed WebSocket message", {
          category: "protocol", endpoint: "/ws", cause: error,
        });
        protocolFailure(apiError);
        closeSocket(4002, "malformed message");
        scheduleRest(0);
        scheduleSocketRetry();
        return;
      }
      if (!isObject(message)) return;
      if (message.type === "hello") {
        const sid = textValue(message.sid);
        if (sid) publish({ sid });
        return;
      }
      if (message.type === "config_changed") {
        void refreshConfig();
        return;
      }
      if (message.type !== "state") return;
      if (!configLoaded || !stateBootstrapped) {
        socketLastStateAt = now();
        armWatchdog();
        const issue = sanitizedIssue(null, {
          category: "bootstrap",
          message: "Verifying the server before showing the workspace.",
          endpoint: !configLoaded ? "/api/config" : "/api/state",
        });
        degradedIssue = issue;
        setConnection(CONNECTION_MODES.CONNECTING, { issue });
        scheduleRest(0);
        return;
      }
      try {
        adoptState(message);
      } catch (error) {
        protocolFailure(error);
        closeSocket(4002, "malformed state");
        scheduleRest(0);
        scheduleSocketRetry();
        return;
      }
      wsStateGeneration += 1;
      socketHasState = true;
      socketLastStateAt = now();
      armWatchdog();
      recordStateSuccess("ws");
    };
    nextSocket.onerror = () => {
      // The close event drives retry/fallback and carries the only useful
      // browser-visible classification. Do not manufacture duplicate events.
    };
    nextSocket.onclose = (event) => {
      if (socket !== nextSocket) return;
      socket = null;
      socketLastStateAt = 0;
      socketHasState = false;
      clearTimer("watchdog");
      if (!running) return;
      if (event.code === 1008) {
        becomeUnauthorized(new ApiError("The access token is missing or invalid", {
          category: "unauthorized", status: 401, endpoint: "/ws",
        }));
        return;
      }
      if (event.code === 4001) {
        sessionEnded = true;
        clearTimer("retry");
        clearTimer("rest");
        clearTimer("offline");
        setConnection(CONNECTION_MODES.OFFLINE, {
          issue: sanitizedIssue(null, {
            category: "session_ended",
            message: "This browser session was disconnected.",
            endpoint: "/ws",
          }),
        });
        return;
      }
      const issue = sanitizedIssue(null, {
        category: "websocket_closed",
        message: "Live connection closed; checking the server over REST.",
        endpoint: "/ws",
      });
      degradedIssue = issue;
      scheduleOffline(issue);
      scheduleRest(0);
      scheduleSocketRetry();
    };
  }

  function onOnline() {
    if (!running || authBlocked || sessionEnded) return;
    scheduleRest(0);
    if (!socket) connectSocket();
  }

  function onOffline() {
    if (!running) return;
    noteFailure(new ApiError("Browser is offline", {
      category: "network", endpoint: "/api/state", retryable: true,
    }));
  }

  async function start() {
    if (running) return snapshot;
    running = true;
    authBlocked = false;
    sessionEnded = false;
    setConnection(CONNECTION_MODES.CONNECTING, { issue: null });
    if (!onlineListenersAttached && typeof globalThis.addEventListener === "function") {
      globalThis.addEventListener("online", onOnline);
      globalThis.addEventListener("offline", onOffline);
      onlineListenersAttached = true;
    }
    await probeRest();
    if (!running || authBlocked || sessionEnded) return snapshot;
    connectSocket();
    if (!socketIsHealthy()) scheduleRest(REST_INTERVAL_MS);
    return snapshot;
  }

  function stop() {
    running = false;
    probeEpoch += 1;
    restInFlight = null;
    clearTimer("retry");
    clearTimer("rest");
    clearTimer("watchdog");
    clearTimer("offline");
    closeSocket(1000, "client stopped");
    if (onlineListenersAttached && typeof globalThis.removeEventListener === "function") {
      globalThis.removeEventListener("online", onOnline);
      globalThis.removeEventListener("offline", onOffline);
      onlineListenersAttached = false;
    }
  }

  async function retry() {
    if (!running) return start();
    authBlocked = false;
    sessionEnded = false;
    configLoaded = false;
    stateBootstrapped = false;
    probeEpoch += 1;
    restInFlight = null;
    retryAttempt = 0;
    cancelOfflineCountdown();
    closeSocket(1000, "manual retry");
    clearTimer("retry");
    clearTimer("rest");
    degradedIssue = null;
    setConnection(CONNECTION_MODES.CONNECTING, { issue: null });
    await probeRest();
    if (!running || authBlocked || sessionEnded) return snapshot;
    connectSocket();
    if (!socketIsHealthy()) scheduleRest(REST_INTERVAL_MS);
    return snapshot;
  }

  async function refreshState() {
    const state = await request("/state", { timeoutMs: 10000 });
    const panes = adoptState(state);
    stateBootstrapped = true;
    recordStateSuccess("rest");
    return panes;
  }

  function recordAction(state, { announce = true } = {}) {
    const key = actionRecordKey(state.paneId, state.actionKey);
    const previous = snapshot.latestActionByPane[state.paneId];
    const latest = !previous || finiteNumber(state.order, 0) >= finiteNumber(previous.order, 0)
      ? state : previous;
    publish({
      actionStates: { ...snapshot.actionStates, [key]: state },
      latestActionByPane: { ...snapshot.latestActionByPane, [state.paneId]: latest },
      lastEvent: announce ? state : snapshot.lastEvent,
    });
  }

  function setOptimisticStar(paneId, value, base) {
    optimisticStars.set(paneId, { value, base });
    const panes = snapshot.panes.map((pane) => pane.id === paneId ? { ...pane, starred: value } : pane);
    publish({ panes, paneMap: new Map(panes.map((pane) => [pane.id, pane])) });
  }

  function rollbackOptimisticStar(paneId) {
    const optimistic = optimisticStars.get(paneId);
    if (!optimistic) return;
    optimisticStars.delete(paneId);
    const panes = snapshot.panes.map((pane) => (
      pane.id === paneId && pane.starred === optimistic.value
        ? { ...pane, starred: optimistic.base } : pane
    ));
    publish({ panes, paneMap: new Map(panes.map((pane) => [pane.id, pane])) });
  }

  function commitOptimisticStar(paneId) {
    const optimistic = optimisticStars.get(paneId);
    if (optimistic) optimistic.committed = true;
  }

  const store = {
    getSnapshot: () => snapshot,
    subscribe,
    start,
    stop,
    retry,
    refreshState,
    refreshConfig,
    request,
    getPane: (paneId) => snapshot.paneMap.get(paneId) || null,
    technicalDetails: (endpoint) => technicalDetails(snapshot.connection, snapshot.config, { endpoint }),
    _recordAction: recordAction,
    _setOptimisticStar: setOptimisticStar,
    _rollbackOptimisticStar: rollbackOptimisticStar,
    _commitOptimisticStar: commitOptimisticStar,
    _handleApiError: handleApiError,
  };
  store.actions = createActionDispatcher(store);
  return store;
}

function shortHash(value) {
  // FNV-1a: action keys need stable identity, not cryptographic secrecy.
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function actionErrorMessage(error) {
  if (!(error instanceof ApiError)) return "Action failed.";
  if (error.category === "unauthorized") return "Authorization is required.";
  if (error.category === "timeout") return "Action timed out. Check the pane before retrying.";
  if (error.category === "network") return "Action could not be confirmed. Check the pane before retrying.";
  if (error.category === "offline") return "Actions are unavailable while offline.";
  if (error.category === "incompatible") return "Actions are blocked until vmux is updated.";
  if (error.category === "not_found") return "The pane is no longer available.";
  if (error.category === "validation") return "The server rejected this action.";
  return "Action failed.";
}

function paneIdOf(pane) {
  return typeof pane === "string" ? pane : (pane && typeof pane.id === "string" ? pane.id : "");
}

/**
 * Central dispatcher for every pane mutation. Nothing catches and discards a
 * failure: methods resolve with the server result or reject with ApiError.
 */
export function createActionDispatcher(store) {
  const inflight = new Map();
  let actionOrder = 0;

  function currentPane(pane) {
    const id = paneIdOf(pane);
    if (id && typeof store.getPane === "function") return store.getPane(id) || null;
    return isObject(pane) ? pane : null;
  }

  function canAct(pane = null) {
    const { connection } = store.getSnapshot();
    if (connection.compatibility.blocksActions) return false;
    if (![CONNECTION_MODES.LIVE, CONNECTION_MODES.REST].includes(connection.mode)) return false;
    if (pane == null) return true;
    const resolved = currentPane(pane);
    return Boolean(resolved && resolved.actionable !== false
      && resolved.status !== "offline" && !String(resolved.id || "").startsWith("cfg:"));
  }

  function stateFor(pane, action = null) {
    const paneId = paneIdOf(pane);
    if (!paneId) return null;
    const snap = store.getSnapshot();
    if (!action) return snap.latestActionByPane[paneId] || null;
    const exact = snap.actionStates[actionRecordKey(paneId, action)];
    if (exact) return exact;
    return Object.values(snap.actionStates)
      .filter((state) => state.paneId === paneId && (state.type === action || state.actionKey === action))
      .reduce((latest, state) => (
        !latest || finiteNumber(state.order, 0) > finiteNumber(latest.order, 0) ? state : latest
      ), null);
  }

  function isPending(pane, action = null) {
    const paneId = paneIdOf(pane);
    if (action) {
      return Object.values(store.getSnapshot().actionStates).some((state) => (
        state.paneId === paneId && state.status === "pending"
        && (state.type === action || state.actionKey === action)
      ));
    }
    return Object.values(store.getSnapshot().actionStates)
      .some((state) => state.paneId === paneId && state.status === "pending");
  }

  function blockedError(pane, endpoint) {
    const connection = store.getSnapshot().connection;
    const incompatible = connection.compatibility.blocksActions;
    const offlinePane = pane && (pane.actionable === false || pane.status === "offline"
      || String(pane.id || "").startsWith("cfg:"));
    return new ApiError(
      incompatible ? "Client and server are incompatible"
        : offlinePane ? "Pane is offline" : "Connection is not ready for actions",
      {
        category: incompatible ? "incompatible" : "offline",
        endpoint,
      },
    );
  }

  function validateOk(result, endpoint) {
    if (!isObject(result) || result.ok !== true) {
      throw new ApiError("Malformed action response", { category: "protocol", endpoint });
    }
    return result;
  }

  function perform({
    paneId,
    type,
    actionKey,
    endpoint,
    body,
    pendingMessage,
    successMessage,
    timeoutMs = 15000,
    announceSuccess = true,
    validate = (value) => validateOk(value, endpoint),
    order = ++actionOrder,
  }) {
    const flightKey = actionRecordKey(paneId, actionKey);
    if (inflight.has(flightKey)) return inflight.get(flightKey);
    const startedAt = Date.now();
    store._recordAction({
      paneId,
      actionKey,
      type,
      status: "pending",
      message: pendingMessage,
      order,
      startedAt,
      finishedAt: null,
    });

    const promise = (async () => {
      try {
        const raw = await store.request(endpoint, { method: "POST", body, timeoutMs });
        const result = validate(raw);
        store._recordAction({
          paneId,
          actionKey,
          type,
          status: "success",
          message: successMessage,
          order,
          startedAt,
          finishedAt: Date.now(),
        }, { announce: announceSuccess });
        return result;
      } catch (rawError) {
        const error = rawError instanceof ApiError ? rawError : new ApiError("Action failed", {
          category: "api", endpoint, cause: rawError,
        });
        store._handleApiError(error);
        store._recordAction({
          paneId,
          actionKey,
          type,
          status: "error",
          message: actionErrorMessage(error),
          order,
          startedAt,
          finishedAt: Date.now(),
        });
        throw error;
      } finally {
        inflight.delete(flightKey);
      }
    })();
    inflight.set(flightKey, promise);
    return promise;
  }

  function select(pane, optionKey) {
    const resolved = currentPane(pane);
    if (!canAct(resolved)) return Promise.reject(blockedError(resolved, "/api/select"));
    const key = textValue(optionKey);
    if (!key) return Promise.reject(new ApiError("A menu key is required", {
      category: "validation", endpoint: "/api/select",
    }));
    return perform({
      paneId: resolved.id,
      type: "select",
      actionKey: `select:${key}`,
      endpoint: "/select",
      body: { id: resolved.id, key },
      pendingMessage: "Sending answer…",
      successMessage: "Answer sent.",
    });
  }

  async function selectThenCompose(pane, optionKey) {
    const result = await select(pane, optionKey);
    const paneId = paneIdOf(pane);
    if (typeof globalThis.dispatchEvent === "function" && typeof globalThis.CustomEvent === "function") {
      globalThis.dispatchEvent(new globalThis.CustomEvent("vmux:focus-composer", {
        detail: { id: paneId },
      }));
    }
    return result;
  }

  function key(pane, namedKey) {
    const resolved = currentPane(pane);
    if (!canAct(resolved)) return Promise.reject(blockedError(resolved, "/api/key"));
    const value = textValue(namedKey);
    if (!value) return Promise.reject(new ApiError("A named key is required", {
      category: "validation", endpoint: "/api/key",
    }));
    return perform({
      paneId: resolved.id,
      type: "key",
      actionKey: `key:${value}`,
      endpoint: "/key",
      body: { id: resolved.id, key: value },
      pendingMessage: "Sending key…",
      successMessage: "Key sent.",
    });
  }

  function text(pane, value, enter = false) {
    const resolved = currentPane(pane);
    if (!canAct(resolved)) return Promise.reject(blockedError(resolved, "/api/text"));
    const textValueRaw = typeof value === "string" ? value : "";
    if (!textValueRaw && !enter) return Promise.reject(new ApiError("Reply text is empty", {
      category: "validation", endpoint: "/api/text",
    }));
    return perform({
      paneId: resolved.id,
      type: "text",
      actionKey: `text:${enter ? 1 : 0}:${shortHash(textValueRaw)}`,
      endpoint: "/text",
      body: { id: resolved.id, text: textValueRaw, enter: Boolean(enter) },
      pendingMessage: "Sending reply…",
      successMessage: "Reply sent.",
    });
  }

  function star(pane, starred) {
    const resolved = currentPane(pane);
    if (!canAct(resolved)) return Promise.reject(blockedError(resolved, "/api/star"));
    if (!resolved.target) return Promise.reject(new ApiError("Pane target is missing", {
      category: "validation", endpoint: "/api/star",
    }));
    const desired = typeof starred === "boolean" ? starred : !resolved.starred;
    const actionKey = "star";
    const flightKey = actionRecordKey(resolved.id, actionKey);
    if (inflight.has(flightKey)) return inflight.get(flightKey);
    store._setOptimisticStar(resolved.id, desired, resolved.starred);
    const promise = perform({
      paneId: resolved.id,
      type: "star",
      actionKey,
      endpoint: "/star",
      body: { target: resolved.target, starred: desired },
      pendingMessage: desired ? "Starring pane…" : "Removing star…",
      successMessage: desired ? "Pane starred." : "Star removed.",
    });
    return promise.then((result) => {
      store._commitOptimisticStar(resolved.id);
      return result;
    }, (error) => {
      store._rollbackOptimisticStar(resolved.id);
      throw error;
    });
  }

  function broadcast(panes, value, enter = true) {
    const requested = Array.isArray(panes) ? panes : [];
    const recipients = [];
    const excluded = [];
    const seen = new Set();
    for (const candidate of requested) {
      const resolved = currentPane(candidate);
      const id = paneIdOf(candidate);
      if (!resolved || !canAct(resolved) || seen.has(resolved.id)) {
        if (id && !seen.has(id)) excluded.push(id);
        continue;
      }
      seen.add(resolved.id);
      recipients.push(resolved);
    }
    const textToSend = typeof value === "string" ? value : "";
    if (!textToSend || recipients.length === 0) {
      return Promise.reject(new ApiError(
        recipients.length === 0 ? "No actionable broadcast recipients" : "Broadcast text is empty",
        { category: "validation", endpoint: "/api/broadcast" },
      ));
    }
    const ids = recipients.map((pane) => pane.id);
    const signature = shortHash(`${ids.join("\u0000")}\u0001${enter ? 1 : 0}\u0001${textToSend}`);
    const actionKey = `broadcast:${signature}`;
    const order = ++actionOrder;
    const startedAt = Date.now();
    for (const recipient of recipients) {
      store._recordAction({
        paneId: recipient.id,
        actionKey,
        type: "broadcast",
        status: "pending",
        message: `Sending broadcast to ${recipients.length} pane${recipients.length === 1 ? "" : "s"}…`,
        order,
        startedAt,
        finishedAt: null,
      }, { announce: false });
    }

    const resultPromise = perform({
      paneId: "broadcast",
      type: "broadcast",
      actionKey,
      endpoint: "/broadcast",
      body: { ids, text: textToSend, enter: Boolean(enter) },
      pendingMessage: `Sending to ${recipients.length} pane${recipients.length === 1 ? "" : "s"}…`,
      successMessage: "Broadcast request completed.",
      timeoutMs: 30000,
      announceSuccess: false,
      order,
      validate: (result) => {
        validateOk(result, "/api/broadcast");
        if (!Number.isFinite(result.sent) || !Array.isArray(result.errors)) {
          throw new ApiError("Malformed broadcast response", {
            category: "protocol", endpoint: "/api/broadcast",
          });
        }
        return result;
      },
    });

    return resultPromise.then((result) => {
      const errors = result.errors.map((entry) => textValue(entry));
      const failedIds = ids.filter((id) => errors.some((entry) => entry === id || entry.startsWith(`${id}:`)));
      const failed = new Set(failedIds);
      const finishedAt = Date.now();
      for (const recipient of recipients) {
        const didFail = failed.has(recipient.id);
        store._recordAction({
          paneId: recipient.id,
          actionKey,
          type: "broadcast",
          status: didFail ? "error" : "success",
          message: didFail ? "Broadcast failed for this pane." : "Broadcast sent to this pane.",
          order,
          startedAt,
          finishedAt,
        }, { announce: false });
      }
      const partial = errors.length > 0 || result.sent < recipients.length;
      const message = partial
        ? `Sent to ${result.sent} of ${recipients.length} panes; ${errors.length} failed.`
        : `Sent to ${result.sent} pane${result.sent === 1 ? "" : "s"}.`;
      store._recordAction({
        paneId: "broadcast",
        actionKey,
        type: "broadcast",
        status: partial ? "error" : "success",
        message,
        order,
        startedAt,
        finishedAt,
      });
      return {
        ...result,
        requested: recipients.length,
        excluded,
        failedIds,
        retryPanes: recipients.filter((pane) => failed.has(pane.id)),
      };
    }, (error) => {
      const finishedAt = Date.now();
      for (const recipient of recipients) {
        store._recordAction({
          paneId: recipient.id,
          actionKey,
          type: "broadcast",
          status: "error",
          message: actionErrorMessage(error),
          order,
          startedAt,
          finishedAt,
        }, { announce: false });
      }
      throw error;
    });
  }

  function run(typeOrSpec, pane, payload = {}) {
    const spec = isObject(typeOrSpec) ? typeOrSpec : { ...payload, type: typeOrSpec, pane };
    switch (spec.type) {
      case "select": return select(spec.pane, spec.key);
      case "selectThenCompose": return selectThenCompose(spec.pane, spec.key);
      case "key": return key(spec.pane, spec.key);
      case "text": return text(spec.pane, spec.text, spec.enter);
      case "star": return star(spec.pane, spec.starred);
      case "broadcast": return broadcast(spec.panes, spec.text, spec.enter);
      default:
        return Promise.reject(new ApiError("Unknown action type", {
          category: "validation", endpoint: "/api",
        }));
    }
  }

  return Object.freeze({
    run,
    select,
    selectThenCompose,
    key,
    text,
    star,
    broadcast,
    isPending,
    stateFor,
    canAct,
    get lastEvent() { return store.getSnapshot().lastEvent; },
  });
}

/** Default singleton used by the no-build application. Call `.start()` once. */
export const vmuxStore = createVmuxStore();

/** Small compatibility wrapper for focused UI modules and settings. */
export function api(path, body = null, method = null, options = {}) {
  const hasBody = body !== null && body !== undefined;
  return vmuxStore.request(path, {
    method: method || (hasBody ? "POST" : "GET"),
    body: hasBody ? body : undefined,
    timeoutMs: options.timeoutMs ?? options.timeout ?? 10000,
    signal: options.signal,
  });
}

/** Upload one image as authenticated raw bytes and validate the path contract. */
export async function uploadImage(file, { signal, onProgress, timeoutMs = 120000 } = {}) {
  if (!file) {
    throw new ApiError("Choose an image to upload", { category: "validation", endpoint: "/api/images" });
  }
  const payload = await vmuxStore.request("/images", {
    method: "POST",
    rawBody: file,
    contentType: file.type || "application/octet-stream",
    timeoutMs,
    signal,
    onUploadProgress: onProgress,
  });
  if (
    !isObject(payload)
    || typeof payload.id !== "string" || !payload.id
    || typeof payload.path !== "string" || !payload.path.startsWith("/")
    || typeof payload.terminal_text !== "string" || !payload.terminal_text.trim()
    || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(payload.mime_type)
    || typeof payload.size !== "number" || !Number.isFinite(payload.size) || payload.size < 1
    || typeof payload.expires_at !== "number" || !Number.isFinite(payload.expires_at)
  ) {
    throw new ApiError("Server returned an invalid image upload response", {
      category: "protocol", endpoint: "/api/images",
    });
  }
  return payload;
}

/** Subscribe a React component to the default or supplied vmux store. */
export function useVmuxState(store = vmuxStore) {
  const ReactRuntime = globalThis.React;
  if (!ReactRuntime || typeof ReactRuntime.useSyncExternalStore !== "function") {
    throw new Error("React 18 useSyncExternalStore is required");
  }
  return ReactRuntime.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

/**
 * React action facade requested by the UI. Each method returns a Promise and
 * `lastEvent` changes for pending/success/error announcements.
 */
export function useActions(store = vmuxStore) {
  const state = useVmuxState(store);
  const actions = store.actions;
  return {
    run: actions.run,
    select: actions.select,
    selectThenCompose: actions.selectThenCompose,
    key: actions.key,
    text: actions.text,
    star: actions.star,
    broadcast: actions.broadcast,
    isPending: actions.isPending,
    stateFor: actions.stateFor,
    canAct: actions.canAct,
    lastEvent: state.lastEvent,
  };
}
