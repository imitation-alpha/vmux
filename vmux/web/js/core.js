const React = window.React;

if (!React || !window.ReactDOM || !window.htm) {
  throw new Error("vmux UI runtime failed to load");
}

export const {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} = React;
export const Fragment = React.Fragment;
export const html = window.htm.bind(React.createElement);

export function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function Icon({ name, size = 20, label = null, className = "" }) {
  // The vendored sprite is intentionally small. These aliases keep component
  // names descriptive while reusing the closest licensed Lucide glyph.
  const aliases = {
    "arrow-down-to-line": "download",
    "chart-no-axes-combined": "chart-no-axes-column",
    "check-circle-2": "circle-check",
    "corner-down-left": "reply",
    "loader-circle": "loader",
    "list-checks": "list",
    "lock-keyhole": "shield-alert",
    "maximize-2": "maximize",
    "minimize-2": "minimize",
    "network": "folder-tree",
    "notebook-tabs": "clipboard",
    "package-x": "x-circle",
    "panel-right": "panel-left",
    "panels-top-left": "layout-grid",
    "pencil-line": "pencil",
    "radio-tower": "radio",
    "search-x": "search",
    "sun-moon": "sun",
    "trash-2": "trash",
  };
  const symbol = aliases[name] || name;
  return html`<svg
    class=${cx("icon", className)}
    width=${size}
    height=${size}
    viewBox="0 0 24 24"
    aria-hidden=${label ? null : "true"}
    role=${label ? "img" : null}
  >
    ${label ? html`<title>${label}</title>` : null}
    <use href=${`/icons/lucide.svg#${symbol}`}></use>
  </svg>`;
}

export function useLayoutMode() {
  const read = () => window.innerWidth < 820 ? "compact" : (window.innerWidth < 1200 ? "medium" : "wide");
  const [mode, setMode] = useState(read);
  useEffect(() => {
    const compact = matchMedia("(max-width: 819px)");
    const medium = matchMedia("(min-width: 820px) and (max-width: 1199px)");
    const update = () => setMode(compact.matches ? "compact" : (medium.matches ? "medium" : "wide"));
    compact.addEventListener("change", update);
    medium.addEventListener("change", update);
    window.addEventListener("resize", update);
    return () => {
      compact.removeEventListener("change", update);
      medium.removeEventListener("change", update);
      window.removeEventListener("resize", update);
    };
  }, []);
  return mode;
}

export function usePrevious(value) {
  const ref = useRef(value);
  useEffect(() => { ref.current = value; }, [value]);
  return ref.current;
}

export function formatAge(timestamp) {
  if (!timestamp) return "No activity";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (seconds < 5) return "Now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function formatNumber(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return new Intl.NumberFormat().format(n);
}

export function formatCost(value) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Number(value || 0) < 10 ? 2 : 0,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

const FOCUSABLE = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");
const DIALOG_STACK = [];

/** Focus-managed modal/sheet used by compact sheets and desktop overlays. */
export function Dialog({
  title,
  subtitle = "",
  onClose,
  children,
  className = "",
  side = false,
  actions = null,
}) {
  const panel = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const titleId = useMemo(() => `dialog-${Math.random().toString(36).slice(2)}`, []);

  useEffect(() => {
    const dialogToken = {};
    DIALOG_STACK.push(dialogToken);
    const restore = document.activeElement;
    const node = panel.current;
    const timer = setTimeout(() => {
      const target = node && (node.querySelector("[autofocus]") || node.querySelector(FOCUSABLE));
      (target || node)?.focus();
    }, 0);
    const onKey = (event) => {
      if (DIALOG_STACK[DIALOG_STACK.length - 1] !== dialogToken) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) {
        event.preventDefault();
        node.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      const index = DIALOG_STACK.lastIndexOf(dialogToken);
      if (index >= 0) DIALOG_STACK.splice(index, 1);
      if (restore && restore.isConnected && typeof restore.focus === "function") restore.focus();
    };
  }, []);

  return html`<div class=${cx("dialog-scrim", side && "side")} onMouseDown=${(e) => {
    if (e.target === e.currentTarget) onClose();
  }}>
    <section
      ref=${panel}
      class=${cx("dialog-panel", "glass", side && "dialog-side", className)}
      role="dialog"
      aria-modal="true"
      aria-labelledby=${titleId}
      tabindex="-1"
    >
      <header class="dialog-head">
        <div class="dialog-heading">
          <h2 id=${titleId}>${title}</h2>
          ${subtitle ? html`<p>${subtitle}</p>` : null}
        </div>
        ${actions}
        <button class="icon-button" aria-label="Close" onClick=${onClose}>
          <${Icon} name="x" />
        </button>
      </header>
      <div class="dialog-body">${children}</div>
    </section>
  </div>`;
}

export function Spinner({ label = "Loading" }) {
  return html`<span class="spinner" role="status"><span aria-hidden="true"></span><span class="sr-only">${label}</span></span>`;
}

export function EmptyState({ icon = "inbox", title, detail, action = null }) {
  return html`<div class="empty-state">
    <div class="empty-icon"><${Icon} name=${icon} size=${28} /></div>
    <h3>${title}</h3>
    ${detail ? html`<p>${detail}</p>` : null}
    ${action}
  </div>`;
}

export function Segmented({ value, options, onChange, label = "View" }) {
  return html`<div class="segmented" role="group" aria-label=${label}>
    ${options.map(([key, text, badge]) => html`<button
      key=${key}
      type="button"
      aria-pressed=${value === key}
      class=${value === key ? "selected" : ""}
      onClick=${() => onChange(key)}
    >${text}${badge ? html`<span class="tab-count">${badge}</span>` : null}</button>`)}
  </div>`;
}

export function InlineNotice({ tone = "neutral", icon = "info", children, action = null }) {
  return html`<div class=${cx("inline-notice", `notice-${tone}`)} role=${tone === "error" ? "alert" : "status"}>
    <${Icon} name=${icon} size=${18} />
    <div>${children}</div>
    ${action}
  </div>`;
}
