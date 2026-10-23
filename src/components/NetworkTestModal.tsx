import { DialogBody, DialogFooter, DialogHeader, ModalRoot, Spinner, showModal } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import { notify } from "../lib/actions";
import { formatBitrate } from "../lib/format";
import { getState, updateSettings, updateStream } from "../lib/store";
import type { Host, NetworkTestResult } from "../types";
import { ButtonRow } from "./ButtonRow";

const GOOD = "#5ba32b";
const WARN = "#e0a526";
const BAD = "#ff6b6b";

/** Stability: what makes a stream smooth or stutter. */
const STABILITY: Record<NonNullable<NetworkTestResult["verdict"]>, { text: string; color: string }> = {
  great: { text: "Very stable", color: GOOD },
  good: { text: "Stable", color: GOOD },
  fair: { text: "Somewhat unstable: expect some stutter", color: WARN },
  poor: { text: "Unstable: streams will stutter", color: BAD },
  unreachable: { text: "The host did not answer", color: BAD },
};

/** Delay: what makes controls feel responsive or laggy. */
const DELAY: Record<NonNullable<NetworkTestResult["delay"]>, { text: string; color: string }> = {
  low: { text: "low, controls feel instant", color: GOOD },
  medium: { text: "medium, slight input lag", color: WARN },
  high: { text: "high, noticeable input lag", color: BAD },
  unreachable: { text: "-", color: BAD },
};

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <>
      <span style={{ opacity: 0.7 }}>{label}</span>
      <span style={color ? { color } : undefined}>{value}</span>
    </>
  );
}

function describeLink(result: NetworkTestResult): string {
  if (!result.iface) return "Unknown";
  if (result.vpn) return `VPN (${result.iface})`;
  if (!result.wireless) return `Wired (${result.iface})`;
  const parts = ["Wi-Fi"];
  if (result.freqMhz) parts.push(result.freqMhz < 3000 ? "2.4 GHz" : result.freqMhz < 5900 ? "5 GHz" : "6 GHz");
  if (result.signalDbm) parts.push(`signal ${result.signalDbm} dBm`);
  if (result.linkMbps) parts.push(`link ${Math.round(result.linkMbps)} Mbit/s`);
  return parts.join(" · ");
}

/** Which bitrate a stream to this host uses now, and how to change it. */
function bitrateTarget(host: Host): {
  label: string;
  kbps: number;
  /** Lowers the bitrate where it is set; null when it cannot be changed from here. */
  lower: ((kbps: number) => string) | null;
} {
  const { settings, presets, inheritPreset, autoPreset } = getState();
  const pinned = settings?.hosts[host.uuid]?.preset;
  if (settings && pinned && pinned !== inheritPreset) {
    const profile = settings.profiles[pinned];
    if (profile) {
      return {
        label: `Profile "${profile.name}" of this host`,
        kbps: profile.stream.bitrateKbps,
        lower: (kbps) => {
          updateSettings(
            {
              profiles: { [pinned]: { stream: { bitrateKbps: kbps } } },
              ...(settings.activePreset === pinned ? { stream: { bitrateKbps: kbps } } : {}),
            },
            0,
          );
          return `Profile "${profile.name}" now streams at ${formatBitrate(kbps)}.`;
        },
      };
    }
    const preset = presets.find((p) => p.id === pinned);
    return {
      label: `Preset "${preset?.label ?? pinned}" of this host`,
      kbps: preset?.values.bitrateKbps ?? settings.stream.bitrateKbps,
      lower: null,
    };
  }
  const active = settings?.activePreset ?? "";
  const profile = settings?.profiles[active];
  const builtIn = !profile && (active === autoPreset || presets.some((p) => p.id === active));
  return {
    label: profile ? `Profile "${profile.name}"` : "Streaming settings",
    kbps: settings?.stream.bitrateKbps ?? 0,
    lower: (kbps) => {
      updateStream({ bitrateKbps: kbps });
      return builtIn
        ? `Bitrate set to ${formatBitrate(kbps)} for every host. The preset is now Custom.`
        : `Bitrate set to ${formatBitrate(kbps)}${profile ? ` in profile "${profile.name}"` : " for every host"}.`;
    },
  };
}

function NetworkTestModal({ host, closeModal }: { host: Host; closeModal?: () => void }) {
  const [result, setResult] = useState<NetworkTestResult | null>(null);
  const [running, setRunning] = useState(true);

  const run = useCallback(async () => {
    setRunning(true);
    try {
      // The previous result stays on screen until the new one is in.
      setResult(await backend.hostNetworkTest(host.uuid));
    } catch (err) {
      setResult({ ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      setRunning(false);
    }
  }, [host.uuid]);

  useEffect(() => {
    void run();
  }, [run]);

  const target = bitrateTarget(host);
  const suggested = result?.ok ? (result.suggestedKbps ?? null) : null;
  // Only lowering is offered: a higher bitrate than chosen is not needed for a good stream.
  const tooHigh = suggested !== null && target.kbps > suggested;
  const stability = result?.ok && result.verdict ? STABILITY[result.verdict] : null;
  const delay = result?.ok && result.delay ? DELAY[result.delay] : null;
  const lower = tooHigh && target.lower && suggested !== null ? target.lower : null;

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>Network test: {host.name}</DialogHeader>
      <DialogBody>
        {running && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <Spinner style={{ width: 20, height: 20 }} />
            Measuring for a few seconds...
          </div>
        )}
        {result && !result.ok && <div style={{ color: BAD }}>{result.error ?? "The test failed."}</div>}
        {result?.ok && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10, opacity: running ? 0.5 : 1 }}>
            {stability && (
              <div style={{ fontSize: 18, fontWeight: 600, color: stability.color }}>{stability.text}</div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 16px" }}>
              <Row
                label="Delay"
                value={
                  result.latencyMs != null && delay
                    ? `${result.latencyMs} ms (${delay.text}), varies by ${result.jitterMs ?? 0} ms`
                    : "-"
                }
                color={delay?.color}
              />
              <Row label="Failed tries" value={`${result.failed ?? 0} of ${result.attempts ?? 0}`} />
              <Row label="This device" value={describeLink(result)} />
              <Row label="Host address" value={result.address ?? "-"} />
              {suggested !== null && (
                <Row label="Suggested bitrate" value={`up to ${formatBitrate(suggested)}`} />
              )}
              <Row label={target.label} value={formatBitrate(target.kbps)} color={tooHigh ? WARN : undefined} />
            </div>
            {(result.notes ?? []).map((note) => (
              <div key={note} style={{ opacity: 0.85 }}>
                • {note}
              </div>
            ))}
            {suggested !== null && !tooHigh && (
              <div style={{ opacity: 0.85 }}>The bitrate fits what this test can see. Changes apply to the next stream.</div>
            )}
            {tooHigh && !target.lower && (
              <div style={{ opacity: 0.85 }}>Pick a lower preset or a profile for this host in its settings.</div>
            )}
          </div>
        )}
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            ...(lower && suggested !== null
              ? [
                  {
                    label: `Use ${formatBitrate(suggested)}`,
                    disabled: running,
                    onClick: () => {
                      notify(`${lower(suggested)} It applies to the next stream.`);
                      closeModal?.();
                    },
                  },
                ]
              : []),
            // Stays enabled while testing, so the focused button does not go dead.
            { label: running ? "Testing..." : "Test again", onClick: () => !running && void run() },
            { label: "Close", onClick: () => closeModal?.() },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

/** Measure the connection to a host and suggest a bitrate. */
export function showNetworkTest(host: Host): void {
  showModal(<NetworkTestModal host={host} />);
}
