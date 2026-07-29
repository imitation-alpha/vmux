import {
  EmptyState,
  Icon,
  InlineNotice,
  Segmented,
  Spinner,
  createContext,
  cx,
  formatCost,
  formatNumber,
  html,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "./core.js";
import { api } from "./state.js";

const UsageContext = createContext(null);
const RANGE_OPTIONS = [["today", "Today"], ["7d", "7D"], ["30d", "30D"], ["months", "Months"]];
const METRIC_OPTIONS = [["cost", "Cost"], ["tokens", "Tokens"]];
const COLORS = ["#0a84ff", "#bf5af2", "#30d158", "#ff9f0a", "#ff375f", "#64d2ff", "#ffd60a"];

function classify(snapshot) {
  if (!snapshot) return "loading";
  if (snapshot.available) {
    if (snapshot.stale) return "stale";
    if (!snapshot.today && !(snapshot.quotas || []).length) return "empty";
    return "ready";
  }
  return snapshot.reason || "error";
}

export function UsageProvider({ threshold = 20, children }) {
  const [snapshot, setSnapshot] = useState(null);
  const [histories, setHistories] = useState({});
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);

  const loadSummary = useCallback(async () => {
    setStatus((s) => snapshot ? s : "loading");
    setError("");
    try {
      const next = await api("/usage", null, "GET", { timeout: 15000 });
      setSnapshot(next);
      setStatus(classify(next));
      return next;
    } catch (err) {
      setError(err.userMessage || err.message || "Usage could not be loaded.");
      setStatus(snapshot?.available ? "stale" : (err.category === "timeout" ? "timeout" : "error"));
      throw err;
    }
  }, [snapshot]);

  useEffect(() => {
    loadSummary().catch((err) => console.warn("vmux usage summary:", err.category || "error"));
  }, []);

  const loadHistory = useCallback(async (range, force = false) => {
    if (histories[range] && !force) return histories[range];
    const period = range === "months" ? "monthly" : "daily";
    const days = range === "today" ? 1 : (range === "7d" ? 7 : (range === "30d" ? 30 : null));
    const suffix = `/usage/history?period=${period}${days ? `&days=${days}` : ""}`;
    try {
      const next = await api(suffix, null, "GET", { timeout: 15000 });
      setHistories((all) => ({ ...all, [range]: next }));
      return next;
    } catch (err) {
      setHistories((all) => ({
        ...all,
        [range]: { available: false, reason: err.category || "error", detail: err.userMessage || err.message, buckets: [] },
      }));
      throw err;
    }
  }, [histories]);

  const refresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError("");
    try {
      // A full refresh may hit one 30-second quota timeout plus three
      // sequential 120-second report timeouts on the server.
      const next = await api("/usage/refresh", { scope: "all" }, "POST", { timeout: 450000 });
      setSnapshot(next);
      setStatus(classify(next));
      setHistories({});
      setHistoryRevision((value) => value + 1);
    } catch (err) {
      setError(err.userMessage || err.message || "Refresh failed.");
      setStatus(snapshot?.available ? "stale" : (err.category === "timeout" ? "timeout" : "error"));
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, snapshot]);

  const warnings = useMemo(() => {
    if (!snapshot?.available || Number(threshold) <= 0) return [];
    return (snapshot.quotas || []).flatMap((quota) => (quota.metrics || []).map((metric) => {
      const remaining = metric.remaining_percent == null && metric.used_percent != null
        ? 100 - Number(metric.used_percent) : Number(metric.remaining_percent);
      return { ...metric, provider: quota.provider, remaining };
    })).filter((metric) => Number.isFinite(metric.remaining) && metric.remaining <= Number(threshold));
  }, [snapshot, threshold]);

  const value = useMemo(() => ({
    snapshot, histories, status, error, refreshing, historyRevision, threshold: Number(threshold), warnings,
    loadSummary, loadHistory, refresh,
  }), [snapshot, histories, status, error, refreshing, historyRevision, threshold, warnings, loadSummary, loadHistory, refresh]);
  return html`<${UsageContext.Provider} value=${value}>${children}<//>`;
}

export function useUsage() {
  const value = useContext(UsageContext);
  if (!value) throw new Error("useUsage must be used inside UsageProvider");
  return value;
}

function resetLabel(metric) {
  if (metric.resets_at) {
    const seconds = Number(metric.resets_at) - Date.now() / 1000;
    if (seconds > 0) {
      const days = Math.floor(seconds / 86400);
      const hours = Math.floor((seconds % 86400) / 3600);
      const minutes = Math.max(1, Math.floor((seconds % 3600) / 60));
      return days ? `Resets in ${days}d ${hours}h` : (hours ? `Resets in ${hours}h ${minutes}m` : `Resets in ${minutes}m`);
    }
  }
  return metric.resets_at_raw ? `Resets ${metric.resets_at_raw}` : "Reset time unavailable";
}

function QuotaMeters({ quotas, threshold }) {
  if (!quotas?.length) return html`<${EmptyState} icon="gauge" title="No provider quotas" detail="Usage totals are available, but no provider quota data was returned." />`;
  return html`<div class="quota-grid">
    ${quotas.map((quota) => html`<article class="quota-card" key=${quota.provider}>
      <header>
        <div><h3>${quota.provider}</h3><p>${[quota.plan, quota.account].filter(Boolean).join(" · ") || "Provider quota"}</p></div>
      </header>
      ${(quota.metrics || []).length ? quota.metrics.map((metric) => {
        const remaining = metric.remaining_percent == null && metric.used_percent != null
          ? Math.max(0, 100 - Number(metric.used_percent)) : Number(metric.remaining_percent);
        const known = Number.isFinite(remaining);
        const low = known && threshold > 0 && remaining <= threshold;
        return html`<div class=${cx("quota-meter", low && "warning")} key=${metric.label}>
          <div class="quota-label"><span>${metric.label}</span><strong>${known ? `${Math.round(remaining)}% left` : (metric.remaining_label || "Unknown")}</strong></div>
          <div class="meter-track" role="progressbar" aria-label=${`${quota.provider} ${metric.label} remaining`} aria-valuemin="0" aria-valuemax="100" aria-valuenow=${known ? Math.round(remaining) : null}>
            <span style=${{ width: `${known ? Math.max(0, Math.min(100, remaining)) : 0}%` }}></span>
          </div>
          <div class="quota-reset">${resetLabel(metric)}</div>
        </div>`;
      }) : html`<p class="secondary">No quota metrics reported.</p>`}
    </article>`)}
  </div>`;
}

function SummaryCards({ today }) {
  const totals = today?.totals || {};
  const delta = today?.cost_delta_pct;
  return html`<div class="usage-summary-grid">
    <article class="metric-card primary"><span>Today’s cost</span><strong>${formatCost(totals.cost)}</strong>
      <small>${delta == null ? "No yesterday comparison" : `${delta > 0 ? "+" : ""}${delta}% vs yesterday`}</small></article>
    <article class="metric-card"><span>Tokens</span><strong>${formatNumber(totals.total)}</strong><small>${formatNumber(totals.input)} in · ${formatNumber(totals.output)} out</small></article>
    <article class="metric-card"><span>Messages</span><strong>${formatNumber(totals.messages)}</strong><small>${today?.date || "Today"}</small></article>
  </div>`;
}

function TopBreakdown({ title, rows, keyName, metric }) {
  return html`<article class="breakdown-card">
    <h3>${title}</h3>
    ${rows?.length ? html`<ol>${rows.map((row) => html`<li key=${row[keyName]}>
      <span>${row[keyName] || "Unknown"}</span><strong>${metric === "cost" ? formatCost(row.cost) : formatNumber(row.total)}</strong>
    </li>`)}</ol>` : html`<p class="secondary">No breakdown available.</p>`}
  </article>`;
}

function bucketValue(bucket, metric) {
  return metric === "cost" ? Number(bucket?.totals?.cost || 0) : Number(bucket?.totals?.total || 0);
}

function UsageChart({ buckets, metric }) {
  const data = buckets || [];
  const clientSeries = useMemo(() => {
    const totals = new Map();
    for (const bucket of data) {
      for (const row of bucket.by_client || []) {
        const key = row.client || "Unknown";
        const value = metric === "cost" ? Number(row.cost || 0) : Number(row.total || 0);
        totals.set(key, (totals.get(key) || 0) + value);
      }
    }
    const ordered = [...totals].sort((a, b) => b[1] - a[1]).map(([name]) => name);
    const tolerance = metric === "cost" ? 0.005 : 0.5;
    const hasUnattributed = data.some((bucket) => {
      const reported = (bucket.by_client || []).reduce((sum, row) => (
        sum + (metric === "cost" ? Number(row.cost || 0) : Number(row.total || 0))
      ), 0);
      return bucketValue(bucket, metric) - reported > tolerance;
    });
    const hasOther = ordered.length > COLORS.length || hasUnattributed;
    const direct = ordered.slice(0, hasOther ? COLORS.length - 1 : COLORS.length);
    return { direct, names: hasOther ? [...direct, "Other"] : direct };
  }, [data, metric]);
  const max = Math.max(1, ...data.map((bucket) => bucketValue(bucket, metric)));
  const width = 800, height = 250, top = 16, bottom = 42, left = 46, right = 12;
  const plotH = height - top - bottom, plotW = width - left - right;
  const slot = plotW / Math.max(1, data.length), barW = Math.max(3, Math.min(44, slot * 0.7));
  const valuesFor = (bucket) => {
    const total = bucketValue(bucket, metric);
    const rows = bucket.by_client || [];
    const values = clientSeries.direct.map((client) => {
      const row = rows.find((candidate) => (candidate.client || "Unknown") === client);
      return row ? (metric === "cost" ? Number(row.cost || 0) : Number(row.total || 0)) : 0;
    });
    if (clientSeries.names.length > clientSeries.direct.length) {
      values.push(Math.max(0, total - values.reduce((sum, value) => sum + value, 0)));
    }
    const represented = values.reduce((sum, value) => sum + value, 0);
    if (total >= 0 && represented > total && represented > 0) {
      return values.map((value) => value * total / represented);
    }
    return values;
  };
  const formatMetric = (value) => metric === "cost" ? formatCost(value) : formatNumber(value);

  if (!data.length) return html`<${EmptyState} icon="chart-no-axes-column" title="No history yet" detail="Refresh usage after tokscale has scanned at least one report period." />`;

  return html`<div class="usage-chart-wrap">
    <svg class="usage-chart" viewBox=${`0 0 ${width} ${height}`} role="img" aria-labelledby="usage-chart-title usage-chart-desc">
      <title id="usage-chart-title">${metric === "cost" ? "Cost" : "Token"} usage by period</title>
      <desc id="usage-chart-desc">${data.length} periods. The accessible table below contains exact values.</desc>
      ${[0, .25, .5, .75, 1].map((part) => {
        const y = top + plotH * (1 - part);
        return html`<g key=${part}><line x1=${left} x2=${width - right} y1=${y} y2=${y} class="chart-grid" />
          <text x=${left - 8} y=${y + 4} text-anchor="end">${metric === "cost" ? `$${(max * part).toFixed(max < 10 ? 1 : 0)}` : formatNumber(max * part)}</text></g>`;
      })}
      ${data.map((bucket, index) => {
        const x = left + slot * index + (slot - barW) / 2;
        const total = bucketValue(bucket, metric);
        const values = clientSeries.names.length ? valuesFor(bucket) : [];
        let cursor = top + plotH;
        const segments = values.length ? values.map((value, ci) => {
          const h = Math.max(0, value / max * plotH);
          cursor -= h;
          return html`<rect key=${ci} x=${x} y=${cursor} width=${barW} height=${h} rx="2" fill=${COLORS[ci % COLORS.length]} />`;
        }) : (() => {
          const h = total / max * plotH;
          return [html`<rect key="total" x=${x} y=${top + plotH - h} width=${barW} height=${h} rx="2" fill=${COLORS[0]} />`];
        })();
        const label = String(bucket.bucket || "");
        const short = label.length > 7 ? label.slice(5) : label;
        return html`<g key=${label}>${segments}<text x=${x + barW / 2} y=${height - 18} text-anchor="middle">${data.length > 16 && index % 3 ? "" : short}</text></g>`;
      })}
    </svg>
    <div class="chart-legend" aria-hidden="true">
      ${(clientSeries.names.length ? clientSeries.names : ["Total"]).map((name, index) => html`<span key=${name}><i style=${{ background: COLORS[index % COLORS.length] }}></i>${name}</span>`)}
    </div>
    <div class="table-scroll">
      <table class="usage-table">
        <caption>Exact totals and stacked client composition by period</caption>
        <thead><tr><th scope="col">Period</th><th scope="col">Cost</th><th scope="col">Tokens</th><th scope="col">Messages</th>
          ${clientSeries.names.map((name) => html`<th scope="col" key=${name}>${name} ${metric === "cost" ? "cost" : "tokens"}</th>`)}</tr></thead>
        <tbody>${data.map((bucket) => html`<tr key=${bucket.bucket}><th scope="row">${bucket.bucket}</th><td>${formatCost(bucket.totals?.cost)}</td><td>${formatNumber(bucket.totals?.total)}</td><td>${formatNumber(bucket.totals?.messages)}</td>
          ${valuesFor(bucket).map((value, index) => html`<td key=${clientSeries.names[index]}>${formatMetric(value)}</td>`)}</tr>`)}</tbody>
        <tfoot><tr><th scope="row">Total</th><td>${formatCost(data.reduce((n, b) => n + Number(b.totals?.cost || 0), 0))}</td><td>${formatNumber(data.reduce((n, b) => n + Number(b.totals?.total || 0), 0))}</td><td>${formatNumber(data.reduce((n, b) => n + Number(b.totals?.messages || 0), 0))}</td>
          ${clientSeries.names.map((name, index) => html`<td key=${name}>${formatMetric(data.reduce((sum, bucket) => sum + valuesFor(bucket)[index], 0))}</td>`)}</tr></tfoot>
      </table>
    </div>
  </div>`;
}

function Unavailable({ status, detail, onRefresh, refreshing }) {
  const content = {
    disabled: ["toggle-left", "Usage tracking is disabled", "Enable it in Settings → Usage, then refresh."],
    not_installed: ["x-circle", "tokscale is not installed", "Install tokscale on the vmux server, then refresh."],
    timeout: ["clock", "Usage collection timed out", "The last scan exceeded its time limit. Existing pane controls are unaffected."],
    error: ["triangle-alert", "Usage is unavailable", detail || "The server could not collect usage data."],
    empty: ["chart-no-axes-column", "No usage data yet", "The collector is ready but has not returned any totals or quotas."],
  }[status] || ["chart-no-axes-column", "Usage is unavailable", detail || "Try again shortly."];
  return html`<${EmptyState} icon=${content[0]} title=${content[1]} detail=${content[2]}
    action=${html`<button class="button primary" disabled=${refreshing || status === "disabled"} onClick=${onRefresh}>${refreshing ? html`<${Spinner} label="Refreshing usage" />` : html`<${Icon} name="refresh-cw" />`} Refresh</button>`} />`;
}

export function UsageDashboard() {
  const usage = useUsage();
  const [metric, setMetric] = useState("cost");
  const [range, setRange] = useState("7d");
  const history = usage.histories[range];
  const historyLoading = usage.snapshot?.available && !history;

  useEffect(() => {
    if (!usage.snapshot?.available) return;
    usage.loadHistory(range).catch((err) => console.warn("vmux usage history:", err.category || "error"));
  }, [usage.snapshot?.available, range, usage.historyRevision]);

  return html`<section class="stats-page" aria-labelledby="stats-title">
    <header class="stats-head">
      <div><p class="eyebrow">Usage & quotas</p><h1 id="stats-title">Stats</h1>
        <p>Current spend, activity, and provider capacity from your local tokscale collector.</p></div>
      <button class="button" onClick=${usage.refresh} disabled=${usage.refreshing || usage.status === "disabled"}>
        ${usage.refreshing ? html`<${Spinner} label="Refreshing usage" />` : html`<${Icon} name="refresh-cw" />`} ${usage.refreshing ? "Refreshing…" : "Refresh"}
      </button>
    </header>

    ${usage.error ? html`<${InlineNotice} tone="error" icon="triangle-alert">${usage.error}<//>` : null}
    ${usage.status === "stale" ? html`<${InlineNotice} tone="warning" icon="clock">Showing the last successful snapshot because the newest refresh failed.<//>` : null}
    ${usage.warnings.length ? html`<${InlineNotice} tone="warning" icon="gauge">${usage.warnings.length} provider quota ${usage.warnings.length === 1 ? "meter is" : "meters are"} at or below ${usage.threshold}% remaining.<//>` : null}

    ${usage.status === "loading" ? html`<div class="stats-loading"><${Spinner} /><span>Loading usage…</span></div>`
      : !usage.snapshot?.available || ["disabled", "not_installed", "timeout", "error", "empty"].includes(usage.status)
        ? html`<${Unavailable} status=${usage.status} detail=${usage.snapshot?.detail || usage.error} onRefresh=${usage.refresh} refreshing=${usage.refreshing} />`
        : html`<div class="stats-content">
          <${SummaryCards} today=${usage.snapshot.today} />
          <section class="stats-section" aria-labelledby="quota-title"><div class="section-heading"><div><p class="eyebrow">Capacity</p><h2 id="quota-title">Provider quotas</h2></div></div>
            <${QuotaMeters} quotas=${usage.snapshot.quotas} threshold=${usage.threshold} /></section>

          <section class="stats-section" aria-labelledby="history-title">
            <div class="section-heading history-heading"><div><p class="eyebrow">History</p><h2 id="history-title">Usage over time</h2></div>
              <div class="chart-controls"><${Segmented} value=${metric} options=${METRIC_OPTIONS} onChange=${setMetric} label="Metric" />
                <${Segmented} value=${range} options=${RANGE_OPTIONS} onChange=${setRange} label="Date range" /></div></div>
            ${historyLoading ? html`<div class="stats-loading compact"><${Spinner} /><span>Loading history…</span></div>`
              : history && !history.available ? html`<${Unavailable} status=${history.reason || "error"} detail=${history.detail} onRefresh=${usage.refresh} refreshing=${usage.refreshing} />`
              : html`<${UsageChart} buckets=${history?.buckets || []} metric=${metric} />`}
          </section>

          <section class="stats-section" aria-labelledby="breakdown-title"><div class="section-heading"><div><p class="eyebrow">Today</p><h2 id="breakdown-title">Leading activity</h2></div></div>
            <div class="breakdown-grid"><${TopBreakdown} title="Top clients" rows=${usage.snapshot.today?.top_clients} keyName="client" metric=${metric} />
              <${TopBreakdown} title="Top models" rows=${usage.snapshot.today?.top_models} keyName="model" metric=${metric} /></div>
          </section>
        </div>`}
  </section>`;
}
