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
}

export interface HostStatus {
  online: boolean;
  busy: boolean;
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
}

/** Per-host settings. Secrets come back masked as `<key>Set` booleans. */
export interface HostConfig {
  /** Known displays of the host. */
  displays?: HostDisplay[];
  /** Selected `outputName`; unset means "do not change Sunshine's setting". */
  display?: string;
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

/** Install state of Moonlight or Sunshine. Only Flatpak installs are `managed` by Eclipse. */
export interface AppStatus {
  installed: boolean;
  managed: boolean;
  version: string | null;
  installation: string | null;
  origin?: string;
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

export interface SunshineStatus {
  installed: boolean;
  kind: "systemd" | "flatpak" | "native" | null;
  unit: string | null;
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
}
