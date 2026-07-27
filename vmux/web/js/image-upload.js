/** Shared image-picker, clipboard, upload, retry, and draft-insertion UI. */

import {
  Fragment,
  Icon,
  cx,
  html,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "./core.js";
import { uploadImage } from "./state.js";

export function appendTerminalText(current, terminalText) {
  const draft = typeof current === "string" ? current : "";
  const path = typeof terminalText === "string" ? terminalText.trim() : "";
  if (!path) return draft;
  if (!draft || /\s$/.test(draft)) return `${draft}${path}`;
  return `${draft} ${path}`;
}

export function clipboardImageFiles(event) {
  const clipboard = event?.clipboardData;
  if (!clipboard) return [];
  const fromItems = Array.from(clipboard.items || [])
    .filter((item) => item.kind === "file" && String(item.type || "").toLowerCase().startsWith("image/"))
    .map((item) => item.getAsFile?.())
    .filter(Boolean);
  if (fromItems.length) return fromItems;
  return Array.from(clipboard.files || []).filter((file) => (
    String(file?.type || "").toLowerCase().startsWith("image/")
  ));
}

function failureMessage(error) {
  if (error?.category === "too_large") return "This image is larger than the 20 MiB limit.";
  if (error?.category === "unsupported") return "Use a PNG, JPEG, WebP, or GIF image.";
  if (error?.category === "storage") return "Temporary image storage is full or unavailable.";
  if (error?.category === "unauthorized") return "Authentication expired. Reconnect before uploading.";
  if (error?.category === "network" || error?.category === "timeout") {
    return "The image upload could not reach vmux.";
  }
  return error?.userMessage || error?.message || "The image could not be uploaded.";
}

export function useImageUpload({ enabled, onInsert, onFocus = null }) {
  const pickerRef = useRef(null);
  const controllerRef = useRef(null);
  const busyRef = useRef(false);
  const enabledRef = useRef(Boolean(enabled));
  const insertRef = useRef(onInsert);
  const focusRef = useRef(onFocus);
  const [state, setState] = useState({
    status: "idle",
    progress: null,
    error: "",
    file: null,
    expiresAt: null,
    retryable: false,
  });
  enabledRef.current = Boolean(enabled);
  insertRef.current = onInsert;
  focusRef.current = onFocus;

  const cancel = useCallback(() => controllerRef.current?.abort(), []);

  useEffect(() => {
    if (!enabled && busyRef.current) cancel();
  }, [enabled, cancel]);
  useEffect(() => () => cancel(), [cancel]);

  const uploadFiles = useCallback(async (values) => {
    const files = Array.from(values || []).filter((file) => (
      String(file?.type || "").toLowerCase().startsWith("image/")
    ));
    if (!files.length || !enabledRef.current || busyRef.current) return false;
    busyRef.current = true;
    try {
      for (const file of files) {
        const controller = new AbortController();
        controllerRef.current = controller;
        setState({ status: "uploading", progress: 0, error: "", file, expiresAt: null, retryable: false });
        try {
          const result = await uploadImage(file, {
            signal: controller.signal,
            onProgress: ({ percent }) => setState((current) => (
              current.status === "uploading"
                ? { ...current, progress: Number.isFinite(percent) ? percent : null }
                : current
            )),
          });
          insertRef.current?.(result.terminal_text, result);
          setState({
            status: "success",
            progress: 100,
            error: "",
            file: null,
            expiresAt: Number(result.expires_at),
            retryable: false,
          });
          requestAnimationFrame(() => focusRef.current?.());
        } catch (error) {
          if (error?.category === "cancelled") {
            setState({ status: "idle", progress: null, error: "", file: null, expiresAt: null, retryable: false });
          } else {
            const retryable = Boolean(error?.retryable);
            setState({
              status: "error",
              progress: null,
              error: failureMessage(error),
              file: retryable ? file : null,
              expiresAt: null,
              retryable,
            });
          }
          return false;
        } finally {
          if (controllerRef.current === controller) controllerRef.current = null;
        }
      }
      return true;
    } finally {
      busyRef.current = false;
    }
  }, []);

  const onPaste = useCallback((event) => {
    const files = clipboardImageFiles(event);
    if (!files.length || !enabledRef.current || busyRef.current) return;
    event.preventDefault();
    void uploadFiles(files);
  }, [uploadFiles]);

  const choose = useCallback(() => {
    if (enabledRef.current && !busyRef.current) pickerRef.current?.click();
  }, []);
  const retry = useCallback(() => {
    if (state.file) void uploadFiles([state.file]);
  }, [state.file, uploadFiles]);

  return {
    ...state,
    busy: state.status === "uploading",
    enabled: Boolean(enabled),
    pickerRef,
    choose,
    cancel,
    retry,
    onPaste,
    uploadFiles,
  };
}

export function ImageUploadButton({ upload, className = "" }) {
  return html`<${Fragment}>
    <input
      ref=${upload.pickerRef}
      class="image-file-input"
      type="file"
      accept="image/*"
      multiple
      disabled=${!upload.enabled || upload.busy}
      onChange=${(event) => {
        const files = event.target.files;
        void upload.uploadFiles(files);
        event.target.value = "";
      }}
    />
    <button
      type="button"
      class=${cx("icon-button", "image-upload-button", className)}
      aria-label="Add image"
      title="Add image"
      disabled=${!upload.enabled || upload.busy}
      onClick=${upload.choose}
    ><${Icon} name="image" size=${18} /></button>
  <//>`;
}

export function ImageUploadStatus({ upload }) {
  if (upload.status === "idle") return null;
  if (upload.status === "uploading") {
    const progress = Number.isFinite(upload.progress) ? Math.round(upload.progress) : null;
    return html`<div class="image-upload-status uploading" role="status" aria-live="polite">
      <${Icon} name="loader-circle" size=${15} />
      <span>Uploading image${progress == null ? "…" : `… ${progress}%`}</span>
      <progress max="100" value=${progress == null ? undefined : progress}></progress>
      <button type="button" class="text-button" onClick=${upload.cancel}>Cancel</button>
    </div>`;
  }
  if (upload.status === "error") {
    return html`<div class="image-upload-status error" role="alert">
      <${Icon} name="circle-alert" size=${15} />
      <span>${upload.error}</span>
      ${upload.retryable ? html`<button type="button" class="text-button" disabled=${!upload.enabled} onClick=${upload.retry}>Retry image upload</button>` : null}
    </div>`;
  }
  const expiry = Number.isFinite(upload.expiresAt)
    ? new Date(upload.expiresAt * 1000).toLocaleString()
    : "within 24 hours";
  return html`<div class="image-upload-status success" role="status" aria-live="polite">
    <${Icon} name="circle-check" size=${15} />
    <span>Image path added. Temporary file expires ${Number.isFinite(upload.expiresAt) ? `at ${expiry}` : expiry}.</span>
  </div>`;
}
