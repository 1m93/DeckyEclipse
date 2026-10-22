import { SidebarNavigation } from "@decky/ui";
import { useEffect, type ReactNode } from "react";
import { FaCog, FaDesktop, FaInfoCircle, FaSlidersH, FaSun, FaWrench } from "react-icons/fa";
import { pageRoute, type PageId } from "../lib/navigation";
import { acquirePolling, flushSettings, loadInitialState, refreshDisplay, useStore } from "../lib/store";
import { AboutPage } from "./AboutPage";
import { GeneralPage } from "./GeneralPage";
import { HostsPage } from "./HostsPage";
import { StreamPage } from "./StreamPage";
import { SunshinePage } from "./SunshinePage";
import { TroubleshootingPage } from "./TroubleshootingPage";

/**
 * Full-screen settings, opened from the Quick Access panel.
 *
 * Like Decky's own settings, every page has its own route and Steam's
 * SidebarNavigation switches pages through the router. Driving it with
 * page/onPageRequested props does not work on current Steam builds.
 */
export function EclipsePage() {
  const ready = useStore((s) => s.ready);
  const useMoonlight = useStore((s) => s.settings?.features.moonlight ?? false);
  const useSunshine = useStore((s) => s.settings?.features.sunshine ?? false);

  useEffect(() => {
    void loadInitialState();
    void refreshDisplay();
    const release = acquirePolling();
    return () => {
      release();
      // Persist anything still debounced when leaving the page.
      void flushSettings();
    };
  }, []);

  if (!ready) return null;

  // Play (Moonlight) pages, then Share (Sunshine), in the order of the Quick
  // Access tabs. Pages for features the user did not opt into are hidden.
  const pages: { id: PageId; title: string; icon: ReactNode; content: ReactNode; visible: boolean }[] = [
    { id: "hosts", title: "Hosts", icon: <FaDesktop />, content: <HostsPage />, visible: useMoonlight },
    { id: "stream", title: "Stream quality", icon: <FaSlidersH />, content: <StreamPage />, visible: useMoonlight },
    { id: "sunshine", title: "Sharing", icon: <FaSun />, content: <SunshinePage />, visible: useSunshine },
    { id: "general", title: "General", icon: <FaCog />, content: <GeneralPage />, visible: true },
    { id: "help", title: "Troubleshooting", icon: <FaWrench />, content: <TroubleshootingPage />, visible: true },
    { id: "about", title: "About", icon: <FaInfoCircle />, content: <AboutPage />, visible: true },
  ];

  return (
    <SidebarNavigation
      title="Eclipse"
      showTitle
      pages={pages.map((p) => ({
        title: p.title,
        icon: p.icon,
        content: p.content,
        route: pageRoute(p.id),
        visible: p.visible,
      }))}
    />
  );
}
