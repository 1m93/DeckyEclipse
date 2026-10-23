import {
  ButtonItem,
  ConfirmModal,
  DialogControlsSection,
  DialogControlsSectionHeader,
  DropdownItem,
  Field,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { refreshSunshineOptions, restartSunshineToApply, setSunshineOptions } from "../lib/actions";
import { useStore } from "../lib/store";
import type { SunshineOptions } from "../types";
import { CommitTextField } from "./CommitTextField";

interface Option<T> {
  data: T;
  label: string;
}

/** `options`, plus the current value when it is not one of them (set in sunshine.conf by hand). */
export function withCurrent<T>(options: Option<T>[], value: T, label: (value: T) => string): Option<T>[] {
  return options.some((o) => o.data === value) ? options : [...options, { data: value, label: label(value) }];
}

const mbps = (kbps: number) => `${Math.round(kbps / 100) / 10} Mbps`;

export const MAX_BITRATE_OPTIONS: Option<number>[] = [
  { data: 0, label: "No limit" },
  ...[5000, 10000, 15000, 20000, 30000, 50000, 80000].map((kbps) => ({ data: kbps, label: mbps(kbps) })),
];
export const maxBitrateLabel = (kbps: number) => (kbps ? mbps(kbps) : "No limit");

const ENCODERS: Option<SunshineOptions["encoder"]>[] = [
  { data: "", label: "Automatic" },
  { data: "vaapi", label: "VA-API (GPU)" },
  { data: "vulkan", label: "Vulkan (GPU)" },
  { data: "software", label: "Software (CPU)" },
];

const CODEC_MODES = (codec: string): Option<number>[] => [
  { data: 0, label: "Automatic" },
  { data: 1, label: "Off" },
  { data: 2, label: `${codec} 8-bit` },
  { data: 3, label: `${codec} 8-bit and 10-bit (HDR)` },
];

const MIN_FPS: Option<number>[] = [
  { data: 0, label: "Automatic (half the stream's)" },
  ...[10, 20, 30, 60].map((fps) => ({ data: fps, label: `${fps} FPS` })),
];

const FEC: Option<number>[] = [
  { data: 0, label: "Off" },
  { data: 10, label: "10 %" },
  { data: 20, label: "20 % (default)" },
  { data: 30, label: "30 %" },
  { data: 50, label: "50 %" },
];

const GAMEPADS: Option<SunshineOptions["gamepad"]>[] = [
  { data: "auto", label: "Automatic (like the client's)" },
  { data: "xone", label: "Xbox One" },
  { data: "ds5", label: "DualSense (PS5)" },
  { data: "switch", label: "Switch Pro" },
];

const BACK_FOR_HOME: Option<number>[] = [
  { data: -1, label: "Off" },
  ...[1000, 2000, 3000].map((ms) => ({ data: ms, label: `Hold ${ms / 1000} s` })),
];

const WEB_UI_ACCESS: Option<SunshineOptions["origin_web_ui_allowed"]>[] = [
  { data: "pc", label: "This device only" },
  { data: "lan", label: "Local network" },
  { data: "wan", label: "Anyone (not recommended)" },
];

const ENCRYPTION: Option<number>[] = [
  { data: 0, label: "Off" },
  { data: 1, label: "When the device supports it" },
  { data: 2, label: "Required" },
];

const PING_TIMEOUT: Option<number>[] = [5000, 10000, 20000, 30000, 60000].map((ms) => ({
  data: ms,
  label: `${ms / 1000} s${ms === 10000 ? " (default)" : ""}`,
}));

const LOG_LEVELS: Option<SunshineOptions["min_log_level"]>[] = [
  { data: "verbose", label: "Verbose" },
  { data: "debug", label: "Debug" },
  { data: "info", label: "Info (default)" },
  { data: "warning", label: "Warnings" },
  { data: "error", label: "Errors" },
];

/**
 * Says when changed options take effect, with a restart when Sunshine still
 * runs with the old ones.
 */
export function ApplyOptionsRow({ running }: { running: boolean }) {
  const needsRestart = useStore((s) => s.sunshineOptions?.needsRestart ?? false);
  const restarting = useStore((s) => s.sunshineOptions?.restarting ?? false);
  const clients = useStore((s) => s.incoming?.clients ?? 0);
  // Kept while restarting, when Sunshine briefly reads as stopped: focus must not jump away.
  if (!restarting && (!running || !needsRestart)) return null;
  return (
    <ButtonItem
      layout="below"
      label="Restart Sunshine to apply"
      description={
        clients > 0
          ? "Changes apply after a restart, which disconnects the devices streaming now."
          : "Changes apply after a restart, which takes a few seconds."
      }
      disabled={restarting}
      onClick={restartSunshineToApply}
    >
      {restarting ? "Restarting..." : "Restart now"}
    </ButtonItem>
  );
}

function confirmReset(defaults: SunshineOptions) {
  showModal(
    <ConfirmModal
      strTitle="Reset Sunshine's options?"
      strDescription="Every option on this page goes back to Sunshine's default. Your login, paired devices and apps are kept."
      strOKButtonText="Reset"
      bDestructiveWarning
      onOK={() => void setSunshineOptions(defaults)}
    />,
  );
}

/** Sunshine's own options (sunshine.conf), for the Sharing page. */
export function SunshineOptionsSections({ running, legacy }: { running: boolean; legacy: boolean }) {
  const options = useStore((s) => s.sunshineOptions);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setError(await refreshSunshineOptions());
  }, []);

  // Read again when Sunshine starts or stops: that also applies pending changes.
  useEffect(() => {
    void load();
  }, [load, running]);

  if (legacy) {
    return (
      <DialogControlsSection>
        <DialogControlsSectionHeader>Stream from this device</DialogControlsSectionHeader>
        <Field
          label="Repair Sunshine to change its options"
          description="This copy of Sunshine keeps its config in the wrong folder. Repair it above first."
        />
      </DialogControlsSection>
    );
  }

  if (!options) {
    return (
      <DialogControlsSection>
        <DialogControlsSectionHeader>Stream from this device</DialogControlsSectionHeader>
        {error ? (
          <ButtonItem
            layout="below"
            label="Sunshine's options are not available"
            description={error}
            onClick={() => void load()}
          >
            Try again
          </ButtonItem>
        ) : (
          <Field label="Reading Sunshine's options..." />
        )}
      </DialogControlsSection>
    );
  }

  const { values, defaults } = options;
  const set = (patch: Partial<SunshineOptions>) => void setSunshineOptions(patch);
  const when = running ? "Changes apply after Sunshine restarts." : "Changes apply the next time Sunshine starts.";

  return (
    <>
      <DialogControlsSection>
        <DialogControlsSectionHeader>Stream from this device</DialogControlsSectionHeader>
        <Field
          description={`Options of Sunshine itself, for every device that streams from here. ${when}`}
          bottomSeparator="none"
        />
        <ApplyOptionsRow running={running} />
        <CommitTextField
          label="Name shown in Moonlight"
          description="How this device appears on the other devices. Empty: its hostname."
          placeholder="Hostname of this device"
          value={values.sunshine_name}
          onCommit={(name) => {
            const trimmed = name.trim();
            if (trimmed.length > 64) return "Use at most 64 characters.";
            if (trimmed.includes("#") || trimmed.startsWith("[")) return "The name cannot contain # or start with [.";
            return setSunshineOptions({ sunshine_name: trimmed });
          }}
        />
        <DropdownItem
          label="Maximum bitrate"
          description="Caps what other devices may ask for. Lower values save battery and Wi-Fi bandwidth."
          rgOptions={withCurrent(MAX_BITRATE_OPTIONS, values.max_bitrate, maxBitrateLabel)}
          selectedOption={values.max_bitrate}
          onChange={(opt) => set({ max_bitrate: opt.data })}
        />
        <DropdownItem
          label="Encoder"
          description="Automatic uses the GPU when it can. Software encoding costs a lot of CPU and battery."
          rgOptions={ENCODERS}
          selectedOption={values.encoder}
          onChange={(opt) => set({ encoder: opt.data })}
        />
        <DropdownItem
          label="HEVC (H.265)"
          rgOptions={CODEC_MODES("HEVC")}
          selectedOption={values.hevc_mode}
          onChange={(opt) => set({ hevc_mode: opt.data })}
        />
        <DropdownItem
          label="AV1"
          description="Automatic offers a codec only when this device can encode it."
          rgOptions={CODEC_MODES("AV1")}
          selectedOption={values.av1_mode}
          onChange={(opt) => set({ av1_mode: opt.data })}
        />
        <DropdownItem
          label="Minimum frame rate"
          description="On a still screen Sunshine sends fewer frames, down to this rate."
          rgOptions={withCurrent(MIN_FPS, values.minimum_fps_target, (fps) => `${fps} FPS`)}
          selectedOption={values.minimum_fps_target}
          onChange={(opt) => set({ minimum_fps_target: opt.data })}
        />
        <DropdownItem
          label="Error correction"
          description="Extra data that repairs video on a lossy network, at the cost of bandwidth."
          rgOptions={withCurrent(FEC, values.fec_percentage, (pct) => `${pct} %`)}
          selectedOption={values.fec_percentage}
          onChange={(opt) => set({ fec_percentage: opt.data })}
        />
        <ToggleField
          label="Stream audio"
          description="Off: the sound stays on this device."
          checked={values.stream_audio}
          onChange={(checked) => set({ stream_audio: checked })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Input from other devices</DialogControlsSectionHeader>
        <ToggleField
          label="Controllers"
          checked={values.controller}
          onChange={(checked) => set({ controller: checked })}
        />
        {values.controller && (
          <>
            <DropdownItem
              label="Controller type"
              description="The controller games on this device see."
              rgOptions={GAMEPADS}
              selectedOption={values.gamepad}
              onChange={(opt) => set({ gamepad: opt.data })}
            />
            {values.gamepad === "auto" && (
              <>
                <ToggleField
                  label="PlayStation controller for motion controls"
                  description="A controller with gyro shows up as a PlayStation controller, so games get its motion."
                  checked={values.motion_as_ds4}
                  onChange={(checked) => set({ motion_as_ds4: checked })}
                />
                <ToggleField
                  label="PlayStation controller for touchpads"
                  description="A controller with a touchpad shows up as a PlayStation controller, so games get its touchpad."
                  checked={values.touchpad_as_ds4}
                  onChange={(checked) => set({ touchpad_as_ds4: checked })}
                />
              </>
            )}
            <DropdownItem
              label="Back button opens Home"
              description="For controllers without a Home/Guide button: holding Back presses it."
              rgOptions={withCurrent(BACK_FOR_HOME, values.back_button_timeout, (ms) => `Hold ${ms / 1000} s`)}
              selectedOption={values.back_button_timeout}
              onChange={(opt) => set({ back_button_timeout: opt.data })}
            />
          </>
        )}
        <ToggleField label="Keyboard" checked={values.keyboard} onChange={(checked) => set({ keyboard: checked })} />
        <ToggleField label="Mouse" checked={values.mouse} onChange={(checked) => set({ mouse: checked })} />
        {values.mouse && (
          <ToggleField
            label="High-resolution scrolling"
            description="Turn off when old games scroll too fast."
            checked={values.high_resolution_scrolling}
            onChange={(checked) => set({ high_resolution_scrolling: checked })}
          />
        )}
        <ToggleField
          label="Touch and pen"
          description="Pass touch and pen input through as it is. Turn off for games that do not support it."
          checked={values.native_pen_touch}
          onChange={(checked) => set({ native_pen_touch: checked })}
        />
      </DialogControlsSection>

      <DialogControlsSection>
        <DialogControlsSectionHeader>Network and security</DialogControlsSectionHeader>
        <DropdownItem
          label="Encryption on the local network"
          description="Protects the stream at home, but costs performance on both devices."
          rgOptions={ENCRYPTION}
          selectedOption={values.lan_encryption_mode}
          onChange={(opt) => set({ lan_encryption_mode: opt.data })}
        />
        <DropdownItem
          label="Encryption over the internet"
          rgOptions={ENCRYPTION}
          selectedOption={values.wan_encryption_mode}
          onChange={(opt) => set({ wan_encryption_mode: opt.data })}
        />
        <ToggleField
          label="Open ports on the router (UPnP)"
          description="For streaming from outside your home. Only works when the router allows UPnP."
          checked={values.upnp}
          onChange={(checked) => set({ upnp: checked })}
        />
        <ToggleField
          label="IPv6"
          description="Also accept connections over IPv6."
          checked={values.address_family === "both"}
          onChange={(checked) => set({ address_family: checked ? "both" : "ipv4" })}
        />
        <DropdownItem
          label="Sunshine web page access"
          rgOptions={WEB_UI_ACCESS}
          selectedOption={values.origin_web_ui_allowed}
          onChange={(opt) => set({ origin_web_ui_allowed: opt.data })}
        />
        <DropdownItem
          label="Disconnect after"
          description="How long a device may stay silent, e.g. on bad Wi-Fi, before its stream ends."
          rgOptions={withCurrent(PING_TIMEOUT, values.ping_timeout, (ms) => `${ms / 1000} s`)}
          selectedOption={values.ping_timeout}
          onChange={(opt) => set({ ping_timeout: opt.data })}
        />
        <DropdownItem
          label="Log detail"
          description="More detail helps to find problems in Troubleshooting → Logs."
          rgOptions={withCurrent(LOG_LEVELS, values.min_log_level, (level) => level)}
          selectedOption={values.min_log_level}
          onChange={(opt) => set({ min_log_level: opt.data })}
        />
        <ButtonItem
          layout="below"
          description="Back to Sunshine's defaults for everything on this page."
          onClick={() => confirmReset(defaults)}
        >
          Reset options
        </ButtonItem>
      </DialogControlsSection>
    </>
  );
}
