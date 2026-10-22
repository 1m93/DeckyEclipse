import { ButtonItem, ConfirmModal, DialogControlsSection, DialogControlsSectionHeader, Field, showModal } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import { notify } from "../lib/actions";
import type { PairedClient, PendingPairing } from "../types";
import { showSunshinePinModal } from "./SunshinePinModal";

/** How often waiting devices are looked up while this section is visible. */
const PENDING_POLL_MS = 3000;

function confirmUnpair(client: PairedClient, onDone: () => void) {
  showModal(
    <ConfirmModal
      strTitle={`Unpair ${client.name}?`}
      strDescription="The device can no longer stream from here until it pairs again."
      strOKButtonText="Unpair"
      bDestructiveWarning
      onOK={async () => {
        const res = await backend.sunshineUnpair(client.uuid);
        notify(res.ok ? `Unpaired ${client.name}` : (res.error ?? "Could not unpair"));
        onDone();
      }}
    />,
  );
}

/**
 * Devices paired with this device's Sunshine, plus devices waiting for a PIN
 * (shown live on newer Sunshine versions).
 */
export function SunshineDevicesSection({
  running,
  hasLogin,
  onChanged,
}: {
  running: boolean;
  hasLogin: boolean;
  /** Paired devices changed (paired or unpaired). */
  onChanged?: () => void;
}) {
  const [clients, setClients] = useState<PairedClient[] | null>(null);
  const [clientsError, setClientsError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPairing[]>([]);

  const loadClients = useCallback(async () => {
    const res = await backend.sunshineClients();
    setClients(res.ok ? res.items : []);
    setClientsError(res.ok ? null : res.error);
  }, []);

  useEffect(() => {
    if (running && hasLogin) void loadClients();
  }, [running, hasLogin, loadClients]);

  // Poll for devices asking to pair, so they show up without any button press.
  useEffect(() => {
    if (!running || !hasLogin) {
      setPending([]);
      return;
    }
    let alive = true;
    const poll = async () => {
      const res = await backend.sunshinePendingPairings();
      if (alive) setPending(res.supported ? res.items : []);
    };
    void poll();
    const timer = window.setInterval(() => void poll(), PENDING_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [running, hasLogin]);

  const afterPairing = () => {
    void loadClients();
    setPending([]);
    onChanged?.();
  };

  if (!hasLogin) {
    return (
      <DialogControlsSection>
        <DialogControlsSectionHeader>Devices</DialogControlsSectionHeader>
        <Field label="Sunshine login needed" description="Save the Sunshine login above to see and pair devices." />
      </DialogControlsSection>
    );
  }

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Devices</DialogControlsSectionHeader>
      {!running && <Field label="Sunshine is stopped" description="Start Sunshine to pair devices and see the list." />}

      {pending.map((request) => (
        <ButtonItem
          key={request.id}
          label={`${request.name} wants to pair`}
          description={request.address || undefined}
          onClick={() => showSunshinePinModal(request, afterPairing)}
        >
          Enter PIN
        </ButtonItem>
      ))}

      {running && (
        <ButtonItem
          layout="below"
          description="Start pairing in Moonlight on the other device, then enter the PIN it shows here."
          onClick={() => showSunshinePinModal(undefined, afterPairing)}
        >
          Pair a device
        </ButtonItem>
      )}

      {running &&
        (clients === null ? (
          <Field label="Loading paired devices..." />
        ) : clientsError ? (
          <Field label="Could not load paired devices" description={clientsError} />
        ) : clients.length === 0 ? (
          <Field label="No paired devices yet" />
        ) : (
          clients.map((client) => (
            <ButtonItem
              key={client.uuid}
              label={client.name}
              description={client.enabled === false ? "Disabled in Sunshine" : undefined}
              onClick={() =>
                confirmUnpair(client, () => {
                  void loadClients();
                  onChanged?.();
                })
              }
            >
              Unpair
            </ButtonItem>
          ))
        ))}
    </DialogControlsSection>
  );
}
