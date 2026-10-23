import { toaster } from "@decky/api";
import { ConfirmModal, Navigation, ToggleField, showModal } from "@decky/ui";
import { useState } from "react";
import { backend } from "../backend";
import type {
  AppOperation,
  Feature,
  Host,
  HostConfig,
  InstallResult,
  PluginUpdateCheck,
  StreamSession,
  SunshineOptions,
  SunshineOptionsResult,
} from "../types";
import {
  flushSettings,
  getState,
  loadInitialState,
  refreshAfterStream,
  refreshHosts,
  refreshIncoming,
  setState,
  updateSettings,
} from "./store";
import { chooseMonitor } from "../components/MonitorChoiceModal";
import { askSudoPassword } from "../components/SudoPasswordModal";
import { showLocalSunshineLogin } from "../components/SunshineLoginModal";
import { hasSunshineLogin } from "./monitors";
import { openEclipsePage } from "./navigation";
import { ensureLauncherShortcut, markInternalLaunch, runApp, terminateApp } from "./steam";

const TITLE = "Eclipse";
/** Drop a launch that Steam never reported as running (Moonlight failed to start). */
const LAUNCH_CONFIRM_TIMEOUT_MS = 30000;

export function notify(body: string, title = TITLE, duration?: number): void {
  toaster.toast({ title, body, duration });
}

/** A stream problem: pressing the toast opens the connection checks for that host. */
function notifyStreamProblem(body: string, hostUuid: string): void {
  toaster.toast({
    title: TITLE,
    body: `${/[.!?]$/.test(body) ? body : `${body}.`} Press for connection checks.`,
    onClick: () => openEclipsePage("help", hostUuid),
  });
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Launch the shared shortcut, which consumes the request the backend just wrote.
 * `internal` launches (quitting a host app) are not tracked as stream sessions.
 */
async function launchPending(internal = false): Promise<number> {
  const appId = await ensureLauncherShortcut();
  if (internal) markInternalLaunch();
  runApp(appId);
  return appId;
}

/**
 * Prepare and launch a stream. Resolves to true once Moonlight was launched.
 * `monitorChosen` skips the "which monitor?" prompt (the app picker already
 * shows a monitor choice).
 */
export async function streamApp(
  host: Host,
  appName: string,
  { monitorChosen = false }: { monitorChosen?: boolean } = {},
): Promise<boolean> {
  const { launching, session } = getState();
  if (launching) return false;
  if (session?.confirmed) {
    notify("A stream is already running. End it first.");
    return false;
  }

  if (!monitorChosen && getState().settings?.hosts[host.uuid]?.askMonitor && hasSunshineLogin(host.uuid)) {
    const choice = await chooseMonitor(host);
    if (choice === undefined) return false;
    updateSettings({ hosts: { [host.uuid]: { display: choice } } }, 0);
  }

  setState({ launching: host.uuid, launchStage: null });
  // The backend reads the host's display choice, make sure it is saved.
  await flushSettings();
  const offline = getState().status[host.uuid]?.online === false;
  if (offline) notify(`Waking ${host.name}...`);
  try {
    let res = await backend.prepareStream(host.uuid, appName);
    if (!res.ok && res.monitorMissing) {
      // The chosen monitor was unplugged: let the user pick from a fresh list, then retry once.
      notify(res.error ?? "The selected monitor is gone");
      const choice = await chooseMonitor(host);
      if (choice === undefined) return false;
      updateSettings({ hosts: { [host.uuid]: { display: choice } } }, 0);
      await flushSettings();
      res = await backend.prepareStream(host.uuid, appName);
    }
    if (!res.ok) {
      notifyStreamProblem(res.error ?? "Could not start the stream", host.uuid);
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
        notifyStreamProblem("Moonlight did not start", host.uuid);
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
    notifyStreamProblem(`Launch failed: ${errorText(err)}`, host.uuid);
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

/** How long to wait for Steam to report the stream as stopped before forcing it. */
const END_STREAM_FALLBACK_MS = 3000;
const handledEnds = new Set<number>();

/**
 * Runs once per stream, however it ended (End stream, Exit game, quitting
 * Moonlight). Refreshes host states and, when "Quit app after stream" is on,
 * makes sure the session on the host ends too.
 */
export function handleStreamEnded(session: StreamSession): void {
  if (handledEnds.has(session.startedAt)) return;
  handledEnds.add(session.startedAt);
  refreshAfterStream();
  if (session.pairAddress) {
    void reportPairing(session.pairAddress);
    return;
  }
  if (!session.hostUuid) return;

  void (async () => {
    try {
      const res = await backend.quitAfterStream(session.hostUuid);
      if (!res.ok) {
        console.warn("[Eclipse] quit after stream failed", res.error);
      } else if (res.launch) {
        // No Sunshine credentials: let Moonlight quit the app (a brief window).
        await launchPending(true);
      }
    } catch (err) {
      console.error("[Eclipse] quit after stream failed", err);
    }
  })();
}

function ConfirmEndStreamModal({
  session,
  onEnd,
  closeModal,
}: {
  session: StreamSession;
  onEnd: () => void;
  closeModal?: () => void;
}) {
  const [dontAsk, setDontAsk] = useState(false);
  return (
    <ConfirmModal
      strTitle={`Close ${session.appName} on ${session.hostName}?`}
      strDescription="Ending the stream also closes the game on the host, so unsaved progress there may be lost. To keep games open after a stream, turn off End the host session too in Settings → Streaming."
      strOKButtonText="End stream and close"
      closeModal={closeModal}
      onOK={() => {
        if (dontAsk) updateSettings({ behavior: { confirmEndStream: false } }, 0);
        onEnd();
      }}
    >
      <ToggleField label="Don't ask again" checked={dontAsk} onChange={setDontAsk} />
    </ConfirmModal>
  );
}

/**
 * End the stream from the Quick Access menu. When that also closes the game
 * on the host, ask first (unless the user turned the question off).
 */
export function requestEndStream(): void {
  const { session, settings } = getState();
  const closesHostApp = Boolean(session?.hostUuid) && !session?.pairAddress && (settings?.stream.quitAppAfter ?? true);
  if (!session || !closesHostApp || settings?.behavior.confirmEndStream === false) {
    void endStream();
    return;
  }
  showModal(<ConfirmEndStreamModal session={session} onEnd={() => void endStream()} />);
}

export async function endStream(): Promise<void> {
  const { session } = getState();
  if (!session) {
    void backend.stopStream();
    return;
  }
  terminateApp(session.appId);
  // Steam normally reports the app as stopped and handleStreamEnded runs from
  // there. If it does not, force-stop Moonlight and finish the job here.
  setTimeout(() => {
    const current = getState().session;
    if (current?.startedAt !== session.startedAt) return;
    void backend.stopStream();
    setState({ session: null });
    handleStreamEnded(session);
  }, END_STREAM_FALLBACK_MS);
}

/** Re-check incoming streams a few times while Sunshine winds them down. */
function refreshIncomingSoon(): void {
  for (const delay of [1500, 4000, 8000]) window.setTimeout(() => void refreshIncoming(), delay);
}

/** Session toasts stay quiet briefly after the user ended the streams: they already got feedback. */
const QUIET_AFTER_END_MS = 15000;
let quietUntil = 0;

export function incomingToastsMuted(): boolean {
  return Date.now() < quietUntil;
}

function confirmRestart(reason: string, onOK: () => void): void {
  showModal(
    <ConfirmModal
      strTitle="Restart Sunshine?"
      strDescription={`${reason} Restarting Sunshine disconnects every device streaming from here; it is back a few seconds later.`}
      strOKButtonText="Restart Sunshine"
      onOK={onOK}
    />,
  );
}

/**
 * End every stream from the Sunshine on this device, or close the app left
 * open there. The open app is closed through Sunshine's API with the saved
 * login. Sunshine is only restarted when that is impossible, and only after
 * the user agreed.
 */
export async function endIncomingStream(): Promise<void> {
  if (getState().endingIncoming) return;
  const streaming = (getState().incoming?.clients ?? 0) > 0;
  const done = streaming ? "The stream from this device ended" : "Closed the app on this device";
  const run = async (restart: boolean): Promise<void> => {
    setState({ endingIncoming: true });
    quietUntil = Date.now() + QUIET_AFTER_END_MS;
    try {
      const res = await backend.sunshineEndSession(restart);
      if (res.ok) {
        notify(res.method === "restarted" ? `Sunshine restarted. ${done}.` : `${done}.`);
      } else if (res.needsRestart && !restart) {
        quietUntil = 0;
        const reason = res.error
          ? `Sunshine did not close the app (${res.error}).`
          : "Without a Sunshine login saved in Eclipse, the app cannot be closed directly.";
        confirmRestart(reason, () => void run(true));
      } else {
        quietUntil = 0;
        notify(res.error ?? "Could not end the stream");
      }
    } catch (err) {
      quietUntil = 0;
      notify(`Could not end the stream: ${errorText(err)}`);
    } finally {
      setState({ endingIncoming: false });
      refreshIncomingSoon();
    }
  };
  await run(false);
}

/** Stopping Sunshine drops whoever is streaming from here: ask first in that case. */
export function confirmStopSunshine(onStop: () => void): void {
  const clients = getState().incoming?.clients ?? 0;
  if (clients === 0) {
    onStop();
    return;
  }
  showModal(
    <ConfirmModal
      strTitle="Turn off Sunshine?"
      strDescription={`${clients === 1 ? "A device is" : `${clients} devices are`} streaming from here and will be disconnected.`}
      strOKButtonText="Turn off"
      onOK={onStop}
    />,
  );
}

function applyOptionsResult(res: SunshineOptionsResult): boolean {
  if (!res.ok || !res.values || !res.defaults) return false;
  const { values, defaults } = res;
  setState((prev) => ({
    sunshineOptions: {
      values,
      defaults,
      needsRestart: Boolean(res.needsRestart),
      restarting: prev.sunshineOptions?.restarting ?? false,
    },
  }));
  return true;
}

/** Option writes queued and not finished yet, and how many were ever queued. */
let writesPending = 0;
let writesQueued = 0;

/**
 * Read the options again. Resolves to an error text when they could not be
 * read. A read that overlaps a write is dropped: it may predate the write.
 */
export async function refreshSunshineOptions(): Promise<string | null> {
  const queuedBefore = writesQueued;
  try {
    const res = await backend.sunshineGetOptions();
    if (writesPending > 0 || writesQueued !== queuedBefore) return null;
    if (applyOptionsResult(res)) return null;
    return res.error ?? "Could not read Sunshine's options";
  } catch (err) {
    return `Could not read Sunshine's options: ${errorText(err)}`;
  }
}

/** Writes one after another: each reads and rewrites the same file. */
let optionsQueue: Promise<unknown> = Promise.resolve();

/**
 * Change options of the Sunshine on this device: shown at once, saved in
 * order. Resolves to an error text (also shown as a toast) or null.
 */
export function setSunshineOptions(patch: Partial<SunshineOptions>): Promise<string | null> {
  setState((prev) =>
    prev.sunshineOptions
      ? { sunshineOptions: { ...prev.sunshineOptions, values: { ...prev.sunshineOptions.values, ...patch } } }
      : {},
  );
  writesPending += 1;
  writesQueued += 1;
  const write = optionsQueue.then(async (): Promise<string | null> => {
    let error: string | null = null;
    let res: SunshineOptionsResult | null = null;
    try {
      res = await backend.sunshineSetOptions(patch);
      if (!res.ok) error = res.error ?? "Could not save the Sunshine option";
    } catch (err) {
      error = `Could not save the Sunshine option: ${errorText(err)}`;
    }
    writesPending -= 1;
    if (error) {
      notify(error);
      // Show what the file really holds; later writes refresh again on their own.
      if (writesPending === 0) await refreshSunshineOptions();
    } else if (res && writesPending === 0) {
      // Earlier results would undo the optimistic values of writes still waiting.
      applyOptionsResult(res);
    }
    return error;
  });
  optionsQueue = write;
  return write;
}

/** Restart Sunshine so changed options apply; asks first when devices are streaming from here. */
export function restartSunshineToApply(): void {
  const run = async () => {
    setState((prev) => (prev.sunshineOptions ? { sunshineOptions: { ...prev.sunshineOptions, restarting: true } } : {}));
    quietUntil = Date.now() + QUIET_AFTER_END_MS;
    try {
      // A change still being saved must be in the file before Sunshine reads it.
      await optionsQueue;
      const res = await backend.sunshineRestart();
      notify(res.ok ? "Sunshine restarted: the changes are applied." : (res.error ?? "Could not restart Sunshine"));
    } catch (err) {
      notify(`Could not restart Sunshine: ${errorText(err)}`);
    } finally {
      setState((prev) =>
        prev.sunshineOptions ? { sunshineOptions: { ...prev.sunshineOptions, restarting: false } } : {},
      );
      await refreshSunshineOptions();
      refreshIncomingSoon();
    }
  };
  const clients = getState().incoming?.clients ?? 0;
  if (clients === 0) {
    void run();
    return;
  }
  confirmRestart("The new options apply after a restart.", () => void run());
}

/**
 * Create the first login of the Sunshine on this device with a generated
 * password, so nothing has to be typed, and show it once.
 */
export async function createSunshineLoginAuto(): Promise<boolean> {
  try {
    const res = await backend.sunshineCreateLoginAuto();
    if (!res.ok || !res.user || !res.password) {
      notify(res.error ?? "Could not create the Sunshine login");
      return false;
    }
    await loadInitialState(true);
    showLocalSunshineLogin("Sunshine login created", res.user, res.password);
    return true;
  } catch (err) {
    notify(`Could not create the Sunshine login: ${errorText(err)}`);
    return false;
  }
}

export async function revealSunshineLogin(): Promise<void> {
  try {
    const res = await backend.sunshineRevealLogin();
    if (res.ok && res.user && res.password) showLocalSunshineLogin("Sunshine login", res.user, res.password);
    else notify(res.error ?? "No Sunshine login saved");
  } catch (err) {
    notify(`Could not read the Sunshine login: ${errorText(err)}`);
  }
}

export async function wakeHost(host: Host): Promise<void> {
  const res = await backend.wakeHost(host.uuid);
  notify(res.ok ? `Waking ${host.name}...` : (res.error ?? "Wake-on-LAN failed"));
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
      await launchPending(true);
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

/**
 * Open Moonlight to pair with `address`. The session is tagged so that, when
 * Moonlight closes, the outcome (paired or not) is reported.
 */
export async function launchPairing(pin: string, address: string): Promise<void> {
  try {
    const appId = await launchPending();
    setState({
      session: {
        appId,
        hostUuid: "",
        hostName: address,
        appName: "Pairing",
        startedAt: Date.now(),
        confirmed: false,
        pairAddress: address,
      },
    });
    // Toasts stay visible on top of Moonlight, so the PIN is always at hand.
    notify(`Pairing PIN: ${pin}`, "Eclipse pairing", 30000);
  } catch (err) {
    notify(`Could not launch Moonlight: ${errorText(err)}`);
  }
}

/** Tell the user how pairing went once Moonlight's pairing window closed. */
async function reportPairing(address: string): Promise<void> {
  // Give Moonlight a moment to write its config.
  await new Promise((resolve) => setTimeout(resolve, 1000));
  try {
    const outcome = await backend.pairingOutcome(address);
    if (outcome.paired) {
      notify(`Paired with ${outcome.host ?? address} ✓`, "Eclipse pairing");
      await loadInitialState(true);
      void refreshHosts(true);
    } else {
      notify(`Pairing with ${address} did not complete. Check the PIN and try again.`, "Eclipse pairing");
    }
  } catch (err) {
    console.error("[Eclipse] pairing outcome check failed", err);
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

/**
 * Install the app behind a feature: Moonlight from Flathub, Sunshine as
 * Sunshine for Gaming Mode (asks for the sudo password). Only ever triggered by the user.
 */
export async function installFeature(feature: Feature): Promise<boolean> {
  if (feature === "moonlight") return runAppOperation(feature, "install", backend.installMoonlight);
  const password = await askSudoPassword("Installing Sunshine for Gaming Mode");
  return password !== null && runAppOperation(feature, "install", () => backend.installSunshine(password));
}

/** `native`: Sunshine for Gaming Mode, which needs the sudo password. */
export async function updateApp(feature: Feature, native = false): Promise<boolean> {
  const password = native ? await askSudoPassword("Updating Sunshine for Gaming Mode") : null;
  if (native && password === null) return false;
  return runAppOperation(feature, "update", () => backend.updateApp(feature, password));
}

export async function uninstallApp(feature: Feature, deleteData: boolean, native = false): Promise<boolean> {
  const password = native ? await askSudoPassword("Removing Sunshine for Gaming Mode") : null;
  if (native && password === null) return false;
  return runAppOperation(feature, "uninstall", () => backend.uninstallApp(feature, deleteData, password));
}

/** Reinstall Sunshine for Gaming Mode: restores its capture permission and updates its helpers. */
export async function repairSunshine(): Promise<boolean> {
  const password = await askSudoPassword("Repairing Sunshine for Gaming Mode");
  return password !== null && runAppOperation("sunshine", "install", () => backend.repairSunshine(password));
}

/** Remove a Sunshine Flatpak left next to Sunshine for Gaming Mode. */
export function uninstallSunshineFlatpak(deleteData: boolean): Promise<boolean> {
  return runAppOperation("sunshine", "uninstall", () => backend.uninstallSunshineFlatpak(deleteData));
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
