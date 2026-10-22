"""Detect the active display to pick sensible default stream settings.

Detection works on any SteamOS device (Steam Deck, other handhelds, desktops,
HTPCs). It uses sysfs, which a normal user can read without root:

* ``/sys/class/drm/card*-*/status`` gives the connected outputs. The connector
  type tells a built-in panel (eDP/LVDS/DSI) apart from a monitor or TV.
* ``/sys/class/drm/card*-*/edid`` is the display's own description of itself.
  Its detailed timings give the native resolution and the highest refresh
  rate it supports at that resolution.
* ``/sys/class/drm/card*-*/modes`` is a fallback for the resolution when the
  EDID cannot be read (the first mode is the preferred one).
* ``/sys/class/dmi/id/product_name`` is only a last fallback for the refresh
  rate. It covers devices whose panel EDID is known to be missing or wrong.

An external display takes precedence over a built-in panel, which handles
docking. Detection runs again at every launch, so docking or undocking is
picked up without changing any setting.

Limits: sysfs cannot show the mode gamescope is actually driving. A refresh
rate limit set by the user, or a custom output resolution, is therefore not
reflected. With several external displays, the first connected one is used.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Optional

DRM_ROOT = "/sys/class/drm"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"

_CONNECTOR_RE = re.compile(r"^card\d+-(.+)$")
_MODE_RE = re.compile(r"^(\d+)x(\d+)")
_INTERNAL_PREFIXES = ("eDP", "LVDS", "DSI")
_EDID_HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
_EDID_BLOCK = 128
_CTA_EXTENSION_TAG = 0x02

# Refresh rate of built-in panels by DMI product name. Only used when the EDID
# gives no usable timing.
_PANEL_REFRESH_FALLBACK = {
    "Jupiter": 60,  # Steam Deck LCD
    "Galileo": 90,  # Steam Deck OLED
}

FALLBACK = {"width": 1280, "height": 800, "refreshHz": 60}


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(64 * 1024)
    except OSError:
        return b""


def _landscape(a: int, b: int) -> tuple[int, int]:
    # Handheld panels are often natively portrait (the Deck reports 800x1280).
    return (max(a, b), min(a, b))


def _parse_mode(mode: str) -> Optional[tuple[int, int]]:
    match = _MODE_RE.match(mode.strip())
    if not match:
        return None
    return _landscape(int(match.group(1)), int(match.group(2)))


# ---------------------------------------------------------------- EDID


def _parse_dtd(block: bytes) -> Optional[dict[str, Any]]:
    """Decode an 18-byte Detailed Timing Descriptor."""
    if len(block) < 18:
        return None
    pixel_clock_khz = (block[0] | (block[1] << 8)) * 10
    if pixel_clock_khz == 0:
        return None  # Display descriptor (name, serial...), not a timing.
    h_active = block[2] | ((block[4] & 0xF0) << 4)
    h_blank = block[3] | ((block[4] & 0x0F) << 8)
    v_active = block[5] | ((block[7] & 0xF0) << 4)
    v_blank = block[6] | ((block[7] & 0x0F) << 8)
    if block[17] & 0x80:
        return None  # Interlaced (e.g. 1080i TV modes) is never a useful stream target.
    total = (h_active + h_blank) * (v_active + v_blank)
    if not h_active or not v_active or not total:
        return None
    return {"width": h_active, "height": v_active, "refreshHz": pixel_clock_khz * 1000 / total}


def parse_edid(edid: bytes) -> list[dict[str, Any]]:
    """Return the detailed timings of an EDID, the preferred one first.

    Reads the base block and CTA-861 extension blocks. DisplayID extensions
    are not parsed. Some high refresh monitors only list their fastest modes
    there, and those then fall back to the base block timings.
    """
    if len(edid) < _EDID_BLOCK or edid[:8] != _EDID_HEADER:
        return []
    timings: list[dict[str, Any]] = []
    for offset in range(54, 126, 18):
        timing = _parse_dtd(edid[offset:offset + 18])
        if timing:
            timings.append(timing)

    extensions = edid[126]
    for index in range(1, extensions + 1):
        block = edid[index * _EDID_BLOCK:(index + 1) * _EDID_BLOCK]
        if len(block) < _EDID_BLOCK or block[0] != _CTA_EXTENSION_TAG:
            continue
        dtd_start = block[2]
        if dtd_start < 4:
            continue
        for offset in range(dtd_start, _EDID_BLOCK - 18, 18):
            timing = _parse_dtd(block[offset:offset + 18])
            if not timing:
                break
            timings.append(timing)
    return timings


def native_timing(edid: bytes) -> Optional[dict[str, Any]]:
    """Native resolution (the preferred timing) and its best refresh rate."""
    timings = parse_edid(edid)
    if not timings:
        return None
    native = _landscape(timings[0]["width"], timings[0]["height"])
    refresh = max(
        t["refreshHz"] for t in timings if _landscape(t["width"], t["height"]) == native
    )
    return {"width": native[0], "height": native[1], "refreshHz": int(round(refresh))}


# ---------------------------------------------------------------- outputs


def connected_outputs(root: str = DRM_ROOT) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return outputs
    for entry in entries:
        match = _CONNECTOR_RE.match(entry)
        if not match:
            continue
        path = os.path.join(root, entry)
        if _read(os.path.join(path, "status")) != "connected":
            continue
        name = match.group(1)
        timing = native_timing(_read_bytes(os.path.join(path, "edid")))
        modes = [m for m in (_read(os.path.join(path, "modes")) or "").splitlines() if m.strip()]
        native = (timing["width"], timing["height"]) if timing else (_parse_mode(modes[0]) if modes else None)
        if not native:
            continue
        outputs.append({
            "name": name,
            "internal": name.startswith(_INTERNAL_PREFIXES),
            "native": native,
            "refreshHz": timing["refreshHz"] if timing else None,
            "source": "edid" if timing else "modes",
        })
    return outputs


def detect(root: str = DRM_ROOT, dmi_path: str = DMI_PRODUCT) -> dict[str, Any]:
    """Return the display streams should target: resolution, refresh rate and origin."""
    outputs = connected_outputs(root)
    external = next((o for o in outputs if not o["internal"]), None)
    chosen = external or next((o for o in outputs if o["internal"]), None)

    refresh = chosen["refreshHz"] if chosen else None
    refresh_source = "edid" if refresh else None
    if not refresh and not external:
        # Built-in panel without a usable EDID: use the known-device table.
        refresh = _PANEL_REFRESH_FALLBACK.get(_read(dmi_path) or "")
        refresh_source = "device" if refresh else None
    if not refresh:
        refresh, refresh_source = FALLBACK["refreshHz"], "default"

    width, height = chosen["native"] if chosen else (FALLBACK["width"], FALLBACK["height"])
    return {
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "refreshHz": int(refresh),
        "refreshSource": refresh_source,
        "output": chosen["name"] if chosen else None,
        "external": external is not None,
    }


def recommended_bitrate(width: int, height: int, fps: int) -> int:
    """Bitrate (Kbps) for a LAN stream, tuned so 1080p60 HEVC gets ~30 Mbps.

    Bitrate grows sub-linearly with pixel count, and above 60 FPS with the
    square root of the frame rate (as Moonlight's own default does).
    """
    pixel_factor = (width * height / (1920 * 1080)) ** 0.75
    fps_factor = (fps if fps <= 60 else math.sqrt(fps / 60) * 60) / 60
    kbps = 30000 * pixel_factor * fps_factor
    return int(min(150_000, max(3000, round(kbps / 500) * 500)))


def auto_values(info: dict[str, Any]) -> dict[str, Any]:
    fps = int(min(120, max(30, info.get("refreshHz") or 60)))
    width, height = int(info["width"]), int(info["height"])
    return {
        "resolution": f"{width}x{height}",
        "fps": fps,
        "bitrateKbps": recommended_bitrate(width, height, fps),
        "videoCodec": "HEVC",
    }


def describe(info: dict[str, Any]) -> str:
    resolution = info["resolution"].replace("x", "×")
    where = "built-in screen" if info.get("output") and not info.get("external") else info.get("output")
    text = f"{resolution} @ {info['refreshHz']} Hz"
    return f"{text}, {where}" if where else text
