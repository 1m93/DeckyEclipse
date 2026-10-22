import { addEventListener, definePlugin, removeEventListener, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { FaMoon } from "react-icons/fa";
import { Events } from "./backend";
import { QuickPanel } from "./components/QuickPanel";
import { checkPluginUpdate, notify } from "./lib/actions";
import { ROUTE } from "./lib/navigation";
import { registerLifetimeTracking } from "./lib/steam";
import { flushSettings, loadInitialState, setState, stopAllPolling } from "./lib/store";
import { EclipsePage } from "./pages/EclipsePage";

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
  const onPairResult = addEventListener<[ok: boolean, error: string | null]>(Events.pairResult, (ok, error) =>
    notify(ok ? "PIN accepted by Sunshine, finishing pairing..." : `Automatic PIN entry failed: ${error ?? "unknown error"}`),
  );

  // Lifetime tracking needs the launcher id, so load state eagerly (one cheap call).
  // The daily update check runs afterwards, off the critical path.
  void loadInitialState().then(() => checkPluginUpdate(true));
  const unregisterLifetime = registerLifetimeTracking();

  return {
    name: "Eclipse",
    titleView: <div className={staticClasses.Title}>Eclipse</div>,
    content: <QuickPanel />,
    icon: <FaMoon />,
    onDismount() {
      stopAllPolling();
      void flushSettings();
      unregisterLifetime();
      removeEventListener(Events.installProgress, onInstallProgress);
      removeEventListener(Events.wakeProgress, onWakeProgress);
      removeEventListener(Events.pairResult, onPairResult);
      removeEventListener(Events.streamProgress, onStreamProgress);
      routerHook.removeRoute(ROUTE);
    },
  };
});
