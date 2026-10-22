import { ButtonItem, DropdownItem, Focusable } from "@decky/ui";
import { useCallback, useEffect, useMemo, useState } from "react";
import { backend } from "../backend";
import { useStore } from "../lib/store";

export type LogSource = "plugin" | "launcher" | "sunshine";

const SOURCES: { data: LogSource; label: string }[] = [
  { data: "plugin", label: "Plugin (installs, Sunshine API, errors)" },
  { data: "launcher", label: "Launcher (Moonlight command lines)" },
  { data: "sunshine", label: "Sunshine on this device" },
];

const READERS: Record<LogSource, () => Promise<string>> = {
  plugin: () => backend.getPluginLog(),
  launcher: () => backend.getLauncherLog(),
  sunshine: () => backend.getSunshineLog(),
};

/** Lines per focusable block: lets the D-pad scroll through long logs. */
const CHUNK_LINES = 12;
const LIVE_REFRESH_MS = 2000;

/**
 * Shows the tail of a log, newest last. While an install runs, the plugin log
 * refreshes on its own so progress can be followed live.
 */
export function LogViewer({ initialSource = "plugin" }: { initialSource?: LogSource }) {
  const [source, setSource] = useState<LogSource>(initialSource);
  const [text, setText] = useState("");
  const installing = useStore((s) => s.installing);

  const refresh = useCallback(async () => {
    setText(await READERS[source]());
  }, [source]);

  useEffect(() => {
    void refresh();
    if (!installing || source !== "plugin") return;
    const timer = window.setInterval(() => void refresh(), LIVE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh, installing, source]);

  const chunks = useMemo(() => {
    const lines = text.split("\n");
    const out: string[] = [];
    for (let i = 0; i < lines.length; i += CHUNK_LINES) out.push(lines.slice(i, i + CHUNK_LINES).join("\n"));
    return out;
  }, [text]);

  return (
    <>
      <DropdownItem label="Log" rgOptions={SOURCES} selectedOption={source} onChange={(opt) => setSource(opt.data)} />
      <ButtonItem
        layout="below"
        description={installing && source === "plugin" ? "Refreshing automatically during the installation." : undefined}
        onClick={() => void refresh()}
      >
        Refresh
      </ButtonItem>
      {text.trim() ? (
        chunks.map((chunk, index) => (
          <Focusable
            key={index}
            style={{
              fontFamily: "monospace",
              fontSize: 11,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              background: "rgba(0, 0, 0, 0.3)",
              padding: "4px 8px",
              borderRadius: index === 0 ? "4px 4px 0 0" : index === chunks.length - 1 ? "0 0 4px 4px" : 0,
            }}
            onActivate={() => void refresh()}
            onOKActionDescription="Refresh"
          >
            {chunk}
          </Focusable>
        ))
      ) : (
        <div style={{ opacity: 0.6, padding: 8 }}>The log is empty.</div>
      )}
    </>
  );
}
