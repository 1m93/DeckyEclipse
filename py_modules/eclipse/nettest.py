"""How good is the connection to a host, and which bitrate suits it?

Sunshine has no speed test, so the test combines what can be measured from
here without help from the host:

* round trips to its streaming server (TCP connects to port 47989): latency,
  jitter and lost attempts;
* the link this device uses to reach it (``ip route get``): wired or Wi-Fi,
  and for Wi-Fi the signal and the negotiated rate (``/proc/net/wireless``,
  ``iw``).

``suggest`` turns that into a bitrate the network should carry comfortably.
"""

from __future__ import annotations

import asyncio
import re
import statistics
import time
from typing import Any, Optional

from . import proc

SAMPLES = 12
SAMPLE_INTERVAL_S = 0.15
SAMPLE_TIMEOUT_S = 1.0
# Bitrates are suggested in steps of this many Kbps, within these bounds.
STEP_KBPS = 5000
MIN_KBPS = 5000
MAX_WIRED_KBPS = 100000
MAX_WIFI_KBPS = 80000
# Over the internet (VPN, or a far host) the home upload usually limits the stream, which
# cannot be measured from here: stay at a bitrate most uploads carry.
MAX_REMOTE_KBPS = 15000
REMOTE_LATENCY_MS = 30
VPN_IFACES = ("tailscale", "wg", "tun", "tap", "zt", "ppp")

_DEV_RE = re.compile(r"\bdev\s+(\S+)")
_TX_RE = re.compile(r"tx bitrate:\s*([\d.]+)\s*MBit/s", re.IGNORECASE)
_SIGNAL_RE = re.compile(r"signal:\s*(-?\d+)\s*dBm", re.IGNORECASE)
_FREQ_RE = re.compile(r"freq:\s*([\d.]+)", re.IGNORECASE)


async def _connect_ms(address: str, port: int) -> Optional[float]:
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), SAMPLE_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        return None
    elapsed = (time.perf_counter() - started) * 1000
    writer.close()
    return elapsed


async def round_trips(address: str, port: int, count: int = SAMPLES) -> list[Optional[float]]:
    samples: list[Optional[float]] = []
    for index in range(count):
        samples.append(await _connect_ms(address, port))
        if index + 1 < count:
            await asyncio.sleep(SAMPLE_INTERVAL_S)
    return samples


def parse_wireless(text: str, iface: str) -> Optional[int]:
    """Signal level (dBm) of ``iface`` from /proc/net/wireless, or None when it is not wireless."""
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if sep and name.strip() == iface:
            fields = rest.split()
            if len(fields) >= 3:
                try:
                    return int(float(fields[2].rstrip(".")))
                except ValueError:
                    return 0
            return 0
    return None


def parse_iw_link(text: str) -> dict[str, Optional[float]]:
    def first(regex: re.Pattern[str]) -> Optional[float]:
        match = regex.search(text)
        return float(match.group(1)) if match else None

    return {"txMbps": first(_TX_RE), "signalDbm": first(_SIGNAL_RE), "freqMhz": first(_FREQ_RE)}


async def link_info(address: str, home: Optional[str] = None) -> dict[str, Any]:
    """The interface used to reach ``address``, and Wi-Fi details when it is wireless."""
    rc, out, _ = await proc.run(["ip", "route", "get", address], timeout=5, home=home)
    match = _DEV_RE.search(out) if rc == 0 else None
    iface = match.group(1) if match else None
    info: dict[str, Any] = {
        "iface": iface, "wireless": False, "vpn": bool(iface and iface.startswith(VPN_IFACES)),
        "signalDbm": None, "linkMbps": None, "freqMhz": None,
    }
    if not iface or info["vpn"]:
        return info
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as fh:
            signal = parse_wireless(fh.read(), iface)
    except OSError:
        signal = None
    if signal is None:
        return info
    info["wireless"] = True
    info["signalDbm"] = signal or None
    rc, out, _ = await proc.run(["iw", "dev", iface, "link"], timeout=5, home=home)
    if rc == 0:
        link = parse_iw_link(out)
        info["linkMbps"] = link["txMbps"]
        info["freqMhz"] = link["freqMhz"]
        if link["signalDbm"] is not None:
            info["signalDbm"] = int(link["signalDbm"])
    return info


def _round_kbps(kbps: float) -> int:
    return max(MIN_KBPS, int(kbps // STEP_KBPS) * STEP_KBPS)


def suggest(samples: list[Optional[float]], link: dict[str, Any]) -> dict[str, Any]:
    """Summary of the measurements, verdicts on delay and stability, and the suggested bitrate (Kbps)."""
    good = [s for s in samples if s is not None]
    loss = 1 - len(good) / len(samples) if samples else 1.0
    latency = statistics.median(good) if good else None
    jitter = statistics.pstdev(good) if len(good) > 1 else 0.0
    notes: list[str] = []
    remote = bool(link.get("vpn")) or (latency is not None and latency > REMOTE_LATENCY_MS)

    if link.get("wireless"):
        rate = link.get("linkMbps")
        signal = link.get("signalDbm")
        # The usable throughput of Wi-Fi is about half its link rate; a stream should take
        # a third of that or less, so it survives others using the network.
        cap = rate * 1000 / 6 if rate else 30000
        cap = min(cap, MAX_WIFI_KBPS)
        if signal is not None and signal <= -75:
            cap *= 0.5
            notes.append("The Wi-Fi signal is weak: move closer to the router.")
        elif signal is not None and signal <= -67:
            cap *= 0.75
            notes.append("The Wi-Fi signal is fair.")
        freq = link.get("freqMhz")
        if freq and freq < 3000:
            notes.append("Connected on 2.4 GHz: a 5 GHz network gives smoother streams.")
    else:
        cap = MAX_WIRED_KBPS
    if remote:
        cap = min(cap, MAX_REMOTE_KBPS)
        notes.append(
            "The host is reached over the internet or a VPN: the upload speed of the host's network limits "
            "the stream, and this test cannot measure it. Lower the bitrate further if it stutters."
        )
    if jitter > 8:
        cap *= 0.75
        notes.append("Latency jumps around: the network is busy or the signal unstable.")
    if loss > 0:
        cap *= 0.5
        notes.append("Some attempts failed: the connection drops packets.")

    # Delay costs responsiveness (input lag); instability (jitter, loss) costs smoothness (stutter).
    if latency is None:
        verdict = delay = "unreachable"
    else:
        delay = "low" if latency < 10 else "medium" if latency < REMOTE_LATENCY_MS else "high"
        if loss == 0 and jitter < 3:
            verdict = "great"
        elif loss == 0 and jitter < 8:
            verdict = "good"
        elif loss <= 0.1 and jitter < 15:
            verdict = "fair"
        else:
            verdict = "poor"
    return {
        "latencyMs": round(latency, 1) if latency is not None else None,
        "jitterMs": round(jitter, 1),
        "lossPct": round(loss * 100),
        "attempts": len(samples),
        "failed": len(samples) - len(good),
        "remote": remote,
        "delay": delay,
        "verdict": verdict,
        "suggestedKbps": _round_kbps(cap) if latency is not None else None,
        "notes": notes,
    }


async def run(address: str, port: int, home: Optional[str] = None) -> dict[str, Any]:
    samples, link = await asyncio.gather(round_trips(address, port), link_info(address, home))
    return {"address": address, **link, **suggest(samples, link)}
