import { Navigation } from "@decky/ui";
import { getState, setState } from "./store";

/** Base route; every settings page lives below it (e.g. /eclipse/hosts). */
export const ROUTE = "/eclipse";

export type PageId = "hosts" | "stream" | "sunshine" | "general" | "about";

export function pageRoute(page: PageId): string {
  return `${ROUTE}/${page}`;
}

/** Open the full-screen settings on a page, optionally focusing a host on the Hosts page. */
export function openEclipsePage(page: PageId = "hosts", host: string | null = null): void {
  const features = getState().settings?.features;
  // Pages of disabled features are hidden, fall back to General.
  const hidden =
    ((page === "hosts" || page === "stream") && !features?.moonlight) || (page === "sunshine" && !features?.sunshine);
  setState({ pageTarget: { page, host } });
  Navigation.CloseSideMenus();
  Navigation.Navigate(pageRoute(hidden ? "general" : page));
}
