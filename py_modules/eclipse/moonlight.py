"""Moonlight client detection, command-line building and installation."""

from __future__ import annotations

import os
import shlex
from typing import Any, Callable, Optional

from . import FLATPAK_MOONLIGHT
from . import flatpak, proc

# (setting key, moonlight flag) pairs for --flag / --no-flag toggles.
_TOGGLES = (
    ("vsync", "vsync"),
    ("framePacing", "frame-pacing"),
    ("performanceOverlay", "performance-overlay"),
    ("gameOptimization", "game-optimization"),
    ("quitAppAfter", "quit-after"),
    ("multiController", "multi-controller"),
    ("absoluteMouse", "absolute-mouse"),
    ("muteOnFocusLoss", "mute-on-focus-loss"),
    ("audioOnHost", "audio-on-host"),
    ("keepAwake", "keep-awake"),
    ("hdr", "hdr"),
)

# Newer flags are only passed when enabled, so older Moonlight builds that do
# not know them keep working with the defaults.
_OPT_IN_FLAGS = (
    ("yuv444", "yuv444"),
)

_CHOICES = {
    "videoCodec": ("video-codec", {"auto", "H.264", "HEVC", "AV1"}),
    "videoDecoder": ("video-decoder", {"auto", "hardware", "software"}),
    "audioConfig": ("audio-config", {"stereo", "5.1-surround", "7.1-surround"}),
    "displayMode": ("display-mode", {"fullscreen", "borderless", "windowed"}),
}


def build_stream_args(stream: dict[str, Any]) -> list[str]:
    """Translate the plugin's stream settings into Moonlight CLI options."""
    args: list[str] = []

    resolution = str(stream.get("resolution") or "")
    width, _, height = resolution.partition("x")
    if width.isdigit() and height.isdigit():
        args += ["--resolution", f"{int(width)}x{int(height)}"]

    fps = int(stream.get("fps") or 0)
    if 10 <= fps <= 480:
        args += ["--fps", str(fps)]

    bitrate = int(stream.get("bitrateKbps") or 0)
    if 500 <= bitrate <= 500_000:
        args += ["--bitrate", str(bitrate)]

    for key, (flag, allowed) in _CHOICES.items():
        value = stream.get(key)
        if value in allowed:
            args += [f"--{flag}", value]

    for key, flag in _TOGGLES:
        if key in stream:
            args.append(f"--{flag}" if stream[key] else f"--no-{flag}")
    for key, flag in _OPT_IN_FLAGS:
        if stream.get(key):
            args.append(f"--{flag}")
    return args


def _flatpak_info(home: Optional[str]) -> Optional[dict[str, Any]]:
    rc, out, _ = proc.run_sync(["flatpak", "info", FLATPAK_MOONLIGHT], timeout=10, home=home)
    if rc != 0:
        return None
    version = None
    installation = None
    for line in out.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Version":
            version = value.strip()
        elif key == "Installation":
            installation = value.strip()
    return {"version": version, "installation": installation}


def resolve(config: dict[str, Any], home: Optional[str] = None) -> dict[str, Any]:
    """Find the Moonlight command to run according to the user's source setting.

    Blocking (it shells out to ``flatpak``); call through ``asyncio.to_thread``
    from the backend.
    """
    source = config.get("source") or "auto"
    custom = (config.get("customCommand") or "").strip()

    if source == "custom":
        if not custom:
            return {"installed": False, "source": "custom", "command": [], "error": "Custom command is empty"}
        return {"installed": True, "source": "custom", "command": shlex.split(custom), "version": None}

    if source in ("auto", "flatpak"):
        info = _flatpak_info(home)
        if info:
            return {
                "installed": True,
                "source": "flatpak",
                "command": ["flatpak", "run", FLATPAK_MOONLIGHT],
                "version": info["version"],
                "installation": info["installation"],
            }

    if source in ("auto", "native"):
        for name in ("moonlight", "moonlight-qt"):
            path = proc.which(name, home)
            if path:
                return {"installed": True, "source": "native", "command": [path], "version": None}

    return {"installed": False, "source": source, "command": [], "error": "Moonlight was not found"}


async def install_flatpak(on_line: Callable[[str], None], home: Optional[str] = None) -> bool:
    """Install Moonlight from Flathub into the user installation."""
    return await flatpak.install(FLATPAK_MOONLIGHT, on_line, home)


async def kill_running(home: Optional[str] = None) -> None:
    """Terminate any Moonlight instance (used when a stream must be force-stopped)."""
    await proc.run(["flatpak", "kill", FLATPAK_MOONLIGHT], timeout=10, home=home)
    for pid in proc.find_processes(("moonlight", "moonlight-qt")):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
