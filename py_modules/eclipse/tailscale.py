"""Tailscale on this device: its address, and peers that may run Sunshine.

Away from home, Moonlight can reach a PC through Tailscale (a WireGuard VPN)
by its Tailscale address. LAN discovery cannot see those PCs, so their
addresses come from ``tailscale status --json`` instead. Nothing here changes
Tailscale; when it is not installed or not running, everything is empty.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import proc

# Where the community SteamOS installers put the CLI, besides PATH.
_CLI_PATHS = ("/opt/tailscale/tailscale", "/usr/bin/tailscale", "/usr/local/bin/tailscale")
_MAX_PEERS = 64


def find_cli(home: Optional[str] = None) -> Optional[str]:
    found = proc.which("tailscale", home)
    if found:
        return found
    candidates = [*_CLI_PATHS]
    if home:
        candidates.append(os.path.join(home, ".local", "bin", "tailscale"))
    return next((path for path in candidates if os.access(path, os.X_OK)), None)


def _ipv4(addresses: Any) -> Optional[str]:
    for address in addresses if isinstance(addresses, list) else []:
        if isinstance(address, str) and address.count(".") == 3:
            return address
    return None


def _name(node: dict[str, Any]) -> str:
    dns = str(node.get("DNSName") or "").split(".")[0]
    return str(node.get("HostName") or dns or "")


def parse_status(text: str) -> dict[str, Any]:
    """``{"running", "self": address or None, "peers": [{"address", "name", "online"}]}``."""
    try:
        data = json.loads(text)
    except ValueError:
        return {"running": False, "self": None, "peers": []}
    if not isinstance(data, dict):
        return {"running": False, "self": None, "peers": []}
    running = data.get("BackendState") == "Running"
    me = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    peers = []
    raw_peers = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
    for node in raw_peers.values():
        if not isinstance(node, dict):
            continue
        address = _ipv4(node.get("TailscaleIPs"))
        if address:
            peers.append({"address": address, "name": _name(node), "online": bool(node.get("Online"))})
    peers.sort(key=lambda p: (not p["online"], p["name"].lower()))
    return {"running": running, "self": _ipv4(me.get("TailscaleIPs")) if running else None, "peers": peers[:_MAX_PEERS]}


async def status(home: Optional[str] = None) -> dict[str, Any]:
    """Tailscale's state on this device; ``installed`` False when there is no CLI."""
    cli = find_cli(home)
    if not cli:
        return {"installed": False, "running": False, "self": None, "peers": []}
    rc, out, _ = await proc.run([cli, "status", "--json"], timeout=5, home=home)
    # "tailscale status" exits non-zero when stopped but still prints the JSON.
    parsed = parse_status(out) if out.strip() else {"running": False, "self": None, "peers": []}
    if rc != 0 and not parsed["running"]:
        parsed["peers"] = []
    return {"installed": True, **parsed}
