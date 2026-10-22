import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "../backend";
import type { SunshineStatus } from "../types";
import { notify, setSunshineAutostart } from "./actions";

/** A change in progress: the switch shows its target and the text says what is happening. */
export type SunshinePending = "starting" | "stopping" | "saving" | null;

/** How long to wait for Sunshine to actually start or stop before reporting a problem. */
const SETTLE_TIMEOUT_MS = 20000;
const SETTLE_POLL_MS = 1000;

/**
 * Local Sunshine status for a component. Fetched when `active` turns true
 * (e.g. the Quick Access menu opens) instead of being polled.
 */
export function useSunshineStatus(active: boolean) {
  const [status, setStatus] = useState<SunshineStatus | null>(null);
  const [pending, setPending] = useState<SunshinePending>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const next = await backend.sunshineStatus();
    if (mounted.current) setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

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
              ? "Sunshine did not start. Check Settings → About → Logs."
              : "Sunshine is still running. Try again, or stop it in Desktop Mode.",
          );
        }
      } finally {
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
  if (status.running) return "Running · other devices can stream from here";
  return status.autostart ? "Stopped · starts automatically with the plugin" : "Stopped · start it when you want to share";
}
