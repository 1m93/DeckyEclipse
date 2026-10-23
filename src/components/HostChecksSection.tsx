import { ButtonItem, DialogControlsSection, DialogControlsSectionHeader, DropdownItem, Field } from "@decky/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { backend } from "../backend";
import { wakeHost } from "../lib/actions";
import { openEclipsePage } from "../lib/navigation";
import { useStore } from "../lib/store";
import type { DiagnosticCheck } from "../types";
import { ButtonRow } from "./ButtonRow";
import { showPairModal } from "./PairModal";
import { CheckRow } from "./SunshineConnectSection";

/** Step-by-step checks of the connection to one host, with the fixes at hand. */
export function HostChecksSection({ initialHost, nonce }: { initialHost: string | null; nonce: number }) {
  const hosts = useStore((s) => s.hosts);
  const [selected, setSelected] = useState<string | null>(initialHost ?? hosts[0]?.uuid ?? null);
  const [checks, setChecks] = useState<DiagnosticCheck[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // Only the latest run may show its result (switching hosts starts a new one).
  const latest = useRef(0);

  useEffect(() => {
    if (initialHost) setSelected(initialHost);
  }, [initialHost, nonce]);

  const host = hosts.find((h) => h.uuid === selected) ?? hosts[0];
  const options = useMemo(() => hosts.map((h) => ({ data: h.uuid, label: h.name })), [hosts]);

  const run = useCallback(async (uuid: string) => {
    const id = ++latest.current;
    setRunning(true);
    setError(null);
    try {
      const res = await backend.hostDiagnostics(uuid);
      if (id !== latest.current) return;
      setChecks(res.ok ? res.checks : null);
      if (!res.ok) setError(res.error ?? "The checks failed");
    } catch (err) {
      if (id !== latest.current) return;
      setChecks(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (id === latest.current) setRunning(false);
    }
  }, []);

  // No results arriving after the page closed.
  useEffect(
    () => () => {
      latest.current += 1;
    },
    [],
  );

  const uuid = host?.uuid;
  // Again on every new request (e.g. another failure notification for the same host).
  useEffect(() => {
    setChecks(null);
    if (uuid) void run(uuid);
  }, [uuid, run, nonce]);

  if (!host) {
    return (
      <DialogControlsSection>
        <DialogControlsSectionHeader>Play: connection to a host</DialogControlsSectionHeader>
        <Field label="No hosts yet" description="Add a host first, from the Quick Access menu or Settings → Streaming." />
      </DialogControlsSection>
    );
  }

  const failed = (id: string) => checks?.find((c) => c.id === id)?.ok === false;
  const canWake = failed("reachable") && checks?.find((c) => c.id === "wake")?.ok === true;
  const fixes = [
    ...(canWake ? [{ label: "Wake the PC", onClick: () => void wakeHost(host) }] : []),
    ...(failed("paired") ? [{ label: "Pair again", onClick: () => showPairModal({ address: "", host }) }] : []),
    { label: "Host settings", onClick: () => openEclipsePage("streaming", host.uuid) },
  ];
  const problems = checks?.filter((c) => c.ok === false).length ?? 0;

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Play: connection to a host</DialogControlsSectionHeader>
      {hosts.length > 1 && (
        <DropdownItem label="Host" rgOptions={options} selectedOption={host.uuid} onChange={(opt) => setSelected(opt.data)} />
      )}
      {checks === null ? (
        <Field label={running ? `Checking ${host.name}...` : (error ?? "Not checked yet")} />
      ) : (
        checks.map((check) => <CheckRow key={check.id} check={check} />)
      )}
      <ButtonRow buttons={fixes} />
      <ButtonItem
        layout="below"
        description={checks ? (problems ? `${problems} problem(s) found.` : "No problems found.") : undefined}
        disabled={running}
        onClick={() => void run(host.uuid)}
      >
        {running ? "Checking..." : "Check again"}
      </ButtonItem>
    </DialogControlsSection>
  );
}
