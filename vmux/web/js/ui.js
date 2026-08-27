import {
  Dialog,
  EmptyState,
  Fragment,
  Icon,
  InlineNotice,
  Segmented,
  cx,
  formatAge,
  html,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./core.js";
import {
  KIND_LABEL,
  STATUS_META,
  actionsAllowed,
  api,
  normalizeStatus,
  setPrefs,
  useActions,
  usePrefs,
} from "./state.js";
import {
  ImageUploadButton,
  ImageUploadStatus,
  appendTerminalText,
  useImageUpload,
} from "./image-upload.js";
import { UsageDashboard, useUsage } from "./usage.js";

const FILTERS = ["queue", "active", "all", "stats"];
const SORTS = [
  ["status", "Status"],
  ["name", "Name"],
  ["active", "Recent activity"],
  ["sent", "Recently sent"],
  ["target", "Pane order"],
];
const DEFAULT_SHORTCUTS = [
  ["CTRL+C", "C-c"], ["ESC", "Escape"], ["TAB", "Tab"], ["⇧TAB", "BTab"],
  ["↵", "Enter"], ["↑", "Up"], ["↓", "Down"], ["^R", "C-r"], ["^O", "C-o"], ["^E", "C-e"],
];
const DEFAULT_SNIPPETS = ["continue", "yes", "no"];
const UNKNOWN_STATUS = {
  label: "Unknown",
  shortLabel: "Unknown",
  icon: "circle-help",
  tone: "neutral",
};
const LIFECYCLE_META = {
  blocked: STATUS_META.needs_input,
  error: STATUS_META.error,
  working: STATUS_META.working,
  done: { label: "Done", shortLabel: "Done", icon: "circle-check-big", tone: "success" },
  idle: STATUS_META.idle,
  offline: STATUS_META.offline,
  unknown: STATUS_META.unknown,
};
const CONNECTION_META = {
  connecting: { label: "Connecting", icon: "loader-circle", tone: "neutral" },
  live: { label: "Live", icon: "wifi", tone: "success" },
  rest: { label: "Updating via REST", icon: "refresh-cw", tone: "warning" },
  updating_rest: { label: "Updating via REST", icon: "refresh-cw", tone: "warning" },
  offline: { label: "Offline", icon: "wifi-off", tone: "error" },
  unauthorized: { label: "Unauthorized", icon: "lock-keyhole", tone: "error" },
  incompatible: { label: "Incompatible", icon: "triangle-alert", tone: "error" },
};
const ORDER_STABILITY_MS = 2000;
const STABLE_PANE_POSITIONS = new WeakMap();

// Deliberate expansion is session state, not a preference. Keeping it outside
// TreeView means switching layouts or closing a compact tree sheet does not
// discard the user's work.
const SESSION_TREE_EXPANDED = new Set();

function normalizedStatus(value) {
  let next = value;
  try { next = normalizeStatus(value); } catch (_) { next = value; }
  if (next && typeof next === "object") next = next.status || next.value;
  return typeof next === "string" && next ? next : "unknown";
}

function statusMeta(value) {
  const raw = typeof value === "string" ? value : value?.state;
  if (LIFECYCLE_META[raw]) return { status: raw, ...LIFECYCLE_META[raw] };
  const status = normalizedStatus(value);
  return { status, ...(STATUS_META[status] || UNKNOWN_STATUS) };
}

export function lifecycleState(pane) { return pane?.lifecycle?.state || ({ needs_input: "blocked" }[normalizedStatus(pane?.status)] || normalizedStatus(pane?.status)); }
function displayLifecycle(pane) { return pane?.lifecycle || { state: lifecycleState(pane), confidence: "low", freshness: "stale", conflicted: false }; }

function kindLabel(value) {
  return KIND_LABEL[value] || "Agent";
}

function rank(pane) {
  const state = lifecycleState(pane);
  const order = { blocked: 0, error: 1, done: 2, working: 3, idle: 4, offline: 5, unknown: 6 };
  return order[state] ?? 99;
}

function targetCompare(a, b) {
  return String(a.target || "").localeCompare(String(b.target || ""), undefined, { numeric: true });
}

function paneCompare(sort = "status") {
  if (sort === "name") return (a, b) => String(a.name || "").localeCompare(String(b.name || "")) || targetCompare(a, b);
  if (sort === "active") return (a, b) => Number(b.updated || 0) - Number(a.updated || 0) || targetCompare(a, b);
  if (sort === "sent") return (a, b) => Number(b.interacted || 0) - Number(a.interacted || 0) || targetCompare(a, b);
  if (sort === "target") return targetCompare;
  return (a, b) => rank(a) - rank(b) || String(a.name || "").localeCompare(String(b.name || "")) || targetCompare(a, b);
}

function sortPanesByPreference(panes, sort = "status") {
  return [...panes].sort((a, b) => Number(Boolean(b.starred)) - Number(Boolean(a.starred)) || paneCompare(sort)(a, b));
}

function sortedPanes(panes, sort = "status") {
  return [...panes].sort((a, b) => {
    const left = STABLE_PANE_POSITIONS.get(a);
    const right = STABLE_PANE_POSITIONS.get(b);
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    if (Number.isFinite(left)) return -1;
    if (Number.isFinite(right)) return 1;
    return Number(Boolean(b.starred)) - Number(Boolean(a.starred)) || paneCompare(sort)(a, b);
  });
}

function sameIDs(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

function hasUrgentTransition(previous, panes) {
  return panes.some((pane) => {
    const before = previous.get(pane.id);
    const after = lifecycleState(pane);
    return (after === "blocked" || after === "error" || after === "done") && before !== after;
  });
}

/**
 * Keep ordinary live sort movement in short fixed windows. Terminal rows still
 * receive fresh pane objects each poll; only their keyed list positions wait.
 */
function useStablePaneOrder(panes, sort, filter) {
  const state = useRef(null);
  const [, redraw] = useState(0);
  const desired = sortPanesByPreference(panes, sort);
  const desiredIDs = desired.map((pane) => pane.id);
  const paneMap = new Map(panes.map((pane) => [pane.id, pane]));
  const statuses = new Map(panes.map((pane) => [pane.id, lifecycleState(pane)]));
  const now = Date.now();

  if (state.current === null) {
    state.current = { ids: desiredIDs, desiredIDs, statuses, sort, filter, until: 0 };
  } else {
    const current = state.current;
    const membershipChanged = current.ids.length !== desiredIDs.length
      || current.ids.some((id) => !paneMap.has(id));
    const immediate = membershipChanged || current.sort !== sort || current.filter !== filter
      || hasUrgentTransition(current.statuses, panes);
    const desiredChanged = !sameIDs(current.desiredIDs, desiredIDs);
    if (immediate) {
      current.ids = desiredIDs;
      current.until = 0;
    } else if (current.until && now >= current.until) {
      current.ids = desiredIDs;
      current.until = 0;
    } else if (desiredChanged && !current.until) {
      current.until = now + ORDER_STABILITY_MS;
    }
    current.desiredIDs = desiredIDs;
    current.statuses = statuses;
    current.sort = sort;
    current.filter = filter;
  }

  const current = state.current;
  useEffect(() => {
    if (!current.until) return undefined;
    const delay = Math.max(0, current.until - Date.now());
    const timer = setTimeout(() => redraw((value) => value + 1), delay);
    return () => clearTimeout(timer);
  }, [current.until, desiredIDs.join(",")]);

  const ordered = current.ids.map((id) => paneMap.get(id)).filter(Boolean);
  ordered.forEach((pane, index) => STABLE_PANE_POSITIONS.set(pane, index));
  return ordered;
}

export function paneMatchesFilter(pane, filter) {
  const status = lifecycleState(pane);
  if (filter === "queue") return pane.attentionSuppressed !== true && (status === "blocked" || status === "error" || status === "done");
  if (filter === "active") return pane.attentionSuppressed !== true && status === "working";
  return true;
}

function attentionPanes(panes) {
  return sortedPanes(panes.filter((pane) => paneMatchesFilter(pane, "queue")), "status");
}

function panesForFilter(panes, filter, sort) {
  if (filter === "queue") return attentionPanes(panes);
  if (filter === "active") return sortedPanes(panes.filter((pane) => paneMatchesFilter(pane, "active")), sort);
  return sortedPanes(panes, sort);
}

function countsFor(panes) {
  return panes.reduce((out, pane) => {
    const status = lifecycleState(pane);
    out[status] = (out[status] || 0) + 1;
    return out;
  }, {});
}

function acknowledgeDirectOpen(panes, id, select, after = null) {
  select(id);
  if (after) after();
  const pane = panes.find((item) => item.id === id);
  if (pane?.lifecycle?.state !== "done" || !pane.lifecycle.revision) return;
  api("/panes/lifecycle/acknowledge", {
    id: pane.id, expected_revision: pane.lifecycle.revision,
  }, "PUT").catch(() => null);
}

function preferredFilter(value) {
  if (value === "needs") return "queue";
  if (value === "working") return "active";
  return FILTERS.includes(value) ? value : "queue";
}

function previewText(pane, lineCount = 5) {
  const lines = pane.preview && pane.preview.length ? pane.preview : (pane.lines || []);
  return lines.slice(-lineCount).join("\n");
}

function actionState(actions, pane) {
  if (!pane || !actions || typeof actions.stateFor !== "function") return null;
  try { return actions.stateFor(pane.id) || null; } catch (_) { return null; }
}

function pending(actions, pane, actionKey) {
  if (!actions || !pane) return false;
  if (typeof actions.isPending === "function") {
    try { if (actions.isPending(pane.id, actionKey)) return true; } catch (_) { /* use state fallback */ }
  }
  const state = actionState(actions, pane);
  if (!state || state.status !== "pending") return false;
  return !actionKey || state.actionKey === actionKey || String(state.actionKey || "").startsWith(`${actionKey}:`);
}

function canAct(actions, connection, pane) {
  if (!pane) return false;
  if (actions && typeof actions.canAct === "function") {
    try { return Boolean(actions.canAct(pane)); } catch (_) { /* use shared predicate */ }
  }
  try { return Boolean(actionsAllowed(connection, pane)); } catch (_) {
    return normalizedStatus(pane.status) !== "offline" && !["offline", "unauthorized", "incompatible"].includes(connection && connection.mode);
  }
}

function reportActionError(error) {
  // The dispatcher owns user-visible state; this prevents an unhandled promise
  // rejection without swallowing the failure during development.
  console.error("vmux pane action failed", error);
}

function connectionMeta(connection) {
  const mode = connection && connection.mode || "connecting";
  const base = { mode, ...(CONNECTION_META[mode] || CONNECTION_META.connecting) };
  if (connection && connection.compatibility && connection.compatibility.status === "unverified"
    && (mode === "live" || mode === "rest" || mode === "updating_rest")) {
    return { ...base, label: `${base.label} · Unverified`, icon: "shield-question" };
  }
  return base;
}

function usageWarnings(usage) {
  const explicit = usage && (usage.warningCount ?? usage.warnings);
  if (Array.isArray(explicit)) return explicit.length;
  if (Number.isFinite(Number(explicit))) return Number(explicit);
  const snapshot = usage && (usage.snapshot || usage.data || usage);
  const quotas = snapshot && Array.isArray(snapshot.quotas) ? snapshot.quotas : [];
  return quotas.filter((quota) => quota.warning || quota.is_warning || Number(quota.remaining_percent) <= Number(quota.warning_threshold ?? -1)).length;
}

function StatusBadge({ value, compact = false }) {
  const meta = statusMeta(value);
  return html`<span class=${cx("status-badge", `status-${meta.status}`, `tone-${meta.tone || "neutral"}`)}>
    <${Icon} name=${meta.icon || "circle-help"} size=${compact ? 14 : 16} />
    ${compact ? html`<span class="sr-only">${meta.label}</span>` : html`<span>${meta.shortLabel || meta.label}</span>`}
  </span>`;
}

function ConnectionBadge({ connection, onClick }) {
  const meta = connectionMeta(connection);
  const content = html`<${Fragment}>
    <${Icon} name=${meta.icon} size=${16} />
    <span>${meta.label}</span>
  <//>`;
  return onClick ? html`<button
    type="button"
    class=${cx("connection-badge", `connection-${meta.mode}`, `tone-${meta.tone}`)}
    onClick=${onClick}
    aria-label=${`Connection: ${meta.label}. Show details`}
  >${content}</button>` : html`<span class=${cx("connection-badge", `connection-${meta.mode}`, `tone-${meta.tone}`)}>${content}</span>`;
}

function PaneActionFeedback({ actions, pane }) {
  const state = actionState(actions, pane);
  if (!state || !state.status || state.status === "idle") return html`<div class="action-feedback-placeholder" aria-live="polite"></div>`;
  const message = state.message || (state.status === "pending" ? "Sending…" : state.status === "success" ? "Sent" : "Action failed");
  return html`<div
    class=${cx("action-feedback", `action-${state.status}`)}
    role=${state.status === "error" ? "alert" : "status"}
    aria-live="polite"
  >
    <${Icon} name=${state.status === "pending" ? "loader-circle" : state.status === "success" ? "circle-check" : "circle-alert"} size=${15} />
    <span>${message}</span>
  </div>`;
}

function StarButton({ pane, actions, connection }) {
  const disabled = !canAct(actions, connection, pane) || pending(actions, pane, "star");
  const label = pane.starred ? "Unstar pane" : "Star pane";
  const click = async () => {
    try { await actions.star(pane); } catch (error) { reportActionError(error); }
  };
  return html`<button
    type="button"
    class=${cx("star-button", pane.starred && "selected")}
    aria-label=${label}
    aria-pressed=${Boolean(pane.starred)}
    title=${label}
    disabled=${disabled}
    onClick=${click}
  ><${Icon} name="star" size=${18} /></button>`;
}

function SearchField({
  value,
  onChange,
  placeholder = "Search panes",
  autoFocus = false,
  combobox = false,
  controls = null,
  activeDescendant = null,
}) {
  return html`<label class="search-field">
    <span class="sr-only">${placeholder}</span>
    <${Icon} name="search" size=${17} />
    <input
      type="search"
      value=${value}
      placeholder=${placeholder}
      autocomplete="off"
      autoFocus=${autoFocus}
      role=${combobox ? "combobox" : undefined}
      aria-autocomplete=${combobox ? "list" : undefined}
      aria-expanded=${combobox ? true : undefined}
      aria-controls=${controls || undefined}
      aria-activedescendant=${activeDescendant || undefined}
      onInput=${(event) => onChange(event.target.value)}
    />
  </label>`;
}

function SortSelect({ value, onChange }) {
  return html`<label class="sort-control">
    <span>Sort</span>
    <select value=${value} onChange=${(event) => onChange(event.target.value)}>
      ${SORTS.map(([key, label]) => html`<option key=${key} value=${key}>${label}</option>`)}
    </select>
  </label>`;
}

function ReplyComposer({ pane, actions, connection, compact = false, autoFocus = false, onSent = null }) {
  const prefs = usePrefs();
  const [text, setText] = useState("");
  const [snippetOpen, setSnippetOpen] = useState(false);
  const input = useRef(null);
  const snippets = prefs.snippets || DEFAULT_SNIPPETS;
  const allowed = canAct(actions, connection, pane);
  const textBusy = pending(actions, pane, "text");
  const imageUpload = useImageUpload({
    enabled: allowed && !textBusy,
    onInsert: (terminalText) => setText((current) => appendTerminalText(current, terminalText)),
    onFocus: () => input.current?.focus(),
  });

  useEffect(() => {
    if (autoFocus) requestAnimationFrame(() => input.current && input.current.focus());
  }, [autoFocus]);

  useEffect(() => {
    const focusComposer = (event) => {
      if (event.detail?.id !== pane.id) return;
      requestAnimationFrame(() => input.current && input.current.focus());
    };
    globalThis.addEventListener?.("vmux:focus-composer", focusComposer);
    return () => globalThis.removeEventListener?.("vmux:focus-composer", focusComposer);
  }, [pane.id]);

  useEffect(() => {
    if (!snippetOpen) return undefined;
    const close = (event) => {
      if (event.key === "Escape" || !(event.target && event.target.closest && event.target.closest(".snippet-picker"))) setSnippetOpen(false);
    };
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", close);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("pointerdown", close);
    };
  }, [snippetOpen]);

  const send = async (enter) => {
    if ((!text && !enter) || !allowed || textBusy || imageUpload.busy) return;
    try {
      await actions.text(pane, text, enter);
      setText("");
      if (onSent) onSent();
    } catch (error) { reportActionError(error); }
  };

  return html`<div class=${cx("reply-composer", compact && "compact")}>
    ${compact ? null : html`<div class="snippet-picker">
      <button
        type="button"
        class="icon-button"
        aria-label="Insert snippet"
        aria-expanded=${snippetOpen}
        disabled=${!allowed}
        onClick=${() => setSnippetOpen((open) => !open)}
      ><${Icon} name="notebook-tabs" size=${18} /></button>
      ${snippetOpen ? html`<div class="snippet-menu glass" role="menu">
        ${snippets.length ? snippets.map((snippet, index) => html`<button
          type="button"
          role="menuitem"
          key=${`${snippet}-${index}`}
          onClick=${() => { setText((current) => current ? `${current}${snippet}` : snippet); setSnippetOpen(false); input.current && input.current.focus(); }}
        >${snippet}</button>`) : html`<p>No snippets. Add them in Settings.</p>`}
      </div>` : null}
    </div>`}
    <${ImageUploadButton} upload=${imageUpload} />
    <label class="reply-field">
      <span class="sr-only">Message for ${pane.name || "pane"}</span>
      <input
        ref=${input}
        type="text"
        value=${text}
        placeholder="Type a reply…"
        autocomplete="off"
        disabled=${!allowed}
        onInput=${(event) => setText(event.target.value)}
        onPaste=${imageUpload.onPaste}
        onKeyDown=${(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send(true);
          }
        }}
      />
    </label>
    <button type="button" class="button secondary" disabled=${!allowed || textBusy || imageUpload.busy || !text} onClick=${() => send(false)}>Send</button>
    <button type="button" class="button primary icon-only" disabled=${!allowed || textBusy || imageUpload.busy} onClick=${() => send(true)}>
      <${Icon} name="corner-down-left" size=${18} />
    </button>
    <${ImageUploadStatus} upload=${imageUpload} />
  </div>`;
}

function PromptActions({ pane, actions, connection, limit = null, onFreeform = null }) {
  const options = Array.isArray(pane.menu) ? pane.menu : [];
  const shown = limit == null ? options : options.slice(0, limit);
  const allowed = canAct(actions, connection, pane);
  const choose = async (option) => {
    const actionKey = `select:${option.key}`;
    if (!allowed || pending(actions, pane, actionKey)) return;
    if (option.freeform && onFreeform) onFreeform();
    try {
      if (option.freeform && typeof actions.selectThenCompose === "function") await actions.selectThenCompose(pane, option.key);
      else await actions.select(pane, option.key);
    } catch (error) { reportActionError(error); }
  };
  if (!shown.length) return null;
  return html`<div class="prompt-actions" aria-label="Quick answers">
    ${shown.map((option) => html`<button
      type="button"
      key=${option.key}
      class=${cx("quick-answer", option.selected && "selected", option.freeform && "freeform")}
      aria-pressed=${Boolean(option.selected)}
      disabled=${!allowed || pending(actions, pane, `select:${option.key}`)}
      onClick=${() => choose(option)}
    >
      <span class="answer-key">${option.key}</span>
      <span>${String(option.label || `Option ${option.key}`).replace(/\s*\(\s*recommended\s*\)\s*$/i, "")}</span>
      ${option.freeform ? html`<${Icon} name="pencil-line" size=${15} />` : null}
    </button>`)}
  </div>`;
}

function PaneActionCard({ pane, actions, connection }) {
  if (normalizedStatus(pane.status) !== "needs_input") return null;
  const options = Array.isArray(pane.menu) ? pane.menu : [];
  const allowed = canAct(actions, connection, pane);
  const question = pane.question || (options.length ? "Choose an option to unblock this agent." : "Waiting for a reply.");
  const choose = async (option) => {
    const actionKey = `select:${option.key}`;
    if (!allowed || pending(actions, pane, actionKey)) return;
    try {
      if (option.freeform && typeof actions.selectThenCompose === "function") await actions.selectThenCompose(pane, option.key);
      else await actions.select(pane, option.key);
    } catch (error) { reportActionError(error); }
  };
  return html`<section class="pane-action-card" aria-label="Action required">
    <p class="pane-action-question">${question}</p>
    ${options.length ? html`<div class="pane-action-options" aria-label="Answers">
      ${options.map((option) => {
        const recommended = /\(\s*recommended\s*\)/i.test(option.label || "");
        const label = String(option.label || `Option ${option.key}`).replace(/\s*\(\s*recommended\s*\)\s*$/i, "");
        return html`<button
          type="button"
          key=${option.key}
          class=${cx("pane-action-option", option.selected && "selected", recommended && "recommended", option.freeform && "freeform")}
          aria-pressed=${Boolean(option.selected)}
          disabled=${!allowed || pending(actions, pane, `select:${option.key}`)}
          onClick=${() => choose(option)}
        >
          <span class="pane-action-key">${option.key}</span>
          <span class="pane-action-copy">
            <strong>${label}</strong>
            ${option.description ? html`<small>${option.description}</small>` : null}
          </span>
          <span class="pane-action-flags">
            ${recommended ? html`<em>Recommended</em>` : null}
            ${option.selected ? html`<em>Current</em>` : null}
            ${option.freeform ? html`<span class="freeform-affordance"><${Icon} name="pencil-line" size=${15} /> Add notes</span>` : null}
          </span>
        </button>`;
      })}
    </div>` : html`<p class="pane-action-reply-hint"><${Icon} name="reply" size=${16} /> Reply in the composer below.</p>`}
  </section>`;
}

function AttentionCard({ pane, selected, onOpen, actions, connection }) {
  const lifecycle = displayLifecycle(pane);
  const meta = statusMeta(lifecycle);
  const needsReply = meta.status === "blocked";
  const [replyOpen, setReplyOpen] = useState(false);
  const question = needsReply
    ? pane.question || (pane.menu && pane.menu.length ? "Choose an option to unblock this agent." : "Waiting for a reply.")
    : meta.status === "done" ? "Work completed. Opening this pane acknowledges completion." : "Recent output matched an error pattern.";
  const more = needsReply && Array.isArray(pane.menu) && pane.menu.length > 3;

  useEffect(() => { setReplyOpen(false); }, [pane.id]);

  return html`<article class=${cx("attention-card", `status-${meta.status}`, selected && "selected")}>
    <header class="attention-card-head">
      <button type="button" class="attention-open" onClick=${() => onOpen(pane.id)}>
        <span class=${cx("status-dot", `status-${meta.status}`)} aria-hidden="true"></span>
        <span class="attention-heading">
          <strong>${pane.name || pane.target || "Unnamed pane"}</strong>
          <span>${pane.target || "Unknown target"} · ${kindLabel(pane.kind)} · ${lifecycle.confidence}/${lifecycle.freshness}${lifecycle.conflicted ? " · conflict" : ""} · ${formatAge(Math.max(pane.updated || 0, pane.interacted || 0))}</span>
        </span>
        <span class="sr-only">Open pane detail</span>
      </button>
      <${StarButton} pane=${pane} actions=${actions} connection=${connection} />
      <${StatusBadge} value=${lifecycle} compact=${true} />
    </header>
    <p class="attention-question">${question}</p>
    ${needsReply ? html`<${PromptActions}
      pane=${pane}
      actions=${actions}
      connection=${connection}
      limit=${3}
      onFreeform=${() => setReplyOpen(true)}
    />` : previewText(pane) ? html`<pre class="attention-preview">${previewText(pane)}</pre>` : null}
    <footer class="attention-card-foot">
      ${needsReply ? html`<button type="button" class="button secondary" aria-expanded=${replyOpen} onClick=${() => setReplyOpen((open) => !open)}>
        <${Icon} name="reply" size=${16} /> Reply
      </button>` : null}
      ${more ? html`<button type="button" class="button quiet" onClick=${() => onOpen(pane.id)}>More options</button>` : null}
      <span class="spacer"></span>
      <button type="button" class="button quiet" onClick=${() => onOpen(pane.id)}>Inspect</button>
    </footer>
    ${replyOpen ? html`<${ReplyComposer} pane=${pane} actions=${actions} connection=${connection} compact=${true} autoFocus=${true} />` : null}
    <${PaneActionFeedback} actions=${actions} pane=${pane} />
  </article>`;
}

function PaneRow({ pane, selected, onOpen, actions, connection, onCreate = null }) {
  const lifecycle = displayLifecycle(pane);
  const meta = statusMeta(lifecycle);
  return html`<div class=${cx("pane-row", selected && "selected", `status-${meta.status}`)}>
    <button type="button" class="pane-row-open" onClick=${() => onOpen(pane.id)}>
      <span class=${cx("status-dot", `status-${meta.status}`)} aria-hidden="true"></span>
      <span class="pane-row-copy">
        <strong>${pane.name || pane.target || "Unnamed pane"}</strong>
        <span>${pane.target || "Unknown target"} · ${kindLabel(pane.kind)} · ${lifecycle.confidence}/${lifecycle.freshness}${lifecycle.conflicted ? " · conflict" : ""}</span>
      </span>
      <${StatusBadge} value=${lifecycle} compact=${true} />
    </button>
    ${onCreate ? html`<button type="button" class="icon-button tree-create" aria-label=${`Split ${pane.name || pane.target}`} title="Split pane" onClick=${() => onCreate({ type: "pane", parentPaneID: pane.id })}><${Icon} name="plus" size=${16} /></button>` : null}
    <${StarButton} pane=${pane} actions=${actions} connection=${connection} />
  </div>`;
}

function splitTarget(pane) {
  const target = String(pane.target || "");
  const colon = target.lastIndexOf(":");
  const session = String(pane.session || (colon >= 0 ? target.slice(0, colon) : target) || "Session");
  const tail = colon >= 0 ? target.slice(colon + 1) : "0.0";
  const dot = tail.indexOf(".");
  const windowId = String((pane.window_index ?? pane.window_id ?? (dot >= 0 ? tail.slice(0, dot) : tail)) || "0");
  const paneIndex = Number.parseInt(dot >= 0 ? tail.slice(dot + 1) : "0", 10) || 0;
  return { session, windowId, paneIndex };
}

function worstStatus(panes) {
  if (!panes.length) return "unknown";
  return panes.reduce((best, pane) => rank(pane) < rank({ lifecycle: { state: best } }) ? lifecycleState(pane) : best, "unknown");
}

function buildTree(panes) {
  const sessions = new Map();
  panes.forEach((pane) => {
    const parts = splitTarget(pane);
    if (!sessions.has(parts.session)) sessions.set(parts.session, new Map());
    const windows = sessions.get(parts.session);
    if (!windows.has(parts.windowId)) windows.set(parts.windowId, []);
    windows.get(parts.windowId).push({ ...pane, _paneIndex: parts.paneIndex });
  });
  return [...sessions.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([session, windows]) => {
      const items = [...windows.entries()]
        .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
        .map(([windowId, windowPanes]) => {
          windowPanes.sort((a, b) => a._paneIndex - b._paneIndex);
          return {
            id: windowId,
            name: windowPanes[0].window || `Window ${windowId}`,
            panes: windowPanes,
            status: worstStatus(windowPanes),
          };
        });
      const all = items.flatMap((item) => item.panes);
      return { id: session, windows: items, panes: all, status: worstStatus(all) };
    });
}

function selectedAncestorKeys(panes, selectedId) {
  const pane = panes.find((item) => item.id === selectedId);
  if (!pane) return [];
  const parts = splitTarget(pane);
  return [`session:${parts.session}`, `window:${parts.session}:${parts.windowId}`];
}

function TreeView({ panes, selectedId, onOpen, actions, connection, onCreate = null }) {
  const tree = useMemo(() => buildTree(panes), [panes]);
  const [expanded, setExpanded] = useState(() => new Set(SESSION_TREE_EXPANDED));

  useEffect(() => {
    const ancestors = selectedAncestorKeys(panes, selectedId);
    if (!ancestors.length) return;
    setExpanded((current) => {
      if (ancestors.every((key) => current.has(key))) return current;
      const next = new Set(current);
      ancestors.forEach((key) => { next.add(key); SESSION_TREE_EXPANDED.add(key); });
      return next;
    });
  }, [panes, selectedId]);

  const toggle = (key) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(key)) {
      next.delete(key);
      SESSION_TREE_EXPANDED.delete(key);
    } else {
      next.add(key);
      SESSION_TREE_EXPANDED.add(key);
    }
    return next;
  });

  if (!tree.length) return html`<${EmptyState} icon="network" title="No panes" detail="Discovered sessions will appear here." />`;
  return html`<div class="pane-tree">
    ${tree.map((session) => {
      const sessionKey = `session:${session.id}`;
      const sessionOpen = expanded.has(sessionKey);
      return html`<div class="tree-session" key=${session.id}>
        <div class="tree-parent-row"><button type="button" class=${cx("tree-node", "tree-parent", `status-${session.status}`)} aria-expanded=${sessionOpen} onClick=${() => toggle(sessionKey)}>
            <${Icon} name=${sessionOpen ? "chevron-down" : "chevron-right"} size=${16} />
            <${StatusBadge} value=${session.status} compact=${true} />
            <strong>${session.id}</strong>
            <span class="tree-count">${session.panes.length}</span>
          </button>
          ${onCreate ? html`<button type="button" class="icon-button tree-create" aria-label=${`New window in ${session.id}`} title="New window" onClick=${() => onCreate({ type: "window", parentSession: session.id })}><${Icon} name="plus" size=${16} /></button>` : null}
        </div>
        ${sessionOpen ? html`<div class="tree-children">
          ${session.windows.map((windowItem) => {
            const windowKey = `window:${session.id}:${windowItem.id}`;
            const windowOpen = expanded.has(windowKey);
            return html`<div class="tree-window" key=${windowItem.id}>
              <div class="tree-parent-row"><button type="button" class=${cx("tree-node", "tree-parent", `status-${windowItem.status}`)} aria-expanded=${windowOpen} onClick=${() => toggle(windowKey)}>
                  <${Icon} name=${windowOpen ? "chevron-down" : "chevron-right"} size=${16} />
                  <${StatusBadge} value=${windowItem.status} compact=${true} />
                  <span>${windowItem.name}</span>
                  <span class="tree-count">${windowItem.panes.length}</span>
                </button>
                ${onCreate && windowItem.panes[0] ? html`<button type="button" class="icon-button tree-create" aria-label=${`Split ${windowItem.name}`} title="Split pane" onClick=${() => onCreate({ type: "pane", parentPaneID: windowItem.panes[0].id })}><${Icon} name="plus" size=${16} /></button>` : null}
              </div>
              ${windowOpen ? html`<div class="tree-children">
                ${windowItem.panes.map((pane) => html`<${PaneRow}
                  key=${pane.id}
                  pane=${pane}
                  selected=${pane.id === selectedId}
                  onOpen=${onOpen}
                  actions=${actions}
                  connection=${connection}
                  onCreate=${onCreate}
                />`)}
              </div>` : null}
            </div>`;
          })}
        </div>` : null}
      </div>`;
    })}
  </div>`;
}

function extractLinks(lines) {
  const text = (lines || []).join("\n");
  const expression = /\bhttps?:\/\/[^\s<>"')\]]+/g;
  const seen = new Set();
  const links = [];
  let match;
  while ((match = expression.exec(text))) {
    const url = match[0].replace(/[.,;:!?)\]}>'"]+$/, "");
    if (url && !seen.has(url)) { seen.add(url); links.push(url); }
  }
  return links;
}

function LinksPanel({ links }) {
  const [message, setMessage] = useState("");
  const copy = async (url) => {
    try {
      await navigator.clipboard.writeText(url);
      setMessage("Link copied");
    } catch (_) { setMessage("Could not copy link"); }
  };
  const open = (url) => window.open(url, "_blank", "noopener,noreferrer");
  return html`<div class="links-panel">
    <div class="links-list">
      ${links.map((url) => html`<div class="link-row" key=${url}>
        <a href=${url} target="_blank" rel="noopener noreferrer">${url}</a>
        <button type="button" class="icon-button" aria-label="Open link" onClick=${() => open(url)}><${Icon} name="external-link" size=${16} /></button>
        <button type="button" class="icon-button" aria-label="Copy link" onClick=${() => copy(url)}><${Icon} name="copy" size=${16} /></button>
      </div>`)}
    </div>
    <div class="sr-status" aria-live="polite">${message}</div>
  </div>`;
}

function Terminal({ pane, actions, connection }) {
  const prefs = usePrefs();
  const [wrap, setWrap] = useState(Boolean(prefs.terminalWrap ?? prefs.wrapTerminal ?? false));
  const [fullScreen, setFullScreen] = useState(false);
  const [linksOpen, setLinksOpen] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const outputRef = useRef(null);
  const following = useRef(true);
  const previousOutput = useRef("");
  const output = (pane.lines || []).join("\n") || "(no output)";
  const links = useMemo(() => extractLinks(pane.lines), [pane.lines]);
  const shortcuts = prefs.actions || DEFAULT_SHORTCUTS;
  const allowed = canAct(actions, connection, pane);

  const scrollLatest = useCallback(() => {
    const node = outputRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    following.current = true;
    setHasNew(false);
  }, []);
  const closeFullScreen = useCallback(() => setFullScreen(false), []);

  useEffect(() => {
    following.current = true;
    previousOutput.current = output;
    setHasNew(false);
    setLinksOpen(false);
    requestAnimationFrame(scrollLatest);
  }, [pane.id]);

  useEffect(() => {
    if (output === previousOutput.current) return;
    previousOutput.current = output;
    if (following.current) requestAnimationFrame(scrollLatest);
    else setHasNew(true);
  }, [output, scrollLatest]);

  useEffect(() => {
    if (following.current) requestAnimationFrame(scrollLatest);
  }, [wrap, fullScreen, scrollLatest]);

  const onScroll = () => {
    const node = outputRef.current;
    if (!node) return;
    const bottom = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
    following.current = bottom;
    if (bottom) setHasNew(false);
  };
  const chooseWrap = (next) => {
    setWrap(next);
    setPrefs({ terminalWrap: next });
  };
  const toggleFullScreen = () => {
    if (!fullScreen && pane?.lifecycle?.state === "done" && pane.lifecycle.revision) {
      api("/panes/lifecycle/acknowledge", {
        id: pane.id, expected_revision: pane.lifecycle.revision,
      }, "PUT").catch(() => null);
    }
    setFullScreen((open) => !open);
  };
  const sendKey = async (key) => {
    if (!allowed || pending(actions, pane, `key:${key}`)) return;
    try { await actions.key(pane, key); } catch (error) { reportActionError(error); }
  };

  const surface = html`<section class=${cx("terminal-panel", fullScreen && "terminal-fullscreen")} aria-label=${`Terminal output for ${pane.name || pane.target}`}>
    <div class="terminal-toolbar">
      <div class="terminal-view-toggle" role="group" aria-label="Terminal wrapping">
        <button type="button" aria-pressed=${!wrap} class=${!wrap ? "selected" : ""} onClick=${() => chooseWrap(false)}>Faithful</button>
        <button type="button" aria-pressed=${wrap} class=${wrap ? "selected" : ""} onClick=${() => chooseWrap(true)}>Wrap</button>
      </div>
      <span class="spacer"></span>
      ${links.length ? html`<button type="button" class="button quiet" aria-label=${`${linksOpen ? "Hide" : "Show"} ${links.length} extracted link${links.length === 1 ? "" : "s"}`} aria-expanded=${linksOpen} onClick=${() => setLinksOpen((open) => !open)}>
        <${Icon} name="link" size=${16} /> ${links.length}
      </button>` : null}
      <button type="button" class="icon-button" aria-label=${fullScreen ? "Exit full screen terminal" : "Open full screen terminal"} onClick=${toggleFullScreen}>
        <${Icon} name=${fullScreen ? "minimize-2" : "maximize-2"} size=${18} />
      </button>
    </div>
    ${linksOpen && links.length ? html`<${LinksPanel} links=${links} />` : null}
    <div class="terminal-output-wrap">
      <pre ref=${outputRef} class=${cx("terminal-output", wrap ? "wrap" : "no-wrap")} tabindex="0" onScroll=${onScroll}>${output}</pre>
      ${hasNew ? html`<button type="button" class="latest-button" onClick=${scrollLatest}>
        <${Icon} name="arrow-down-to-line" size=${16} /> Latest
      </button>` : null}
    </div>
    <div class="terminal-shortcuts" aria-label="Terminal shortcuts">
      ${shortcuts.map(([label, key], index) => html`<button
        type="button"
        class="key-button"
        key=${`${key}-${index}`}
        disabled=${!allowed || pending(actions, pane, `key:${key}`)}
        onClick=${() => sendKey(key)}
      >${label}</button>`)}
    </div>
    <${ReplyComposer} pane=${pane} actions=${actions} connection=${connection} />
    <${PaneActionFeedback} actions=${actions} pane=${pane} />
  </section>`;

  return fullScreen ? html`<${Dialog}
    title=${pane.name || "Terminal"}
    subtitle=${pane.target || ""}
    onClose=${closeFullScreen}
    className="terminal-dialog"
  >${surface}<//>` : surface;
}

function PaneDetail({ pane, actions, connection }) {
  if (!pane) return html`<${EmptyState} icon="panel-right" title="Select a pane" detail="Choose a pane from the queue or navigator to inspect it." />`;
  const lifecycle = displayLifecycle(pane);
  const meta = statusMeta(lifecycle);
  return html`<article class="pane-detail">
    <header class="pane-detail-head">
      <div>
        <div class="pane-detail-title-row">
          <span class=${cx("status-dot", `status-${meta.status}`)} aria-hidden="true"></span>
          <h1>${pane.name || pane.target || "Unnamed pane"}</h1>
        </div>
        <p>${pane.target || "Unknown target"} · ${kindLabel(pane.kind)}</p>
      </div>
      <span class="spacer"></span>
      <${StatusBadge} value=${lifecycle} />
      <${StarButton} pane=${pane} actions=${actions} connection=${connection} />
    </header>
    <div class="pane-detail-scroll">
      <${PaneActionCard} pane=${pane} actions=${actions} connection=${connection} />
      <dl class="pane-facts">
        <div><dt>Lifecycle</dt><dd>${meta.label} · ${lifecycle.confidence} confidence · ${lifecycle.freshness}${lifecycle.conflicted ? " · conflict" : ""}</dd></div>
        <div><dt>Pane</dt><dd>${pane.target || "Unknown"}</dd></div>
        <div><dt>Updated</dt><dd>${formatAge(pane.updated)}</dd></div>
        <div><dt>Last sent</dt><dd>${formatAge(pane.interacted)}</dd></div>
      </dl>
      <${Terminal} pane=${pane} actions=${actions} connection=${connection} />
    </div>
  </article>`;
}

function FilterTabs({ value, onChange, counts, usageWarning = 0, includeStats = true }) {
  const queueCount = Number(counts.blocked || 0) + Number(counts.error || 0) + Number(counts.done || 0);
  const options = [
    ["queue", "Queue", queueCount],
    ["active", "Active", counts.working || 0],
    ["all", "All", counts.total || 0],
  ];
  if (includeStats) options.push(["stats", "Stats", usageWarning]);
  return html`<${Segmented}
    value=${value}
    onChange=${onChange}
    label="Pane filter"
    options=${options}
  />`;
}

function ModeEmpty({ mode, connected }) {
  if (!connected) return html`<${EmptyState} icon="wifi-off" title="Waiting for vmux" detail="The last snapshot remains available while the connection recovers." />`;
  if (mode === "queue") return html`<${EmptyState} icon="circle-check" title="Queue is clear" detail="No panes currently need your attention." />`;
  if (mode === "active") return html`<${EmptyState} icon="loader-circle" title="No active panes" detail="Working agents will appear here." />`;
  return html`<${EmptyState} icon="panels-top-left" title="No panes discovered" detail="Start or discover a tmux pane to populate the workspace." />`;
}

function PaneList({ panes, mode, selectedId, onOpen, actions, connection }) {
  if (!panes.length) return html`<${ModeEmpty} mode=${mode} connected=${connection && connection.mode !== "offline"} />`;
  if (mode === "queue") return html`<div class="attention-list">
    ${panes.map((pane) => html`<${AttentionCard}
      key=${pane.id}
      pane=${pane}
      selected=${pane.id === selectedId}
      onOpen=${onOpen}
      actions=${actions}
      connection=${connection}
    />`)}
  </div>`;
  return html`<div class="pane-list">
    ${panes.map((pane) => html`<${PaneRow}
      key=${pane.id}
      pane=${pane}
      selected=${pane.id === selectedId}
      onOpen=${onOpen}
      actions=${actions}
      connection=${connection}
    />`)}
  </div>`;
}

function AppActions({ onBroadcast, onSettings, onCreate = null }) {
  return html`<div class="app-actions">
    ${onCreate ? html`<button type="button" class="icon-button" aria-label="Create tmux target" title="Create" onClick=${() => onCreate({})}>
      <${Icon} name="plus" />
    </button>` : null}
    <button type="button" class="icon-button" title="Broadcast" onClick=${onBroadcast}>
      <${Icon} name="radio-tower" />
    </button>
    <button type="button" class="icon-button" title="Settings" onClick=${onSettings}>
      <${Icon} name="settings" />
    </button>
  </div>`;
}

function BrandHeader({ connection, onConnection, onBroadcast, onSettings, onCreate = null, compact = false }) {
  return html`<header class=${cx("brand-header", compact && "compact")}>
    <div class="brand-lockup"><span>v</span><strong>mux</strong></div>
    <span class="spacer"></span>
    <${ConnectionBadge} connection=${connection} onClick=${onConnection} />
    <${AppActions} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} />
  </header>`;
}

/** Generic workspace destinations supplied by the capability-gated agent UI. */
export function WorkspaceNav({ navigation, layout = "wide" }) {
  if (!navigation || !Array.isArray(navigation.items)) return null;
  return html`<nav class=${cx("workspace-destinations", `workspace-destinations-${layout}`, layout === "compact" && "glass")} aria-label="Workspace">
    ${navigation.items.map(([key, icon, label]) => {
      const badge = Number(navigation.badges?.[key] || 0);
      return html`<button
        type="button"
        key=${key}
        class=${navigation.current === key ? "selected" : ""}
        aria-current=${navigation.current === key ? "page" : null}
        onClick=${() => navigation.onNavigate(key)}
      ><${Icon} name=${icon} size=${layout === "compact" ? 20 : 18} /><span>${label}</span>${badge ? html`<b>${badge}</b>` : null}</button>`;
    })}
  </nav>`;
}

function UsageView({ usage, layout }) {
  return html`<section class="usage-destination" aria-label="Usage statistics">
    <${UsageDashboard} state=${usage} usage=${usage} layout=${layout} />
  </section>`;
}

function CommandPalette({ panes, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const matches = useMemo(() => sortedPanes(panes.filter((pane) => {
    const haystack = `${pane.name || ""} ${pane.target || ""} ${kindLabel(pane.kind)}`.toLowerCase();
    return !query || haystack.includes(query.toLowerCase());
  }), "status").slice(0, 10), [panes, query]);
  useEffect(() => { setIndex(0); }, [query]);
  const close = useCallback(() => onClose(), [onClose]);
  return html`<${Dialog} title="Jump to pane" subtitle="Search the current swarm" onClose=${close} className="command-palette">
    <div class="palette-content" onKeyDown=${(event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); setIndex((value) => Math.min(matches.length - 1, value + 1)); }
      else if (event.key === "ArrowUp") { event.preventDefault(); setIndex((value) => Math.max(0, value - 1)); }
      else if (event.key === "Enter" && matches[index]) { event.preventDefault(); onPick(matches[index].id); }
    }}>
      <${SearchField}
        value=${query}
        onChange=${setQuery}
        placeholder="Agent, target, or kind"
        autoFocus=${true}
        combobox=${true}
        controls="palette-results"
        activeDescendant=${matches[index] ? `palette-option-${index}` : null}
      />
      <div id="palette-results" class="palette-results" role="listbox" aria-label="Matching panes">
        ${matches.map((pane, itemIndex) => html`<button
          id=${`palette-option-${itemIndex}`}
          type="button"
          role="option"
          aria-selected=${itemIndex === index}
          class=${itemIndex === index ? "selected" : ""}
          key=${pane.id}
          onMouseEnter=${() => setIndex(itemIndex)}
          onClick=${() => onPick(pane.id)}
        >
          <${StatusBadge} value=${displayLifecycle(pane)} compact=${true} />
          <span><strong>${pane.name || pane.target}</strong><small>${pane.target} · ${kindLabel(pane.kind)}</small></span>
        </button>`)}
        ${matches.length ? null : html`<${EmptyState} icon="search-x" title="No matches" detail="Try a pane target or agent name." />`}
      </div>
    </div>
    <p class="palette-help">Use ↑ and ↓ to navigate, Enter to open, Escape to close.</p>
  <//>`;
}

function CompactShell({ panes, connection, actions, usage, selectedId, setSelectedId, filter, setFilter, onBroadcast, onSettings, onCreate, onConnection, workspaceNav, openPaneId }) {
  const prefs = usePrefs();
  const [treeOpen, setTreeOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [query, setQuery] = useState("");
  const counts = countsFor(panes);
  counts.total = panes.length;
  const warningCount = usageWarnings(usage);
  const searched = useMemo(() => panes.filter((pane) => !query || `${pane.name || ""} ${pane.target || ""} ${kindLabel(pane.kind)}`.toLowerCase().includes(query.toLowerCase())), [panes, query]);
  const visible = useMemo(() => panesForFilter(searched, filter, prefs.sort), [searched, filter, prefs.sort]);
  const selected = panes.find((pane) => pane.id === selectedId) || null;
  const openPane = (id) => acknowledgeDirectOpen(panes, id, setSelectedId, () => setDetailOpen(true));
  useEffect(() => {
    if (!workspaceNav?.paneId || !panes.some((pane) => pane.id === workspaceNav.paneId)) return;
    setSelectedId(workspaceNav.paneId);
    setDetailOpen(true);
  }, [panes, workspaceNav?.paneId]);
  useEffect(() => {
    if (!openPaneId || !panes.some((pane) => pane.id === openPaneId)) return;
    setSelectedId(openPaneId);
    setDetailOpen(true);
  }, [openPaneId, panes, setSelectedId]);

  return html`<div class="app-shell compact-shell">
    <${BrandHeader} compact=${true} connection=${connection} onConnection=${onConnection} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} />
    <main class="compact-main">
      ${filter === "stats" ? html`<${UsageView} usage=${usage} layout="compact" />` : html`<${Fragment}>
        <header class="destination-head">
          <div><p>${filter === "queue" ? "Attention first" : filter === "active" ? "In progress" : "Entire swarm"}</p><h1>${filter === "queue" ? "Queue" : filter === "active" ? "Active" : "All panes"}</h1></div>
          ${filter === "all" ? html`<button type="button" class="button secondary" onClick=${() => setTreeOpen(true)}><${Icon} name="network" size=${17} /> Tree</button>` : null}
        </header>
        ${workspaceNav ? html`<div class="pane-subnav"><${FilterTabs} value=${filter} onChange=${setFilter} counts=${counts} includeStats=${false} /></div>` : null}
        <${SearchField} value=${query} onChange=${setQuery} placeholder="Search panes" />
        ${filter === "all" ? html`<${SortSelect} value=${prefs.sort} onChange=${(sort) => setPrefs({ sort })} />` : null}
        <${PaneList} panes=${visible} mode=${filter} selectedId=${selectedId} onOpen=${openPane} actions=${actions} connection=${connection} />
      <//>`}
    </main>
    ${workspaceNav ? html`<${WorkspaceNav} navigation=${workspaceNav} layout="compact" />` : html`<nav class="compact-dock glass" aria-label="Primary">
      ${[
        ["queue", "inbox", "Queue", Number(counts.blocked || 0) + Number(counts.error || 0) + Number(counts.done || 0)],
        ["active", "loader-circle", "Active", counts.working || 0],
        ["all", "panels-top-left", "All", panes.length],
        ["stats", "chart-no-axes-combined", "Stats", warningCount],
      ].map(([key, icon, label, badge]) => html`<button
        type="button"
        key=${key}
        class=${filter === key ? "selected" : ""}
        aria-current=${filter === key ? "page" : null}
        onClick=${() => setFilter(key)}
      ><${Icon} name=${icon} size=${20} /><span>${label}</span>${badge ? html`<b>${badge}</b>` : null}</button>`)}
    </nav>`}
    ${treeOpen ? html`<${Dialog} title="Swarm tree" subtitle=${`${panes.length} panes`} onClose=${() => setTreeOpen(false)} className="focus-sheet compact-sheet">
      <${TreeView} panes=${panes} selectedId=${selectedId} onOpen=${(id) => acknowledgeDirectOpen(panes, id, setSelectedId, () => { setTreeOpen(false); setDetailOpen(true); })} actions=${actions} connection=${connection} onCreate=${onCreate} />
    <//>` : null}
    ${detailOpen && selected ? html`<${Dialog} title=${selected.name || selected.target} subtitle=${`${selected.target || ""} · ${kindLabel(selected.kind)}`} onClose=${() => setDetailOpen(false)} className="focus-sheet compact-sheet">
      <${PaneDetail} pane=${selected} actions=${actions} connection=${connection} />
    <//>` : null}
  </div>`;
}

function MediumShell({ panes, connection, actions, usage, selectedId, setSelectedId, filter, setFilter, onBroadcast, onSettings, onCreate, onConnection, workspaceNav }) {
  const prefs = usePrefs();
  const [query, setQuery] = useState("");
  const [treeOpen, setTreeOpen] = useState(false);
  const counts = countsFor(panes);
  counts.total = panes.length;
  const searched = useMemo(() => panes.filter((pane) => !query || `${pane.name || ""} ${pane.target || ""} ${kindLabel(pane.kind)}`.toLowerCase().includes(query.toLowerCase())), [panes, query]);
  const visible = useMemo(() => panesForFilter(searched, filter, prefs.sort), [searched, filter, prefs.sort]);
  const selected = panes.find((pane) => pane.id === selectedId) || null;
  const openPane = (id) => acknowledgeDirectOpen(panes, id, setSelectedId);

  if (filter === "stats") return html`<div class="app-shell medium-shell stats-shell">
    <${BrandHeader} connection=${connection} onConnection=${onConnection} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} />
    <div class="stats-navigation">${workspaceNav ? html`<${WorkspaceNav} navigation=${workspaceNav} layout="medium" />` : html`<${FilterTabs} value=${filter} onChange=${setFilter} counts=${counts} usageWarning=${usageWarnings(usage)} />`}</div>
    <main><${UsageView} usage=${usage} layout="medium" /></main>
  </div>`;

  return html`<div class="app-shell medium-shell">
    <aside class="master-column">
      <${BrandHeader} connection=${connection} onConnection=${onConnection} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} />
      ${workspaceNav ? html`<${Fragment}><${WorkspaceNav} navigation=${workspaceNav} layout="medium" /><${FilterTabs} value=${filter} onChange=${setFilter} counts=${counts} includeStats=${false} /><//>`
        : html`<${FilterTabs} value=${filter} onChange=${setFilter} counts=${counts} usageWarning=${usageWarnings(usage)} />`}
      <div class="master-tools">
        <${SearchField} value=${query} onChange=${setQuery} />
        <button type="button" class="icon-button" aria-label="Open swarm tree" onClick=${() => setTreeOpen(true)}><${Icon} name="network" /></button>
      </div>
      ${filter === "all" ? html`<${SortSelect} value=${prefs.sort} onChange=${(sort) => setPrefs({ sort })} />` : null}
      <div class="master-scroll"><${PaneList} panes=${visible} mode=${filter} selectedId=${selectedId} onOpen=${openPane} actions=${actions} connection=${connection} /></div>
    </aside>
    <main class="detail-column"><${PaneDetail} pane=${selected} actions=${actions} connection=${connection} /></main>
    ${treeOpen ? html`<${Dialog} side=${true} title="Swarm tree" subtitle=${`${panes.length} panes`} onClose=${() => setTreeOpen(false)} className="tree-drawer">
      <${TreeView} panes=${panes} selectedId=${selectedId} onOpen=${(id) => acknowledgeDirectOpen(panes, id, setSelectedId, () => setTreeOpen(false))} actions=${actions} connection=${connection} onCreate=${onCreate} />
    <//>` : null}
  </div>`;
}

function Navigator({ panes, connection, actions, usage, selectedId, onOpen, destination, setDestination, onBroadcast, onSettings, onCreate, onConnection, workspaceNav }) {
  const prefs = usePrefs();
  const [query, setQuery] = useState("");
  const view = prefs.view === "list" ? "list" : "tree";
  const searched = useMemo(() => panes.filter((pane) => !query || `${pane.name || ""} ${pane.target || ""} ${kindLabel(pane.kind)}`.toLowerCase().includes(query.toLowerCase())), [panes, query]);
  const visible = useMemo(() => sortedPanes(searched, prefs.sort), [searched, prefs.sort]);
  const counts = countsFor(panes);
  return html`<aside class="wide-navigator">
    <${BrandHeader} connection=${connection} onConnection=${onConnection} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} />
    ${workspaceNav ? html`<${WorkspaceNav} navigation=${workspaceNav} layout="wide" />` : html`<nav class="wide-destinations" aria-label="Workspace">
      <button type="button" class=${destination === "queue" ? "selected" : ""} aria-current=${destination === "queue" ? "page" : null} onClick=${() => setDestination("queue")}>
        <${Icon} name="inbox" size=${18} /> Workspace
        ${Number(counts.blocked || 0) + Number(counts.error || 0) + Number(counts.done || 0) ? html`<b>${Number(counts.blocked || 0) + Number(counts.error || 0) + Number(counts.done || 0)}</b>` : null}
      </button>
      <button type="button" class=${destination === "stats" ? "selected" : ""} aria-current=${destination === "stats" ? "page" : null} onClick=${() => setDestination("stats")}>
        <${Icon} name="chart-no-axes-combined" size=${18} /> Stats
        ${usageWarnings(usage) ? html`<b>${usageWarnings(usage)}</b>` : null}
      </button>
    </nav>`}
    <div class="navigator-summary" aria-label="Pane summary">
      <span><b>${counts.blocked || 0}</b> need you</span>
      <span><b>${counts.done || 0}</b> done</span>
      <span><b>${counts.error || 0}</b> errors</span>
      <span><b>${counts.working || 0}</b> working</span>
    </div>
    <div class="navigator-tools">
      <${SearchField} value=${query} onChange=${setQuery} placeholder="Search swarm" />
      <${Segmented} value=${view} onChange=${(next) => setPrefs({ view: next })} label="Navigator view" options=${[["tree", "Tree"], ["list", "List"]]} />
      <${SortSelect} value=${prefs.sort} onChange=${(sort) => setPrefs({ sort })} />
    </div>
    <nav class="navigator-scroll" aria-label="Panes">
      ${view === "tree" ? html`<${TreeView} panes=${searched} selectedId=${selectedId} onOpen=${onOpen} actions=${actions} connection=${connection} onCreate=${onCreate} />`
        : html`<${PaneList} panes=${visible} mode="all" selectedId=${selectedId} onOpen=${onOpen} actions=${actions} connection=${connection} />`}
    </nav>
  </aside>`;
}

function WideShell({ panes, connection, actions, usage, selectedId, setSelectedId, filter, setFilter, onBroadcast, onSettings, onCreate, onConnection, workspaceNav }) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const queue = useMemo(() => attentionPanes(panes), [panes]);
  const selected = panes.find((pane) => pane.id === selectedId) || null;
  const openPane = (id) => acknowledgeDirectOpen(panes, id, setSelectedId);
  const priority = useMemo(() => {
    const urgent = attentionPanes(panes);
    const urgentIds = new Set(urgent.map((pane) => pane.id));
    return urgent.concat(sortedPanes(panes.filter((pane) => !urgentIds.has(pane.id)), "status"));
  }, [panes]);

  useEffect(() => {
    const onKey = (event) => {
      const tag = String(event.target && event.target.tagName || "").toLowerCase();
      const editing = ["input", "textarea", "select"].includes(tag) || (event.target && event.target.isContentEditable);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (editing || paletteOpen || filter === "stats") return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const current = priority.findIndex((pane) => pane.id === selectedId);
        const start = current < 0 ? 0 : current;
        const next = event.key === "ArrowDown" ? Math.min(priority.length - 1, start + 1) : Math.max(0, start - 1);
        if (priority[next]) openPane(priority[next].id);
      } else if (/^[0-9]$/.test(event.key) && selected && Array.isArray(selected.menu)) {
        const option = selected.menu.find((item) => String(item.key) === event.key);
        if (option && canAct(actions, connection, selected)) {
          event.preventDefault();
          const task = option.freeform && typeof actions.selectThenCompose === "function"
            ? actions.selectThenCompose(selected, option.key)
            : actions.select(selected, option.key);
          task.catch(reportActionError);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [actions, connection, filter, openPane, paletteOpen, priority, selected, selectedId]);

  const pick = (id) => acknowledgeDirectOpen(panes, id, setSelectedId, () => { setFilter("queue"); setPaletteOpen(false); });
  return html`<div class="app-shell wide-shell">
    <${Navigator}
      panes=${panes}
      connection=${connection}
      actions=${actions}
      usage=${usage}
      selectedId=${selectedId}
      onOpen=${openPane}
      destination=${filter === "stats" ? "stats" : "queue"}
      setDestination=${setFilter}
      onBroadcast=${onBroadcast}
      onSettings=${onSettings}
      onCreate=${onCreate}
      onConnection=${onConnection}
      workspaceNav=${workspaceNav}
    />
    ${filter === "stats" ? html`<main class="wide-stats"><${UsageView} usage=${usage} layout="wide" /></main>` : html`<${Fragment}>
      <section class="wide-queue">
        <header class="column-head"><div><p>Attention queue</p><h1>${queue.length ? `${queue.length} need attention` : "Queue clear"}</h1></div><span class="spacer"></span><kbd>⌘K</kbd></header>
        <div class="column-scroll"><${PaneList} panes=${queue} mode="queue" selectedId=${selectedId} onOpen=${openPane} actions=${actions} connection=${connection} /></div>
      </section>
      <main class="wide-inspector"><${PaneDetail} pane=${selected} actions=${actions} connection=${connection} /></main>
    <//>`}
    ${paletteOpen ? html`<${CommandPalette} panes=${panes} onPick=${pick} onClose=${() => setPaletteOpen(false)} />` : null}
  </div>`;
}

export function ConnectionDetails({ connection, onRetry = null }) {
  const meta = connectionMeta(connection);
  const issue = connection && connection.issue || {};
  const compatibility = connection && connection.compatibility || {};
  const fields = [
    ["Host", location.host],
    ["Endpoint", issue.endpoint],
    ["HTTP status", issue.httpStatus ?? issue.status],
    ["Compatibility", compatibility.status],
    ["Client version", compatibility.clientVersion ?? compatibility.client_version],
    ["Server version", compatibility.serverVersion ?? compatibility.server_version],
    ["Protocol", compatibility.protocolVersion ?? compatibility.protocol_version],
    ["Server protocol", compatibility.serverProtocolVersion ?? compatibility.server_protocol_version],
    ["Expected protocol", compatibility.expectedProtocol ?? compatibility.expected_protocol],
    ["Minimum server", compatibility.minimumServerVersion ?? compatibility.minimum_server_version],
    ["Category", issue.category],
    ["Timestamp", issue.timestamp],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  return html`<div class="connection-details">
    <${InlineNotice}
      tone=${meta.tone}
      icon=${meta.icon}
      action=${onRetry ? html`<button type="button" class="button secondary" onClick=${onRetry}>Retry</button>` : null}
    ><strong>${meta.label}</strong><p>${meta.mode === "rest" || meta.mode === "updating_rest" ? "Live updates are unavailable, so vmux is refreshing over REST." : meta.mode === "offline" ? "Showing the last in-memory snapshot while vmux reconnects." : meta.mode === "incompatible" ? "Update vmux or this web client before sending pane actions." : meta.mode === "unauthorized" ? "Enter a valid access token to continue." : meta.mode === "live" ? "WebSocket updates are healthy." : "Establishing a connection to vmux."}</p><//>
    ${fields.length ? html`<dl class="technical-details">
      ${fields.map(([label, value]) => html`<div key=${label}><dt>${label}</dt><dd>${String(value)}</dd></div>`)}
    </dl>` : null}
  </div>`;
}

export function TokenGate({ onSubmit = null }) {
  const [value, setValue] = useState("");
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const submit = async () => {
    const token = value.trim();
    if (!token || status === "pending") return;
    setStatus("pending");
    setMessage("Connecting…");
    try {
      if (onSubmit) await onSubmit(token);
      else {
        localStorage.setItem("vmux_token", token);
        location.replace(`${location.pathname}${location.hash || ""}`);
      }
      setStatus("success");
      setMessage("Connected");
    } catch (_) {
      setStatus("error");
      setMessage("That token was not accepted. Try again.");
    }
  };
  return html`<main class="token-gate">
    <section class="token-card glass" aria-labelledby="token-title">
      <div class="brand-lockup large"><span>v</span><strong>mux</strong></div>
      <h1 id="token-title">Connect to vmux</h1>
      <p>Enter the access token configured on this server. It stays on this device.</p>
      <label>
        <span>Access token</span>
        <input
          type="password"
          value=${value}
          autocomplete="current-password"
          autoFocus=${true}
          onInput=${(event) => setValue(event.target.value)}
          onKeyDown=${(event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } }}
        />
      </label>
      <button type="button" class="button primary" disabled=${!value.trim() || status === "pending"} onClick=${submit}>
        ${status === "pending" ? "Connecting…" : "Connect"}
      </button>
      <div class=${cx("gate-status", status === "error" && "error")} role=${status === "error" ? "alert" : "status"} aria-live="polite">${message}</div>
    </section>
  </main>`;
}

export function Workspace({
  layout,
  panes = [],
  connection = { mode: "connecting" },
  onRetry = null,
  onBroadcast = () => {},
  onSettings = () => {},
  onCreate = null,
  openPaneId = "",
  workspaceNav = null,
}) {
  const prefs = usePrefs();
  const actions = useActions();
  const usage = useUsage();
  const [filter, setFilterState] = useState(() => preferredFilter(prefs.defaultFilter));
  const [selectedId, setSelectedId] = useState(null);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const routedPaneOpen = useRef({ id: "", opened: false });
  const stablePanes = useStablePaneOrder(panes, prefs.sort, filter);

  const priority = useMemo(() => {
    const urgent = attentionPanes(stablePanes);
    const urgentIds = new Set(urgent.map((pane) => pane.id));
    return urgent.concat(sortedPanes(stablePanes.filter((pane) => !urgentIds.has(pane.id)), prefs.sort));
  }, [stablePanes, prefs.sort]);

  useEffect(() => {
    if (selectedId && stablePanes.some((pane) => pane.id === selectedId)) return;
    setSelectedId(priority[0] ? priority[0].id : null);
  }, [stablePanes, priority, selectedId]);

  useEffect(() => {
    if (!workspaceNav) {
      routedPaneOpen.current = { id: "", opened: false };
      return;
    }
    if (workspaceNav.current === "stats" && filter !== "stats") setFilterState("stats");
    if (workspaceNav.current === "panes" && filter === "stats") setFilterState("queue");
    const routedPaneId = workspaceNav.current === "panes" ? workspaceNav.paneId || "" : "";
    if (routedPaneOpen.current.id !== routedPaneId) {
      routedPaneOpen.current = { id: routedPaneId, opened: false };
    }
    if (routedPaneId && stablePanes.some((pane) => pane.id === routedPaneId)) {
      if (!routedPaneOpen.current.opened) {
        routedPaneOpen.current.opened = true;
        acknowledgeDirectOpen(stablePanes, routedPaneId, setSelectedId);
      } else {
        setSelectedId(routedPaneId);
      }
    }
  }, [filter, stablePanes, workspaceNav]);
  useEffect(() => {
    if (openPaneId && stablePanes.some((pane) => pane.id === openPaneId)) {
      setSelectedId(openPaneId);
    }
  }, [openPaneId, stablePanes]);
  const setFilter = (next) => {
    const normalized = preferredFilter(next);
    setFilterState(normalized);
  };
  const openConnection = () => setConnectionOpen(true);
  const shellProps = {
    panes: stablePanes,
    connection,
    actions,
    usage,
    selectedId,
    setSelectedId,
    filter,
    setFilter,
    onBroadcast,
    onSettings,
    onCreate,
    onConnection: openConnection,
    openPaneId,
    workspaceNav,
  };

  return html`<${Fragment}>
    ${layout === "wide" ? html`<${WideShell} ...${shellProps} />`
      : layout === "medium" ? html`<${MediumShell} ...${shellProps} />`
      : html`<${CompactShell} ...${shellProps} />`}
    ${connectionOpen ? html`<${Dialog} title="Connection" subtitle=${connectionMeta(connection).label} onClose=${() => setConnectionOpen(false)} className="connection-dialog">
      <${ConnectionDetails} connection=${connection} onRetry=${onRetry} />
    <//>` : null}
  <//>`;
}

export { TreeView, Terminal, AttentionCard, PaneDetail, extractLinks };
