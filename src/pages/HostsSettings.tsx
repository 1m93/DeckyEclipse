import {
  ButtonItem,
  ConfirmModal,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  Menu,
  MenuItem,
  showContextMenu,
  showModal,
} from "@decky/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { backend } from "../backend";
import { showAddHostModal } from "../components/AddHostModal";
import { showAppPicker } from "../components/AppPickerModal";
import { ButtonRow } from "../components/ButtonRow";
import { CommitTextField } from "../components/CommitTextField";
import { MonitorsSection, showSunshineLogin } from "../components/MonitorsSection";
import { showNetworkTest } from "../components/NetworkTestModal";
import { showPairModal } from "../components/PairModal";
import { StatusDot } from "../components/StatusDot";
import { notify, wakeHost } from "../lib/actions";
import { describeHost, FALLBACK_APPS, hostState } from "../lib/format";
import { addLibraryShortcut, removeLibraryShortcut } from "../lib/steam";
import { profileOptions, setHostConfig, useStore } from "../lib/store";
import type { Host, HostConfig, Patch } from "../types";

const EMPTY_CFG: HostConfig = {};
const ellipsis = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

async function addToLibrary(host: Host, appName: string): Promise<void> {
  try {
    const { created } = await addLibraryShortcut(host.uuid, appName);
    notify(created ? `Added "${appName}" to your library` : `"${appName}" is already in your library`);
  } catch (err) {
    notify(`Could not add to library: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** Controller-friendly app list: one press to pick, no dropdown + button combo. */
function showLibraryAppMenu(host: Host, target?: EventTarget) {
  const apps = (host.apps.length ? host.apps : FALLBACK_APPS).filter((app) => !app.hidden);
  showContextMenu(
    <Menu label="Add to Steam library">
      {apps.map((app) => (
        <MenuItem key={app.name} onClick={() => void addToLibrary(host, app.name)}>
          {app.name}
        </MenuItem>
      ))}
    </Menu>,
    target,
  );
}

function confirmReset(host: Host) {
  showModal(
    <ConfirmModal
      strTitle={`Reset ${host.name}?`}
      strDescription="Clears Eclipse's settings for this host: quality preset, displays, MAC address, Sunshine credentials and favorites. Pairing in Moonlight is not affected."
      strOKButtonText="Reset"
      bDestructiveWarning
      onOK={() => void setHostConfig(host.uuid, null).then(() => notify(`Reset Eclipse settings for ${host.name}`))}
    />,
  );
}

function HostDetails({ host }: { host: Host }) {
  const status = useStore((s) => s.status[host.uuid]);
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]) ?? EMPTY_CFG;
  const presets = useStore((s) => s.presets);
  const profiles = useStore((s) => s.settings?.profiles);
  const inheritPreset = useStore((s) => s.inheritPreset);
  const library = useStore((s) => s.settings?.shortcuts.library);
  const streamingHere = useStore((s) => s.session?.hostUuid === host.uuid);

  const presetOptions = useMemo(() => {
    const options = [
      { data: inheritPreset, label: "Same as the settings above" },
      ...profileOptions(profiles),
      ...presets.map((p) => ({ data: p.id, label: p.label })),
    ];
    // A pinned preset that no longer exists still shows as what it does now.
    return cfg.preset && !options.some((o) => o.data === cfg.preset)
      ? [...options, { data: cfg.preset, label: "Removed profile (same as above)" }]
      : options;
  }, [presets, profiles, inheritPreset, cfg.preset]);
  const entries = useMemo(
    () => Object.entries(library ?? {}).filter(([, e]) => e.hostUuid === host.uuid),
    [library, host.uuid],
  );

  const save = async (patch: Patch<HostConfig>) => {
    const error = await setHostConfig(host.uuid, patch);
    if (error) notify(error);
  };
  const detectedMac = host.mac ?? status?.mac ?? null;
  const mac = cfg.mac || detectedMac;
  const state = hostState(status, streamingHere);
  const hasLogin = Boolean(cfg.sunshineUser && cfg.sunshinePasswordSet);
  const addresses = host.addresses.map((a) => `${a.address}:${a.port} (${a.kind})`).join(" · ");

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>
          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <StatusDot state={state} spacing={0} />
            <span style={ellipsis}>{host.name}</span>
          </span>
        </DialogControlsSectionHeader>
        <Field label="Status" description={describeHost(host, status, Boolean(mac), streamingHere)} />
        <Field label="Addresses" description={addresses || "None"} />
        {status?.version && <Field label="Sunshine version" description={status.version} />}
        <ButtonRow
          buttons={[
            { label: "Stream", onClick: () => showAppPicker(host) },
            { label: "Wake", disabled: !mac, onClick: () => void wakeHost(host) },
            { label: "Pair again", onClick: () => showPairModal({ address: "", host }) },
          ]}
        />
        <ButtonItem
          layout="below"
          description="Measures delay and stability to this host and this device's link, and suggests a bitrate. For a stuttering stream."
          onClick={() => showNetworkTest(host)}
        >
          Network test
        </ButtonItem>
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Profile for this host</DialogControlsSectionHeader>
        <DropdownItem
          label="Profile"
          description="Use another preset or profile for this host, e.g. for a weaker or remote PC."
          rgOptions={presetOptions}
          selectedOption={cfg.preset ?? inheritPreset}
          onChange={(opt) => void save({ preset: opt.data })}
        />
      </DialogControlsSection>

      <MonitorsSection host={host} cfg={cfg} />

      <DialogControlsSection>
        <DialogControlsSectionHeader>Wake-on-LAN</DialogControlsSectionHeader>
        <CommitTextField
          label="MAC address"
          description={detectedMac ? `Detected: ${detectedMac}. Leave empty to use it.` : "Not detected, enter it to enable Wake."}
          placeholder={detectedMac ?? "aa:bb:cc:dd:ee:ff"}
          value={cfg.mac ?? ""}
          onCommit={(value) => setHostConfig(host.uuid, { mac: value.trim() || null })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>This host's Sunshine login</DialogControlsSectionHeader>
        <Field
          label={hasLogin ? `Connected as ${cfg.sunshineUser}` : "Not connected"}
          description="Optional. With the login of Sunshine's web page on the host, Eclipse can switch monitors, enter pairing PINs and close host apps from Gaming Mode."
        />
        <ButtonRow
          buttons={[
            { label: hasLogin ? "Change login" : "Connect", onClick: () => showSunshineLogin(host, cfg) },
            {
              label: "Test",
              disabled: !hasLogin,
              onClick: async () => {
                const res = await backend.testHostCredentials(host.uuid);
                notify(res.ok ? "Sunshine login works" : (res.error ?? "Test failed"));
              },
            },
            {
              label: "Forget",
              disabled: !hasLogin,
              onClick: () => void save({ sunshineUser: null, sunshinePassword: null }),
            },
          ]}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Steam library</DialogControlsSectionHeader>
        {entries.map(([id, entry]) => (
          <ButtonItem
            key={id}
            label={entry.appName}
            description="Shortcut in your Steam library"
            onClick={() => void removeLibraryShortcut(id).then(() => notify(`Removed "${entry.appName}"`))}
          >
            Remove
          </ButtonItem>
        ))}
        <ButtonItem
          layout="below"
          description="Library shortcuts stream directly and wake the host first when needed."
          onClick={(evt) => showLibraryAppMenu(host, evt.currentTarget ?? undefined)}
        >
          Add app to library...
        </ButtonItem>
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Reset</DialogControlsSectionHeader>
        <ButtonItem layout="below" onClick={() => confirmReset(host)}>
          Reset Eclipse settings for this host
        </ButtonItem>
      </DialogControlsSection>
    </>
  );
}

/** `pageTarget.nonce` of the last request to show a host that was handled. */
let handledNonce = -1;

/** Hosts part of the Streaming page: pick a host and change its settings. */
export function HostsSettings() {
  const hosts = useStore((s) => s.hosts);
  const target = useStore((s) => (s.pageTarget.page === "streaming" ? s.pageTarget.host : null));
  const nonce = useStore((s) => s.pageTarget.nonce);
  const [selected, setSelected] = useState<string | null>(target ?? hosts[0]?.uuid ?? null);
  const anchor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Each request is handled once: opening the page again later starts at the top.
    if (!target || nonce === handledNonce) return;
    handledNonce = nonce;
    setSelected(target);
    // The hosts come after the stream settings: bring the requested one into view and focus.
    const timer = window.setTimeout(() => {
      const section = anchor.current?.nextElementSibling;
      section?.scrollIntoView({ block: "start" });
      section?.querySelector<HTMLElement>("button, [tabindex]")?.focus();
    }, 150);
    return () => window.clearTimeout(timer);
  }, [target, nonce]);

  const host = hosts.find((h) => h.uuid === selected) ?? hosts[0];
  const options = useMemo(() => hosts.map((h) => ({ data: h.uuid, label: h.name })), [hosts]);

  return (
    <>
      {/* Marks where the hosts start, for requests to show a host. */}
      <div ref={anchor} />
      <DialogControlsSection>
        <DialogControlsSectionHeader>Hosts</DialogControlsSectionHeader>
        {hosts.length === 0 ? (
          <ButtonItem
            layout="below"
            label="No paired hosts"
            description="Pair this device with a PC running Sunshine. Paired hosts are read from Moonlight."
            onClick={showAddHostModal}
          >
            Add host
          </ButtonItem>
        ) : (
          <>
            <DropdownItem
              label="Host"
              rgOptions={options}
              selectedOption={host?.uuid}
              onChange={(opt) => setSelected(opt.data)}
            />
            <ButtonItem layout="below" onClick={showAddHostModal}>
              Add another host
            </ButtonItem>
          </>
        )}
      </DialogControlsSection>
      {host && <HostDetails key={host.uuid} host={host} />}
    </>
  );
}
