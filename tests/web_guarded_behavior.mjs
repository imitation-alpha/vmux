import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { ApiError, createActionDispatcher, createApiClient, normalizePane } from "../vmux/web/js/state.js";

const wire = {
  id: "h:opaque", target: "herdr:opaque", name: "Review", kind: "codex",
  provider: "herdr", status: "needs_input", actionable: true,
  hierarchy: [
    { kind: "session", id: "hs:1", label: "agents" },
    { kind: "workspace", id: "hw:1", label: "Project" },
    { kind: "tab", id: "ht:1", label: "Review tab" },
    { kind: "pane", id: "hp:1", label: "Worker" },
  ],
  capabilities: { input: "guarded_v1", keys: ["Enter", "Escape", "C-c", "C-u"], broadcast: false, create: false },
  native_agent: { present: true, kind: "codex", status: "blocked", state_change_seq: 4, interactive_ready: true },
  action_guard: { endpoint_revision: "7", prompt_fingerprint: "sha256:prompt", options_fingerprint: "sha256:options" },
  menu: [{ id: "o:yes", key: "1", label: "Yes", selected: true }],
  lines: ["Continue?"], question: "Continue?",
};
const connection = { mode: "live", compatibility: { blocksActions: false } };

function harness(raw = wire) {
  let pane = normalizePane(raw);
  const requests = [], records = [], errors = [], stars = [];
  let refreshes = 0;
  let respond = async () => ({ ok: true });
  const snapshot = { connection, actionStates: {}, latestActionByPane: {} };
  const store = {
    getPane: id => id === pane.id ? pane : null,
    getSnapshot: () => snapshot,
    request: (path, options) => { requests.push({ path, ...options }); return respond(path, options); },
    refreshState: async () => { refreshes++; },
    _recordAction: record => { records.push(record); snapshot.latestActionByPane[record.paneId] = record; },
    _handleApiError: error => errors.push(error),
    _setOptimisticStar: (...args) => stars.push(["set", ...args]),
    _commitOptimisticStar: id => stars.push(["commit", id]),
    _rollbackOptimisticStar: id => stars.push(["rollback", id]),
  };
  return {
    actions: createActionDispatcher(store), requests, records, errors, stars,
    get pane() { return pane; },
    get refreshes() { return refreshes; },
    setRaw: raw => { pane = normalizePane(raw); },
    respond: fn => { respond = fn; },
  };
}

async function herdr() {
  const h = harness();
  assert.equal(h.pane.provider, "herdr");
  assert.deepEqual(h.pane.hierarchy.map(node => node.label), ["agents", "Project", "Review tab", "Worker"]);
  assert.deepEqual(h.pane.actionGuard, wire.action_guard);
  assert.equal(h.pane.nativeAgent.status, "blocked");
  assert.equal(h.pane.nativeAgent.stateChangeSeq, 4);
  assert.equal(h.pane.nativeAgent.interactiveReady, true);
  for (const patch of [{ stale: true }, { actionable: false }, { capabilities: { input: "none" } }, { provider: "future" }]) {
    h.setRaw({ ...wire, ...patch });
    assert.equal(h.actions.canAct(h.pane), false);
    await assert.rejects(h.actions.text(h.pane, "answer", true));
  }
  assert.equal(h.requests.length, 0);
  h.setRaw(wire);
  await assert.rejects(h.actions.key(h.pane, "Tab"));
  await assert.rejects(h.actions.broadcast([h.pane], "answer"));
  assert.equal(h.requests.length, 0);

  const require = createRequire(import.meta.url);
  let slots = [], cursor = 0;
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    createContext: value => ({ value }),
    useCallback: fn => fn,
    useMemo: fn => fn(),
    useRef: value => ({ current: value }),
    useEffect: () => {},
    useState: initial => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], update => { slots[index] = typeof update === "function" ? update(slots[index]) : update; }];
    },
    useSyncExternalStore: (_subscribe, get) => get(),
  };
  globalThis.React = React;
  globalThis.window = { React, ReactDOM: {}, htm: require("../vmux/web/vendor/htm.umd.js") };
  const { Terminal, TreeView, PaneDetail } = await import("../vmux/web/js/ui.js");
  function nodes(value) {
    if (Array.isArray(value)) return value.flatMap(nodes);
    if (!value || typeof value !== "object") return [];
    return [value, ...nodes(value.children)];
  }
  function text(value) {
    if (Array.isArray(value)) return value.map(text).join("");
    if (value && typeof value === "object") return text(value.children);
    return value == null || typeof value === "boolean" ? "" : String(value);
  }
  function render(component, props) { cursor = 0; return component(props); }
  const props = { pane: h.pane, actions: h.actions, connection };
  let terminal = render(Terminal, props);
  let keys = nodes(terminal).filter(node => node.props.class === "key-button");
  assert.deepEqual(keys.map(text), ["CTRL+C", "ESC", "↵"]);
  await keys[2].props.onClick();
  assert.equal(h.requests[0].path, "/input");
  assert.equal(h.requests[0].body.key, "Enter");
  h.setRaw({ ...wire, stale: true });
  terminal = render(Terminal, { ...props, pane: h.pane });
  keys = nodes(terminal).filter(node => node.props.class === "key-button");
  assert.ok(keys.every(node => node.props.disabled));
  await keys[0].props.onClick();
  assert.equal(h.requests.length, 1);
  assert.ok(text(PaneDetail({ ...props, pane: h.pane })).includes("Read-only snapshot"));
  h.setRaw(wire);
  slots = [];
  const treeProps = { panes: [h.pane], actions: h.actions, connection, onCreate: () => assert.fail("creation") };
  let tree = render(TreeView, treeProps);
  const parent = nodes(tree).find(node => node.type === "button" && text(node).includes("agents / Project"));
  assert.ok(parent);
  parent.props.onClick();
  tree = render(TreeView, treeProps);
  assert.ok(text(tree).includes("Review tab"));
  assert.equal(nodes(tree).filter(node => String(node.props.class).includes("tree-create")).length, 0);
}

async function dispatcher() {
  const h = harness();
  await h.actions.select(h.pane, h.pane.menu[0]);
  await h.actions.key(h.pane, "Enter");
  await h.actions.text(h.pane, "answer", true);
  assert.deepEqual(h.requests.map(request => request.path), ["/input", "/input", "/input"]);
  assert.deepEqual(h.requests.map(request => request.body.operation), ["select", "key", "text"]);
  assert.equal(h.requests[0].body.option_id, "o:yes");
  assert.equal(h.requests[2].body.text, "answer");
  assert.equal(h.requests[2].body.enter, true);
  for (const request of h.requests) {
    assert.deepEqual(request.body.expected, wire.action_guard);
    assert.equal(request.method, "POST");
    assert.ok(request.body.idempotency_key);
  }
  assert.equal(new Set(h.requests.map(request => request.body.idempotency_key)).size, 3);

  let reject;
  h.respond(() => new Promise((_resolve, fail) => { reject = fail; }));
  const first = h.actions.key(h.pane, "Enter");
  const duplicate = h.actions.key(h.pane, "Enter");
  assert.equal(first, duplicate);
  assert.equal(h.requests.length, 4);
  const conflict = new ApiError("Prompt changed", { status: 409, endpoint: "/input" });
  const rejected = assert.rejects(first, error => error === conflict);
  reject(conflict);
  await rejected;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.refreshes, 1);
  assert.equal(h.requests.length, 4);
  assert.equal(h.records.at(-1).status, "error");
  assert.equal(h.errors.at(-1), conflict);

  for (const field of ["endpoint_revision", "prompt_fingerprint", "options_fingerprint"]) {
    for (const submit of [
      (actions, pane) => actions.key(pane, "Enter"),
      (actions, pane) => actions.text(pane, "answer", true),
      (actions, pane) => actions.select(pane, "1"),
    ]) {
      const advanced = harness();
      const observed = advanced.pane;
      advanced.setRaw({ ...wire, action_guard: { ...wire.action_guard, [field]: "changed" } });
      await assert.rejects(submit(advanced.actions, observed), error => error.status === 409);
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(advanced.requests.length, 0);
      assert.equal(advanced.refreshes, 1);
      assert.equal(advanced.records.at(-1).status, "error");
      assert.deepEqual(observed.actionGuard, wire.action_guard);
      await submit(advanced.actions, advanced.pane);
      assert.equal(advanced.requests.length, 1);
      assert.deepEqual(advanced.requests[0].body.expected, advanced.pane.actionGuard);
    }
  }
  const advancedText = harness();
  const observedText = advancedText.pane;
  advancedText.setRaw({ ...wire, action_guard: { ...wire.action_guard, prompt_fingerprint: "new" } });
  await assert.rejects(advancedText.actions.text(observedText, "draft", false), error => error.status === 409);
  assert.equal(advancedText.requests.length, 0);
  await advancedText.actions.key(observedText, "C-c");
  assert.deepEqual(advancedText.requests[0].body.expected, observedText.actionGuard);
  await assert.rejects(advancedText.actions.key(advancedText.pane.id, "Enter"));
  assert.equal(advancedText.requests.length, 1);

  const legacy = harness({ id: "%1", target: "work:1.1", status: "needs_input" });
  await legacy.actions.select(legacy.pane, "1");
  await legacy.actions.key(legacy.pane, "Enter");
  await legacy.actions.text(legacy.pane, "answer", true);
  assert.deepEqual(legacy.requests.map(request => request.path), ["/select", "/key", "/text"]);
  assert.deepEqual(legacy.requests[2].body, { id: "%1", text: "answer", enter: true });
  await legacy.actions.star(legacy.pane, true);
  assert.deepEqual(legacy.stars, [["set", "%1", true, false], ["commit", "%1"]]);
  const failure = new ApiError("Rejected", { status: 500 });
  legacy.respond(async () => { throw failure; });
  await assert.rejects(legacy.actions.star(legacy.pane, false), error => error === failure);
  assert.deepEqual(legacy.stars.at(-1), ["rollback", "%1"]);
  assert.equal(legacy.refreshes, 0);
  legacy.respond(async () => ({ ok: true, sent: 1, errors: [] }));
  await legacy.actions.broadcast([legacy.pane, h.pane], "all", true);
  assert.equal(legacy.requests.at(-1).path, "/broadcast");
  assert.deepEqual(legacy.requests.at(-1).body.ids, ["%1"]);
}

async function delivery() {
  for (const reason of ["partial_delivery_unknown", "delivery_unknown"]) {
    const h = harness();
    const fetched = [];
    const client = createApiClient({
      origin: "http://localhost", token: "", xhrFactory: null,
      fetchImpl: async (url, options) => {
        fetched.push({ url, body: JSON.parse(options.body) });
        return { status: 409, text: async () => JSON.stringify({ detail: { reason } }) };
      },
    });
    h.respond(client.request);
    let decoded;
    await assert.rejects(h.actions.text(h.pane, "continue", true), error => {
      decoded = error;
      return error instanceof ApiError && error.reason === reason && error.status === 409;
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(fetched.length, 1);
    assert.equal(fetched[0].url, "http://localhost/api/input");
    assert.equal(fetched[0].body.text, "continue");
    assert.equal(fetched[0].body.enter, true);
    assert.equal(h.requests.length, 1);
    assert.equal(h.refreshes, 1);
    assert.equal(decoded.retryable, false);
    assert.equal(h.errors.at(-1), decoded);
    const feedback = h.actions.stateFor(h.pane);
    assert.equal(feedback.status, "error");
    assert.equal(feedback.message, decoded.userMessage);
    assert.ok(feedback.message.includes("Inspect the terminal before retrying"));
    assert.ok(feedback.message.includes("avoid duplicate input"));
    if (reason === "partial_delivery_unknown") {
      assert.ok(feedback.message.includes("Enter was not confirmed"));
    }
  }
  for (const [detail, expectedReason, message] of [
    ["Legacy rejection", null, "Legacy rejection"],
    [{ reason: "prompt_changed" }, "prompt_changed", "Request failed (409)"],
    [{ reason: { nested: "delivery_unknown" } }, null, "Request failed (409)"],
    [{ reason: "bad\nreason" }, null, "Request failed (409)"],
    [{ reason: "constructor" }, "constructor", "Request failed (409)"],
  ]) {
    const client = createApiClient({
      token: "", xhrFactory: null,
      fetchImpl: async () => ({ status: 409, text: async () => JSON.stringify({ detail }) }),
    });
    await assert.rejects(client.request("/input"), error => (
      error.reason === expectedReason && error.userMessage === message
    ));
  }
  const uncertain = new ApiError("Server error", { reason: "delivery_unknown", retryable: true, status: 503 });
  assert.equal(uncertain.retryable, false);
  assert.ok(uncertain.userMessage.includes("Inspect the terminal before retrying"));
}

if (process.argv[2] === "herdr") await herdr();
else if (process.argv[2] === "dispatcher") await dispatcher();
else if (process.argv[2] === "delivery") await delivery();
else assert.fail("unknown behavioral case");
