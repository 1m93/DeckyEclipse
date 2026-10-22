import { ConfirmModal, TextField, showModal } from "@decky/ui";
import { useState } from "react";

function SudoPasswordModal({
  action,
  onDone,
  closeModal,
}: {
  action: string;
  onDone: (password: string | null) => void;
  closeModal?: () => void;
}) {
  const [password, setPassword] = useState("");
  const finish = (value: string | null) => {
    onDone(value);
    closeModal?.();
  };

  return (
    <ConfirmModal
      strTitle="Password needed"
      strOKButtonText="Continue"
      bOKDisabled={!password}
      closeModal={() => finish(null)}
      onCancel={() => finish(null)}
      onOK={() => finish(password)}
    >
      <div style={{ marginBottom: 12, opacity: 0.8 }}>
        {action} needs your SteamOS password, the one sudo asks for. Eclipse hands it to sudo for this step only and
        never saves it. Never set one? In Desktop Mode, open Konsole and run passwd.
      </div>
      <TextField label="Password" bIsPassword focusOnMount value={password} onChange={(e) => setPassword(e.target.value)} />
    </ConfirmModal>
  );
}

/** Ask for the sudo password. Resolves to null when the user cancels. */
export function askSudoPassword(action: string): Promise<string | null> {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value: string | null) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    showModal(<SudoPasswordModal action={action} onDone={done} />);
  });
}
