import {
  ButtonItem,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  TextField,
} from "@decky/ui";
import { useMemo, useState } from "react";
import { backend } from "../backend";
import { notify } from "../lib/actions";
import { setHostConfig } from "../lib/store";
import type { Host, HostConfig, HostDisplay, Patch } from "../types";

/** Merge detected displays into the saved list, keyed by output name. */
function mergeDisplays(saved: HostDisplay[], detected: HostDisplay[]): HostDisplay[] {
  const byName = new Map(saved.map((d) => [d.outputName, d]));
  for (const display of detected) byName.set(display.outputName, display);
  return [...byName.values()];
}

/** Lets the user pick which host display Sunshine captures (via `output_name`). */
export function HostDisplaysSection({ host, cfg }: { host: Host; cfg: HostConfig }) {
  const displays = useMemo(() => cfg.displays ?? [], [cfg.displays]);
  const [detecting, setDetecting] = useState(false);
  const [label, setLabel] = useState("");
  const [outputName, setOutputName] = useState("");
  const hasCreds = Boolean(cfg.sunshineUser && cfg.sunshinePasswordSet);

  const options = useMemo(
    () => [{ data: "", label: "Keep Sunshine's setting" }, ...displays.map((d) => ({ data: d.outputName, label: d.label }))],
    [displays],
  );

  const save = async (patch: Patch<HostConfig>) => {
    const error = await setHostConfig(host.uuid, patch);
    if (error) notify(error);
  };

  const detect = async () => {
    setDetecting(true);
    try {
      const res = await backend.detectHostDisplays(host.uuid);
      if (res.displays.length) {
        await save({ displays: mergeDisplays(displays, res.displays) });
        notify(`Found ${res.displays.length} display(s) on ${host.name}`);
      } else {
        notify(res.error ?? "No displays found");
      }
    } finally {
      setDetecting(false);
    }
  };

  const remove = (name: string) =>
    void save({
      displays: displays.filter((d) => d.outputName !== name),
      ...(cfg.display === name ? { display: null } : {}),
    });

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Host displays</DialogControlsSectionHeader>
      <Field
        description={
          hasCreds
            ? "Choose which display of the host Sunshine streams. Switching changes Sunshine's Output Name setting and restarts Sunshine (the original config is backed up first)."
            : "Needs the host's Sunshine credentials (below), because the display is changed through the Sunshine web API."
        }
      />
      {displays.length > 0 && (
        <DropdownItem
          label="Display for streams"
          description="Also selectable when picking an app."
          rgOptions={options}
          selectedOption={cfg.display ?? ""}
          disabled={!hasCreds}
          onChange={(opt) => void save({ display: opt.data || null })}
        />
      )}
      {displays.map((d) => (
        <ButtonItem key={d.outputName} label={d.label} description={d.outputName} onClick={() => remove(d.outputName)}>
          Remove
        </ButtonItem>
      ))}
      <ButtonItem layout="below" disabled={!hasCreds || detecting} onClick={() => void detect()}>
        {detecting ? "Reading Sunshine log..." : "Detect displays from Sunshine"}
      </ButtonItem>
      <Field
        label="Add manually"
        description="Linux hosts: connector name such as HDMI-A-1 or a monitor index. Windows hosts: the device_id listed under Currently available display devices in Sunshine's Troubleshooting log."
      />
      <TextField label="Name" value={label} onChange={(e) => setLabel(e.target.value)} />
      <TextField label="Output name" value={outputName} onChange={(e) => setOutputName(e.target.value)} />
      <ButtonItem
        layout="below"
        disabled={!outputName.trim()}
        onClick={async () => {
          const name = outputName.trim();
          await save({ displays: mergeDisplays(displays, [{ outputName: name, label: label.trim() || name }]) });
          setLabel("");
          setOutputName("");
        }}
      >
        Add display
      </ButtonItem>
    </DialogControlsSection>
  );
}
