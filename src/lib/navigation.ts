import { Navigation } from "@decky/ui";
import type { LogSource } from "../components/LogViewer";
import { getState, setState } from "./store";

/** Base route; every settings page lives below it (e.g. /eclipse/streaming). */
export const ROUTE = "/eclipse";

export type PageId = "streaming" | "sunshine" | "general" | "help" | "about";

export function pageRoute(page: PageId): string {
  return `${ROUTE}/${page}`;
}

/**
 * Open the full-screen settings on a page, optionally showing a host on the
 * Streaming page, or a log on the Troubleshooting page.
 */
export function openEclipsePage(page: PageId = "streaming", host: string | null = null, log: LogSource | null = null): void {
  const features = getState().settings?.features;
  // Pages of disabled features are hidden, fall back to General.
  const hidden =
    (page === "streaming" && !features?.moonlight) || (page === "sunshine" && !features?.sunshine);
  setState((prev) => ({ pageTarget: { page, host, log, nonce: prev.pageTarget.nonce + 1 } }));
  Navigation.CloseSideMenus();
  Navigation.Navigate(pageRoute(hidden ? "general" : page));
}
