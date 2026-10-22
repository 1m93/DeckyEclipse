import { ButtonItem, Focusable, Menu, MenuItem, MenuSeparator, showContextMenu } from "@decky/ui";
import { memo, useCallback } from "react";
import { FaPlay } from "react-icons/fa";
import { quitHostApp, streamApp, wakeHost } from "../lib/actions";
import { hostState, openAppName, STATE_COLORS, type HostState } from "../lib/format";
import { openEclipsePage } from "../lib/navigation";
import { useStore } from "../lib/store";
import type { Host, HostStatus } from "../types";
import { showAppPicker } from "./AppPickerModal";
import { showPairModal } from "./PairModal";
import { StatusDot } from "./StatusDot";

function showHostMenu(host: Host, lastApp: string | undefined, canWake: boolean, busy: boolean, target?: EventTarget) {
  showContextMenu(
    <Menu label={host.name}>
      {lastApp && <MenuItem onClick={() => void streamApp(host, lastApp)}>Resume "{lastApp}"</MenuItem>}
      <MenuItem onClick={() => showAppPicker(host)}>Choose app...</MenuItem>
      {canWake && <MenuItem onClick={() => void wakeHost(host)}>Wake (Wake-on-LAN)</MenuItem>}
      {busy && <MenuItem onClick={() => void quitHostApp(host)}>Quit running app</MenuItem>}
      <MenuSeparator />
      <MenuItem onClick={() => showPairModal({ address: "", host })}>Pair again</MenuItem>
      <MenuItem onClick={() => openEclipsePage("hosts", host.uuid)}>Host settings</MenuItem>
    </Menu>,
    target,
  );
}

const CHIP_TEXT: Record<HostState, string> = {
  online: "Online",
  busy: "App open",
  streaming: "Streaming",
  offline: "Offline",
  unknown: "…",
};

function chipText(state: HostState, status: HostStatus | undefined, launching: boolean): string {
  if (launching) return "Starting…";
  if (state === "online" && status?.latencyMs != null) return `${status.latencyMs} ms`;
  return CHIP_TEXT[state];
}

const ellipsis = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

/**
 * One host in the Quick Access list, rendered as a full-width ButtonItem so it
 * lines up with the other panel buttons.
 * A: choose an app · X: wake when offline · Y: host menu.
 */
export const HostRow = memo(function HostRow({ host }: { host: Host }) {
  const status = useStore((s) => s.status[host.uuid]);
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]);
  const launching = useStore((s) => s.launching === host.uuid);
  const streamingApp = useStore((s) => (s.session?.hostUuid === host.uuid ? s.session.appName : null));

  const state = hostState(status, streamingApp !== null);
  const canWake = state === "offline" && Boolean(cfg?.mac || host.mac || status?.mac);
  const openApp = state === "busy" ? openAppName(host, status) : null;
  // What the second line shows, in order of relevance.
  const appLine = streamingApp ?? (openApp ? `${openApp} is open` : (cfg?.lastApp ?? null));
  const subtitle = appLine ?? status?.address ?? host.addresses[0]?.address ?? "";

  const open = useCallback(() => showAppPicker(host), [host]);
  const wake = useCallback(() => void wakeHost(host), [host]);
  const menu = useCallback(
    (evt: { currentTarget?: EventTarget | null }) =>
      showHostMenu(host, cfg?.lastApp, canWake, state === "busy", evt.currentTarget ?? undefined),
    [host, cfg?.lastApp, canWake, state],
  );

  return (
    // Gamepad actions bubble from the focused button to this wrapper.
    <Focusable
      onSecondaryActionDescription={canWake ? "Wake" : undefined}
      onSecondaryButton={canWake ? wake : undefined}
      onOptionsActionDescription="Options"
      onOptionsButton={menu}
    >
      <ButtonItem layout="below" bottomSeparator="none" onClick={open}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", textAlign: "left" }}>
          <StatusDot state={state} spacing={0} />
          <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, lineHeight: 1.25 }}>
            <span style={{ ...ellipsis, fontWeight: 600 }}>{host.name}</span>
            {subtitle && (
              <span style={{ ...ellipsis, fontSize: 12, opacity: 0.65, display: "flex", alignItems: "center", gap: 5 }}>
                {appLine && <FaPlay size={8} style={{ flexShrink: 0 }} />}
                {subtitle}
              </span>
            )}
          </div>
          <span
            style={{
              flexShrink: 0,
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: 10,
              color: STATE_COLORS[state],
              background: "rgba(255, 255, 255, 0.08)",
            }}
          >
            {chipText(state, status, launching)}
          </span>
        </div>
      </ButtonItem>
    </Focusable>
  );
});
