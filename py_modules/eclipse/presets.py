"""Stream setting defaults and built-in presets.

This is the single source of truth for presets: the frontend fetches them
through the backend and the launcher uses them to build Moonlight arguments.
"""

from __future__ import annotations

from typing import Any, Optional

from . import display

STREAM_DEFAULTS: dict[str, Any] = {
    "resolution": "1280x800",
    "fps": 60,
    "bitrateKbps": 15000,
    "videoCodec": "HEVC",
    "videoDecoder": "auto",
    "hdr": False,
    "yuv444": False,
    "audioConfig": "stereo",
    "audioOnHost": False,
    "displayMode": "fullscreen",
    "vsync": False,
    "framePacing": True,
    "performanceOverlay": False,
    "gameOptimization": True,
    # End the session on the host (close the app) when the stream ends.
    "quitAppAfter": True,
    "multiController": True,
    "absoluteMouse": False,
    "muteOnFocusLoss": False,
    "keepAwake": True,
}

# Values a preset overrides; everything else stays as configured by the user.
PRESETS: list[dict[str, Any]] = [
    {
        "id": "deck",
        "label": "Steam Deck (800p 60 FPS)",
        "values": {"resolution": "1280x800", "fps": 60, "bitrateKbps": 15000, "videoCodec": "HEVC"},
    },
    {
        "id": "deck-oled",
        "label": "Steam Deck OLED (800p 90 FPS)",
        "values": {"resolution": "1280x800", "fps": 90, "bitrateKbps": 22000, "videoCodec": "HEVC"},
    },
    {
        "id": "battery",
        "label": "Battery saver (800p 30 FPS)",
        "values": {"resolution": "1280x800", "fps": 30, "bitrateKbps": 8000, "videoCodec": "HEVC"},
    },
    {
        "id": "remote",
        "label": "Remote / weak Wi-Fi (720p 60 FPS)",
        "values": {"resolution": "1280x720", "fps": 60, "bitrateKbps": 6000, "videoCodec": "HEVC"},
    },
    {
        "id": "docked-1080",
        "label": "1080p 60 FPS",
        "values": {"resolution": "1920x1080", "fps": 60, "bitrateKbps": 30000, "videoCodec": "HEVC"},
    },
    {
        "id": "docked-1440",
        "label": "1440p 60 FPS",
        "values": {"resolution": "2560x1440", "fps": 60, "bitrateKbps": 50000, "videoCodec": "HEVC"},
    },
    {
        "id": "docked-4k",
        "label": "4K 60 FPS",
        "values": {"resolution": "3840x2160", "fps": 60, "bitrateKbps": 80000, "videoCodec": "HEVC"},
    },
]

PRESETS_BY_ID = {preset["id"]: preset for preset in PRESETS}

# Dynamic preset matching the detected display (the default).
AUTO_PRESET = "auto"
CUSTOM_PRESET = "custom"
# Per-host value meaning "follow the global configuration".
INHERIT_PRESET = "global"


def auto_preset(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": AUTO_PRESET,
        "label": f"Auto: match display ({display.describe(info)})",
        "values": display.auto_values(info),
    }


def all_presets(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Built-in presets with the display-dependent Auto preset first."""
    return [auto_preset(info), *PRESETS]


def effective_stream(
    settings: dict[str, Any],
    host_uuid: Optional[str] = None,
    display_info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve the stream configuration used for ``host_uuid``.

    The global ``stream`` block is the base. The Auto preset re-detects the
    display every time, so docking changes the stream without new settings.
    When a host is pinned to a preset, that preset's values win for that host.
    """
    stream = {**STREAM_DEFAULTS, **(settings.get("stream") or {})}
    selected = settings.get("activePreset")
    if host_uuid:
        host_cfg = (settings.get("hosts") or {}).get(host_uuid) or {}
        if host_cfg.get("preset") and host_cfg["preset"] != INHERIT_PRESET:
            selected = host_cfg["preset"]

    if selected == AUTO_PRESET:
        stream.update(display.auto_values(display_info or display.detect()))
    elif selected in PRESETS_BY_ID:
        stream.update(PRESETS_BY_ID[selected]["values"])
    return stream
