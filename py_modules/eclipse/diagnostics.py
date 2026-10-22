"""Why can't other devices find or reach the Sunshine server on this device?

Each check returns ``ok`` as True (fine), False (a problem) or None (could not
tell), plus a hint telling the user what to do. The checks only read state;
nothing is changed on the system.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Optional

from . import DEFAULT_HTTP_PORT, DEFAULT_SUNSHINE_WEB_PORT, FLATPAK_SUNSHINE
from . import network, proc

_IP_LINE_RE = re.compile(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/(\d+)")
_VIRTUAL_IFACES = ("docker", "br-", "virbr", "veth", "tailscale", "zt", "lo")

# Default Sunshine ports, for the firewall hint.
PORTS_HINT = "TCP 47984, 47989, 47990, 48010 and UDP 47998-48000, 48002, 48010"


async def local_ipv4(home: Optional[str] = None) -> list[dict[str, str]]:
    """LAN IPv4 addresses of this device, physical interfaces first."""
    rc, out, _ = await proc.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], timeout=5, home=home)
    if rc != 0:
        return []
    found = []
    for line in out.splitlines():
        match = _IP_LINE_RE.match(line.strip())
        if match:
            iface, address, _ = match.groups()
            found.append({"interface": iface, "address": address})
    return sorted(found, key=lambda entry: entry["interface"].startswith(_VIRTUAL_IFACES))


async def _system_service(name: str, home: Optional[str]) -> Optional[bool]:
    """True if active, False if installed but not running, None if not installed."""
    rc, out, _ = await proc.run(["systemctl", "list-unit-files", "--no-legend", name], timeout=5, home=home)
    if rc != 0 or not out.strip():
        return None
    rc, _, _ = await proc.run(["systemctl", "is-active", "--quiet", name], timeout=5, home=home)
    return rc == 0


async def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


def sunshine_log_path(home: str) -> Optional[str]:
    """Sunshine's own log file (it writes one next to its config)."""
    candidates = [
        os.path.join(home, ".var", "app", FLATPAK_SUNSHINE, "config", "sunshine", "sunshine.log"),
        os.path.join(home, ".config", "sunshine", "sunshine.log"),
    ]
    existing = [path for path in candidates if os.path.isfile(path)]
    return max(existing, key=os.path.getmtime) if existing else None


_FLATPAK_CAPTURE_HINT = (
    "The Flatpak build of Sunshine cannot capture the screen with KMS, which Gaming Mode needs. "
    "Clients then fail with \"Failed to initialize video capture/encoding (Error 503)\". "
    "Install Sunshine for Gaming Mode from Eclipse's Sunshine page instead."
)
_REPAIR_HINT = (
    "Sunshine is not allowed to read the screen (KMS). Use Repair on Eclipse's Sunshine page; "
    "it asks for your password once."
)
_SETCAP_HINT = (
    "Sunshine is not allowed to read the screen (KMS). A native Sunshine needs the cap_sys_admin "
    "capability: sudo setcap cap_sys_admin+p $(readlink -f $(which sunshine))."
)


def _capture_problems(kind: Optional[str]) -> tuple[tuple[str, str], ...]:
    """Capture errors Sunshine logs on Linux (strings from its kmsgrab.cpp), with hints for this install."""
    permission = _REPAIR_HINT if kind == "eclipse" else _SETCAP_HINT
    return (
        ("AppImage and Flatpak do not support KMS capture", _FLATPAK_CAPTURE_HINT),
        ("Probably not permitted", permission),
        ("Failed to gain CAP_SYS_ADMIN", permission),
    )


def _capture_problem(log_path: Optional[str], kind: Optional[str] = None) -> Optional[str]:
    """A hint for the most recent known capture error in Sunshine's log, if any."""
    if not log_path:
        return None
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 512 * 1024))
            tail = fh.read().decode(errors="replace")
    except OSError:
        return None
    found = [(tail.rfind(needle), hint) for needle, hint in _capture_problems(kind) if needle in tail]
    return max(found)[1] if found else None


def _check(check_id: str, label: str, ok: Optional[bool], detail: str = "", hint: str = "") -> dict[str, Any]:
    return {"id": check_id, "label": label, "ok": ok, "detail": detail, "hint": hint if ok is not True else ""}


async def run(status: dict[str, Any], home: str) -> dict[str, Any]:
    """Run all checks concurrently. ``status`` is LocalSunshine.status()."""
    server, web, avahi, firewalld, ufw, ips = await asyncio.gather(
        network.probe("127.0.0.1", DEFAULT_HTTP_PORT, 2.0),
        _tcp_open("127.0.0.1", DEFAULT_SUNSHINE_WEB_PORT),
        _system_service("avahi-daemon.service", home),
        _system_service("firewalld.service", home),
        _system_service("ufw.service", home),
        local_ipv4(home),
    )
    running = bool(status.get("running"))
    firewall_on = bool(firewalld or ufw)
    log_path = sunshine_log_path(home)
    kind = status.get("kind")
    capture_problem = _capture_problem(log_path, kind)
    native_state = status.get("native") or {}

    checks = [
        _check("running", "Sunshine is running", running, hint="Start Sunshine with the switch above."),
        _check(
            "server", "Streaming server answers (port 47989)",
            server is not None if running else None,
            detail=f"Server name: {server.get('hostname')}" if server else "",
            hint="Sunshine runs but its server does not answer. It may still be starting, or it failed: see the Sunshine log below.",
        ),
        _check(
            "web", "Web UI answers (port 47990)", web if running else None,
            hint="The web UI is needed to set a login and to pair clients. See the Sunshine log below.",
        ),
        *([_check(
            "permission", "Allowed to capture Gaming Mode", bool(native_state.get("captureReady")),
            hint=_REPAIR_HINT,
        )] if kind == "eclipse" else []),
        # Known limitation, no need to wait for the error in the log.
        *([_check("flatpak", "Can capture Gaming Mode", False, detail="Flatpak build", hint=_FLATPAK_CAPTURE_HINT)]
          if kind == "flatpak" else []),
        _check(
            "capture", "Screen capture",
            # Only a known error is conclusive; without one the log cannot prove capture works.
            False if capture_problem else None,
            detail="" if capture_problem else "No capture error in Sunshine's log (it only shows up once a client tries to stream).",
            hint=capture_problem or "",
        ),
        _check(
            "network", "Connected to a network", bool(ips),
            detail=", ".join(f"{i['address']} ({i['interface']})" for i in ips),
            hint="Connect this device to the same Wi-Fi or LAN as the other device.",
        ),
        _check(
            "discovery", "Automatic discovery (avahi-daemon)", avahi,
            detail="Running" if avahi else "",
            hint=(
                "Other devices find this one automatically through mDNS, which needs avahi-daemon. "
                "In Desktop Mode run: sudo systemctl enable --now avahi-daemon. "
                "Until then, add this device in Moonlight by its IP address (shown above)."
            ) if avahi is False else (
                "avahi-daemon is not installed, so automatic discovery cannot work. "
                "Add this device in Moonlight by its IP address (shown above)."
            ),
        ),
        _check(
            "firewall", "No firewall blocking Sunshine", not firewall_on,
            detail="firewalld active" if firewalld else ("ufw active" if ufw else ""),
            hint=f"A firewall is active. Allow Sunshine's ports: {PORTS_HINT}.",
        ),
    ]
    return {"checks": checks, "addresses": ips, "logPath": log_path}
