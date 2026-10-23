import { DialogBody, DialogFooter, DialogHeader, Focusable, ModalRoot, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { getState, PROFILE_NAME_MAX } from "../lib/store";
import { ButtonRow } from "./ButtonRow";

function ProfileNameModal({
  title,
  action,
  initial,
  ignoreId,
  onSave,
  closeModal,
}: {
  title: string;
  action: string;
  initial: string;
  /** The profile being renamed: its own name is not a duplicate. */
  ignoreId?: string;
  onSave: (name: string) => void;
  closeModal?: () => void;
}) {
  const [name, setName] = useState(initial);
  const trimmed = name.trim();
  const profiles = getState().settings?.profiles ?? {};
  const duplicate = Object.entries(profiles).some(
    ([id, profile]) => id !== ignoreId && profile.name.toLowerCase() === trimmed.toLowerCase(),
  );
  const tooLong = trimmed.length > PROFILE_NAME_MAX;
  const canSave = trimmed.length > 0 && !duplicate && !tooLong;

  const save = () => {
    if (!canSave) return;
    onSave(trimmed);
    closeModal?.();
  };

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>{title}</DialogHeader>
      <DialogBody>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <TextField
            label="Profile name"
            description="Keep the suggestion or press A to type one, for example TV or Hotel Wi-Fi."
            focusOnMount
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              // Enter on the on-screen keyboard saves, like the Save button.
              if (e.key === "Enter") save();
            }}
          />
          {(duplicate || tooLong) && (
            <div style={{ color: "#ff6b6b" }}>
              {duplicate ? "A profile with this name already exists." : `Use at most ${PROFILE_NAME_MAX} characters.`}
            </div>
          )}
        </Focusable>
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            { label: action, disabled: !canSave, onClick: save },
            { label: "Cancel", onClick: () => closeModal?.() },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

/** Ask for a profile name; `onSave` gets the trimmed name. */
export function showProfileNameModal(options: {
  title: string;
  action: string;
  initial?: string;
  ignoreId?: string;
  onSave: (name: string) => void;
}): void {
  showModal(
    <ProfileNameModal
      title={options.title}
      action={options.action}
      initial={options.initial ?? ""}
      ignoreId={options.ignoreId}
      onSave={options.onSave}
    />,
  );
}
