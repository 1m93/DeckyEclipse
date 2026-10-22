import type { Host, HostApp, HostConfig, HostStatus } from "../types";

export type HostState = "online" | "busy" | "offline" | "unknown";

export function hostState(status: HostStatus | undefined): HostState {
  if (!status) return "unknown";
  if (!status.online) return "offline";
  return status.busy ? "busy" : "online";
}

export const STATE_COLORS: Record<HostState, string> = {
  online: "#5ba32b",
  busy: "#e5a50a",
  offline: "#8b929a",
  unknown: "#3d4450",
};

export function describeHost(host: Host, status: HostStatus | undefined, canWake: boolean): string {
  switch (hostState(status)) {
    case "online":
      return [`Online`, status?.address, status?.latencyMs != null ? `${status.latencyMs} ms` : null]
        .filter(Boolean)
        .join(" · ");
    case "busy":
      return "Busy · a stream or app is running";
    case "offline":
      return canWake ? "Offline · press X to wake" : "Offline";
    default:
      return host.addresses[0]?.address ?? "Checking...";
  }
}

export function formatBitrate(kbps: number): string {
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(kbps % 1000 === 0 ? 0 : 1)} Mbps` : `${kbps} Kbps`;
}

/** Last played first, then favorites, then Moonlight's order. Hidden apps are skipped. */
export function sortApps(apps: HostApp[], cfg: HostConfig): HostApp[] {
  const favorites = new Set(cfg.favorites ?? []);
  const rank = (app: HostApp) => (app.name === cfg.lastApp ? 0 : favorites.has(app.name) ? 1 : 2);
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
