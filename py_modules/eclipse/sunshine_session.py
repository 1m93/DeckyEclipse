"""Who is streaming from the Sunshine server on this device.

Sunshine has no API for its streaming sessions, so the state is pieced
together from two sources:

* ``/serverinfo`` (unauthenticated HTTP) names the app that is open
  (``currentgame``). An app stays open after a client disconnects without
  quitting it, so an open app alone does not mean anyone is watching.
* Sunshine's log records every client that connects to or leaves the control
  stream ("CLIENT CONNECTED", "CLIENT DISCONNECTED", "Ping Timeout") and the
  end of all sessions ("Process terminated"). Sunshine rewrites its log on
  every start, and a startup line resets the count as well.

Sunshine does not log which device connected, so sessions are counted, not
named.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

from . import sunshine_native as native

# "[2026-10-22 10:00:00.123]: Info: CLIENT CONNECTED"
_LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?\]:\s*\w+:\s*(.*)$")
_STARTUP_RE = re.compile(r"^Sunshine version:", re.IGNORECASE)
# Only the end of the log matters; older lines belong to finished sessions.
MAX_LOG_BYTES = 2 * 1024 * 1024
_HEAD_BYTES = 64
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Where Sunshine's own assets (default app images, default apps.json) live.
_ASSET_DIRS = (
    native.ROOT,
    *native.LEGACY_ROOTS,
    "/usr/share/sunshine",
    "/usr/local/share/sunshine",
    "/var/lib/flatpak/app/dev.lizardbyte.app.Sunshine/current/active/files/share/sunshine",
)


@dataclass
class LogState:
    """Clients connected according to the log, and since when (epoch seconds)."""

    clients: int = 0
    since: Optional[float] = None
    # Each client's connect time, oldest first; the session started with the first.
    connected_at: list[float] = field(default_factory=list)


def _timestamp(text: str) -> Optional[float]:
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def apply_log(state: LogState, text: str) -> LogState:
    """Feed log lines into ``state`` (in place) and return it."""
    for raw in text.splitlines():
        match = _LINE_RE.match(raw.strip())
        if not match:
            continue
        stamp, message = match.groups()
        message = message.strip()
        if _STARTUP_RE.match(message) or message == "Process terminated":
            state.connected_at.clear()
        elif message == "CLIENT CONNECTED":
            state.connected_at.append(_timestamp(stamp) or time.time())
        elif message == "CLIENT DISCONNECTED" or message.endswith(": Ping Timeout"):
            if state.connected_at:
                state.connected_at.pop(0)
        else:
            continue
        state.clients = len(state.connected_at)
        state.since = state.connected_at[0] if state.connected_at else None
    return state


class LogFollower:
    """Reads only what was added to the log since the last call."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._path: Optional[str] = None
        self._inode: Optional[int] = None
        self._offset = 0
        self._partial = ""
        # Start of the file: it holds the startup time, so it changes when Sunshine rewrites the log.
        self._head = b""
        self.state = LogState()

    def read(self, path: Optional[str]) -> LogState:
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        if not path or st is None:
            self._reset()
            return self.state
        try:
            with open(path, "rb") as fh:
                head = fh.read(_HEAD_BYTES)
                # A new file, or Sunshine started over and rewrote it.
                if (
                    path != self._path
                    or st.st_ino != self._inode
                    or st.st_size < self._offset
                    or head[: len(self._head)] != self._head
                ):
                    self._reset()
                    self._path, self._inode = path, st.st_ino
                    self._offset = max(0, st.st_size - MAX_LOG_BYTES)
                if len(self._head) < _HEAD_BYTES:
                    self._head = head
                if st.st_size == self._offset:
                    return self.state
                fh.seek(self._offset)
                data = fh.read(MAX_LOG_BYTES)
        except OSError:
            return self.state
        self._offset += len(data)
        text = self._partial + data.decode("utf-8", errors="replace")
        # Keep an unfinished last line for the next read.
        text, _, self._partial = text.rpartition("\n")
        apply_log(self.state, text)
        return self.state


def _app_ids(name: str, image_hash: Optional[str], index: int) -> tuple[str, str]:
    """Sunshine's app ids: CRC32 of name (+ image hash), and the same with the list index."""
    base = name + (image_hash or "")

    def crc(text: str) -> str:
        value = zlib.crc32(text.encode())
        # Truncated to a signed 32-bit int, then made positive (abs()).
        signed = value - (1 << 32) if value >= 1 << 31 else value
        return str(abs(signed))

    return crc(base), crc(base + str(index))


def _image_hashes(image: str) -> list[Optional[str]]:
    """Possible image hashes Sunshine mixed into the id: none (default image) or the PNG's sha256."""
    hashes: list[Optional[str]] = [None]
    if not image or not image.lower().endswith(".png"):
        return hashes
    candidates = [os.path.join(base, image.lstrip("./")) for base in _ASSET_DIRS]
    candidates += [os.path.join(base, "assets", os.path.basename(image)) for base in _ASSET_DIRS]
    if os.path.isabs(image):
        candidates.insert(0, image)
    for path in candidates:
        try:
            with open(path, "rb") as fh:
                data = fh.read(16 * 1024 * 1024)
        except OSError:
            continue
        if data.startswith(_PNG_SIGNATURE):
            hashes.append(hashlib.sha256(data).hexdigest())
            # Used by Sunshine instead when it cannot hash the file.
            hashes.append(path)
    return hashes


def _load_apps(config_dir: Optional[str]) -> list[dict[str, Any]]:
    paths = [os.path.join(config_dir, "apps.json")] if config_dir else []
    paths += [os.path.join(base, "apps.json") for base in _ASSET_DIRS]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                apps = json.load(fh).get("apps")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(apps, list):
            return [app for app in apps if isinstance(app, dict)]
    return []


def app_name(app_id: Optional[str], config_dir: Optional[str]) -> Optional[str]:
    """Name of the open app from its ``currentgame`` id, or None when it cannot be matched."""
    if not app_id:
        return None
    for index, app in enumerate(_load_apps(config_dir)):
        name = str(app.get("name") or "")
        if not name:
            continue
        for image_hash in _image_hashes(str(app.get("image-path") or "")):
            if app_id in _app_ids(name, image_hash, index):
                return name
    return None


class SessionTracker:
    """Session state of the Sunshine on this device, read incrementally."""

    def __init__(self) -> None:
        self._log = LogFollower()
        self._names: dict[tuple[str, Optional[str]], Optional[str]] = {}

    def _app_name(self, app_id: str, config_dir: Optional[str]) -> Optional[str]:
        # Hashing app images on every poll would be wasteful; ids only change with apps.json.
        key = (app_id, config_dir)
        if key not in self._names:
            if len(self._names) > 32:
                self._names.clear()
            self._names[key] = app_name(app_id, config_dir)
        return self._names[key]

    def update(self, server: Optional[dict[str, Any]], log_path: Optional[str]) -> dict[str, Any]:
        """``server``: parsed ``/serverinfo`` of the local Sunshine, or None when it does not answer."""
        log_state = self._log.read(log_path)
        config_dir = os.path.dirname(log_path) if log_path else None
        app_id = server.get("currentGame") if server else None
        if server and not app_id and log_state.connected_at:
            # Nothing can stream without an open app. Drop clients whose leaving
            # was not logged, or the next session would count them too.
            log_state.connected_at.clear()
            log_state.clients, log_state.since = 0, None
        name = self._app_name(app_id, config_dir) if app_id else None
        return summarize(server, log_state, name)


def summarize(server: Optional[dict[str, Any]], log_state: LogState, name: Optional[str]) -> dict[str, Any]:
    """The session state sent to the frontend.

    ``reachable``: Sunshine answers. ``appOpen``: an app is open (a stream can
    be resumed). ``clients``: devices streaming right now. ``since``: when the
    current session started (epoch ms), or null.
    """
    if not server:
        return {"reachable": False, "appOpen": False, "appName": None, "clients": 0, "since": None}
    app_id = server.get("currentGame")
    # Without an open app nothing can be streaming, whatever the log says.
    clients = log_state.clients if app_id else 0
    return {
        "reachable": True,
        "appOpen": bool(app_id),
        "appName": name if app_id else None,
        "clients": clients,
        "since": int(log_state.since * 1000) if clients and log_state.since else None,
    }


# "When the stream ends" waits this long, so a device can reconnect.
AUTO_OFF_AFTER_STREAM_S = 60.0
# Longer while the app is still open: the device may have dropped and want to resume it.
AUTO_OFF_AFTER_STREAM_APP_OPEN_S = 5 * 60.0


class AutoOff:
    """When to turn Sunshine off because nobody streams (the ``autoOff`` option)."""

    def __init__(self) -> None:
        self.option: Optional[str] = None
        self.reset()

    def reset(self) -> None:
        # Since when nobody streams (monotonic), and whether anyone did since Sunshine started.
        self.idle_since: Optional[float] = None
        self.had_stream = False

    def update(self, state: dict[str, Any], option: str, now: float) -> Optional[str]:
        """Feed the current session state; returns why to turn Sunshine off now, or None."""
        if option != self.option:
            # A new choice counts from now, not from when Sunshine started.
            self.option = option
            self.idle_since = None
        if not state["reachable"]:
            self.reset()
            return None
        if state["clients"]:
            self.idle_since, self.had_stream = None, True
            return None
        if self.idle_since is None:
            self.idle_since = now
        idle = now - self.idle_since
        if option == "afterStream":
            wait = AUTO_OFF_AFTER_STREAM_APP_OPEN_S if state.get("appOpen") else AUTO_OFF_AFTER_STREAM_S
            return "the stream ended" if self.had_stream and idle >= wait else None
        if option.isdigit() and int(option) > 0:
            minutes = int(option)
            return f"nobody streamed for {minutes} minutes" if idle >= minutes * 60 else None
        return None
