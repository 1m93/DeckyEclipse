import { DialogBody, DialogFooter, DialogHeader, Focusable, ModalRoot, Spinner, TextField, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import { backend } from "../backend";
import { ButtonRow } from "./ButtonRow";

type Mode = "checking" | "enter" | "create";

/**
 * Asks for the SteamOS password that sudo needs. A fresh SteamOS has none:
 * then the user picks one here, and it is set with passwd before being used.
 */
function SudoPasswordModal({
  action,
  onDone,
  closeModal,
}: {
  action: string;
  onDone: (password: string | null) => void;
  closeModal?: () => void;
}) {
  const [mode, setMode] = useState<Mode>("checking");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    backend
      .sudoPasswordState()
      .then((res) => alive && setMode(res.state === "none" ? "create" : "enter"))
      .catch(() => alive && setMode("enter"));
    return () => {
      alive = false;
    };
  }, []);

  const finish = (value: string | null) => {
    onDone(value);
    closeModal?.();
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await backend.createSudoPassword(password);
      if (res.ok) finish(password);
      else setError(res.error ?? "Could not set the password");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const creating = mode === "create";
  const mismatch = creating && repeat.length > 0 && repeat !== password;

  const submit = () => (creating ? void create() : finish(password));
  const canSubmit = mode !== "checking" && !busy && Boolean(password) && (!creating || repeat === password);

  // ModalRoot, not ConfirmModal: ConfirmModal closes as soon as OK is pressed,
  // while setting a new password takes a moment and can fail.
  return (
    // While passwd runs, B must not drop the result: the password may already be set.
    <ModalRoot onCancel={() => !busy && finish(null)} closeModal={() => !busy && finish(null)}>
      <DialogHeader>{creating ? "Choose a password" : "Password needed"}</DialogHeader>
      <DialogBody>
        {mode === "checking" ? (
          <Spinner style={{ width: 28, height: 28 }} />
        ) : (
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ opacity: 0.8 }}>
              {creating
                ? `${action} needs this device's admin password, and none is set yet. Choose one now: SteamOS asks for it for system changes, so remember it. Eclipse never saves it.`
                : `${action} needs this device's admin password (the SteamOS password). Eclipse uses it for this step only and never saves it.`}
            </div>
            <TextField
              label={creating ? "New password" : "Password"}
              bIsPassword
              focusOnMount
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {creating && (
              <TextField
                label="Repeat the password"
                bIsPassword
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
              />
            )}
            {(error || mismatch) && (
              <div style={{ color: "#ff6b6b" }}>{error ?? "The two passwords are different."}</div>
            )}
          </Focusable>
        )}
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            {
              label: busy ? "Setting..." : creating ? "Set password and continue" : "Continue",
              disabled: !canSubmit,
              onClick: submit,
            },
            { label: "Cancel", disabled: busy, onClick: () => finish(null) },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

/** Ask for the sudo password (or set the first one). Resolves to null when the user cancels. */
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
