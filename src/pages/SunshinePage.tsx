import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  Field,
  Navigation,
  ToggleField,
} from "@decky/ui";
import { CommitTextField } from "../components/CommitTextField";
import { InstallProgress } from "../components/InstallProgress";
import { showSunshinePinModal } from "../components/SunshinePinModal";
import { installFeature } from "../lib/actions";
import { updateSettings, useStore } from "../lib/store";
import { useSunshineStatus } from "../lib/sunshine";

export function SunshinePage() {
  const sunshine = useStore((s) => s.settings?.sunshine);
  const installing = useStore((s) => s.installing);
  const { status, busy, refresh, setRunning, setAutostart } = useSunshineStatus(true);

  if (!sunshine) return null;

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Sunshine server on this device</DialogControlsSectionHeader>
        {!status ? (
          <Field description="Checking..." />
        ) : !status.installed ? (
          <>
            <Field
              label="Sunshine is not installed"
              description="Installs the dev.lizardbyte.app.Sunshine Flatpak from Flathub for your user."
            />
            {installing === "sunshine" ? (
              <InstallProgress />
            ) : (
              <ButtonItem
                layout="below"
                disabled={installing !== null}
                onClick={async () => {
                  if (await installFeature("sunshine")) await refresh();
                }}
              >
                Install Sunshine
              </ButtonItem>
            )}
          </>
        ) : (
          <>
            <Field label="Installation" description={status.unit ? `systemd user service ${status.unit}` : status.kind ?? ""} />
            <ToggleField
              label="Running"
              checked={status.running}
              disabled={busy}
              onChange={(checked) => void setRunning(checked)}
            />
            <ToggleField
              label="Start automatically"
              description="Off: Sunshine only runs when you start it here or in the Quick Access menu. On: it starts when the plugin loads (and its user service is enabled)."
              checked={status.autostart}
              disabled={busy}
              onChange={(checked) => void setAutostart(checked)}
            />
            {status.unitEnabled && !status.autostart && (
              <ButtonItem
                layout="below"
                description="The Sunshine systemd service is enabled outside of Eclipse, so it still starts on login."
                disabled={busy}
                onClick={() => void setAutostart(false)}
              >
                Disable service autostart
              </ButtonItem>
            )}
            <ButtonItem layout="below" disabled={!status.running || !status.credentials} onClick={showSunshinePinModal}>
              Pair a client (enter PIN)
            </ButtonItem>
            <ButtonItem
              layout="below"
              onClick={() => Navigation.NavigateToExternalWeb(`https://localhost:${sunshine.port || 47990}`)}
            >
              Open Sunshine web UI
            </ButtonItem>
          </>
        )}
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Web UI credentials</DialogControlsSectionHeader>
        <Field description="The username and password you created in Sunshine's web UI. Stored locally with private file permissions." />
        <CommitTextField
          label="Username"
          value={sunshine.user}
          onCommit={(user) => updateSettings({ sunshine: { user: user.trim() } }, 0)}
        />
        <CommitTextField
          label="Password"
          bIsPassword
          placeholder={sunshine.passwordSet ? "•••••••• (saved)" : ""}
          value=""
          onCommit={(password) => password && updateSettings({ sunshine: { password } }, 0)}
        />
        <CommitTextField
          label="Web UI port"
          mustBeNumeric
          value={String(sunshine.port || 47990)}
          onCommit={(port) => updateSettings({ sunshine: { port: Number(port) || 47990 } }, 0)}
        />
      </DialogControlsSection>
    </>
  );
}
