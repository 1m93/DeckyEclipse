import {
  DialogButton,
  DialogFooter,
  DialogHeader,
  DropdownItem,
  Focusable,
  ModalRoot,
  Spinner,
  showModal,
} from "@decky/ui";
import { memo, useCallback, useMemo } from "react";
import { FaPlay, FaStar } from "react-icons/fa";
import { quitHostApp, streamApp, toggleFavorite, notify, wakeHost } from "../lib/actions";
import { describeHost, FALLBACK_APPS, hostState, sortApps } from "../lib/format";
import { addLibraryShortcut } from "../lib/steam";
import { updateSettings, useStore } from "../lib/store";
import type { Host, HostApp, HostConfig } from "../types";
import { StatusDot } from "./StatusDot";
import { showPairModal } from "./PairModal";

const EMPTY_CFG: HostConfig = {};

interface Props {
  host: Host;
  closeModal?: () => void;
}

const AppButton = memo(function AppButton({
  app,
  favorite,
  last,
  disabled,
  onStart,
  onFavorite,
  onAddToLibrary,
}: {
  app: HostApp;
  favorite: boolean;
  last: boolean;
  disabled: boolean;
  onStart: (name: string) => void;
  onFavorite: (name: string) => void;
  onAddToLibrary: (name: string) => void;
}) {
  return (
    <DialogButton
      disabled={disabled}
      preferredFocus={last}
      style={{ display: "flex", alignItems: "center", gap: 10, textAlign: "left", minHeight: 48 }}
      onClick={() => onStart(app.name)}
      onOKActionDescription="Stream"
      onSecondaryActionDescription={favorite ? "Unfavorite" : "Favorite"}
      onSecondaryButton={() => onFavorite(app.name)}
      onOptionsActionDescription="Add to library"
      onOptionsButton={() => onAddToLibrary(app.name)}
    >
      <FaPlay size={12} style={{ opacity: last ? 1 : 0.4 }} />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{app.name}</span>
      {last && <span style={{ fontSize: 11, opacity: 0.7 }}>LAST PLAYED</span>}
      {app.hdr && <span style={{ fontSize: 11, opacity: 0.7 }}>HDR</span>}
      {favorite && <FaStar size={12} color="#e5a50a" />}
    </DialogButton>
  );
});

function AppPickerModal({ host: initialHost, closeModal }: Props) {
  const host = useStore((s) => s.hosts.find((h) => h.uuid === initialHost.uuid)) ?? initialHost;
  const status = useStore((s) => s.status[host.uuid]);
  const cfg = useStore((s) => s.settings?.hosts[host.uuid]) ?? EMPTY_CFG;
  const launching = useStore((s) => s.launching === host.uuid);
  const waking = useStore((s) => s.waking[host.uuid]);
  const launchStage = useStore((s) => s.launchStage);

  const displayOptions = useMemo(
    () =>
      cfg.displays?.length
        ? [
            { data: "", label: "Keep Sunshine's setting" },
            ...cfg.displays.map((d) => ({ data: d.outputName, label: d.label })),
          ]
        : null,
    [cfg.displays],
  );

  const usingFallback = host.apps.length === 0;
  const apps = useMemo(() => sortApps(usingFallback ? FALLBACK_APPS : host.apps, cfg), [host.apps, cfg, usingFallback]);
  const favorites = useMemo(() => new Set(cfg.favorites ?? []), [cfg.favorites]);
  const state = hostState(status);
  const canWake = state === "offline" && Boolean(cfg.mac || host.mac || status?.mac);

  // Keep the picker open on failure so the user can retry or pick another app.
  const start = useCallback(
    async (name: string) => {
      if (await streamApp(host, name)) closeModal?.();
    },
    [host, closeModal],
  );
  const favorite = useCallback((name: string) => toggleFavorite(host, name), [host]);
  const addToLibrary = useCallback(
    async (name: string) => {
      try {
        const { created } = await addLibraryShortcut(host.uuid, name);
        notify(created ? `Added "${name}" to your library` : `"${name}" is already in your library`);
      } catch (err) {
        notify(`Could not add to library: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [host.uuid],
  );

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>
        <div style={{ display: "flex", alignItems: "center" }}>
          <StatusDot state={state} />
          {host.name}
        </div>
      </DialogHeader>
      <div style={{ fontSize: 13, opacity: 0.75, marginBottom: 12 }}>
        {launching ? (
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Spinner style={{ width: 16, height: 16 }} />
            {waking ? `Waking host... ${waking[0]}s / ${waking[1]}s` : (launchStage ?? "Preparing stream...")}
          </span>
        ) : (
          describeHost(host, status, canWake)
        )}
      </div>
      {usingFallback && (
        <div style={{ fontSize: 12, opacity: 0.65, marginBottom: 8 }}>
          Moonlight has not cached this host's app list yet. Open Moonlight once to load it; default Sunshine apps are
          shown meanwhile.
        </div>
      )}
      {displayOptions && (
        <DropdownItem
          label="Host display"
          description="Switching restarts Sunshine on the host, which takes a few seconds."
          rgOptions={displayOptions}
          selectedOption={cfg.display ?? ""}
          disabled={launching}
          onChange={(opt) => updateSettings({ hosts: { [host.uuid]: { display: opt.data || null } } }, 0)}
        />
      )}
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }} flow-children="column">
        {apps.map((app) => (
          <AppButton
            key={app.name}
            app={app}
            favorite={favorites.has(app.name)}
            last={app.name === cfg.lastApp}
            disabled={launching}
            onStart={start}
            onFavorite={favorite}
            onAddToLibrary={addToLibrary}
          />
        ))}
      </Focusable>
      <DialogFooter>
        <Focusable style={{ display: "flex", gap: 8, marginTop: 16 }} flow-children="row">
          {canWake && (
            <DialogButton style={{ flex: 1 }} onClick={() => void wakeHost(host)}>
              Wake
            </DialogButton>
          )}
          {state === "busy" && (
            <DialogButton style={{ flex: 1 }} onClick={() => void quitHostApp(host)}>
              Quit running app
            </DialogButton>
          )}
          <DialogButton
            style={{ flex: 1 }}
            onClick={() => {
              closeModal?.();
              showPairModal({ address: "", host });
            }}
          >
            Pair again
          </DialogButton>
        </Focusable>
      </DialogFooter>
    </ModalRoot>
  );
}

export function showAppPicker(host: Host): void {
  showModal(<AppPickerModal host={host} />);
}
