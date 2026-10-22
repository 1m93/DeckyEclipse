import { memo } from "react";
import { STATE_COLORS, type HostState } from "../lib/format";

/** Colored host state dot. `spacing` is the gap to the following content. */
export const StatusDot = memo(function StatusDot({ state, spacing = 8 }: { state: HostState; spacing?: number }) {
  return (
    <span
      aria-label={state}
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        marginRight: spacing,
        flexShrink: 0,
        background: STATE_COLORS[state],
        boxShadow: state === "online" ? `0 0 6px ${STATE_COLORS.online}` : undefined,
      }}
    />
  );
});
