import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  Focusable,
} from "@decky/ui";
import { useEffect, useMemo, useState } from "react";
import { backend } from "../backend";
import { showAddHostModal } from "../components/AddHostModal";
import { showAppPicker } from "../components/AppPickerModal";
import { CommitTextField } from "../components/CommitTextField";
import { HostDisplaysSection } from "../components/HostDisplaysSection";
import { showPairModal } from "../components/PairModal";
import { StatusDot } from "../components/StatusDot";
import { notify, wakeHost } from "../lib/actions";
import { FALLBACK_APPS, hostState } from "../lib/format";
import { addLibraryShortcut, removeLibraryShortcut } from "../lib/steam";
import { setHostConfig, useStore } from "../lib/store";
import type { Host, HostConfig, Patch } from "../types";

const EMPTY_CFG: HostConfig = {};

function HostDetails({ host }: { host: Host }) {
  const status = useStore((s) => s.status[host.uuid]);
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]) ?? EMPTY_CFG;
  const presets = useStore((s) => s.presets);
  const inheritPreset = useStore((s) => s.inheritPreset);
  const library = useStore((s) => s.settings?.shortcuts.library);
  const [libraryApp, setLibraryApp] = useState<string | null>(null);

  const presetOptions = useMemo(
    () => [{ data: inheritPreset, label: "Use global stream settings" }, ...presets.map((p) => ({ data: p.id, label: p.label }))],
    [presets, inheritPreset],
  );
  const appOptions = useMemo(
    () => (host.apps.length ? host.apps : FALLBACK_APPS).map((a) => ({ data: a.name, label: a.name })),
    [host.apps],
  );
  const entries = useMemo(
    () => Object.entries(library ?? {}).filter(([, e]) => e.hostUuid === host.uuid),
    [library, host.uuid],
  );

  const save = async (patch: Patch<HostConfig>) => {
    const error = await setHostConfig(host.uuid, patch);
    if (error) notify(error);
  };
  const detectedMac = host.mac ?? status?.mac ?? null;
  const state = hostState(status);

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>
          <span style={{ display: "flex", alignItems: "center" }}>
            <StatusDot state={state} />
            {host.name}
          </span>
        </DialogControlsSectionHeader>
        <Field
          label="Addresses"
          description={host.addresses.map((a) => `${a.address}:${a.port} (${a.kind})`).join(", ") || "None"}
        />
        {status?.version && <Field label="Sunshine version" description={status.version} />}
        <Focusable style={{ display: "flex", gap: 8, padding: "8px 0" }} flow-children="row">
          <ButtonItem layout="below" onClick={() => showAppPicker(host)}>
            Stream
          </ButtonItem>
          <ButtonItem layout="below" onClick={() => void wakeHost(host)}>
            Wake
          </ButtonItem>
          <ButtonItem layout="below" onClick={() => showPairModal({ address: "", host })}>
            Pair again
          </ButtonItem>
        </Focusable>
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Streaming</DialogControlsSectionHeader>
        <DropdownItem
          label="Quality for this host"
          description="Pin a preset for this host, e.g. a weaker remote PC. Other options follow the global settings."
          rgOptions={presetOptions}
          selectedOption={cfg.preset ?? inheritPreset}
          onChange={(opt) => void save({ preset: opt.data })}
        />
      </DialogControlsSection>

      <HostDisplaysSection host={host} cfg={cfg} />

      <DialogControlsSection>
        <DialogControlsSectionHeader>Wake-on-LAN</DialogControlsSectionHeader>
        <CommitTextField
          label="MAC address"
          description={detectedMac ? `Detected: ${detectedMac}. Leave empty to use it.` : "Not detected yet, enter it manually."}
          placeholder={detectedMac ?? "aa:bb:cc:dd:ee:ff"}
          value={cfg.mac ?? ""}
          onCommit={(mac) => void save({ mac: mac.trim() || null })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Sunshine web API</DialogControlsSectionHeader>
        <Field description="Optional. Lets Eclipse enter pairing PINs and quit host apps without leaving Gaming Mode." />
        <CommitTextField
          label="Username"
          value={cfg.sunshineUser ?? ""}
          onCommit={(user) => void save({ sunshineUser: user.trim() })}
        />
        <CommitTextField
          label="Password"
          bIsPassword
          placeholder={cfg.sunshinePasswordSet ? "•••••••• (saved)" : ""}
          value=""
          onCommit={(password) => password && void save({ sunshinePassword: password })}
        />
        <ButtonItem
          layout="below"
          onClick={async () => {
            const res = await backend.testHostCredentials(host.uuid);
            notify(res.ok ? "Sunshine credentials work" : (res.error ?? "Test failed"));
          }}
        >
          Test credentials
        </ButtonItem>
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Steam library shortcuts</DialogControlsSectionHeader>
        <Field description="Shortcuts appear in your library and stream directly, waking the host first when needed." />
        {entries.map(([id, entry]) => (
          <ButtonItem
            key={id}
            label={entry.appName}
            description="Press to remove from the library"
            onClick={() => void removeLibraryShortcut(id).then(() => notify(`Removed "${entry.appName}"`))}
          >
            Remove
          </ButtonItem>
        ))}
        <DropdownItem
          label="App"
          rgOptions={appOptions}
          selectedOption={libraryApp}
          strDefaultLabel="Choose an app"
          onChange={(opt) => setLibraryApp(opt.data)}
        />
        <ButtonItem
          layout="below"
          disabled={!libraryApp}
          onClick={async () => {
            if (!libraryApp) return;
            try {
              const { created } = await addLibraryShortcut(host.uuid, libraryApp);
              notify(created ? `Added "${libraryApp}" to your library` : `"${libraryApp}" is already in your library`);
            } catch (err) {
              notify(`Could not add to library: ${err instanceof Error ? err.message : String(err)}`);
            }
          }}
        >
          Add to Steam library
        </ButtonItem>
        <ButtonItem
          layout="below"
          onClick={() => void setHostConfig(host.uuid, null).then(() => notify(`Reset Eclipse settings for ${host.name}`))}
        >
          Reset host settings
        </ButtonItem>
      </DialogControlsSection>
    </>
  );
}

export function HostsPage() {
  const hosts = useStore((s) => s.hosts);
  const target = useStore((s) => s.pageTarget.host);
  const [selected, setSelected] = useState<string | null>(target ?? hosts[0]?.uuid ?? null);

  useEffect(() => {
    if (target) setSelected(target);
  }, [target]);

  const host = hosts.find((h) => h.uuid === selected) ?? hosts[0];
  const options = useMemo(() => hosts.map((h) => ({ data: h.uuid, label: h.name })), [hosts]);

  return (
    <>
      <DialogControlsSection>
        {hosts.length > 0 ? (
          <DropdownItem label="Host" rgOptions={options} selectedOption={host?.uuid} onChange={(opt) => setSelected(opt.data)} />
        ) : (
          <Field
            label="No paired hosts"
            description="Add a host to pair this device with a PC running Sunshine. Paired hosts are read from Moonlight."
          />
        )}
        <ButtonItem layout="below" onClick={showAddHostModal}>
          Add host
        </ButtonItem>
      </DialogControlsSection>
      {host && <HostDetails key={host.uuid} host={host} />}
    </>
  );
}
