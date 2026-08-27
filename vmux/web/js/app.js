import {
  Dialog,
  Icon,
  InlineNotice,
  Spinner,
  cx,
  html,
  useCallback,
  useEffect,
  useLayoutMode,
  useMemo,
  useRef,
  useState,
} from "./core.js";
import {
  TOKEN_KEY,
  clearCredentials,
  useActions,
  usePrefs,
  useVmuxState,
  vmuxStore,
} from "./state.js";
import { SettingsOverlay } from "./settings.js";
import { paneMatchesFilter, TokenGate } from "./ui.js";
import { AgentWorkspace } from "./agent-ui.js";
import { navigateAgentRoute, useAgentState } from "./agent-state.js";
import { CreationDialog, creationCapability } from "./creation.js";
import { UsageProvider } from "./usage.js";

function useServiceWorkerLifecycle() {
  const [registration, setRegistration] = useState(null);
  const [waiting, setWaiting] = useState(null);
  const [error, setError] = useState("");
  const activating = useRef(false);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return undefined;
    let disposed = false;
    let currentRegistration = null;

    const watch = (reg) => {
      currentRegistration = reg;
      if (disposed) return;
      setRegistration(reg);
      if (reg.waiting && navigator.serviceWorker.controller) setWaiting(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const worker = reg.installing;
        if (!worker) return;
        worker.addEventListener("statechange", () => {
          if (!disposed && worker.state === "installed" && navigator.serviceWorker.controller) setWaiting(worker);
        });
      });
    };

    const register = async () => {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" });
        watch(reg);
      } catch (err) {
        if (!disposed) setError("Offline support could not be initialized.");
        console.warn("[vmux] service worker registration failed", err instanceof Error ? err.name : "error");
      }
    };
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });

    const onController = () => {
      if (activating.current) location.reload();
    };
    navigator.serviceWorker.addEventListener("controllerchange", onController);
    const onVisible = () => {
      if (document.visibilityState === "visible" && currentRegistration) {
        currentRegistration.update().catch((err) => console.warn("[vmux] update check failed", err instanceof Error ? err.name : "error"));
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      window.removeEventListener("load", register);
      navigator.serviceWorker.removeEventListener("controllerchange", onController);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const activate = useCallback(() => {
    const worker = waiting || registration?.waiting;
    if (!worker) return;
    activating.current = true;
    worker.postMessage({ type: "ACTIVATE_UPDATE" });
  }, [registration, waiting]);

  return { waiting, error, activate, dismissError: () => setError("") };
}

export function attentionNotificationIDs(panes, review, alertErrors = false) {
  const reviewSettings = review?.settings;
  const batching = Boolean(reviewSettings?.enabled);
  const statuses = batching
    ? (reviewSettings?.urgent_bypass?.pane_errors ? ["error"] : [])
    : (alertErrors ? ["needs_input", "error"] : ["needs_input"]);
  const ids = new Set(
    panes.filter((pane) => statuses.includes(pane.status)).map((pane) => `pane:${pane.id}`),
  );
  if (!batching) {
    for (const pane of panes) {
      if (pane?.lifecycle?.state === "done") {
        ids.add(`done:${pane.id}:${pane.lifecycle.revision || 0}`);
      }
    }
  }
  for (const group of review?.groups || []) {
    for (const decision of group?.decisions || []) {
      const priority = String(decision?.priority || "").toLowerCase();
      if (decision?.status === "pending" && (priority === "high" || priority === "critical")) {
        ids.add(`decision:${decision.id}`);
      }
    }
  }
  return ids;
}

function useAttentionNotifications(panes, review) {
  const prefs = usePrefs();
  const previous = useRef(null);
  useEffect(() => {
    const current = attentionNotificationIDs(panes, review, prefs.alertErrors);
    const freshIDs = previous.current ? [...current].filter((id) => !previous.current.has(id)) : [];
    const fresh = freshIDs.length > 0;
    const completion = freshIDs.some((id) => id.startsWith("done:"));
    if (fresh && prefs.sound) {
      try {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (Context) {
          const context = new Context();
          const oscillator = context.createOscillator();
          const gain = context.createGain();
          oscillator.frequency.value = 820;
          oscillator.connect(gain); gain.connect(context.destination);
          gain.gain.setValueAtTime(.0001, context.currentTime);
          gain.gain.exponentialRampToValueAtTime(.12, context.currentTime + .01);
          gain.gain.exponentialRampToValueAtTime(.0001, context.currentTime + .35);
          oscillator.start(); oscillator.stop(context.currentTime + .37);
        }
      } catch (err) {
        console.warn("[vmux] attention sound failed", err instanceof Error ? err.name : "error");
      }
    }
    if (fresh && prefs.notify && "Notification" in window && Notification.permission === "granted") {
      try {
        // Deliberately generic: pane names, prompts, output, usage, and tokens
        // never enter OS notification history.
        new Notification("vmux", { body: completion ? "An agent completed its work." : "An agent needs your attention." });
      } catch (err) {
        console.warn("[vmux] notification failed", err instanceof Error ? err.name : "error");
      }
    }
    const needs = panes.filter((pane) => pane?.lifecycle?.state === "blocked" || pane.status === "needs_input").length;
    document.title = needs ? `(${needs}) vmux` : "vmux";
    previous.current = current;
  }, [panes, prefs.alertErrors, prefs.notify, prefs.sound, review]);
}

function UpdateBanner({ lifecycle }) {
  if (!lifecycle.waiting && !lifecycle.error) return null;
  return html`<aside class="update-banner glass" role="status" aria-live="polite">
    <${Icon} name=${lifecycle.waiting ? "download" : "triangle-alert"} />
    <div><strong>${lifecycle.waiting ? "Update ready" : "Offline support unavailable"}</strong>
      <span>${lifecycle.waiting ? "Reload when you’re ready to use the new shell." : lifecycle.error}</span></div>
    ${lifecycle.waiting ? html`<button class="button primary" onClick=${lifecycle.activate}>Update</button>`
      : html`<button class="icon-button" aria-label="Dismiss" onClick=${lifecycle.dismissError}><${Icon} name="x" /><//>`}
  </aside>`;
}

function ActionAnnouncement({ event }) {
  const [visible, setVisible] = useState(null);
  useEffect(() => {
    if (!event) return undefined;
    setVisible(event);
    const delay = event.status === "pending" ? 15000 : (event.status === "error" ? 6500 : 3500);
    const timer = setTimeout(() => setVisible((current) => current === event ? null : current), delay);
    return () => clearTimeout(timer);
  }, [event]);
  if (!visible) return html`<div class="sr-only" aria-live="polite"></div>`;
  return html`<div class="action-live" aria-live=${visible.status === "error" ? "assertive" : "polite"}>
    <div class=${cx("action-toast", visible.status === "error" && "error")} role=${visible.status === "error" ? "alert" : "status"}>
      ${visible.message || (visible.status === "pending" ? "Sending…" : visible.status === "success" ? "Sent." : "Action failed.")}
    </div>
  </div>`;
}

function scopeMatches(pane, scope) {
  return paneMatchesFilter(pane, scope);
}

function BroadcastDialog({ panes, connection, onClose }) {
  const actions = useActions();
  const [scope, setScope] = useState("queue");
  const [text, setText] = useState("");
  const [enter, setEnter] = useState(true);
  const [sending, setSending] = useState(false);
  const [sendingCount, setSendingCount] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const scoped = useMemo(() => panes.filter((pane) => scopeMatches(pane, scope)), [panes, scope]);
  const recipients = useMemo(() => scoped.filter((pane) => actions.canAct(pane)), [scoped, actions]);
  const excluded = scoped.length - recipients.length;

  const send = async (retryPanes = null) => {
    const target = Array.isArray(retryPanes) ? retryPanes : recipients;
    if (!text.trim() || !target.length || sending) return;
    setSending(true); setSendingCount(target.length); setError(""); setResult(null);
    try {
      const next = await actions.broadcast(target, text, enter);
      setResult(next);
    } catch (err) {
      setError(err.userMessage || err.message || "The broadcast could not be confirmed.");
    } finally {
      setSending(false); setSendingCount(0);
    }
  };

  return html`<${Dialog} title="Broadcast" subtitle="Send one message to actionable panes" onClose=${onClose} className="broadcast-dialog">
    <div class="broadcast-body">
      <div class="broadcast-scopes" role="group" aria-label="Broadcast recipients">
        ${[["queue", "Queue"], ["active", "Active"], ["all", "All"]].map(([key, label]) => {
          const count = panes.filter((pane) => scopeMatches(pane, key) && actions.canAct(pane)).length;
          return html`<button aria-pressed=${scope === key} class=${scope === key ? "selected" : ""} key=${key} onClick=${() => { setScope(key); setResult(null); }}>${label}<b>${count}</b></button>`;
        })}
      </div>
      <div class="recipient-summary"><strong>${recipients.length} actionable</strong><span>${excluded ? `${excluded} offline or unavailable excluded` : "No offline panes in this scope"}</span></div>
      <label class="field-stack"><span>Message</span><textarea rows="6" autoFocus=${true} placeholder=${`Message ${recipients.length} pane${recipients.length === 1 ? "" : "s"}…`} value=${text} onInput=${(event) => setText(event.target.value)}></textarea></label>
      <label class="broadcast-enter"><input type="checkbox" checked=${enter} onChange=${(event) => setEnter(event.target.checked)} /> Press Enter after the message</label>
      ${sending ? html`<div class="broadcast-progress" role="status"><${Spinner} label="Sending broadcast" /><div><strong>Sending to ${sendingCount} pane${sendingCount === 1 ? "" : "s"}…</strong><span>Keep this dialog open for completion details.</span></div></div>` : null}
      ${result ? html`<${InlineNotice} tone=${result.errors.length ? "warning" : "neutral"} icon=${result.errors.length ? "triangle-alert" : "circle-check"}>
        <strong>${result.errors.length ? "Broadcast partially completed" : "Broadcast complete"}</strong>
        <p>Sent to ${result.sent} of ${result.requested}. ${result.excluded?.length ? `${result.excluded.length} unavailable panes were excluded.` : ""}</p>
        ${result.failedIds?.length ? html`<p>Could not send to: ${result.failedIds.map((id) => panes.find((pane) => pane.id === id)?.name || id).join(", ")}.</p>` : null}
        ${result.retryPanes?.length ? html`<button class="button" onClick=${() => send(result.retryPanes)}>Retry ${result.retryPanes.length} failed</button>` : null}
      <//>` : null}
      ${error ? html`<${InlineNotice} tone="error" icon="triangle-alert"><strong>Broadcast failed</strong><p>${error}</p><button class="button" onClick=${() => send()}>Retry</button><//>` : null}
      <div class="broadcast-actions"><button class="button" onClick=${onClose}>${result ? "Done" : "Cancel"}</button><button class="button primary" disabled=${sending || !text.trim() || !recipients.length} onClick=${() => send()}>
        ${sending ? html`<${Spinner} label="Sending" />` : html`<${Icon} name="send" />`} ${sending ? "Sending…" : `Send to ${recipients.length}`}
      </button></div>
    </div>
  <//>`;
}

function App() {
  const state = useVmuxState();
  const agentState = useAgentState();
  const prefs = usePrefs();
  const layout = useLayoutMode();
  const lifecycle = useServiceWorkerLifecycle();
  const [broadcast, setBroadcast] = useState(false);
  const [settings, setSettings] = useState(false);
  const [creation, setCreation] = useState(null);
  const [pendingCreated, setPendingCreated] = useState(null);
  const [createdPaneID, setCreatedPaneID] = useState("");
  const [creationNotice, setCreationNotice] = useState(null);
  const [configOverride, setConfigOverride] = useState(null);
  const config = configOverride || state.config;
  const createCapability = creationCapability(config);
  useAttentionNotifications(
    state.panes,
    config?.experimental_agent_workspace_enabled === true ? agentState.review : null,
  );

  useEffect(() => {
    let mounted = true;
    const stopForCredentialClear = () => vmuxStore.stop();
    globalThis.addEventListener?.("vmux:credentials-cleared", stopForCredentialClear);
    vmuxStore.start().catch((err) => {
      if (mounted) console.warn("[vmux] transport startup failed", err instanceof Error ? err.name : "error");
    });
    return () => {
      mounted = false;
      globalThis.removeEventListener?.("vmux:credentials-cleared", stopForCredentialClear);
      vmuxStore.stop();
    };
  }, []);
  useEffect(() => { if (state.config) setConfigOverride(state.config); }, [state.config]);
  useEffect(() => {
    if (!pendingCreated) return undefined;
    if (state.panes.some((pane) => pane.id === pendingCreated.paneId)) {
      if (config?._info?.capabilities?.agent_context_v1?.enabled === true) {
        navigateAgentRoute("panes", pendingCreated.paneId);
      } else {
        setCreatedPaneID(pendingCreated.paneId);
      }
      setCreationNotice({
        status: "success",
        message: `Created ${pendingCreated.target} and opened its pane.`,
      });
      setPendingCreated(null);
      return undefined;
    }
    const remaining = Math.max(0, pendingCreated.expiresAt - Date.now());
    const timer = setTimeout(() => {
      setCreationNotice({
        status: "success",
        message: `Created ${pendingCreated.target}, but its pane has not appeared. Refresh the workspace.`,
      });
      setPendingCreated(null);
    }, remaining);
    return () => clearTimeout(timer);
  }, [config, pendingCreated, state.panes]);
  useEffect(() => {
    if (!creationNotice) return undefined;
    const timer = setTimeout(() => setCreationNotice(null), 7000);
    return () => clearTimeout(timer);
  }, [creationNotice]);

  const retry = useCallback(() => {
    vmuxStore.retry().catch((err) => console.warn("[vmux] manual retry failed", err instanceof Error ? err.name : "error"));
  }, []);
  const submitToken = async (token) => {
    await clearCredentials({ reload: false });
    localStorage.setItem(TOKEN_KEY, token);
    location.replace(`${location.pathname}${location.hash || ""}`);
  };
  const created = (result) => {
    setCreation(null);
    setCreationNotice({ status: "success", message: `Created ${result.target}. Opening its pane…` });
    setPendingCreated({ paneId: result.pane_id, target: result.target, expiresAt: Date.now() + 10000 });
    vmuxStore.refreshState().catch((error) => {
      console.warn("[vmux] post-creation refresh failed", error instanceof Error ? error.name : "error");
    });
  };

  if (state.connection.mode === "unauthorized") {
    return html`<${TokenGate} onSubmit=${submitToken} />`;
  }
  return html`<${UsageProvider}
    threshold=${config?.usage_alert_threshold ?? 20}
    hiddenProviders=${config?.usage_hidden_quota_providers || []}
    hiddenMetrics=${config?.usage_hidden_quota_metrics || []}
  >
    <${AgentWorkspace}
      layout=${layout}
      panes=${state.panes}
      connection=${state.connection}
      config=${config}
      onRetry=${retry}
      onBroadcast=${() => setBroadcast(true)}
      onSettings=${() => setSettings(true)}
      onCreate=${createCapability.supported ? (initial = {}) => setCreation(initial) : null}
      openPaneId=${createdPaneID}
    />
    ${broadcast ? html`<${BroadcastDialog} panes=${state.panes} connection=${state.connection} onClose=${() => setBroadcast(false)} />` : null}
    ${settings ? html`<${SettingsOverlay}
      layout=${layout}
      panes=${state.panes}
      connection=${state.connection}
      config=${config}
      onConfig=${setConfigOverride}
      onRetry=${retry}
      onClose=${() => setSettings(false)}
    />` : null}
    ${creation ? html`<${CreationDialog}
      panes=${state.panes}
      connection=${state.connection}
      config=${config}
      initial=${creation}
      onClose=${() => setCreation(null)}
      onCreated=${created}
    />` : null}
    <${ActionAnnouncement} event=${creationNotice || state.lastEvent} />
    <${UpdateBanner} lifecycle=${lifecycle} />
  <//>`;
}

window.addEventListener("focusout", (event) => {
  const tag = String(event.target?.tagName || "").toLowerCase();
  const next = String(event.relatedTarget?.tagName || "").toLowerCase();
  if ((tag === "input" || tag === "textarea") && next !== "input" && next !== "textarea") {
    setTimeout(() => window.scrollTo(0, 0), 100);
  }
});

window.ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
