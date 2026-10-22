import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  Field,
  Navigation,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import { ButtonRow } from "../components/ButtonRow";
import { InstallProgress } from "../components/InstallProgress";
import { SunshineConnectSection } from "../components/SunshineConnectSection";
import { SunshineDevicesSection } from "../components/SunshineDevicesSection";
import { installFeature, repairSunshine } from "../lib/actions";
import { flushSettings, loadInitialState, updateSettings, useStore } from "../lib/store";
import type { SunshineLoginState, SunshineStatus } from "../types";
import { describeSunshine, useSunshineStatus } from "../lib/sunshine";

const INSTALL_DESCRIPTION =
  "Installs Sunshine for Gaming Mode: Sunshine's official build, set up so it can stream Gaming Mode " +
  "without a black picture. Asks for your password once.";

function describeInstallation(status: SunshineStatus): string {
  if (status.kind === "eclipse") {
    const version = status.native?.version ? ` ${status.native.version}` : "";
    return `Sunshine for Gaming Mode${version}`;
  }
  if (status.unit) return `systemd user service ${status.unit}`;
  return status.kind ?? "";
}

/** Why Sunshine for Gaming Mode needs a repair, or null. */
function repairReason(status: SunshineStatus): string | null {
  if (status.kind !== "eclipse" || !status.native) return null;
  if (!status.native.captureReady) return "Sunshine is not allowed to capture the screen, so streams fail.";
  if (status.native.helpersOutdated) return "This Eclipse version brings a newer Gaming Mode fix for Sunshine.";
  return null;
}

function InstallButton({ label, onDone }: { label: string; onDone: () => Promise<unknown> }) {
  const installing = useStore((s) => s.installing);
  if (installing === "sunshine") return <InstallProgress />;
  return (
    <ButtonItem
      layout="below"
      disabled={installing !== null}
      onClick={async () => {
        if (await installFeature("sunshine")) await onDone();
      }}
    >
      {label}
    </ButtonItem>
  );
}

export function SunshinePage() {
  const sunshine = useStore((s) => s.settings?.sunshine);
  const installing = useStore((s) => s.installing);
  const { status, pending, busy, refresh, setRunning, setAutostart } = useSunshineStatus(true);

  // Install, update and repair change what is installed.
  useEffect(() => {
    if (!installing) void refresh();
  }, [installing, refresh]);

  if (!sunshine) return null;
  const repair = status ? repairReason(status) : null;

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Sunshine server on this device</DialogControlsSectionHeader>
        {!status ? (
          <Field label="Checking..." />
        ) : !status.installed ? (
          <>
            <Field label="Sunshine is not installed" description={INSTALL_DESCRIPTION} />
            <InstallButton label="Install Sunshine" onDone={refresh} />
          </>
        ) : (
          <>
            <Field label="Installation" description={describeInstallation(status)} />
            {status.kind === "flatpak" && (
              <>
                <Field
                  label="The Flatpak cannot stream Gaming Mode"
                  description={`Other devices get an error or a black picture. ${INSTALL_DESCRIPTION}`}
                />
                <InstallButton label="Install Sunshine for Gaming Mode" onDone={refresh} />
              </>
            )}
            {repair &&
              (installing === "sunshine" ? (
                <InstallProgress />
              ) : (
                <ButtonItem
                  layout="below"
                  label="Repair needed"
                  description={`${repair} Repair reinstalls the same version and asks for your password.`}
                  disabled={installing !== null}
                  onClick={async () => {
                    if (await repairSunshine()) await refresh();
                  }}
                >
                  Repair
                </ButtonItem>
              ))}
            <ToggleField
              label="Running"
              description={describeSunshine(status, pending)}
              checked={status.running}
              disabled={busy}
              onChange={(checked) => void setRunning(checked)}
            />
            <ToggleField
              label="Start automatically"
              description="Off (default): Sunshine only runs when you start it here or in the Quick Access menu. On: it starts with the device (its user service is enabled)."
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
            <ButtonItem
              layout="below"
              onClick={() => Navigation.NavigateToExternalWeb(`https://localhost:${sunshine.port || 47990}`)}
            >
              Open Sunshine web UI
            </ButtonItem>
          </>
        )}
      </DialogControlsSection>

      {/* Re-check once a start/stop has settled, not while it is still in progress. */}
      {status?.installed && (
        <SunshineDevicesSection running={status.running && pending === null} hasLogin={status.credentials} />
      )}

      {status?.installed && <SunshineConnectSection refreshKey={status.running} paused={pending !== null} />}

      <SunshineLoginForm running={Boolean(status?.running) && pending === null} onChanged={() => void refresh()} />
    </>
  );
}

const LOGIN_STATE_TEXT: Record<SunshineLoginState, string> = {
  ok: "The saved login works.",
  needs_setup: "Sunshine has no login yet. Create one to pair devices and manage Sunshine from here.",
  invalid: "Sunshine rejected the saved login. Enter the correct username and password.",
  missing: "Sunshine already has a login. Enter it here.",
  unreachable: "Sunshine is not running, so the login cannot be checked.",
};

/**
 * Login of the Sunshine web UI on this device, with an explicit Save. When
 * Sunshine has no login yet (fresh install), the login can be created here.
 */
function SunshineLoginForm({ running, onChanged }: { running: boolean; onChanged: () => void }) {
  const sunshine = useStore((s) => s.settings?.sunshine);
  const [state, setState] = useState<SunshineLoginState | null>(null);
  const [user, setUser] = useState(sunshine?.user ?? "");
  const [password, setPassword] = useState("");
  const [port, setPort] = useState(String(sunshine?.port || 47990));
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  const checkState = useCallback(async () => {
    const res = await backend.sunshineLoginState();
    setState(res.state);
    return res.state;
  }, []);

  // Re-check whenever Sunshine starts or stops.
  useEffect(() => {
    void checkState();
  }, [checkState, running]);

  // Follow the saved values (e.g. after Forget or a reload).
  useEffect(() => setUser(sunshine?.user ?? ""), [sunshine?.user]);
  useEffect(() => setPort(String(sunshine?.port || 47990)), [sunshine?.port]);

  if (!sunshine) return null;
  const portNumber = Number(port) || 47990;
  const creating = state === "needs_setup";
  const dirty = user.trim() !== sunshine.user || password.length > 0 || portNumber !== (sunshine.port || 47990);
  const saved = Boolean(sunshine.user && sunshine.passwordSet);

  const run = async (action: () => Promise<{ ok: boolean; text: string }>) => {
    setBusy(true);
    setFeedback(null);
    try {
      setFeedback(await action());
    } finally {
      setBusy(false);
      await checkState();
      // Other controls on the page (e.g. paired devices) depend on the login.
      onChanged();
    }
  };

  const create = () =>
    run(async () => {
      const res = await backend.sunshineCreateLogin(user.trim(), password);
      if (!res.ok) return { ok: false, text: res.error ?? "Could not create the login" };
      setPassword("");
      await loadInitialState(true);
      return { ok: true, text: "Login created and saved ✓ Use it for Sunshine's web page too." };
    });

  const save = () =>
    run(async () => {
      updateSettings({ sunshine: { user: user.trim(), port: portNumber, ...(password ? { password } : {}) } }, 0);
      await flushSettings();
      setPassword("");
      // Saving and checking in one step tells the user right away if it works.
      const now = await checkState();
      return now === "ok"
        ? { ok: true, text: "Saved ✓ The login works." }
        : { ok: false, text: `Saved, but: ${LOGIN_STATE_TEXT[now]}` };
    });

  const forget = () =>
    run(async () => {
      updateSettings({ sunshine: { user: "", password: null } }, 0);
      await flushSettings();
      setPassword("");
      return { ok: true, text: "Login removed from Eclipse (Sunshine keeps it)." };
    });

  const statusText = feedback ? (
    <span style={{ color: feedback.ok ? "#5ba32b" : "#ff6b6b" }}>{feedback.text}</span>
  ) : state ? (
    LOGIN_STATE_TEXT[state]
  ) : (
    "Checking..."
  );

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Sunshine login</DialogControlsSectionHeader>
      <Field
        label={
          creating ? "Create a Sunshine login" : saved ? `Saved for user "${sunshine.user}"` : "No login saved in Eclipse"
        }
        description={statusText}
      />
      <TextField label="Username" value={user} onChange={(e) => setUser(e.target.value)} />
      <TextField
        label="Password"
        bIsPassword
        placeholder={!creating && sunshine.passwordSet ? "•••••••• (saved, leave empty to keep)" : ""}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {!creating && (
        <TextField label="Web UI port" mustBeNumeric value={port} onChange={(e) => setPort(e.target.value)} />
      )}
      {creating ? (
        <ButtonRow
          buttons={[
            {
              label: busy ? "Creating..." : "Create login",
              disabled: busy || !user.trim() || !password,
              onClick: () => void create(),
            },
          ]}
        />
      ) : (
        <ButtonRow
          buttons={[
            { label: busy ? "Working..." : "Save", disabled: busy || !dirty || !user.trim(), onClick: () => void save() },
            {
              label: "Check",
              disabled: busy,
              onClick: () =>
                void run(async () => {
                  const now = await checkState();
                  return { ok: now === "ok", text: LOGIN_STATE_TEXT[now] };
                }),
            },
            { label: "Forget", disabled: busy || !saved, onClick: () => void forget() },
          ]}
        />
      )}
    </DialogControlsSection>
  );
}
