import { ButtonItem, Field, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useEffect, useState } from "react";
import { backend } from "../backend";
import { FEATURE_LABELS, installFeature, saveFeatures } from "../lib/actions";
import { useStore } from "../lib/store";
import type { Feature } from "../types";
import { InstallProgress } from "./InstallProgress";

/** First-run choice of features. Nothing is installed or started from here. */
export function FeatureSetup() {
  const moonlightInstalled = useStore((s) => s.moonlight?.installed ?? false);
  const [moonlight, setMoonlight] = useState(moonlightInstalled);
  const [sunshine, setSunshine] = useState(false);
  const [saving, setSaving] = useState(false);

  // Pre-select what is already installed; the user still confirms.
  useEffect(() => {
    let alive = true;
    void backend.sunshineStatus().then((s) => alive && s.installed && setSunshine(true));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <PanelSection title="Welcome to Eclipse">
      <PanelSectionRow>
        <Field description="Choose what you use. You can change this later in Settings → General." bottomSeparator="none" />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Play games from a PC"
          description="Moonlight client: stream from a computer running Sunshine."
          checked={moonlight}
          onChange={setMoonlight}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Share this device's games"
          description="Sunshine host: let other devices stream from here. It only runs when you start it, unless you turn on autostart."
          checked={sunshine}
          onChange={setSunshine}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={saving || (!moonlight && !sunshine)}
          onClick={async () => {
            setSaving(true);
            await saveFeatures({ moonlight, sunshine });
            setSaving(false);
          }}
        >
          Continue
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

const INSTALL_TEXT: Record<Feature, string> = {
  moonlight: "Moonlight is needed to stream from your PC. It can be installed from Flathub for your user.",
  sunshine:
    "Sunshine is needed to share this device. Eclipse installs a build of it that can stream Gaming Mode (asks for your password once).",
};

/** Shown for an enabled feature whose app is missing. Installs only on request. */
export function InstallSection({ feature }: { feature: Feature }) {
  const installing = useStore((s) => s.installing);
  const label = FEATURE_LABELS[feature];

  return (
    <PanelSection title={label}>
      <PanelSectionRow>
        <Field label={`${label} is not installed`} description={INSTALL_TEXT[feature]} bottomSeparator="none" />
      </PanelSectionRow>
      <PanelSectionRow>
        {installing === feature ? (
          <InstallProgress />
        ) : (
          <ButtonItem layout="below" disabled={installing !== null} onClick={() => void installFeature(feature)}>
            Install {label}
          </ButtonItem>
        )}
      </PanelSectionRow>
    </PanelSection>
  );
}
