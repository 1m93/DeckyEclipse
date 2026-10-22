import { useSyncExternalStore } from "react";
import { backend } from "../backend";
import type {
  AppOperation,
  DisplayInfo,
  Feature,
  Host,
  HostConfig,
  HostStatus,
  LauncherInfo,
  MoonlightInfo,
  Patch,
  Preset,
  Settings,
  StreamSession,
  StreamSettings,
  SunshineOverview,
  SunshineSession,
} from "../types";
import { deepMerge, mergePatches } from "./merge";

export interface MonitorState {
  loading: boolean;
  /** `output_name` Sunshine uses now; "" is its default monitor, null unknown. */
  current: string | null;
  /** When Sunshine last listed its monitors (host clock). */
  detectedAt: string | null;
  error: string | null;
}

/** Tab of the Quick Access menu: streaming from a PC, or sharing this device. */
export type QuickTab = "play" | "share";

export interface State {
  ready: boolean;
  loadError: string | null;
  settings: Settings | null;
  presets: Preset[];
  autoPreset: string;
  customPreset: string;
  inheritPreset: string;
  display: DisplayInfo | null;
  hosts: Host[];
  status: Record<string, HostStatus>;
  refreshing: boolean;
  moonlight: MoonlightInfo | null;
  launcher: LauncherInfo | null;
  configPath: string | null;
  installing: Feature | null;
  operation: AppOperation | null;
  installLine: string;
  pluginVersion: string | null;
  /** Wake progress per host uuid: [elapsed, total] seconds. */
  waking: Record<string, [number, number]>;
  session: StreamSession | null;
  /** Host uuid whose stream is being prepared (waking, writing request...). */
  launching: string | null;
  /** Current step while preparing a stream, e.g. "Switching host display...". */
  launchStage: string | null;
  /** Live monitor info per host uuid, refreshed when it matters. */
  monitors: Record<string, MonitorState>;
  /** Page and host the full settings page should open on. */
  /** `nonce` changes on every request, so asking for the same host again still applies. */
  pageTarget: { page: string; host: string | null; log?: string | null; nonce: number };
  /** Streams from the Sunshine on this device, pushed by the backend. */
  incoming: SunshineSession | null;
  /** Address and paired devices of the Sunshine on this device. */
  sharing: SunshineOverview | null;
  /** The user asked to end the streams from this device and it is under way. */
  endingIncoming: boolean;
  quickTab: QuickTab;
}

const QUICK_TAB_KEY = "eclipse.quickTab";

/** The last Quick Access tab is a per-device convenience: storage may be unavailable. */
function readQuickTab(): QuickTab {
  try {
    return window.localStorage.getItem(QUICK_TAB_KEY) === "share" ? "share" : "play";
  } catch {
    return "play";
  }
}

const initialState: State = {
  ready: false,
  loadError: null,
  settings: null,
  presets: [],
  autoPreset: "auto",
  customPreset: "custom",
  inheritPreset: "global",
  display: null,
  hosts: [],
  status: {},
  refreshing: false,
  moonlight: null,
  launcher: null,
  configPath: null,
  installing: null,
  operation: null,
  installLine: "",
  pluginVersion: null,
  waking: {},
  session: null,
  launching: null,
  launchStage: null,
  monitors: {},
  pageTarget: { page: "hosts", host: null, nonce: 0 },
  incoming: null,
  sharing: null,
  endingIncoming: false,
  quickTab: readQuickTab(),
};

// ---------------------------------------------------------------- core store

let state: State = initialState;
const listeners = new Set<() => void>();

export function getState(): State {
  return state;
}

export function setState(patch: Partial<State> | ((prev: State) => Partial<State>)): void {
  const next = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...next };
  listeners.forEach((listener) => listener());
}

/** `remember`: the user picked it. Automatic switches do not replace the user's choice. */
export function setQuickTab(tab: QuickTab, remember = true): void {
  if (state.quickTab !== tab) setState({ quickTab: tab });
  if (!remember) return;
  try {
    window.localStorage.setItem(QUICK_TAB_KEY, tab);
  } catch {
    // Not remembered, which is fine.
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Subscribe a component to a slice of the store. Selectors must return
 * values that are referentially stable between unrelated updates (state
 * fields or primitives) to avoid needless re-renders.
 */
export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state));
}

// ---------------------------------------------------------------- loading

let loadPromise: Promise<void> | null = null;

/** Without an answer by then, the backend most likely failed to start: say so instead of loading forever. */
const LOAD_TIMEOUT_MS = 20000;

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (err: unknown) => {
        window.clearTimeout(timer);
        reject(err);
      },
    );
  });
}

export function loadInitialState(force = false): Promise<void> {
  if (loadPromise && !force) return loadPromise;
  // Retrying after an error: show the spinner again meanwhile.
  if (state.loadError) setState({ loadError: null });
  loadPromise = withTimeout(
    backend.getState(),
    LOAD_TIMEOUT_MS,
    "The Eclipse backend does not answer. It may have failed to start: see ~/homebrew/logs/Eclipse/ on the device, or restart Decky.",
  )
    .then((initial) => {
      setState({
        ready: true,
        loadError: null,
        settings: initial.settings,
        presets: initial.presets,
        autoPreset: initial.autoPreset,
        display: initial.display,
        customPreset: initial.customPreset,
        inheritPreset: initial.inheritPreset,
        hosts: initial.hosts,
        moonlight: initial.moonlight,
        launcher: initial.launcher,
        configPath: initial.configPath,
        installing: initial.installing,
        operation: initial.operation,
        pluginVersion: initial.pluginVersion,
      });
    })
    .catch((err: unknown) => {
      loadPromise = null;
      setState({ loadError: err instanceof Error ? err.message : String(err) });
    });
  return loadPromise;
}

/**
 * Re-detect the display (docking changes it) and refresh the Auto preset.
 * When Auto is active, the shown stream values follow it locally; the backend
 * re-detects on its own at launch time, so nothing needs to be saved.
 */
export async function refreshDisplay(): Promise<void> {
  try {
    const { display, presets } = await backend.getDisplay();
    setState((prev) => {
      const auto = presets.find((p) => p.id === prev.autoPreset);
      const followAuto = auto && prev.settings?.activePreset === prev.autoPreset;
      return {
        display,
        presets: sameJson(prev.presets, presets) ? prev.presets : presets,
        settings:
          followAuto && prev.settings
            ? { ...prev.settings, stream: { ...prev.settings.stream, ...auto.values } }
            : prev.settings,
      };
    });
  } catch (err) {
    console.error("[Eclipse] display detection failed", err);
  }
}

export function featureEnabled(s: State, feature: Feature): boolean {
  return Boolean(s.settings?.features[feature]);
}

type IncomingHandler = (prev: SunshineSession | null, next: SunshineSession) => void;
let incomingHandler: IncomingHandler | null = null;

/** Called on every change of the incoming stream state, wherever it came from (event or refresh). */
export function setIncomingHandler(handler: IncomingHandler | null): void {
  incomingHandler = handler;
}

export function applyIncoming(next: SunshineSession): void {
  const prev = state.incoming;
  if (sameJson(prev, next)) return;
  setState({ incoming: next });
  incomingHandler?.(prev, next);
}

/** Sync the incoming stream state, e.g. when the UI opens (events only report changes). */
export async function refreshIncoming(): Promise<SunshineSession | null> {
  try {
    const incoming = await backend.sunshineSession();
    applyIncoming(incoming);
    return incoming;
  } catch (err) {
    console.error("[Eclipse] Sunshine session check failed", err);
    return null;
  }
}

export async function refreshSharing(): Promise<void> {
  try {
    const sharing = await backend.sunshineOverview();
    setState((prev) => (sameJson(prev.sharing, sharing) ? {} : { sharing }));
  } catch (err) {
    console.error("[Eclipse] Sunshine overview failed", err);
    // Show "unknown" instead of looking up forever.
    setState((prev) => (prev.sharing ? {} : { sharing: { address: null, paired: null } }));
  }
}

// ---------------------------------------------------------------- host status polling

let pollUsers = 0;
let pollTimer: number | undefined;
let inFlight: Promise<void> | null = null;

export function refreshHosts(force = false): Promise<void> {
  if (inFlight) return inFlight;
  setState({ refreshing: true });
  inFlight = (async () => {
    try {
      const [hosts, status] = await Promise.all([backend.getHosts(), backend.probeHosts(force)]);
      // Keep previous references when nothing changed, so rows don't re-render.
      setState((prev) => ({
        hosts: sameJson(prev.hosts, hosts) ? prev.hosts : hosts,
        status: sameJson(prev.status, status) ? prev.status : status,
      }));
    } catch (err) {
      console.error("[Eclipse] refresh failed", err);
    } finally {
      inFlight = null;
      setState({ refreshing: false });
    }
  })();
  return inFlight;
}

/**
 * Polling pauses during a stream, so host states are stale when it ends.
 * Re-probe shortly after (and once more, since quitting the app on the host
 * can take a few seconds).
 */
export function refreshAfterStream(): void {
  window.setTimeout(() => void refreshHosts(true), 2000);
  window.setTimeout(() => void refreshHosts(true), 8000);
}

function schedulePoll(): void {
  window.clearTimeout(pollTimer);
  if (pollUsers <= 0) return;
  const seconds = Math.max(3, state.settings?.behavior.pollIntervalS ?? 8);
  pollTimer = window.setTimeout(async () => {
    // Never probe while streaming: it would only compete for bandwidth.
    if (!state.session) await refreshHosts();
    schedulePoll();
  }, seconds * 1000);
}

/**
 * Ref-counted polling: the Quick Access panel and the full page can both
 * request polling, but only one timer ever runs.
 */
export function acquirePolling(): () => void {
  pollUsers += 1;
  if (pollUsers === 1) {
    void refreshHosts();
    schedulePoll();
  }
  return () => {
    pollUsers -= 1;
    if (pollUsers <= 0) {
      pollUsers = 0;
      window.clearTimeout(pollTimer);
    }
  };
}

export function stopAllPolling(): void {
  pollUsers = 0;
  window.clearTimeout(pollTimer);
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ---------------------------------------------------------------- settings

let pendingPatch: Patch<Settings> | null = null;
let saveTimer: number | undefined;

/**
 * Optimistically apply a settings patch and persist it. Rapid changes (e.g.
 * dragging a slider) are coalesced into a single backend write.
 */
export function updateSettings(patch: Patch<Settings>, debounceMs = 250): void {
  setState((prev) => ({ settings: prev.settings ? deepMerge(prev.settings, patch) : prev.settings }));
  pendingPatch = pendingPatch ? mergePatches(pendingPatch, patch) : patch;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(flushSettings, debounceMs);
}

export async function flushSettings(): Promise<void> {
  window.clearTimeout(saveTimer);
  const patch = pendingPatch;
  pendingPatch = null;
  if (!patch) return;
  try {
    const saved = await backend.updateSettings(patch);
    if (!pendingPatch) setState({ settings: saved });
  } catch (err) {
    console.error("[Eclipse] failed to save settings", err);
  }
}

/** Update stream settings; any manual change detaches from the active preset. */
export function updateStream(patch: Partial<StreamSettings>): void {
  const { presets, customPreset, settings } = state;
  const active = presets.find((p) => p.id === settings?.activePreset);
  const touchesPreset = active && Object.keys(patch).some((key) => key in active.values);
  updateSettings({ stream: patch, ...(touchesPreset ? { activePreset: customPreset } : {}) });
}

export function applyPreset(presetId: string): void {
  const preset = state.presets.find((p) => p.id === presetId);
  updateSettings({ activePreset: presetId, ...(preset ? { stream: preset.values } : {}) }, 0);
}

export async function setHostConfig(uuid: string, patch: Patch<HostConfig> | null): Promise<string | null> {
  const res = await backend.setHostConfig(uuid, patch);
  setState({ settings: res.settings });
  return res.ok ? null : res.error;
}

export function hostConfig(uuid: string): HostConfig {
  return state.settings?.hosts[uuid] ?? {};
}
