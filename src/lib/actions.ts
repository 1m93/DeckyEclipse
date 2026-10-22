import { toaster } from "@decky/api";
import { Navigation } from "@decky/ui";
import { backend } from "../backend";
import type { AppOperation, Feature, Host, HostConfig, InstallResult, PluginUpdateCheck } from "../types";
import { flushSettings, getState, loadInitialState, refreshHosts, setState, updateSettings } from "./store";
import { openEclipsePage } from "./navigation";
import { ensureLauncherShortcut, runApp, terminateApp } from "./steam";

const TITLE = "Eclipse";
/** Drop a launch that Steam never reported as running (Moonlight failed to start). */
const LAUNCH_CONFIRM_TIMEOUT_MS = 30000;

export function notify(body: string, title = TITLE, duration?: number): void {
  toaster.toast({ title, body, duration });
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Launch the shared shortcut, which consumes the request the backend just wrote. */
async function launchPending(): Promise<number> {
  const appId = await ensureLauncherShortcut();
  runApp(appId);
  return appId;
}

/** Prepare and launch a stream. Resolves to true once Moonlight was launched. */
export async function streamApp(host: Host, appName: string): Promise<boolean> {
  const { launching, session } = getState();
  if (launching) return false;
  if (session?.confirmed) {
    notify("A stream is already running. End it first.");
    return false;
  }

  setState({ launching: host.uuid, launchStage: null });
  // The backend reads the host's display choice, make sure it is saved.
  await flushSettings();
  const offline = getState().status[host.uuid]?.online === false;
  if (offline) notify(`Waking ${host.name}...`);
  try {
    const res = await backend.prepareStream(host.uuid, appName);
    if (!res.ok) {
      notify(res.error ?? "Could not start the stream");
      return false;
    }
    const appId = await launchPending();
    const startedAt = Date.now();
    setState({
      session: { appId, hostUuid: host.uuid, hostName: host.name, appName, startedAt, confirmed: false },
    });
    setTimeout(() => {
      const current = getState().session;
      if (current && current.startedAt === startedAt && !current.confirmed) {
        setState({ session: null });
        notify("Moonlight did not start. Check the launcher log in Eclipse settings.");
      }
    }, LAUNCH_CONFIRM_TIMEOUT_MS);
    // The backend already stored lastApp; mirror it locally without another write.
    setState((prev) => ({
      settings: prev.settings && {
        ...prev.settings,
        hosts: { ...prev.settings.hosts, [host.uuid]: { ...prev.settings.hosts[host.uuid], lastApp: appName } },
      },
    }));
    Navigation.CloseSideMenus();
    return true;
  } catch (err) {
    notify(`Launch failed: ${errorText(err)}`);
    return false;
  } finally {
    setState((prev) => ({ launching: null, launchStage: null, waking: omit(prev.waking, host.uuid) }));
  }
}

export async function openMoonlight(): Promise<void> {
  try {
    await backend.prepareGui();
    await launchPending();
    Navigation.CloseSideMenus();
  } catch (err) {
    notify(`Could not open Moonlight: ${errorText(err)}`);
  }
}

export async function endStream(): Promise<void> {
  const { session } = getState();
  if (session) terminateApp(session.appId);
  // Fallback for Moonlight processes Steam does not track (e.g. started outside Steam).
  setTimeout(() => void backend.stopStream(), 1500);
  setState({ session: null });
}

export async function wakeHost(host: Host): Promise<void> {
  const res = await backend.wakeHost(host.uuid);
  notify(res.ok ? `Magic packet sent to ${host.name}` : (res.error ?? "Wake-on-LAN failed"));
  if (res.ok) {
    // Hosts usually take a few seconds to come back; refresh a couple of times.
    setTimeout(() => void refreshHosts(true), 8000);
    setTimeout(() => void refreshHosts(true), 20000);
  }
}

export async function quitHostApp(host: Host): Promise<void> {
  try {
    const res = await backend.prepareQuit(host.uuid);
    if (!res.ok) {
      notify(res.error ?? "Could not quit the running app");
      return;
    }
    if (res.launch) {
      await launchPending();
    } else {
      notify(`Closed the running app on ${host.name}`);
      void refreshHosts(true);
    }
  } catch (err) {
    notify(`Quit failed: ${errorText(err)}`);
  }
}

/** Write a pair request; the caller launches it once the user saw the PIN. */
export async function preparePairing(
  address: string,
  host: Host | null,
  creds: Partial<HostConfig> | null,
): Promise<{ pin: string; autoSubmit: boolean } | null> {
  const res = await backend.preparePair(address, host?.uuid ?? null, creds);
  if (!res.ok || !res.pin) {
    notify(res.error ?? "Could not start pairing");
    return null;
  }
  return { pin: res.pin, autoSubmit: Boolean(res.autoSubmit) };
}

export async function launchPairing(pin: string): Promise<void> {
  try {
    await launchPending();
    // Toasts stay visible on top of Moonlight, so the PIN is always at hand.
    notify(`Pairing PIN: ${pin}`, "Eclipse pairing", 30000);
  } catch (err) {
    notify(`Could not launch Moonlight: ${errorText(err)}`);
  }
}

export function toggleFavorite(host: Host, appName: string): void {
  const favorites = getState().settings?.hosts[host.uuid]?.favorites ?? [];
  const next = favorites.includes(appName) ? favorites.filter((f) => f !== appName) : [...favorites, appName];
  updateSettings({ hosts: { [host.uuid]: { favorites: next } } }, 0);
}

export const FEATURE_LABELS: Record<Feature, string> = { moonlight: "Moonlight", sunshine: "Sunshine" };

const OPERATION_DONE: Record<AppOperation, string> = {
  install: "installed",
  update: "updated",
  uninstall: "uninstalled",
};

/** Run an install/update/uninstall through the backend with shared progress state. */
async function runAppOperation(
  feature: Feature,
  operation: AppOperation,
  call: () => Promise<InstallResult>,
): Promise<boolean> {
  if (getState().installing) return false;
  setState({ installing: feature, operation, installLine: "Starting..." });
  const label = FEATURE_LABELS[feature];
  try {
    const res = await call();
    if (!res.ok) {
      notify(res.cancelled ? `${label}: ${operation} cancelled` : (res.warning ?? res.error ?? `${label}: ${operation} failed`));
      return false;
    }
    notify(res.warning ?? `${label} ${OPERATION_DONE[operation]}`, TITLE, res.warning ? 15000 : undefined);
    await loadInitialState(true);
    return true;
  } catch (err) {
    notify(`${label}: ${operation} failed: ${errorText(err)}`);
    return false;
  } finally {
    setState({ installing: null, operation: null });
  }
}

/** Install the app behind a feature from Flathub. Only ever triggered by the user. */
export function installFeature(feature: Feature): Promise<boolean> {
  return runAppOperation(feature, "install", feature === "moonlight" ? backend.installMoonlight : backend.installSunshine);
}

export function updateApp(feature: Feature): Promise<boolean> {
  return runAppOperation(feature, "update", () => backend.updateApp(feature));
}

export function uninstallApp(feature: Feature, deleteData: boolean): Promise<boolean> {
  return runAppOperation(feature, "uninstall", () => backend.uninstallApp(feature, deleteData));
}

export async function cancelInstall(): Promise<void> {
  setState({ installLine: "Cancelling..." });
  const res = await backend.cancelInstall();
  if (!res.ok) notify(res.error ?? "Could not cancel the installation");
}

/**
 * Save which features the user wants. The Sunshine service is synced with the
 * autostart option (off by default), so it never starts unless asked to.
 */
export async function saveFeatures(features: Record<Feature, boolean>): Promise<void> {
  updateSettings({ features: { ...features, configured: true } }, 0);
  await flushSettings();
  const autostart = features.sunshine && Boolean(getState().settings?.sunshine.autostart);
  await setSunshineAutostart(autostart);
}

export async function setSunshineAutostart(enabled: boolean): Promise<boolean> {
  const res = await backend.sunshineSetAutostart(enabled);
  setState((prev) => ({
    settings: prev.settings && { ...prev.settings, sunshine: { ...prev.settings.sunshine, autostart: enabled } },
  }));
  if (!res.ok) notify(res.error ?? "Could not change the Sunshine service");
  return res.ok;
}

const UPDATE_CHECK_INTERVAL_S = 24 * 60 * 60;

/**
 * Check GitHub for a new plugin release. With `automatic`, respects the daily
 * interval and the skipped version, and only notifies when there is news.
 */
export async function checkPluginUpdate(automatic: boolean): Promise<PluginUpdateCheck | null> {
  const updates = getState().settings?.updates;
  if (automatic) {
    if (!updates?.autoCheck) return null;
    if (Date.now() / 1000 - updates.lastCheck < UPDATE_CHECK_INTERVAL_S) return null;
  }
  try {
    const res = await backend.checkPluginUpdate();
    setState((prev) => ({
      settings: prev.settings && {
        ...prev.settings,
        updates: { ...prev.settings.updates, lastCheck: Math.floor(Date.now() / 1000) },
      },
    }));
    if (automatic && res.available && res.latest && res.latest !== updates?.skippedVersion) {
      toaster.toast({
        title: TITLE,
        body: `Version ${res.latest} is available (you have ${res.current}).`,
        onClick: () => openEclipsePage("about"),
      });
    }
    return res;
  } catch (err) {
    if (!automatic) notify(`Update check failed: ${errorText(err)}`);
    return null;
  }
}

function omit<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const { [key]: _removed, ...rest } = record;
  return rest;
}
