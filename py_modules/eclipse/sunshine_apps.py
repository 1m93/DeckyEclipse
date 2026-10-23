"""Steam games as apps of the Sunshine on this device.

Moonlight lists the apps of Sunshine's ``apps.json``. Adding a Steam game
there lets another device start that game directly: the app runs
``steam steam://rungameid/<id>``, which the Steam client already running in
Gaming Mode picks up. Games added by Eclipse are recognized by that command,
so no marker is stored and Sunshine's web UI can still edit them.

Box art must be a PNG for Sunshine. Steam caches covers as JPEG, so the
frontend converts them (``cover_source`` hands it the file) and the PNG is
stored next to Sunshine's config.

Standard library only.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import tempfile
import threading
from typing import Any, Optional

APPS_FILE = "apps.json"
COVERS_DIR = "eclipse-covers"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_COVER_BYTES = 8 * 1024 * 1024
# Sunshine's own default list, used when the config has none yet.
DEFAULT_APPS: dict[str, Any] = {
    "env": {"PATH": "$(PATH):$(HOME)/.local/bin"},
    "apps": [
        {"name": "Desktop", "image-path": "desktop.png"},
        {
            "name": "Steam Big Picture",
            "detached": ["setsid steam steam://open/bigpicture"],
            "prep-cmd": [{"do": "", "undo": "setsid steam steam://close/bigpicture"}],
            "image-path": "steam.png",
        },
    ],
}

_RUN_RE = re.compile(r"steam://rungameid/(\d+)")
# Steam game ids fit in 32 bits; larger rungameid values are non-Steam shortcuts.
_MAX_STEAM_ID = 2**32 - 1
# Every change reads, edits and rewrites apps.json: one at a time.
_APPS_LOCK = threading.Lock()
# Installed "apps" that are not games.
_TOOL_IDS = {228980, 1070560, 1391110, 1628350, 1493710, 1826330, 2180100, 2805730}
_TOOL_NAME_RE = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common|SteamVR)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


# ---------------------------------------------------------------- Steam library


def parse_vdf(text: str) -> dict[str, Any]:
    """Text VDF (Valve KeyValues) as nested dicts; keys keep their case."""
    root: dict[str, Any] = {}
    stack = [root]
    key: Optional[str] = None
    for match in _TOKEN_RE.finditer(text):
        string, brace = match.groups()
        if brace == "{":
            child: dict[str, Any] = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = string.replace('\\"', '"').replace("\\\\", "\\")
        else:
            stack[-1][key] = string.replace('\\"', '"').replace("\\\\", "\\")
            key = None
    return root


def steam_root(home: str) -> Optional[str]:
    for path in (os.path.join(home, ".local", "share", "Steam"), os.path.join(home, ".steam", "steam")):
        if os.path.isdir(os.path.join(path, "steamapps")):
            return os.path.realpath(path)
    return None


def library_folders(root: str) -> list[str]:
    folders = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), "r", encoding="utf-8", errors="replace") as fh:
            data = parse_vdf(fh.read())
    except OSError:
        return folders
    entries = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    for value in entries.values() if isinstance(entries, dict) else []:
        path = value.get("path") if isinstance(value, dict) else value if isinstance(value, str) else None
        if path and os.path.isdir(path) and os.path.realpath(path) not in folders:
            folders.append(os.path.realpath(path))
    return folders


def is_game(app_id: int, name: str) -> bool:
    return app_id not in _TOOL_IDS and not _TOOL_NAME_RE.match(name)


def installed_games(home: str) -> list[dict[str, Any]]:
    """Installed Steam games, sorted by name: ``{"appId": int, "name": str}``."""
    root = steam_root(home)
    if not root:
        return []
    games: dict[int, str] = {}
    for folder in library_folders(root):
        for manifest in glob.glob(os.path.join(folder, "steamapps", "appmanifest_*.acf")):
            try:
                with open(manifest, "r", encoding="utf-8", errors="replace") as fh:
                    state = parse_vdf(fh.read()).get("AppState") or {}
                app_id = int(state.get("appid", "0"))
                # StateFlags 4: fully installed.
                installed = int(state.get("StateFlags", "0") or 0) & 4
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            name = str(state.get("name") or "").strip()
            if app_id and name and installed and is_game(app_id, name):
                games[app_id] = name
    return [{"appId": app_id, "name": name} for app_id, name in sorted(games.items(), key=lambda i: i[1].lower())]


def cover_source(home: str, app_id: int) -> Optional[str]:
    """Best cover image of a game: the user's custom grid art, else Steam's cached portrait capsule."""
    root = steam_root(home)
    if not root:
        return None
    candidates: list[str] = []
    for ext in ("png", "jpg", "jpeg"):
        candidates += glob.glob(os.path.join(root, "userdata", "*", "config", "grid", f"{app_id}p.{ext}"))
    cache = os.path.join(root, "appcache", "librarycache")
    candidates += glob.glob(os.path.join(cache, str(app_id), "**", "library_600x900*.jpg"), recursive=True)
    candidates += glob.glob(os.path.join(cache, f"{app_id}_library_600x900*.jpg"))
    candidates += glob.glob(os.path.join(cache, str(app_id), "**", "library_capsule*.jpg"), recursive=True)
    for path in candidates:
        try:
            if 0 < os.path.getsize(path) <= MAX_COVER_BYTES:
                return path
        except OSError:
            continue
    return None


def read_cover(path: str) -> dict[str, str]:
    """A cover file for the frontend: its MIME type and base64 data."""
    with open(path, "rb") as fh:
        data = fh.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
        raise ValueError("Cover image is too large")
    mime = "image/png" if data.startswith(PNG_SIGNATURE) else "image/jpeg"
    return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}


# ---------------------------------------------------------------- apps.json


def apps_path(config_dir: str) -> str:
    return os.path.join(config_dir, APPS_FILE)


def read_apps(config_dir: str) -> dict[str, Any]:
    """Sunshine's apps.json, or its default list when there is none (Sunshine creates the same)."""
    try:
        # utf-8-sig: the file may have been edited on Windows, with a BOM.
        with open(apps_path(config_dir), "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT_APPS))
    except ValueError as exc:
        raise ValueError(f"Sunshine's app list is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("apps", []), list):
        raise ValueError("Sunshine's app list has an unexpected format")
    data.setdefault("apps", [])
    return data


def write_apps(config_dir: str, data: dict[str, Any]) -> None:
    path = apps_path(config_dir)
    os.makedirs(config_dir, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".apps-", suffix=".eclipse-tmp", dir=config_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4)
        os.chmod(temp, (os.stat(path).st_mode & 0o777) if os.path.exists(path) else 0o644)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def steam_app_id(app: dict[str, Any]) -> Optional[int]:
    """The Steam game an app starts, when it is one added this way."""
    commands = list(app.get("detached") or []) + [app.get("cmd") or ""]
    for command in commands:
        match = _RUN_RE.search(str(command))
        if match and 0 < int(match.group(1)) <= _MAX_STEAM_ID:
            return int(match.group(1))
    return None


def list_apps(config_dir: str) -> list[dict[str, Any]]:
    """The apps Moonlight sees, in order: ``{"index", "name", "steamAppId", "hasCover"}``."""
    out = []
    for index, app in enumerate(read_apps(config_dir)["apps"]):
        if not isinstance(app, dict):
            continue
        image = str(app.get("image-path") or "")
        out.append({
            "index": index,
            "name": str(app.get("name") or ""),
            "steamAppId": steam_app_id(app),
            "hasCover": bool(image) and os.path.isabs(image) and os.path.isfile(image),
        })
    return out


def cover_path(config_dir: str, app_id: int) -> str:
    return os.path.join(config_dir, COVERS_DIR, f"{app_id}.png")


def save_cover(config_dir: str, app_id: int, png_base64: str) -> str:
    data = base64.b64decode(png_base64, validate=True)
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("The cover is not a PNG image")
    if len(data) > MAX_COVER_BYTES:
        raise ValueError("The cover is too large")
    path = cover_path(config_dir, app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


QUIT_SCRIPT = "eclipse-quit-game.sh"
# Ends a running Steam game: Steam gives every process of a game ``SteamAppId``.
_QUIT_SCRIPT_TEXT = """#!/bin/sh
# Written by Eclipse: ends the Steam game $1 when its Sunshine app is closed.
case "$1" in ''|*[!0-9]*) exit 0 ;; esac
for p in /proc/[0-9]*; do
  if tr '\\000' '\\n' < "$p/environ" 2>/dev/null | grep -qx "SteamAppId=$1"; then
    kill "${p#/proc/}" 2>/dev/null
  fi
done
exit 0
"""


def ensure_quit_script(config_dir: str) -> str:
    path = os.path.join(config_dir, QUIT_SCRIPT)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.read() == _QUIT_SCRIPT_TEXT:
                return path
    except OSError:
        pass
    os.makedirs(config_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_QUIT_SCRIPT_TEXT)
    os.chmod(path, 0o755)
    return path


def _arg(text: str) -> str:
    # Sunshine splits commands on spaces and understands double quotes.
    return f'"{text}"' if " " in text else text


def game_app(app_id: int, name: str, cover: Optional[str], via_flatpak: bool, quit_script: Optional[str] = None) -> dict[str, Any]:
    # The Flatpak sandbox has no Steam: the commands run on the host.
    host = "flatpak-spawn --host " if via_flatpak else ""
    command = f"steam steam://rungameid/{app_id}"
    command = f"{host}{command}" if via_flatpak else f"setsid {command}"
    app: dict[str, Any] = {
        "name": name,
        "detached": [command],
        "auto-detach": "true",
    }
    if quit_script:
        # Closing the app (Moonlight's Quit, or Close app here) also ends the game on this device.
        app["prep-cmd"] = [{"do": "", "undo": f"{host}sh {_arg(quit_script)} {app_id}"}]
    if cover:
        app["image-path"] = cover
    return app


def add_games(config_dir: str, games: list[dict[str, Any]], via_flatpak: bool) -> int:
    """Add Steam games (``{"appId", "name"}``) that are not in the list yet. Returns how many were added."""
    with _APPS_LOCK:
        return _add_games(config_dir, games, via_flatpak)


def _add_games(config_dir: str, games: list[dict[str, Any]], via_flatpak: bool) -> int:
    data = read_apps(config_dir)
    quit_script = ensure_quit_script(config_dir) if games else None
    present = {steam_app_id(app) for app in data["apps"] if isinstance(app, dict)}
    added = 0
    for game in games:
        app_id = int(game["appId"])
        name = str(game.get("name") or "").strip()
        if app_id <= 0 or not name or app_id in present:
            continue
        cover = cover_path(config_dir, app_id)
        data["apps"].append(
            game_app(app_id, name[:100], cover if os.path.isfile(cover) else None, via_flatpak, quit_script)
        )
        present.add(app_id)
        added += 1
    if added:
        write_apps(config_dir, data)
    return added


def remove_games(config_dir: str, app_ids: list[int]) -> int:
    """Remove the apps of these Steam games (and their covers). Returns how many were removed."""
    with _APPS_LOCK:
        return _remove_games(config_dir, app_ids)


def _remove_games(config_dir: str, app_ids: list[int]) -> int:
    data = read_apps(config_dir)
    drop = {int(i) for i in app_ids}
    kept = [app for app in data["apps"] if not (isinstance(app, dict) and steam_app_id(app) in drop)]
    removed = len(data["apps"]) - len(kept)
    if removed:
        data["apps"] = kept
        write_apps(config_dir, data)
        for app_id in drop:
            try:
                os.unlink(cover_path(config_dir, app_id))
            except OSError:
                pass
    return removed
