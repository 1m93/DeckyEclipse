import {
  ButtonItem,
  ConfirmModal,
  DialogBody,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DialogFooter,
  DialogHeader,
  DropdownItem,
  Field,
  Focusable,
  ModalRoot,
  PanelSectionRow,
  Spinner,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "../backend";
import { notify, refreshSunshineOptions } from "../lib/actions";
import { addGamesToSunshine } from "../lib/covers";
import { loadInitialState } from "../lib/store";
import type { BackupInfo, BackupLocation } from "../types";
import { ButtonRow } from "./ButtonRow";

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatDate(ms: number): string {
  return ms ? new Date(ms).toLocaleString() : "Unknown date";
}

function describeBackup(info: BackupInfo): string {
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return [
    info.location,
    plural(info.profiles, "profile"),
    `settings for ${plural(info.hosts, "host")}`,
    plural(info.games, "game"),
    info.hasPasswords ? "with saved passwords" : "without passwords",
  ].join(" · ");
}

function CreateBackupModal({
  locations,
  onDone,
  closeModal,
}: {
  locations: BackupLocation[];
  onDone: () => void;
  closeModal?: () => void;
}) {
  const [withPasswords, setWithPasswords] = useState(false);
  // A removable drive first: a full SteamOS reinstall erases this device's storage.
  const [location, setLocation] = useState(() => (locations.find((l) => l.removable) ?? locations[0])?.id ?? "home");
  const [busy, setBusy] = useState(false);
  const chosen = locations.find((l) => l.id === location);

  const create = async () => {
    setBusy(true);
    try {
      const res = await backend.backupCreate(withPasswords, location);
      if (!res.ok) {
        notify(res.error ?? "Could not create the backup");
        return;
      }
      notify(`Backup saved to ${res.path ?? chosen?.label ?? "the backup folder"}`);
      onDone();
      closeModal?.();
    } catch (err) {
      notify(`Could not create the backup: ${errorText(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalRoot onCancel={() => !busy && closeModal?.()} closeModal={() => !busy && closeModal?.()}>
      <DialogHeader>Back up settings</DialogHeader>
      <DialogBody>
        <Focusable style={{ display: "flex", flexDirection: "column" }} flow-children="column">
          <div style={{ opacity: 0.8, marginBottom: 8 }}>
            Saves profiles, stream settings, settings of your hosts, Sunshine's options and the Steam games offered to
            other devices. PCs and devices are not paired by a backup: pair them again after reinstalling.
          </div>
          <DropdownItem
            label="Save to"
            description={
              chosen?.removable
                ? `In the Eclipse folder of the ${chosen.label}.`
                : "Kept when the plugin is removed, but a full SteamOS reinstall erases it: use a microSD card or USB drive for that."
            }
            rgOptions={locations.map((l) => ({ data: l.id, label: l.label }))}
            selectedOption={location}
            disabled={busy}
            onChange={(opt) => setLocation(opt.data)}
          />
          <ToggleField
            label="Include saved passwords"
            description="The Sunshine logins saved in Eclipse. They are stored readable in the file: keep it private."
            checked={withPasswords}
            disabled={busy}
            onChange={setWithPasswords}
          />
        </Focusable>
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            { label: busy ? "Saving..." : "Back up", disabled: busy, onClick: () => void create() },
            { label: "Cancel", disabled: busy, onClick: () => closeModal?.() },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

type RestoreStage = "confirm" | "working" | "done";

/** Confirm, then restore with progress, then what was restored and what was not. */
function RestoreModal({ info, onDone, closeModal }: { info: BackupInfo; onDone: () => void; closeModal?: () => void }) {
  const [stage, setStage] = useState<RestoreStage>("confirm");
  const [progress, setProgress] = useState("Restoring settings...");
  const [result, setResult] = useState<{ ok: boolean; lines: string[] }>({ ok: true, lines: [] });

  const run = async () => {
    setStage("working");
    const lines: string[] = [];
    let ok = true;
    try {
      const res = await backend.backupRestore(info.locationId, info.name);
      if (!res.ok) {
        ok = false;
        lines.push(res.error ?? "Could not restore the backup");
      } else {
        lines.push("Profiles, stream settings and host settings are back.");
        if (res.games?.length) {
          setProgress(`Adding games to Sunshine... 0 of ${res.games.length}`);
          try {
            const added = await addGamesToSunshine(res.games, (done, total) =>
              setProgress(`Adding games to Sunshine... ${done} of ${total}`),
            );
            if (added.ok) {
              const n = res.games.length;
              lines.push(`${n} Steam ${n === 1 ? "game is" : "games are"} offered to other devices again.`);
            }
            else lines.push(added.error ?? "The Steam games were not added to Sunshine.");
          } catch (err) {
            lines.push(`The Steam games were not added to Sunshine: ${errorText(err)}`);
          }
        }
        lines.push(...(res.warnings ?? []).map((w) => `${w}.`));
      }
    } catch (err) {
      ok = false;
      lines.push(`Could not restore the backup: ${errorText(err)}`);
    } finally {
      // The backend may have replaced the settings even when a later step failed.
      await loadInitialState(true);
      void refreshSunshineOptions();
      onDone();
    }
    setResult({ ok, lines });
    setStage("done");
  };

  const busy = stage === "working";
  return (
    <ModalRoot onCancel={() => !busy && closeModal?.()} closeModal={() => !busy && closeModal?.()}>
      <DialogHeader>{stage === "done" ? (result.ok ? "Settings restored" : "Restore failed") : "Restore this backup?"}</DialogHeader>
      <DialogBody>
        {stage === "confirm" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div>
              Settings from {formatDate(info.createdAt)} ({info.location}) replace the current ones: profiles, stream
              settings, settings of your hosts and Sunshine's options.
            </div>
            <div style={{ opacity: 0.8 }}>
              {info.hasPasswords ? "" : "Passwords saved on this device are kept. "}
              PCs and devices must be paired again; their settings apply once they are. For Sunshine's options and
              games, install Sunshine first.
            </div>
          </div>
        )}
        {stage === "working" && (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Spinner style={{ width: 20, height: 20 }} />
            {progress}
          </div>
        )}
        {stage === "done" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {result.lines.map((line) => (
              <div key={line}>• {line}</div>
            ))}
          </div>
        )}
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={
            stage === "confirm"
              ? [
                  { label: "Restore", onClick: () => void run() },
                  { label: "Cancel", onClick: () => closeModal?.() },
                ]
              : [{ label: busy ? "Restoring..." : "Close", disabled: busy, onClick: () => closeModal?.() }]
          }
        />
      </DialogFooter>
    </ModalRoot>
  );
}

function confirmDelete(info: BackupInfo, onDone: () => void) {
  showModal(
    <ConfirmModal
      strTitle="Delete this backup?"
      strDescription={`${info.error ? info.name : `The backup from ${formatDate(info.createdAt)}`} is deleted from ${info.location}.`}
      strOKButtonText="Delete"
      bDestructiveWarning
      onOK={async () => {
        try {
          const res = await backend.backupDelete(info.locationId, info.name);
          if (!res.ok) notify(res.error ?? "Could not delete the backup");
        } catch (err) {
          notify(`Could not delete the backup: ${errorText(err)}`);
        } finally {
          onDone();
        }
      }}
    />,
  );
}

/** Back up the settings to a file, and restore them (e.g. after reinstalling SteamOS). */
export function BackupSection() {
  const [items, setItems] = useState<BackupInfo[] | null>(null);
  const [locations, setLocations] = useState<BackupLocation[]>([]);
  const [folder, setFolder] = useState<string | null>(null);
  const topButton = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await backend.backupList();
      setItems(res.items);
      setLocations(res.locations);
      setFolder(res.folder);
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** After a row goes away, keep the controller's focus in this section. */
  const afterDelete = () => {
    void load().then(() =>
      window.setTimeout(() => topButton.current?.querySelector<HTMLElement>("button, [tabindex]")?.focus(), 100),
    );
  };

  const drives = locations.filter((l) => l.removable).map((l) => l.label);
  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Backup</DialogControlsSectionHeader>
      <div ref={topButton}>
        <ButtonItem
          layout="below"
          description={
            drives.length
              ? `Save to this device (${folder ?? "~/Documents/Eclipse"}) or to the ${drives.join(", ")}.`
              : `Saved in ${folder ?? "~/Documents/Eclipse"}. Insert a microSD card or USB drive to keep a backup through a SteamOS reinstall.`
          }
          onClick={() => showModal(<CreateBackupModal locations={locations} onDone={() => void load()} />)}
        >
          Back up now
        </ButtonItem>
      </div>
      {items === null ? (
        <Field label="Looking for backups..." />
      ) : items.length === 0 ? (
        <Field label="No backups yet" description="Backups on a microSD card or USB drive show up here too." />
      ) : (
        items.map((info) => (
          // Y deletes: the main button restores, which is what people look for.
          <Focusable
            key={`${info.locationId}/${info.name}`}
            onOptionsActionDescription="Delete"
            onOptionsButton={() => confirmDelete(info, afterDelete)}
          >
            {info.error ? (
              <ButtonItem
                label={info.name}
                description={`${info.location} · ${info.error}`}
                onClick={() => confirmDelete(info, afterDelete)}
              >
                Delete
              </ButtonItem>
            ) : (
              <ButtonItem
                label={formatDate(info.createdAt)}
                description={`${describeBackup(info)} · Y: delete`}
                onClick={() => showModal(<RestoreModal info={info} onDone={() => void load()} />)}
              >
                Restore...
              </ButtonItem>
            )}
          </Focusable>
        ))
      )}
    </DialogControlsSection>
  );
}

/** Restore entry for first-run setup (a Quick Access panel row): shown only when a backup exists. */
export function RestoreFromBackupButton() {
  const [latest, setLatest] = useState<BackupInfo | null>(null);
  useEffect(() => {
    let alive = true;
    backend
      .backupList()
      .then((res) => alive && setLatest(res.items.find((item) => !item.error) ?? null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  if (!latest) return null;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label="Restore from a backup"
        description={`Latest: ${formatDate(latest.createdAt)} (${latest.location}). More in Settings → General → Backup.`}
        onClick={() => showModal(<RestoreModal info={latest} onDone={() => undefined} />)}
      >
        Restore...
      </ButtonItem>
    </PanelSectionRow>
  );
}
