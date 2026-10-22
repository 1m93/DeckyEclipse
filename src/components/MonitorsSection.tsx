import {
  ButtonItem,
  ConfirmModal,
  DialogBody,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DialogFooter,
  DialogHeader,
  Field,
  Focusable,
  Menu,
  MenuItem,
  ModalRoot,
  TextField,
  ToggleField,
  showContextMenu,
  showModal,
} from "@decky/ui";
import { useEffect, useMemo, useState } from "react";
import { FaCheck } from "react-icons/fa";
import { backend } from "../backend";
import { notify } from "../lib/actions";
import { monitorLabel, refreshMonitors } from "../lib/monitors";
import { setHostConfig, useStore } from "../lib/store";
import type { Host, HostConfig, HostDisplay, Patch } from "../types";
import { ButtonRow } from "./ButtonRow";

/** Merge detected monitors into the saved list, keeping names the user gave. */
function mergeDisplays(saved: HostDisplay[], found: HostDisplay[], keepLabels = true): HostDisplay[] {
  const byName = new Map(saved.map((d) => [d.outputName, d]));
  for (const display of found) {
    const existing = byName.get(display.outputName);
    byName.set(display.outputName, keepLabels && existing ? existing : display);
  }
  return [...byName.values()];
}

async function findMonitors(host: Host): Promise<{ count: number; error: string | null }> {
  const res = await refreshMonitors(host);
  if (!res?.ok) return { count: 0, error: res?.error ?? "No monitors found" };
  return { count: (res.settings.hosts[host.uuid]?.displays ?? []).length, error: null };
}

// ---------------------------------------------------------------- modals

/** Step 1 of the setup: Sunshine login, then monitors are found right away. */
function SunshineLoginModal({ host, cfg, closeModal }: { host: Host; cfg: HostConfig; closeModal?: () => void }) {
  const [user, setUser] = useState(cfg.sunshineUser ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canConnect = user.trim().length > 0 && (password.length > 0 || Boolean(cfg.sunshinePasswordSet));

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const saveError = await setHostConfig(host.uuid, {
        sunshineUser: user.trim(),
        ...(password ? { sunshinePassword: password } : {}),
      });
      if (saveError) return setError(saveError);
      const test = await backend.testHostCredentials(host.uuid);
      if (!test.ok) return setError(test.error ?? "Could not log in to Sunshine");
      const found = await findMonitors(host);
      notify(found.count ? `Found ${found.count} monitor(s) on ${host.name}` : `Connected to ${host.name}`);
      closeModal?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>Connect to Sunshine on {host.name}</DialogHeader>
      <DialogBody>
        <div style={{ marginBottom: 12, opacity: 0.8 }}>
          Use the login of Sunshine's web page on {host.name} (https://{host.addresses[0]?.address ?? "host"}:47990).
          Eclipse uses it to switch monitors, enter pairing PINs and close apps on the host.
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <TextField label="Username" value={user} focusOnMount onChange={(e) => setUser(e.target.value)} />
          <TextField
            label="Password"
            bIsPassword
            placeholder={cfg.sunshinePasswordSet ? "•••••••• (saved)" : ""}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Focusable>
        {error && <div style={{ color: "#ff6b6b", marginTop: 8 }}>{error}</div>}
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            { label: busy ? "Connecting..." : "Connect", disabled: busy || !canConnect, onClick: () => void connect() },
            { label: "Cancel", onClick: () => closeModal?.() },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

function TextInputModal({
  title,
  label,
  description,
  initial = "",
  okText,
  onSubmit,
  closeModal,
}: {
  title: string;
  label: string;
  description?: string;
  initial?: string;
  okText: string;
  onSubmit: (value: string) => void;
  closeModal?: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <ConfirmModal
      strTitle={title}
      strOKButtonText={okText}
      bOKDisabled={!value.trim()}
      closeModal={closeModal}
      onCancel={closeModal}
      onOK={() => {
        onSubmit(value.trim());
        closeModal?.();
      }}
    >
      <TextField label={label} description={description} value={value} focusOnMount onChange={(e) => setValue(e.target.value)} />
    </ConfirmModal>
  );
}

export function showSunshineLogin(host: Host, cfg: HostConfig): void {
  showModal(<SunshineLoginModal host={host} cfg={cfg} />);
}

// ---------------------------------------------------------------- monitor list

function MonitorRow({
  display,
  selected,
  streamingNow,
  onSelect,
  onRename,
  onRemove,
}: {
  display: HostDisplay;
  selected: boolean;
  streamingNow: boolean;
  onSelect: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const status =
    selected && streamingNow
      ? "Used for your streams"
      : selected
        ? "Switches to this monitor on your next stream"
        : streamingNow
          ? "Sunshine streams this monitor now"
          : "";

  return (
    // The options button opens the menu; the event bubbles from the focused button.
    <Focusable
      onOptionsActionDescription="Rename / remove"
      onOptionsButton={(evt) =>
        showContextMenu(
          <Menu label={display.label}>
            <MenuItem onClick={onRename}>Rename</MenuItem>
            <MenuItem tone="destructive" onClick={onRemove}>
              Remove
            </MenuItem>
          </Menu>,
          evt.currentTarget ?? undefined,
        )
      }
    >
      <ButtonItem
        label={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {selected && <FaCheck size={12} color="#1a9fff" />}
            {display.label}
          </span>
        }
        description={status || undefined}
        // Not disabled: the selected row must stay focusable for its options menu.
        onClick={() => !selected && onSelect()}
      >
        {selected ? "Selected" : "Use"}
      </ButtonItem>
    </Focusable>
  );
}

/**
 * Choose which monitor of the host is streamed. Sunshine streams one monitor
 * (its "Output Name" setting); Eclipse switches it before a stream starts.
 */
export function MonitorsSection({ host, cfg }: { host: Host; cfg: HostConfig }) {
  const displays = useMemo(() => cfg.displays ?? [], [cfg.displays]);
  const hasCreds = Boolean(cfg.sunshineUser && cfg.sunshinePasswordSet);
  const info = useStore((s) => s.monitors[host.uuid]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const loading = info?.loading ?? false;

  // Monitors may have changed on the host: re-read them when the page opens.
  useEffect(() => {
    if (hasCreds) void refreshMonitors(host);
  }, [host, hasCreds]);

  const save = async (patch: Patch<HostConfig>) => {
    const error = await setHostConfig(host.uuid, patch);
    if (error) notify(error);
  };

  const refresh = async (rescan: boolean) => {
    const res = await refreshMonitors(host, rescan);
    if (res && !res.ok) notify(res.error ?? "Could not read the host's monitors");
  };

  const openLogin = () => showSunshineLogin(host, cfg);
  const addManually = () =>
    showModal(
      <TextInputModal
        title="Add a monitor manually"
        label="Monitor ID"
        description="Linux hosts: a connector name such as HDMI-A-1. Windows hosts: the device_id listed under 'Currently available display devices' in Sunshine's Troubleshooting log."
        okText="Add"
        onSubmit={(id) =>
          void save({ displays: mergeDisplays(displays, [{ outputName: id, label: id, manual: true }]) })
        }
      />,
    );

  // Not connected yet: one clear call to action.
  if (!hasCreds) {
    return (
      <DialogControlsSection>
        <DialogControlsSectionHeader>Monitors</DialogControlsSectionHeader>
        <ButtonItem
          layout="below"
          label="Stream a different monitor"
          description={`If ${host.name} has several monitors, Eclipse can choose which one is streamed. Connect to Sunshine on ${host.name} first.`}
          onClick={openLogin}
        >
          Set up...
        </ButtonItem>
      </DialogControlsSection>
    );
  }

  const currentLabel = loading
    ? "Checking..."
    : info?.error
      ? `Unknown (${info.error})`
      : monitorLabel(displays, info?.current ?? null);
  const listAge = info?.detectedAt
    ? `Monitor list from Sunshine at ${info.detectedAt} (host time). Plugged a monitor in or out since? Use Rescan.`
    : "Use Rescan if a monitor was plugged in or out since Sunshine started.";

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Monitors</DialogControlsSectionHeader>
      <Field label="Sunshine streams" description={currentLabel} />
      <ToggleField
        label="Ask which monitor each time"
        description="Show the monitor list, freshly read from the host, whenever a stream starts."
        checked={Boolean(cfg.askMonitor)}
        onChange={(checked) => void save({ askMonitor: checked })}
      />

      {displays.length === 0 ? (
        <ButtonItem
          layout="below"
          label="No monitors found yet"
          description="Eclipse reads the monitor list from Sunshine on the host. Rescan restarts Sunshine so it lists its monitors again."
          disabled={loading}
          onClick={() => void refresh(true)}
        >
          {loading ? "Looking for monitors..." : "Rescan monitors"}
        </ButtonItem>
      ) : (
        <>
          {displays.map((d) => (
            <MonitorRow
              key={d.outputName}
              display={d}
              selected={cfg.display === d.outputName}
              streamingNow={info?.current === d.outputName}
              onSelect={() => void save({ display: d.outputName })}
              onRename={() =>
                showModal(
                  <TextInputModal
                    title="Rename monitor"
                    label="Name"
                    initial={d.label}
                    okText="Save"
                    onSubmit={(name) =>
                      void save({
                        displays: displays.map((x) => (x.outputName === d.outputName ? { ...x, label: name } : x)),
                      })
                    }
                  />,
                )
              }
              onRemove={() =>
                void save({
                  displays: displays.filter((x) => x.outputName !== d.outputName),
                  ...(cfg.display === d.outputName ? { display: null } : {}),
                })
              }
            />
          ))}
          <Field
            label="How it works"
            description={
              cfg.display
                ? "Before each stream, Eclipse points Sunshine at the selected monitor. Sunshine restarts when it has to switch, which takes a few seconds."
                : "No monitor selected: Sunshine keeps streaming the monitor shown above."
            }
          />
          {cfg.display && (
            <ButtonItem layout="below" onClick={() => void save({ display: null })}>
              Stop switching monitors
            </ButtonItem>
          )}
        </>
      )}

      <Field label="Monitor list" description={listAge} />
      <ButtonRow
        buttons={[
          { label: loading ? "Refreshing..." : "Refresh", disabled: loading, onClick: () => void refresh(false) },
          { label: "Rescan (restarts Sunshine)", disabled: loading, onClick: () => void refresh(true) },
        ]}
      />
      <ButtonItem layout="below" onClick={() => setShowAdvanced((v) => !v)}>
        {showAdvanced ? "Hide advanced" : "Advanced..."}
      </ButtonItem>
      {showAdvanced && (
        <ButtonRow
          buttons={[
            { label: "Add manually", onClick: addManually },
            { label: "Sunshine login", onClick: openLogin },
          ]}
        />
      )}
    </DialogControlsSection>
  );
}
