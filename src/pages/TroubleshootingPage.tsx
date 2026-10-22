import { ButtonItem, DialogControlsSection, DialogControlsSectionHeader, Field } from "@decky/ui";
import { HostChecksSection } from "../components/HostChecksSection";
import { LogViewer, type LogSource } from "../components/LogViewer";
import { SunshineConnectSection } from "../components/SunshineConnectSection";
import { loadInitialState, useStore } from "../lib/store";
import { useSunshineStatus } from "../lib/sunshine";

function SharingChecks() {
  const { status, pending } = useSunshineStatus(true);
  if (!status?.installed) return null;
  return <SunshineConnectSection refreshKey={status.running} paused={pending !== null} />;
}

/** Connection checks for both directions, and the logs, in one place. */
export function TroubleshootingPage() {
  const useSunshine = useStore((s) => s.settings?.features.sunshine ?? false);
  const useMoonlight = useStore((s) => s.settings?.features.moonlight ?? false);
  const requested = useStore((s) => (s.pageTarget.page === "help" ? s.pageTarget.log : null));
  const requestedHost = useStore((s) => (s.pageTarget.page === "help" ? s.pageTarget.host : null));
  const requestNonce = useStore((s) => s.pageTarget.nonce);
  const initialLog: LogSource =
    requested === "sunshine" || requested === "launcher" || requested === "plugin"
      ? requested
      : useSunshine && !useMoonlight
        ? "sunshine"
        : "plugin";

  return (
    <>
      {useMoonlight && <HostChecksSection initialHost={requestedHost} nonce={requestNonce} />}
      {useSunshine && <SharingChecks />}

      <DialogControlsSection>
        <DialogControlsSectionHeader>Logs</DialogControlsSectionHeader>
        <Field
          label="Log files"
          description="Plugin: ~/homebrew/logs/Eclipse/ · Launcher: ~/homebrew/settings/Eclipse/launcher.log · Sunshine: ~/.config/sunshine/sunshine.log"
        />
        {/* Remount when another log is asked for while the page is open. */}
        <LogViewer key={initialLog} initialSource={initialLog} />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Plugin</DialogControlsSectionHeader>
        <ButtonItem
          layout="below"
          description="Reads settings, hosts and Moonlight's state again, e.g. after pairing in Desktop Mode."
          onClick={() => void loadInitialState(true)}
        >
          Reload plugin state
        </ButtonItem>
      </DialogControlsSection>
    </>
  );
}
