/**
 * Metadata-only Plan Review draft persistence.
 *
 * Drafts intentionally exclude prompts, option copy, conversation text, and
 * agent context. The server-provided opaque fingerprints let a client prove a
 * restored selection still refers to the same guarded decision.
 */

const STORAGE_KEY = "vmux_review_drafts_v1";
const STORAGE_VERSION = 1;

function text(value) {
  return typeof value === "string" ? value : "";
}

function integer(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : 0;
}

function recordKey(record) {
  return [
    record.server_instance_id,
    record.decision_id,
    record.option_id,
    record.revision,
    record.prompt_fingerprint,
  ].map((value) => encodeURIComponent(String(value))).join(":");
}

function sanitizeRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = {
    server_instance_id: text(value.server_instance_id),
    decision_id: text(value.decision_id),
    option_id: text(value.option_id),
    revision: integer(value.revision),
    binding_revision: integer(value.binding_revision),
    prompt_fingerprint: text(value.prompt_fingerprint),
    options_fingerprint: text(value.options_fingerprint),
    updated_at: Number.isFinite(Number(value.updated_at)) ? Number(value.updated_at) : Date.now(),
  };
  if (!record.server_instance_id || !record.decision_id || !record.option_id
      || !record.prompt_fingerprint || !record.options_fingerprint) return null;
  return { ...record, key: recordKey(record) };
}

function decisionRecord(serverInstanceID, decision, optionID) {
  return sanitizeRecord({
    server_instance_id: serverInstanceID,
    decision_id: decision?.id,
    option_id: optionID,
    revision: decision?.revision,
    binding_revision: decision?.binding_revision,
    prompt_fingerprint: decision?.prompt_fingerprint,
    options_fingerprint: decision?.options_fingerprint,
    updated_at: Date.now(),
  });
}

function browserStorage() {
  try {
    return globalThis.localStorage || null;
  } catch (_) {
    return null;
  }
}

export function draftMatchesDecision(draft, decision) {
  if (!draft || !decision || decision.status !== "pending") return false;
  const optionExists = Array.isArray(decision.options)
    && decision.options.some((option) => String(option?.id || "") === draft.option_id);
  return optionExists
    && integer(decision.revision) === draft.revision
    && integer(decision.binding_revision) === draft.binding_revision
    && text(decision.prompt_fingerprint) === draft.prompt_fingerprint
    && text(decision.options_fingerprint) === draft.options_fingerprint;
}

export function createReviewDraftStore({ storage } = {}) {
  const backend = storage === undefined ? browserStorage() : storage;
  let memory = null;

  function readAll() {
    if (memory !== null) return [...memory];
    if (!backend) {
      memory = [];
      return [];
    }
    try {
      const payload = JSON.parse(backend.getItem(STORAGE_KEY) || "{}");
      if (payload.version !== STORAGE_VERSION || !Array.isArray(payload.drafts)) {
        memory = [];
        return [];
      }
      const seen = new Set();
      const drafts = [];
      for (const value of payload.drafts) {
        const record = sanitizeRecord(value);
        if (!record || seen.has(record.key)) continue;
        seen.add(record.key);
        drafts.push(record);
      }
      memory = drafts;
      return [...drafts];
    } catch (_) {
      memory = [];
      return [];
    }
  }

  function writeAll(values) {
    const drafts = values.map(sanitizeRecord).filter(Boolean);
    memory = drafts;
    if (!backend) return;
    const persisted = drafts.map(({ key: _key, ...record }) => record);
    try {
      if (persisted.length) {
        backend.setItem(
          STORAGE_KEY,
          JSON.stringify({ version: STORAGE_VERSION, drafts: persisted }),
        );
      } else {
        backend.removeItem(STORAGE_KEY);
      }
    } catch (_) {
      // Private browsing and storage quotas retain this session's memory copy.
    }
  }

  function list(serverInstanceID) {
    const serverID = text(serverInstanceID);
    if (!serverID) return [];
    return readAll()
      .filter((draft) => draft.server_instance_id === serverID)
      .sort((left, right) => left.updated_at - right.updated_at);
  }

  function stage(serverInstanceID, decision, optionID) {
    const record = decisionRecord(text(serverInstanceID), decision, text(optionID));
    if (!record) return null;
    const remaining = readAll().filter((draft) => !(
      draft.server_instance_id === record.server_instance_id
      && draft.decision_id === record.decision_id
    ));
    writeAll([...remaining, record]);
    return record;
  }

  function remove(serverInstanceID, decisionID) {
    const serverID = text(serverInstanceID);
    const id = text(decisionID);
    writeAll(readAll().filter((draft) => !(
      draft.server_instance_id === serverID && draft.decision_id === id
    )));
  }

  function reconcile(serverInstanceID, decisions, {
    authoritative = false,
    preserveDecisionIDs = [],
  } = {}) {
    if (!authoritative) return list(serverInstanceID);
    const serverID = text(serverInstanceID);
    if (!serverID) return [];
    const preserved = new Set(
      (Array.isArray(preserveDecisionIDs) ? preserveDecisionIDs : [])
        .map(text)
        .filter(Boolean),
    );
    const current = new Map(
      (Array.isArray(decisions) ? decisions : [])
        .filter((decision) => decision?.id)
        .map((decision) => [String(decision.id), decision]),
    );
    const kept = readAll().filter((draft) => {
      if (draft.server_instance_id !== serverID) return true;
      if (preserved.has(draft.decision_id)) return true;
      return draftMatchesDecision(draft, current.get(draft.decision_id));
    });
    writeAll(kept);
    return kept.filter((draft) => draft.server_instance_id === serverID);
  }

  return Object.freeze({ list, stage, remove, reconcile });
}

export const reviewDraftStore = createReviewDraftStore();
