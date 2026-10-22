import { Field, Menu, MenuItem, MenuSeparator, showContextMenu } from "@decky/ui";
import { memo, useCallback, useRef } from "react";
import { quitHostApp, streamApp, wakeHost } from "../lib/actions";
import { describeHost, hostState } from "../lib/format";
import { openEclipsePage } from "../lib/navigation";
import { useStore } from "../lib/store";
import type { Host } from "../types";
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

/**
 * One host in the Quick Access list.
 * A: choose an app · X: wake when offline · Y: host menu.
 */
export const HostRow = memo(function HostRow({ host }: { host: Host }) {
  const status = useStore((s) => s.status[host.uuid]);
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]);
  const launching = useStore((s) => s.launching === host.uuid);

  const state = hostState(status);
  const canWake = state === "offline" && Boolean(cfg?.mac || host.mac || status?.mac);

  // Field can fire both onActivate and onClick for a single press; open once.
  const lastOpen = useRef(0);
  const open = useCallback(() => {
    const now = Date.now();
    if (now - lastOpen.current < 500) return;
    lastOpen.current = now;
    showAppPicker(host);
  }, [host]);
  const wake = useCallback(() => void wakeHost(host), [host]);
  const menu = useCallback(
    (evt: { currentTarget?: EventTarget | null }) =>
      showHostMenu(host, cfg?.lastApp, canWake, state === "busy", evt.currentTarget ?? undefined),
    [host, cfg?.lastApp, canWake, state],
  );

  return (
    <Field
      label={
        <span style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
          <StatusDot state={state} />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{host.name}</span>
        </span>
      }
      description={launching ? "Starting stream..." : describeHost(host, status, canWake)}
      focusable
      highlightOnFocus
      bottomSeparator="none"
      padding="compact"
      onActivate={open}
      onClick={open}
      onOKActionDescription="Choose app"
      onSecondaryActionDescription={canWake ? "Wake" : undefined}
      onSecondaryButton={canWake ? wake : undefined}
      onOptionsActionDescription="Options"
      onOptionsButton={menu}
    />
  );
});
