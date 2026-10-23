import { useQuickAccessVisible } from "@decky/api";
import {
  ButtonItem,
  DialogButton,
  DropdownItem,
  Field,
  Focusable,
  GamepadButton,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
  type GamepadEvent,
} from "@decky/ui";
import { forwardRef, useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from "react";
import { backend } from "../backend";
import {
  confirmStopSunshine,
  createSunshineLoginAuto,
  endIncomingStream,
  endStream,
  requestEndStream,
  installFeature,
  openMoonlight,
  quitHostApp,
  refreshSunshineOptions,
  repairSunshine,
  setSunshineOptions,
} from "../lib/actions";
import { ACCENT, formatBitrate, formatDuration } from "../lib/format";
import { openEclipsePage } from "../lib/navigation";
import {
  acquirePolling,
  applyPreset,
  getState,
  updateSettings,
  loadInitialState,
  refreshDisplay,
  refreshHosts,
  refreshIncoming,
  refreshSharing,
  setQuickTab,
  streamChoices,
  updateStream,
  useStore,
  type QuickTab,
} from "../lib/store";
import {
  AUTO_OFF_OPTIONS,
  describeDevices,
  describeSunshine,
  pairingLabel,
  repairReason,
  useSunshineStatus,
} from "../lib/sunshine";
import type { PendingPairing, SunshineStatus } from "../types";
import { showAddHostModal } from "./AddHostModal";
import { ButtonRow } from "./ButtonRow";
import { FeatureSetup, InstallSection } from "./FeatureSetup";
import { HostRow } from "./HostRow";
import { PlayIcon, ShareIcon } from "./icons";
import { showNetworkTest } from "./NetworkTestModal";
import { InstallProgress } from "./InstallProgress";
import { ApplyOptionsRow, MAX_BITRATE_OPTIONS, maxBitrateLabel, withCurrent } from "./SunshineOptionsSections";
import { showSunshinePinModal } from "./SunshinePinModal";

/** How often devices waiting to pair are looked up while the menu is open. */
const PENDING_POLL_MS = 3000;

const ellipsis = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

/** Milliseconds since `since`, ticking every few seconds while mounted. */
function useElapsed(since: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [since]);
  return since === null ? 0 : now - since;
}

/** Text block of a live activity, with the accent of its direction on the left. */
function LiveText({ accent, title, detail }: { accent: string; title: ReactNode; detail?: ReactNode }) {
  return (
    <div
      style={{
        borderLeft: `3px solid ${accent}`,
        paddingLeft: 10,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minWidth: 0,
      }}
    >
      <span style={{ ...ellipsis, fontWeight: 600 }}>{title}</span>
      {detail && <span style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.35 }}>{detail}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- live activity

/** This device streaming from a PC (or Moonlight pairing), and a stream being prepared. */
function OutgoingLive() {
  const session = useStore((s) => s.session);
  const launching = useStore((s) => (s.launching ? s.hosts.find((h) => h.uuid === s.launching) : undefined));
  const launchStage = useStore((s) => s.launchStage);
  const waking = useStore((s) => (s.launching ? s.waking[s.launching] : undefined));
  const quitAfter = useStore((s) => s.settings?.stream.quitAppAfter ?? true);
  const host = useStore((s) => (session ? s.hosts.find((h) => h.uuid === session.hostUuid) : undefined));
  const elapsed = useElapsed(session?.confirmed ? session.startedAt : null);

  if (!session) {
    if (!launching) return null;
    return (
      <PanelSection title="Starting stream">
        <PanelSectionRow>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }}>
            <Spinner style={{ width: 20, height: 20, flexShrink: 0 }} />
            <LiveText
              accent={ACCENT.play}
              title={launching.name}
              detail={waking ? `Waking the host... ${waking[0]} of ${waking[1]} s` : (launchStage ?? "Preparing...")}
            />
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (session.pairAddress) {
    return (
      <PanelSection title="Pairing">
        <PanelSectionRow>
          <div style={{ padding: "6px 0" }}>
            <LiveText
              accent={ACCENT.play}
              title={`Pairing with ${session.hostName}`}
              detail="Enter the PIN on the host. Moonlight closes when pairing is done."
            />
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void endStream()}>
            Cancel pairing
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const quitsHostApp = Boolean(session.hostUuid) && quitAfter;
  return (
    <PanelSection title="Playing">
      <PanelSectionRow>
        <div style={{ padding: "6px 0" }}>
          <LiveText
            accent={ACCENT.play}
            title={session.appName}
            detail={
              <>
                {session.hostName ? `on ${session.hostName} · ` : ""}
                {session.confirmed ? formatDuration(elapsed) : "starting..."}
              </>
            }
          />
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description={quitsHostApp ? "Also closes the game on the host." : "The game stays open on the host."}
          onClick={requestEndStream}
        >
          End stream
        </ButtonItem>
      </PanelSectionRow>
      {host && !quitsHostApp && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void quitHostApp(host)}>
            Quit app on host
          </ButtonItem>
        </PanelSectionRow>
      )}
      {host && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Stuttering or lagging? Check the network."
            onClick={() => showNetworkTest(host)}
          >
            Network test
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

/** Other devices streaming from this one. */
function IncomingLive() {
  const incoming = useStore((s) => s.incoming);
  const ending = useStore((s) => s.endingIncoming);
  const elapsed = useElapsed(incoming?.since ?? null);
  if (!incoming || incoming.clients === 0) return null;

  const devices = incoming.clients === 1 ? "A device is" : `${incoming.clients} devices are`;
  const detail = [describeDevices(incoming.devices, incoming.clients), incoming.appName, incoming.since ? formatDuration(elapsed) : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <PanelSection title="Sharing">
      <PanelSectionRow>
        <div style={{ padding: "6px 0" }}>
          <LiveText accent={ACCENT.share} title={`${devices} streaming from here`} detail={detail || undefined} />
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={ending} onClick={() => void endIncomingStream()}>
          {ending ? "Ending..." : "End stream"}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

/** Devices asking to pair with this device's Sunshine (newer Sunshine lists them). */
function PairingRequests({ visible }: { visible: boolean }) {
  const reachable = useStore((s) => s.incoming?.reachable ?? false);
  const hasLogin = useStore((s) => Boolean(s.settings?.sunshine.user && s.settings.sunshine.passwordSet));
  const [requests, setRequests] = useState<PendingPairing[]>([]);
  const active = visible && reachable && hasLogin;

  useEffect(() => {
    if (!active) {
      setRequests([]);
      return;
    }
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const res = await backend.sunshinePendingPairings();
        if (!alive) return;
        setRequests(res.supported ? res.items : []);
        // Older Sunshine cannot list waiting devices: no point in asking again.
        if (!res.supported) return;
      } catch {
        if (!alive) return;
        setRequests([]);
      }
      timer = window.setTimeout(() => void poll(), PENDING_POLL_MS);
    };
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [active]);

  if (requests.length === 0) return null;
  return (
    <PanelSection title="Pairing request">
      {requests.map((request) => (
        <PanelSectionRow key={request.id}>
          <ButtonItem
            layout="below"
            label={`${pairingLabel(request)} wants to pair`}
            description={request.address || undefined}
            onClick={() =>
              showSunshinePinModal(request, () => {
                setRequests((prev) => prev.filter((r) => r.id !== request.id));
                void refreshSharing();
              })
            }
          >
            Enter PIN
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}

// ---------------------------------------------------------------- tabs

/** Height kept clear above a focused control, so the pinned tabs never cover it. */
const TABS_HEIGHT = 52;

const TabButton = forwardRef<HTMLDivElement, { tab: QuickTab; label: string; icon: ReactNode; badge?: string; hint: string }>(
  function TabButton({ tab, label, icon, badge, hint }, ref) {
    const active = useStore((s) => s.quickTab === tab);
    const side = tab === "play" ? "left" : "right";
    const hintLabel = <span style={{ fontSize: 10, fontWeight: 700, opacity: 0.6, flexShrink: 0 }}>{hint}</span>;
    return (
      <DialogButton
        ref={ref}
        style={{
          flex: 1,
          minWidth: 0,
          padding: "6px 8px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
          // An underline marks the selected tab; the background stays free for the focus highlight.
          boxShadow: active ? `inset 0 -3px 0 ${ACCENT[tab]}` : "none",
          opacity: active ? 1 : 0.65,
        }}
        onClick={() => setQuickTab(tab)}
      >
        {/* The bumper hint sits on the side of its bumper: LB left of Play, RB right of Share. */}
        {side === "left" && hintLabel}
        {icon}
        <span style={ellipsis}>{label}</span>
        {badge && (
          <span
            style={{ width: 8, height: 8, borderRadius: "50%", background: badge, flexShrink: 0 }}
            aria-hidden="true"
          />
        )}
        {side === "right" && hintLabel}
      </DialogButton>
    );
  },
);

/**
 * Play (stream from a PC) and Share (let others stream from here), pinned to
 * the top of the panel while it scrolls. LB/RB switch tabs from anywhere in
 * the panel (see QuickPanel).
 */
function QuickTabs({ playRef, shareRef }: { playRef: Ref<HTMLDivElement>; shareRef: Ref<HTMLDivElement> }) {
  const playing = useStore((s) => s.session !== null || s.launching !== null);
  const sharing = useStore((s) => (s.incoming?.clients ? "live" : s.incoming?.reachable ? "on" : "off"));
  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        // Opaque, so content scrolling underneath does not show through.
        background: "#0e141b",
        boxShadow: "0 4px 8px rgba(0, 0, 0, 0.35)",
      }}
    >
      <PanelSection>
        <PanelSectionRow>
          <Focusable flow-children="row" style={{ display: "flex", gap: 6 }}>
            <TabButton
              ref={playRef}
              tab="play"
              label="Play"
              hint="LB"
              icon={<PlayIcon />}
              badge={playing ? ACCENT.play : undefined}
            />
            <TabButton
              ref={shareRef}
              tab="share"
              label="Share"
              hint="RB"
              icon={<ShareIcon />}
              badge={sharing === "live" ? ACCENT.share : sharing === "on" ? "#5ba32b" : undefined}
            />
          </Focusable>
        </PanelSectionRow>
      </PanelSection>
    </div>
  );
}

// ---------------------------------------------------------------- Play tab

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
    <PanelSection title="Stream from a PC" spinner={refreshing}>
      {visible.length === 0 && (
        <PanelSectionRow>
          <Field
            label={hosts.length ? "All hosts are offline" : "No hosts yet"}
            description={
              hosts.length
                ? "Offline hosts are hidden. Turn on Show offline hosts in Settings → General to wake one."
                : "A host is a PC running Sunshine (free, from app.lizardbyte.dev). Install it there, then add the PC here."
            }
            bottomSeparator="none"
          />
        </PanelSectionRow>
      )}
      {visible.map((host) => (
        <PanelSectionRow key={host.uuid}>
          <HostRow host={host} />
        </PanelSectionRow>
      ))}
      {visible.length > 0 && (
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.55, padding: "4px 0 0" }}>A: choose a game · X: wake · Y: more</div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonRow
          buttons={[
            { label: "Add host", onClick: showAddHostModal },
            { label: refreshing ? "Refreshing..." : "Refresh", disabled: refreshing, onClick: () => void refreshHosts(true) },
          ]}
        />
      </PanelSectionRow>
    </PanelSection>
  );
}

function QuickStreamSettings() {
  const settings = useStore((s) => s.settings);
  const presets = useStore((s) => s.presets);
  const profiles = useStore((s) => s.settings?.profiles);
  const customPreset = useStore((s) => s.customPreset);
  const streaming = useStore((s) => s.session !== null && !s.session.pairAddress);

  const options = useMemo(
    () => streamChoices(presets, profiles, customPreset),
    [presets, profiles, customPreset],
  );
  if (!settings) return null;
  const { stream } = settings;
  const summary = `${stream.resolution} · ${stream.fps} FPS · ${formatBitrate(stream.bitrateKbps)} · ${stream.videoCodec}`;

  return (
    <PanelSection title="Stream quality">
      <PanelSectionRow>
        <DropdownItem
          label="Profile"
          description={streaming ? `${summary}. Changes apply to the next stream.` : summary}
          rgOptions={options}
          selectedOption={settings.activePreset}
          onChange={(opt) => {
            if (opt.data === customPreset && settings.activePreset !== customPreset) openEclipsePage("streaming");
            else applyPreset(opt.data);
          }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="HDR" checked={stream.hdr} onChange={(checked) => updateStream({ hdr: checked })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Performance overlay"
          checked={stream.performanceOverlay}
          onChange={(checked) => updateStream({ performanceOverlay: checked })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => openEclipsePage("streaming")}>
          All streaming settings
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function PlayTab({ visible }: { visible: boolean }) {
  const ready = useStore((s) => s.ready);
  const installed = useStore((s) => s.moonlight?.installed ?? false);

  // Probe hosts and the display only while the list is actually on screen.
  useEffect(() => {
    if (!visible || !ready || !installed) return;
    void refreshDisplay();
    return acquirePolling();
  }, [visible, ready, installed]);

  if (!installed) return <InstallSection feature="moonlight" />;
  return (
    <>
      <HostsSection />
      <QuickStreamSettings />
    </>
  );
}

// ---------------------------------------------------------------- Share tab

/** Install or repair problems that stop sharing from working, with the action that fixes them. */
function ShareProblems({ status, onFixed }: { status: SunshineStatus; onFixed: () => void }) {
  const installing = useStore((s) => s.installing);
  if (installing === "sunshine") {
    return (
      <PanelSectionRow>
        <InstallProgress />
      </PanelSectionRow>
    );
  }
  if (status.kind === "flatpak") {
    return (
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label="The Flatpak cannot share Gaming Mode"
          description="Other devices get an error or a black picture. Install Sunshine for Gaming Mode (asks for your password once)."
          disabled={installing !== null}
          onClick={async () => (await installFeature("sunshine")) && onFixed()}
        >
          Install Sunshine for Gaming Mode
        </ButtonItem>
      </PanelSectionRow>
    );
  }
  const repair = repairReason(status);
  if (!repair) return null;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label="Repair needed"
        description={`${repair} Asks for your password.`}
        disabled={installing !== null}
        onClick={async () => (await repairSunshine()) && onFixed()}
      >
        Repair
      </ButtonItem>
    </PanelSectionRow>
  );
}

/** The Sunshine options most often changed, the others are in Settings → Sharing. */
function QuickShareOptions({ visible, running }: { visible: boolean; running: boolean }) {
  const options = useStore((s) => s.sunshineOptions);

  useEffect(() => {
    if (visible) void refreshSunshineOptions();
  }, [visible, running]);

  if (!options) return null;
  const { values } = options;
  return (
    <PanelSection title="Sharing options">
      {(options.restarting || (running && options.needsRestart)) && (
        <PanelSectionRow>
          <ApplyOptionsRow running={running} />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <DropdownItem
          label="Maximum bitrate"
          rgOptions={withCurrent(MAX_BITRATE_OPTIONS, values.max_bitrate, maxBitrateLabel)}
          selectedOption={values.max_bitrate}
          onChange={(opt) => void setSunshineOptions({ max_bitrate: opt.data })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Stream audio"
          checked={values.stream_audio}
          onChange={(checked) => void setSunshineOptions({ stream_audio: checked })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Controllers"
          checked={values.controller}
          onChange={(checked) => void setSunshineOptions({ controller: checked })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Keyboard and mouse"
          checked={values.keyboard && values.mouse}
          onChange={(checked) => void setSunshineOptions({ keyboard: checked, mouse: checked })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description="Steam games other devices can start directly from Moonlight."
          onClick={() => openEclipsePage("sunshine")}
        >
          Choose games...
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => openEclipsePage("sunshine")}>
          All sharing settings
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function ShareTab({ visible }: { visible: boolean }) {
  const { status, pending, busy, refresh, setRunning } = useSunshineStatus(visible);
  const sharing = useStore((s) => s.sharing);
  const incoming = useStore((s) => s.incoming);
  const ending = useStore((s) => s.endingIncoming);
  const autoOff = useStore((s) => s.settings?.sunshine.autoOff ?? "never");
  const running = Boolean(status?.running) && pending === null;
  const hasLogin = Boolean(status?.credentials);
  // Whether Sunshine has a login yet (null: not known yet). Without one, it is created with one press.
  const [needsLogin, setNeedsLogin] = useState<boolean | null>(null);
  const [creatingLogin, setCreatingLogin] = useState(false);

  useEffect(() => {
    setNeedsLogin(null);
    if (!visible || !running || hasLogin) return;
    let alive = true;
    let timer: number | undefined;
    const check = async (attempt: number) => {
      try {
        const res = await backend.sunshineLoginState();
        if (!alive) return;
        // Just started: the web UI may not listen yet. Ask again shortly.
        if (res.state === "unreachable" && attempt < 5) {
          timer = window.setTimeout(() => void check(attempt + 1), 2000);
          return;
        }
        setNeedsLogin(res.state === "needs_setup");
      } catch {
        if (alive) setNeedsLogin(false);
      }
    };
    void check(0);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [visible, running, hasLogin]);

  // Address and paired devices, once Sunshine is up (and again when the login changes).
  useEffect(() => {
    if (visible && running) void refreshSharing();
  }, [visible, running, hasLogin]);

  if (!status) {
    return (
      <PanelSection title="Share this device">
        <PanelSectionRow>
          <div style={{ display: "flex", justifyContent: "center", padding: 12 }}>
            <Spinner style={{ width: 24, height: 24 }} />
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }
  if (!status.installed) return <InstallSection feature="sunshine" />;

  const lingering = running && Boolean(incoming?.appOpen) && incoming?.clients === 0;
  return (
    <>
      <PanelSection title="Share this device">
        <ShareProblems status={status} onFixed={() => void refresh()} />
        <PanelSectionRow>
          <ToggleField
            label="Sunshine"
            description={describeSunshine(status, pending)}
            checked={status.running}
            disabled={busy || ending}
            onChange={(checked) =>
              checked ? void setRunning(true) : confirmStopSunshine(() => void setRunning(false))
            }
          />
        </PanelSectionRow>
        {status.running && (
          <PanelSectionRow>
            <DropdownItem
              label="Turn off automatically"
              rgOptions={AUTO_OFF_OPTIONS}
              selectedOption={autoOff}
              onChange={(opt) => updateSettings({ sunshine: { autoOff: opt.data } }, 0)}
            />
          </PanelSectionRow>
        )}
        {lingering && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={`${incoming?.appName ?? "An app"} is still open`}
              description="No device is connected. A device can resume it, or close it to end the session."
              disabled={ending}
              onClick={() => void endIncomingStream()}
            >
              {ending ? "Closing..." : "Close app"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {running && !hasLogin && (
          <PanelSectionRow>
            {needsLogin === null ? (
              <Field label="Finish setup" description="Checking Sunshine's login..." bottomSeparator="none" />
            ) : needsLogin ? (
              <ButtonItem
                layout="below"
                label="Finish setup"
                description="Eclipse creates a Sunshine login for you, used to pair devices and end streams from here."
                disabled={creatingLogin}
                onClick={async () => {
                  setCreatingLogin(true);
                  try {
                    if (await createSunshineLoginAuto()) await refresh();
                  } finally {
                    setCreatingLogin(false);
                  }
                }}
              >
                {creatingLogin ? "Creating..." : "Set up automatically"}
              </ButtonItem>
            ) : (
              <ButtonItem
                layout="below"
                label="Sunshine login needed"
                description="Sunshine already has a login. Enter it once so Eclipse can pair devices and end streams."
                onClick={() => openEclipsePage("sunshine")}
              >
                Enter login
              </ButtonItem>
            )}
          </PanelSectionRow>
        )}
        {running && (
          <PanelSectionRow>
            <Field
              label={
                !sharing
                  ? "Looking up this device's address..."
                  : sharing.address
                    ? `This device: ${sharing.address}`
                    : "No network address"
              }
              description={
                sharing && !sharing.address
                  ? "Connect to the same Wi-Fi or LAN as the other device."
                  : `The other device needs Moonlight. On the same network it finds this device on its own; if not, add this address in Moonlight.${
                      sharing?.remoteAddress ? ` Away from home (Tailscale): ${sharing.remoteAddress}.` : ""
                    }`
              }
              bottomSeparator="none"
            />
          </PanelSectionRow>
        )}
        {running && hasLogin && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description={
                sharing?.paired == null
                  ? undefined
                  : sharing.paired === 0
                    ? "No paired devices yet."
                    : `${sharing.paired} paired ${sharing.paired === 1 ? "device" : "devices"}.`
              }
              onClick={() => showSunshinePinModal(undefined, () => void refreshSharing())}
            >
              Pair a device
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
      {!status.native?.legacy && <QuickShareOptions visible={visible} running={running} />}
    </>
  );
}

// ---------------------------------------------------------------- panel

function Footer({ tab }: { tab: QuickTab }) {
  const canOpenMoonlight = useStore(
    (s) => Boolean(s.settings?.features.moonlight) && (s.moonlight?.installed ?? false),
  );
  const settings = { label: "Settings", onClick: () => openEclipsePage(tab === "play" ? "streaming" : "sunshine") };
  return (
    <PanelSection>
      <PanelSectionRow>
        <ButtonRow
          buttons={
            tab === "play" && canOpenMoonlight
              ? [{ label: "Open Moonlight", onClick: () => void openMoonlight() }, settings]
              : [settings]
          }
        />
      </PanelSectionRow>
    </PanelSection>
  );
}

export function QuickPanel() {
  const ready = useStore((s) => s.ready);
  const loadError = useStore((s) => s.loadError);
  const configured = useStore((s) => s.settings?.features.configured ?? false);
  const useMoonlight = useStore((s) => s.settings?.features.moonlight ?? false);
  const useSunshine = useStore((s) => s.settings?.features.sunshine ?? false);
  const chosenTab = useStore((s) => s.quickTab);
  const visible = useQuickAccessVisible();
  const wasVisible = useRef(false);
  const both = useMoonlight && useSunshine;
  const playRef = useRef<HTMLDivElement>(null);
  const shareRef = useRef<HTMLDivElement>(null);

  const onBumper = useCallback((evt: GamepadEvent) => {
    const button = evt.detail.button;
    if (button !== GamepadButton.BUMPER_LEFT && button !== GamepadButton.BUMPER_RIGHT) return;
    if (evt.detail.is_repeat) return;
    const next: QuickTab = button === GamepadButton.BUMPER_LEFT ? "play" : "share";
    setQuickTab(next);
    // The focused control may belong to the tab that just went away: keep focus on the tab itself.
    window.requestAnimationFrame(() => (next === "play" ? playRef : shareRef).current?.focus());
  }, []);

  useEffect(() => {
    void loadInitialState();
  }, []);

  useEffect(() => {
    // Wait for the state before treating the menu as opened.
    if (!ready) return;
    const opened = visible && !wasVisible.current;
    wasVisible.current = visible;
    if (!opened) return;
    if (useSunshine) void refreshIncoming();
    // On opening, show the side where something is happening.
    if (both) {
      const { session, launching, incoming } = getState();
      if (session || launching) setQuickTab("play", false);
      else if (incoming?.clients) setQuickTab("share", false);
    }
  }, [visible, ready, both, useSunshine]);

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

  if (!useMoonlight && !useSunshine) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Nothing turned on" description="Turn on Play or Share in Settings → General." />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => openEclipsePage("general")}>
            Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const tab: QuickTab = both ? chosenTab : useMoonlight ? "play" : "share";
  const content = (
    <>
      {useMoonlight && <OutgoingLive />}
      {useSunshine && <IncomingLive />}
      {useSunshine && <PairingRequests visible={visible} />}
      {tab === "play" ? <PlayTab visible={visible} /> : <ShareTab visible={visible} />}
      <Footer tab={tab} />
    </>
  );
  if (!both) return content;

  return (
    // Button presses bubble up from the focused control: LB/RB work anywhere in the panel.
    <Focusable
      className="eclipse-quick-panel"
      onButtonDown={onBumper}
      actionDescriptionMap={{ [GamepadButton.BUMPER_LEFT]: "Play", [GamepadButton.BUMPER_RIGHT]: "Share" }}
    >
      {/* Steam scrolls a focused control to the top edge: keep it clear of the pinned tabs. */}
      <style>{`.eclipse-quick-panel * { scroll-margin-top: ${TABS_HEIGHT}px; }`}</style>
      <QuickTabs playRef={playRef} shareRef={shareRef} />
      {content}
    </Focusable>
  );
}
