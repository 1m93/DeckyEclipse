"""Persistent JSON settings with atomic writes and secret masking."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from typing import Any

from .log import get_logger
from .presets import AUTO_PRESET, STREAM_DEFAULTS

SCHEMA_VERSION = 2

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    # Which parts of the plugin the user opted into during first-run setup.
    # Nothing is installed or started for a feature that is not enabled.
    "features": {
        "configured": False,
        "moonlight": False,
        "sunshine": False,
    },
    "activePreset": AUTO_PRESET,
    "stream": dict(STREAM_DEFAULTS),
    # Stream profiles saved by the user, keyed by id ("p-..."): {"name": str, "stream": {...}}.
    # Selected like a preset (activePreset, or a host's preset).
    "profiles": {},
    # Keyed by Moonlight host UUID. Fields: mac, preset, lastApp,
    # sunshineUser, sunshinePassword, sunshinePort, favorites.
    "hosts": {},
    "moonlight": {
        # auto | flatpak | native | custom
        "source": "auto",
        "customCommand": "",
    },
    "sunshine": {
        # Credentials of the Sunshine server running on this device.
        "user": "",
        "password": "",
        "port": 47990,
        "deviceName": "",
        # Start Sunshine when the plugin loads (and enable its user service).
        # Off by default: Sunshine then only runs when started manually.
        "autostart": False,
        # Toast when another device starts or stops streaming from this one.
        "notifySessions": True,
        # Toast when a device asks to pair (it waits for its PIN to be entered here).
        "notifyPairing": True,
        # Turn Sunshine off when nobody streams: "never", "afterStream" (a minute
        # after the last device left) or a number of idle minutes ("15", "30", "60").
        "autoOff": "never",
        # Turn Sunshine off when the battery falls to this level (percent) while not
        # charging: "off", "10", "20" or "30".
        "batteryGuard": "off",
        # Answer mDNS for Sunshine when it cannot publish through avahi-daemon.
        "announce": True,
    },
    "behavior": {
        "autoWake": True,
        "wakeTimeoutS": 90,
        "showOffline": True,
        "pollIntervalS": 8,
        # Ask before End stream closes the game on the host.
        "confirmEndStream": True,
    },
    "updates": {
        # Check GitHub for a new plugin release at most once a day.
        "autoCheck": True,
        "lastCheck": 0,
        # Release the user chose to skip; no reminder until a newer one appears.
        "skippedVersion": "",
    },
    "shortcuts": {
        # Steam app id of the hidden-ish "Moonlight (Eclipse)" launcher shortcut.
        "launcherAppId": 0,
        # Library entries created with "Add to library", keyed by a short id.
        "library": {},
    },
}

SECRET_KEYS = {"password", "sunshinePassword"}


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into a copy of ``base``.

    A ``None`` value in ``patch`` deletes the key, which is how the frontend
    removes per-host entries.
    """
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def mask_secrets(value: Any) -> Any:
    """Replace secret values with a boolean ``<key>Set`` flag for the frontend."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in SECRET_KEYS:
                out[f"{key}Set"] = bool(item)
            else:
                out[key] = mask_secrets(item)
        return out
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def strip_masked_fields(patch: Any) -> Any:
    """Drop ``<secret>Set`` echo fields and empty secrets coming from the frontend.

    An empty secret means "unchanged"; clearing a secret requires ``None``.
    """
    if not isinstance(patch, dict):
        return patch
    out: dict[str, Any] = {}
    for key, item in patch.items():
        if key.endswith("Set") and key[:-3] in SECRET_KEYS:
            continue
        if key in SECRET_KEYS and item == "":
            continue
        out[key] = strip_masked_fields(item)
    return out


def migrate(data: dict[str, Any], stored: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade settings saved by older versions. Returns ``(data, changed)``.

    A missing file is a fresh install and needs nothing: defaults are current.
    """
    if not stored:
        return data, False
    version = int(stored.get("version") or 1)
    if version >= SCHEMA_VERSION:
        return data, False
    if version < 2:
        # v2: ending a stream also ends the session on the host by default.
        data["stream"]["quitAppAfter"] = True
    data["version"] = SCHEMA_VERSION
    return data, True


class SettingsStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if not isinstance(stored, dict):
                raise ValueError("settings root is not an object")
        except FileNotFoundError:
            stored = {}
        except (OSError, ValueError) as exc:
            get_logger().error("Failed to read settings, using defaults: %s", exc)
            stored = {}
        data, migrated = migrate(deep_merge(DEFAULTS, stored), stored)
        if migrated and stored:
            self._data = data
            try:
                self._write()
            except OSError as exc:
                get_logger().error("Failed to save migrated settings: %s", exc)
        return data

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    @property
    def data(self) -> dict[str, Any]:
        """Read-only snapshot. Mutate through :meth:`update`."""
        return self._data

    def public(self) -> dict[str, Any]:
        return mask_secrets(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data = deep_merge(self._data, strip_masked_fields(patch))
            self._write()
        return self._data

    def replace(self, data: dict[str, Any]) -> dict[str, Any]:
        """Replace every setting (restoring a backup); missing ones get their defaults."""
        with self._lock:
            self._data = deep_merge(DEFAULTS, {**strip_masked_fields(data), "version": SCHEMA_VERSION})
            self._write()
        return self._data

    def _write(self) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            # Settings may hold Sunshine credentials, keep them private.
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def load_settings_readonly(path: str) -> dict[str, Any]:
    """Load settings without creating a store (used by the launcher)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            return migrate(deep_merge(DEFAULTS, stored), stored)[0]
    except (OSError, ValueError):
        pass
    return copy.deepcopy(DEFAULTS)
