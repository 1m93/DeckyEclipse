import { toaster } from "@decky/api";
import { backend } from "../backend";
import type { Host, HostDisplay, MonitorRefresh } from "../types";
import { getState, setState, type MonitorState } from "./store";

const EMPTY: MonitorState = { loading: false, current: null, detectedAt: null, error: null };

export function monitorState(uuid: string): MonitorState {
  return getState().monitors[uuid] ?? EMPTY;
}

function patchMonitor(uuid: string, patch: Partial<MonitorState>): void {
  setState((prev) => ({ monitors: { ...prev.monitors, [uuid]: { ...(prev.monitors[uuid] ?? EMPTY), ...patch } } }));
}

export function hasSunshineLogin(uuid: string): boolean {
  const cfg = getState().settings?.hosts[uuid];
  return Boolean(cfg?.sunshineUser && cfg?.sunshinePasswordSet);
}

export function monitorLabel(displays: HostDisplay[] | undefined, outputName: string | null): string {
  if (outputName === null) return "Unknown";
  if (outputName === "") return "Main monitor (Sunshine default)";
  return displays?.find((d) => d.outputName === outputName)?.label ?? outputName;
}

const inFlight = new Map<string, Promise<MonitorRefresh | null>>();

/**
 * Re-read the host's monitors from Sunshine and sync the saved list. With
 * `rescan`, Sunshine is restarted first so it lists monitors again (use when
 * a monitor was plugged in or out since Sunshine last started).
 */
export function refreshMonitors(host: Host, rescan = false): Promise<MonitorRefresh | null> {
  if (!hasSunshineLogin(host.uuid)) return Promise.resolve(null);
  const key = `${host.uuid}:${rescan}`;
  const pending = inFlight.get(key);
  if (pending) return pending;

  patchMonitor(host.uuid, { loading: true, error: null });
  const job = backend
    .refreshHostMonitors(host.uuid, rescan)
    .then((res) => {
      setState({ settings: res.settings });
      patchMonitor(host.uuid, {
        loading: false,
        current: res.current ?? null,
        detectedAt: res.detectedAt ?? null,
        error: res.ok ? null : res.error,
      });
      if (res.selectionLost) {
        toaster.toast({
          title: "Eclipse",
          body: `The selected monitor is no longer connected to ${host.name}. Choose another one.`,
        });
      }
      return res;
    })
    .catch((err: unknown) => {
      patchMonitor(host.uuid, { loading: false, error: String(err) });
      return null;
    })
    .finally(() => inFlight.delete(key));
  inFlight.set(key, job);
  return job;
}
