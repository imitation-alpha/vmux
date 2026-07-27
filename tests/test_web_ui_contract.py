"""Fast, module-aware contracts for the no-build React web client."""

from __future__ import annotations

import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "vmux" / "web"


def source(relative: str) -> str:
    return (WEB / relative).read_text()


def function_body(text: str, name: str, next_name: str | None = None) -> str:
    start = text.index(f"function {name}(")
    if next_name:
        return text[start : text.index(f"function {next_name}(", start)]
    return text[start:]


def test_minimal_shell_loads_only_same_origin_vendored_runtime_and_modules():
    html = source("index.html")
    for asset in (
        "/styles.css",
        "/vendor/react.production.min.js",
        "/vendor/react-dom.production.min.js",
        "/vendor/htm.umd.js",
        "/js/app.js",
    ):
        assert asset in html
    assert 'type="module" src="/js/app.js"' in html
    assert "function Workspace(" not in html
    assert "https://" not in html


def test_module_boundaries_keep_transport_ui_usage_and_settings_separate():
    app = source("js/app.js")
    assert 'from "./state.js"' in app
    assert 'from "./settings.js"' in app
    assert 'from "./ui.js"' in app
    assert 'from "./usage.js"' in app
    assert "vmuxStore.start()" in app
    assert 'from "./agent-ui.js"' in app
    for module in (
        "core.js",
        "state.js",
        "image-upload.js",
        "agent-state.js",
        "review-drafts.js",
        "agent-ui.js",
        "ui.js",
        "usage.js",
        "settings.js",
        "app.js",
    ):
        assert (WEB / "js" / module).is_file()


def test_agent_workspace_is_capability_gated_separate_and_hash_routed():
    state = source("js/agent-state.js")
    ui = source("js/agent-ui.js")
    worker = source("sw.js")
    assert 'export const AGENT_CAPABILITY = "agent_context_v1"' in state
    assert 'const capabilities = isObject(info.capabilities)' in state
    assert 'url.pathname = "/ws/agents"' in state
    assert 'from "./agent-state.js"' in ui
    assert 'from "./agent-ui.js"' not in source("js/state.js")
    for destination in ("agents", "decisions", "panes", "timeline", "stats"):
        assert f'["{destination}"' in state
    assert '"/js/agent-state.js"' in worker
    assert '"/js/image-upload.js"' in worker
    assert '"/js/review-drafts.js"' in worker
    assert '"/js/agent-ui.js"' in worker
    assert 'isLiveEndpoint(url.pathname)' in worker


def test_review_contract_is_capability_gated_explicit_and_conflict_safe():
    state = source("js/agent-state.js")
    ui = source("js/agent-ui.js")
    drafts = source("js/review-drafts.js")
    app = source("js/app.js")

    assert 'export const REVIEW_CAPABILITY = "agent_review_v1"' in state
    for endpoint in (
        'request("/review")',
        'request("/review/settings", body, "PATCH")',
    ):
        assert endpoint in state
    acknowledge = function_body(state, "acknowledgeReview", "updateReviewSettings")
    assert '/review`' in acknowledge
    assert "{ snapshot_id: snapshotId }" in acknowledge
    assert '"PUT"' in acknowledge
    assert "if (!id || state.reviewEnabled || !resume?.as_of_snapshot_id) return;" in ui
    assert 'route.destination === "review"' in ui
    assert "Quick Review" in ui and "Plan Review" in ui
    assert "Terminal review" in ui and "Open Pane" in ui
    assert "Opening them does not acknowledge or answer anything." in ui
    assert "async function submitReviewDecision(staged)" in state
    assert "current = await loadDecision" in state
    assert "decisionDeliveryUncertain(current)" in state
    assert "The reply may have been delivered" in state
    for field in (
        "revision",
        "binding_revision",
        "prompt_fingerprint",
        "options_fingerprint",
    ):
        assert field in function_body(state, "reviewDecisionMatches", "normalizeTimelineEvent")
        assert field in drafts
    review_submission = function_body(state, "submitReviewDecision", "quickReply")
    assert "reviewDecisionMatches(normalizedStage, current)" in review_submission
    assert 'capabilityMode(owner, "decision_reply") !== "verified_terminal"' in review_submission
    assert "broadcast" not in review_submission.lower()
    assert "Intentionally sequential" in state
    assert "alert_on_needs_input" not in app
    assert "const fresh = await agentStore.loadReview()" in ui
    assert "quickPendingRef.current.size" in ui
    assert "quickReplyPromises.get(decision.id)" in state
    assert "Send custom response" in ui and "Ask more" in ui
    assert "custom_text: text(selection.customText).trim()" in state
    assert 'role=${optionsRole}' in ui and 'aria-checked=${optionRole' in ui
    assert "aria-pressed=${sinceReview}" in ui
    assert "attentionNotificationIDs" in app
    assert 'priority === "high" || priority === "critical"' in app
    assert "prompt" not in function_body(app, "attentionNotificationIDs", "useAttentionNotifications")


def test_plan_drafts_are_metadata_only_and_deep_context_is_server_filtered():
    state = source("js/agent-state.js")
    ui = source("js/agent-ui.js")
    drafts = source("js/review-drafts.js")

    allowed = {
        "server_instance_id",
        "decision_id",
        "option_id",
        "revision",
        "binding_revision",
        "prompt_fingerprint",
        "options_fingerprint",
        "updated_at",
    }
    record = function_body(drafts, "sanitizeRecord", "decisionRecord")
    for field in allowed:
        assert field in record
    for forbidden in ("prompt_text", "conversation", "message_content", "option_label", "custom_text"):
        assert forbidden not in record
    for parameter in ('parameters.set("q"', 'parameters.set("role"', 'parameters.set("after"', 'parameters.set("before"'):
        assert parameter in state
    assert "Since last review" in ui
    assert "Older transcript content is outside the server’s retention boundary." in ui
    assert "hidden reasoning, tool results, commands, or raw terminal scrollback" in ui
    message_page = function_body(state, "loadMessagePage", "loadDecision")
    assert "messageRequestSequences" in message_page
    assert "reviewed_snapshot_sequence" in message_page
    assert "keepExistingBaseline" in message_page
    assert "group?.reviewed_snapshot_at" in ui


def test_review_scope_accessibility_and_deep_context_css_are_hardened():
    state = source("js/agent-state.js")
    ui = source("js/agent-ui.js")
    css = source("styles.css")

    configure = function_body(state, "configure", "stop")
    for value in (
        "serverChanged",
        "reviewCapabilityChanged",
        "scopeGeneration += 1",
        "review: serverChanged || reviewCapabilityChanged ? null",
        "planResults: serverChanged || reviewCapabilityChanged ? []",
    ):
        assert value in configure
    start_another = ui[ui.index("const startAnother =") : ui.index("if (!review", ui.index("const startAnother ="))]
    assert "setComplete(false)" in start_another
    assert ".dialog-panel.deep-context-dialog" in css
    assert ".deep-context-dialog .dialog-panel" not in css


def test_agent_rest_actions_are_revision_safe_and_resume_is_non_mutating():
    state = source("js/agent-state.js")
    ui = source("js/agent-ui.js")
    for endpoint in (
        'collectCursorPages("/agents"',
        'collectCursorPages("/decisions"',
        'collectCursorPages("/timeline"',
        '/resume`)',
        '/messages`',
        '/timeline`',
        '/visit`',
        '/binding`',
        '/reply`',
    ):
        assert endpoint in state
    for field in (
        "idempotency_key",
        "expected_revision",
        "expected_binding_revision",
        "prompt_fingerprint",
        "custom_text",
    ):
        assert field in state
    assert "const envelopeEvent = isObject(message.event) ? message.event : message;" in state
    assert "envelopeEvent.agent_id || envelopeEvent.session_id" in state
    assert 'type === "hello" || type === "ping"' in state
    assert "function timelineTitle(source)" in state
    assert "agent.binding?.revision ?? agent.binding_revision" in state
    assert "source.name || source.title || context.name" in state
    assert "binding?expected_binding_revision=" in state
    assert 'setValue("");' in ui
    assert "Resume is deliberately non-mutating" in ui
    assert 'agentStore.sendMessage(agent.id, content)' in ui
    assert "capabilityMode(agent, \"chat_send\")" in ui
    assert 'chatMode === "idle_only"' in ui
    assert 'decisionMode === "verified_terminal"' in ui
    assert "reported.length ? reported : panes.map" not in ui
    assert "No verified pane candidates" in ui
    assert "runtime ===" not in ui


def test_agent_cursor_pagination_is_bounded_deduplicated_and_order_aware():
    state = source("js/agent-state.js")
    assert "const CURSOR_PAGE_CAP = 20;" in state
    assert "const CURSOR_ITEM_CAP = 2000;" in state
    assert "pageCount < CURSOR_PAGE_CAP && itemCount < CURSOR_ITEM_CAP" in state
    assert "requestedCursors.has(next)" in state
    assert "itemIds.has(id)" in state
    assert 'cursor=${encodeURIComponent(value)}' in state
    for collection in (
        'collectCursorPages("/agents", ["agents", "items"], { idOf: agentWireId })',
        'collectCursorPages("/decisions", ["decisions", "items"], { idOf: decisionWireId })',
        'collectCursorPages("/timeline", ["events", "timeline", "items"], { idOf: timelineWireId })',
    ):
        assert collection in state
    assert 'collectCursorPages(`/agents/${encodeURIComponent(id)}/messages`' in state
    assert "idOf: messageWireId, reversePages: true" in state
    assert 'collectCursorPages(`/agents/${encodeURIComponent(id)}/timeline`' in state
    assert 'collectCursorPages("/decisions?status=pending"' in state
    assert 'collectCursorPages("/decisions?status=submitting"' in state
    assert "mergeDecisionCollections(history.items, pending.items, submitting.items)" in state
    assert "decision.revision >= decisions[existingIndex].revision" in state


def test_adaptive_shell_is_width_based_with_compact_medium_and_wide_modes():
    core = source("js/core.js")
    ui = source("js/ui.js")
    css = source("styles.css")
    layout = function_body(core, "useLayoutMode", "usePrevious")
    assert "window.innerWidth < 820" in layout
    assert "window.innerWidth < 1200" in layout
    assert 'matchMedia("(max-width: 819px)")' in layout
    assert "pointer" not in layout.lower()
    assert "function CompactShell(" in ui
    assert "function MediumShell(" in ui
    assert "function WideShell(" in ui
    assert '@media (max-width: 819px)' in css
    assert '@media (hover: none)' in css
    compact = function_body(ui, "CompactShell", "MediumShell")
    for destination in ("queue", "active", "all", "stats"):
        assert f'["{destination}"' in compact


def test_tree_retains_deliberate_expansion_and_expands_selected_ancestors():
    ui = source("js/ui.js")
    tree = function_body(ui, "TreeView", "extractLinks")
    assert "const SESSION_TREE_EXPANDED = new Set();" in ui
    assert "selectedAncestorKeys(panes, selectedId)" in tree
    assert "SESSION_TREE_EXPANDED.add(key)" in tree
    assert "SESSION_TREE_EXPANDED.delete(key)" in tree
    assert "useState(() => new Set(SESSION_TREE_EXPANDED))" in tree
    assert "aria-expanded=${sessionOpen}" in tree
    assert "aria-expanded=${windowOpen}" in tree
    assert "sessionOpen ?" in tree
    assert "windowOpen ?" in tree


def test_agent_kinds_and_unknown_wire_values_have_safe_labels():
    state = source("js/state.js")
    settings = source("js/settings.js")
    for kind, label in (
        ("claude-code", "Claude Code"),
        ("codex", "Codex"),
        ("grok", "Grok"),
        ("opencode", "OpenCode"),
        ("antigravity", "Antigravity"),
        ("generic", "Agent"),
        ("shell", "Shell"),
    ):
        assert f'"{kind}"' in state
        assert label in state
        assert f'"{kind}"' in settings
    assert 'const status = KNOWN_STATUSES.includes(rawStatus) ? rawStatus : "unknown"' in state
    assert 'const kind = KNOWN_KINDS.includes(rawKind) ? rawKind : "generic"' in state
    assert 'unknown: "Unknown"' in state


def test_menu_descriptions_normalize_compare_and_render_in_the_action_card():
    state = source("js/state.js")
    ui = source("js/ui.js")
    styles = source("styles.css")
    assert "description: textValue(option.description)" in state
    assert "a.description === b.description" in state
    assert 'function PaneActionCard({ pane, actions, connection })' in ui
    pane_detail = function_body(ui, "PaneDetail", "FilterTabs")
    assert pane_detail.index("<${PaneActionCard}") < pane_detail.index("<${Terminal}")
    assert "option.description" in ui
    assert ".pane-action-option" in styles
    assert ".pane-action-copy small" in styles


def test_actions_share_one_dispatcher_and_never_swallow_failures():
    state = source("js/state.js")
    ui = source("js/ui.js")
    app = source("js/app.js")
    assert "export function createActionDispatcher(store)" in state
    for endpoint in ("/select", "/key", "/text", "/star", "/broadcast"):
        assert f'endpoint: "{endpoint}"' in state
    assert "if (inflight.has(flightKey)) return inflight.get(flightKey);" in state
    assert "_setOptimisticStar" in state
    assert "_rollbackOptimisticStar" in state
    assert "function PaneActionFeedback(" in ui
    assert "Broadcast partially completed" in app
    assert ".catch(() => {})" not in "\n".join((state, ui, app))


def test_image_uploads_use_authenticated_raw_bytes_and_only_append_to_composers():
    state = source("js/state.js")
    upload = source("js/image-upload.js")
    ui = source("js/ui.js")
    agent_ui = source("js/agent-ui.js")
    worker = source("sw.js")

    client = function_body(state, "createApiClient", "parseSemver")
    assert "rawBody" in client
    assert 'headers.set("Authorization", `Bearer ${token}`)' in client
    assert 'headers.set("Content-Type", declaredType)' in client
    assert "body: hasRawBody ? rawBody" in client
    assert "JSON.stringify(body)" in client
    assert "onUploadProgress" in client
    assert 'vmuxStore.request("/images"' in state
    assert 'accept="image/*"' in upload
    assert "clipboardImageFiles(event)" in upload
    assert "event.preventDefault()" in upload
    assert "appendTerminalText" in upload
    assert "Retry image upload" in upload and "Cancel" in upload
    assert 'from "./image-upload.js"' in ui
    assert 'from "./image-upload.js"' in agent_ui
    assert "onPaste=${imageUpload.onPaste}" in ui
    assert "onPaste=${imageUpload.onPaste}" in agent_ui
    assert '"/js/image-upload.js"' in worker
    assert 'request.method !== "GET"' in worker
    for forbidden in ('actions.text(', 'agentStore.sendMessage(', 'endpoint: "/text"'):
        assert forbidden not in upload


def test_connection_compatibility_and_token_security_contracts_are_present():
    state = source("js/state.js")
    assert "const REST_INTERVAL_MS = 2000;" in state
    assert "const OFFLINE_GRACE_MS = 10000;" in state
    assert "const WS_RETRY_INITIAL_MS = 500;" in state
    assert "const WS_RETRY_MAX_MS = 8000;" in state
    assert "const WS_RETRY_JITTER_MS = 300;" in state
    assert "Math.max(3000, poll * 2000 + 1000)" in state
    assert "export function evaluateCompatibility(" in state
    assert 'protocol_mismatch' in state
    assert 'metadata_malformed' in state
    assert 'metadata_missing' in state
    assert 'url.searchParams.delete("token")' in state
    assert "history.replaceState" in state
    assert "purgeTokenCacheEntries" in state
    assert "authorization" not in function_body(state, "sanitizedIssue", "actionRecordKey").lower()


def test_usage_dashboard_has_api_states_local_svg_and_accessible_table():
    usage = source("js/usage.js")
    for endpoint in ('api("/usage"', 'api("/usage/refresh"', '`/usage/history?period='):
        assert endpoint in usage
    for state in ("disabled", "not_installed", "timeout", "error", "empty", "stale"):
        assert state in usage
    assert '<svg class="usage-chart"' in usage
    assert 'role="img"' in usage
    assert '<table class="usage-table">' in usage
    assert "Today’s cost" in usage
    assert "Top clients" in usage
    assert "Top models" in usage
    assert 'snapshot?.available ? "stale"' in usage
    assert '"Other"' in usage
    shell = source("index.html").lower()
    assert "chart.js" not in shell
    assert "d3.js" not in shell
    assert "https://" not in shell


def test_settings_categories_and_editing_modes_cover_existing_configuration():
    settings = source("js/settings.js")
    for category in (
        "Appearance & Alerts",
        "Input Shortcuts & Snippets",
        "Server & Discovery",
        "Agent Overrides & Detectors",
        "Usage",
        "Sessions",
        "Connection & About",
    ):
        assert category in settings
    for field in (
        "capture_lines",
        "naming_mode",
        "usage_enabled",
        "usage_quota_refresh",
        "usage_report_refresh",
        "usage_alert_threshold",
    ):
        assert field in settings
    assert "function SaveBar(" in settings
    assert "const queue = useRef(Promise.resolve())" in settings
    assert 'api("/sessions")' in settings
    assert 'api("/sessions/kill"' in settings


def test_terminal_is_plain_text_selectable_wrap_aware_and_full_screen():
    ui = source("js/ui.js")
    css = source("styles.css")
    terminal = function_body(ui, "Terminal", "PaneDetail")
    assert "(pane.lines || []).join" in terminal
    assert "${output}</pre>" in terminal
    assert "innerHTML" not in "\n".join(source(f"js/{name}") for name in ("app.js", "core.js", "state.js", "ui.js", "usage.js", "settings.js"))
    assert "dangerouslySetInnerHTML" not in ui
    assert 'class=${cx("terminal-output", wrap ? "wrap" : "no-wrap")}' in terminal
    assert "following.current" in terminal
    assert "Latest" in terminal
    assert "Open full screen terminal" in terminal
    assert "navigator.clipboard.writeText" in ui
    assert "user-select: text" in css
    assert ".terminal-output.no-wrap" in css and "overflow-x: auto" in css


def test_accessibility_keyboard_and_reduced_motion_contracts_remain_visible():
    core = source("js/core.js")
    ui = source("js/ui.js")
    css = source("styles.css")
    assert "const FOCUSABLE" in core
    assert 'class="segmented" role="group"' in core
    assert "aria-pressed=${value === key}" in core
    assert 'event.key === "Escape"' in core
    assert "restore.focus" in core
    assert 'aria-live="polite"' in ui
    assert "min-height: 44px" in css
    assert ":focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "meta+K" not in ui  # palette uses platform-neutral Ctrl/Command event handling
    assert "event.metaKey || event.ctrlKey" in ui


def test_service_worker_caches_only_explicit_shell_and_navigation_fallback():
    worker = source("sw.js")
    assert "const SHELL_ASSETS = Object.freeze([" in worker
    assert "const NETWORK_TIMEOUT_MS = 3000;" in worker
    assert "isTransientServerFailure(response)" in worker
    assert "isLiveEndpoint(url.pathname)" in worker
    assert "request.headers.has(\"Authorization\")" in worker
    assert "Boolean(url.search)" in worker
    assert 'request.mode === "navigate"' in worker
    assert "!SHELL_PATHS.has(url.pathname)" in worker
    assert "cache.match(SHELL_KEY)" in worker
    assert "self.skipWaiting()" in worker
    assert "caches.match(request)" not in worker


def test_manifest_and_all_declared_static_assets_exist():
    manifest = json.loads(source("manifest.webmanifest"))
    icon_entries = {(icon["src"], icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert ("/icon-192.png", "192x192", "any") in icon_entries
    assert ("/icon-512.png", "512x512", "any") in icon_entries
    assert ("/icon-maskable-512.png", "512x512", "maskable") in icon_entries
    html = source("index.html")
    assert 'rel="apple-touch-icon" sizes="180x180" href="/icon-180.png"' in html
    for relative in (
        "icon-180.png",
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
        "icons/lucide.svg",
        "icons/LUCIDE_LICENSE.txt",
    ):
        assert (WEB / relative).is_file(), relative
