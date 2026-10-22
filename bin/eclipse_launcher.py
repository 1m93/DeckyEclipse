#!/usr/bin/env python3
"""Steam shortcut entry point for Eclipse.

Steam runs this script through ``/usr/bin/python3`` for two kinds of
non-Steam shortcuts:

* The shared launcher shortcut (no arguments). It consumes the one-shot
  request written by the plugin backend; without a request it opens the
  Moonlight UI, so launching it from the library still does something useful.
* Library shortcuts (``--library <id>``) created with "Add to Steam library".
  They stream a fixed host/app and wake the host first when needed.

The script finally ``exec``s Moonlight so Steam tracks the Moonlight process
itself (overlay, controller config, playtime and "Exit game" all work).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR / "py_modules"))

from eclipse import LAUNCHER_LOG, SETTINGS_FILE  # noqa: E402
from eclipse import host_display, launch, moonlight, network  # noqa: E402
from eclipse.moonlight_config import MoonlightConfig  # noqa: E402
from eclipse.settings import load_settings_readonly  # noqa: E402

_MAX_LOG_BYTES = 256 * 1024


def settings_dir() -> Path:
    override = os.environ.get("DECKY_ECLIPSE_SETTINGS_DIR")
    if override:
        return Path(override)
    # Decky layout: <homebrew>/plugins/<name> and <homebrew>/settings/<name>.
    return PLUGIN_DIR.parents[1] / "settings" / PLUGIN_DIR.name


def log(message: str) -> None:
    path = settings_dir() / LAUNCHER_LOG
    try:
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.old"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass
    print(message, file=sys.stderr)


def library_request(settings: dict, entry_id: str) -> dict | None:
    entry = (settings.get("shortcuts", {}).get("library") or {}).get(entry_id)
    if not entry:
        log(f"Unknown library entry: {entry_id}")
        return None
    return {"action": "stream", "host": entry["hostName"], "hostUuid": entry["hostUuid"], "app": entry["appName"]}


def wake_if_needed(settings: dict, host_uuid: str) -> None:
    """Wake an offline host and wait for it, so library shortcuts just work."""
    behavior = settings.get("behavior") or {}
    if not behavior.get("autoWake", True):
        return
    home = str(Path.home())
    host = next((h for h in MoonlightConfig(home).hosts() if h["uuid"] == host_uuid), None)
    if not host or not host["addresses"]:
        return

    def online() -> bool:
        return any(network.probe_sync(a["address"], a["port"], 1.0) for a in host["addresses"])

    if online():
        return
    host_cfg = (settings.get("hosts") or {}).get(host_uuid) or {}
    mac = network.normalize_mac(host_cfg.get("mac")) or host.get("mac")
    if not mac:
        log(f"{host['name']} is offline and has no MAC address for Wake-on-LAN")
        return

    timeout = float(behavior.get("wakeTimeoutS") or 90)
    hints = [a["address"] for a in host["addresses"]]
    log(f"Waking {host['name']} ({mac})")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        network.send_wol(mac, hints)
        time.sleep(3)
        if online():
            log(f"{host['name']} is online")
            # Give the streaming service a moment to finish starting.
            time.sleep(3)
            return
    log(f"{host['name']} did not wake up within {int(timeout)}s, trying anyway")


def select_host_display(settings: dict, host_uuid: str) -> None:
    """Library shortcuts bypass the backend, so apply the host display choice here."""
    host = next((h for h in MoonlightConfig(str(Path.home())).hosts() if h["uuid"] == host_uuid), None)
    if not host:
        return
    result = host_display.apply_host_selection(settings, host, str(settings_dir()))
    if result["changed"]:
        log(f"Switched {host['name']} to display {settings['hosts'][host_uuid]['display']}")
    if not result["ok"]:
        # Streaming the current display beats not streaming at all.
        log(f"Display switch failed, streaming anyway: {result['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Eclipse Moonlight launcher")
    parser.add_argument("--library", help="Library entry id to stream")
    args = parser.parse_args()

    directory = settings_dir()
    settings = load_settings_readonly(str(directory / SETTINGS_FILE))

    if args.library:
        request = library_request(settings, args.library)
        if request is None:
            return 1
    else:
        request = launch.consume_request(str(directory)) or {"action": "gui"}

    resolved = moonlight.resolve(settings.get("moonlight") or {}, str(Path.home()))
    if not resolved["installed"]:
        log(f"Cannot launch: {resolved.get('error')}")
        return 1

    if args.library and request.get("hostUuid"):
        wake_if_needed(settings, request["hostUuid"])
        select_host_display(settings, request["hostUuid"])

    argv = launch.build_argv(resolved["command"], request, settings)
    log(f"Launching: {' '.join(argv)}")
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        log(f"exec failed: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
