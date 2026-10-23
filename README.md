# Eclipse

Eclipse is a [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that lets you use
[Moonlight](https://moonlight-stream.org) and [Sunshine](https://app.lizardbyte.dev/Sunshine/) from SteamOS
Gaming Mode. Everything works with a controller, so you never need Desktop Mode.

## Install

1. Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) and turn on its developer mode
   (Decky settings → General → Developer mode).
2. In Decky settings → Developer, use *Install plugin from URL* with the `.zip` link of the
   [latest release](https://github.com/1m93/DeckyEclipse/releases/latest), or download the zip and use
   *Install plugin from ZIP*.
3. Open Eclipse in the Quick Access menu and pick what you use.

Eclipse checks for new releases once a day and tells you; updating works the same way.

## Releasing

Bump `version` in `package.json`, optionally add `.github/release-notes/v<version>.md`, commit, then push a tag
`v<version>`. The *Release* workflow checks, builds and publishes the zip.

## Features

Both halves are optional. When you first open the plugin, it asks what you use: **play games from a PC**
(Moonlight), **share this device's games** (Sunshine), or both. Only the parts you pick are shown, and nothing is
installed until you press *Install*. You can change this later in *Settings → General*.

The Quick Access menu treats both halves the same:

- At the top, whatever is happening right now: the stream you are playing, devices streaming from this one,
  and devices asking to pair. Each comes with its action (*End stream*, *Enter PIN*).
- Below it, two tabs: **Play** (hosts and stream quality) and **Share** (the Sunshine server, this device's
  address, pairing and the most used sharing options). The menu remembers the last tab and opens on the side where something is happening.
- The full-screen settings follow the same order: *Streaming* (profiles, stream quality and hosts), then
  *Sharing*, then *General*, *Troubleshooting* (connection checks and logs) and *About*.

**Backup**: *General → Backup* saves profiles, stream and host settings, Sunshine's options and the Steam games
offered to other devices (passwords only when asked for) to `~/Documents/Eclipse`, or to the `Eclipse` folder of a
microSD card or USB drive, which survives reinstalling SteamOS. Backups on any of them are listed for restore,
also on the welcome screen of a fresh install. Pairing is not part of a backup.

**Streaming (Moonlight client)**

- Lists paired hosts from Moonlight's own config and shows them as online, busy or offline, with latency.
- Pick an app and stream with one button. The last-played app gets focus first, so **A, A** resumes it.
- Wake-on-LAN: the MAC address is read from Moonlight or Sunshine, or you can enter it yourself. Offline hosts
  are woken automatically before a stream starts.
- *Add host* lists the PCs running Sunshine on your network (it asks each address of this device's /24 network
  for `/serverinfo`), so you do not need to know the PC's IP address. You can still type one. When Tailscale
  runs on this device, PCs in your tailnet are listed too ("via Tailscale"). For a PC already added at home,
  pressing it saves its Tailscale address, so the same host streams away from home (Eclipse then hands Moonlight
  the address that answers).
- *Network test* (host settings, Y on a host, or the *Playing* card) measures delay (input lag) and stability
  (jitter, failed tries: stutter) to the host, reads this device's link (wired, VPN, or Wi-Fi band, signal and
  rate) and suggests a bitrate, with a button to lower the one in use (global settings, or the host's profile).
  Over the internet or a VPN it stays at 15 Mbps: the host's upload speed cannot be measured from here.
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
- **Profiles**: *Save as profile* keeps the current stream settings under a name (for example TV or Hotel
  Wi-Fi). A profile is picked like a preset, in the settings or the Quick Access menu, and every change made
  while it is selected is saved to it. Profiles can be renamed and deleted.
- You can give a host its own preset or profile, for example a lower one for a remote PC.
- **Host display selection** for hosts with several monitors. Moonlight cannot choose a host display, so Eclipse
  changes Sunshine's *Output Name* (`output_name`) setting through the Sunshine web API and restarts Sunshine
  before the stream starts. This needs the host's Sunshine credentials.
  - Displays are detected from the Sunshine log. On Linux hosts Eclipse reads the `Monitor N is HDMI-A-1` lines
    and uses the connector name. On Windows hosts it reads the `Currently available display devices` JSON and
    uses `device_id`. You can also add displays by hand.
  - Sunshine's API replaces the whole config file on save. Eclipse reads the full config, changes only
    `output_name`, and writes everything back. Before the first change it saves a backup to
    `~/homebrew/settings/Eclipse/sunshine-config-backup-<host>.json`.
  - The monitor list is re-read every time the app picker or the host page opens. Only Sunshine's most
    recent monitor list is used. Monitors that are gone are dropped, and so is a selection pointing at one.
    Names you gave and monitors added by hand are kept. Sunshine only lists monitors when it starts, so
    *Rescan* restarts Sunshine to pick up a monitor plugged in since then.
  - *Ask which monitor each time* (per host) shows a freshly read monitor list whenever a stream starts,
    including Resume from the Quick Access menu. If the selected monitor is unplugged, starting a stream asks
    for another one instead of failing.
  - Nothing is written when the display is already selected. Nothing is changed while the host is busy with
    another stream, because the restart would end it. *Keep Sunshine's setting* (the default) never touches the
    config.
- "Add to Steam library" makes a separate non-Steam shortcut for a host/app pair, using Moonlight's cached box
  art. The shortcut wakes the host if needed and starts streaming.
- A *Playing* card at the top of the Quick Access menu with *End stream*, plus *Quit app on host* when ending
  the stream leaves the app open. When *End stream* also closes the game on the host, Eclipse asks first
  (*General → Ask before closing the game on the host*).
- *Troubleshooting → Play* checks the connection to a host step by step (network, the PC answers, Sunshine
  runs, paired, Wake-on-LAN, its Sunshine login) and offers the fix (*Wake*, *Pair again*). Stream failure
  notifications open it.
- **Ending a stream also ends the session on the host by default** (*End the host session too* in *Streaming*
  settings). Moonlight gets `--quit-after`. Steam's End stream and Exit game kill Moonlight, which can leave the
  app open on the host, so Eclipse checks the host a few seconds after the stream ends. If an app is still
  open, Eclipse closes it through the Sunshine API when credentials are saved, or with `moonlight quit`
  otherwise. Note that Sunshine reports a host as busy whenever an app is open on it, even with no client
  connected.
- If Moonlight is not installed, Eclipse can install it from Flathub (per-user install, so there is no password
  prompt).

**Sunshine on this device (host)**

- Install **Sunshine for Gaming Mode** when you need it (see below). Installing, updating, repairing and
  removing it ask for your SteamOS (sudo) password; Eclipse passes it to sudo for that step and never saves it.
  A fresh SteamOS has no password: Eclipse notices (from `passwd`'s prompts) and lets you choose one right
  there, so there is no trip to Desktop Mode.
- *Set up automatically* creates Sunshine's web UI login with a generated password and shows it once. Eclipse
  uses it to pair devices and end streams. *Show password* in *Sharing* shows it again, for the web page.
- **Turn off automatically** (off by default) saves battery: Sunshine turns itself off a minute after the
  stream ends, or after 15/30/60 minutes without a stream.
- Sunshine only runs when you start it from the Quick Access menu or the *Sharing* page. **Start automatically**
  is a separate option and is off by default. With it on, Eclipse starts Sunshine when the plugin loads and
  enables the systemd user service. With it off, Eclipse disables the service, so Sunshine never starts by
  itself.
- A Sunshine Flatpak is still detected and controlled, but it cannot stream Gaming Mode. Eclipse offers to
  install Sunshine for Gaming Mode instead and to remove the Flatpak.
- Pair Moonlight clients with your Deck and see the real result (paired, wrong PIN, timed out).
- **Steam games for other devices**: *Add Steam games* lists the installed games and adds the chosen ones to
  Sunshine's `apps.json`, so Moonlight on another device starts them directly (`steam steam://rungameid/<id>`).
  Covers come from Steam's cache, converted to the PNG Sunshine needs. Closing the app (Quit in Moonlight, or
  *Close app* here) also ends the game on this device.
- **Discovery fix**: on SteamOS, Sunshine may not be allowed to publish itself through avahi-daemon
  (`avahi::entry_group_new() failed: Not permitted`), so Moonlight never lists the device. Eclipse notices the
  error in Sunshine's log and answers the mDNS queries for `_nvstream._tcp` itself (*Help other devices find
  this one*, on by default).
- The *Sharing* card names the devices streaming from here as far as the network shows: those that talked to
  Sunshine just before the stream, by reverse DNS name when the router knows it.
- **Turn off on low battery** (off by default): Sunshine turns off when the battery falls to 10/20/30 % while
  not charging, after the current stream (or at 5 % during it). If you turn Sunshine on while already below the
  level, it stays on.
- **Sunshine's own options** in *Sharing*, so the web page is rarely needed: the name shown in Moonlight,
  maximum bitrate, encoder, HEVC/AV1, minimum frame rate, error correction, audio, controllers (type, Back for
  Home), keyboard, mouse, touch, encryption, UPnP, IPv6, web page access, timeout and log detail. The Share tab
  has the most used ones. Eclipse edits `sunshine.conf` directly (no login needed), keeps every line it does
  not know and drops values equal to Sunshine's default. Sunshine reads the file when it starts, so while it
  runs Eclipse offers *Restart now*, and asks first when devices are streaming.
  - Newer Sunshine versions list the devices waiting to pair. They show up live at the top of the Quick Access
    menu and on the *Sharing* page.
  - Older versions take the PIN only. Eclipse then confirms pairing by watching for the new device in the
    paired list.
- See the paired devices and unpair them.
- See when other devices stream from this one: the Quick Access menu shows how many are connected, which app
  and for how long, with *End stream*. A notification tells you when a device starts or stops streaming (*General
  → Notifications*, on by default). Sunshine has no API for its sessions, so Eclipse combines `/serverinfo` (the
  open app) with Sunshine's log (clients connecting and leaving); Sunshine does not log which device it is.
  *End stream* closes the open app through the Sunshine API with the saved login, or restarts Sunshine (after
  asking) without one.
- When the Deck pairs with a PC, Eclipse reports the outcome after Moonlight's pairing window closes.

### Sunshine for Gaming Mode

The Flatpak and AppImage builds of Sunshine cannot capture Gaming Mode: their KMS capture is compiled out.
Eclipse installs Sunshine's official Ubuntu 22.04 build instead, into `/home/.ecl/sunshine`:

- The installer (`bin/eclipse_sunshine_root.py`) runs as root through sudo. It downloads the release from
  GitHub and the two libraries SteamOS lacks from Ubuntu (checked by SHA-256), and builds the installation in a
  fresh root-owned directory. `/home` survives SteamOS updates.
- Sunshine reads its web UI from the compiled-in `/usr/share/sunshine`, which is read-only on SteamOS. The
  installer rewrites that path in the binary to `/home/.ecl/sunshine`. It has the same length and the same
  ending: the compiler stores the literal `"sunshine"` (the name of the config directory) in the last bytes of
  `"/usr/share/sunshine"`, so those bytes must stay. Eclipse versions that used `/home/.eclipse-sun/` made
  Sunshine keep its data in `~/.config/pse-sun`; *Repair* moves the installation and that data back, with the
  login, paired devices and server certificate.
- `bin/eclipse-sunshine` (`native/sunshine_launcher.c`) has the `cap_sys_admin,cap_sys_nice` file
  capabilities and starts Sunshine with them as ambient capabilities. Sunshine drops them right after startup
  except in its DRM worker thread. The launcher only passes them on while the installation is owned by root.
- `lib/libeclipse-sunshine.so` (`native/sunshine_preload.c`) is preloaded into Sunshine:
  - Sunshine 2026.906 and newer stream a black picture in Gaming Mode
    ([LizardByte/Sunshine#5839](https://github.com/LizardByte/Sunshine/issues/5839)): they capture
    gamescope's overlay plane instead of the primary plane. The library hides overlay planes from Sunshine, so
    it captures the primary plane. Gamescope does not have to force composition, so no extra battery is used.
  - When Sunshine restarts itself, it re-executes its own binary. The library sends that through the launcher,
    so Sunshine keeps its capabilities and libraries.
- The installer also adds Sunshine's udev rules and loads `uhid` for virtual controllers.
- Sunshine 2026.914 adds a second record when a device pairs again and then rejects that device (Moonlight:
  "Certificate verification failed", Error 401). Eclipse removes the older records: from the state file before
  Sunshine starts, and through the API (with the saved login) as soon as the file changes.
- Sunshine runs as the systemd user service `eclipse-sunshine.service`, with its config in
  `~/.config/sunshine`. Eclipse sets `system_tray = disabled` there, because Gaming Mode has no tray.
- After an Eclipse update that changes these helpers, the *Sharing* page offers **Repair**, which reinstalls
  the same version.

The helpers are cross-compiled with zig (installed from PyPI) by `pnpm native`. `pnpm package` does this
automatically.

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
  flatpak.py                Flathub installs (Moonlight), Flatpak detection
  display.py                Display detection (DRM sysfs + EDID) for the Auto preset
  network.py                Async /serverinfo probes, Wake-on-LAN
  sunshine.py               Sunshine web API client, local service control
  sunshine_native.py        Sunshine for Gaming Mode (shared with the root installer)
  sunshine_config.py        Options of the local Sunshine (sunshine.conf)
  sunshine_apps.py          Steam library and Sunshine's apps.json
  mdns.py                   mDNS responder announcing Sunshine when avahi refuses
  nettest.py / tailscale.py Connection test, Tailscale peers
  battery.py / backup.py    Low-battery guard, settings backups
bin/eclipse_sunshine_root.py  Root installer of Sunshine for Gaming Mode (run with sudo)
native/                     C helpers of Sunshine for Gaming Mode (launcher, preload library)
  host_display.py           Host display detection/switching via Sunshine output_name
  launch.py                 Launch request file + argv building
  presets.py / settings.py  Presets, saved profiles and persistent settings
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

Requirements: Node.js 20+, [pnpm](https://pnpm.io) and Python 3.9+. `pnpm package` also installs zig from
PyPI into `~/.cache/decky-eclipse/toolchain` the first time.

```sh
pnpm install
pnpm build        # bundle the frontend into dist/
pnpm typecheck    # TypeScript checks
pnpm test         # backend unit tests
pnpm native       # cross-compile the native helpers into out/native/
pnpm package      # build out/Eclipse-<version>.zip
```

To install on a Deck, turn on Decky's developer mode, copy the zip over, and use *Install plugin from ZIP*. You
can also copy `out/Eclipse` to `~/homebrew/plugins/` and restart Decky
(`sudo systemctl restart plugin_loader`).

Logs can be read on the device in *Settings → Troubleshooting → Logs*. The plugin log shows install output, Sunshine API
errors and so on, and refreshes live while an install runs. The launcher log shows the exact Moonlight command
lines. The files are:

- Backend: `~/homebrew/logs/Eclipse/`
- Launcher: `~/homebrew/settings/Eclipse/launcher.log`

Moonlight and Sunshine can be installed, update-checked, updated and uninstalled in *Settings → General → Apps*.
Eclipse manages Flatpak installs and Sunshine for Gaming Mode. Flatpak operations use the installation the app
lives in (`--user` or `--system`). Sunshine for Gaming Mode is updated from GitHub Releases. Uninstalling can
also delete the app data.

The plugin checks GitHub Releases of the `repository` in `package.json` once a day (*About → Plugin updates*).
To install an update, use Decky's *Developer → Third-Party Plugins* (from URL or ZIP). The plugin folder is
owned by root, so the plugin cannot replace itself.

Flatpak installs can be cancelled from the progress view. This stops the whole `flatpak` process group. The
root installer of Sunshine for Gaming Mode cannot be stopped from the plugin, because it runs as root.

## Security

Sunshine web UI credentials are optional. They are stored in `~/homebrew/settings/Eclipse/settings.json` with
`0600` permissions and are never sent to the frontend. Sunshine uses a self-signed certificate, so Eclipse does
not verify TLS certificates when it talks to the Sunshine API. Only save credentials for hosts on networks you
trust.

The sudo password for Sunshine for Gaming Mode is written to sudo's standard input and is neither stored nor
logged. Sunshine for Gaming Mode holds `cap_sys_admin` in its DRM worker thread, which is close to root
access. Its files are owned by root, so other programs of the `deck` user cannot replace them.

## License

BSD-3-Clause
