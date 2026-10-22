import { callable } from "@decky/api";
import type {
  AppsStatus,
  AppUpdateCheck,
  DisplayInfo,
  Feature,
  Host,
  HostConfig,
  HostDisplay,
  HostStatus,
  InitialState,
  InstallResult,
  LibraryEntryResult,
  MoonlightInfo,
  PairResult,
  Patch,
  PluginUpdateCheck,
  Preset,
  QuitResult,
  Result,
  Settings,
  SunshineStatus,
} from "./types";

/** Typed bindings for every method exposed by the Python `Plugin` class. */
export const backend = {
  getState: callable<[], InitialState>("get_state"),
  getDisplay: callable<[], { display: DisplayInfo; presets: Preset[] }>("get_display"),
  getHosts: callable<[], Host[]>("get_hosts"),
  probeHosts: callable<[force: boolean], Record<string, HostStatus>>("probe_hosts"),
  updateSettings: callable<[patch: Patch<Settings>], Settings>("update_settings"),
  setHostConfig: callable<[uuid: string, patch: Patch<HostConfig> | null], Result & { settings: Settings }>(
    "set_host_config",
  ),
  refreshMoonlight: callable<[], MoonlightInfo>("refresh_moonlight"),

  wakeHost: callable<[uuid: string], Result>("wake_host"),
  prepareStream: callable<[uuid: string, appName: string], Result>("prepare_stream"),
  prepareGui: callable<[], Result>("prepare_gui"),
  preparePair: callable<
    [address: string, uuid: string | null, creds: Partial<HostConfig> | null],
    PairResult
  >("prepare_pair"),
  prepareQuit: callable<[uuid: string], QuitResult>("prepare_quit"),
  stopStream: callable<[], void>("stop_stream"),
  testHostCredentials: callable<[uuid: string], Result>("test_host_credentials"),
  detectHostDisplays: callable<[uuid: string], Result & { displays: HostDisplay[] }>("detect_host_displays"),

  addLibraryEntry: callable<[uuid: string, appName: string], LibraryEntryResult>("add_library_entry"),
  setLibraryAppId: callable<[entryId: string, steamAppId: number], void>("set_library_app_id"),
  removeLibraryEntry: callable<[entryId: string], void>("remove_library_entry"),
  getBoxart: callable<[uuid: string, appId: string], string | null>("get_boxart"),

  installMoonlight: callable<[], InstallResult>("install_moonlight"),
  installSunshine: callable<[], InstallResult>("install_sunshine"),
  cancelInstall: callable<[], Result>("cancel_install"),
  appsStatus: callable<[], AppsStatus>("apps_status"),
  checkAppUpdate: callable<[feature: Feature], AppUpdateCheck>("check_app_update"),
  updateApp: callable<[feature: Feature], InstallResult>("update_app"),
  uninstallApp: callable<[feature: Feature, deleteData: boolean], InstallResult>("uninstall_app"),
  checkPluginUpdate: callable<[], PluginUpdateCheck>("check_plugin_update"),

  sunshineStatus: callable<[], SunshineStatus>("sunshine_status"),
  sunshineStart: callable<[], Result>("sunshine_start"),
  sunshineStop: callable<[], Result>("sunshine_stop"),
  sunshineSetAutostart: callable<[enabled: boolean], Result>("sunshine_set_autostart"),
  sunshineSubmitPin: callable<[pin: string, clientName: string], Result>("sunshine_submit_pin"),

  getLauncherLog: callable<[], string>("get_launcher_log"),
  getPluginLog: callable<[], string>("get_plugin_log"),
};

/** Backend events emitted with `decky.emit`. */
export const Events = {
  installProgress: "eclipse_install_progress",
  wakeProgress: "eclipse_wake_progress",
  pairResult: "eclipse_pair_result",
  streamProgress: "eclipse_stream_progress",
} as const;
