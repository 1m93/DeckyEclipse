import { DialogControlsSection, DialogControlsSectionHeader, DropdownItem, SliderField, ToggleField } from "@decky/ui";
import { useMemo } from "react";
import { applyPreset, updateStream, useStore } from "../lib/store";
import type { AudioConfig, DisplayMode, StreamSettings, VideoCodec, VideoDecoder } from "../types";

const RESOLUTIONS = [
  "1280x720",
  "1280x800",
  "1920x1080",
  "1920x1200",
  "2560x1440",
  "2560x1600",
  "3440x1440",
  "3840x2160",
].map((r) => ({ data: r, label: r.replace("x", " × ") }));

const FPS = [30, 40, 45, 50, 60, 72, 90, 120, 144].map((fps) => ({ data: fps, label: `${fps} FPS` }));

const CODECS: { data: VideoCodec; label: string }[] = [
  { data: "auto", label: "Automatic" },
  { data: "H.264", label: "H.264" },
  { data: "HEVC", label: "HEVC (H.265)" },
  { data: "AV1", label: "AV1" },
];

const DECODERS: { data: VideoDecoder; label: string }[] = [
  { data: "auto", label: "Automatic" },
  { data: "hardware", label: "Force hardware" },
  { data: "software", label: "Force software" },
];

const AUDIO: { data: AudioConfig; label: string }[] = [
  { data: "stereo", label: "Stereo" },
  { data: "5.1-surround", label: "5.1 surround" },
  { data: "7.1-surround", label: "7.1 surround" },
];

const DISPLAY_MODES: { data: DisplayMode; label: string }[] = [
  { data: "fullscreen", label: "Fullscreen" },
  { data: "borderless", label: "Borderless window" },
  { data: "windowed", label: "Windowed" },
];

type ToggleKey = {
  [K in keyof StreamSettings]: StreamSettings[K] extends boolean ? K : never;
}[keyof StreamSettings];

const TOGGLES: { key: ToggleKey; label: string; description: string }[] = [
  { key: "framePacing", label: "Frame pacing", description: "Smoother video at the cost of a little latency." },
  { key: "vsync", label: "V-Sync", description: "Removes tearing, adds latency. Gamescope usually handles this." },
  { key: "performanceOverlay", label: "Performance overlay", description: "Show Moonlight's stream statistics." },
  { key: "gameOptimization", label: "Optimize game settings", description: "Let the host tune games for the stream." },
  {
    key: "quitAppAfter",
    label: "End the host session too",
    description:
      "When the stream ends (End stream, Exit game or quitting Moonlight), close the app on the host so the session fully stops. Turn off to leave it open and resume later.",
  },
  { key: "multiController", label: "Multiple controllers", description: "Forward every connected controller." },
  { key: "absoluteMouse", label: "Absolute mouse mode", description: "Better for remote desktop use with touch." },
  { key: "muteOnFocusLoss", label: "Mute when unfocused", description: "Silence the stream when Moonlight loses focus." },
  { key: "keepAwake", label: "Keep display awake", description: "Prevent this device's screen from dimming while streaming." },
];

export function StreamPage() {
  const settings = useStore((s) => s.settings);
  const presets = useStore((s) => s.presets);
  const customPreset = useStore((s) => s.customPreset);

  const presetOptions = useMemo(
    () => [...presets.map((p) => ({ data: p.id, label: p.label })), { data: customPreset, label: "Custom" }],
    [presets, customPreset],
  );
  if (!settings) return null;
  const { stream } = settings;
  const resolutionOptions = RESOLUTIONS.some((r) => r.data === stream.resolution)
    ? RESOLUTIONS
    : [...RESOLUTIONS, { data: stream.resolution, label: stream.resolution }];

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Quality</DialogControlsSectionHeader>
        <DropdownItem
          label="Preset"
          description="Presets set resolution, frame rate, bitrate and codec. Changing any of them switches to Custom."
          rgOptions={presetOptions}
          selectedOption={settings.activePreset}
          onChange={(opt) => opt.data !== customPreset && applyPreset(opt.data)}
        />
        <DropdownItem
          label="Resolution"
          rgOptions={resolutionOptions}
          selectedOption={stream.resolution}
          onChange={(opt) => updateStream({ resolution: opt.data })}
        />
        <DropdownItem
          label="Frame rate"
          rgOptions={FPS}
          selectedOption={stream.fps}
          onChange={(opt) => updateStream({ fps: opt.data })}
        />
        <SliderField
          label="Bitrate"
          description="Higher is sharper but needs a stronger network. 10-20 Mbps suits the Deck screen."
          value={Math.round(stream.bitrateKbps / 1000)}
          min={1}
          max={150}
          step={1}
          showValue
          valueSuffix=" Mbps"
          editableValue
          minimumDpadGranularity={1}
          onChange={(mbps) => updateStream({ bitrateKbps: mbps * 1000 })}
        />
        <DropdownItem
          label="Video codec"
          description="HEVC is the best default on the Steam Deck. AV1 needs a recent host GPU."
          rgOptions={CODECS}
          selectedOption={stream.videoCodec}
          onChange={(opt) => updateStream({ videoCodec: opt.data })}
        />
        <DropdownItem
          label="Video decoder"
          rgOptions={DECODERS}
          selectedOption={stream.videoDecoder}
          onChange={(opt) => updateStream({ videoDecoder: opt.data })}
        />
        <ToggleField
          label="HDR"
          description="Requires an HDR display (Steam Deck OLED or an HDR TV) and HDR enabled on the host."
          checked={stream.hdr}
          onChange={(checked) => updateStream({ hdr: checked })}
        />
        <ToggleField
          label="YUV 4:4:4"
          description="Sharper text for desktop use; costs bandwidth and decoder support."
          checked={stream.yuv444}
          onChange={(checked) => updateStream({ yuv444: checked })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Audio & display</DialogControlsSectionHeader>
        <DropdownItem
          label="Audio channels"
          rgOptions={AUDIO}
          selectedOption={stream.audioConfig}
          onChange={(opt) => updateStream({ audioConfig: opt.data })}
        />
        <ToggleField
          label="Play audio on host"
          checked={stream.audioOnHost}
          onChange={(checked) => updateStream({ audioOnHost: checked })}
        />
        <DropdownItem
          label="Display mode"
          rgOptions={DISPLAY_MODES}
          selectedOption={stream.displayMode}
          onChange={(opt) => updateStream({ displayMode: opt.data })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Behavior</DialogControlsSectionHeader>
        {TOGGLES.map((toggle) => (
          <ToggleField
            key={toggle.key}
            label={toggle.label}
            description={toggle.description}
            checked={stream[toggle.key]}
            onChange={(checked) => updateStream({ [toggle.key]: checked })}
          />
        ))}
      </DialogControlsSection>
    </>
  );
}
