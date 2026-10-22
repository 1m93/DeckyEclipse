import { ButtonItem, DialogControlsSection, DialogControlsSectionHeader, Field } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaCheckCircle, FaQuestionCircle, FaTimesCircle } from "react-icons/fa";
import { backend } from "../backend";
import type { DiagnosticCheck, SunshineDiagnostics } from "../types";
import { LogViewer } from "./LogViewer";

function CheckIcon({ ok }: { ok: boolean | null }) {
  if (ok === true) return <FaCheckCircle color="#5ba32b" style={{ flexShrink: 0 }} />;
  if (ok === false) return <FaTimesCircle color="#ff6b6b" style={{ flexShrink: 0 }} />;
  return <FaQuestionCircle color="#8b929a" style={{ flexShrink: 0 }} />;
}

function CheckRow({ check }: { check: DiagnosticCheck }) {
  const text = check.ok === false ? check.hint || check.detail : check.detail || (check.ok === null ? check.hint : "");
  return (
    <Field
      label={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <CheckIcon ok={check.ok} />
          {check.label}
        </span>
      }
      description={text || undefined}
      focusable
    />
  );
}

/**
 * Helps another device find and reach Sunshine here: the address to type in
 * Moonlight, checks for the usual causes, and Sunshine's own log.
 * `refreshKey` re-runs the checks (e.g. after Sunshine was started);
 * `paused` holds them while a start/stop is still in progress.
 */
export function SunshineConnectSection({ refreshKey, paused = false }: { refreshKey: unknown; paused?: boolean }) {
  const [result, setResult] = useState<SunshineDiagnostics | null>(null);
  const [running, setRunning] = useState(false);
  const [showLog, setShowLog] = useState(false);

  const runChecks = useCallback(async () => {
    setRunning(true);
    try {
      setResult(await backend.sunshineDiagnostics());
    } finally {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    if (!paused) void runChecks();
  }, [runChecks, refreshKey, paused]);

  const address = result?.addresses[0]?.address;
  const problems = result?.checks.filter((c) => c.ok === false).length ?? 0;

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Connect from another device</DialogControlsSectionHeader>
      <Field
        label={address ? `This device: ${address}` : "This device has no network address"}
        description={
          address
            ? `If it does not show up in Moonlight on the other device, add it by hand: in Moonlight press + (Add host) and enter ${address}.`
            : "Connect to the same Wi-Fi or LAN as the other device."
        }
      />
      {result?.addresses.slice(1).length ? (
        <Field
          label="Other addresses"
          description={result.addresses
            .slice(1)
            .map((a) => `${a.address} (${a.interface})`)
            .join(", ")}
        />
      ) : null}
      {result?.checks.map((check) => <CheckRow key={check.id} check={check} />)}
      <ButtonItem
        layout="below"
        description={result ? (problems ? `${problems} problem(s) found.` : "No problems found.") : undefined}
        disabled={running}
        onClick={() => void runChecks()}
      >
        {running ? "Checking..." : "Run checks again"}
      </ButtonItem>
      <ButtonItem layout="below" onClick={() => setShowLog((v) => !v)}>
        {showLog ? "Hide Sunshine log" : "Show Sunshine log"}
      </ButtonItem>
      {showLog && <LogViewer initialSource="sunshine" />}
    </DialogControlsSection>
  );
}
