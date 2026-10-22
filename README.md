# Eclipse

Eclipse is a [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that lets you use
[Moonlight](https://moonlight-stream.org) and [Sunshine](https://app.lizardbyte.dev/Sunshine/) from SteamOS
Gaming Mode. Everything works with a controller, so you never need Desktop Mode.

## Features

Both halves are optional. When you first open the plugin, it asks what you use: **play games from a PC**
(Moonlight), **share this device's games** (Sunshine), or both. Only the parts you pick are shown, and nothing is
installed until you press *Install*. You can change this later in *Settings → General*.

**Streaming (Moonlight client)**

- Lists paired hosts from Moonlight's own config and shows them as online, busy or offline, with latency.
- Pick an app and stream with one button. The last-played app gets focus first, so **A, A** resumes it.
- Wake-on-LAN: the MAC address is read from Moonlight or Sunshine, or you can enter it yourself. Offline hosts
  are woken automatically before a stream starts.
- Pairing from Gaming Mode: Eclipse makes the PIN and shows it in a large dialog and a notification. If you save
  the host's Sunshine web UI credentials, Eclipse enters the PIN on the host for you.
- The default quality preset is **Auto: match display**. It picks resolution, frame rate and bitrate for the
  screen in use, on any SteamOS device. It reads the display's EDID for the native resolution and the highest
  refresh rate at that resolution. An external monitor or TV takes precedence over a built-in panel. The display
  is detected again each time a stream starts, so docking or undocking is handled with no settings change. If
  the EDID cannot be read, the resolution comes from the DRM mode list. The refresh rate then falls back to a
  small table of known panels (Steam Deck LCD/OLED), and to 60 Hz otherwise.
- Fixed presets are also available (Deck 800p60, OLED 90 FPS, battery saver, remote/weak Wi-Fi, 1080p, 1440p,
  4K). You can also set resolution, FPS, bitrate, codec, HDR, YUV 4:4:4, audio, frame pacing, V-Sync and more by
  hand.
- You can give a host its own quality preset, for example a lower one for a remote PC.
- **Host display selection** for hosts with several monitors. Moonlight cannot choose a host display, so Eclipse
  changes Sunshine's *Output Name* (`output_name`) setting through the Sunshine web API and restarts Sunshine
  before the stream starts. This needs the host's Sunshine credentials.
  - Displays are detected from the Sunshine log. On Linux hosts Eclipse reads the `Monitor N is HDMI-A-1` lines
    and uses the connector name. On Windows hosts it reads the `Currently available display devices` JSON and
    uses `device_id`. You can also add displays by hand.
  - Sunshine's API replaces the whole config file on save. Eclipse reads the full config, changes only
    `output_name`, and writes everything back. Before the first change it saves a backup to
    `~/homebrew/settings/Eclipse/sunshine-config-backup-<host>.json`.
  - Nothing is written when the display is already selected. Nothing is changed while the host is busy with
    another stream, because the restart would end it. *Keep Sunshine's setting* (the default) never touches the
    config.
- "Add to Steam library" makes a separate non-Steam shortcut for a host/app pair, using Moonlight's cached box
  art. The shortcut wakes the host if needed and starts streaming.
- A "Now streaming" panel with *End stream* and *Quit app on host*.
- If Moonlight is not installed, Eclipse can install it from Flathub (per-user install, so there is no password
  prompt).

**Sunshine on this device (host)**

- Install Sunshine from Flathub when you need it (per-user install). Eclipse then tries to run its service setup
  script. That script may need root to add udev rules for controller input. If it fails in Gaming Mode, Eclipse
  shows the command to run in Desktop Mode.
- Sunshine only runs when you start it from the Quick Access menu or the Sunshine page. **Start automatically**
  is a separate option and is off by default. With it on, Eclipse starts Sunshine when the plugin loads and
  enables the systemd user service. With it off, Eclipse disables the service, so Sunshine never starts by
  itself.
- Enter the PIN from a Moonlight client that wants to pair with your Deck.

## How it works

Gaming Mode only shows windows from processes that Steam started. Because of this, the Decky backend cannot
start Moonlight itself. Eclipse works around this in three steps:

1. The backend writes a short-lived, single-use launch request (`launch-request.json` in the plugin settings
   directory).
2. The frontend starts a non-Steam shortcut called **Moonlight (Eclipse)**. Eclipse creates this shortcut the
   first time you stream.
3. The shortcut runs `bin/eclipse_launcher.py`. It reads the request, builds the Moonlight command line, and
   `exec`s Moonlight. Steam then tracks Moonlight like any game, so the overlay, Steam Input, playtime and
   *Exit game* all work.

If you start the shortcut from the library with no request waiting, it simply opens Moonlight.

```
main.py                     Decky backend (RPC methods, events)
bin/eclipse_launcher.py     Steam shortcut entry point
py_modules/eclipse/         Shared Python modules (no dependency on Decky)
  moonlight_config.py       Moonlight QSettings parser (hosts, apps, MAC, box art)
  moonlight.py              Moonlight detection, CLI arguments
  flatpak.py                Per-user Flathub installs (Moonlight, Sunshine)
  display.py                Display detection (DRM sysfs + EDID) for the Auto preset
  network.py                Async /serverinfo probes, Wake-on-LAN
  sunshine.py               Sunshine web API client, local service control
  host_display.py           Host display detection/switching via Sunshine output_name
  launch.py                 Launch request file + argv building
  presets.py / settings.py  Presets and persistent settings
src/                        React frontend (Quick Access panel + full-screen settings)
tests/                      Backend unit tests (stdlib unittest)
```

### Performance notes

- One `get_state` call loads everything the UI needs when the panel first opens.
- Hosts are probed with raw asyncio sockets, all addresses at the same time, with short timeouts. Polling only
  runs while the Quick Access menu or the settings page is visible. It is shared between the two, and it pauses
  while a stream is running.
- The Moonlight config is re-parsed only when its mtime changes. Moonlight and Sunshine detection results are
  cached.
- Settings changes appear in the UI right away and are written to disk in batches, so dragging a slider does
  not cause a write per step.

## Controls

| Where              | A           | X                   | Y                  |
| ------------------ | ----------- | ------------------- | ------------------ |
| Host row (QAM)     | Choose app  | Wake (when offline) | Host menu          |
| App list           | Stream      | Toggle favorite     | Add to library     |

## Development

Requirements: Node.js 20+, [pnpm](https://pnpm.io) and Python 3.9+.

```sh
pnpm install
pnpm build        # bundle the frontend into dist/
pnpm typecheck    # TypeScript checks
pnpm test         # backend unit tests
pnpm package      # build out/Eclipse-<version>.zip
```

To install on a Deck, turn on Decky's developer mode, copy the zip over, and use *Install plugin from ZIP*. You
can also copy `out/Eclipse` to `~/homebrew/plugins/` and restart Decky
(`sudo systemctl restart plugin_loader`).

Logs can be read on the device in *Settings → About → Logs*. The plugin log shows install output, Sunshine API
errors and so on, and refreshes live while an install runs. The launcher log shows the exact Moonlight command
lines. The files are:

- Backend: `~/homebrew/logs/Eclipse/`
- Launcher: `~/homebrew/settings/Eclipse/launcher.log`

Moonlight and Sunshine can be installed, update-checked, updated and uninstalled in *Settings → General → Apps*.
Only Flatpak installs are managed. The operation uses the installation the app lives in (`--user` or
`--system`). Uninstalling can also delete the app data.

The plugin checks GitHub Releases of the `repository` in `package.json` once a day (*About → Plugin updates*).
To install an update, use Decky's *Developer → Third-Party Plugins* (from URL or ZIP). The plugin folder is
owned by root, so the plugin cannot replace itself.

Installs can be cancelled from the progress view. This stops the whole `flatpak` process group.

## Security

Sunshine web UI credentials are optional. They are stored in `~/homebrew/settings/Eclipse/settings.json` with
`0600` permissions and are never sent to the frontend. Sunshine uses a self-signed certificate, so Eclipse does
not verify TLS certificates when it talks to the Sunshine API. Only save credentials for hosts on networks you
trust.

## License

BSD-3-Clause
