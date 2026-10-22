import { ButtonItem, ProgressBar } from "@decky/ui";
import { useMemo } from "react";
import { cancelInstall } from "../lib/actions";
import { openEclipsePage } from "../lib/navigation";
import { useStore } from "../lib/store";

/** Last percentage in a flatpak progress line, e.g. "Installing 1/2… 42%  3.1 MB/s". */
function parsePercent(line: string): number | null {
  const matches = line.match(/(\d{1,3})%/g);
  if (!matches) return null;
  const value = Number(matches[matches.length - 1].slice(0, -1));
  return value >= 0 && value <= 100 ? value : null;
}

/** Progress bar, status text below it, and a Cancel button for the running install. */
export function InstallProgress() {
  const line = useStore((s) => s.installLine);
  const percent = useMemo(() => parsePercent(line), [line]);
  const cancelling = line === "Cancelling...";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
      <ProgressBar indeterminate={percent === null} nProgress={percent ?? 0} nTransitionSec={0.5} />
      <div
        style={{
          fontSize: 12,
          opacity: 0.75,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {line || "Starting..."}
      </div>
      <ButtonItem layout="below" disabled={cancelling} onClick={() => void cancelInstall()}>
        {cancelling ? "Cancelling..." : "Cancel"}
      </ButtonItem>
      <ButtonItem layout="below" onClick={() => openEclipsePage("about")}>
        View log
      </ButtonItem>
    </div>
  );
}
