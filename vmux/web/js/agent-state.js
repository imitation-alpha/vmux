/**
 * Agent workspace state, REST actions, hash routes, and invalidation transport.
 *
 * This module intentionally does not share PaneState or the pane WebSocket.
 * Agent data is capability-gated, hydrated from authenticated REST endpoints,
 * and retained only in memory by the browser.
 */

import { useEffect, useState } from "./core.js";
import { TOKEN, api } from "./state.js";
import { reviewDraftStore } from "./review-drafts.js";

export const AGENT_CAPABILITY = "agent_context_v1";
export const REVIEW_CAPABILITY = "agent_review_v1";
export const LEGACY_AGENT_DESTINATIONS = Object.freeze([
  ["agents", "bot", "Agents"],
  ["decisions", "inbox", "Inbox"],
  ["panes", "panels-top-left", "Panes"],
  ["timeline", "clock", "Timeline"],
  ["stats", "chart-no-axes-combined", "Stats"],
]);
export const AGENT_DESTINATIONS = Object.freeze([
  ["agents", "bot", "Agents"],
  ["review", "list-checks", "Review"],
  ["panes", "panels-top-left", "Panes"],
  ["timeline", "clock", "Timeline"],
  ["stats", "chart-no-axes-combined", "Stats"],
]);

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function text(value, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function identifier(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

function timestamp(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value > 1e12 ? value / 1000 : value;
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return 0;
}

function normalizeCapabilityValue(value) {
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "available" : "unavailable";
  if (isObject(value)) return text(value.mode || value.status, "available");
  return "unavailable";
}

function timelineTitle(source) {
  const explicit = text(source.title || source.summary || source.description);
  if (explicit) return explicit;
  const delta = isObject(source.delta) ? source.delta : {};
  const context = isObject(source.context) ? source.context : {};
  if (array(delta.decisions_added).length) return "Decision needed";
  if (array(delta.completed).length || array(delta.completed_items).length) return "Work completed";
  if (array(delta.new_blockers).length) return "New blocker reported";
  if (delta.goal_changed) return "Goal changed";
  if (delta.current_task_changed || delta.task_changed) return "Task changed";
  if (delta.lifecycle_changed) return `Agent ${text(delta.lifecycle_changed.to, "state changed")}`;
  return text(context.current_task || context.progress_summary || context.goal, "Agent context updated");
}

export function agentContextCapability(config) {
  const info = isObject(config?._info) ? config._info : {};
  const capabilities = isObject(info.capabilities) ? info.capabilities : {};
  const raw = capabilities[AGENT_CAPABILITY] ?? capabilities.agent_context;
  if (raw === true) return { enabled: true, mode: "available" };
  if (!isObject(raw)) return { enabled: false, mode: "unavailable" };
  const enabled = raw.enabled !== false && raw.available !== false;
  return { ...raw, enabled, mode: text(raw.mode, enabled ? "available" : "unavailable") };
}

export function agentReviewCapability(config) {
  const info = isObject(config?._info) ? config._info : {};
  const capabilities = isObject(info.capabilities) ? info.capabilities : {};
  const raw = capabilities[REVIEW_CAPABILITY];
  if (raw === true) return { enabled: true, mode: "available" };
  if (!isObject(raw)) return { enabled: false, mode: "unavailable" };
  const enabled = raw.enabled !== false && raw.available !== false;
  return { ...raw, enabled, mode: text(raw.mode, enabled ? "available" : "unavailable") };
}

export function capabilityMode(agent, key) {
  const capabilities = isObject(agent?.capabilities) ? agent.capabilities : {};
  return normalizeCapabilityValue(capabilities[key]);
}

function normalizeProgress(raw) {
  if (!isObject(raw)) return null;
  const completed = Number(raw.completed);
  const total = Number(raw.total);
  const percent = Number(raw.percent);
  return {
    completed: Number.isFinite(completed) ? completed : null,
    total: Number.isFinite(total) && total > 0 ? total : null,
    percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent))
      : Number.isFinite(completed) && Number.isFinite(total) && total > 0
        ? Math.max(0, Math.min(100, completed / total * 100)) : null,
    source: text(raw.source, "runtime"),
  };
}

function normalizeListItem(raw, index) {
  if (typeof raw === "string") return { id: `item-${index}`, title: raw, text: raw };
  const item = isObject(raw) ? raw : {};
  const label = text(item.title || item.label || item.text || item.description, "Update");
  return { ...item, id: identifier(item.id) || `item-${index}`, title: label, text: label };
}

export function normalizeAgent(raw) {
  const source = isObject(raw) ? raw : {};
  const context = isObject(source.context) ? source.context : source;
  const binding = isObject(source.binding) ? source.binding : {};
  const id = identifier(source.id || source.session_id || context.session_id);
  return {
    ...source,
    ...context,
    id,
    session_id: identifier(source.session_id || context.session_id || id),
    runtime: text(source.runtime || context.runtime, "Unknown runtime"),
    runtime_display_name: text(source.runtime_display_name || context.runtime_display_name || source.runtime || context.runtime, "Unknown runtime"),
    name: text(source.name || source.title || context.name || context.goal || context.current_task, "Agent session"),
    goal: text(context.goal),
    current_task: text(context.current_task),
    progress_summary: text(context.progress_summary),
    next_action: text(context.next_action),
    lifecycle: text(source.lifecycle || context.lifecycle || source.status, "unknown"),
    revision: Number(source.revision ?? context.revision ?? 0),
    last_updated: timestamp(source.last_updated || context.last_updated || source.updated_at),
    progress: normalizeProgress(context.progress),
    estimated_completion: context.estimated_completion ?? null,
    completed_items: array(context.completed_items).map(normalizeListItem),
    blockers: array(context.blockers).map(normalizeListItem),
    capabilities: isObject(source.capabilities) ? source.capabilities : {},
    extraction_health: text(source.extraction_health || context.extraction_health, "unknown"),
    association: text(source.association || binding.association, normalizeCapabilityValue(source.capabilities?.association)),
    pane_id: identifier(source.pane_id || binding.pane_id),
    binding: {
      ...binding,
      pane_id: identifier(binding.pane_id || source.pane_id),
      target: text(binding.target || source.target),
      revision: Number(binding.revision ?? source.binding_revision ?? 0),
      association: text(binding.association || source.association, "unavailable"),
      candidates: array(binding.candidates || source.binding_candidates),
    },
  };
}

export function normalizeDecision(raw) {
  const source = isObject(raw) ? raw : {};
  const options = array(source.options).map((option, index) => {
    const item = isObject(option) ? option : { label: String(option) };
    return {
      ...item,
      id: identifier(item.id || item.option_id || item.key) || `option-${index}`,
      label: text(item.label || item.title, `Option ${index + 1}`),
      description: text(item.description),
      recommended: Boolean(item.recommended),
      destructive: Boolean(item.destructive),
    };
  });
  const recommendation = identifier(source.recommendation || source.recommended_option_id);
  return {
    ...source,
    id: identifier(source.id),
    agent_id: identifier(source.agent_id || source.session_id),
    title: text(source.title, "Decision needed"),
    description: text(source.description),
    kind: text(source.kind, "question"),
    priority: text(source.priority, "normal"),
    status: text(source.status, "pending"),
    review_status: text(source.review_status),
    created_at: timestamp(source.created_at || source.created_time),
    revision: Number(source.revision ?? 0),
    binding_revision: Number(source.binding_revision ?? 0),
    prompt_fingerprint: text(source.prompt_fingerprint),
    options_fingerprint: text(source.options_fingerprint),
    allow_custom: Boolean(source.allow_custom),
    recommendation,
    options: options.map((option) => ({
      ...option,
      recommended: option.recommended || (recommendation && option.id === recommendation),
    })),
  };
}

function normalizeReviewGroup(raw, index = 0) {
  const source = isObject(raw) ? raw : {};
  const agent = normalizeAgent(source.agent || source.context || {});
  const decisions = array(source.decisions || source.pending_decisions).map(normalizeDecision);
  return {
    ...source,
    id: identifier(source.agent_id || agent.id) || `review-group-${index}`,
    agent_id: identifier(source.agent_id || agent.id),
    agent,
    decisions,
    changes: isObject(source.changes) ? source.changes : {},
    as_of_snapshot_id: identifier(source.as_of_snapshot_id),
    as_of_snapshot_sequence: Number(source.as_of_snapshot_sequence ?? 0),
    as_of_snapshot_at: timestamp(source.as_of_snapshot_at),
    reviewed_snapshot_id: identifier(source.reviewed_snapshot_id),
    reviewed_snapshot_sequence: Number(source.reviewed_snapshot_sequence ?? 0),
    reviewed_snapshot_at: source.reviewed_snapshot_at == null
      ? null : timestamp(source.reviewed_snapshot_at),
    reviewed_at: source.reviewed_at == null ? null : timestamp(source.reviewed_at),
    has_changes: Boolean(source.has_changes),
    history_truncated: Boolean(source.history_truncated),
    oldest_pending_decision_at: timestamp(source.oldest_pending_decision_at),
    rank_reason: text(source.rank_reason, "changed"),
    attention_reasons: array(source.attention_reasons).map((value) => text(value)).filter(Boolean),
  };
}

function normalizeReview(raw) {
  const source = isObject(raw) ? raw : {};
  const settings = isObject(source.settings) ? source.settings : {};
  const urgent = isObject(settings.urgent_bypass) ? settings.urgent_bypass : {};
  return {
    version: text(source.version, REVIEW_CAPABILITY),
    generated_at: timestamp(source.generated_at),
    settings: {
      enabled: Boolean(settings.enabled),
      interval_minutes: settings.interval_minutes == null ? null : Number(settings.interval_minutes),
      next_due_at: timestamp(settings.next_due_at),
      last_digest_at: timestamp(settings.last_digest_at),
      urgent_bypass: {
        high_critical_decisions: urgent.high_critical_decisions !== false,
        pane_errors: Boolean(urgent.pane_errors),
      },
      min_interval_minutes: Number(settings.min_interval_minutes ?? 5),
      max_interval_minutes: Number(settings.max_interval_minutes ?? 1440),
      presets: array(settings.presets).map(Number).filter(Number.isFinite),
    },
    due: isObject(source.due) ? source.due : {},
    counts: isObject(source.counts) ? source.counts : {},
    groups: array(source.groups || source.agents).map(normalizeReviewGroup),
    terminal_items: array(source.terminal_items).map((item) => ({
      id: identifier(item?.id || `pane:${item?.pane_id || ""}`),
      pane_id: identifier(item?.pane_id),
      status: text(item?.status, "needs_input"),
      kind: text(item?.kind, "generic"),
      updated_at: timestamp(item?.updated_at),
      acknowledgeable: false,
    })).filter((item) => item.pane_id),
  };
}

export function reviewDecisionMatches(staged, current) {
  if (!staged || !current || current.status !== "pending") return false;
  const optionID = identifier(staged.option_id || staged.optionId);
  const customText = text(staged.custom_text || staged.customText).trim();
  const validChoice = (optionID && current.options.some((option) => option.id === optionID))
    || (customText && current.allow_custom);
  return Boolean(validChoice)
    && Number(staged.revision) === Number(current.revision)
    && Number(staged.binding_revision) === Number(current.binding_revision)
    && text(staged.prompt_fingerprint) === text(current.prompt_fingerprint)
    && text(staged.options_fingerprint) === text(current.options_fingerprint);
}

function decisionDeliveryUncertain(decision) {
  const status = text(decision?.status).toLowerCase();
  const reviewStatus = text(decision?.review_status).toLowerCase();
  return ["unknown", "terminal_required"].includes(status)
    || ["unknown", "terminal_required"].includes(reviewStatus);
}

export function normalizeTimelineEvent(raw, index = 0) {
  const source = isObject(raw) ? raw : {};
  return {
    ...source,
    id: identifier(source.id || source.snapshot_id || source.event_id) || `event-${index}`,
    agent_id: identifier(source.agent_id || source.session_id),
    type: text(source.type || source.kind, "activity"),
    title: timelineTitle(source),
    description: text(source.description || source.detail),
    occurred_at: timestamp(source.occurred_at || source.created_at || source.timestamp || source.last_updated),
  };
}

export function normalizeMessage(raw, index = 0) {
  const source = isObject(raw) ? raw : {};
  return {
    ...source,
    id: identifier(source.id || source.message_id || source.client_message_id) || `message-${index}`,
    role: text(source.role, "assistant"),
    content: text(source.content || source.text),
    status: text(source.status, "observed"),
    created_at: timestamp(source.created_at || source.timestamp),
  };
}

function listPayload(value, keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return [];
}

function nextCursor(value) {
  return isObject(value) ? value.next_cursor ?? null : null;
}

const CURSOR_PAGE_CAP = 20;
const CURSOR_ITEM_CAP = 2000;

function cursorValue(value) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function withCursor(path, cursor) {
  const value = cursorValue(cursor);
  if (!value) return path;
  return `${path}${path.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(value)}`;
}

function agentWireId(raw) {
  const source = isObject(raw) ? raw : {};
  const context = isObject(source.context) ? source.context : {};
  return identifier(source.id || source.session_id || context.session_id);
}

function decisionWireId(raw) {
  return identifier(isObject(raw) ? raw.id : null);
}

function timelineWireId(raw) {
  const source = isObject(raw) ? raw : {};
  return identifier(source.id || source.snapshot_id || source.event_id);
}

function messageWireId(raw) {
  const source = isObject(raw) ? raw : {};
  return identifier(source.id || source.message_id || source.client_message_id);
}

function mergeDecisionCollections(...collections) {
  const decisions = [];
  const indices = new Map();
  for (const raw of collections.flat()) {
    const decision = normalizeDecision(raw);
    if (!decision.id) continue;
    const existingIndex = indices.get(decision.id);
    if (existingIndex === undefined) {
      indices.set(decision.id, decisions.length);
      decisions.push(decision);
    } else if (decision.revision >= decisions[existingIndex].revision) {
      decisions[existingIndex] = decision;
    }
  }
  return decisions;
}

function uid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function parseAgentRoute(hash = globalThis.location?.hash || "") {
  const source = String(hash || "").replace(/^#/, "");
  const path = source.startsWith("/") ? source : "";
  const parts = path.split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch (_) { return part; }
  });
  const destination = ["agents", "review", "decisions", "panes", "timeline", "stats"].includes(parts[0])
    ? parts[0] : "agents";
  return { destination, id: parts[1] || "", valid: Boolean(path && parts[0]) };
}

export function agentRoute(destination, id = "") {
  const safeDestination = ["agents", "review", "decisions", "panes", "timeline", "stats"].includes(destination)
    ? destination : "agents";
  return `#/${safeDestination}${id ? `/${encodeURIComponent(id)}` : ""}`;
}

export function navigateAgentRoute(destination, id = "", { replace = false } = {}) {
  const hash = agentRoute(destination, id);
  if (replace && globalThis.history?.replaceState) {
    globalThis.history.replaceState(null, "", `${globalThis.location.pathname}${globalThis.location.search}${hash}`);
    globalThis.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    globalThis.location.hash = hash;
  }
}

export function useAgentRoute() {
  const [route, setRoute] = useState(() => parseAgentRoute());
  useEffect(() => {
    const update = () => setRoute(parseAgentRoute());
    globalThis.addEventListener?.("hashchange", update);
    return () => globalThis.removeEventListener?.("hashchange", update);
  }, []);
  return route;
}

export function createAgentStore({
  request = api,
  WebSocketImpl = globalThis.WebSocket || null,
  origin = globalThis.location?.origin || "http://localhost",
  token = TOKEN,
} = {}) {
  const listeners = new Set();
  let enabled = false;
  let reviewEnabled = false;
  let running = false;
  let socket = null;
  let reconnectTimer = null;
  let reconnectAttempt = 0;
  let refreshTimer = null;
  let generation = 0;
  let scopeGeneration = 0;
  let reviewRequestSequence = 0;
  const pendingVisits = new Set();
  const pendingReviews = new Set();
  const messageRequestSequences = new Map();
  const quickReplyPromises = new Map();
  const uncertainPlanDrafts = new Set();
  let snapshot = {
    enabled: false,
    reviewEnabled: false,
    serverInstanceId: "",
    status: "disabled",
    error: "",
    agents: [],
    agentMap: {},
    decisions: [],
    timeline: [],
    details: {},
    resume: {},
    messages: {},
    messagePages: {},
    agentTimelines: {},
    review: null,
    planDrafts: [],
    planResults: [],
    loading: {},
    mutations: {},
    nextCursor: { agents: null, decisions: null, timeline: null },
    live: "offline",
    cursor: null,
  };

  function publish(patch) {
    snapshot = { ...snapshot, ...patch };
    for (const listener of Array.from(listeners)) listener();
  }

  function patchMap(key, id, value) {
    publish({ [key]: { ...snapshot[key], [id]: value } });
  }

  function setLoading(key, value) {
    publish({ loading: { ...snapshot.loading, [key]: value } });
  }

  function setMutation(key, value) {
    publish({ mutations: { ...snapshot.mutations, [key]: value } });
  }

  async function collectCursorPages(path, keys, { idOf, reversePages = false } = {}) {
    const pages = [];
    const itemIds = new Set();
    const requestedCursors = new Set();
    let cursor = null;
    let remainingCursor = null;
    let itemCount = 0;

    for (let pageCount = 0; pageCount < CURSOR_PAGE_CAP && itemCount < CURSOR_ITEM_CAP; pageCount += 1) {
      const requestedCursor = cursorValue(cursor);
      if (requestedCursor) requestedCursors.add(requestedCursor);
      const payload = await request(withCursor(path, requestedCursor));
      const page = [];
      for (const item of listPayload(payload, keys)) {
        const id = typeof idOf === "function" ? idOf(item) : "";
        if (id && itemIds.has(id)) continue;
        if (id) itemIds.add(id);
        page.push(item);
        itemCount += 1;
        if (itemCount >= CURSOR_ITEM_CAP) break;
      }
      pages.push(page);

      const next = cursorValue(nextCursor(payload));
      remainingCursor = next || null;
      if (!next || requestedCursors.has(next)) break;
      cursor = next;
    }

    const orderedPages = reversePages ? [...pages].reverse() : pages;
    return { items: orderedPages.flat(), nextCursor: remainingCursor };
  }

  async function loadAgents() {
    const result = await collectCursorPages("/agents", ["agents", "items"], { idOf: agentWireId });
    const agents = result.items.map(normalizeAgent).filter((agent) => agent.id);
    publish({ agents, agentMap: Object.fromEntries(agents.map((agent) => [agent.id, agent])), nextCursor: { ...snapshot.nextCursor, agents: result.nextCursor } });
    return agents;
  }

  async function loadDecisions() {
    const [history, pending, submitting] = await Promise.all([
      collectCursorPages("/decisions", ["decisions", "items"], { idOf: decisionWireId }),
      collectCursorPages("/decisions?status=pending", ["decisions", "items"], { idOf: decisionWireId }),
      collectCursorPages("/decisions?status=submitting", ["decisions", "items"], { idOf: decisionWireId }),
    ]);
    const decisions = mergeDecisionCollections(history.items, pending.items, submitting.items);
    publish({ decisions, nextCursor: { ...snapshot.nextCursor, decisions: history.nextCursor } });
    return decisions;
  }

  async function loadTimeline() {
    const result = await collectCursorPages("/timeline", ["events", "timeline", "items"], { idOf: timelineWireId });
    const timeline = result.items.map(normalizeTimelineEvent);
    publish({ timeline, nextCursor: { ...snapshot.nextCursor, timeline: result.nextCursor } });
    return timeline;
  }

  function reviewDecisions(review) {
    return review?.groups?.flatMap((group) => group.decisions || []) || [];
  }

  async function loadReview() {
    if (!reviewEnabled) return null;
    const requestScope = scopeGeneration;
    const requestServer = snapshot.serverInstanceId;
    const requestSequence = ++reviewRequestSequence;
    const payload = await request("/review");
    if (
      requestScope !== scopeGeneration
      || requestServer !== snapshot.serverInstanceId
      || requestSequence !== reviewRequestSequence
      || !reviewEnabled
    ) {
      return null;
    }
    const review = normalizeReview(payload);
    const currentReviewDecisions = reviewDecisions(review);
    const currentByID = new Map(currentReviewDecisions.map((decision) => [decision.id, decision]));
    for (const decisionID of [...uncertainPlanDrafts]) {
      const current = currentByID.get(decisionID);
      if (current && !decisionDeliveryUncertain(current)) uncertainPlanDrafts.delete(decisionID);
    }
    const decisions = mergeDecisionCollections(snapshot.decisions, currentReviewDecisions);
    const planDrafts = reviewDraftStore.reconcile(
      snapshot.serverInstanceId,
      currentReviewDecisions,
      {
        authoritative: true,
        preserveDecisionIDs: [...uncertainPlanDrafts],
      },
    );
    publish({ review, decisions, planDrafts });
    return review;
  }

  async function hydrate() {
    if (!enabled) return false;
    const currentGeneration = ++generation;
    publish({ status: snapshot.agents.length ? "refreshing" : "loading", error: "" });
    const loaders = [loadAgents(), loadDecisions(), loadTimeline()];
    if (reviewEnabled) loaders.push(loadReview());
    const results = await Promise.allSettled(loaders);
    if (currentGeneration !== generation || !enabled) return false;
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length === results.length) {
      const error = failures[0]?.reason;
      publish({ status: "error", error: error?.userMessage || error?.message || "Agent workspace is unavailable." });
      return false;
    }
    publish({ status: failures.length ? "degraded" : "ready", error: failures.length ? "Some agent data could not be refreshed." : "" });
    return true;
  }

  async function loadAgent(id) {
    if (!id || !enabled) return null;
    setLoading(`agent:${id}`, true);
    try {
      const [detailResult, resumeResult, messagesResult, timelineResult] = await Promise.allSettled([
        request(`/agents/${encodeURIComponent(id)}`),
        request(`/agents/${encodeURIComponent(id)}/resume`),
        collectCursorPages(`/agents/${encodeURIComponent(id)}/messages`, ["messages", "items"], { idOf: messageWireId, reversePages: true }),
        collectCursorPages(`/agents/${encodeURIComponent(id)}/timeline`, ["events", "timeline", "items"], { idOf: timelineWireId }),
      ]);
      if (detailResult.status === "rejected") throw detailResult.reason;
      const detail = normalizeAgent(detailResult.value);
      const resume = resumeResult.status === "fulfilled" && isObject(resumeResult.value) ? resumeResult.value : null;
      const messages = messagesResult.status === "fulfilled"
        ? messagesResult.value.items.map(normalizeMessage) : [];
      const timeline = timelineResult.status === "fulfilled"
        ? timelineResult.value.items.map(normalizeTimelineEvent) : [];
      patchMap("details", id, detail);
      patchMap("resume", id, resume);
      patchMap("messages", id, messages);
      patchMap("agentTimelines", id, timeline);
      return detail;
    } finally {
      setLoading(`agent:${id}`, false);
    }
  }

  async function loadMessagePage(id, {
    q = "",
    role = "",
    after = null,
    before = null,
    cursor = null,
    append = false,
  } = {}) {
    if (!id || !enabled) return null;
    const parameters = new URLSearchParams({ limit: "100" });
    if (q) parameters.set("q", q);
    if (role) parameters.set("role", role);
    if (after != null && Number.isFinite(Number(after))) parameters.set("after", String(after));
    if (before != null && Number.isFinite(Number(before))) parameters.set("before", String(before));
    if (cursor) parameters.set("cursor", String(cursor));
    const key = `messages:${id}`;
    const requestSequence = (messageRequestSequences.get(id) || 0) + 1;
    const requestScope = scopeGeneration;
    messageRequestSequences.set(id, requestSequence);
    const isCurrentRequest = () => requestScope === scopeGeneration
      && messageRequestSequences.get(id) === requestSequence;
    setLoading(key, true);
    try {
      const payload = await request(`/agents/${encodeURIComponent(id)}/messages?${parameters}`);
      if (!isCurrentRequest()) return null;
      const incoming = listPayload(payload, ["messages", "items"]).map(normalizeMessage);
      const existingPage = snapshot.messagePages[id] || {};
      const existing = append ? array(existingPage.messages) : [];
      const byID = new Map([...existing, ...incoming].map((message) => [message.id, message]));
      const incomingReviewedSequence = Number(payload?.reviewed_snapshot_sequence ?? 0);
      const existingReviewedSequence = Number(existingPage.reviewed_snapshot_sequence ?? 0);
      const keepExistingBaseline = existingReviewedSequence > incomingReviewedSequence;
      const page = {
        messages: [...byID.values()].sort((left, right) => left.created_at - right.created_at),
        next_cursor: nextCursor(payload),
        retained_from: timestamp(payload?.retained_from),
        retained_to: timestamp(payload?.retained_to),
        reviewed_at: keepExistingBaseline
          ? existingPage.reviewed_at
          : (payload?.reviewed_at == null ? existingPage.reviewed_at ?? null : timestamp(payload.reviewed_at)),
        reviewed_snapshot_id: keepExistingBaseline
          ? identifier(existingPage.reviewed_snapshot_id)
          : identifier(payload?.reviewed_snapshot_id || existingPage.reviewed_snapshot_id),
        reviewed_snapshot_sequence: Math.max(incomingReviewedSequence, existingReviewedSequence),
        reviewed_snapshot_at: keepExistingBaseline
          ? existingPage.reviewed_snapshot_at
          : (payload?.reviewed_snapshot_at == null
            ? existingPage.reviewed_snapshot_at ?? null : timestamp(payload.reviewed_snapshot_at)),
        history_truncated: Boolean(payload?.history_truncated),
        filters: { q, role, after, before },
        partial: Boolean(nextCursor(payload)),
        error: "",
      };
      patchMap("messagePages", id, page);
      return page;
    } catch (error) {
      if (!isCurrentRequest()) return null;
      patchMap("messagePages", id, {
        ...(snapshot.messagePages[id] || {}),
        error: error?.userMessage || error?.message || "Conversation could not be loaded.",
      });
      throw error;
    } finally {
      if (isCurrentRequest()) setLoading(key, false);
    }
  }

  async function loadDecision(id) {
    if (!id) return null;
    const requestScope = scopeGeneration;
    setLoading(`decision:${id}`, true);
    try {
      const payload = await request(`/decisions/${encodeURIComponent(id)}`);
      if (requestScope !== scopeGeneration) return null;
      const decision = normalizeDecision(payload?.decision || payload);
      const decisions = snapshot.decisions.some((item) => item.id === id)
        ? snapshot.decisions.map((item) => item.id === id ? decision : item)
        : [decision, ...snapshot.decisions];
      publish({ decisions });
      return decision;
    } finally {
      if (requestScope === scopeGeneration) setLoading(`decision:${id}`, false);
    }
  }

  async function acknowledgeVisit(id, snapshotId) {
    const key = `${id}:${snapshotId}`;
    if (!id || !snapshotId || pendingVisits.has(key)) return false;
    pendingVisits.add(key);
    try {
      await request(`/agents/${encodeURIComponent(id)}/visit`, { snapshot_id: snapshotId }, "PUT");
      return true;
    } finally {
      pendingVisits.delete(key);
    }
  }

  async function acknowledgeReview(id, snapshotId) {
    const key = `${id}:${snapshotId}`;
    if (!id || !snapshotId || pendingReviews.has(key)) return false;
    const actionScope = scopeGeneration;
    pendingReviews.add(key);
    setMutation(`review:${id}`, { status: "pending" });
    try {
      const group = snapshot.review?.groups?.find((item) => (
        item.agent_id === id && item.as_of_snapshot_id === snapshotId
      ));
      const payload = await request(
        `/agents/${encodeURIComponent(id)}/review`,
        { snapshot_id: snapshotId },
        "PUT",
      );
      if (actionScope !== scopeGeneration) return false;
      const existingPage = snapshot.messagePages[id];
      if (existingPage) {
        const acknowledgedSequence = Number(
          payload?.snapshot_sequence ?? group?.as_of_snapshot_sequence ?? 0,
        );
        if (acknowledgedSequence >= Number(existingPage.reviewed_snapshot_sequence ?? 0)) {
          patchMap("messagePages", id, {
            ...existingPage,
            reviewed_snapshot_id: identifier(payload?.snapshot_id || snapshotId),
            reviewed_snapshot_sequence: acknowledgedSequence,
            reviewed_snapshot_at: timestamp(
              payload?.snapshot_at || group?.as_of_snapshot_at,
            ) || existingPage.reviewed_snapshot_at || null,
            reviewed_at: timestamp(payload?.reviewed_at) || existingPage.reviewed_at || null,
          });
        }
      }
      setMutation(`review:${id}`, { status: "success" });
      await loadReview();
      return true;
    } catch (error) {
      if (actionScope === scopeGeneration) {
        setMutation(`review:${id}`, {
          status: "error",
          message: error?.userMessage || error?.message || "Review state could not be updated.",
        });
      }
      throw error;
    } finally {
      pendingReviews.delete(key);
    }
  }

  async function updateReviewSettings({ intervalMinutes, urgentPaneErrors } = {}) {
    const body = {};
    if (intervalMinutes !== undefined) body.interval_minutes = intervalMinutes;
    if (urgentPaneErrors !== undefined) body.urgent_pane_errors = Boolean(urgentPaneErrors);
    const payload = await request("/review/settings", body, "PATCH");
    const review = snapshot.review
      ? { ...snapshot.review, settings: normalizeReview({ settings: payload?.settings || payload }).settings }
      : snapshot.review;
    publish({ review });
    await loadReview();
    return snapshot.review?.settings || null;
  }

  async function sendMessage(id, content) {
    const agent = snapshot.details[id] || snapshot.agentMap[id] || {};
    const expectedBindingRevision = Number(agent.binding?.revision ?? agent.binding_revision ?? 0);
    const body = {
      text: content,
      client_message_id: uid(),
      expected_binding_revision: expectedBindingRevision,
    };
    const key = `message:${id}`;
    setMutation(key, { status: "pending" });
    try {
      const payload = await request(`/agents/${encodeURIComponent(id)}/messages`, body, "POST");
      const message = normalizeMessage(payload?.message || payload);
      const messages = [...(snapshot.messages[id] || []), message];
      patchMap("messages", id, messages);
      setMutation(key, { status: "success" });
      scheduleRefresh(["agents"]);
      return message;
    } catch (error) {
      setMutation(key, { status: "error", message: error?.userMessage || error?.message || "Message could not be sent." });
      throw error;
    }
  }

  async function replyDecision(decision, { optionId = "", customText = "" } = {}) {
    const key = `decision:${decision.id}`;
    const actionScope = scopeGeneration;
    setMutation(key, { status: "pending" });
    try {
      const payload = await request(`/decisions/${encodeURIComponent(decision.id)}/reply`, {
        option_id: optionId || null,
        custom_text: customText || null,
        idempotency_key: uid(),
        expected_revision: decision.revision,
        expected_binding_revision: decision.binding_revision,
        prompt_fingerprint: decision.prompt_fingerprint,
      }, "POST");
      if (actionScope !== scopeGeneration) {
        const error = new Error("The server changed while the response was being submitted.");
        error.code = "server_scope_changed";
        throw error;
      }
      const next = normalizeDecision(payload?.decision || payload);
      publish({ decisions: snapshot.decisions.map((item) => item.id === next.id ? next : item) });
      setMutation(key, { status: "success" });
      scheduleRefresh(["agents", "timeline"]);
      return next;
    } catch (error) {
      if (actionScope === scopeGeneration) {
        setMutation(key, { status: "error", message: error?.status === 409
          ? "This decision changed. Refresh it before replying." : error?.userMessage || error?.message || "Reply could not be sent." });
        if (error?.status === 409 || error?.status === 410) await loadDecision(decision.id).catch(() => null);
      }
      throw error;
    }
  }

  function reviewOwner(decision) {
    const group = snapshot.review?.groups?.find((item) => item.agent_id === decision.agent_id);
    return group?.agent || snapshot.details[decision.agent_id] || snapshot.agentMap[decision.agent_id] || null;
  }

  function stagePlanDecision(decision, optionId) {
    const draft = reviewDraftStore.stage(snapshot.serverInstanceId, decision, optionId);
    if (!draft) return null;
    publish({ planDrafts: reviewDraftStore.list(snapshot.serverInstanceId), planResults: [] });
    return draft;
  }

  function removePlanDraft(decisionId) {
    reviewDraftStore.remove(snapshot.serverInstanceId, decisionId);
    publish({ planDrafts: reviewDraftStore.list(snapshot.serverInstanceId) });
  }

  function resetReviewResults() {
    publish({ planResults: [] });
  }

  async function submitReviewDecision(staged) {
    const decisionID = staged?.decision_id || staged?.id || "";
    const submissionScope = scopeGeneration;
    let current;
    try {
      current = await loadDecision(decisionID);
    } catch (error) {
      return {
        decision_id: decisionID,
        status: error?.status === 404 || error?.status === 410 ? "conflict" : "terminal_required",
        message: error?.status === 404 || error?.status === 410
          ? "The decision is no longer available." : "The agent must be reviewed in its terminal.",
      };
    }
    if (submissionScope !== scopeGeneration || !current) {
      return {
        decision_id: decisionID,
        status: "terminal_required",
        message: "The server changed before this response could be verified.",
      };
    }
    const normalizedStage = staged?.decision_id ? staged : {
      decision_id: current.id,
      option_id: staged?.option_id || staged?.optionId,
      revision: staged?.revision,
      binding_revision: staged?.binding_revision,
      prompt_fingerprint: staged?.prompt_fingerprint,
      options_fingerprint: staged?.options_fingerprint,
      custom_text: staged?.custom_text || staged?.customText,
    };
    if (decisionDeliveryUncertain(current)) {
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: "terminal_required",
        message: "Delivery state is uncertain. Open the terminal before responding again.",
      };
    }
    if (!reviewDecisionMatches(normalizedStage, current)) {
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: "conflict",
        message: "The decision changed after it was reviewed.",
      };
    }
    const owner = reviewOwner(current);
    if (capabilityMode(owner, "decision_reply") !== "verified_terminal") {
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: "terminal_required",
        message: "The current terminal prompt cannot be safely answered here.",
      };
    }
    if (submissionScope !== scopeGeneration) {
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: "terminal_required",
        message: "The server changed before this response could be submitted.",
      };
    }
    try {
      const decision = await replyDecision(current, {
        optionId: normalizedStage.option_id,
        customText: normalizedStage.custom_text,
      });
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: "success",
        message: "Response submitted.",
        decision,
      };
    } catch (error) {
      if (error?.status === 409 || error?.status === 410) {
        let fresh = null;
        try { fresh = await loadDecision(current.id); } catch (_) { /* classified below */ }
        if (decisionDeliveryUncertain(fresh)) {
          return {
            decision_id: current.id,
            agent_id: current.agent_id,
            status: "terminal_required",
            message: "The reply may have been delivered, but its outcome is uncertain. Open the terminal before responding again.",
          };
        }
        if (fresh && (
          fresh.status !== "pending"
          || !reviewDecisionMatches(normalizedStage, fresh)
        )) {
          return {
            decision_id: current.id,
            agent_id: current.agent_id,
            status: "conflict",
            message: fresh.status === "pending"
              ? "The decision or terminal binding changed."
              : `The decision is now ${fresh.status}.`,
          };
        }
        if (error.status === 409) {
          return {
            decision_id: current.id,
            agent_id: current.agent_id,
            status: "terminal_required",
            message: "The reply may have been delivered, but its outcome could not be confirmed. Open the terminal before responding again.",
          };
        }
      }
      return {
        decision_id: current.id,
        agent_id: current.agent_id,
        status: error?.status === 410 ? "conflict" : "terminal_required",
        message: error?.status === 410
          ? "The decision is no longer available." : "Open the terminal to finish this item.",
      };
    }
  }

  function quickReply(decision, choice = {}) {
    const existing = quickReplyPromises.get(decision.id);
    if (existing) return existing;
    const selection = typeof choice === "string" ? { optionId: choice } : choice;
    const promise = (async () => {
      const staged = {
        decision_id: decision.id,
        option_id: selection.optionId || "",
        custom_text: text(selection.customText).trim(),
        revision: decision.revision,
        binding_revision: decision.binding_revision,
        prompt_fingerprint: decision.prompt_fingerprint,
        options_fingerprint: decision.options_fingerprint,
      };
      const result = await submitReviewDecision(staged);
      publish({ planResults: [result] });
      return result;
    })().finally(() => {
      if (quickReplyPromises.get(decision.id) === promise) {
        quickReplyPromises.delete(decision.id);
      }
    });
    quickReplyPromises.set(decision.id, promise);
    return promise;
  }

  async function submitPlanReview() {
    const drafts = [...snapshot.planDrafts];
    const results = [];
    publish({ planResults: [], mutations: { ...snapshot.mutations, plan: { status: "pending" } } });
    // Intentionally sequential: every item is re-fetched and may stop being
    // actionable while earlier human choices are applied.
    for (const draft of drafts) {
      const result = await submitReviewDecision(draft);
      results.push(result);
      publish({ planResults: [...results] });
      if (result.status === "success" || result.status === "conflict") {
        uncertainPlanDrafts.delete(draft.decision_id);
        reviewDraftStore.remove(snapshot.serverInstanceId, draft.decision_id);
        publish({ planDrafts: reviewDraftStore.list(snapshot.serverInstanceId) });
      } else if (result.status === "terminal_required") {
        uncertainPlanDrafts.add(draft.decision_id);
      }
    }
    setMutation("plan", { status: "success" });
    await loadReview().catch(() => null);
    return results;
  }

  async function bindAgent(id, paneId, expectedRevision = 0) {
    const key = `binding:${id}`;
    setMutation(key, { status: "pending" });
    try {
      const payload = await request(`/agents/${encodeURIComponent(id)}/binding`, {
        pane_id: paneId,
        expected_binding_revision: expectedRevision,
      }, "PUT");
      const detail = normalizeAgent(payload?.agent || payload);
      patchMap("details", id, detail);
      setMutation(key, { status: "success" });
      await loadAgents();
      return detail;
    } catch (error) {
      setMutation(key, { status: "error", message: error?.status === 409
        ? "The session binding changed. Review the current candidates." : error?.userMessage || error?.message || "Session could not be linked." });
      if (error?.status === 409) await loadAgent(id).catch(() => null);
      throw error;
    }
  }

  async function unbindAgent(id, expectedRevision = 0) {
    const key = `binding:${id}`;
    setMutation(key, { status: "pending" });
    try {
      await request(
        `/agents/${encodeURIComponent(id)}/binding?expected_binding_revision=${encodeURIComponent(expectedRevision)}`,
        null,
        "DELETE",
      );
      setMutation(key, { status: "success" });
      await Promise.all([loadAgent(id), loadAgents()]);
      return true;
    } catch (error) {
      setMutation(key, { status: "error", message: error?.userMessage || error?.message || "Session could not be unlinked." });
      throw error;
    }
  }

  function scheduleRefresh(resources = ["agents", "decisions", "timeline", "review"]) {
    if (!enabled || !running) return;
    if (refreshTimer !== null) globalThis.clearTimeout(refreshTimer);
    refreshTimer = globalThis.setTimeout(async () => {
      refreshTimer = null;
      const tasks = [];
      if (resources.includes("agents")) tasks.push(loadAgents());
      if (resources.includes("decisions")) tasks.push(loadDecisions());
      if (resources.includes("timeline")) tasks.push(loadTimeline());
      if (resources.includes("review") && reviewEnabled) tasks.push(loadReview());
      await Promise.allSettled(tasks);
    }, 100);
  }

  function socketUrl() {
    const url = new URL(origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/ws/agents";
    url.search = "";
    if (token) url.searchParams.set("token", token);
    if (snapshot.cursor) url.searchParams.set("cursor", snapshot.cursor);
    return url.toString();
  }

  function closeSocket() {
    if (reconnectTimer !== null) globalThis.clearTimeout(reconnectTimer);
    reconnectTimer = null;
    const active = socket;
    socket = null;
    if (active) {
      active.onopen = active.onmessage = active.onerror = active.onclose = null;
      try { active.close(1000, "agent workspace stopped"); } catch (_) { /* browser owns close errors */ }
    }
  }

  function reconnect() {
    if (!enabled || !running || !WebSocketImpl || reconnectTimer !== null) return;
    const delay = Math.min(10000, 500 * (2 ** reconnectAttempt));
    reconnectAttempt += 1;
    reconnectTimer = globalThis.setTimeout(() => { reconnectTimer = null; connectSocket(); }, delay);
  }

  function connectSocket() {
    if (!enabled || !running || socket || !WebSocketImpl) return;
    let next;
    try { next = new WebSocketImpl(socketUrl()); } catch (_) { reconnect(); return; }
    socket = next;
    next.onopen = () => {
      if (socket !== next) return;
      reconnectAttempt = 0;
      publish({ live: "live" });
    };
    next.onmessage = (event) => {
      if (socket !== next) return;
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (!isObject(message)) return;
      if (message.cursor !== undefined && message.cursor !== null) publish({ cursor: String(message.cursor) });
      const type = text(message.type);
      if (type === "hello" || type === "ping") return;
      if (type === "reset" || type === "rehydrate") { void hydrate(); return; }
      const envelopeEvent = isObject(message.event) ? message.event : message;
      const kind = text(envelopeEvent.kind || type);
      const resources = array(message.resources).length ? message.resources
        : kind.includes("review") ? ["review", "decisions", "agents"]
          : kind.includes("decision") ? ["decisions", "agents", "timeline", "review"]
          : kind.includes("timeline") || kind.includes("history") ? ["timeline", "agents", "review"]
            : ["agents", "decisions", "timeline", "review"];
      scheduleRefresh(resources);
      const agentId = identifier(envelopeEvent.agent_id || envelopeEvent.session_id);
      if (agentId && snapshot.details[agentId]) void loadAgent(agentId);
    };
    next.onerror = () => {};
    next.onclose = () => {
      if (socket !== next) return;
      socket = null;
      publish({ live: "offline" });
      reconnect();
    };
  }

  async function configure(config) {
    const capability = agentContextCapability(config);
    const reviewCapability = agentReviewCapability(config);
    const serverInstanceId = text(config?._info?.server_instance_id);
    if (!capability.enabled) {
      enabled = false;
      reviewEnabled = false;
      running = false;
      generation += 1;
      scopeGeneration += 1;
      messageRequestSequences.clear();
      quickReplyPromises.clear();
      uncertainPlanDrafts.clear();
      pendingVisits.clear();
      pendingReviews.clear();
      closeSocket();
      publish({
        enabled: false,
        reviewEnabled: false,
        serverInstanceId: "",
        status: "disabled",
        live: "offline",
        error: "",
        review: null,
        planDrafts: [],
        planResults: [],
        messagePages: {},
        loading: {},
        mutations: {},
      });
      return false;
    }
    const serverChanged = snapshot.enabled && serverInstanceId !== snapshot.serverInstanceId;
    const reviewCapabilityChanged = snapshot.enabled && reviewCapability.enabled !== snapshot.reviewEnabled;
    if (serverChanged || reviewCapabilityChanged) {
      generation += 1;
      scopeGeneration += 1;
      messageRequestSequences.clear();
      quickReplyPromises.clear();
      uncertainPlanDrafts.clear();
      pendingReviews.clear();
      if (serverChanged) {
        pendingVisits.clear();
        closeSocket();
      }
    }
    enabled = true;
    reviewEnabled = reviewCapability.enabled;
    running = true;
    publish({
      enabled: true,
      reviewEnabled,
      serverInstanceId,
      planDrafts: reviewEnabled ? reviewDraftStore.list(serverInstanceId) : [],
      planResults: serverChanged || reviewCapabilityChanged ? [] : snapshot.planResults,
      review: serverChanged || reviewCapabilityChanged ? null : snapshot.review,
      messagePages: serverChanged ? {} : snapshot.messagePages,
      agents: serverChanged ? [] : snapshot.agents,
      agentMap: serverChanged ? {} : snapshot.agentMap,
      decisions: serverChanged ? [] : snapshot.decisions,
      timeline: serverChanged ? [] : snapshot.timeline,
      details: serverChanged ? {} : snapshot.details,
      resume: serverChanged ? {} : snapshot.resume,
      messages: serverChanged ? {} : snapshot.messages,
      agentTimelines: serverChanged ? {} : snapshot.agentTimelines,
      cursor: serverChanged ? null : snapshot.cursor,
      mutations: serverChanged || reviewCapabilityChanged ? {} : snapshot.mutations,
      loading: serverChanged || reviewCapabilityChanged ? {} : snapshot.loading,
      status: snapshot.status === "disabled" || serverChanged ? "loading" : snapshot.status,
    });
    const loaded = await hydrate();
    connectSocket();
    return loaded;
  }

  function stop() {
    running = false;
    generation += 1;
    if (refreshTimer !== null) globalThis.clearTimeout(refreshTimer);
    refreshTimer = null;
    closeSocket();
    publish({ live: "offline" });
  }

  return Object.freeze({
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot() { return snapshot; },
    configure,
    stop,
    hydrate,
    loadAgent,
    loadDecision,
    loadReview,
    loadMessagePage,
    acknowledgeVisit,
    acknowledgeReview,
    updateReviewSettings,
    sendMessage,
    replyDecision,
    quickReply,
    stagePlanDecision,
    removePlanDraft,
    resetReviewResults,
    submitPlanReview,
    bindAgent,
    unbindAgent,
  });
}

export const agentStore = createAgentStore();

export function useAgentState(store = agentStore) {
  const ReactRuntime = globalThis.React;
  if (!ReactRuntime?.useSyncExternalStore) throw new Error("React 18 useSyncExternalStore is required");
  return ReactRuntime.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
