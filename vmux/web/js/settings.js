import {
  Dialog,
  Icon,
  InlineNotice,
  Segmented,
  Spinner,
  cx,
  html,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./core.js";
import {
  FALLBACK_KEYS,
  api,
  clearCredentials,
  setPrefs,
  usePrefs,
} from "./state.js";

const CATEGORIES = [
  ["appearance", "sun", "Appearance & Alerts"],
  ["input", "keyboard", "Input Shortcuts & Snippets"],
  ["server", "server", "Server & Discovery"],
  ["experimental", "shield-question", "Experimental"],
  ["agents", "bot", "Agent Overrides & Detectors"],
  ["usage", "chart-no-axes-column", "Usage"],
  ["sessions", "monitor", "Sessions"],
  ["about", "info", "Connection & About"],
];

function reportPatchFailure(error) {
  // The settings hook has already rendered the user-facing error. Keep a
  // non-sensitive category in developer tools without discarding rejection.
  console.warn("[vmux] settings update failed", error?.category || error?.name || "error");
}

function Switch({ checked, onChange, label, disabled = false }) {
  return html`<label class=${cx("switch", disabled && "disabled")}>
    <span class="sr-only">${label}</span>
    <input type="checkbox" checked=${!!checked} disabled=${disabled} onChange=${(e) => onChange(e.target.checked)} />
    <span class="switch-track" aria-hidden="true"><span></span></span>
  </label>`;
}

function SettingRow({ label, detail = "", children, align = "center" }) {
  return html`<div class="setting-row" style=${{ alignItems: align }}>
    <div class="setting-copy"><div class="setting-label">${label}</div>${detail ? html`<div class="setting-detail">${detail}</div>` : null}</div>
    <div class="setting-control">${children}</div>
  </div>`;
}

function SettingGroup({ title, detail = "", children }) {
  return html`<section class="setting-section">
    <header><h3>${title}</h3>${detail ? html`<p>${detail}</p>` : null}</header>
    <div class="setting-group">${children}</div>
  </section>`;
}

function SaveBar({ dirty, pending, onSave, savedText = "Saved", invalid = false }) {
  return html`<div class="save-bar">
    <span class="save-state" aria-live="polite">${pending ? "Saving…" : (invalid ? "Fix invalid values" : (dirty ? "Unsaved changes" : savedText))}</span>
    <button class="button primary" disabled=${!dirty || pending || invalid} onClick=${onSave}>
      ${pending ? html`<${Spinner} label="Saving" />` : html`<${Icon} name="save" />`} Save
    </button>
  </div>`;
}

function validateNumberDraft(value, label, min, max, { integer = false, step = null } = {}) {
  const trimmed = String(value).trim();
  const number = Number(trimmed);
  if (!trimmed || !Number.isFinite(number)) return { number, error: `${label} must be a number.` };
  if (number < min || number > max) return { number, error: `${label} must be between ${min} and ${max}.` };
  if (integer && !Number.isInteger(number)) return { number, error: `${label} must be a whole number.` };
  if (step !== null) {
    const offset = (number - min) / step;
    if (Math.abs(offset - Math.round(offset)) > 1e-8) {
      return { number, error: `${label} must use increments of ${step}.` };
    }
  }
  return { number, error: "" };
}

function useServerConfig(initial, onConfig) {
  const [config, setConfig] = useState(initial || null);
  const configRef = useRef(initial || null);
  const confirmedRef = useRef(initial || null);
  const operationsRef = useRef([]);
  const operationIdRef = useRef(0);
  const hadFailureRef = useRef(false);
  const [pending, setPending] = useState(new Set());
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(!!initial);
  const queue = useRef(Promise.resolve());

  const renderVisible = useCallback(() => {
    const visible = operationsRef.current.reduce(
      (next, operation) => operation.optimistic ? { ...next, ...operation.partial } : next,
      confirmedRef.current,
    );
    configRef.current = visible;
    setConfig(visible);
  }, []);

  const updatePending = useCallback(() => {
    setPending(new Set(operationsRef.current.map((operation) => operation.key)));
  }, []);

  const adoptServer = useCallback((next, notify = true) => {
    confirmedRef.current = next;
    renderVisible();
    if (notify) onConfig?.(next);
  }, [onConfig, renderVisible]);

  useEffect(() => {
    if (initial && initial !== confirmedRef.current && !operationsRef.current.length) {
      adoptServer(initial, false);
    }
  }, [initial]);

  useEffect(() => {
    if (initial) return;
    api("/config").then((next) => { adoptServer(next); setLoaded(true); })
      .catch((err) => { setError(err.userMessage || err.message || "Server settings could not be loaded."); setLoaded(true); });
  }, []);

  const patch = useCallback((partial, key, optimistic = true) => {
    if (!confirmedRef.current) return Promise.reject(new Error("Configuration has not loaded"));
    if (!operationsRef.current.length) {
      hadFailureRef.current = false;
      setError("");
    }
    const operation = { id: ++operationIdRef.current, key, partial, optimistic };
    operationsRef.current.push(operation);
    setFeedback("Saving changes…");
    updatePending();
    if (optimistic) renderVisible();
    const task = queue.current.catch((error) => { reportPatchFailure(error); return undefined; }).then(async () => {
      try {
        const next = await api("/config", partial, "PATCH");
        operationsRef.current = operationsRef.current.filter((candidate) => candidate.id !== operation.id);
        adoptServer(next);
        setFeedback(operationsRef.current.length
          ? "Saving changes…"
          : (hadFailureRef.current ? "Some changes were not saved." : "Changes saved."));
        return next;
      } catch (err) {
        hadFailureRef.current = true;
        operationsRef.current = operationsRef.current.filter((candidate) => candidate.id !== operation.id);
        renderVisible();
        setError(err.userMessage || err.message || "The server rejected that change.");
        setFeedback(operationsRef.current.length ? "Saving remaining changes…" : "Change was not saved.");
        throw err;
      } finally {
        updatePending();
      }
    });
    queue.current = task;
    return task;
  }, [adoptServer, renderVisible, updatePending]);

  return { config, loaded, pending, error, feedback, setError, patch };
}

function AppearanceSettings() {
  const prefs = usePrefs();
  const [notice, setNotice] = useState("");
  const toggleNotifications = async (value) => {
    if (!value) { setPrefs({ notify: false }); return; }
    if (!("Notification" in window)) { setNotice("This browser does not support system notifications."); return; }
    try {
      const result = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
      const enabled = result === "granted";
      setPrefs({ notify: enabled });
      setNotice(enabled ? "Notifications enabled." : "Notification permission was not granted.");
    } catch (err) {
      setNotice("Notification permission could not be requested.");
    }
  };
  return html`<div>
    <${SettingGroup} title="Appearance">
      <${SettingRow} label="Theme"><${Segmented} value=${prefs.theme} options=${[["auto", "System"], ["light", "Light"], ["dark", "Dark"]]} onChange=${(theme) => setPrefs({ theme })} label="Theme" /><//>
      <${SettingRow} label="Glass chrome" detail="Translucency is limited to floating navigation and dialogs."><${Switch} label="Glass chrome" checked=${prefs.glass} onChange=${(glass) => setPrefs({ glass })} /><//>
      <${SettingRow} label="Ambient motion" detail="Off by default and always suppressed with Reduce Motion."><${Switch} label="Ambient motion" checked=${prefs.ambient} onChange=${(ambient) => setPrefs({ ambient })} /><//>
    <//>
    <${SettingGroup} title="Attention alerts">
      <${SettingRow} label="System notifications"><${Switch} label="System notifications" checked=${prefs.notify} onChange=${toggleNotifications} /><//>
      <${SettingRow} label="Sound"><${Switch} label="Sound" checked=${prefs.sound} onChange=${(sound) => setPrefs({ sound })} /><//>
      <${SettingRow} label="Also alert on errors" detail="Needs-input alerts are always included."><${Switch} label="Also alert on errors" checked=${prefs.alertErrors} onChange=${(alertErrors) => setPrefs({ alertErrors })} /><//>
      <${SettingRow} label="Default destination"><${Segmented} value=${prefs.defaultFilter} options=${[["queue", "Queue"], ["active", "Active"], ["all", "All"]]} onChange=${(defaultFilter) => setPrefs({ defaultFilter })} label="Default destination" /><//>
    <//>
    ${notice ? html`<${InlineNotice} icon="bell">${notice}<//>` : null}
  </div>`;
}

const move = (array, index, delta) => {
  const nextIndex = index + delta;
  if (nextIndex < 0 || nextIndex >= array.length) return array;
  const next = array.slice();
  [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
  return next;
};

function InputSettings({ allowedKeys }) {
  const prefs = usePrefs();
  const [actions, setActions] = useState(() => (prefs.actions || []).map((row) => [...row]));
  const [snippets, setSnippets] = useState(() => [...(prefs.snippets || [])]);
  const baseline = JSON.stringify({ actions: prefs.actions, snippets: prefs.snippets });
  const dirty = JSON.stringify({ actions, snippets }) !== baseline;
  const save = () => {
    const normalizedActions = actions
      .map(([label, key]) => [label.trim(), key])
      .filter(([label]) => label);
    const normalizedSnippets = snippets.map((snippet) => snippet.trim()).filter(Boolean);
    setActions(normalizedActions.map((row) => [...row]));
    setSnippets([...normalizedSnippets]);
    setPrefs({ actions: normalizedActions, snippets: normalizedSnippets });
  };
  return html`<div>
    <${SettingGroup} title="Shortcut buttons" detail="Choose the key controls that appear under each terminal.">
      ${actions.map(([label, key], index) => html`<div class="editor-row" key=${index}>
        <input aria-label=${`Shortcut ${index + 1} label`} value=${label} onInput=${(e) => setActions(actions.map((row, i) => i === index ? [e.target.value, key] : row))} />
        <select aria-label=${`Shortcut ${index + 1} key`} value=${key} onChange=${(e) => setActions(actions.map((row, i) => i === index ? [label, e.target.value] : row))}>
          ${allowedKeys.map((allowed) => html`<option key=${allowed} value=${allowed}>${allowed}</option>`)}</select>
        <button class="icon-button" disabled=${index === 0} onClick=${() => setActions(move(actions, index, -1))}><${Icon} name="arrow-up" /><//>
        <button class="icon-button" disabled=${index === actions.length - 1} onClick=${() => setActions(move(actions, index, 1))}><${Icon} name="arrow-down" /><//>
        <button class="icon-button danger" onClick=${() => setActions(actions.filter((_, i) => i !== index))}><${Icon} name="trash" /><//>
      </div>`)}
      <button class="text-button" onClick=${() => setActions([...actions, ["Key", "Enter"]])}><${Icon} name="plus" /> Add shortcut</button>
    <//>
    <${SettingGroup} title="Snippets" detail="Reusable text inserted into the pane composer.">
      ${snippets.map((snippet, index) => html`<div class="editor-row snippet" key=${index}>
        <input aria-label=${`Snippet ${index + 1}`} value=${snippet} onInput=${(e) => setSnippets(snippets.map((s, i) => i === index ? e.target.value : s))} />
        <button class="icon-button" disabled=${index === 0} onClick=${() => setSnippets(move(snippets, index, -1))}><${Icon} name="arrow-up" /><//>
        <button class="icon-button" disabled=${index === snippets.length - 1} onClick=${() => setSnippets(move(snippets, index, 1))}><${Icon} name="arrow-down" /><//>
        <button class="icon-button danger" onClick=${() => setSnippets(snippets.filter((_, i) => i !== index))}><${Icon} name="trash" /><//>
      </div>`)}
      <button class="text-button" onClick=${() => setSnippets([...snippets, ""])}><${Icon} name="plus" /> Add snippet</button>
    <//>
    <${SaveBar} dirty=${dirty} pending=${false} onSave=${save} />
  </div>`;
}

function ServerSettings({ config, pending, patch }) {
  const [poll, setPoll] = useState(String(config.poll_interval));
  const [capture, setCapture] = useState(String(config.capture_lines));
  const pollDraft = validateNumberDraft(poll, "Poll interval", 0.2, 10, { step: 0.1 });
  const captureDraft = validateNumberDraft(capture, "Scrollback", 40, 2000, { integer: true });
  const errors = [pollDraft.error, captureDraft.error].filter(Boolean);
  const dirty = Number(poll) !== Number(config.poll_interval) || Number(capture) !== Number(config.capture_lines);
  const save = () => {
    if (errors.length) return;
    patch({ poll_interval: pollDraft.number, capture_lines: captureDraft.number }, "server-numbers", false).catch(reportPatchFailure);
  };
  return html`<div>
    <${SettingGroup} title="Discovery">
      <${SettingRow} label="Auto-discover panes" detail="Include matching agent panes without a target list."><${Switch} label="Auto-discover panes" checked=${config.auto_discover} disabled=${pending.has("auto_discover")} onChange=${(value) => patch({ auto_discover: value }, "auto_discover").catch(reportPatchFailure)} /><//>
      <${SettingRow} label="Show idle shells"><${Switch} label="Show idle shells" checked=${config.include_shells} disabled=${pending.has("include_shells")} onChange=${(value) => patch({ include_shells: value }, "include_shells").catch(reportPatchFailure)} /><//>
      <${SettingRow} label="Pane name" detail="Manual overrides always win."><select aria-label="Pane name" value=${config.naming_mode} disabled=${pending.has("naming_mode")} onChange=${(e) => patch({ naming_mode: e.target.value }, "naming_mode").catch(reportPatchFailure)}>
        ${[["session_window_pane", "Session:window:pane"], ["session_pane", "Session:pane"], ["window_pane", "Window:pane"], ["pane", "Pane"], ["title", "Title"], ["window", "Window"], ["target", "Target"], ["command", "Command"], ["smart", "Smart task"]].map(([value, label]) => html`<option key=${value} value=${value}>${label}</option>`)}</select><//>
    <//>
    <${SettingGroup} title="Polling & capture">
      <${SettingRow} label="Poll interval" detail="0.2–10 seconds"><input aria-label="Poll interval" aria-invalid=${!!pollDraft.error} aria-describedby=${pollDraft.error ? "server-number-errors" : undefined} class="number-input" type="number" min="0.2" max="10" step="0.1" value=${poll} onInput=${(e) => setPoll(e.target.value)} /><//>
      <${SettingRow} label="Scrollback" detail="40–2,000 terminal lines"><input aria-label="Scrollback" aria-invalid=${!!captureDraft.error} aria-describedby=${captureDraft.error ? "server-number-errors" : undefined} class="number-input" type="number" min="40" max="2000" step="1" value=${capture} onInput=${(e) => setCapture(e.target.value)} /><//>
    <//>
    ${errors.length ? html`<p id="server-number-errors" class="settings-validation" role="alert">${errors.join(" ")}</p>` : null}
    <${SaveBar} dirty=${dirty} pending=${pending.has("server-numbers")} invalid=${!!errors.length} onSave=${save} />
  </div>`;
}

function AgentSettings({ config, panes, pending, patch }) {
  const targets = useMemo(() => [...new Set([...(config._info?.targets || []), ...(config.overrides || []).map((o) => o.target), ...panes.map((p) => p.target)])].sort(), [config, panes]);
  const [overrides, setOverrides] = useState(() => (config.overrides || []).map((o) => ({ ...o })));
  const [prompts, setPrompts] = useState(() => (config.generic_prompt_patterns || []).join("\n"));
  const [errors, setErrors] = useState(() => (config.error_patterns || []).join("\n"));
  const current = JSON.stringify({ overrides: config.overrides || [], prompts: config.generic_prompt_patterns || [], errors: config.error_patterns || [] });
  const draft = JSON.stringify({ overrides, prompts: prompts.split("\n").map((s) => s.trim()).filter(Boolean), errors: errors.split("\n").map((s) => s.trim()).filter(Boolean) });
  const dirty = current !== draft;
  const byTarget = new Map(overrides.map((o) => [o.target, o]));
  const edit = (target, change) => {
    const next = { target, name: null, kind: null, star: false, ...(byTarget.get(target) || {}), ...change };
    const remaining = overrides.filter((o) => o.target !== target);
    setOverrides(next.name || next.kind || next.star ? [...remaining, next] : remaining);
  };
  const save = () => patch({
    overrides,
    generic_prompt_patterns: prompts.split("\n").map((s) => s.trim()).filter(Boolean),
    error_patterns: errors.split("\n").map((s) => s.trim()).filter(Boolean),
  }, "agents", false).catch(reportPatchFailure);
  return html`<div>
    <${SettingGroup} title="Agent overrides" detail="Names and kinds save together to prevent stale whole-list updates.">
      ${targets.length ? targets.map((target) => {
        const row = byTarget.get(target) || { target, name: "", kind: null, star: false };
        return html`<div class="agent-editor" key=${target}>
          <div class="agent-target">${target}</div>
          <input aria-label=${`Name for ${target}`} placeholder="Custom name" value=${row.name || ""} onInput=${(e) => edit(target, { name: e.target.value || null })} />
          <select aria-label=${`Agent kind for ${target}`} value=${row.kind || "auto"} onChange=${(e) => edit(target, { kind: e.target.value === "auto" ? null : e.target.value })}>
            ${[["auto", "Auto detect"], ["claude-code", "Claude Code"], ["codex", "Codex"], ["grok", "Grok"], ["opencode", "OpenCode"], ["antigravity", "Antigravity"], ["generic", "Generic agent"], ["shell", "Shell"]].map(([value, label]) => html`<option key=${value} value=${value}>${label}</option>`)}</select>
        </div>`;
      }) : html`<p class="setting-empty">No discovered panes or saved overrides.</p>`}
    <//>
    <${SettingGroup} title="Detectors" detail="One bounded regular expression per line. Invalid patterns are rejected by the server.">
      <label class="field-stack"><span>Prompt patterns</span><textarea rows="7" value=${prompts} onInput=${(e) => setPrompts(e.target.value)}></textarea></label>
      <label class="field-stack"><span>Error patterns</span><textarea rows="7" value=${errors} onInput=${(e) => setErrors(e.target.value)}></textarea></label>
    <//>
    <${SaveBar} dirty=${dirty} pending=${pending.has("agents")} onSave=${save} />
  </div>`;
}

function ExperimentalSettings({ config, pending, patch }) {
  const key = "experimental_agent_workspace_enabled";
  return html`<div>
    <${SettingGroup}
      title="Agent Workspace"
      detail="Experimental features may change as runtime log formats and workspace contracts evolve."
    >
      <${SettingRow}
        label="Enable Agent Workspace"
        detail="Observes local Codex and Claude runtime logs and stores normalized visible messages, decisions, and timeline history on this server. Turning it off stops observation, review scheduling, agent sockets, and access without deleting existing structured history."
        align="start"
      >
        <${Switch}
          label="Enable Agent Workspace"
          checked=${config[key] === true}
          disabled=${pending.has(key)}
          onChange=${(value) => patch({ [key]: value }, key, false).catch(reportPatchFailure)}
        />
      <//>
    <//>
    <${InlineNotice} icon="database">
      Re-enabling restores access to retained history. Runtime-log locations and retention remain server YAML settings.
    <//>
  </div>`;
}

function UsageSettings({ config, pending, patch }) {
  const [quota, setQuota] = useState(String(config.usage_quota_refresh));
  const [report, setReport] = useState(String(config.usage_report_refresh));
  const [threshold, setThreshold] = useState(String(config.usage_alert_threshold));
  const quotaDraft = validateNumberDraft(quota, "Quota refresh", 30, 3600);
  const reportDraft = validateNumberDraft(report, "Report refresh", 60, 3600);
  const thresholdDraft = validateNumberDraft(threshold, "Warning threshold", 0, 100);
  const errors = [quotaDraft.error, reportDraft.error, thresholdDraft.error].filter(Boolean);
  const dirty = Number(quota) !== Number(config.usage_quota_refresh) || Number(report) !== Number(config.usage_report_refresh) || Number(threshold) !== Number(config.usage_alert_threshold);
  const save = () => {
    if (errors.length) return;
    patch({ usage_quota_refresh: quotaDraft.number, usage_report_refresh: reportDraft.number, usage_alert_threshold: thresholdDraft.number }, "usage-values", false).catch(reportPatchFailure);
  };
  const info = config._info?.usage || {};
  return html`<div>
    <${SettingGroup} title="Usage collector" detail="vmux reads local tokscale output; no usage data is sent to vmux itself.">
      <${SettingRow} label="Enable usage tracking"><${Switch} label="Enable usage tracking" checked=${config.usage_enabled} disabled=${pending.has("usage_enabled")} onChange=${(value) => patch({ usage_enabled: value }, "usage_enabled").catch(reportPatchFailure)} /><//>
      <${SettingRow} label="Collector"><span class=${cx("availability", info.installed ? "good" : "warn")}><${Icon} name=${info.installed ? "circle-check" : "circle-alert"} />${info.installed ? "Installed" : "Not installed"}</span><//>
      <${SettingRow} label="Quota refresh" detail="30–3,600 seconds"><input aria-label="Quota refresh" aria-invalid=${!!quotaDraft.error} aria-describedby=${quotaDraft.error ? "usage-number-errors" : undefined} class="number-input" type="number" min="30" max="3600" step="any" value=${quota} onInput=${(e) => setQuota(e.target.value)} /><//>
      <${SettingRow} label="Report refresh" detail="60–3,600 seconds"><input aria-label="Report refresh" aria-invalid=${!!reportDraft.error} aria-describedby=${reportDraft.error ? "usage-number-errors" : undefined} class="number-input" type="number" min="60" max="3600" step="any" value=${report} onInput=${(e) => setReport(e.target.value)} /><//>
      <${SettingRow} label="Warning threshold" detail="Percent remaining; 0 disables alerts"><input aria-label="Warning threshold" aria-invalid=${!!thresholdDraft.error} aria-describedby=${thresholdDraft.error ? "usage-number-errors" : undefined} class="number-input" type="number" min="0" max="100" step="any" value=${threshold} onInput=${(e) => setThreshold(e.target.value)} /><//>
    <//>
    ${errors.length ? html`<p id="usage-number-errors" class="settings-validation" role="alert">${errors.join(" ")}</p>` : null}
    <${SaveBar} dirty=${dirty} pending=${pending.has("usage-values")} invalid=${!!errors.length} onSave=${save} />
  </div>`;
}

function friendlyDevice(ua) {
  if (!ua) return "Unknown device";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh|Mac OS X/.test(ua)) return /Chrome|CriOS/.test(ua) ? "Mac · Chrome" : "Mac · Safari";
  if (/Windows/.test(ua)) return "Windows";
  if (/Linux/.test(ua)) return "Linux";
  return "Browser session";
}

function age(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function SessionsSettings() {
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(new Set());
  const load = useCallback(async () => {
    try { const result = await api("/sessions"); setSessions(result.sessions || []); setError(""); }
    catch (err) { setError(err.userMessage || err.message || "Sessions could not be loaded."); }
  }, []);
  useEffect(() => { load(); const timer = setInterval(load, 3000); return () => clearInterval(timer); }, []);
  const disconnect = async (id) => {
    setPending((all) => new Set(all).add(id));
    try { await api("/sessions/kill", { id }); await load(); }
    catch (err) { setError(err.userMessage || err.message || "The session could not be disconnected."); }
    finally { setPending((all) => { const next = new Set(all); next.delete(id); return next; }); }
  };
  return html`<div><${SettingGroup} title="Connected sessions" detail="Live WebSocket clients appear here. REST fallback clients do not.">
    ${sessions == null ? html`<div class="setting-loading"><${Spinner} /> Loading sessions…</div>`
      : sessions.length ? sessions.map((session) => html`<div class="session-row" key=${session.id}>
        <div class="session-icon"><${Icon} name="monitor" /></div><div><strong>${friendlyDevice(session.ua)}</strong><span>${session.ip} · connected ${age(session.age)} ago</span></div>
        <button class="button danger" disabled=${pending.has(session.id)} onClick=${() => disconnect(session.id)}>${pending.has(session.id) ? "Disconnecting…" : "Disconnect"}</button>
      </div>`) : html`<p class="setting-empty">No active WebSocket sessions.</p>`}
  <//>${error ? html`<${InlineNotice} tone="error" icon="triangle-alert">${error}<//>` : null}</div>`;
}

function connectionLabel(mode) {
  return ({ connecting: "Connecting", live: "Live", rest: "Updating via REST", updating_rest: "Updating via REST", offline: "Offline", unauthorized: "Unauthorized", incompatible: "Incompatible" })[mode] || "Connecting";
}

function AboutSettings({ config, connection, onRetry }) {
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");
  const issue = connection.issue || {};
  const compatibility = connection.compatibility || {};
  const details = {
    host: location.host,
    endpoint: issue.endpoint || "/api/state",
    http_status: issue.httpStatus || issue.status || null,
    client_version: "0.1.0",
    server_version: config?._info?.version || null,
    protocol_version: config?._info?.compatibility?.protocol_version ?? null,
    category: issue.category || connection.mode,
    timestamp: new Date().toISOString(),
  };
  const signOut = async () => {
    setSigningOut(true); setError("");
    try { await clearCredentials({ reload: true }); }
    catch (err) { setError("Credentials could not be fully cleared from this browser."); setSigningOut(false); }
  };
  return html`<div>
    <${SettingGroup} title="Connection">
      <${SettingRow} label="Status"><span class=${cx("connection-value", connection.mode)}><i></i>${connectionLabel(connection.mode)}</span><//>
      <${SettingRow} label="Server"><code>${location.host}</code><//>
      <${SettingRow} label="Server version"><span>${config?._info?.version || "Unverified"}</span><//>
      <${SettingRow} label="Protocol"><span>${compatibility.status === "unverified" ? "Unverified" : (compatibility.protocolVersion ?? config?._info?.compatibility?.protocol_version ?? "Unverified")}</span><//>
      <${SettingRow} label="Web compatibility"><span>${compatibility.message || (compatibility.blocked ? "Update required" : "Compatible")}</span><//>
      <div class="connection-actions"><button class="button" onClick=${onRetry}><${Icon} name="refresh-cw" /> Retry now</button><button class="button danger" disabled=${signingOut} onClick=${signOut}><${Icon} name="log-out" /> ${signingOut ? "Signing out…" : "Sign out"}</button></div>
    <//>
    <${SettingGroup} title="Sanitized technical details" detail="This excludes credentials, pane text, account data, and request bodies.">
      <pre class="technical-details">${JSON.stringify(details, null, 2)}</pre>
    <//>
    <${SettingGroup} title="About vmux">
      <${SettingRow} label="Web client"><span>0.1.0 · protocol 1</span><//>
      <${SettingRow} label="Minimum server"><span>0.1.0</span><//>
      <${SettingRow} label="Privacy"><span>Pane snapshots remain memory-only in this PWA.</span><//>
    <//>
    ${error ? html`<${InlineNotice} tone="error" icon="triangle-alert">${error}<//>` : null}
  </div>`;
}

export function SettingsOverlay({ layout, panes, connection, config: initialConfig, onConfig, onRetry, onClose }) {
  const [category, setCategory] = useState(layout === "compact" ? null : "appearance");
  const server = useServerConfig(initialConfig, onConfig);
  const active = category || "appearance";
  const allowedKeys = server.config?._info?.allowed_keys || FALLBACK_KEYS;
  const body = !server.loaded ? html`<div class="setting-loading"><${Spinner} /> Loading settings…</div>`
    : !server.config ? html`<${InlineNotice} tone="error" icon="triangle-alert">${server.error || "Settings are unavailable."}<//>`
    : active === "appearance" ? html`<${AppearanceSettings} />`
    : active === "input" ? html`<${InputSettings} allowedKeys=${allowedKeys} />`
    : active === "server" ? html`<${ServerSettings} config=${server.config} pending=${server.pending} patch=${server.patch} />`
    : active === "experimental" ? html`<${ExperimentalSettings} config=${server.config} pending=${server.pending} patch=${server.patch} />`
    : active === "agents" ? html`<${AgentSettings} config=${server.config} panes=${panes} pending=${server.pending} patch=${server.patch} />`
    : active === "usage" ? html`<${UsageSettings} config=${server.config} pending=${server.pending} patch=${server.patch} />`
    : active === "sessions" ? html`<${SessionsSettings} />`
    : html`<${AboutSettings} config=${server.config} connection=${connection} onRetry=${onRetry} />`;

  return html`<${Dialog} title="Settings" subtitle=${layout === "compact" && category ? CATEGORIES.find(([key]) => key === category)?.[2] : "Preferences, server controls, and connection details"} onClose=${onClose} className="settings-dialog">
    ${server.error ? html`<${InlineNotice} tone="error" icon="triangle-alert">${server.error}<//>` : null}
    ${server.feedback && !server.error ? html`<div class="settings-save-feedback" role="status" aria-live="polite">${server.feedback}</div>` : null}
    <div class=${cx("settings-layout", layout === "compact" && "compact")}>
      ${layout === "compact" && !category ? html`<nav class="settings-category-list" aria-label="Settings categories">
        ${CATEGORIES.map(([key, icon, label]) => html`<button key=${key} onClick=${() => setCategory(key)}><span><${Icon} name=${icon} /></span><strong>${label}</strong><${Icon} name="chevron-right" /></button>`)}
      </nav>` : html`<${FragmentOrNav} layout=${layout} category=${active} setCategory=${setCategory} />`}
      ${layout === "compact" && !category ? null : html`<div class="settings-content">
        ${layout === "compact" ? html`<button class="settings-back" onClick=${() => setCategory(null)}><${Icon} name="chevron-left" /> All settings</button>` : null}
        <div class="settings-section-title"><${Icon} name=${CATEGORIES.find(([key]) => key === active)?.[1]} /><h2>${CATEGORIES.find(([key]) => key === active)?.[2]}</h2></div>
        ${body}
      </div>`}
    </div>
  <//>`;
}

function FragmentOrNav({ layout, category, setCategory }) {
  if (layout === "compact") return null;
  return html`<nav class="settings-sidebar" aria-label="Settings categories">
    ${CATEGORIES.map(([key, icon, label]) => html`<button class=${category === key ? "selected" : ""} key=${key} aria-current=${category === key ? "page" : null} onClick=${() => setCategory(key)}><${Icon} name=${icon} />${label}</button>`)}
  </nav>`;
}
