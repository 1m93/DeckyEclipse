import { DialogBody, DialogButton, DialogFooter, DialogHeader, Focusable, ModalRoot, Spinner, showModal } from "@decky/ui";
import { useEffect } from "react";
import { FaCheck, FaDesktop } from "react-icons/fa";
import { monitorLabel, refreshMonitors } from "../lib/monitors";
import { useStore } from "../lib/store";
import type { Host, HostConfig } from "../types";
import { ButtonRow } from "./ButtonRow";

const EMPTY_CFG: HostConfig = {};

/** Result of the prompt: an output name, `null` for "don't switch", or `undefined` when cancelled. */
export type MonitorChoice = string | null | undefined;

function MonitorChoiceModal({
  host,
  onChoose,
  closeModal,
}: {
  host: Host;
  onChoose: (choice: MonitorChoice) => void;
  closeModal?: () => void;
}) {
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]) ?? EMPTY_CFG;
  const info = useStore((s) => s.monitors[host.uuid]);
  const displays = cfg.displays ?? [];

  // Always start from a fresh list: monitors may have changed since last time.
  useEffect(() => {
    void refreshMonitors(host);
    // Closing with B or the background also resolves the prompt (as cancelled).
    return () => onChoose(undefined);
  }, [host, onChoose]);

  const choose = (choice: MonitorChoice) => {
    onChoose(choice);
    closeModal?.();
  };

  const loading = info?.loading ?? true;
  const current = info?.current ?? null;

  return (
    <ModalRoot onCancel={() => choose(undefined)} closeModal={closeModal}>
      <DialogHeader>Which monitor of {host.name}?</DialogHeader>
      <DialogBody>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, opacity: 0.75, marginBottom: 10 }}>
          {loading && <Spinner style={{ width: 14, height: 14 }} />}
          {loading
            ? "Checking the host's monitors..."
            : info?.error
              ? info.error
              : `Sunshine streams: ${monitorLabel(displays, current)}${info?.detectedAt ? ` · list from ${info.detectedAt}` : ""}`}
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }} flow-children="column">
          {displays.map((d) => {
            const selected = cfg.display === d.outputName;
            const isCurrent = current === d.outputName;
            return (
              <DialogButton
                key={d.outputName}
                preferredFocus={selected}
                style={{ display: "flex", alignItems: "center", gap: 10, textAlign: "left", minHeight: 48 }}
                onClick={() => choose(d.outputName)}
              >
                <FaDesktop size={14} style={{ opacity: 0.7, flexShrink: 0 }} />
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.label}</span>
                {isCurrent && <span style={{ fontSize: 11, opacity: 0.7 }}>STREAMED NOW</span>}
                {selected && <FaCheck size={12} color="#1a9fff" />}
              </DialogButton>
            );
          })}
          <DialogButton preferredFocus={!cfg.display} onClick={() => choose(null)}>
            Don't switch ({monitorLabel(displays, current)})
          </DialogButton>
        </Focusable>
        {!loading && displays.length === 0 && (
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
            No monitor list yet. Rescan restarts Sunshine on the host so it lists its monitors again.
          </div>
        )}
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            { label: "Rescan monitors", disabled: loading, onClick: () => void refreshMonitors(host, true) },
            { label: "Cancel", onClick: () => choose(undefined) },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

/** Ask which monitor to stream. Resolves once, whichever way the prompt is closed. */
export function chooseMonitor(host: Host): Promise<MonitorChoice> {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (choice: MonitorChoice) => {
      if (settled) return;
      settled = true;
      resolve(choice);
    };
    showModal(<MonitorChoiceModal host={host} onChoose={settle} />);
  });
}
