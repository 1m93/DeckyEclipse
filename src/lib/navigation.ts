import { Navigation } from "@decky/ui";
import type { LogSource } from "../components/LogViewer";
import { getState, setState } from "./store";

/** Base route; every settings page lives below it (e.g. /eclipse/hosts). */
export const ROUTE = "/eclipse";

export type PageId = "hosts" | "stream" | "sunshine" | "general" | "help" | "about";

export function pageRoute(page: PageId): string {
  return `${ROUTE}/${page}`;
}

/**
 * Open the full-screen settings on a page, optionally focusing a host on the
 * Hosts page, or a log on the Troubleshooting page.
 */
export function openEclipsePage(page: PageId = "hosts", host: string | null = null, log: LogSource | null = null): void {
  const features = getState().settings?.features;
  // Pages of disabled features are hidden, fall back to General.
  const hidden =
    ((page === "hosts" || page === "stream") && !features?.moonlight) || (page === "sunshine" && !features?.sunshine);
  setState((prev) => ({ pageTarget: { page, host, log, nonce: prev.pageTarget.nonce + 1 } }));
  Navigation.CloseSideMenus();
  Navigation.Navigate(pageRoute(hidden ? "general" : page));
}
