/** Capability-gated Agent Context & Decision Inbox interface. */

import {
  Dialog,
  EmptyState,
  Fragment,
  Icon,
  InlineNotice,
  Spinner,
  cx,
  formatAge,
  html,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./core.js";
import { actionsAllowed, useActions } from "./state.js";
import {
  ImageUploadButton,
  ImageUploadStatus,
  appendTerminalText,
  useImageUpload,
} from "./image-upload.js";
import {
  AGENT_DESTINATIONS,
  LEGACY_AGENT_DESTINATIONS,
  agentContextCapability,
  agentReviewCapability,
  agentRoute,
  agentStore,
  capabilityMode,
  navigateAgentRoute,
  useAgentRoute,
  useAgentState,
} from "./agent-state.js";
import { ConnectionDetails, PaneDetail, Workspace, WorkspaceNav } from "./ui.js";

function connectionLabel(connection) {
  const labels = {
    live: "Live", updating_rest: "Updating via REST", rest: "Updating via REST",
    offline: "Offline", connecting: "Connecting", unauthorized: "Unauthorized",
    incompatible: "Incompatible",
  };
  return labels[connection?.mode] || "Connecting";
}

function AgentHeader({ connection, onConnection, onSettings, onCreate = null, compact = false }) {
  const label = connectionLabel(connection);
  return html`<header class=${cx("agent-brand-header", compact && "compact")}>
    <div class="brand-lockup"><span>v</span><strong>mux</strong></div>
    <span class="agent-product-name">Agent workspace</span>
    <span class="spacer"></span>
    <button type="button" class="agent-connection" aria-label=${`Connection: ${label}. Show details`} onClick=${onConnection}>
      <span aria-hidden="true"></span>${compact ? null : label}
    </button>
    ${onCreate ? html`<button type="button" class="icon-button" aria-label="Create tmux target" title="Create" onClick=${() => onCreate({})}><${Icon} name="plus" /></button>` : null}
    <button type="button" class="icon-button" title="Settings" onClick=${onSettings}><${Icon} name="settings" /></button>
  </header>`;
}

function AgentStatus({ agent }) {
  const lifecycle = agent.lifecycle || "unknown";
  const attention = agent.blockers?.length || agent.pending_decisions_count || lifecycle === "waiting";
  const tone = lifecycle === "error" ? "error" : attention ? "attention" : lifecycle === "working" || lifecycle === "running" ? "working" : "idle";
  return html`<span class=${cx("agent-status", `agent-status-${tone}`)}><span aria-hidden="true"></span>${lifecycle.replaceAll("_", " ")}</span>`;
}

function CapabilityPill({ label, value }) {
  const available = value && value !== "unavailable" && value !== "unknown";
  return html`<span class=${cx("capability-pill", available ? "available" : "unavailable")}>
    <${Icon} name=${available ? "check-circle" : "circle-alert"} size=${13} />${label}: ${String(value || "unavailable").replaceAll("_", " ")}
  </span>`;
}

function ProgressMeter({ progress }) {
  const percent = Number(progress?.percent);
  if (!Number.isFinite(percent)) return html`<div class="agent-progress indeterminate" role="progressbar" aria-label="Progress is not available"><span></span></div>`;
  return html`<div class="agent-progress" role="progressbar" aria-label="Agent progress" aria-valuenow=${Math.round(percent)} aria-valuemin="0" aria-valuemax="100">
    <span style=${{ width: `${Math.max(0, Math.min(100, percent))}%` }}></span>
  </div>`;
}

function AgentCard({ agent, selected = false, onOpen }) {
  const pending = Number(agent.pending_decisions_count || agent.decisions_pending || 0);
  return html`<article class=${cx("agent-card", selected && "selected")}>
    <button type="button" class="agent-card-open" onClick=${() => onOpen(agent.id)}>
      <header><span class="agent-avatar"><${Icon} name="bot" size=${21} /></span><div><strong>${agent.name}</strong><span>${agent.runtime_display_name}</span></div><${AgentStatus} agent=${agent} /></header>
      <h3>${agent.current_task || agent.goal || "Waiting for structured context"}</h3>
      <${ProgressMeter} progress=${agent.progress} />
      <footer><span>${agent.progress_summary || agent.next_action || "No progress summary yet"}</span><time>${formatAge(agent.last_updated)}</time></footer>
      ${pending ? html`<span class="agent-attention"><${Icon} name="circle-alert" size=${15} />${pending} decision${pending === 1 ? "" : "s"}</span>` : null}
    </button>
  </article>`;
}

function AgentList({ agents, selectedId = "", onOpen }) {
  if (!agents.length) return html`<${EmptyState} icon="bot" title="No structured agents yet" detail="Start Codex or Claude Code in a discovered tmux pane. vmux will show it here after a native session is associated." />`;
  return html`<div class="agent-card-list">${agents.map((agent) => html`<${AgentCard} key=${agent.id} agent=${agent} selected=${agent.id === selectedId} onOpen=${onOpen} />`)}</div>`;
}

function listText(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "Update";
  if (value.from !== undefined || value.to !== undefined) {
    const from = value.from || "Not set";
    const to = value.to || "Not set";
    return `${from} → ${to}`;
  }
  return value.title || value.label || value.text || value.description || "Update";
}

function ChangeList({ resume, empty = "Nothing new since the shared visit baseline." }) {
  const delta = resume?.changes || resume?.delta || {};
  const groups = [
    ["completed", "Completed", "circle-check"],
    ["completed_items", "Completed", "circle-check"],
    ["new_blockers", "New blockers", "triangle-alert"],
    ["decisions_added", "Needs your decision", "circle-alert"],
    ["resolved_blockers", "Resolved blockers", "check-circle"],
    ["current_task_changed", "Task changed", "activity"],
    ["next_action_changed", "Next step changed", "activity"],
    ["goal_changed", "Goal changed", "activity"],
    ["lifecycle_changed", "Agent state changed", "activity"],
  ];
  const seen = new Set();
  const rows = [];
  for (const [key, label, icon] of groups) {
    const values = Array.isArray(delta?.[key]) ? delta[key] : delta?.[key] ? [delta[key]] : [];
    for (const value of values) {
      const rowKey = `${label}:${listText(value)}`;
      if (seen.has(rowKey)) continue;
      seen.add(rowKey);
      rows.push({ label, icon, text: listText(value), tone: key.includes("blocker") || key.includes("decision") ? "attention" : "success" });
    }
  }
  if (!rows.length && Array.isArray(resume?.changes)) {
    for (const value of resume.changes) rows.push({ label: "Changed", icon: "activity", text: listText(value), tone: "neutral" });
  }
  if (!rows.some((row) => row.label === "Needs your decision")) {
    for (const decision of (resume?.pending_decisions || resume?.decisions || [])) {
      rows.push({ label: "Needs your decision", icon: "circle-alert", text: listText(decision), tone: "attention" });
    }
  }
  if (!rows.length) return html`<p class="agent-muted">${empty}</p>`;
  return html`<ul class="change-list">${rows.map((row, index) => html`<li class=${`change-${row.tone}`} key=${`${row.label}-${index}`}><${Icon} name=${row.icon} size=${17} /><div><span>${row.label}</span><strong>${row.text}</strong></div></li>`)}</ul>`;
}

function ContextList({ title, items, empty, tone = "neutral" }) {
  return html`<section class=${cx("context-section", `context-${tone}`)}><h3>${title}</h3>
    ${items?.length ? html`<ul>${items.map((item, index) => html`<li key=${item.id || index}>${listText(item)}</li>`)}</ul>` : html`<p>${empty}</p>`}
  </section>`;
}

function BindingPanel({ agent, state }) {
  const binding = agent.binding || {};
  // Candidate omission is a safety signal. Never substitute the general pane
  // inventory: only the backend can associate a runtime session with a pane.
  const candidates = Array.isArray(binding.candidates) ? binding.candidates : [];
  const candidateIds = candidates.map((candidate) => String(candidate?.pane_id || candidate?.id || candidate || "")).filter(Boolean);
  const candidateKey = candidateIds.join("\0");
  const [selected, setSelected] = useState(() => String(candidates[0]?.pane_id || candidates[0]?.id || candidates[0] || ""));
  useEffect(() => {
    setSelected((current) => candidateIds.includes(current) ? current : candidateIds[0] || "");
  }, [agent.id, candidateKey]);
  const mutation = state.mutations[`binding:${agent.id}`] || {};
  const needsChoice = ["ambiguous", "probable"].includes(agent.association || binding.association) || (!binding.pane_id && candidates.length);
  if (!needsChoice && binding.pane_id) return html`<div class="binding-summary"><div><strong>Linked terminal</strong><span>${binding.target || binding.pane_id}</span></div><button class="text-button danger" disabled=${mutation.status === "pending"} onClick=${() => agentStore.unbindAgent(agent.id, binding.revision).catch(() => null)}>Unlink</button></div>`;
  if (!needsChoice) return html`<${InlineNotice} tone="warning" icon="link"><strong>No live terminal linked</strong><p>Context remains readable, but chat and decision replies are unavailable.</p><//>`;
  if (!candidates.length) return html`<section class="binding-panel binding-panel-empty"><div><p class="eyebrow">Association needs review</p><h3>No verified pane candidates</h3><p>vmux cannot safely link this session yet. Open the terminal workspace to inspect the agent, then refresh after the backend reports a matching candidate.</p></div><a class="button secondary" href=${agentRoute("panes")}><${Icon} name="square-terminal" size=${17} />Open terminal workspace</a></section>`;
  const selectedIsCandidate = candidateIds.includes(selected);
  return html`<section class="binding-panel"><div><p class="eyebrow">Association needs review</p><h3>Choose the running pane</h3><p>vmux found more than one plausible session. Linking is required before it can send input.</p></div>
    <label><span>Candidate pane</span><select value=${selected} onChange=${(event) => setSelected(event.target.value)}>
      ${candidates.map((candidate, index) => {
        const paneId = String(candidate?.pane_id || candidate?.id || candidate || "");
        const label = candidate?.name || candidate?.target || paneId || `Candidate ${index + 1}`;
        return html`<option key=${paneId || index} value=${paneId}>${label}${candidate?.confidence ? ` · ${candidate.confidence}` : ""}</option>`;
      })}
    </select></label>
    <button class="button primary" disabled=${!selectedIsCandidate || mutation.status === "pending"} onClick=${() => selectedIsCandidate && agentStore.bindAgent(agent.id, selected, binding.revision).catch(() => null)}>${mutation.status === "pending" ? "Linking…" : "Link session"}</button>
    ${mutation.status === "error" ? html`<p class="form-error" role="alert">${mutation.message}</p>` : null}
  </section>`;
}

function MessageList({ messages }) {
  if (!messages?.length) return html`<div class="chat-empty"><${Icon} name="messages-square" size=${25} /><p>No visible messages have been captured for this session yet.</p></div>`;
  return html`<ol class="agent-messages">${messages.map((message) => html`<li class=${cx(`message-${message.role}`, `message-${message.status}`)} key=${message.id}>
    <div><strong>${message.role === "user" ? "You" : message.role === "assistant" ? "Agent" : message.role}</strong><span>${message.status.replaceAll("_", " ")}</span></div>
    <p>${message.content}</p>
  </li>`)}</ol>`;
}

function AgentChat({ agent, messages, composerRef, connection, pane }) {
  const [value, setValue] = useState("");
  const mutation = useAgentState().mutations[`message:${agent.id}`] || {};
  const chatMode = capabilityMode(agent, "chat_send");
  const allowed = chatMode === "idle_only" && Boolean(pane) && actionsAllowed(connection, pane);
  const imageUpload = useImageUpload({
    enabled: allowed && mutation.status !== "pending",
    onInsert: (terminalText) => setValue((current) => appendTerminalText(current, terminalText)),
    onFocus: () => composerRef.current?.focus(),
  });
  const send = async () => {
    const content = value.trim();
    if (!content || !allowed || mutation.status === "pending" || imageUpload.busy) return;
    try { await agentStore.sendMessage(agent.id, content); setValue(""); } catch (_) { /* mutation state renders the failure */ }
  };
  useEffect(() => {
    const focus = (event) => {
      if (event.detail?.id && event.detail.id !== agent.id) return;
      setValue("");
      requestAnimationFrame(() => composerRef.current?.focus());
    };
    globalThis.addEventListener?.("vmux:focus-agent-chat", focus);
    return () => globalThis.removeEventListener?.("vmux:focus-agent-chat", focus);
  }, [agent.id, composerRef]);
  return html`<section class="agent-chat" aria-label="Agent chat">
    <header><div><h2>Chat</h2><p>Messages go to this running agent session.</p></div><${CapabilityPill} label="Send" value=${chatMode} /></header>
    <${MessageList} messages=${messages} />
    <div class="suggested-prompts" aria-label="Suggested prompts">${["Summarize changes", "What remains?", "Explain the blockers", "Continue from current context"].map((prompt) => html`<button type="button" key=${prompt} disabled=${!allowed} onClick=${() => { setValue(prompt); composerRef.current?.focus(); }}>${prompt}</button>`)}</div>
    <div class="agent-composer">
      <label class="agent-composer-field"><span class="sr-only">Message this agent</span><textarea ref=${composerRef} rows="3" value=${value} placeholder=${allowed ? "Message the active agent…" : "Chat requires a confirmed idle agent prompt"} disabled=${!allowed} onInput=${(event) => setValue(event.target.value)} onPaste=${imageUpload.onPaste} onKeyDown=${(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }}></textarea></label>
      <div class="composer-actions"><${ImageUploadButton} upload=${imageUpload} />
        <button type="button" class="button primary" disabled=${!allowed || !value.trim() || mutation.status === "pending" || imageUpload.busy} onClick=${send}><${Icon} name="send" size=${17} />${mutation.status === "pending" ? "Sending…" : "Send"}</button>
      </div>
    </div>
    <${ImageUploadStatus} upload=${imageUpload} />
    ${mutation.status === "error" ? html`<p class="form-error" role="alert">${mutation.message}</p>` : null}
  </section>`;
}

function TimelineList({ events, empty = "No structured activity has been recorded yet." }) {
  if (!events?.length) return html`<${EmptyState} icon="clock" title="No timeline yet" detail=${empty} />`;
  return html`<ol class="agent-timeline-list">${events.map((event) => html`<li key=${event.id}><span class="timeline-marker"><${Icon} name=${event.type.includes("decision") ? "circle-alert" : event.type.includes("complete") ? "check-circle" : "activity"} size=${15} /></span><div><time>${event.occurred_at ? new Date(event.occurred_at * 1000).toLocaleString() : "Time unavailable"}</time><strong>${event.title}</strong>${event.description ? html`<p>${event.description}</p>` : null}</div></li>`)}</ol>`;
}

function dateInputEpoch(value, endOfDay = false) {
  if (!value) return null;
  const date = new Date(`${value}T${endOfDay ? "23:59:59.999" : "00:00:00"}`);
  return Number.isFinite(date.getTime()) ? date.getTime() / 1000 : null;
}

function SemanticActivity({ events }) {
  if (!events?.length) return html`<${EmptyState} icon="clock" title="No semantic activity yet" detail="Only retained, user-visible agent activity appears here." />`;
  return html`<ol class="semantic-activity">${events.map((event) => html`<li key=${event.id}>
    <header><time>${event.occurred_at ? new Date(event.occurred_at * 1000).toLocaleString() : "Time unavailable"}</time><strong>${event.title}</strong></header>
    <${ChangeList} resume=${{ changes: event.delta || {} }} empty="No semantic changes were retained for this event." />
  </li>`)}</ol>`;
}

function DeepContext({ agent, group, state, onClose }) {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sinceReview, setSinceReview] = useState(false);
  const page = state.messagePages[agent.id] || {};
  const timeline = state.agentTimelines[agent.id] || [];
  const reviewedAt = group?.reviewed_snapshot_at
    ?? page.reviewed_snapshot_at
    ?? group?.reviewed_at
    ?? page.reviewed_at;
  const after = sinceReview && reviewedAt
    ? reviewedAt : dateInputEpoch(fromDate);
  const before = dateInputEpoch(toDate, true);

  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      agentStore.loadMessagePage(agent.id, { q: query.trim(), role, after, before }).catch(() => null);
    }, 250);
    return () => globalThis.clearTimeout(timer);
  }, [agent.id, query, role, after, before]);

  const sections = useMemo(() => {
    const values = [];
    let key = "";
    for (const message of page.messages || []) {
      const date = message.created_at ? new Date(message.created_at * 1000) : null;
      const nextKey = date ? date.toLocaleDateString() : "Time unavailable";
      if (nextKey !== key) {
        key = nextKey;
        values.push({ key, messages: [] });
      }
      values[values.length - 1].messages.push(message);
    }
    return values;
  }, [page.messages]);

  return html`<${Dialog} title="Deep Context" subtitle=${agent.name} onClose=${onClose} className="deep-context-dialog">
    <div class="deep-context">
      <${InlineNotice} icon="check-circle"><strong>Visible context only</strong><p>vmux does not retain hidden reasoning, tool results, commands, or raw terminal scrollback.</p><//>
      <section class="deep-context-section">
        <header><div><p class="eyebrow">Retained transcript</p><h2>Conversation</h2></div>
          ${reviewedAt ? html`<button type="button" class=${cx("button", sinceReview && "primary")} aria-pressed=${sinceReview} onClick=${() => setSinceReview(!sinceReview)}>Since last review</button>` : null}
        </header>
        <div class="deep-context-filters">
          <label><span>Search</span><input type="search" value=${query} placeholder="Search visible messages" onInput=${(event) => setQuery(event.target.value)} /></label>
          <label><span>Role</span><select value=${role} onChange=${(event) => setRole(event.target.value)}><option value="">All roles</option><option value="user">You</option><option value="assistant">Agent</option></select></label>
          <label><span>From</span><input type="date" value=${fromDate} disabled=${sinceReview} onInput=${(event) => setFromDate(event.target.value)} /></label>
          <label><span>To</span><input type="date" value=${toDate} onInput=${(event) => setToDate(event.target.value)} /></label>
        </div>
        ${state.loading[`messages:${agent.id}`] && !page.messages?.length ? html`<div class="agent-loading"><${Spinner} label="Searching conversation" />Loading retained messages…</div>` : null}
        ${page.error ? html`<${InlineNotice} tone="warning" icon="triangle-alert"><strong>Conversation is partially available</strong><p>${page.error}</p><//>` : null}
        ${sections.length ? html`<div class="conversation-sections">${sections.map((section) => html`<section key=${section.key}><h3>${section.key}</h3><ol class="agent-messages">${section.messages.map((message) => html`<li class=${cx(`message-${message.role}`, `message-${message.status}`)} key=${message.id}>
          <div><strong>${message.role === "user" ? "You" : "Agent"}</strong><time>${message.created_at ? new Date(message.created_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "Time unavailable"}</time></div><p>${message.content}</p>
        </li>`)}</ol></section>`)}</div>` : html`<${EmptyState} icon="messages-square" title="No matching visible messages" detail="Try a different search, role, or date range." />`}
        ${page.history_truncated ? html`<p class="retention-note"><${Icon} name="clock-alert" size=${15} />Older transcript content is outside the server’s retention boundary.</p>` : null}
        ${page.next_cursor ? html`<button type="button" class="button secondary" disabled=${state.loading[`messages:${agent.id}`]} onClick=${() => agentStore.loadMessagePage(agent.id, {
          ...page.filters, cursor: page.next_cursor, append: true,
        }).catch(() => null)}>Load older retained messages</button>` : null}
      </section>
      <section class="deep-context-section"><header><div><p class="eyebrow">Semantic history</p><h2>Activity</h2></div></header><${SemanticActivity} events=${timeline} /></section>
    </div>
  <//>`;
}

function TerminalFallback({ pane, connection, onClose }) {
  const actions = useActions();
  return html`<${Dialog} title=${pane.name || pane.target || "Terminal"} subtitle="Terminal fallback" onClose=${onClose} className="terminal-dialog"><${PaneDetail} pane=${pane} actions=${actions} connection=${connection} /><//>`;
}

function AgentDetail({ id, state, panes, connection, compact = false }) {
  const summary = state.agentMap[id];
  const agent = state.details[id] || summary;
  const resume = state.resume[id];
  const messages = state.messages[id] || [];
  const timeline = state.agentTimelines[id] || [];
  const loading = state.loading[`agent:${id}`];
  const composer = useRef(null);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [deepContextOpen, setDeepContextOpen] = useState(false);
  const paneId = agent?.binding?.pane_id || agent?.pane_id;
  const pane = panes.find((item) => item.id === paneId) || null;
  const reviewGroup = state.review?.groups?.find((group) => group.agent_id === id) || null;

  useEffect(() => { if (id) agentStore.loadAgent(id).catch(() => null); }, [id]);
  useEffect(() => {
    if (!id || state.reviewEnabled || !resume?.as_of_snapshot_id) return;
    // A v1 server has no explicit review baseline, so retain its visit contract.
    agentStore.acknowledgeVisit(id, resume.as_of_snapshot_id).catch(() => null);
  }, [id, state.reviewEnabled, resume?.as_of_snapshot_id]);

  if (!agent && loading) return html`<div class="agent-loading"><${Spinner} label="Loading agent" />Loading agent context…</div>`;
  if (!agent) return html`<${EmptyState} icon="search-x" title="Agent not found" detail="The session may have expired or moved outside the retention window." />`;

  const context = resume?.context || resume?.current_context || resume?.agent?.context || agent;
  const changeSummary = state.reviewEnabled
    ? {
        changes: reviewGroup?.changes || {},
        pending_decisions: reviewGroup?.decisions || [],
        history_truncated: Boolean(reviewGroup?.history_truncated),
      }
    : resume;
  const association = agent.association || agent.binding?.association || "unavailable";
  const openResume = () => {
    // Resume is deliberately non-mutating: it clears and focuses the composer.
    globalThis.dispatchEvent?.(new CustomEvent("vmux:focus-agent-chat", { detail: { id: agent.id } }));
    requestAnimationFrame(() => composer.current?.scrollIntoView({ behavior: "smooth", block: "center" }));
  };
  return html`<article class=${cx("agent-detail", compact && "compact")}>
    <header class="agent-detail-head"><div><a class="agent-back" href=${agentRoute("agents")}><${Icon} name="chevron-left" size=${17} />Agents</a><h1>${agent.name}</h1><p>${agent.runtime_display_name} · updated ${formatAge(agent.last_updated)}</p></div><span class="spacer"></span>
      <button type="button" class="button secondary" onClick=${() => setDeepContextOpen(true)}><${Icon} name="notebook-tabs" size=${17} />Deep Context</button>
      ${reviewGroup?.as_of_snapshot_id ? html`<button type="button" class="button secondary" disabled=${state.mutations[`review:${id}`]?.status === "pending"} onClick=${() => agentStore.acknowledgeReview(id, reviewGroup.as_of_snapshot_id).catch(() => null)}>Mark reviewed</button>` : null}
      <${AgentStatus} agent=${agent} />
    </header>
    <div class="agent-detail-scroll">
      ${state.status === "degraded" ? html`<${InlineNotice} tone="warning" icon="triangle-alert"><strong>Partial agent data</strong><p>${state.error}</p><//>` : null}
      ${association !== "confirmed" ? html`<${InlineNotice} tone="warning" icon="shield-question"><strong>${association === "ambiguous" ? "Session association is ambiguous" : "Read-only session context"}</strong><p>vmux will not send terminal input until the native session and live prompt are verified.</p><//>` : null}
      <${BindingPanel} agent=${agent} state=${state} />

      <section class="resume-card"><header><div><p class="eyebrow">Welcome back</p><h2>${context.goal || "Current agent context"}</h2></div><button type="button" class="button primary" onClick=${openResume}><${Icon} name="play" size=${17} />Resume</button></header>
        <dl><div><dt>Current task</dt><dd>${context.current_task || "Not reported"}</dd></div><div><dt>Next step</dt><dd>${context.next_action || "Not reported"}</dd></div><div><dt>Progress</dt><dd>${context.progress_summary || "No stable progress estimate"}</dd></div><div><dt>ETA</dt><dd>${context.estimated_completion || "Not available"}</dd></div></dl>
        <${ProgressMeter} progress=${context.progress} />
        ${changeSummary?.history_truncated ? html`<p class="retention-note"><${Icon} name="clock-alert" size=${15} />Older changes were removed by the retention policy.</p>` : null}
      </section>

      <section class="changed-card"><p class="eyebrow">${state.reviewEnabled ? "Since your last review" : "Since you left"}</p><h2>What’s changed</h2><${ChangeList} resume=${changeSummary} empty=${state.reviewEnabled ? "No semantic changes since the review baseline." : "Nothing new since the shared visit baseline."} /></section>
      <div class="context-grid"><${ContextList} title="Completed" items=${context.completed_items || agent.completed_items} empty="No completed items reported." tone="success" /><${ContextList} title="Blockers" items=${context.blockers || agent.blockers} empty="No blockers reported." tone="attention" /></div>

      <section class="agent-capabilities"><h2>Connection health</h2><div><${CapabilityPill} label="Association" value=${association} /><${CapabilityPill} label="Context" value=${capabilityMode(agent, "context")} /><${CapabilityPill} label="Chat" value=${capabilityMode(agent, "chat_send")} /><${CapabilityPill} label="Decisions" value=${capabilityMode(agent, "decision_reply")} /></div><p>Extraction: ${agent.extraction_health || "unknown"}. Features are controlled by reported capabilities, not the runtime name.</p></section>
      <${AgentChat} agent=${agent} messages=${messages} composerRef=${composer} connection=${connection} pane=${pane} />
      <section class="agent-local-timeline"><header><h2>Recent activity</h2><a href=${agentRoute("timeline", agent.id)}>Full timeline</a></header><${TimelineList} events=${timeline.slice(0, 8)} /></section>
      ${pane ? html`<section class="terminal-fallback"><div><h2>Terminal fallback</h2><p>Open the live pane for output and controls that are not available as structured actions.</p></div><button type="button" class="button secondary" onClick=${() => setTerminalOpen(true)}><${Icon} name="square-terminal" size=${17} />Open terminal</button></section>` : null}
    </div>
    ${terminalOpen && pane ? html`<${TerminalFallback} pane=${pane} connection=${connection} onClose=${() => setTerminalOpen(false)} />` : null}
    ${deepContextOpen ? html`<${DeepContext} agent=${agent} group=${reviewGroup} state=${state} onClose=${() => setDeepContextOpen(false)} />` : null}
  </article>`;
}

function DecisionCard({ decision, selected = false, onOpen }) {
  return html`<button type="button" class=${cx("decision-card", selected && "selected")} onClick=${() => onOpen(decision.id)}>
    <span class=${cx("decision-priority", `priority-${decision.priority}`)}>${decision.priority}</span><div><strong>${decision.title}</strong><span>${decision.description || "Open for context and options"}</span><small>${formatAge(decision.created_at)}</small></div><span class=${`decision-state state-${decision.status}`}>${decision.status}</span><${Icon} name="chevron-right" size=${17} />
  </button>`;
}

function DecisionList({ decisions, selectedId = "", onOpen }) {
  if (!decisions.length) return html`<${EmptyState} icon="circle-check" title="Decision inbox is clear" detail="Verified questions from running agents will appear here." />`;
  return html`<div class="decision-list">${decisions.map((decision) => html`<${DecisionCard} key=${decision.id} decision=${decision} selected=${decision.id === selectedId} onOpen=${onOpen} />`)}</div>`;
}

function DecisionDetail({ id, state }) {
  const decision = state.decisions.find((item) => item.id === id);
  const [optionId, setOptionId] = useState("");
  const [custom, setCustom] = useState("");
  const mutation = state.mutations[`decision:${id}`] || {};
  useEffect(() => { if (id) agentStore.loadDecision(id).catch(() => null); }, [id]);
  useEffect(() => { setOptionId(""); setCustom(""); }, [id, decision?.revision]);
  if (!decision && state.loading[`decision:${id}`]) return html`<div class="agent-loading"><${Spinner} />Loading decision…</div>`;
  if (!decision) return html`<${EmptyState} icon="search-x" title="Decision not found" detail="It may have been resolved, expired, or removed by retention." />`;
  const pending = decision.status === "pending";
  const owner = state.details[decision.agent_id] || state.resume[decision.agent_id]?.agent || state.agentMap[decision.agent_id];
  const decisionMode = capabilityMode(owner, "decision_reply");
  const canSubmit = pending && decisionMode === "verified_terminal";
  const submit = () => canSubmit && agentStore.replyDecision(decision, { optionId, customText: custom.trim() }).catch(() => null);
  const askMore = () => {
    navigateAgentRoute("agents", decision.agent_id);
    setTimeout(() => globalThis.dispatchEvent?.(new CustomEvent("vmux:focus-agent-chat", { detail: { id: decision.agent_id } })), 40);
  };
  return html`<article class="decision-detail"><header><div><a class="agent-back" href=${agentRoute("decisions")}><${Icon} name="chevron-left" size=${17} />Inbox</a><p class="eyebrow">${decision.kind.replaceAll("_", " ")}</p><h1>${decision.title}</h1></div><span class=${cx("decision-priority", `priority-${decision.priority}`)}>${decision.priority}</span></header>
    <div class="decision-detail-scroll"><section class="decision-context"><h2>Context</h2><p>${decision.description || "The agent did not provide additional context."}</p></section>
      ${decision.options.length ? html`<fieldset class="decision-options" disabled=${!canSubmit || mutation.status === "pending"}><legend>Choose a response</legend>${decision.options.map((option) => html`<label class=${cx(option.destructive && "destructive", option.recommended && "recommended")} key=${option.id}><input type="radio" name=${`decision-${decision.id}`} value=${option.id} checked=${optionId === option.id} onChange=${() => { setOptionId(option.id); setCustom(""); }} /><span><strong>${option.label}${option.recommended ? html`<em>Recommended</em>` : null}</strong>${option.description ? html`<small>${option.description}</small>` : null}</span></label>`)}</fieldset>` : null}
      ${decision.allow_custom ? html`<label class="custom-decision"><span>Custom response</span><textarea rows="4" value=${custom} disabled=${!canSubmit || mutation.status === "pending"} placeholder="Tell the agent what you want to do…" onInput=${(event) => { setCustom(event.target.value); if (event.target.value) setOptionId(""); }}></textarea></label>` : null}
      ${pending && !canSubmit ? html`<${InlineNotice} tone="warning" icon="shield-question"><strong>Verified terminal reply unavailable</strong><p>The backend has not confirmed this decision against the agent’s current terminal prompt. Open the agent or terminal and refresh before responding.</p><//>` : null}
      ${!pending ? html`<${InlineNotice} icon="check-circle"><strong>This decision is ${decision.status}</strong><p>Refresh the inbox for the latest agent state.</p><//>` : null}
      ${mutation.status === "error" ? html`<p class="form-error" role="alert">${mutation.message}</p>` : null}
      <div class="decision-actions"><button type="button" class="button secondary" onClick=${askMore}><${Icon} name="message-square" size=${17} />Ask more</button><button type="button" class="button primary" disabled=${!canSubmit || mutation.status === "pending" || (!optionId && !custom.trim())} onClick=${submit}>${mutation.status === "pending" ? "Submitting…" : "Send response"}</button></div>
    </div>
  </article>`;
}

function DestinationHeading({ eyebrow, title, detail, action = null }) {
  return html`<header class="agent-page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1>${detail ? html`<p>${detail}</p>` : null}</div>${action}</header>`;
}

function AgentDestination({ state, route, panes, connection, layout }) {
  const open = (id) => navigateAgentRoute("agents", id);
  if (layout === "compact" && route.id) return html`<${AgentDetail} id=${route.id} state=${state} panes=${panes} connection=${connection} compact=${true} />`;
  return html`<div class=${cx("agent-master-detail", !route.id && "without-selection")}><section class="agent-master"><${DestinationHeading} eyebrow="Running workspace" title="Agents" detail="Resume work from structured context, not terminal scrollback." />${state.reviewEnabled ? html`<${ReviewSummary} review=${state.review} compact=${true} />` : null}<${AgentList} agents=${state.agents} selectedId=${route.id} onOpen=${open} /></section><main class="agent-inspector">${route.id ? html`<${AgentDetail} id=${route.id} state=${state} panes=${panes} connection=${connection} />` : html`<${EmptyState} icon="bot" title="Choose an agent" detail="Select a session to see its resume summary, decisions, chat, and timeline." />`}</main></div>`;
}

function DecisionDestination({ state, route, layout }) {
  const pending = state.decisions.filter((item) => item.status === "pending");
  const detail = `${pending.length} pending verified request${pending.length === 1 ? "" : "s"}.`;
  const open = (id) => navigateAgentRoute("decisions", id);
  if (layout === "compact" && route.id) return html`<${DecisionDetail} id=${route.id} state=${state} />`;
  return html`<div class=${cx("agent-master-detail", !route.id && "without-selection")}><section class="agent-master"><${DestinationHeading} eyebrow="Human decisions" title="Decision inbox" detail=${detail} /><${DecisionList} decisions=${state.decisions} selectedId=${route.id} onOpen=${open} /></section><main class="agent-inspector">${route.id ? html`<${DecisionDetail} id=${route.id} state=${state} />` : html`<${EmptyState} icon="inbox" title="Choose a decision" detail="Review the context, recommendation, and runtime-provided response options." />`}</main></div>`;
}

function nextReviewLabel(settings) {
  if (!settings?.enabled) return "Review timer off";
  if (!settings.next_due_at) return "Next review not scheduled";
  return `Next review ${new Date(settings.next_due_at * 1000).toLocaleString()}`;
}

function ReviewSummary({ review, compact = false }) {
  const counts = review?.counts || {};
  const metrics = [
    ["Agents changed", Number(counts.agents_changed || 0)],
    ["Pending decisions", Number(counts.pending_decisions || 0)],
    ["Terminal requests", Number(counts.terminal_requests || 0)],
  ];
  return html`<section class=${cx("review-summary", compact && "compact")} aria-label="Review summary">
    ${metrics.map(([label, value]) => html`<div key=${label}><strong>${value}</strong><span>${label}</span></div>`)}
    <div class="review-next"><${Icon} name="clock" size=${17} /><span>${nextReviewLabel(review?.settings)}</span></div>
  </section>`;
}

function ReviewSchedule({ settings }) {
  const serverSelected = settings?.enabled
    ? ([30, 60].includes(settings.interval_minutes) ? String(settings.interval_minutes) : "custom")
    : "off";
  const [custom, setCustom] = useState(
    settings?.interval_minutes && ![30, 60].includes(settings.interval_minutes)
      ? String(settings.interval_minutes) : "",
  );
  const [selected, setSelected] = useState(serverSelected);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setSelected(serverSelected);
    if (serverSelected === "custom") setCustom(String(settings.interval_minutes));
  }, [serverSelected, settings?.interval_minutes]);
  const update = async (value) => {
    if (working) return;
    setSelected(value);
    if (value === "custom") return;
    setWorking(true);
    setError("");
    try {
      await agentStore.updateReviewSettings({
        intervalMinutes: value === "off" ? null : Number(value),
      });
    } catch (reason) {
      setSelected(serverSelected);
      setError(reason?.userMessage || reason?.message || "The review schedule could not be updated.");
    }
    finally { setWorking(false); }
  };
  const saveCustom = async () => {
    const value = Number(custom);
    if (!Number.isInteger(value) || value < 5 || value > 1440 || working) return;
    setWorking(true);
    setError("");
    try { await agentStore.updateReviewSettings({ intervalMinutes: value }); }
    catch (reason) {
      setError(reason?.userMessage || reason?.message || "The review schedule could not be updated.");
    }
    finally { setWorking(false); }
  };
  const updatePaneErrors = async (value) => {
    if (working) return;
    setWorking(true);
    setError("");
    try { await agentStore.updateReviewSettings({ urgentPaneErrors: value }); }
    catch (reason) {
      setError(reason?.userMessage || reason?.message || "The urgent bypass could not be updated.");
    }
    finally { setWorking(false); }
  };
  return html`<details class="review-schedule"><summary><${Icon} name="clock" size=${17} />Review schedule</summary>
    <div><label><span>Batch normal requests</span><select value=${selected} disabled=${working} onChange=${(event) => update(event.target.value)}>
      <option value="off">Off — notify immediately</option><option value="30">Every 30 minutes</option><option value="60">Every 60 minutes</option><option value="custom">Custom interval</option>
    </select></label>
    ${selected === "custom" ? html`<label><span>Minutes (5–1440)</span><span class="inline-field"><input type="number" min="5" max="1440" value=${custom} onInput=${(event) => setCustom(event.target.value)} /><button type="button" class="button secondary" disabled=${working || Number(custom) < 5 || Number(custom) > 1440} onClick=${saveCustom}>Save</button></span></label>` : null}
    <label class="review-error-toggle"><input type="checkbox" checked=${Boolean(settings?.urgent_bypass?.pane_errors)} disabled=${working} onChange=${(event) => updatePaneErrors(event.target.checked)} />Let pane errors bypass the timer</label>
    ${error ? html`<p class="form-error" role="alert">${error}</p>` : null}
    <p>Explicit high and critical runtime decisions always bypass batching. vmux never infers urgency from prompt words.</p></div>
  </details>`;
}

function attentionLabel(value) {
  const labels = {
    critical_decision: "Critical decision",
    high_decision: "High-priority decision",
    error: "Agent error",
    extraction_error: "Context extraction degraded",
    pending_decision: "Oldest pending decision",
    new_blocker: "New blocker",
    semantic_change: "Changed since review",
  };
  return labels[value] || String(value || "Needs review").replaceAll("_", " ");
}

function ReviewDecisionChoices({
  decision,
  mode,
  selectedOptionID = "",
  onChoose,
  result,
  submitting = false,
}) {
  const actionable = decision.status === "pending";
  const [custom, setCustom] = useState("");
  useEffect(() => setCustom(""), [decision.id, decision.revision, mode]);
  const askMore = () => {
    navigateAgentRoute("agents", decision.agent_id);
    globalThis.setTimeout(() => globalThis.dispatchEvent?.(
      new CustomEvent("vmux:focus-agent-chat", { detail: { id: decision.agent_id } }),
    ), 40);
  };
  const optionRole = mode === "plan" ? "radio" : null;
  const optionsRole = mode === "plan" ? "radiogroup" : "group";
  const disabled = submitting || result?.status === "success";
  return html`<section class="review-decision">
    <header><div><span class=${cx("decision-priority", `priority-${decision.priority}`)}>${decision.priority}</span><h4>${decision.title}</h4></div><span class=${`decision-state state-${decision.status}`}>${decision.status}</span></header>
    ${decision.description ? html`<p>${decision.description}</p>` : null}
    ${actionable ? html`<div class="review-options" role=${optionsRole} aria-label=${`Responses for ${decision.title}`}>${decision.options.map((option) => html`<button type="button" role=${optionRole} aria-checked=${optionRole ? option.id === selectedOptionID : null} class=${cx(option.id === selectedOptionID && "selected", option.recommended && "recommended")} key=${option.id} disabled=${disabled} onClick=${() => { setCustom(""); onChoose(option.id, ""); }}>
      <span><strong>${option.label}</strong>${option.description ? html`<small>${option.description}</small>` : null}</span>${option.recommended ? html`<em>Recommended</em>` : null}<${Icon} name=${mode === "plan" ? "radio" : "send"} size=${16} />
    </button>`)}</div>` : html`<${InlineNotice} tone="warning" icon="square-terminal"><strong>Terminal review required</strong><p>This request is not currently safe to submit as a structured decision.</p><//>`}
    ${actionable && decision.allow_custom && mode === "quick" ? html`<label class="review-custom-response"><span>Custom response</span><textarea rows="3" value=${custom} disabled=${disabled} placeholder="Tell the agent what you want to do…" onInput=${(event) => setCustom(event.target.value)}></textarea><button type="button" class="button secondary" disabled=${disabled || !custom.trim()} onClick=${() => onChoose("", custom.trim())}>${submitting ? "Submitting…" : "Send custom response"}</button></label>` : null}
    ${actionable && decision.allow_custom && mode === "plan" ? html`<p class="review-custom-note">Custom text is available in Quick Review and is never stored in a Plan Review draft.</p>` : null}
    <div class="review-decision-actions"><button type="button" class="text-button" onClick=${askMore}><${Icon} name="message-square" size=${16} />Ask more</button></div>
    ${result ? html`<p class=${cx("review-result", `result-${result.status}`)} role="status"><${Icon} name=${result.status === "success" ? "check-circle" : result.status === "conflict" ? "refresh-cw" : "square-terminal"} size=${16} />${result.message}</p>` : null}
  </section>`;
}

function ReviewAgentCard({
  group,
  mode,
  drafts = [],
  results = [],
  submittingDecisionIDs = [],
  onQuick,
  onStage,
  onMark,
}) {
  const agent = group.agent;
  const context = agent.context || agent;
  const draftFor = (decision) => drafts.find((draft) => draft.decision_id === decision.id);
  const resultFor = (decision) => results.find((result) => result.decision_id === decision.id);
  return html`<article class="review-agent-card" data-agent-id=${group.agent_id}>
    <header><div><p class="eyebrow">${agent.runtime_display_name || agent.runtime}</p><h2>${agent.name}</h2></div><${AgentStatus} agent=${agent} /></header>
    <div class="review-reasons">${(group.attention_reasons.length ? group.attention_reasons : [group.rank_reason]).map((reason) => html`<span key=${reason}>${attentionLabel(reason)}</span>`)}</div>
    <dl class="review-context"><div><dt>Observed goal</dt><dd>${context.goal || "Not reported"}</dd></div><div><dt>Current task</dt><dd>${context.current_task || "Not reported"}</dd></div><div><dt>Next action</dt><dd>${context.next_action || "Not reported"}</dd></div><div><dt>Progress</dt><dd>${context.progress_summary || "No stable summary"}</dd></div></dl>
    <${ProgressMeter} progress=${context.progress} />
    <section class="review-changes"><h3>Changes since review</h3><${ChangeList} resume=${{ changes: group.changes, pending_decisions: [] }} empty="No semantic changes since the review baseline." />${group.history_truncated ? html`<p class="retention-note">Available history starts after the saved review baseline.</p>` : null}</section>
    <div class="context-grid"><${ContextList} title="Blockers" items=${context.blockers} empty="No blockers reported." tone="attention" /><${ContextList} title="Completed" items=${context.completed_items} empty="No completed items reported." tone="success" /></div>
    ${group.decisions.map((decision) => html`<${ReviewDecisionChoices} key=${decision.id} decision=${decision} mode=${mode} selectedOptionID=${draftFor(decision)?.option_id || ""} result=${resultFor(decision)} submitting=${submittingDecisionIDs.includes(decision.id)} onChoose=${(optionID, customText) => mode === "plan" ? onStage(decision, optionID) : onQuick(decision, { optionId: optionID, customText })} />`)}
    ${group.as_of_snapshot_id ? html`<footer><button type="button" class="text-button" onClick=${onMark}>Mark reviewed</button><span>Snapshot ${group.as_of_snapshot_sequence || "current"}</span></footer>` : null}
  </article>`;
}

function TerminalReview({ items, panes }) {
  if (!items.length) return null;
  return html`<section class="terminal-review"><header><div><p class="eyebrow">Unstructured requests</p><h2>Terminal review</h2><p>These panes do not expose a verified structured decision. Opening them does not acknowledge or answer anything.</p></div></header>
    <div>${items.map((item) => {
      const pane = panes.find((value) => value.id === item.pane_id);
      return html`<article key=${item.id}><div><${Icon} name=${item.status === "error" ? "triangle-alert" : "square-terminal"} /><span><strong>${pane?.name || "Terminal pane"}</strong><small>${item.status.replaceAll("_", " ")} · ${item.kind.replaceAll("-", " ")}</small></span></div><a class="button secondary" href=${agentRoute("panes", item.pane_id)}>Open Pane</a></article>`;
    })}</div>
  </section>`;
}

function ReviewDestination({ state, route, panes }) {
  const review = state.review;
  const [mode, setMode] = useState("");
  const [groups, setGroups] = useState([]);
  const [index, setIndex] = useState(0);
  const [quickResults, setQuickResults] = useState([]);
  const [processed, setProcessed] = useState([]);
  const [complete, setComplete] = useState(false);
  const [submittingPlan, setSubmittingPlan] = useState(false);
  const [startingMode, setStartingMode] = useState("");
  const [startError, setStartError] = useState("");
  const [quickSubmitting, setQuickSubmitting] = useState([]);
  const startingRef = useRef(false);
  const quickPendingRef = useRef(new Set());

  const begin = async (nextMode) => {
    if (startingRef.current) return;
    startingRef.current = true;
    setStartingMode(nextMode);
    setStartError("");
    try {
      const fresh = await agentStore.loadReview();
      if (!fresh) throw new Error("The review queue changed servers while it was loading.");
      const captured = [...fresh.groups];
      const focused = captured.findIndex((group) => group.agent_id === route.id
        || group.decisions.some((decision) => decision.id === route.id));
      agentStore.resetReviewResults();
      setGroups(captured);
      setIndex(focused >= 0 ? focused : 0);
      setQuickResults([]);
      setProcessed([]);
      setQuickSubmitting([]);
      quickPendingRef.current.clear();
      setComplete(captured.length === 0);
      setMode(nextMode);
    } catch (error) {
      setStartError(error?.userMessage || error?.message || "The review queue could not be refreshed.");
    } finally {
      startingRef.current = false;
      setStartingMode("");
    }
  };
  const current = groups[index] || null;
  const advance = (outcome) => {
    if (quickPendingRef.current.size) return;
    const nextProcessed = [...processed, { agent_id: current?.agent_id, outcome }];
    setProcessed(nextProcessed);
    const nextIndex = groups.findIndex((group, candidate) => candidate > index
      && !nextProcessed.some((value) => value.agent_id === group.agent_id));
    if (nextIndex >= 0) setIndex(nextIndex);
    else setComplete(true);
  };
  const acknowledge = async (group, outcome = "reviewed") => {
    if (mode === "quick" && quickPendingRef.current.size) return false;
    if (group?.as_of_snapshot_id) {
      try { await agentStore.acknowledgeReview(group.agent_id, group.as_of_snapshot_id); }
      catch (_) { return false; }
    }
    if (mode === "quick") advance(outcome);
    return true;
  };
  const quick = async (decision, choice) => {
    if (quickPendingRef.current.size) return;
    quickPendingRef.current.add(decision.id);
    setQuickSubmitting([...quickPendingRef.current]);
    try {
      const result = await agentStore.quickReply(decision, choice);
      const next = [...quickResults.filter((value) => value.decision_id !== decision.id), result];
      setQuickResults(next);
      if (result.status === "success" && current?.decisions.every((item) => next.some((value) => value.decision_id === item.id && value.status === "success"))) {
        quickPendingRef.current.delete(decision.id);
        setQuickSubmitting([]);
        await acknowledge(current, "answered");
      }
    } finally {
      quickPendingRef.current.delete(decision.id);
      setQuickSubmitting([...quickPendingRef.current]);
    }
  };
  const submitPlan = async () => {
    setSubmittingPlan(true);
    try {
      const results = await agentStore.submitPlanReview();
      for (const group of groups) {
        if (group.decisions.length && group.decisions.every((decision) => results.some((result) => result.decision_id === decision.id && result.status === "success"))) {
          if (group.as_of_snapshot_id) await agentStore.acknowledgeReview(group.agent_id, group.as_of_snapshot_id).catch(() => null);
        }
      }
      setComplete(true);
    } finally {
      setSubmittingPlan(false);
    }
  };
  const startAnother = () => {
    setMode("");
    setGroups([]);
    setIndex(0);
    setQuickResults([]);
    setProcessed([]);
    setComplete(false);
    setQuickSubmitting([]);
    quickPendingRef.current.clear();
    agentStore.resetReviewResults();
  };

  if (!review && state.status !== "error") return html`<div class="agent-loading"><${Spinner} label="Loading review" />Loading review queue…</div>`;
  if (!review) return html`<${EmptyState} icon="cloud-off" title="Review unavailable" detail=${state.error || "The current server could not provide a review queue."} />`;

  return html`<section class="review-destination">
    <${DestinationHeading} eyebrow="Human-in-the-loop" title="Review" detail="Recover context, handle verified decisions, and open terminal-only requests without automatic replies." action=${html`<button type="button" class="button secondary" onClick=${() => agentStore.loadReview().catch(() => null)}><${Icon} name="refresh-cw" size=${17} />Refresh</button>`} />
    <${ReviewSummary} review=${review} />
    <${ReviewSchedule} settings=${review.settings} />
    ${!mode ? html`<section class="review-mode-picker"><h2>Start a review session</h2><p>Both modes refetch every decision before sending through the existing terminal safety checks.</p><div>
      <button type="button" disabled=${Boolean(startingMode)} onClick=${() => { void begin("quick"); }}><${Icon} name="zap" size=${22} /><span><strong>${startingMode === "quick" ? "Refreshing…" : "Quick Review"}</strong><small>Answer one verified request, then advance.</small></span></button>
      <button type="button" disabled=${Boolean(startingMode)} onClick=${() => { void begin("plan"); }}><${Icon} name="list" size=${22} /><span><strong>${startingMode === "plan" ? "Refreshing…" : "Plan Review"}</strong><small>Stage metadata-only choices, inspect the set, then submit sequentially.</small></span></button>
    </div>${startError ? html`<p class="form-error" role="alert">${startError}</p>` : null}</section>` : null}
    ${mode === "quick" && !complete && current ? html`<div class="quick-review"><div class="review-session-progress"><strong>Agent ${index + 1} of ${groups.length}</strong><span>${processed.filter((item) => item.outcome === "skipped").length} skipped</span></div>
      <${ReviewAgentCard} group=${current} mode="quick" results=${quickResults} submittingDecisionIDs=${quickSubmitting} onQuick=${quick} onStage=${() => {}} onMark=${() => acknowledge(current)} />
      <div class="review-session-actions"><button type="button" class="button secondary" disabled=${Boolean(quickSubmitting.length)} onClick=${() => advance("skipped")}>Skip</button><button type="button" class="button primary" disabled=${Boolean(quickSubmitting.length)} onClick=${() => acknowledge(current, "reviewed")}>${index === groups.length - 1 ? "Done" : "Next"}</button></div>
    </div>` : null}
    ${mode === "plan" && !complete ? html`<div class="plan-review"><header><div><h2>Plan Review</h2><p>${state.planDrafts.length} staged choice${state.planDrafts.length === 1 ? "" : "s"} on this device.</p></div><button type="button" class="button primary" disabled=${!state.planDrafts.length || submittingPlan} onClick=${submitPlan}>${submittingPlan ? "Submitting sequentially…" : "Submit staged plan"}</button></header>
      <div class="review-card-stack">${groups.map((group) => html`<${ReviewAgentCard} key=${group.agent_id} group=${group} mode="plan" drafts=${state.planDrafts} results=${state.planResults} onQuick=${() => {}} onStage=${(decision, optionID) => agentStore.stagePlanDecision(decision, optionID)} onMark=${() => acknowledge(group)} />`)}</div>
    </div>` : null}
    ${complete ? html`<section class="review-complete" role="status"><${Icon} name="circle-check-big" size=${36} /><h2>Review session complete</h2><p>${mode === "plan" ? `${state.planResults.filter((item) => item.status === "success").length} submitted, ${state.planResults.filter((item) => item.status === "conflict").length} conflicted, ${state.planResults.filter((item) => item.status === "terminal_required").length} require terminal review.` : `${processed.filter((item) => item.outcome !== "skipped").length} reviewed and ${processed.filter((item) => item.outcome === "skipped").length} skipped.`}</p><button type="button" class="button secondary" onClick=${startAnother}>Start another review</button></section>` : null}
    ${!review.groups.length && !review.terminal_items.length ? html`<${EmptyState} icon="check-circle-2" title="Review is clear" detail="No structured changes, pending decisions, or terminal-only requests need attention." />` : null}
    <${TerminalReview} items=${review.terminal_items} panes=${panes} />
  </section>`;
}

function TimelineDestination({ state, route }) {
  const agent = route.id ? state.agentMap[route.id] : null;
  const events = route.id ? (state.agentTimelines[route.id] || state.timeline.filter((event) => event.agent_id === route.id)) : state.timeline;
  useEffect(() => { if (route.id) agentStore.loadAgent(route.id).catch(() => null); }, [route.id]);
  return html`<section class="timeline-destination"><${DestinationHeading} eyebrow="Historical replay" title=${agent ? `${agent.name} timeline` : "Timeline"} detail=${agent ? "Structured activity for this session." : "Structured activity across all retained agent sessions."} action=${route.id ? html`<a class="button secondary" href=${agentRoute("timeline")}><${Icon} name="users" size=${17} />All agents</a>` : null} /><${TimelineList} events=${events} /></section>`;
}

function AgentChrome({ layout, state, route, connection, onRetry, onSettings, onCreate, children }) {
  const [connectionOpen, setConnectionOpen] = useState(false);
  const pending = state.decisions.filter((item) => item.status === "pending").length;
  const items = state.reviewEnabled ? AGENT_DESTINATIONS : LEGACY_AGENT_DESTINATIONS;
  const navigation = {
    items,
    current: route.destination,
    badges: {
      decisions: pending,
      review: Number(state.review?.counts?.total_cards || state.review?.counts?.pending_decisions || 0),
    },
    onNavigate: (destination) => navigateAgentRoute(destination),
  };
  if (layout === "compact") return html`<div class="agent-workspace agent-workspace-compact app-shell"><${AgentHeader} compact=${true} connection=${connection} onConnection=${() => setConnectionOpen(true)} onSettings=${onSettings} onCreate=${onCreate} /><main class="agent-workspace-main">${children}</main><${WorkspaceNav} navigation=${navigation} layout="compact" />${connectionOpen ? html`<${Dialog} title="Connection" subtitle=${connectionLabel(connection)} onClose=${() => setConnectionOpen(false)}><${ConnectionDetails} connection=${connection} onRetry=${onRetry} /><//>` : null}</div>`;
  return html`<div class=${cx("agent-workspace", `agent-workspace-${layout}`, "app-shell")}><aside class="agent-workspace-sidebar"><${AgentHeader} connection=${connection} onConnection=${() => setConnectionOpen(true)} onSettings=${onSettings} onCreate=${onCreate} /><${WorkspaceNav} navigation=${navigation} layout="wide" /><div class="agent-sidebar-summary"><strong>${state.agents.length}</strong><span>agents</span><strong>${pending}</strong><span>need you</span></div></aside><main class="agent-workspace-main">${children}</main>${connectionOpen ? html`<${Dialog} title="Connection" subtitle=${connectionLabel(connection)} onClose=${() => setConnectionOpen(false)}><${ConnectionDetails} connection=${connection} onRetry=${onRetry} /><//>` : null}</div>`;
}

export function AgentWorkspace({
  layout,
  panes = [],
  connection,
  config,
  onRetry,
  onBroadcast,
  onSettings,
  onCreate,
  openPaneId,
}) {
  const capability = agentContextCapability(config);
  const reviewCapability = agentReviewCapability(config);
  const state = useAgentState();
  const rawRoute = useAgentRoute();
  const route = reviewCapability.enabled && rawRoute.destination === "decisions"
    ? { ...rawRoute, destination: "review" }
    : !reviewCapability.enabled && rawRoute.destination === "review"
      ? { ...rawRoute, destination: "decisions" } : rawRoute;

  useEffect(() => {
    void agentStore.configure(config).catch((error) => console.warn("[vmux] agent workspace startup failed", error?.name || "error"));
    return () => agentStore.stop();
  }, [config]);

  useEffect(() => {
    if (!config || capability.enabled || !rawRoute.valid || !globalThis.history?.replaceState) return;
    globalThis.history.replaceState(
      globalThis.history.state,
      "",
      `${globalThis.location.pathname}${globalThis.location.search}`,
    );
    if (typeof globalThis.Event === "function") {
      globalThis.dispatchEvent(new globalThis.Event("hashchange"));
    }
  }, [Boolean(config), capability.enabled, rawRoute.valid]);

  if (!capability.enabled) return html`<${Workspace} layout=${layout} panes=${panes} connection=${connection} onRetry=${onRetry} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} openPaneId=${openPaneId} />`;

  const pending = state.decisions.filter((item) => item.status === "pending").length;
  const navigation = {
    items: reviewCapability.enabled ? AGENT_DESTINATIONS : LEGACY_AGENT_DESTINATIONS,
    current: route.destination,
    badges: {
      decisions: pending,
      review: Number(state.review?.counts?.total_cards || state.review?.counts?.pending_decisions || 0),
    },
    onNavigate: (destination) => navigateAgentRoute(destination),
    paneId: route.destination === "panes" ? route.id : "",
  };
  if (route.destination === "panes" || route.destination === "stats") {
    return html`<${Workspace} layout=${layout} panes=${panes} connection=${connection} onRetry=${onRetry} onBroadcast=${onBroadcast} onSettings=${onSettings} onCreate=${onCreate} openPaneId=${openPaneId} workspaceNav=${navigation} />`;
  }

  let content;
  if (state.status === "loading" && !state.agents.length) content = html`<div class="agent-loading"><${Spinner} label="Loading agent workspace" />Loading agent context…</div>`;
  else if (state.status === "error" && !state.agents.length) content = html`<${EmptyState} icon="cloud-off" title="Agent workspace unavailable" detail=${state.error} action=${html`<button type="button" class="button primary" onClick=${() => agentStore.hydrate()}>Retry</button>`} />`;
  else if (route.destination === "decisions") content = html`<${DecisionDestination} state=${state} route=${route} layout=${layout} />`;
  else if (route.destination === "review") content = html`<${ReviewDestination} state=${state} route=${route} panes=${panes} />`;
  else if (route.destination === "timeline") content = html`<${TimelineDestination} state=${state} route=${route} />`;
  else content = html`<${AgentDestination} state=${state} route=${route} panes=${panes} connection=${connection} layout=${layout} />`;

  return html`<${AgentChrome} layout=${layout} state=${state} route=${route} connection=${connection} onRetry=${onRetry} onSettings=${onSettings} onCreate=${onCreate}>${content}<//>`;
}
