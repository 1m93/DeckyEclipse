import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "../backend";
import type { StreamingDevice, SunshineAutoOff, SunshineBatteryGuard, SunshineStatus } from "../types";
import { notify, setSunshineAutostart } from "./actions";
import { refreshIncoming, useStore } from "./store";

/** A change in progress: the switch shows its target and the text says what is happening. */
export type SunshinePending = "starting" | "stopping" | "saving" | null;

/** How long to wait for Sunshine to actually start or stop before reporting a problem. */
const SETTLE_TIMEOUT_MS = 20000;
const SETTLE_POLL_MS = 1000;

/** Last known status: shown right away when a view opens, then refreshed. */
let lastStatus: SunshineStatus | null = null;

/**
 * Local Sunshine status for a component. Fetched when `active` turns true
 * (e.g. the Quick Access menu opens) instead of being polled.
 */
export function useSunshineStatus(active: boolean) {
  const [status, setStatus] = useState<SunshineStatus | null>(lastStatus);
  const [pending, setPendingState] = useState<SunshinePending>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const next = await backend.sunshineStatus();
    lastStatus = next;
    if (mounted.current) setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  // Install, update, repair and uninstall change what is installed: re-read once they finish,
  // wherever they were started from (Quick Access menu or settings).
  const installing = useStore((s) => s.installing);
  const wasInstalling = useRef(installing);
  useEffect(() => {
    const finished = wasInstalling.current !== null && installing === null;
    wasInstalling.current = installing;
    if (finished) void refresh();
  }, [installing, refresh]);

  // Sunshine can also start or stop on its own (auto-off, a crash, the web UI):
  // the session watcher notices first, then the status follows.
  const reachable = useStore((s) => s.incoming?.reachable ?? null);
  const lastReachable = useRef(reachable);
  const pendingRef = useRef<SunshinePending>(null);
  useEffect(() => {
    const changed = reachable !== lastReachable.current;
    lastReachable.current = reachable;
    // A change the user started is followed by setRunning itself.
    if (active && changed && reachable !== null && pendingRef.current === null) void refresh();
  }, [active, reachable, refresh]);

  const setPending = useCallback((value: SunshinePending) => {
    pendingRef.current = value;
    setPendingState(value);
  }, []);

  /**
   * Start or stop Sunshine, then wait until it really is running/stopped.
   * Services can take several seconds; a single fixed delay made the switch
   * jump back while Sunshine was still starting.
   */
  const setRunning = useCallback(
    async (running: boolean) => {
      setPending(running ? "starting" : "stopping");
      try {
        const res = running ? await backend.sunshineStart() : await backend.sunshineStop();
        if (!res.ok) {
          notify(res.error ?? `Could not ${running ? "start" : "stop"} Sunshine`);
          return;
        }
        const deadline = Date.now() + SETTLE_TIMEOUT_MS;
        while (Date.now() < deadline && mounted.current) {
          await new Promise((resolve) => setTimeout(resolve, SETTLE_POLL_MS));
          const next = await refresh();
          if (next.running === running) return;
        }
        if (mounted.current) {
          notify(
            running
              ? "Sunshine did not start. Check Settings → Troubleshooting → Logs."
              : "Sunshine is still running. Try again, or stop it in Desktop Mode.",
          );
        }
      } finally {
        // The session state follows on its own within seconds; make it immediate.
        void refreshIncoming();
        if (mounted.current) {
          setPending(null);
          void refresh();
        }
      }
    },
    [refresh],
  );

  const setAutostart = useCallback(
    async (enabled: boolean) => {
      setPending("saving");
      try {
        await setSunshineAutostart(enabled);
        await refresh();
      } finally {
        if (mounted.current) setPending(null);
      }
    },
    [refresh],
  );

  // While a change is in progress, show where it is heading.
  const shown: SunshineStatus | null =
    status && pending === "starting"
      ? { ...status, running: true }
      : status && pending === "stopping"
        ? { ...status, running: false }
        : status;

  return { status: shown, pending, busy: pending !== null, refresh, setRunning, setAutostart };
}

/** One-line description of the server state, including changes in progress. */
export function describeSunshine(status: SunshineStatus, pending: SunshinePending): string {
  if (pending === "starting") return "Starting Sunshine...";
  if (pending === "stopping") return "Stopping Sunshine...";
  if (status.running) return "On · other devices can stream from here";
  return status.autostart ? "Off · starts automatically with the device" : "Off · stays off until you turn it on";
}

/** Why Sunshine for Gaming Mode needs a repair, or null. */
export function repairReason(status: SunshineStatus): string | null {
  if (status.kind !== "eclipse" || !status.native) return null;
  if (status.native.legacy) {
    return "This copy of Sunshine keeps its data in the wrong folder, which breaks pairing (Error 401 in Moonlight). Repair moves it and keeps your login and devices.";
  }
  if (!status.native.captureReady) return "Sunshine is not allowed to capture the screen, so streams fail.";
  if (status.native.helpersOutdated) return "This Eclipse version brings a newer Gaming Mode fix for Sunshine.";
  return null;
}

export const AUTO_OFF_OPTIONS: { data: SunshineAutoOff; label: string }[] = [
  { data: "never", label: "Never" },
  { data: "afterStream", label: "After the stream" },
  { data: "15", label: "15 min idle" },
  { data: "30", label: "30 min idle" },
  { data: "60", label: "1 h idle" },
];

export const BATTERY_GUARD_OPTIONS: { data: SunshineBatteryGuard; label: string }[] = [
  { data: "off", label: "Never" },
  { data: "10", label: "At 10 %" },
  { data: "20", label: "At 20 %" },
  { data: "30", label: "At 30 %" },
];

/**
 * Who streams, as far as the network shows: "From Phone (192.168.1.5)". The
 * guess is hedged when it lists more devices than are streaming. Null when unknown.
 */
export function describeDevices(devices: StreamingDevice[] | undefined, clients = devices?.length ?? 0): string | null {
  if (!devices?.length || clients <= 0) return null;
  const names = devices.map((d) => (d.name ? `${d.name} (${d.address})` : `a device at ${d.address}`));
  const list = names.join(", ");
  return devices.length > clients ? `Probably from ${list}` : `From ${list}`;
}
