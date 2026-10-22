"""Read paired hosts and their app lists from Moonlight's QSettings INI file.

Moonlight (moonlight-qt) persists its state with ``QSettings`` in INI format::

    [hosts]
    1\\hostname=GAMING-PC
    1\\uuid=...
    1\\localaddress=192.168.1.10
    1\\mac=@ByteArray(...)
    1\\apps\\1\\name=Desktop
    1\\apps\\1\\id=881448767
    1\\apps\\size=1
    size=1

We only read this file; Moonlight remains the owner of pairing state.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Optional

from . import DEFAULT_HTTP_PORT, FLATPAK_MOONLIGHT
from .log import get_logger

_ORG_DIR = "Moonlight Game Streaming Project"
_CONF_NAME = "Moonlight.conf"

_SIMPLE_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "0": "\0", "\\": "\\", "\"": "\"", "'": "'", "?": "?",
}


def config_candidates(home: str) -> list[str]:
    return [
        os.path.join(home, ".var", "app", FLATPAK_MOONLIGHT, "config", _ORG_DIR, _CONF_NAME),
        os.path.join(home, ".config", _ORG_DIR, _CONF_NAME),
    ]


def find_config(home: str) -> Optional[str]:
    for path in config_candidates(home):
        if os.path.isfile(path):
            return path
    return None


def _unescape(raw: str) -> str:
    """Decode a QSettings INI value (quotes, C escapes and ``\\x`` hex runs)."""
    out: list[str] = []
    i = 0
    in_quotes = False
    length = len(raw)
    while i < length:
        ch = raw[i]
        if ch == "\"":
            in_quotes = not in_quotes
            i += 1
        elif ch == "\\" and i + 1 < length:
            nxt = raw[i + 1]
            if nxt == "x":
                # QSettings reads hex digits greedily after \x.
                j = i + 2
                while j < length and raw[j] in "0123456789abcdefABCDEF":
                    j += 1
                digits = raw[i + 2:j]
                out.append(chr(int(digits, 16)) if digits else "x")
                i = j
            elif nxt in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[nxt])
                i += 2
            else:
                out.append(nxt)
                i += 2
        elif ch == "," and not in_quotes:
            # Unquoted commas separate QStringList items; keep a readable join.
            out.append(", ")
            i += 1
            while i < length and raw[i] == " ":
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def decode_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("@ByteArray(") and raw.endswith(")"):
        return _unescape(raw[len("@ByteArray("):-1]).encode("latin-1", errors="replace")
    if raw.startswith("@"):
        # @Variant / @Rect etc. are not needed by this plugin.
        return None
    return _unescape(raw)


def parse_ini(text: str) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    current = sections.setdefault("General", {})
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        current[key.strip().replace("\\", "/")] = decode_value(value)
    return sections


def _format_mac(value: Any) -> Optional[str]:
    if isinstance(value, bytes) and len(value) == 6 and any(value):
        return ":".join(f"{b:02x}" for b in value)
    if isinstance(value, str) and len(value.replace(":", "").replace("-", "")) == 12:
        return value.lower().replace("-", ":")
    return None


def _to_port(value: Any) -> int:
    try:
        port = int(value)
        return port if 0 < port < 65536 else DEFAULT_HTTP_PORT
    except (TypeError, ValueError):
        return DEFAULT_HTTP_PORT


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1")


def hosts_from_sections(sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    table = sections.get("hosts") or {}
    try:
        count = int(table.get("size") or 0)
    except (TypeError, ValueError):
        count = 0

    hosts: list[dict[str, Any]] = []
    for idx in range(1, count + 1):
        prefix = f"{idx}/"

        def get(key: str, default: Any = None) -> Any:
            return table.get(prefix + key, default)

        uuid = get("uuid")
        name = get("hostname") or get("customname")
        if not uuid or not name:
            continue

        addresses: list[dict[str, Any]] = []
        for kind in ("manual", "local", "remote", "ipv6"):
            address = get(f"{kind}address")
            if isinstance(address, str) and address:
                addresses.append({
                    "kind": kind,
                    "address": address,
                    "port": _to_port(get(f"{kind}port")),
                })

        apps: list[dict[str, Any]] = []
        try:
            app_count = int(get("apps/size") or 0)
        except (TypeError, ValueError):
            app_count = 0
        for app_idx in range(1, app_count + 1):
            app_prefix = f"apps/{app_idx}/"
            app_name = get(app_prefix + "name")
            if not app_name:
                continue
            apps.append({
                "id": str(get(app_prefix + "id") or ""),
                "name": app_name,
                "hdr": _as_bool(get(app_prefix + "hdr")),
                "hidden": _as_bool(get(app_prefix + "hidden")),
            })

        server_cert = get("srvcert")
        hosts.append({
            "uuid": uuid,
            "name": name,
            "addresses": addresses,
            "mac": _format_mac(get("mac")),
            "apps": apps,
            # Moonlight stores the host certificate only once pairing succeeded.
            "paired": bool(server_cert) and len(server_cert) > 32,
        })
    return hosts


class MoonlightConfig:
    """mtime-cached reader so frequent UI refreshes stay cheap."""

    def __init__(self, home: str) -> None:
        self.home = home
        self._path: Optional[str] = None
        self._mtime = -1.0
        self._hosts: list[dict[str, Any]] = []

    @property
    def path(self) -> Optional[str]:
        return self._path

    def hosts(self) -> list[dict[str, Any]]:
        path = find_config(self.home)
        if not path:
            self._path, self._mtime, self._hosts = None, -1.0, []
            return []
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return self._hosts
        if path == self._path and mtime == self._mtime:
            return self._hosts
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                self._hosts = hosts_from_sections(parse_ini(fh.read()))
            self._path, self._mtime = path, mtime
        except OSError as exc:
            get_logger().error("Failed to read Moonlight config %s: %s", path, exc)
        return self._hosts


def find_boxart(home: str, host_uuid: str, app_id: str) -> Optional[str]:
    """Locate Moonlight's cached box art for an app, if it was ever displayed."""
    if not host_uuid or not app_id or "/" in host_uuid or "/" in app_id:
        return None
    roots = [
        os.path.join(home, ".var", "app", FLATPAK_MOONLIGHT, "cache", _ORG_DIR),
        os.path.join(home, ".cache", _ORG_DIR),
    ]
    for root in roots:
        escaped = glob.escape(root)
        for pattern in (os.path.join(escaped, "boxart"), os.path.join(escaped, "*", "boxart")):
            for directory in glob.glob(pattern):
                candidate = os.path.join(directory, host_uuid, f"{app_id}.png")
                if os.path.isfile(candidate):
                    return candidate
    return None
