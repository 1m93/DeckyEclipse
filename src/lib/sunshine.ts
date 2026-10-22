import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import type { Result, SunshineStatus } from "../types";
import { notify, setSunshineAutostart } from "./actions";

/**
 * Local Sunshine status for a component. Fetched when `active` turns true
 * (e.g. the Quick Access menu opens) instead of being polled.
 */
export function useSunshineStatus(active: boolean) {
  const [status, setStatus] = useState<SunshineStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => setStatus(await backend.sunshineStatus()), []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  const run = useCallback(
    async (action: () => Promise<Result | boolean>) => {
      setBusy(true);
      try {
        const res = await action();
        if (typeof res === "object" && !res.ok) notify(res.error ?? "Sunshine command failed");
        // Give the service a moment to settle before reading its state back.
        await new Promise((resolve) => setTimeout(resolve, 1200));
        await refresh();
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  return {
    status,
    busy,
    refresh,
    setRunning: (running: boolean) => run(running ? backend.sunshineStart : backend.sunshineStop),
    setAutostart: (enabled: boolean) => run(() => setSunshineAutostart(enabled)),
  };
}
