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
  ModalRoot,
  Spinner,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "../backend";
import { notify, refreshSunshineOptions } from "../lib/actions";
import { addGamesToSunshine } from "../lib/covers";
import { getState } from "../lib/store";
import type { SteamGame, SunshineApp } from "../types";
import { ButtonRow } from "./ButtonRow";
import { ApplyOptionsRow } from "./SunshineOptionsSections";

async function sunshineRunning(): Promise<boolean> {
  try {
    return (await backend.sunshineStatus()).running;
  } catch {
    return false;
  }
}

/** Pick installed Steam games to offer to other devices. */
function SteamGamesModal({ onDone, closeModal }: { onDone: () => void; closeModal?: () => void }) {
  const [games, setGames] = useState<SteamGame[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(() => new Set());
  const [progress, setProgress] = useState<string | null>(null);
  const [installed, setInstalled] = useState(0);
  const busy = progress !== null;

  useEffect(() => {
    let alive = true;
    backend
      .sunshineGames()
      .then((res) => {
        if (!alive) return;
        if (!res.ok) {
          setError(res.error ?? "Could not read the Steam library");
          setGames([]);
          return;
        }
        const present = new Set(res.apps.map((app) => app.steamAppId));
        setInstalled(res.games.length);
        setGames(res.games.filter((game) => !present.has(game.appId)));
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setError(err instanceof Error ? err.message : String(err));
        setGames([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const toggle = (appId: number, on: boolean) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(appId);
      else next.delete(appId);
      return next;
    });

  const add = async () => {
    const selected = (games ?? []).filter((game) => chosen.has(game.appId));
    if (selected.length === 0) return;
    setProgress(`Preparing covers... 0 of ${selected.length}`);
    try {
      const res = await addGamesToSunshine(selected, (done, total) =>
        setProgress(`Preparing covers... ${done} of ${total}`),
      );
      if (!res.ok) {
        notify(res.error ?? "Could not add the games");
        return;
      }
      const what = res.added === 1 ? "Added 1 game to Sunshine" : `Added ${res.added} games to Sunshine`;
      notify(getState().sunshineOptions?.needsRestart || (await sunshineRunning()) ? `${what}. Restart Sunshine to apply.` : `${what}.`);
      onDone();
      closeModal?.();
    } catch (err) {
      notify(`Could not add the games: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setProgress(null);
    }
  };

  const allChosen = games !== null && games.length > 0 && chosen.size === games.length;
  const buttons = [
    {
      label: chosen.size > 0 ? `Add ${chosen.size} ${chosen.size === 1 ? "game" : "games"}` : "Add",
      disabled: busy || chosen.size === 0,
      onClick: () => void add(),
    },
    ...(games && games.length > 1
      ? [
          {
            label: allChosen ? "Select none" : "Select all",
            disabled: busy,
            onClick: () => setChosen(allChosen ? new Set<number>() : new Set(games.map((g) => g.appId))),
          },
        ]
      : []),
  ];
  return (
    // Adding writes covers one by one: B must not leave it half done.
    <ModalRoot onCancel={() => !busy && closeModal?.()} closeModal={() => !busy && closeModal?.()}>
      <DialogHeader>Add Steam games</DialogHeader>
      <DialogBody>
        {games === null ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Spinner style={{ width: 20, height: 20 }} />
            Reading the Steam library...
          </div>
        ) : games.length === 0 ? (
          <div style={{ opacity: 0.8 }}>
            {error ??
              (installed > 0
                ? "Every installed Steam game is already in the list."
                : "No installed Steam games found. Games added to Steam as non-Steam shortcuts are not listed.")}
          </div>
        ) : (
          <Focusable style={{ display: "flex", flexDirection: "column" }} flow-children="column">
            <div style={{ opacity: 0.8, marginBottom: 6 }}>
              Other devices see these in Moonlight and start them directly. Only installed games are listed.
            </div>
            {/* Above the list: a long list would put the buttons out of reach of the D-pad. */}
            {progress && <div style={{ opacity: 0.8 }}>{progress}</div>}
            <ButtonRow buttons={buttons} />
            {games.map((game) => (
              <ToggleField
                key={game.appId}
                label={game.name}
                checked={chosen.has(game.appId)}
                disabled={busy}
                onChange={(on) => toggle(game.appId, on)}
              />
            ))}
          </Focusable>
        )}
      </DialogBody>
      <DialogFooter>
        <ButtonRow buttons={[{ label: "Cancel", disabled: busy, onClick: () => closeModal?.() }]} />
      </DialogFooter>
    </ModalRoot>
  );
}

function confirmRemove(app: SunshineApp, onOK: () => void) {
  showModal(
    <ConfirmModal
      strTitle={`Remove "${app.name}"?`}
      strDescription="Other devices no longer see this game in Moonlight. The game stays installed here."
      strOKButtonText="Remove"
      onOK={onOK}
    />,
  );
}

/** The apps other devices see in Moonlight, with Steam games added from here. */
export function SunshineGamesSection({ running }: { running: boolean }) {
  const [apps, setApps] = useState<SunshineApp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const addButton = useRef<HTMLDivElement>(null);
  // Only the latest load is shown: an older one may finish last.
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const res = await backend.sunshineGames();
      if (seq !== loadSeq.current) return;
      setApps(res.ok ? res.apps : []);
      setError(res.ok ? null : (res.error ?? "Could not read Sunshine's app list"));
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setApps([]);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, running]);

  const changed = () => {
    void load();
    // A running Sunshine reads its app list at startup: the restart row appears.
    void refreshSunshineOptions();
  };

  const remove = async (app: SunshineApp) => {
    if (app.steamAppId === null) return;
    setRemoving(app.steamAppId);
    try {
      const res = await backend.sunshineRemoveGames([app.steamAppId]);
      if (!res.ok) notify(res.error ?? "Could not remove the game");
      else notify(res.needsRestart ? `Removed "${app.name}". Restart Sunshine to apply.` : `Removed "${app.name}".`);
    } catch (err) {
      notify(`Could not remove the game: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRemoving(null);
      changed();
      // The removed row disappears: keep the controller's focus in this section.
      window.setTimeout(() => addButton.current?.querySelector<HTMLElement>("button, [tabindex]")?.focus(), 100);
    }
  };

  const steamGames = apps?.filter((app) => app.steamAppId !== null) ?? [];
  const others = apps?.filter((app) => app.steamAppId === null) ?? [];
  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Games for other devices</DialogControlsSectionHeader>
      <ApplyOptionsRow running={running} />
      {apps === null ? (
        <Field label="Reading Sunshine's app list..." />
      ) : error ? (
        <ButtonItem layout="below" label="The app list is not available" description={error} onClick={() => void load()}>
          Try again
        </ButtonItem>
      ) : (
        <>
          <Field
            label={others.length ? `Also offered: ${others.map((a) => a.name).join(", ")}` : "Apps"}
            description="Moonlight on the other device lists these apps. A Steam game added here starts straight into the game."
          />
          <div ref={addButton}>
            <ButtonItem layout="below" onClick={() => showModal(<SteamGamesModal onDone={changed} />)}>
              Add Steam games...
            </ButtonItem>
          </div>
          {steamGames.map((app) => (
            <ButtonItem
              key={app.index}
              label={app.name}
              description={app.hasCover ? "Steam game" : "Steam game · no cover found"}
              disabled={removing !== null}
              onClick={() => confirmRemove(app, () => void remove(app))}
            >
              {removing === app.steamAppId ? "Removing..." : "Remove"}
            </ButtonItem>
          ))}
        </>
      )}
    </DialogControlsSection>
  );
}
