import { DialogControlsSection, DialogControlsSectionHeader, Field } from "@decky/ui";
import { PluginUpdateSection } from "../components/PluginUpdateSection";
import { useStore } from "../lib/store";

export function AboutPage() {
  const configPath = useStore((s) => s.configPath);
  const launcher = useStore((s) => s.launcher);
  const hostCount = useStore((s) => s.hosts.length);
  const useMoonlight = useStore((s) => s.settings?.features.moonlight ?? false);

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Eclipse</DialogControlsSectionHeader>
        <Field
          label="Moonlight & Sunshine for Gaming Mode"
          description="Play games streamed from a PC with Moonlight, and share this device's games with Sunshine, without leaving Gaming Mode."
        />
        {useMoonlight && (
          <>
            <Field label="Moonlight config" description={configPath ?? "Not found (pair a host first)"} />
            <Field label="Paired hosts" description={String(hostCount)} />
            <Field
              label="Launcher script"
              description={`${launcher?.script ?? "-"} · Streams start through a non-Steam shortcut, so Moonlight gets the Steam overlay, Steam Input and the Exit button.`}
            />
          </>
        )}
      </DialogControlsSection>

      <PluginUpdateSection />
    </>
  );
}
