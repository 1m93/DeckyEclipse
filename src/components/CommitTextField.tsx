import { TextField, type TextFieldProps } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { ButtonRow } from "./ButtonRow";

interface Props extends Omit<TextFieldProps, "value" | "onChange" | "onBlur" | "onKeyDown"> {
  value: string;
  /** Persist the value. May be async; a returned string is shown as an error. */
  onCommit: (value: string) => void | string | null | Promise<void | string | null>;
}

const SAVED_HINT_MS = 2500;

/**
 * Text field with an explicit Save step. Save / Undo appear as soon as the
 * text differs from the saved value; Enter also saves. Nothing is saved
 * silently on blur, so the user always knows whether a change was stored.
 */
export function CommitTextField({ value, onCommit, description, ...rest }: Props) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => setDraft(value), [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const dirty = draft !== value;

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      const error = await onCommit(draft);
      setFeedback(error ? { ok: false, text: error } : { ok: true, text: "Saved ✓" });
    } finally {
      setSaving(false);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setFeedback(null), SAVED_HINT_MS);
    }
  };

  return (
    <>
      <TextField
        {...rest}
        description={
          feedback ? <span style={{ color: feedback.ok ? "#5ba32b" : "#ff6b6b" }}>{feedback.text}</span> : description
        }
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setFeedback(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
        }}
      />
      {dirty && (
        <ButtonRow
          buttons={[
            { label: saving ? "Saving..." : "Save", disabled: saving, onClick: () => void save() },
            { label: "Undo", disabled: saving, onClick: () => setDraft(value) },
          ]}
        />
      )}
    </>
  );
}
