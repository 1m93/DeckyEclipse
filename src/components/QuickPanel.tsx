import { useQuickAccessVisible } from "@decky/api";
import { ButtonItem, DropdownItem, Field, PanelSection, PanelSectionRow, Spinner, ToggleField } from "@decky/ui";
import { useEffect, useMemo, useState } from "react";
import { endStream, openMoonlight, quitHostApp } from "../lib/actions";
import { formatBitrate } from "../lib/format";
import { openEclipsePage } from "../lib/navigation";
import {
  acquirePolling,
  applyPreset,
  loadInitialState,
  refreshDisplay,
  refreshHosts,
  updateStream,
  useStore,
} from "../lib/store";
import { useSunshineStatus } from "../lib/sunshine";
import { showAddHostModal } from "./AddHostModal";
import { FeatureSetup, InstallSection } from "./FeatureSetup";
import { HostRow } from "./HostRow";
import { showSunshinePinModal } from "./SunshinePinModal";

function NowStreaming() {
  const session = useStore((s) => s.session);
  const host = useStore((s) => (session ? s.hosts.find((h) => h.uuid === session.hostUuid) : undefined));
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!session) return;
    const tick = () => setElapsed(Math.floor((Date.now() - session.startedAt) / 1000));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [session]);

  if (!session) return null;
  const minutes = Math.floor(elapsed / 60);
  const title = session.hostName ? `${session.appName} on ${session.hostName}` : session.appName;

  return (
    <PanelSection title="Now streaming">
      <PanelSectionRow>
        <Field
          label={title}
          description={session.confirmed ? `${minutes} min` : "Starting..."}
          bottomSeparator="none"
          padding="compact"
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void endStream()}>
          End stream
        </ButtonItem>
      </PanelSectionRow>
      {host && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void quitHostApp(host)}>
            Quit app on host
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

function HostsSection() {
  const hosts = useStore((s) => s.hosts);
  const status = useStore((s) => s.status);
  const refreshing = useStore((s) => s.refreshing);
  const showOffline = useStore((s) => s.settings?.behavior.showOffline ?? true);

  const visible = useMemo(() => {
    const list = showOffline ? hosts : hosts.filter((h) => status[h.uuid]?.online !== false);
    // Online hosts first, keep Moonlight's order otherwise.
    return [...list].sort((a, b) => Number(!status[a.uuid]?.online) - Number(!status[b.uuid]?.online));
  }, [hosts, status, showOffline]);

  return (
    <PanelSection title="Hosts" spinner={refreshing}>
      {visible.length === 0 && (
        <PanelSectionRow>
          <Field
            description={hosts.length ? "All hosts are offline." : "No paired hosts yet. Add one to get started."}
            bottomSeparator="none"
          />
        </PanelSectionRow>
      )}
      {visible.map((host) => (
        <PanelSectionRow key={host.uuid}>
          <HostRow host={host} />
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={showAddHostModal}>
          Add host
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={refreshing} onClick={() => void refreshHosts(true)}>
          Refresh
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function QuickStreamSettings() {
  const settings = useStore((s) => s.settings);
  const presets = useStore((s) => s.presets);
  const customPreset = useStore((s) => s.customPreset);

  const options = useMemo(
    () => [...presets.map((p) => ({ data: p.id, label: p.label })), { data: customPreset, label: "Custom" }],
    [presets, customPreset],
  );
  if (!settings) return null;
  const { stream } = settings;

  return (
    <PanelSection title="Stream">
      <PanelSectionRow>
        <DropdownItem
          label="Quality"
          description={`${stream.resolution} · ${stream.fps} FPS · ${formatBitrate(stream.bitrateKbps)} · ${stream.videoCodec}`}
          rgOptions={options}
          selectedOption={settings.activePreset}
          onChange={(opt) => {
            if (opt.data === customPreset) openEclipsePage("stream");
            else applyPreset(opt.data);
          }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Performance overlay"
          checked={stream.performanceOverlay}
          onChange={(checked) => updateStream({ performanceOverlay: checked })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="HDR" checked={stream.hdr} onChange={(checked) => updateStream({ hdr: checked })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => openEclipsePage("stream")}>
          All stream settings
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function LocalSunshine({ visible }: { visible: boolean }) {
  const { status, busy, setRunning } = useSunshineStatus(visible);

  if (!status) return null;
  if (!status.installed) return <InstallSection feature="sunshine" />;

  return (
    <PanelSection title="Sunshine on this device">
      <PanelSectionRow>
        <ToggleField
          label="Sunshine server"
          description={
            status.running
              ? "Running · other devices can stream from here"
              : status.autostart
                ? "Stopped · starts automatically with the plugin"
                : "Stopped · start it when you want to share"
          }
          checked={status.running}
          disabled={busy}
          onChange={(checked) => void setRunning(checked)}
        />
      </PanelSectionRow>
      {status.running && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => (status.credentials ? showSunshinePinModal() : openEclipsePage("sunshine"))}
          >
            {status.credentials ? "Enter client PIN" : "Set up credentials"}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export function QuickPanel() {
  const ready = useStore((s) => s.ready);
  const loadError = useStore((s) => s.loadError);
  const configured = useStore((s) => s.settings?.features.configured ?? false);
  const useMoonlight = useStore((s) => s.settings?.features.moonlight ?? false);
  const useSunshine = useStore((s) => s.settings?.features.sunshine ?? false);
  const installed = useStore((s) => s.moonlight?.installed ?? false);
  const visible = useQuickAccessVisible();
  const streaming = useMoonlight && installed;

  useEffect(() => {
    void loadInitialState();
  }, []);

  // Probe hosts and the display only while the panel is actually on screen.
  useEffect(() => {
    if (!visible || !ready || !streaming) return;
    void refreshDisplay();
    return acquirePolling();
  }, [visible, ready, streaming]);

  if (loadError) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Eclipse failed to load" description={loadError} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void loadInitialState(true)}>
            Retry
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (!ready) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
            <Spinner style={{ width: 32, height: 32 }} />
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (!configured) return <FeatureSetup />;

  return (
    <>
      <NowStreaming />
      {useMoonlight && !installed && <InstallSection feature="moonlight" />}
      {streaming && (
        <>
          <HostsSection />
          <QuickStreamSettings />
        </>
      )}
      {useSunshine && <LocalSunshine visible={visible} />}
      <PanelSection>
        {streaming && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void openMoonlight()}>
              Open Moonlight
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => openEclipsePage(streaming ? "hosts" : "general")}>
            Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
