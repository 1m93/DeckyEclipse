import { ConfirmModal, Focusable, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { showPairModal } from "./PairModal";

function AddHostModal({ closeModal }: { closeModal?: () => void }) {
  const [address, setAddress] = useState("");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const valid = address.trim().length > 0;

  return (
    <ConfirmModal
      strTitle="Add a host"
      strOKButtonText="Continue"
      bOKDisabled={!valid}
      closeModal={closeModal}
      onCancel={closeModal}
      onOK={() => {
        closeModal?.();
        const creds = user && password ? { sunshineUser: user, sunshinePassword: password } : null;
        showPairModal({ address: address.trim(), host: null, creds });
      }}
    >
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <TextField
          label="Host address"
          description="IP address or hostname of the PC running Sunshine, e.g. 192.168.1.20"
          value={address}
          focusOnMount
          onChange={(e) => setAddress(e.target.value)}
        />
        <TextField
          label="Sunshine username (optional)"
          description="With credentials, the pairing PIN is entered on the host automatically."
          value={user}
          onChange={(e) => setUser(e.target.value)}
        />
        <TextField
          label="Sunshine password (optional)"
          bIsPassword
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Focusable>
    </ConfirmModal>
  );
}

export function showAddHostModal(): void {
  showModal(<AddHostModal />);
}
