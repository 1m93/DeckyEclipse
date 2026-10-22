import { ButtonItem, DialogControlsSection, DialogControlsSectionHeader, Field } from "@decky/ui";
import { LogViewer } from "../components/LogViewer";
import { PluginUpdateSection } from "../components/PluginUpdateSection";
import { loadInitialState, useStore } from "../lib/store";

export function AboutPage() {
  const configPath = useStore((s) => s.configPath);
  const launcher = useStore((s) => s.launcher);
  const hostCount = useStore((s) => s.hosts.length);

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Eclipse</DialogControlsSectionHeader>
        <Field
          label="Moonlight & Sunshine for Gaming Mode"
          description="Streams are launched through a non-Steam shortcut so Moonlight gets full Gaming Mode integration: overlay, Steam Input and the Exit button."
        />
        <Field label="Moonlight config" description={configPath ?? "Not found (pair a host first)"} />
        <Field label="Paired hosts" description={String(hostCount)} />
        <Field label="Launcher script" description={launcher?.script ?? "-"} />
        <Field
          label="Log files"
          description="Plugin: ~/homebrew/logs/Eclipse/ · Launcher: ~/homebrew/settings/Eclipse/launcher.log"
        />
        <ButtonItem layout="below" onClick={() => void loadInitialState(true)}>
          Reload plugin state
        </ButtonItem>
      </DialogControlsSection>

      <PluginUpdateSection />

      <DialogControlsSection>
        <DialogControlsSectionHeader>Logs</DialogControlsSectionHeader>
        <LogViewer />
      </DialogControlsSection>
    </>
  );
}
