import { memo } from "react";
import { STATE_COLORS, type HostState } from "../lib/format";

export const StatusDot = memo(function StatusDot({ state }: { state: HostState }) {
  return (
    <span
      aria-label={state}
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        marginRight: 8,
        flexShrink: 0,
        background: STATE_COLORS[state],
        boxShadow: state === "online" ? `0 0 6px ${STATE_COLORS.online}` : undefined,
      }}
    />
  );
});
