/** Capability-gated tmux creation flow. Paths remain in memory; only the last
 * runtime identifier is stored locally. */

import {
  Dialog,
  Icon,
  InlineNotice,
  Segmented,
  Spinner,
  cx,
  html,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./core.js";
import { ApiError, api } from "./state.js";

export const CREATION_CAPABILITY = "tmux_create_v1";
export const CREATION_RUNTIME_KEY = "vmux_creation_runtime";
const RUNTIME_IDS = ["shell", "codex", "claude", "agy", "grok", "opencode"];

const RUNTIME_LABELS = {
  shell: "Shell", codex: "Codex", claude: "Claude", agy: "Antigravity",
  grok: "Grok Build", opencode: "OpenCode",
};

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

export function creationCapability(config) {
  const capabilities = object(config?._info?.capabilities) ? config._info.capabilities : {};
  const raw = capabilities[CREATION_CAPABILITY];
  if (raw === true) return { supported: true, enabled: true, version: 1, reason: "" };
  if (!object(raw)) return { supported: false, enabled: false, version: 0, reason: "" };
  return {
    ...raw,
    supported: raw.supported !== false,
    enabled: raw.enabled === true,
    version: Number(raw.version || 1),
    reason: text(raw.reason),
  };
}

export function creationBlockReason(config, connection) {
  const capability = creationCapability(config);
  if (!capability.supported) return "This server does not support tmux creation. Update vmux to use this feature.";
  if (connection?.compatibility?.blocksActions || connection?.mode === "incompatible") {
    return "Creation is unavailable until the client and server are compatible.";
  }
  if (!["live", "updating_rest", "rest"].includes(connection?.mode)) {
    return "Creation is unavailable while vmux is offline or reconnecting.";
  }
  if (!capability.enabled) return capability.reason || "Creation is not configured on this server.";
  return "";
}

function loadSavedRuntime() {
  try {
    const value = globalThis.localStorage?.getItem(CREATION_RUNTIME_KEY) || "";
    return RUNTIME_IDS.includes(value) ? value : "shell";
  } catch (_) {
    return "shell";
  }
}

function saveRuntime(value) {
  if (!RUNTIME_IDS.includes(value)) return;
  try { globalThis.localStorage?.setItem(CREATION_RUNTIME_KEY, value); } catch (_) {}
}

export function suggestedCreationName(path, fallback = "session") {
  const part = String(path || "").replace(/\/+$/, "").split("/").pop() || fallback;
  const slug = part.normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "")
    .toLowerCase().slice(0, 64).replace(/[-_]+$/g, "");
  return slug || fallback;
}

function sessionName(pane) {
  const target = text(pane?.target);
  const colon = target.lastIndexOf(":");
  return colon >= 0 ? target.slice(0, colon) : target;
}

function normalizedInfo(raw) {
  if (!object(raw)) throw new ApiError("Server returned invalid creation metadata", {
    category: "protocol", endpoint: "/api/tmux/creation",
  });
  const roots = Array.isArray(raw.roots) ? raw.roots.filter((root) => (
    object(root) && text(root.label) && text(root.path)
  )).map((root) => ({ label: root.label, path: root.path })) : [];
  const recents = Array.isArray(raw.recent_directories) ? raw.recent_directories.filter((item) => (
    object(item) && text(item.path)
  )).map((item) => ({
    path: item.path, name: text(item.name, item.path), rootLabel: text(item.root_label),
  })) : [];
  const runtimes = Array.isArray(raw.runtimes) ? raw.runtimes.filter((item) => (
    object(item) && RUNTIME_IDS.includes(item.id)
  )).map((item) => ({
    id: item.id,
    label: text(item.label, item.id),
    available: item.available === true,
    reason: text(item.reason),
  })) : [];
  return {
    enabled: raw.enabled === true,
    reason: text(raw.reason),
    roots,
    recents,
    runtimes,
  };
}

function breadcrumbs(directory) {
  if (!directory?.root?.path || !directory?.path) return [];
  const rootPath = directory.root.path.replace(/\/+$/, "") || "/";
  const relative = directory.path.slice(rootPath.length).split("/").filter(Boolean);
  const values = [{ label: directory.root.label, path: rootPath }];
  let current = rootPath;
  for (const part of relative) {
    current = `${current === "/" ? "" : current}/${part}`;
    values.push({ label: part, path: current });
  }
  return values;
}

export function CreationDialog({ panes, connection, config, initial = {}, onClose, onCreated }) {
  const capabilityReason = creationBlockReason(config, connection);
  const [info, setInfo] = useState(null);
  const [directory, setDirectory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [browsing, setBrowsing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [type, setType] = useState(["session", "window", "pane"].includes(initial.type) ? initial.type : "session");
  const [cwd, setCwd] = useState("");
  const [runtime, setRuntime] = useState(loadSavedRuntime);
  const [parentSession, setParentSession] = useState(text(initial.parentSession));
  const [parentPaneID, setParentPaneID] = useState(text(initial.parentPaneID));
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [split, setSplit] = useState("side_by_side");
  const [sizePercent, setSizePercent] = useState(50);
  const directoryRequest = useRef(0);

  const livePanes = useMemo(() => panes.filter((pane) => pane.actionable !== false && pane.status !== "offline"), [panes]);
  const sessions = useMemo(() => [...new Set(livePanes.map(sessionName).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true })), [livePanes]);
  const runtimeItems = info?.runtimes?.length ? info.runtimes : RUNTIME_IDS.map((id) => ({
    id, label: RUNTIME_LABELS[id] || id,
    available: id === "shell", reason: id === "shell" ? "" : "Runtime metadata is unavailable.",
  }));
  const selectedRuntime = runtimeItems.find((item) => item.id === runtime);
  const blocked = capabilityReason || (info && (!info.enabled ? info.reason || "Creation is not configured." : ""));

  useEffect(() => {
    if (type === "window" && !parentSession) setParentSession(sessions[0] || "");
    if (type === "pane" && !parentPaneID) setParentPaneID(livePanes[0]?.id || "");
  }, [type, sessions, livePanes, parentSession, parentPaneID]);

  useEffect(() => {
    if (!nameTouched && type !== "pane") setName(suggestedCreationName(cwd, type));
  }, [cwd, type, nameTouched]);

  const loadDirectory = async (path, { quiet = false } = {}) => {
    if (!path) return;
    const requestID = ++directoryRequest.current;
    if (!quiet) setBrowsing(true);
    setError("");
    try {
      const value = await api(`/tmux/directories?path=${encodeURIComponent(path)}`);
      if (!object(value) || !text(value.path) || !Array.isArray(value.directories)) {
        throw new ApiError("Server returned an invalid directory listing", {
          category: "protocol", endpoint: "/api/tmux/directories",
        });
      }
      if (requestID !== directoryRequest.current) return;
      setDirectory(value);
      setCwd(value.path);
    } catch (reason) {
      if (requestID !== directoryRequest.current) return;
      setError(reason?.userMessage || reason?.message || "That directory could not be opened.");
    } finally {
      if (!quiet && requestID === directoryRequest.current) setBrowsing(false);
    }
  };

  useEffect(() => {
    let active = true;
    const start = async () => {
      if (capabilityReason) { setLoading(false); return; }
      try {
        const loaded = normalizedInfo(await api("/tmux/creation"));
        if (!active) return;
        setInfo(loaded);
        const saved = loaded.runtimes.find((item) => item.id === runtime && item.available);
        const chosenRuntime = saved ? runtime : "shell";
        setRuntime(chosenRuntime);
        const firstPath = loaded.recents[0]?.path || loaded.roots[0]?.path || "";
        setCwd(firstPath);
        if (firstPath) await loadDirectory(firstPath, { quiet: true });
      } catch (reason) {
        if (active) setError(reason?.userMessage || reason?.message || "Creation settings could not be loaded.");
      } finally {
        if (active) setLoading(false);
      }
    };
    void start();
    return () => { active = false; };
  }, []);

  const submit = async () => {
    if (submitting || blocked || !cwd || !selectedRuntime?.available) return;
    const body = { type, cwd, runtime };
    if (type === "session" || type === "window") body.name = nameTouched ? name.trim() : null;
    if (type === "window") body.parent_session = parentSession;
    if (type === "pane") {
      body.parent_pane_id = parentPaneID;
      body.split = split;
      body.size_percent = Number(sizePercent);
    }
    setSubmitting(true);
    setError("");
    saveRuntime(runtime);
    try {
      const result = await api("/tmux/create", body);
      if (!object(result) || !/^%\d+$/.test(text(result.pane_id)) || !text(result.target)) {
        throw new ApiError("Server returned an invalid creation result", {
          category: "protocol", endpoint: "/api/tmux/create",
        });
      }
      onCreated(result);
    } catch (reason) {
      setError(reason?.userMessage || reason?.message || "The tmux target could not be created.");
      setSubmitting(false);
    }
  };

  const canSubmit = !blocked && info?.enabled && cwd && selectedRuntime?.available
    && (type !== "window" || parentSession)
    && (type !== "pane" || parentPaneID)
    && (type === "pane" || (!nameTouched || /^[A-Za-z0-9_-]{1,64}$/.test(name)));

  return html`<${Dialog} title="Create tmux target" subtitle="Start detached work inside an allowed directory" onClose=${onClose} className="creation-dialog">
    <form class="creation-form" onSubmit=${(event) => { event.preventDefault(); void submit(); }}>
      ${blocked ? html`<${InlineNotice} tone="warning" icon="lock-keyhole"><strong>Creation unavailable</strong><p>${blocked}</p><//>` : null}
      ${loading ? html`<div class="creation-loading"><${Spinner} label="Loading creation settings" />Loading server configuration…</div>` : null}
      ${!loading && info ? html`<${Segmented} value=${type} onChange=${setType} label="Creation type" options=${[
        ["session", "Session"], ["window", "Window"], ["pane", "Pane"],
      ]} />` : null}

      ${!loading && info ? html`<div class="creation-fields">
        ${type === "window" ? html`<label class="field-stack"><span>Parent session</span><select value=${parentSession} onChange=${(event) => setParentSession(event.target.value)}>
          ${sessions.map((session) => html`<option value=${session} key=${session}>${session}</option>`)}
        </select></label>` : null}
        ${type === "pane" ? html`<label class="field-stack"><span>Parent pane</span><select value=${parentPaneID} onChange=${(event) => setParentPaneID(event.target.value)}>
          ${livePanes.map((pane) => html`<option value=${pane.id} key=${pane.id}>${pane.name} · ${pane.target}</option>`)}
        </select></label>` : null}

        <section class="creation-path"><header><div><span>Directory</span><small>Only configured roots are exposed</small></div></header>
          <div class="creation-shortcuts">
            ${info.roots.map((root) => html`<button type="button" class="button quiet" key=${root.path} onClick=${() => loadDirectory(root.path)}><${Icon} name="folder" size=${16} />${root.label}</button>`)}
            ${info.recents.map((recent) => html`<button type="button" class="button quiet" key=${recent.path} onClick=${() => loadDirectory(recent.path)}><${Icon} name="history" size=${16} />${recent.name}</button>`)}
          </div>
          <div class="creation-manual"><input aria-label="Creation directory" value=${cwd} placeholder="/absolute/path or ~/path" onInput=${(event) => {
            directoryRequest.current += 1;
            setBrowsing(false);
            setCwd(event.target.value);
          }} /><button type="button" class="button secondary" disabled=${!cwd} onClick=${() => loadDirectory(cwd)}>${browsing ? "Opening…" : "Browse"}</button></div>
          ${directory ? html`<nav class="creation-breadcrumbs" aria-label="Directory breadcrumbs">${breadcrumbs(directory).map((crumb) => html`<button type="button" key=${crumb.path} onClick=${() => loadDirectory(crumb.path)}>${crumb.label}</button>`)}</nav>` : null}
          ${directory ? html`<div class="creation-directories" role="list" aria-label="Child directories">
            ${directory.parent ? html`<button type="button" role="listitem" onClick=${() => loadDirectory(directory.parent)}><${Icon} name="corner-left-up" size=${17} /><span><strong>Parent directory</strong><small>${directory.parent}</small></span></button>` : null}
            ${directory.directories.map((child) => html`<button type="button" role="listitem" key=${child.path} onClick=${() => loadDirectory(child.path)}><${Icon} name="folder" size=${17} /><span><strong>${child.name}</strong><small>${child.path}</small></span></button>`)}
            ${!directory.directories.length ? html`<p>No child directories.</p>` : null}
            ${directory.truncated ? html`<p>Showing the first 500 directories.</p>` : null}
          </div>` : null}
        </section>

        <fieldset class="creation-runtimes"><legend>Runtime</legend>${runtimeItems.map((item) => html`<button type="button" class=${cx(runtime === item.id && "selected")} aria-pressed=${runtime === item.id} disabled=${!item.available} key=${item.id} onClick=${() => { setRuntime(item.id); saveRuntime(item.id); }}><${Icon} name=${item.id === "shell" ? "square-terminal" : "bot"} size=${18} /><span><strong>${item.label}</strong><small>${item.available ? "Available" : item.reason || "Unavailable"}</small></span></button>`)}</fieldset>

        ${type !== "pane" ? html`<label class="field-stack"><span>Name <small>letters, numbers, _ or -</small></span><input value=${name} maxlength="64" onInput=${(event) => { setNameTouched(true); setName(event.target.value); }} /></label>` : html`<div class="creation-split">
          <${Segmented} value=${split} onChange=${setSplit} label="Split direction" options=${[["side_by_side", "Side by side"], ["stacked", "Stacked"]]} />
          <label><span>New pane size</span><input type="range" min="10" max="90" step="1" value=${sizePercent} onInput=${(event) => setSizePercent(Number(event.target.value))} /><output>${sizePercent}%</output></label>
        </div>`}
      </div>` : null}

      ${error ? html`<${InlineNotice} tone="error" icon="triangle-alert"><strong>Could not continue</strong><p>${error}</p><//>` : null}
      <div class="creation-actions"><button type="button" class="button" onClick=${onClose}>Cancel</button><button type="submit" class="button primary" disabled=${submitting || !canSubmit}>${submitting ? html`<${Spinner} label="Creating" />` : html`<${Icon} name="plus" size=${17} />`} ${submitting ? "Creating…" : `Create ${type}`}</button></div>
    </form>
  <//>`;
}
