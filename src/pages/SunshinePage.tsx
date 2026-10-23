import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  Navigation,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaCheckCircle, FaRegCircle } from "react-icons/fa";
import { backend } from "../backend";
import { ButtonRow } from "../components/ButtonRow";
import { InstallProgress } from "../components/InstallProgress";
import { SunshineDevicesSection } from "../components/SunshineDevicesSection";
import { SunshineOptionsSections } from "../components/SunshineOptionsSections";
import {
  confirmStopSunshine,
  createSunshineLoginAuto,
  endIncomingStream,
  installFeature,
  repairSunshine,
  revealSunshineLogin,
} from "../lib/actions";
import { ACCENT, formatDuration } from "../lib/format";
import { openEclipsePage } from "../lib/navigation";
import { flushSettings, loadInitialState, refreshIncoming, refreshSharing, updateSettings, useStore } from "../lib/store";
import { AUTO_OFF_OPTIONS, describeSunshine, repairReason, useSunshineStatus } from "../lib/sunshine";
import type { SunshineLoginState, SunshineStatus } from "../types";

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

/** Login state of the Sunshine web UI on this device, re-checked when Sunshine starts or stops. */
function useLoginState(running: boolean) {
  const [state, setState] = useState<SunshineLoginState | null>(null);
  const check = useCallback(async () => {
    const res = await backend.sunshineLoginState();
    setState(res.state);
    return res.state;
  }, []);
  useEffect(() => {
    void check();
  }, [check, running]);
  return { state, check };
}

function Step({ done, label, detail }: { done: boolean; label: string; detail?: string }) {
  return (
    <Field
      label={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {done ? (
            <FaCheckCircle color="#5ba32b" style={{ flexShrink: 0 }} />
          ) : (
            <FaRegCircle style={{ flexShrink: 0, opacity: 0.6 }} />
          )}
          <span style={{ opacity: done ? 0.7 : 1 }}>{label}</span>
        </span>
      }
      description={done ? undefined : detail}
    />
  );
}

/**
 * First-time checklist, hidden once a login is saved and a device is paired.
 * Sunshine being stopped alone does not bring it back: off is its normal state.
 */
function SetupSteps({ running, loginDone, paired }: { running: boolean; loginDone: boolean; paired: number | null }) {
  const hasDevice = (paired ?? 0) > 0;
  // Without a running Sunshine the paired devices are unknown (null): do not nag.
  if (loginDone && (paired === null || hasDevice)) return null;
  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Get started</DialogControlsSectionHeader>
      <Step done={running} label="Turn on Sunshine" detail="With the Sunshine switch above." />
      <Step
        done={loginDone}
        label="Save a Sunshine login"
        detail="Below. Eclipse uses it to pair devices and end streams from Gaming Mode."
      />
      <Step
        done={hasDevice}
        label="Pair a device"
        detail="Install Moonlight (moonlight-stream.org) on the other device and select this device there. Then enter the PIN it shows under Devices."
      />
    </DialogControlsSection>
  );
}

/** Streams from this device right now, with the action to end them. */
function ActivitySection() {
  const incoming = useStore((s) => s.incoming);
  const ending = useStore((s) => s.endingIncoming);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);
  if (!incoming?.appOpen) return null;

  const app = incoming.appName ?? "An app";
  let label: string;
  let description: string;
  if (incoming.clients > 0) {
    label = incoming.clients === 1 ? "A device is streaming from here" : `${incoming.clients} devices are streaming from here`;
    description = [incoming.appName, incoming.since ? formatDuration(now - incoming.since) : null]
      .filter(Boolean)
      .join(" · ");
  } else {
    label = `${app} is still open`;
    description = "No device is connected. A device can resume it, or close it to end the session.";
  }
  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Now</DialogControlsSectionHeader>
      <ButtonItem
        layout="below"
        label={<span style={{ borderLeft: `3px solid ${ACCENT.share}`, paddingLeft: 8 }}>{label}</span>}
        description={description || undefined}
        disabled={ending}
        onClick={() => void endIncomingStream()}
      >
        {ending ? "Working..." : incoming.clients > 0 ? "End stream" : "Close app"}
      </ButtonItem>
    </DialogControlsSection>
  );
}

export function SunshinePage() {
  const sunshine = useStore((s) => s.settings?.sunshine);
  const installing = useStore((s) => s.installing);
  const sharing = useStore((s) => s.sharing);
  const { status, pending, busy, refresh, setRunning, setAutostart } = useSunshineStatus(true);
  const running = Boolean(status?.running) && pending === null;
  const login = useLoginState(running);

  useEffect(() => {
    void refreshIncoming();
    if (running) void refreshSharing();
  }, [running, login.state]);

  if (!sunshine) return null;
  const repair = status ? repairReason(status) : null;
  // A saved login counts as done unless Sunshine says otherwise (it cannot tell while stopped).
  const loginDone =
    Boolean(sunshine.user && sunshine.passwordSet) &&
    (login.state === null || login.state === "ok" || login.state === "unreachable");
  const afterLoginChange = () => {
    void login.check();
    void refresh();
  };

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Sunshine on this device</DialogControlsSectionHeader>
        {!status ? (
          <Field label="Checking..." />
        ) : !status.installed ? (
          <>
            <Field
              label="Sunshine is not installed"
              description={`Sunshine lets other devices stream games from this one. ${INSTALL_DESCRIPTION}`}
            />
            <InstallButton label="Install Sunshine" onDone={refresh} />
          </>
        ) : (
          <>
            <ToggleField
              label="Sunshine"
              description={describeSunshine(status, pending)}
              checked={status.running}
              disabled={busy}
              onChange={(checked) =>
                checked ? void setRunning(true) : confirmStopSunshine(() => void setRunning(false))
              }
            />
            <ToggleField
              label="Start automatically"
              description="Off (default): Sunshine only runs when you turn it on here or in the Quick Access menu. On: it starts with the device."
              checked={status.autostart}
              disabled={busy}
              onChange={(checked) => void setAutostart(checked)}
            />
            <DropdownItem
              label="Turn off automatically"
              description="Saves battery: Sunshine turns itself off when nobody streams."
              rgOptions={AUTO_OFF_OPTIONS}
              selectedOption={sunshine.autoOff}
              onChange={(opt) => updateSettings({ sunshine: { autoOff: opt.data } }, 0)}
            />
            {status.unitEnabled && !status.autostart && (
              <ButtonItem
                layout="below"
                description="The Sunshine service is enabled outside of Eclipse, so it still starts on login."
                disabled={busy}
                onClick={() => void setAutostart(false)}
              >
                Disable service autostart
              </ButtonItem>
            )}
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
            <Field label="Installed" description={describeInstallation(status)} />
          </>
        )}
      </DialogControlsSection>

      {status?.installed && (
        <>
          <ActivitySection />
          <SetupSteps running={running} loginDone={loginDone} paired={running ? (sharing?.paired ?? null) : null} />
          <SunshineLoginForm state={login.state} onChanged={afterLoginChange} />
          {/* Re-check once a start/stop has settled, not while it is still in progress. */}
          <SunshineDevicesSection
            running={running}
            hasLogin={status.credentials}
            onChanged={() => void refreshSharing()}
          />
          <DialogControlsSection>
            <DialogControlsSectionHeader>Connect from another device</DialogControlsSectionHeader>
            <Field
              label={sharing?.address ? `This device: ${sharing.address}` : "Address shown once Sunshine runs"}
              description={
                sharing?.address
                  ? `Moonlight on the same network finds this device on its own. If it does not, press + (Add host) in Moonlight and enter ${sharing.address}.`
                  : "Connect to the same Wi-Fi or LAN as the other device."
              }
            />
            <ButtonItem
              layout="below"
              description="Checks the network, firewall and screen capture, and shows Sunshine's log."
              onClick={() => openEclipsePage("help", null, "sunshine")}
            >
              Run connection checks
            </ButtonItem>
            <ButtonItem
              layout="below"
              description="Sunshine's own settings page, for apps and everything else."
              onClick={() => Navigation.NavigateToExternalWeb(`https://localhost:${sunshine.port || 47990}`)}
            >
              Open Sunshine web UI
            </ButtonItem>
          </DialogControlsSection>
          <SunshineOptionsSections running={running} legacy={Boolean(status.native?.legacy)} />
        </>
      )}
    </>
  );
}

const LOGIN_STATE_TEXT: Record<SunshineLoginState, string> = {
  ok: "The saved login works.",
  needs_setup:
    "Sunshine has no login yet. Create one: Eclipse uses it to pair devices and end streams for you, and it opens Sunshine's web page.",
  invalid: "Sunshine rejected the saved login. Enter the correct username and password.",
  missing: "Sunshine already has a login. Enter it here.",
  unreachable: "Sunshine is not running, so the login cannot be checked.",
};

/**
 * Login of the Sunshine web UI on this device. A working login collapses to
 * one line; the form opens when there is something to fix or on Change.
 * When Sunshine has no login yet (fresh install), it can be created here.
 */
function SunshineLoginForm({ state, onChanged }: { state: SunshineLoginState | null; onChanged: () => void }) {
  const sunshine = useStore((s) => s.settings?.sunshine);
  const [user, setUser] = useState(sunshine?.user ?? "");
  const [password, setPassword] = useState("");
  const [port, setPort] = useState(String(sunshine?.port || 47990));
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  // Follow the saved values (e.g. after Forget or a reload).
  useEffect(() => setUser(sunshine?.user ?? ""), [sunshine?.user]);
  useEffect(() => setPort(String(sunshine?.port || 47990)), [sunshine?.port]);

  if (!sunshine) return null;
  const portNumber = Number(port) || 47990;
  const creating = state === "needs_setup";
  const dirty = user.trim() !== sunshine.user || password.length > 0 || portNumber !== (sunshine.port || 47990);
  const saved = Boolean(sunshine.user && sunshine.passwordSet);
  const showForm = editing || creating || (state !== null && state !== "ok" && state !== "unreachable") || !saved;

  const run = async (action: () => Promise<{ ok: boolean; text: string }>) => {
    setBusy(true);
    setFeedback(null);
    try {
      const result = await action();
      setFeedback(result);
      if (result.ok) setEditing(false);
    } finally {
      setBusy(false);
      // The page re-checks the login and what depends on it (devices, steps).
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
      const now = (await backend.sunshineLoginState()).state;
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

  const cancelEdit = () => {
    setUser(sunshine.user);
    setPassword("");
    setPort(String(sunshine.port || 47990));
    setFeedback(null);
    setEditing(false);
  };

  const statusText = feedback ? (
    <span style={{ color: feedback.ok ? "#5ba32b" : "#ff6b6b" }}>{feedback.text}</span>
  ) : state ? (
    LOGIN_STATE_TEXT[state]
  ) : (
    "Checking..."
  );

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>This device's Sunshine login</DialogControlsSectionHeader>
      <Field
        label={
          creating ? "Create a Sunshine login" : saved ? `Saved for user "${sunshine.user}"` : "No login saved in Eclipse"
        }
        description={statusText}
      />
      {!showForm ? (
        <ButtonRow
          buttons={[
            { label: "Show password", disabled: busy, onClick: () => void revealSunshineLogin() },
            { label: "Change", disabled: busy, onClick: () => setEditing(true) },
            { label: busy ? "Working..." : "Forget", disabled: busy, onClick: () => void forget() },
          ]}
        />
      ) : (
        <>
          {creating && (
            <>
              <ButtonItem
                layout="below"
                description="Eclipse picks a username and password, saves them and shows them to you once. Nothing to type."
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    if (await createSunshineLoginAuto()) setFeedback({ ok: true, text: "Login created and saved ✓" });
                  } finally {
                    setBusy(false);
                    onChanged();
                  }
                }}
              >
                Create login automatically
              </ButtonItem>
              <Field label="Or choose your own" bottomSeparator="none" />
            </>
          )}
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
                {
                  label: busy ? "Working..." : "Save",
                  disabled: busy || !dirty || !user.trim(),
                  onClick: () => void save(),
                },
                ...(editing ? [{ label: "Cancel", disabled: busy, onClick: cancelEdit }] : []),
                ...(saved && !editing ? [{ label: "Forget", disabled: busy, onClick: () => void forget() }] : []),
              ]}
            />
          )}
        </>
      )}
    </DialogControlsSection>
  );
}
