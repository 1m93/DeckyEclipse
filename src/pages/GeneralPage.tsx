import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { backend } from "../backend";
import { AppsSection } from "../components/AppsSection";
import { BackupSection } from "../components/BackupSection";
import { CommitTextField } from "../components/CommitTextField";
import { notify, saveFeatures } from "../lib/actions";
import { isShortcutPresent, recreateLauncherShortcut } from "../lib/steam";
import { flushSettings, setState, updateSettings, useStore } from "../lib/store";
import type { MoonlightSource, Settings } from "../types";

const SOURCES: { data: MoonlightSource; label: string }[] = [
  { data: "auto", label: "Automatic (Flatpak, then native)" },
  { data: "flatpak", label: "Flatpak" },
  { data: "native", label: "Native binary in PATH" },
  { data: "custom", label: "Custom command" },
];

/** Settings that only matter when streaming from a PC with Moonlight. */
function MoonlightSettings({ settings }: { settings: Settings }) {
  const moonlight = useStore((s) => s.moonlight);
  const { behavior } = settings;
  const launcherId = settings.shortcuts.launcherAppId;

  const changeMoonlight = async (patch: Partial<Settings["moonlight"]>) => {
    updateSettings({ moonlight: patch }, 0);
    await flushSettings();
    setState({ moonlight: await backend.refreshMoonlight() });
  };

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Moonlight</DialogControlsSectionHeader>
        <Field
          label="Status"
          description={
            moonlight?.installed
              ? `Found (${moonlight.source}${moonlight.version ? ` ${moonlight.version}` : ""})`
              : (moonlight?.error ?? "Not found")
          }
        />
        <DropdownItem
          label="Moonlight source"
          rgOptions={SOURCES}
          selectedOption={settings.moonlight.source}
          onChange={(opt) => void changeMoonlight({ source: opt.data })}
        />
        {settings.moonlight.source === "custom" && (
          <CommitTextField
            label="Custom command"
            description="Command used instead of Moonlight, e.g. /home/deck/Applications/Moonlight.AppImage"
            value={settings.moonlight.customCommand}
            onCommit={(cmd) => void changeMoonlight({ customCommand: cmd })}
          />
        )}
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Wake-on-LAN</DialogControlsSectionHeader>
        <ToggleField
          label="Wake hosts automatically"
          description="When streaming to an offline host, send a magic packet and wait for it to come online."
          checked={behavior.autoWake}
          onChange={(checked) => updateSettings({ behavior: { autoWake: checked } })}
        />
        <SliderField
          label="Wake timeout"
          value={behavior.wakeTimeoutS}
          min={15}
          max={180}
          step={5}
          showValue
          valueSuffix=" s"
          disabled={!behavior.autoWake}
          onChange={(value) => updateSettings({ behavior: { wakeTimeoutS: value } })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Interface</DialogControlsSectionHeader>
        <ToggleField
          label="Ask before closing the game on the host"
          description="When End stream also closes the game on the host, confirm first, since unsaved progress there may be lost."
          checked={behavior.confirmEndStream}
          onChange={(checked) => updateSettings({ behavior: { confirmEndStream: checked } })}
        />
        <ToggleField
          label="Show offline hosts"
          checked={behavior.showOffline}
          onChange={(checked) => updateSettings({ behavior: { showOffline: checked } })}
        />
        <SliderField
          label="Status refresh interval"
          description="How often host status is checked while the Quick Access menu is open. Paused during streams."
          value={behavior.pollIntervalS}
          min={3}
          max={60}
          step={1}
          showValue
          valueSuffix=" s"
          onChange={(value) => updateSettings({ behavior: { pollIntervalS: value } })}
        />
        <CommitTextField
          label="Device name"
          description="Name sent to Sunshine when Eclipse enters a pairing PIN automatically."
          placeholder="Steam Deck"
          value={settings.sunshine.deviceName}
          onCommit={(name) => updateSettings({ sunshine: { deviceName: name.trim() } }, 0)}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Launcher shortcut</DialogControlsSectionHeader>
        <Field
          label="Moonlight (Eclipse)"
          description={
            launcherId && isShortcutPresent(launcherId)
              ? `Non-Steam shortcut ${launcherId}. Eclipse launches streams through it so Gaming Mode can show Moonlight.`
              : "Not created yet. It is created automatically on the first stream."
          }
        />
        <ButtonItem
          layout="below"
          onClick={async () => {
            try {
              const id = await recreateLauncherShortcut();
              notify(`Launcher shortcut ready (${id})`);
            } catch (err) {
              notify(`Failed: ${err instanceof Error ? err.message : String(err)}`);
            }
          }}
        >
          Recreate launcher shortcut
        </ButtonItem>
      </DialogControlsSection>
    </>
  );
}

export function GeneralPage() {
  const settings = useStore((s) => s.settings);
  if (!settings) return null;
  const { features } = settings;
  const toggleFeature = (feature: "moonlight" | "sunshine", enabled: boolean) =>
    void saveFeatures({ moonlight: features.moonlight, sunshine: features.sunshine, [feature]: enabled });

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Features</DialogControlsSectionHeader>
        <ToggleField
          label="Play: stream games from a PC (Moonlight)"
          description="Hosts and streaming controls. Moonlight is only installed when you ask for it."
          checked={features.moonlight}
          onChange={(checked) => toggleFeature("moonlight", checked)}
        />
        <ToggleField
          label="Share: let other devices stream from here (Sunshine)"
          description="Controls for a Sunshine server on this device. Turning this off also turns off its autostart."
          checked={features.sunshine}
          onChange={(checked) => toggleFeature("sunshine", checked)}
        />
      </DialogControlsSection>

      <AppsSection />

      {features.sunshine && (
        <DialogControlsSection>
          <DialogControlsSectionHeader>Notifications</DialogControlsSectionHeader>
          <ToggleField
            label="When a device streams from here"
            description="Notify when another device starts or stops streaming from this one, even with the Quick Access menu closed."
            checked={settings.sunshine.notifySessions}
            onChange={(checked) => updateSettings({ sunshine: { notifySessions: checked } }, 0)}
          />
          <ToggleField
            label="When a device wants to pair"
            description="Notify when a device asks to pair, so its PIN can be entered here. Needs a Sunshine login saved in Eclipse."
            checked={settings.sunshine.notifyPairing}
            onChange={(checked) => updateSettings({ sunshine: { notifyPairing: checked } }, 0)}
          />
        </DialogControlsSection>
      )}

      {features.moonlight && <MoonlightSettings settings={settings} />}

      <BackupSection />
    </>
  );
}
