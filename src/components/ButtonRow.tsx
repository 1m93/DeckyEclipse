import { DialogButton, Focusable } from "@decky/ui";
import type { ReactNode } from "react";

export interface RowButton {
  label: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}

/**
 * Equal-width buttons on one line, navigated left/right with the D-pad.
 * ButtonItem is a full settings row and breaks when laid out horizontally,
 * so plain DialogButtons are used, as Steam's own dialogs do.
 */
export function ButtonRow({ buttons }: { buttons: RowButton[] }) {
  return (
    <Focusable flow-children="row" style={{ display: "flex", gap: 8, padding: "10px 0" }}>
      {buttons.map((button, index) => (
        <DialogButton
          key={index}
          style={{ flex: 1, minWidth: 0, padding: "8px 12px" }}
          disabled={button.disabled}
          onClick={button.onClick}
        >
          {button.label}
        </DialogButton>
      ))}
    </Focusable>
  );
}
