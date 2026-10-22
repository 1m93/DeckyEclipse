import { ButtonItem, DialogControlsSection, DialogControlsSectionHeader, Field, Navigation, ToggleField } from "@decky/ui";
import { useState } from "react";
import { checkPluginUpdate } from "../lib/actions";
import { updateSettings, useStore } from "../lib/store";
import type { PluginUpdateCheck } from "../types";

/**
 * Plugin version and GitHub release check. Installing stays with Decky:
 * the plugin folder is owned by root, so the plugin cannot update itself.
 */
export function PluginUpdateSection() {
  const version = useStore((s) => s.pluginVersion);
  const updates = useStore((s) => s.settings?.updates);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<PluginUpdateCheck | null>(null);

  const check = async () => {
    setChecking(true);
    setResult(await checkPluginUpdate(false));
    setChecking(false);
  };

  const lastCheck = updates?.lastCheck ? new Date(updates.lastCheck * 1000).toLocaleString() : "never";
  let status = `Last checked: ${lastCheck}`;
  if (result) {
    status = !result.ok
      ? (result.error ?? "Update check failed")
      : result.available
        ? `Version ${result.latest} is available.`
        : `You have the latest version (${result.current}).`;
  }

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Plugin updates</DialogControlsSectionHeader>
      <Field label="Installed version" description={version ?? "unknown"} />
      <Field label="Status" description={status} />
      <ButtonItem layout="below" disabled={checking} onClick={() => void check()}>
        {checking ? "Checking..." : "Check for updates"}
      </ButtonItem>
      {result?.available && (
        <>
          {result.notes && (
            <Field label={`What's new in ${result.latest}`} description={result.notes} />
          )}
          {result.downloadUrl && (
            <Field
              label="Install"
              description={`In Decky: Settings → Developer → Third-Party Plugins, install from URL with: ${result.downloadUrl}`}
            />
          )}
          {result.url && (
            <ButtonItem layout="below" onClick={() => Navigation.NavigateToExternalWeb(result.url!)}>
              Open release page
            </ButtonItem>
          )}
          <ButtonItem
            layout="below"
            onClick={() => updateSettings({ updates: { skippedVersion: result.latest ?? "" } }, 0)}
          >
            Skip this version
          </ButtonItem>
        </>
      )}
      <ToggleField
        label="Check automatically"
        description="Look for a new release once a day and show a notification. Plugins from the Decky store are updated by Decky itself."
        checked={updates?.autoCheck ?? true}
        onChange={(checked) => updateSettings({ updates: { autoCheck: checked } }, 0)}
      />
    </DialogControlsSection>
  );
}
