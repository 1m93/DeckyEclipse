"""Back up and restore Eclipse's settings.

A backup is one JSON file in an ``Eclipse`` folder: ``~/Documents/Eclipse``
on this device (survives removing the plugin), or on a microSD card or USB
drive (survives reinstalling SteamOS, which erases this device's storage).
It holds:

* the plugin settings: profiles, stream settings, per-host settings,
  Sunshine options of the plugin. Saved passwords only when asked for.
  Steam shortcuts are left out: their ids belong to this Steam install;
* the Sunshine options that differ from Sunshine's defaults;
* the Steam games added to Sunshine.

Pairing (Moonlight's and Sunshine's certificates) is not part of it.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from typing import Any, Optional

from .settings import SECRET_KEYS

FORMAT = "eclipse-backup"
VERSION = 1
DIRNAME = os.path.join("Documents", "Eclipse")
FOLDER = "Eclipse"
MEDIA_ROOT = "/run/media"
HOME_LOCATION = "home"
MAX_BYTES = 2 * 1024 * 1024
# Any JSON file in the folder is listed (a copy may have been renamed); no path parts.
_NAME_RE = re.compile(r"[^/\\\x00]+\.json")
# The user name each saved password belongs to.
_USER_OF = {"password": "user", "sunshinePassword": "sunshineUser"}
# Settings that only make sense on the device they were made on.
_LOCAL_KEYS = ("shortcuts", "updates")


def backup_dir(home: str) -> str:
    return os.path.join(home, DIRNAME)


def _media_mounts(root: str) -> list[str]:
    """Mounted microSD cards and USB drives (SteamOS mounts them under /run/media[/<user>])."""
    found: list[str] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return found
    for name in entries:
        path = os.path.join(root, name)
        if os.path.ismount(path):
            found.append(path)
        elif os.path.isdir(path):
            try:
                found += [os.path.join(path, n) for n in sorted(os.listdir(path)) if os.path.ismount(os.path.join(path, n))]
            except OSError:
                continue
    return found


def locations(home: str, media_root: str = MEDIA_ROOT) -> list[dict[str, Any]]:
    """Where backups can go: this device, then each writable removable drive."""
    out = [{"id": HOME_LOCATION, "label": "This device", "path": backup_dir(home), "removable": False}]
    for mount in _media_mounts(media_root):
        if not os.access(mount, os.W_OK):
            continue
        name = os.path.basename(mount)
        label = "microSD card" if name.startswith("mmcblk") else f"Drive {name}"
        out.append({"id": mount, "label": label, "path": os.path.join(mount, FOLDER), "removable": True})
    return out


def _location(home: str, location_id: str, media_root: str = MEDIA_ROOT) -> dict[str, Any]:
    for location in locations(home, media_root):
        if location["id"] == location_id:
            return location
    raise ValueError("That drive is not available any more")


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items() if k not in SECRET_KEYS}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def _has_secrets(value: Any) -> bool:
    if isinstance(value, dict):
        return any((k in SECRET_KEYS and bool(v)) or _has_secrets(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_secrets(v) for v in value)
    return False


def build(settings: dict[str, Any], options: Optional[dict[str, Any]], games: list[dict[str, Any]],
          include_secrets: bool, plugin_version: Optional[str], now: Optional[float] = None) -> dict[str, Any]:
    data = {k: copy.deepcopy(v) for k, v in settings.items() if k not in _LOCAL_KEYS}
    if not include_secrets:
        data = _strip_secrets(data)
    return {
        "format": FORMAT,
        "version": VERSION,
        "createdAt": int((now if now is not None else time.time()) * 1000),
        "pluginVersion": plugin_version,
        "settings": data,
        # None: Sunshine's options were not read (not installed), so a restore leaves them alone.
        "sunshineOptions": dict(options) if options is not None else None,
        "sunshineGames": [{"appId": int(g["appId"]), "name": str(g["name"])} for g in games],
    }


def write(home: str, backup: dict[str, Any], location_id: str = HOME_LOCATION, media_root: str = MEDIA_ROOT) -> str:
    directory = _location(home, location_id, media_root)["path"]
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(backup["createdAt"] / 1000))
    path = os.path.join(directory, f"eclipse-backup-{stamp}.json")
    suffix = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"eclipse-backup-{stamp}-{suffix}.json")
        suffix += 1
    temp = path + ".tmp"
    # May hold passwords: private to the user from the first byte.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(backup, fh, indent=2, sort_keys=True)
    os.replace(temp, path)
    return path


def validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("This is not an Eclipse backup")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("The backup is damaged")
    if version > VERSION:
        raise ValueError("This backup was made by a newer Eclipse: update the plugin first")
    if not isinstance(data.get("settings"), dict):
        raise ValueError("The backup has no settings")
    options = data.get("sunshineOptions")
    if (options is not None and not isinstance(options, dict)) or not isinstance(data.get("sunshineGames", []), list):
        raise ValueError("The backup is damaged")
    if not isinstance(data.get("createdAt", 0), (int, float)):
        data["createdAt"] = 0
    return data


def safe_path(home: str, location_id: str, name: str, media_root: str = MEDIA_ROOT) -> str:
    """Path of backup ``name`` in a backup folder; refuses anything else."""
    if not _NAME_RE.fullmatch(name) or name.startswith("."):
        raise ValueError("Unknown backup file")
    return os.path.join(_location(home, location_id, media_root)["path"], name)


def read(home: str, location_id: str, name: str, media_root: str = MEDIA_ROOT) -> dict[str, Any]:
    path = safe_path(home, location_id, name, media_root)
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("The backup file is too large")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return validate(json.load(fh))
        except UnicodeDecodeError as exc:
            raise ValueError("The backup file is damaged") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("The backup file is damaged") from exc


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (dict, list)) else 0


def list_backups(home: str, media_root: str = MEDIA_ROOT) -> list[dict[str, Any]]:
    """Backups in every location, newest first. A file that cannot be used is listed with its ``error``."""
    out = []
    for location in locations(home, media_root):
        try:
            names = [n for n in os.listdir(location["path"]) if _NAME_RE.fullmatch(n) and not n.startswith(".")]
        except OSError:
            continue
        for name in names:
            item: dict[str, Any] = {
                "name": name, "locationId": location["id"], "location": location["label"],
                "createdAt": 0, "hasPasswords": False, "profiles": 0, "hosts": 0, "games": 0, "error": None,
            }
            try:
                data = read(home, location["id"], name, media_root)
                settings = data["settings"]
                item.update({
                    "createdAt": int(data.get("createdAt") or 0),
                    "hasPasswords": _has_secrets(settings),
                    "profiles": _count(settings.get("profiles")),
                    "hosts": _count(settings.get("hosts")),
                    "games": _count(data.get("sunshineGames")),
                })
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                item["error"] = str(exc) or "The backup file is damaged"
            out.append(item)
    return sorted(out, key=lambda b: (b["error"] is not None, -b["createdAt"], b["name"]))


def _same_type(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def sanitize(restored: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    """``restored`` without values whose type does not match the defaults (a damaged or edited backup)."""
    if not isinstance(restored, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in restored.items():
        if key not in defaults:
            continue
        default = defaults[key]
        if isinstance(default, dict) and default:
            if isinstance(value, dict):
                out[key] = sanitize(value, default)
        elif _same_type(value, default):
            out[key] = value
    return out


def merge_settings(current: dict[str, Any], restored: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Settings after restoring: the backup's values, keeping this device's local
    settings and the saved passwords the backup does not carry."""
    out = {k: copy.deepcopy(v) for k, v in sanitize(restored, defaults).items() if k not in _LOCAL_KEYS and k != "version"}
    for key in _LOCAL_KEYS:
        if key in current:
            out[key] = copy.deepcopy(current[key])

    def keep_secrets(target: Any, source: Any) -> None:
        if not isinstance(target, dict) or not isinstance(source, dict):
            return
        for key, value in source.items():
            if key in SECRET_KEYS and value and not target.get(key):
                # A password belongs to its user name: keep them together.
                user_key = _USER_OF.get(key)
                if user_key and source.get(user_key):
                    target[user_key] = source[user_key]
                target[key] = value
            elif isinstance(value, dict) and isinstance(target.get(key), dict):
                keep_secrets(target[key], value)

    keep_secrets(out, current)
    # Entries keyed by id must be objects; a damaged one would break every lookup.
    for key in ("hosts", "profiles"):
        if isinstance(out.get(key), dict):
            out[key] = {k: v for k, v in out[key].items() if isinstance(v, dict)}
    profiles = out.get("profiles") or {}
    for pid in [k for k, v in profiles.items() if not isinstance(v.get("name"), str) or not isinstance(v.get("stream"), dict)]:
        del profiles[pid]
    return out


def delete(home: str, location_id: str, name: str, media_root: str = MEDIA_ROOT) -> None:
    os.unlink(safe_path(home, location_id, name, media_root))
