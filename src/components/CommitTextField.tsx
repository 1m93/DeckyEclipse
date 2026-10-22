import { TextField, type TextFieldProps } from "@decky/ui";
import { useEffect, useState } from "react";

interface Props extends Omit<TextFieldProps, "value" | "onChange" | "onBlur"> {
  value: string;
  /** Called once editing ends (blur / keyboard closed), not on every keystroke. */
  onCommit: (value: string) => void;
}

/** A text field that keeps edits local and only persists the final value. */
export function CommitTextField({ value, onCommit, ...rest }: Props) {
  const [draft, setDraft] = useState(value);

  useEffect(() => setDraft(value), [value]);

  return (
    <TextField
      {...rest}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== value) onCommit(draft);
      }}
    />
  );
}
