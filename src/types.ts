/** Shapes shared with the Python backend (see main.py). */

export interface HostAddress {
  kind: "manual" | "local" | "remote" | "ipv6";
  address: string;
  port: number;
}

export interface HostApp {
  id: string;
  name: string;
  hdr: boolean;
  hidden: boolean;
}

export interface Host {
  uuid: string;
  name: string;
  addresses: HostAddress[];
  mac: string | null;
  apps: HostApp[];
  /** Moonlight holds the host's certificate, i.e. pairing succeeded. */
  paired?: boolean;
}

export interface HostStatus {
  online: boolean;
  /** An app is open on the host (it may have no client attached). */
  busy: boolean;
  /** Moonlight app id of the open app, matches `HostApp.id`. */
  currentGame?: string | null;
  hostname?: string | null;
  version?: string | null;
  mac?: string | null;
  address?: string;
  port?: number;
  latencyMs?: number;
}

export type VideoCodec = "auto" | "H.264" | "HEVC" | "AV1";
export type VideoDecoder = "auto" | "hardware" | "software";
export type AudioConfig = "stereo" | "5.1-surround" | "7.1-surround";
export type DisplayMode = "fullscreen" | "borderless" | "windowed";

export interface StreamSettings {
  resolution: string;
  fps: number;
  bitrateKbps: number;
  videoCodec: VideoCodec;
  videoDecoder: VideoDecoder;
  hdr: boolean;
  yuv444: boolean;
  audioConfig: AudioConfig;
  audioOnHost: boolean;
  displayMode: DisplayMode;
  vsync: boolean;
  framePacing: boolean;
  performanceOverlay: boolean;
  gameOptimization: boolean;
  quitAppAfter: boolean;
  multiController: boolean;
  absoluteMouse: boolean;
  muteOnFocusLoss: boolean;
  keepAwake: boolean;
}

export interface Preset {
  id: string;
  label: string;
  values: Partial<StreamSettings>;
}

/** A display of the host that Sunshine can capture. */
export interface HostDisplay {
  /** Value for Sunshine's `output_name` (connector name on Linux, device id on Windows). */
  outputName: string;
  label: string;
  /** Added by hand; kept even when Sunshine's list does not contain it. */
  manual?: boolean;
}

export interface MonitorRefresh extends Result {
  /** When Sunshine last listed its monitors (host clock, from its log). */
  detectedAt?: string | null;
  /** `output_name` Sunshine uses now; empty string means its default monitor. */
  current?: string | null;
  /** The selected monitor is gone and the selection was cleared. */
  selectionLost?: boolean;
  settings: Settings;
}

/** Per-host settings. Secrets come back masked as `<key>Set` booleans. */
export interface HostConfig {
  /** Known displays of the host. */
  displays?: HostDisplay[];
  /** Selected `outputName`; unset means "do not change Sunshine's setting". */
  display?: string;
  /** Ask which monitor to use every time a stream starts. */
  askMonitor?: boolean;
  mac?: string;
  preset?: string;
  lastApp?: string;
  favorites?: string[];
  sunshineUser?: string;
  sunshinePassword?: string;
  sunshinePasswordSet?: boolean;
  sunshinePort?: number;
}

export interface LibraryEntry {
  hostUuid: string;
  hostName: string;
  appName: string;
  steamAppId: number;
}

export type MoonlightSource = "auto" | "flatpak" | "native" | "custom";

export type Feature = "moonlight" | "sunshine";

export interface Settings {
  version: number;
  /** Opt-in features chosen during first-run setup. */
  features: { configured: boolean; moonlight: boolean; sunshine: boolean };
  activePreset: string;
  stream: StreamSettings;
  hosts: Record<string, HostConfig>;
  moonlight: { source: MoonlightSource; customCommand: string };
  sunshine: {
    user: string;
    password?: string;
    passwordSet?: boolean;
    port: number;
    deviceName: string;
    autostart: boolean;
  };
  behavior: {
    autoWake: boolean;
    wakeTimeoutS: number;
    showOffline: boolean;
    pollIntervalS: number;
  };
  updates: {
    autoCheck: boolean;
    lastCheck: number;
    skippedVersion: string;
  };
  shortcuts: {
    launcherAppId: number;
    library: Record<string, LibraryEntry>;
  };
}

/** Recursive partial used for settings patches; `null` deletes a key. */
export type Patch<T> = {
  [K in keyof T]?: T[K] extends readonly unknown[]
    ? T[K] | null
    : T[K] extends object
      ? Patch<T[K]> | null
      : T[K] | null;
};

export interface MoonlightInfo {
  installed: boolean;
  source: string;
  version?: string | null;
  installation?: string | null;
  error?: string;
}

export interface LauncherInfo {
  python: string;
  script: string;
  startDir: string;
}

export interface DisplayInfo {
  width: number;
  height: number;
  resolution: string;
  refreshHz: number;
  /** Where the refresh rate came from: the display's EDID, a known-device table, or the 60 Hz default. */
  refreshSource: "edid" | "device" | "default";
  output: string | null;
  external: boolean;
}

export interface InitialState {
  settings: Settings;
  presets: Preset[];
  autoPreset: string;
  display: DisplayInfo;
  customPreset: string;
  inheritPreset: string;
  hosts: Host[];
  moonlight: MoonlightInfo;
  configPath: string | null;
  launcher: LauncherInfo;
  installing: Feature | null;
  operation: AppOperation | null;
  pluginVersion: string | null;
}

export type AppOperation = "install" | "update" | "uninstall";

/**
 * Install state of Moonlight or Sunshine. Eclipse manages Flatpak installs and
 * Sunshine for Gaming Mode (`installation: "eclipse"`).
 */
export interface AppStatus {
  installed: boolean;
  managed: boolean;
  version: string | null;
  installation: string | null;
  origin?: string;
  /** Sunshine for Gaming Mode is installed and a Sunshine Flatpak is still there too. */
  alsoFlatpak?: boolean;
}

export interface AppsStatus {
  moonlight: AppStatus;
  sunshine: AppStatus;
  operation: AppOperation | null;
  busy: Feature | null;
}

export interface AppUpdateCheck extends Result {
  available: boolean;
  version: string | null;
}

export interface PluginUpdateCheck extends Result {
  current: string;
  latest: string | null;
  available: boolean;
  url: string | null;
  downloadUrl: string | null;
  notes: string | null;
}

export interface Result {
  ok: boolean;
  error: string | null;
}

export interface InstallResult extends Result {
  /** Set when the install worked but a follow-up step needs attention. */
  warning?: string | null;
  /** True when the user cancelled the installation. */
  cancelled?: boolean;
  /** sudo did not accept the password. */
  passwordRejected?: boolean;
}

export interface StreamPrepareResult extends Result {
  /** The selected host monitor is gone; the user should pick another one. */
  monitorMissing?: boolean;
}

export interface PairResult extends Result {
  pin?: string;
  autoSubmit?: boolean;
}

export interface QuitResult extends Result {
  launch?: boolean;
}

export interface LibraryEntryResult extends Result {
  id?: string;
  existingAppId?: number;
  name?: string;
  launchOptions?: string;
  boxartAppId?: string;
}

export interface DiagnosticCheck {
  id: string;
  label: string;
  /** true: fine · false: a problem · null: could not tell / not applicable. */
  ok: boolean | null;
  detail: string;
  hint: string;
}

export interface SunshineDiagnostics {
  checks: DiagnosticCheck[];
  addresses: { interface: string; address: string }[];
  logPath: string | null;
}

/** See `sunshine_login_state` in main.py. */
export type SunshineLoginState = "ok" | "needs_setup" | "invalid" | "missing" | "unreachable";

/** Sunshine for Gaming Mode, see py_modules/eclipse/sunshine_native.py. */
export interface NativeSunshineState {
  installed: boolean;
  version: string | null;
  /** The launcher has the capabilities needed to capture Gaming Mode. */
  captureReady: boolean;
  /** The plugin ships newer helpers than the installed ones: a repair installs them. */
  helpersOutdated: boolean;
}

export interface SunshineStatus {
  installed: boolean;
  /** "eclipse": Sunshine for Gaming Mode. */
  kind: "eclipse" | "systemd" | "flatpak" | "native" | null;
  unit: string | null;
  /** A Sunshine Flatpak is installed (possibly next to Sunshine for Gaming Mode). */
  flatpak: boolean;
  native: NativeSunshineState | null;
  running: boolean;
  /** The plugin's autostart option. */
  autostart: boolean;
  /** Whether the systemd user service is enabled (it then starts at login on its own). */
  unitEnabled: boolean;
  credentials: boolean;
}

export interface StreamSession {
  /** Steam app id of the shortcut running the stream. */
  appId: number;
  hostUuid: string;
  hostName: string;
  appName: string;
  startedAt: number;
  /** True once Steam reported the shortcut as running. */
  confirmed: boolean;
  /** Set when Moonlight was opened to pair with this address, not to stream. */
  pairAddress?: string;
}

/** A device waiting for its PIN to be entered (newer Sunshine). */
export interface PendingPairing {
  id: string;
  name: string;
  address: string;
}

export interface PairedClient {
  uuid: string;
  name: string;
  enabled: boolean;
}

/** Result of entering a PIN; `paired` is the real outcome, not just "PIN sent". */
export interface PairOutcome extends Result {
  paired: boolean;
  client?: string;
  /** Several devices are waiting: pick one and retry with its id. */
  choose?: PendingPairing[];
}
