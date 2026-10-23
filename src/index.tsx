import { addEventListener, definePlugin, removeEventListener, routerHook, toaster } from "@decky/api";
import { Navigation, staticClasses } from "@decky/ui";
import { Events } from "./backend";
import { EclipseIcon } from "./components/icons";
import { QuickPanel } from "./components/QuickPanel";
import { checkPluginUpdate, handleStreamEnded, incomingToastsMuted, notify } from "./lib/actions";
import { ROUTE } from "./lib/navigation";
import { registerLifetimeTracking, setStreamEndedHandler } from "./lib/steam";
import {
  applyIncoming,
  flushSettings,
  getState,
  loadInitialState,
  refreshIncoming,
  setIncomingHandler,
  setQuickTab,
  setState,
  stopAllPolling,
} from "./lib/store";
import { EclipsePage } from "./pages/EclipsePage";
import type { SunshineSession } from "./types";

/** Decky's tab of the Quick Access menu (`EQuickAccessTab.Decky`; the enum only exists as a type). */
const DECKY_QUICK_ACCESS_TAB = 999 as NonNullable<Parameters<typeof Navigation.OpenQuickAccessMenu>[0]>;

/** Toast for a change in the streams from this device, or null when there is nothing to tell. */
function incomingNews(prev: SunshineSession | null, next: SunshineSession): string | null {
  // Unknown before (e.g. the plugin just loaded): no news, only the current state.
  if (!prev) return null;
  const app = next.appName ? ` (${next.appName})` : "";
  if (prev.clients === 0 && next.clients > 0) return `A device started streaming from this one${app}.`;
  if (next.clients > prev.clients) return `Another device joined the stream${app}.`;
  if (prev.clients > 0 && next.clients === 0) return "Streaming from this device ended.";
  return null;
}

export default definePlugin(() => {
  // Not exact: the settings pages are sub-routes such as /eclipse/hosts.
  routerHook.addRoute(ROUTE, EclipsePage);

  const onInstallProgress = addEventListener<[target: string, line: string]>(Events.installProgress, (_target, line) =>
    setState({ installLine: line }),
  );
  const onWakeProgress = addEventListener<[uuid: string, elapsed: number, total: number]>(
    Events.wakeProgress,
    (uuid, elapsed, total) => setState((prev) => ({ waking: { ...prev.waking, [uuid]: [elapsed, total] } })),
  );
  const onStreamProgress = addEventListener<[uuid: string, stage: string]>(Events.streamProgress, (_uuid, stage) =>
    setState({ launchStage: stage }),
  );
  setIncomingHandler((prev, next) => {
    const settings = getState().settings;
    const news = incomingNews(prev, next);
    // The user's own End stream already says so.
    if (!news || incomingToastsMuted() || !settings?.features.sunshine || !settings.sunshine.notifySessions) return;
    toaster.toast({
      title: "Eclipse",
      body: news,
      onClick: () => {
        setQuickTab("share", false);
        Navigation.OpenQuickAccessMenu(DECKY_QUICK_ACCESS_TAB);
      },
    });
  });
  const onIncoming = addEventListener<[session: SunshineSession]>(Events.sunshineSession, applyIncoming);
  const onAutoOff = addEventListener<[message: string]>(Events.sunshineAutoOff, (message) => {
    notify(message);
    void refreshIncoming();
  });
  const onPairResult = addEventListener<[ok: boolean, error: string | null]>(Events.pairResult, (ok, error) =>
    notify(ok ? "PIN accepted by Sunshine, finishing pairing..." : `Automatic PIN entry failed: ${error ?? "unknown error"}`),
  );

  // Lifetime tracking needs the launcher id, so load state eagerly (one cheap call).
  // The daily update check runs afterwards, off the critical path.
  void loadInitialState().then(() => {
    if (getState().settings?.features.sunshine) void refreshIncoming();
    return checkPluginUpdate(true);
  });
  setStreamEndedHandler(handleStreamEnded);
  const unregisterLifetime = registerLifetimeTracking();

  return {
    name: "Eclipse",
    titleView: <div className={staticClasses.Title}>Eclipse</div>,
    content: <QuickPanel />,
    icon: <EclipseIcon />,
    onDismount() {
      stopAllPolling();
      void flushSettings();
      unregisterLifetime();
      removeEventListener(Events.installProgress, onInstallProgress);
      removeEventListener(Events.wakeProgress, onWakeProgress);
      removeEventListener(Events.pairResult, onPairResult);
      removeEventListener(Events.streamProgress, onStreamProgress);
      removeEventListener(Events.sunshineSession, onIncoming);
      setIncomingHandler(null);
      removeEventListener(Events.sunshineAutoOff, onAutoOff);
      routerHook.removeRoute(ROUTE);
    },
  };
});
