"""Host reachability probes and Wake-on-LAN.

Probes hit the unauthenticated ``/serverinfo`` endpoint that every
GameStream-compatible host (Sunshine, GFE) exposes on its HTTP port. The
async probe uses raw asyncio streams so many hosts can be checked
concurrently without spawning threads.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
import uuid as uuid_lib
from typing import Any, Optional

from . import DEFAULT_HTTP_PORT

_TAG_RE = re.compile(r"<(\w+)>([^<]*)</\1>")
_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.IGNORECASE)
_CLIENT_ID = "0123456789ABCDEF"
_MAX_RESPONSE = 64 * 1024


def normalize_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    mac = mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac) or mac == "00:00:00:00:00:00":
        return None
    return mac


def split_address(address: str, default_port: int = DEFAULT_HTTP_PORT) -> tuple[str, int]:
    """Split ``host``, ``host:port``, ``[v6]`` or ``[v6]:port`` into parts."""
    address = address.strip()
    if address.startswith("["):
        host, _, rest = address[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return host, int(port) if port.isdigit() else default_port
    if address.count(":") == 1:
        host, _, port = address.partition(":")
        return host, int(port) if port.isdigit() else default_port
    return address, default_port


def _serverinfo_request(host: str) -> bytes:
    host_header = f"[{host}]" if ":" in host else host
    path = f"/serverinfo?uniqueid={_CLIENT_ID}&uuid={uuid_lib.uuid4().hex}"
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Connection: close\r\n"
        "User-Agent: DeckyEclipse\r\n\r\n"
    ).encode()


def parse_serverinfo(body: str) -> dict[str, Any]:
    """Parse ``/serverinfo``.

    In Sunshine, ``state``/``currentgame`` only say whether an app process is
    running on the host (``proc::proc.running()``), not whether a client is
    streaming. An app keeps running after a client disconnects without
    quitting it, so ``busy`` means "an app is open and can be resumed".
    """
    tags = {key.lower(): value.strip() for key, value in _TAG_RE.findall(body)}
    state = tags.get("state", "")
    current_game = tags.get("currentgame", "0")
    running = current_game not in ("", "0")
    busy = state.endswith("_BUSY") or running
    return {
        "online": True,
        "busy": busy,
        "currentGame": current_game if running else None,
        "hostname": tags.get("hostname"),
        "uniqueId": tags.get("uniqueid"),
        "version": tags.get("appversion"),
        "mac": normalize_mac(tags.get("mac")),
        "codecSupport": tags.get("servercodecmodesupport"),
    }


async def probe(address: str, port: int, timeout: float = 1.5) -> Optional[dict[str, Any]]:
    """Return parsed serverinfo for a host, or ``None`` when unreachable."""
    started = time.monotonic()
    writer: Optional[asyncio.StreamWriter] = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout)
        writer.write(_serverinfo_request(address))
        await writer.drain()
        remaining = max(0.2, timeout - (time.monotonic() - started))
        data = await asyncio.wait_for(reader.read(_MAX_RESPONSE), remaining)
        # Read until EOF (Connection: close) without exceeding the size cap.
        while len(data) < _MAX_RESPONSE:
            remaining = max(0.1, timeout - (time.monotonic() - started))
            chunk = await asyncio.wait_for(reader.read(_MAX_RESPONSE - len(data)), remaining)
            if not chunk:
                break
            data += chunk
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if writer is not None:
            writer.close()

    text = data.decode(errors="replace")
    if "<root" not in text:
        return None
    info = parse_serverinfo(text)
    info["address"] = address
    info["port"] = port
    info["latencyMs"] = int((time.monotonic() - started) * 1000)
    return info


async def probe_any(addresses: list[dict[str, Any]], timeout: float = 1.5) -> Optional[dict[str, Any]]:
    """Probe all known addresses of a host concurrently, first answer wins."""
    if not addresses:
        return None
    tasks = [
        asyncio.ensure_future(probe(entry["address"], int(entry.get("port") or DEFAULT_HTTP_PORT), timeout))
        for entry in addresses
    ]
    try:
        for next_done in asyncio.as_completed(tasks):
            result = await next_done
            if result:
                return result
    finally:
        for task in tasks:
            task.cancel()
    return None


def probe_sync(address: str, port: int, timeout: float = 1.5) -> Optional[dict[str, Any]]:
    """Blocking variant of :func:`probe` for the launcher. ``None`` when unreachable."""
    try:
        with socket.create_connection((address, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_serverinfo_request(address))
            data = b""
            while len(data) < _MAX_RESPONSE:
                chunk = sock.recv(_MAX_RESPONSE - len(data))
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    text = data.decode(errors="replace")
    if "<root" not in text:
        return None
    return {**parse_serverinfo(text), "address": address, "port": port}


def _broadcast_targets(address_hints: list[str]) -> list[str]:
    targets = ["255.255.255.255"]
    for hint in address_hints:
        try:
            ip = ipaddress.ip_address(hint)
        except ValueError:
            continue
        if ip.version == 4 and ip.is_private:
            # Assume a /24 for the subnet-directed broadcast, the common home LAN case.
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
            targets.append(str(network.broadcast_address))
        if ip.version == 4:
            # Unicast also helps routers that keep a static ARP entry.
            targets.append(str(ip))
    return list(dict.fromkeys(targets))


def send_wol(mac: str, address_hints: Optional[list[str]] = None) -> bool:
    """Send Wake-on-LAN magic packets to every plausible target."""
    normalized = normalize_mac(mac)
    if not normalized:
        return False
    payload = b"\xff" * 6 + bytes.fromhex(normalized.replace(":", "")) * 16

    sent = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in _broadcast_targets(address_hints or []):
            for port in (9, 7, 40000):
                try:
                    sock.sendto(payload, (target, port))
                    sent = True
                except OSError:
                    continue
    return sent


# Addresses scanned per network at most: a /24, which covers home networks.
_SCAN_PREFIX = 24
_SCAN_CONCURRENCY = 64


def scan_targets(local: list[dict[str, str]]) -> list[str]:
    """Addresses to look for hosts at: the /24 (or smaller) network of each local address, minus our own."""
    own = {entry["address"] for entry in local}
    targets: list[str] = []
    seen_networks = set()
    for entry in local:
        try:
            prefix = max(_SCAN_PREFIX, int(entry.get("prefix") or _SCAN_PREFIX))
            network = ipaddress.ip_network(f"{entry['address']}/{prefix}", strict=False)
        except ValueError:
            continue
        if network in seen_networks or not network.is_private:
            continue
        seen_networks.add(network)
        targets += [str(ip) for ip in network.hosts() if str(ip) not in own]
    return targets


async def discover(local: list[dict[str, str]], timeout: float = 0.8) -> list[dict[str, Any]]:
    """Sunshine (or GameStream) hosts answering ``/serverinfo`` on the local networks."""
    semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def check(address: str) -> Optional[dict[str, Any]]:
        async with semaphore:
            return await probe(address, DEFAULT_HTTP_PORT, timeout)

    results = await asyncio.gather(*(check(address) for address in scan_targets(local)))
    found: dict[str, dict[str, Any]] = {}
    for info in results:
        if not info:
            continue
        # A host with several addresses answers on each of them: list it once.
        key = info.get("uniqueId") or info["address"]
        found.setdefault(key, info)
    return sorted(found.values(), key=lambda info: (info.get("hostname") or "").lower())
