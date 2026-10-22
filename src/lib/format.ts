import type { Host, HostApp, HostConfig, HostStatus } from "../types";

/**
 * "busy" means an app is open on the host. Sunshine reports that whether or
 * not a client is attached, so it is shown as "App open", not as in use.
 * "streaming" is this device's own stream to the host.
 */
export type HostState = "online" | "busy" | "streaming" | "offline" | "unknown";

export function hostState(status: HostStatus | undefined, streamingHere = false): HostState {
  if (streamingHere) return "streaming";
  if (!status) return "unknown";
  if (!status.online) return "offline";
  return status.busy ? "busy" : "online";
}

export const STATE_COLORS: Record<HostState, string> = {
  online: "#5ba32b",
  busy: "#e5a50a",
  streaming: "#1a9fff",
  offline: "#8b929a",
  unknown: "#3d4450",
};

/** Name of the app open on the host, when Moonlight knows its id. */
export function openAppName(host: Host, status: HostStatus | undefined): string | null {
  const id = status?.currentGame;
  if (!id) return null;
  return host.apps.find((app) => app.id === id)?.name ?? null;
}

export function describeHost(host: Host, status: HostStatus | undefined, canWake: boolean, streamingHere = false): string {
  switch (hostState(status, streamingHere)) {
    case "streaming":
      return "Streaming to this device";
    case "online":
      return [`Online`, status?.address, status?.latencyMs != null ? `${status.latencyMs} ms` : null]
        .filter(Boolean)
        .join(" · ");
    case "busy": {
      const name = openAppName(host, status);
      return `${name ? `"${name}"` : "An app"} is open · stream it to resume`;
    }
    case "offline":
      return canWake ? "Offline · press X to wake" : "Offline";
    default:
      return host.addresses[0]?.address ?? "Checking...";
  }
}

export function formatBitrate(kbps: number): string {
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(kbps % 1000 === 0 ? 0 : 1)} Mbps` : `${kbps} Kbps`;
}

/**
 * The app open on the host first (resume), then last played, then favorites,
 * then Moonlight's order. Hidden apps are skipped.
 */
export function sortApps(apps: HostApp[], cfg: HostConfig, openApp: string | null = null): HostApp[] {
  const favorites = new Set(cfg.favorites ?? []);
  const rank = (app: HostApp) =>
    app.name === openApp ? 0 : app.name === cfg.lastApp ? 1 : favorites.has(app.name) ? 2 : 3;
  return apps
    .filter((app) => !app.hidden)
    .map((app, index) => ({ app, index }))
    .sort((a, b) => rank(a.app) - rank(b.app) || a.index - b.index)
    .map(({ app }) => app);
}

/** Sunshine ships these by default; used when Moonlight has not cached an app list yet. */
export const FALLBACK_APPS: HostApp[] = [
  { id: "", name: "Desktop", hdr: false, hidden: false },
  { id: "", name: "Steam Big Picture", hdr: false, hidden: false },
];
