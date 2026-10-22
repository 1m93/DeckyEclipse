import { backend } from "../backend";
import type { StreamSession } from "../types";
import { getState, refreshAfterStream, setState, updateSettings, flushSettings } from "./store";

const LAUNCHER_NAME = "Moonlight (Eclipse)";
/** ELaunchSource._2ftLibraryDetails, the same source the library "Play" button uses. */
const LAUNCH_SOURCE = 100;
/** ELibraryAssetType.Capsule (portrait grid art). */
const ASSET_CAPSULE = 0;

function overview(appId: number) {
  return appId ? window.appStore.GetAppOverviewByAppID(appId) : null;
}

/** Steam's 64-bit game id for a non-Steam shortcut. */
export function gameIdFor(appId: number): string {
  const ov = overview(appId) as { m_gameid?: string; gameid?: string } | null;
  const known = ov?.m_gameid ?? ov?.gameid;
  if (known) return known;
  return ((BigInt(appId) << 32n) | 0x02000000n).toString();
}

async function waitForOverview(appId: number, timeoutMs = 3000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (overview(appId)) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

function quote(path: string): string {
  return `"${path.replace(/"/g, '\\"')}"`;
}

/** Create a non-Steam shortcut and make sure every field is applied. */
async function createShortcut(name: string, exe: string, startDir: string, launchOptions: string): Promise<number> {
  const appId = await window.SteamClient.Apps.AddShortcut(name, exe, startDir, launchOptions);
  if (!appId) throw new Error("Steam refused to create the shortcut");
  // Some Steam builds ignore part of AddShortcut's arguments, set them explicitly.
  window.SteamClient.Apps.SetShortcutName(appId, name);
  window.SteamClient.Apps.SetShortcutExe(appId, quote(exe));
  window.SteamClient.Apps.SetShortcutStartDir(appId, quote(startDir));
  window.SteamClient.Apps.SetShortcutLaunchOptions(appId, launchOptions);
  await waitForOverview(appId);
  return appId;
}

let ensuring: Promise<number> | null = null;

/**
 * Return the app id of the shared launcher shortcut, creating or repairing it
 * on demand. Paths are refreshed every time because a plugin reinstall can
 * move the plugin directory.
 */
export function ensureLauncherShortcut(): Promise<number> {
  ensuring ??= (async () => {
    const { settings, launcher } = getState();
    if (!settings || !launcher) throw new Error("Plugin state is not loaded yet");
    const launchOptions = quote(launcher.script);
    const existing = settings.shortcuts.launcherAppId;

    if (existing && overview(existing)) {
      window.SteamClient.Apps.SetShortcutExe(existing, quote(launcher.python));
      window.SteamClient.Apps.SetShortcutStartDir(existing, quote(launcher.startDir));
      window.SteamClient.Apps.SetShortcutLaunchOptions(existing, launchOptions);
      return existing;
    }

    const appId = await createShortcut(LAUNCHER_NAME, launcher.python, launcher.startDir, launchOptions);
    updateSettings({ shortcuts: { launcherAppId: appId } }, 0);
    await flushSettings();
    return appId;
  })().finally(() => {
    ensuring = null;
  });
  return ensuring;
}

export async function recreateLauncherShortcut(): Promise<number> {
  const existing = getState().settings?.shortcuts.launcherAppId;
  if (existing && overview(existing)) window.SteamClient.Apps.RemoveShortcut(existing);
  updateSettings({ shortcuts: { launcherAppId: 0 } }, 0);
  await flushSettings();
  return ensureLauncherShortcut();
}

export function runApp(appId: number): void {
  window.SteamClient.Apps.RunGame(gameIdFor(appId), "", -1, LAUNCH_SOURCE);
}

export function terminateApp(appId: number): void {
  window.SteamClient.Apps.TerminateApp(gameIdFor(appId), false);
}

export function isShortcutPresent(appId: number): boolean {
  return Boolean(overview(appId));
}

/** Create a dedicated library shortcut for a host/app pair, with box art when cached. */
export async function addLibraryShortcut(hostUuid: string, appName: string): Promise<{ appId: number; created: boolean }> {
  const res = await backend.addLibraryEntry(hostUuid, appName);
  if (!res.ok || !res.id) throw new Error(res.error ?? "Failed to create the library entry");
  if (res.existingAppId && overview(res.existingAppId)) return { appId: res.existingAppId, created: false };

  const { launcher, hosts } = getState();
  if (!launcher || !res.name || !res.launchOptions) throw new Error("Plugin state is not loaded yet");
  const hostName = hosts.find((h) => h.uuid === hostUuid)?.name ?? "";

  const appId = await createShortcut(res.name, launcher.python, launcher.startDir, res.launchOptions);
  await backend.setLibraryAppId(res.id, appId);
  setState((prev) => ({
    settings: prev.settings && {
      ...prev.settings,
      shortcuts: {
        ...prev.settings.shortcuts,
        library: {
          ...prev.settings.shortcuts.library,
          [res.id!]: { hostUuid, hostName, appName, steamAppId: appId },
        },
      },
    },
  }));

  if (res.boxartAppId) {
    const art = await backend.getBoxart(hostUuid, res.boxartAppId).catch(() => null);
    if (art) {
      await window.SteamClient.Apps.SetCustomArtworkForApp(appId, art, "png", ASSET_CAPSULE).catch(() => undefined);
    }
  }
  return { appId, created: true };
}

export async function removeLibraryShortcut(entryId: string): Promise<void> {
  const entry = getState().settings?.shortcuts.library[entryId];
  if (entry?.steamAppId && overview(entry.steamAppId)) window.SteamClient.Apps.RemoveShortcut(entry.steamAppId);
  await backend.removeLibraryEntry(entryId);
  updateSettings({ shortcuts: { library: { [entryId]: null } } }, 0);
}

/** Map Steam app ids of our shortcuts to what they stream, for session tracking. */
function describeShortcut(appId: number): { hostUuid: string; hostName: string; appName: string } | null {
  const settings = getState().settings;
  if (!settings) return null;
  for (const entry of Object.values(settings.shortcuts.library)) {
    if (entry.steamAppId === appId) {
      return { hostUuid: entry.hostUuid, hostName: entry.hostName, appName: entry.appName };
    }
  }
  return null;
}

let streamEndedHandler: (session: StreamSession) => void = () => refreshAfterStream();
let internalLaunches = 0;

/** Called once per stream when it ends, however it ended. */
export function setStreamEndedHandler(handler: (session: StreamSession) => void): void {
  streamEndedHandler = handler;
}

/**
 * The next launcher start is plugin housekeeping (e.g. `moonlight quit`),
 * not a stream, so it must not show up as a session.
 */
export function markInternalLaunch(): void {
  internalLaunches += 1;
}

/** Keep the stream session in sync with Steam's app lifetime notifications. */
export function registerLifetimeTracking(): () => void {
  const registration = window.SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n) => {
    const { session, settings } = getState();
    const isLauncher = n.unAppID === settings?.shortcuts.launcherAppId;

    if (n.bRunning) {
      if (session && session.appId === n.unAppID) {
        setState({ session: { ...session, confirmed: true } });
        return;
      }
      const library = describeShortcut(n.unAppID);
      if (library) {
        setState({ session: { appId: n.unAppID, ...library, startedAt: Date.now(), confirmed: true } });
      } else if (isLauncher && !session && internalLaunches > 0) {
        internalLaunches -= 1;
      } else if (isLauncher && !session) {
        // Launched from the library without a pending request: plain Moonlight UI.
        setState({
          session: { appId: n.unAppID, hostUuid: "", hostName: "", appName: "Moonlight", startedAt: Date.now(), confirmed: true },
        });
      }
    } else if (session && session.appId === n.unAppID) {
      setState({ session: null });
      streamEndedHandler(session);
    }
  });
  return () => registration.unregister();
}
