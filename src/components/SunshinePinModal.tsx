import { ConfirmModal, Focusable, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { backend } from "../backend";
import { notify } from "../lib/actions";

/** Enter a PIN shown by a Moonlight client that wants to pair with this device's Sunshine. */
function SunshinePinModal({ closeModal }: { closeModal?: () => void }) {
  const [pin, setPin] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const res = await backend.sunshineSubmitPin(pin, name);
    setBusy(false);
    if (res.ok) {
      notify("Client paired with Sunshine");
      closeModal?.();
    } else {
      setError(res.error ?? "Pairing failed");
    }
  };

  return (
    <ConfirmModal
      strTitle="Pair a Moonlight client"
      strOKButtonText={busy ? "Sending..." : "Pair"}
      bOKDisabled={busy || pin.length !== 4}
      closeModal={closeModal}
      onCancel={closeModal}
      onOK={() => void submit()}
    >
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <TextField
          label="PIN"
          description="The 4-digit PIN displayed by the Moonlight client"
          mustBeNumeric
          rangeMin={0}
          rangeMax={9999}
          value={pin}
          focusOnMount
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 4))}
        />
        <TextField label="Client name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        {error && <div style={{ color: "#ff6b6b" }}>{error}</div>}
      </Focusable>
    </ConfirmModal>
  );
}

export function showSunshinePinModal(): void {
  showModal(<SunshinePinModal />);
}
