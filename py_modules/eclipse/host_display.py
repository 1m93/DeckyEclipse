"""Choose which of the host's displays Sunshine captures.

Moonlight has no way to pick a host display, so this goes through Sunshine:
the ``output_name`` setting selects the captured display, and it only takes
effect after a restart.

The Sunshine API facts this relies on (checked against Sunshine's
``confighttp.cpp``):

* ``GET /api/config`` returns every key of the config file, plus the
  ``status``, ``platform`` and ``version`` fields.
* ``POST /api/config`` **replaces** the whole config file with the posted keys.
  We therefore always post the complete config with only ``output_name``
  changed, and keep a backup of the original first.
* ``POST /api/restart`` restarts Sunshine, and the request may never get an
  answer.
* No endpoint lists displays. ``GET /api/logs`` returns the log, where
  Sunshine prints them at startup:
  - Linux (KMS): ``Monitor 0 is HDMI-A-1: <description>``. ``output_name``
    accepts the index or the connector name; we use the name because it
    stays the same when the enumeration order changes.
  - Windows: ``Currently available display devices:`` followed by a JSON list
    with ``device_id``, which is what ``output_name`` expects.

Everything here is blocking so the Steam launcher can reuse it. The backend
calls it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

from . import network
from .log import get_logger
from .sunshine import SunshineApi, describe_status

_META_KEYS = ("status", "platform", "version")
_KMS_MONITOR_RE = re.compile(r"Monitor (\d+) is ([A-Za-z0-9_-]+): ?(.*)$")
_KMS_START = "Start of KMS monitor list"
_WINDOWS_MARKER = "Currently available display devices:"
# Sunshine log lines start with "[2026-10-01 10:00:00.000]: Info: ...".
_LOG_TIME_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_MAX_LOG_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------- detection


def _line_time(log_text: str, position: int) -> Optional[str]:
    """Timestamp of the log line containing ``position``."""
    line_start = log_text.rfind("\n", 0, position) + 1
    match = _LOG_TIME_RE.match(log_text[line_start:line_start + 40])
    return match.group(1) if match else None


def _parse_kms(log_text: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """Monitors from the LAST KMS enumeration only.

    Sunshine enumerates monitors again on later starts/sessions; older lists
    may name monitors that are gone, so they must not be merged in.
    """
    start = log_text.rfind(_KMS_START)
    block = log_text[start:] if start >= 0 else log_text
    displays: dict[str, dict[str, str]] = {}
    for line in block.splitlines():
        if start >= 0 and "End of KMS monitor list" in line:
            break
        match = _KMS_MONITOR_RE.search(line)
        if match:
            _, connector, description = match.groups()
            label = f"{connector} ({description.strip()})" if description.strip() else connector
            displays[connector] = {"outputName": connector, "label": label}
    return list(displays.values()), (_line_time(log_text, start) if start >= 0 else None)


def _parse_windows(log_text: str) -> tuple[list[dict[str, str]], Optional[str]]:
    position = log_text.rfind(_WINDOWS_MARKER)
    if position < 0:
        return [], None
    start = log_text.find("[", position)
    if start < 0:
        return [], None
    try:
        devices, _ = json.JSONDecoder().raw_decode(log_text[start:])
    except ValueError:
        return [], None
    displays: list[dict[str, str]] = []
    for device in devices if isinstance(devices, list) else []:
        if not isinstance(device, dict) or not device.get("device_id"):
            continue
        name = device.get("friendly_name") or device.get("display_name") or device["device_id"]
        display_name = device.get("display_name")
        label = f"{name} ({display_name})" if display_name and display_name != name else str(name)
        displays.append({"outputName": str(device["device_id"]), "label": label})
    return displays, _line_time(log_text, position)


def parse_displays_with_time(log_text: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """Displays from the most recent enumeration in a Sunshine log, and when it happened."""
    windows = _parse_windows(log_text)
    return windows if windows[0] else _parse_kms(log_text)


def parse_displays(log_text: str) -> list[dict[str, str]]:
    """Extract the host's displays from a Sunshine log, best effort."""
    return parse_displays_with_time(log_text)[0]


def detect_displays(api: SunshineApi) -> dict[str, Any]:
    """Read the monitor list Sunshine logged most recently.

    The list is only as fresh as Sunshine's last enumeration; see
    :func:`rescan_displays` to force a new one.
    """
    status, text = api.request_sync("GET", "/api/logs", None, 10.0, _MAX_LOG_BYTES, True)
    if status != 200 or not isinstance(text, str):
        return {"ok": False, "displays": [], "detectedAt": None, "error": describe_status(status)}
    displays, detected_at = parse_displays_with_time(text)
    error = None if displays else "No monitor list found in the Sunshine log. Try Rescan, or add one manually."
    return {"ok": bool(displays), "displays": displays, "detectedAt": detected_at, "error": error}


def rescan_displays(api: SunshineApi, addresses: list[dict[str, Any]]) -> dict[str, Any]:
    """Restart Sunshine so it enumerates monitors again, then read the new list."""
    if any((network.probe_sync(a["address"], int(a["port"]), 1.5) or {}).get("busy") for a in addresses):
        return {"ok": False, "displays": [], "detectedAt": None,
                "error": "An app is still open on the host. Quit it first, then rescan."}
    api.request_sync("POST", "/api/restart", {}, 3.0)
    if not _wait_restart(addresses):
        return {"ok": False, "displays": [], "detectedAt": None, "error": "Sunshine did not come back after restarting"}
    return detect_displays(api)


# ---------------------------------------------------------------- switching


def current_output(api: SunshineApi) -> dict[str, Any]:
    """The display Sunshine is set to capture. Empty means its default (main) display."""
    config, error = read_config(api)
    if config is None:
        return {"ok": False, "current": None, "error": error}
    return {"ok": True, "current": str(config.get("output_name") or ""), "error": None}


def read_config(api: SunshineApi) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    status, payload = api.request_sync("GET", "/api/config", None, 8.0)
    if status != 200 or not isinstance(payload, dict) or payload.get("status") is not True:
        return None, describe_status(status) if status != 200 else "Unexpected Sunshine config response"
    return {k: v for k, v in payload.items() if k not in _META_KEYS}, None


def _backup(config: dict[str, Any], backup_path: Optional[str]) -> None:
    """Keep the original config the first time we touch it."""
    if not backup_path or os.path.exists(backup_path):
        return
    try:
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, sort_keys=True)
        os.chmod(backup_path, 0o600)
    except OSError as exc:
        get_logger().warning("Could not back up the Sunshine config: %s", exc)


def _wait_restart(addresses: list[dict[str, Any]], timeout: float = 40.0) -> bool:
    """Wait for Sunshine to go down (restart started) and come back up."""

    def online() -> bool:
        return any(network.probe_sync(a["address"], int(a["port"]), 1.0) for a in addresses)

    down_deadline = time.monotonic() + 6.0
    while time.monotonic() < down_deadline and online():
        time.sleep(0.5)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if online():
            # Give the streaming side a moment after the HTTP server is back.
            time.sleep(2.0)
            return True
        time.sleep(1.0)
    return False


def switch_display(
    api: SunshineApi,
    output_name: str,
    addresses: list[dict[str, Any]],
    backup_path: Optional[str] = None,
) -> dict[str, Any]:
    """Point Sunshine at ``output_name`` and restart it if the value changed.

    An empty ``output_name`` removes the setting, so Sunshine uses its default
    (primary) display.
    """
    config, error = read_config(api)
    if config is None:
        return {"ok": False, "changed": False, "error": error}

    current = str(config.get("output_name") or "")
    if current == output_name:
        return {"ok": True, "changed": False, "error": None}

    # Restarting Sunshine would close the app that is open on the host (and any
    # stream attached to it), so only switch when no app is running.
    if any((network.probe_sync(a["address"], int(a["port"]), 1.5) or {}).get("busy") for a in addresses):
        return {
            "ok": False,
            "changed": False,
            "error": "An app is still open on the host. Quit it first, then switch displays.",
        }

    _backup(config, backup_path)
    updated = dict(config)
    if output_name:
        updated["output_name"] = output_name
    else:
        updated.pop("output_name", None)

    status, payload = api.request_sync("POST", "/api/config", updated, 8.0)
    if status != 200 or not (isinstance(payload, dict) and payload.get("status") in (True, "true")):
        return {"ok": False, "changed": False, "error": f"Saving the Sunshine config failed: {describe_status(status)}"}

    get_logger().info("Sunshine output_name %r -> %r, restarting", current, output_name)
    # Sunshine may restart before answering; an unreachable result is expected.
    api.request_sync("POST", "/api/restart", {}, 3.0)
    if not _wait_restart(addresses):
        return {"ok": False, "changed": True, "error": "Sunshine did not come back after switching the display"}
    return {"ok": True, "changed": True, "error": None}


def apply_host_selection(settings: dict[str, Any], host: dict[str, Any], backup_dir: str) -> dict[str, Any]:
    """Apply the display chosen for ``host`` before a stream, if any.

    No selection means "leave Sunshine's display setting alone".
    """
    cfg = (settings.get("hosts") or {}).get(host["uuid"]) or {}
    target = cfg.get("display")
    if not target:
        return {"ok": True, "changed": False, "error": None}
    if not cfg.get("sunshineUser") or not cfg.get("sunshinePassword"):
        return {"ok": False, "changed": False, "error": "Switching displays needs the host's Sunshine credentials"}

    online = next(
        (a for a in host["addresses"] if network.probe_sync(a["address"], int(a["port"]), 1.5)),
        None,
    )
    if not online:
        return {"ok": False, "changed": False, "error": f"{host['name']} is offline"}
    api = SunshineApi(online["address"], cfg["sunshineUser"], cfg["sunshinePassword"], int(cfg.get("sunshinePort") or 0))

    # Monitors come and go; never point Sunshine at one that is gone. If the
    # log cannot be read, trust the selection rather than blocking the stream.
    found = detect_displays(api)
    if found["ok"] and str(target) not in {d["outputName"] for d in found["displays"]}:
        known = (cfg.get("displays") or [])
        name = next((d.get("label") for d in known if d.get("outputName") == target), target)
        return {"ok": False, "changed": False, "missing": True,
                "error": f"Monitor '{name}' is not connected to {host['name']} any more. Choose another monitor."}

    backup_path = os.path.join(backup_dir, f"sunshine-config-backup-{host['uuid']}.json")
    return switch_display(api, str(target), [online], backup_path)
