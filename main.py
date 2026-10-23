"""Eclipse - Moonlight & Sunshine control for SteamOS Gaming Mode (Decky backend)."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import socket
import sys
import time
from typing import Any, Optional

import decky

# Decky adds py_modules to sys.path for us, but keep this for local tooling.
sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))

from eclipse import DEFAULT_HTTP_PORT, LAUNCHER_LOG, SETTINGS_FILE  # noqa: E402
from eclipse import FLATPAK_MOONLIGHT, FLATPAK_SUNSHINE  # noqa: E402
from eclipse import diagnostics, display, flatpak, host_display, launch, moonlight, network, updates  # noqa: E402
from eclipse import proc, sunshine_config, sunshine_data, sunshine_native  # noqa: E402
from eclipse import backup, battery, mdns, nettest, sunshine_apps, tailscale  # noqa: E402
from eclipse.sunshine_session import AutoOff, PeerTracker, SessionTracker, client_tcp_ports, read_tcp_peers  # noqa: E402
from eclipse.log import set_logger  # noqa: E402
from eclipse.moonlight_config import MoonlightConfig, find_boxart  # noqa: E402
from eclipse.presets import AUTO_PRESET, CUSTOM_PRESET, INHERIT_PRESET, all_presets, effective_stream  # noqa: E402
from eclipse.settings import DEFAULTS as DEFAULT_SETTINGS, SettingsStore  # noqa: E402
from eclipse.sunshine import LocalSunshine, SunshineApi, pair_client, pairing_device_name  # noqa: E402

set_logger(decky.logger)
log = decky.logger

FLATPAK_IDS = {"moonlight": FLATPAK_MOONLIGHT, "sunshine": FLATPAK_SUNSHINE}
PROBE_CACHE_S = 3.0
# Minimum time between install progress events sent to the frontend.
PROGRESS_INTERVAL_S = 0.5
LOG_TAIL_LINES = 200
# How often the Sunshine on this device is checked for streaming sessions.
SESSION_POLL_S = 3.0
SESSION_IDLE_POLL_S = 10.0
SESSION_EVENT = "eclipse_sunshine_session"
# Probes in a row Sunshine must miss before it counts as stopped.
SESSION_MISSES_OFFLINE = 2
AUTO_OFF_EVENT = "eclipse_sunshine_auto_off"
PAIRING_EVENT = "eclipse_sunshine_pairing"
# How often Sunshine's log is checked for a failed mDNS publication, and how
# long to wait before trying to announce again after the responder failed.
MDNS_CHECK_S = 20.0
MDNS_RETRY_S = 60.0
# LAN addresses change rarely (Wi-Fi switch): re-read them at most this often.
ADDRESSES_CACHE_S = 30.0
# Reverse DNS names of streaming devices, also when unknown.
PEER_NAME_CACHE_S = 600.0
PEER_NAME_TIMEOUT_S = 2.0
# Block elements flatpak uses to draw progress bars.
_BAR_CHARS = re.compile(r"[\u2580-\u259f]+")


def _clean_progress(line: str) -> str:
    """Strip progress-bar glyphs and collapse whitespace in flatpak output."""
    return " ".join(_BAR_CHARS.sub(" ", line).split())
MOONLIGHT_CACHE_S = 30.0
MAX_BOXART_BYTES = 4 * 1024 * 1024


class Plugin:
    settings: SettingsStore
    config: MoonlightConfig
    local_sunshine: LocalSunshine

    _probe_cache: dict[str, Any]
    _probe_at: float
    _probe_lock: asyncio.Lock
    _moonlight: Optional[dict[str, Any]]
    _moonlight_at: float
    # Feature with a running operation ("moonlight" / "sunshine"), one at a time.
    _installing: Optional[str]
    # "install" | "update" | "uninstall"
    _operation: Optional[str]
    _install_task: Optional[asyncio.Future]
    _cancel_requested: bool
    # False while a step runs as root (sudo): it cannot be stopped from here.
    _cancellable: bool
    _pending_creds: dict[str, dict[str, Any]]
    _tasks: set[asyncio.Task]
    _sessions: SessionTracker
    _session_lock: asyncio.Lock
    _session_last: Optional[dict[str, Any]]
    _session_misses: int
    _auto_off: AutoOff
    # mtime of Sunshine's state file when duplicate paired devices were last looked for.
    _state_checked: float
    _mdns: Optional[mdns.Responder]
    # (checked at, Sunshine failed to publish) from its log.
    _mdns_failed: tuple[float, bool]
    _mdns_retry_at: float
    # The last attempt to announce failed (e.g. UDP 5353 unavailable).
    _mdns_failed_start: bool
    _addresses: tuple[float, list[dict[str, str]]]
    _peers: PeerTracker
    # Reverse DNS names of streaming devices: address -> (looked up at, name).
    _peer_names: dict[str, tuple[float, Optional[str]]]
    _battery_guard: battery.BatteryGuard
    # Pairing requests already announced, and whether this Sunshine can list them (None: not known yet).
    _pairings_seen: set[str]
    _pairings_supported: Optional[bool]

    # ------------------------------------------------------------------ lifecycle

    async def _main(self) -> None:
        home = decky.DECKY_USER_HOME
        self.settings = SettingsStore(os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SETTINGS_FILE))
        self.config = MoonlightConfig(home)
        self.local_sunshine = LocalSunshine(home, decky.DECKY_PLUGIN_LOG_DIR, decky.DECKY_PLUGIN_DIR)
        self._probe_cache = {}
        self._probe_at = float("-inf")
        self._probe_lock = asyncio.Lock()
        self._moonlight = None
        self._moonlight_at = 0.0
        self._installing = None
        self._operation = None
        self._install_task = None
        self._cancel_requested = False
        self._cancellable = True
        self._pending_creds = {}
        self._tasks = set()
        self._sessions = SessionTracker()
        self._session_lock = asyncio.Lock()
        self._session_last = None
        self._session_misses = 0
        self._auto_off = AutoOff()
        self._state_checked = 0.0
        self._mdns = None
        self._mdns_failed = (float("-inf"), False)
        self._mdns_retry_at = 0.0
        self._mdns_failed_start = False
        self._addresses = (float("-inf"), [])
        self._peers = PeerTracker()
        self._peer_names = {}
        self._battery_guard = battery.BatteryGuard()
        self._pairings_seen = set()
        self._pairings_supported = None
        log.info("Eclipse backend started (home=%s)", home)

        features = self.settings.data["features"]
        if features.get("sunshine") and self.settings.data["sunshine"].get("autostart"):
            self._spawn(self._autostart_sunshine())
        self._spawn(self._watch_sunshine_sessions())

    async def _unload(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._mdns:
            await self._mdns.stop()
            self._mdns = None
        log.info("Eclipse backend unloaded")

    async def _uninstall(self) -> None:
        log.info("Eclipse uninstalled")

    # ------------------------------------------------------------------ helpers

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve_moonlight(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if force or self._moonlight is None or now - self._moonlight_at > MOONLIGHT_CACHE_S:
            self._moonlight = await asyncio.to_thread(
                moonlight.resolve, self.settings.data["moonlight"], decky.DECKY_USER_HOME
            )
            self._moonlight_at = now
        return self._moonlight

    def _host_cfg(self, uuid: str) -> dict[str, Any]:
        return (self.settings.data.get("hosts") or {}).get(uuid) or {}

    def _hosts(self) -> list[dict[str, Any]]:
        hosts = self.config.hosts()
        self._adopt_pending_creds(hosts)
        return hosts

    def _find_host(self, uuid: str) -> Optional[dict[str, Any]]:
        return next((h for h in self._hosts() if h["uuid"] == uuid), None)

    def _adopt_pending_creds(self, hosts: list[dict[str, Any]]) -> None:
        """Attach Sunshine credentials entered while adding a host by address.

        The host UUID is only known after Moonlight paired and wrote its config.
        """
        if not self._pending_creds:
            return
        for host in hosts:
            for entry in host["addresses"]:
                creds = self._pending_creds.pop(entry["address"], None)
                if creds:
                    self.settings.update({"hosts": {host["uuid"]: creds}})
                    break

    def _host_mac(self, host: dict[str, Any]) -> Optional[str]:
        cfg = self._host_cfg(host["uuid"])
        cached = self._probe_cache.get(host["uuid"]) or {}
        return network.normalize_mac(cfg.get("mac")) or host.get("mac") or cached.get("mac")

    def _sunshine_api_for(self, host: dict[str, Any], address: Optional[str] = None) -> Optional[SunshineApi]:
        cfg = self._host_cfg(host["uuid"])
        if not cfg.get("sunshineUser") or not cfg.get("sunshinePassword"):
            return None
        target = address or self._best_address(host)
        if not target:
            return None
        return SunshineApi(target, cfg["sunshineUser"], cfg["sunshinePassword"], int(cfg.get("sunshinePort") or 0))

    def _host_addresses(self, host: dict[str, Any]) -> list[dict[str, Any]]:
        """Moonlight's addresses of a host plus the ones saved in Eclipse (e.g. its Tailscale address)."""
        known = {a["address"] for a in host["addresses"]}
        extra = [
            {"kind": "extra", "address": a, "port": DEFAULT_HTTP_PORT}
            for a in self._host_cfg(host["uuid"]).get("extraAddresses") or []
            if isinstance(a, str) and a not in known
        ]
        return [*host["addresses"], *extra]

    def _moonlight_target(self, host: dict[str, Any], status: Optional[dict[str, Any]]) -> str:
        """What to give Moonlight for a host: its name, or the reachable address when Moonlight does
        not know that address yet (Moonlight then adds it to the host, matched by its id)."""
        address = (status or {}).get("address")
        if address and address not in {a["address"] for a in host["addresses"]}:
            return str(address)
        return host["name"]

    def _best_address(self, host: dict[str, Any]) -> Optional[str]:
        cached = self._probe_cache.get(host["uuid"]) or {}
        if cached.get("address"):
            return cached["address"]
        return host["addresses"][0]["address"] if host["addresses"] else None

    async def _emit(self, event: str, *args: Any) -> None:
        try:
            await decky.emit(event, *args)
        except Exception as exc:  # noqa: BLE001 - never let UI events break a flow
            log.warning("emit %s failed: %s", event, exc)

    # ------------------------------------------------------------------ state

    async def get_state(self) -> dict[str, Any]:
        """Everything the UI needs on first render, in a single round trip."""
        resolved = await self._resolve_moonlight()
        display_info = await asyncio.to_thread(display.detect)
        return {
            "settings": self.settings.public(),
            "presets": all_presets(display_info),
            "autoPreset": AUTO_PRESET,
            "display": display_info,
            "customPreset": CUSTOM_PRESET,
            "inheritPreset": INHERIT_PRESET,
            "hosts": self._hosts(),
            "moonlight": {k: v for k, v in resolved.items() if k != "command"},
            "configPath": self.config.path,
            "launcher": {
                "python": "/usr/bin/python3",
                "script": os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "eclipse_launcher.py"),
                "startDir": os.path.join(decky.DECKY_PLUGIN_DIR, "bin"),
            },
            "installing": self._installing,
            "operation": self._operation,
            "pluginVersion": updates.read_package(decky.DECKY_PLUGIN_DIR).get("version"),
        }

    async def get_display(self) -> dict[str, Any]:
        """Re-detect the display (e.g. after docking) and the matching Auto preset."""
        info = await asyncio.to_thread(display.detect)
        return {"display": info, "presets": all_presets(info)}

    async def get_hosts(self) -> list[dict[str, Any]]:
        return self._hosts()

    async def probe_hosts(self, force: bool = False) -> dict[str, Any]:
        """Probe all hosts concurrently. Results are cached briefly to dedupe callers."""
        async with self._probe_lock:
            if not force and time.monotonic() - self._probe_at < PROBE_CACHE_S:
                return self._probe_cache
            hosts = self._hosts()
            results = await asyncio.gather(*(network.probe_any(self._host_addresses(h)) for h in hosts))
            cache: dict[str, Any] = {}
            for host, info in zip(hosts, results):
                previous = self._probe_cache.get(host["uuid"]) or {}
                if info:
                    cache[host["uuid"]] = info
                else:
                    # Remember a MAC learned while online so WoL works once it sleeps.
                    cache[host["uuid"]] = {"online": False, "busy": False, "mac": previous.get("mac")}
            self._probe_cache = cache
            self._probe_at = time.monotonic()
            return cache

    async def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.settings.update(patch)
        if "moonlight" in patch:
            self._moonlight = None
        return self.settings.public()

    async def set_host_config(self, uuid: str, patch: Optional[dict[str, Any]]) -> dict[str, Any]:
        if patch and patch.get("mac"):
            mac = network.normalize_mac(patch["mac"])
            if not mac:
                return {"ok": False, "error": "Invalid MAC address", "settings": self.settings.public()}
            patch = {**patch, "mac": mac}
        self.settings.update({"hosts": {uuid: patch}})
        return {"ok": True, "error": None, "settings": self.settings.public()}

    async def refresh_moonlight(self) -> dict[str, Any]:
        resolved = await self._resolve_moonlight(force=True)
        return {k: v for k, v in resolved.items() if k != "command"}

    # ------------------------------------------------------------------ wake

    async def wake_host(self, uuid: str) -> dict[str, Any]:
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        mac = self._host_mac(host)
        if not mac:
            return {"ok": False, "error": "No MAC address known for this host"}
        ok = await asyncio.to_thread(network.send_wol, mac, [a["address"] for a in host["addresses"]])
        return {"ok": ok, "error": None if ok else "Failed to send the magic packet"}

    async def _wait_online(self, host: dict[str, Any], mac: str, timeout: float) -> bool:
        hints = [a["address"] for a in host["addresses"]]
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.to_thread(network.send_wol, mac, hints)
            await self._emit("eclipse_wake_progress", host["uuid"], int(time.monotonic() - started), int(timeout))
            if await network.probe_any(self._host_addresses(host), 2.0):
                return True
            await asyncio.sleep(2)
        return False

    # ------------------------------------------------------------------ launch requests

    async def prepare_stream(self, uuid: str, app_name: str) -> dict[str, Any]:
        """Validate, wake the host when needed, and write the stream request."""
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        resolved = await self._resolve_moonlight()
        if not resolved["installed"]:
            return {"ok": False, "error": resolved.get("error") or "Moonlight is not installed"}

        behavior = self.settings.data["behavior"]
        status = await network.probe_any(self._host_addresses(host))
        if not status:
            mac = self._host_mac(host)
            if behavior.get("autoWake") and mac:
                woke = await self._wait_online(host, mac, float(behavior.get("wakeTimeoutS") or 90))
                if not woke:
                    return {"ok": False, "error": f"{host['name']} did not wake up"}
                # Let the streaming service finish starting after boot/resume.
                await asyncio.sleep(3)
            else:
                return {"ok": False, "error": f"{host['name']} is offline"}

        if self._host_cfg(uuid).get("display"):
            await self._emit("eclipse_stream_progress", uuid, "Switching host display...")
            switched = await asyncio.to_thread(
                host_display.apply_host_selection, self.settings.data, host, decky.DECKY_PLUGIN_SETTINGS_DIR
            )
            if not switched["ok"]:
                return {"ok": False, "error": switched["error"], "monitorMissing": bool(switched.get("missing"))}

        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {
            "action": "stream",
            "host": self._moonlight_target(host, status or await network.probe_any(self._host_addresses(host))),
            "hostUuid": uuid,
            "app": app_name,
        })
        self.settings.update({"hosts": {uuid: {"lastApp": app_name}}})
        return {"ok": True, "error": None}

    async def prepare_gui(self) -> dict[str, Any]:
        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {"action": "gui"})
        return {"ok": True, "error": None}

    async def prepare_pair(self, address: str, uuid: Optional[str] = None,
                           creds: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Generate a PIN and write a pair request.

        When Sunshine credentials are known, the PIN is submitted to the host
        automatically shortly after Moonlight starts pairing.
        """
        address = (address or "").strip()
        host = self._find_host(uuid) if uuid else None
        if host and not address:
            address = self._best_address(host) or host["name"]
        if not address:
            return {"ok": False, "error": "No address given"}

        pin = f"{secrets.randbelow(10000):04d}"
        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {"action": "pair", "host": address, "pin": pin})

        api: Optional[SunshineApi] = None
        if host:
            api = self._sunshine_api_for(host, network.split_address(address)[0])
        elif creds and creds.get("sunshineUser") and creds.get("sunshinePassword"):
            host_part, _ = network.split_address(address)
            api = SunshineApi(host_part, creds["sunshineUser"], creds["sunshinePassword"], int(creds.get("sunshinePort") or 0))
            self._pending_creds[host_part] = {
                "sunshineUser": creds["sunshineUser"],
                "sunshinePassword": creds["sunshinePassword"],
                "sunshinePort": int(creds.get("sunshinePort") or 0) or None,
            }
        if api:
            self._spawn(self._auto_submit_pin(api, pin))
        return {"ok": True, "pin": pin, "autoSubmit": api is not None, "error": None}

    async def _auto_submit_pin(self, api: SunshineApi, pin: str) -> None:
        name = self.settings.data["sunshine"].get("deviceName") or "Steam Deck"
        # Moonlight needs a moment to start and ask the host to pair; retry until
        # the request shows up (pair_client reports "no device is waiting" until then).
        await asyncio.sleep(3)
        result: dict[str, Any] = {"ok": False, "error": "Timed out waiting for Moonlight to start pairing"}
        for _ in range(10):
            result = await pair_client(api, pin, name)
            if result["ok"] or "waiting" not in (result.get("error") or ""):
                break
            await asyncio.sleep(2)
        await self._emit("eclipse_pair_result", result["ok"], result.get("error"))

    async def pairing_outcome(self, address: str) -> dict[str, Any]:
        """After Moonlight's pairing window closed: did pairing with ``address`` succeed?"""
        wanted = network.split_address(address)[0].lower()
        for host in self._hosts():
            names = {host["name"].lower()} | {network.split_address(a["address"])[0].lower() for a in host["addresses"]}
            if wanted in names:
                return {"paired": bool(host.get("paired")), "host": host["name"]}
        return {"paired": False, "host": None}

    async def prepare_quit(self, uuid: str) -> dict[str, Any]:
        """Quit the running app on a host, via the Sunshine API when possible."""
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        api = self._sunshine_api_for(host)
        if api:
            result = await api.close_app()
            if result["ok"]:
                return {"ok": True, "launch": False, "error": None}
            log.info("Sunshine close failed (%s), falling back to Moonlight", result["error"])
        status = await network.probe_any(self._host_addresses(host))
        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {"action": "quit", "host": self._moonlight_target(host, status)})
        return {"ok": True, "launch": True, "error": None}

    async def quit_after_stream(self, uuid: str) -> dict[str, Any]:
        """Make sure the host session ends after a stream, when the user wants that.

        Moonlight's ``--quit-after`` covers a normal exit, but when Steam kills
        Moonlight (End stream, Exit game) the app can stay open on the host.
        Give Moonlight a few seconds first, then close whatever is still open.
        """
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "launch": False, "error": "Unknown host"}
        stream = await asyncio.to_thread(effective_stream, self.settings.data, uuid)
        if not stream.get("quitAppAfter"):
            return {"ok": True, "launch": False, "error": None}

        for _ in range(3):
            await asyncio.sleep(2)
            status = await network.probe_any(self._host_addresses(host))
            if not status or not status.get("busy"):
                return {"ok": True, "launch": False, "error": None}
        log.info("An app is still open on %s after the stream, quitting it", host["name"])
        return await self.prepare_quit(uuid)

    async def stop_stream(self) -> None:
        await moonlight.kill_running(decky.DECKY_USER_HOME)

    async def discover_hosts(self) -> dict[str, Any]:
        """PCs running Sunshine on the local network, for Add host. Marks the ones already added."""
        local = [
            entry for entry in await diagnostics.local_ipv4(decky.DECKY_USER_HOME)
            if not entry["interface"].startswith(diagnostics.VIRTUAL_IFACES)
        ]
        ts = await tailscale.status(decky.DECKY_USER_HOME)
        ts_peers = [p for p in ts["peers"] if p["online"]] if ts["running"] else []
        found, remote = await asyncio.gather(
            network.discover(local[:2]),
            asyncio.gather(*(network.probe(p["address"], 47989, timeout=1.5) for p in ts_peers)),
        )
        lan_ids = {info.get("uniqueId") for info in found}
        for info in remote:
            # A PC on the LAN is listed once, by its LAN address.
            if info and (not info.get("uniqueId") or info.get("uniqueId") not in lan_ids):
                found.append({**info, "via": "tailscale"})
        hosts = self._hosts()
        known = {host["uuid"] for host in hosts}
        addresses_of = {host["uuid"]: {a["address"] for a in self._host_addresses(host)} for host in hosts}
        own_ids = set()
        if self.settings.data["features"].get("sunshine"):
            web_port = int(self.settings.data["sunshine"].get("port") or 47990)
            own = await network.probe("127.0.0.1", web_port - 1, timeout=0.5)
            if own and own.get("uniqueId"):
                own_ids.add(own["uniqueId"])
        items = [
            {
                "address": info["address"],
                "name": info.get("hostname") or info["address"],
                "known": info.get("uniqueId") in known,
                "uuid": info.get("uniqueId") if info.get("uniqueId") in known else None,
                # Known, but not at this address yet: it can be saved as another way to reach it.
                "newAddress": info.get("uniqueId") in known and info["address"] not in addresses_of.get(info.get("uniqueId"), set()),
                "via": info.get("via", "lan"),
            }
            for info in found
            if info.get("uniqueId") not in own_ids
        ]
        return {
            "ok": True,
            "error": None,
            "items": items,
            "scanned": bool(local),
            "tailscale": "running" if ts["running"] else "stopped" if ts["installed"] else "missing",
        }

    async def add_host_address(self, uuid: str, address: str) -> dict[str, Any]:
        """Save another address of a paired host (e.g. its Tailscale address) to reach it away from home."""
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        info = await network.probe(str(address), DEFAULT_HTTP_PORT, timeout=2.0)
        if not info or info.get("uniqueId") != uuid:
            return {"ok": False, "error": f"{address} is not {host['name']}, or it does not answer."}
        extra = [a for a in self._host_cfg(uuid).get("extraAddresses") or [] if a != address]
        self.settings.update({"hosts": {uuid: {"extraAddresses": [*extra, str(address)][-4:]}}})
        self._probe_at = float("-inf")
        return {"ok": True, "error": None, "settings": self.settings.public()}

    async def host_network_test(self, uuid: str) -> dict[str, Any]:
        """Latency, jitter and the link to a host, with the bitrate it suits."""
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        probe = await network.probe_any(self._host_addresses(host))
        if not probe:
            known = ", ".join(a["address"] for a in self._host_addresses(host)) or "none"
            return {
                "ok": False,
                "error": f"{host['name']} does not answer at its known addresses ({known}). At home: wake it or check "
                "that Sunshine runs. Away from home: add its Tailscale address in Add a host.",
            }
        address = probe.get("address") or self._best_address(host)
        port = int(probe.get("port") or 47989)
        result = await nettest.run(address, port, decky.DECKY_USER_HOME)
        return {"ok": True, "error": None, **result}

    async def detect_host_displays(self, uuid: str) -> dict[str, Any]:
        """List the host's displays from its Sunshine log (needs credentials)."""
        host = self._find_host(uuid)
        api = self._sunshine_api_for(host) if host else None
        if not api:
            return {"ok": False, "displays": [], "error": "Set the host's Sunshine credentials first"}
        return await asyncio.to_thread(host_display.detect_displays, api)

    async def refresh_host_monitors(self, uuid: str, rescan: bool = False) -> dict[str, Any]:
        """Re-read the host's monitors and replace the saved list with them.

        Names the user gave and manually added monitors are kept. Monitors
        that are gone are dropped, and so is a selection pointing at one.
        ``rescan`` restarts Sunshine first so it enumerates monitors again.
        """
        host = self._find_host(uuid)
        api = self._sunshine_api_for(host) if host else None
        if not host or not api:
            return {"ok": False, "error": "Set the host's Sunshine login first", "settings": self.settings.public()}

        if rescan:
            found = await asyncio.to_thread(host_display.rescan_displays, api, host["addresses"])
        else:
            found = await asyncio.to_thread(host_display.detect_displays, api)
        current = await asyncio.to_thread(host_display.current_output, api)
        if not found["ok"]:
            return {**found, "current": current.get("current"), "settings": self.settings.public()}

        cfg = self._host_cfg(uuid)
        saved = {d["outputName"]: d for d in cfg.get("displays") or []}
        detected = [
            {**d, "label": saved[d["outputName"]]["label"]} if d["outputName"] in saved else d
            for d in found["displays"]
        ]
        names = {d["outputName"] for d in detected}
        manual = [d for d in saved.values() if d.get("manual") and d["outputName"] not in names]
        selection_lost = bool(cfg.get("display")) and cfg["display"] not in names | {d["outputName"] for d in manual}

        patch: dict[str, Any] = {"displays": detected + manual}
        if selection_lost:
            patch["display"] = None
        self.settings.update({"hosts": {uuid: patch}})
        return {
            "ok": True,
            "error": None,
            "detectedAt": found.get("detectedAt"),
            "current": current.get("current"),
            "selectionLost": selection_lost,
            "settings": self.settings.public(),
        }

    async def host_current_display(self, uuid: str) -> dict[str, Any]:
        """Which display the host's Sunshine captures right now (needs credentials)."""
        host = self._find_host(uuid)
        api = self._sunshine_api_for(host) if host else None
        if not api:
            return {"ok": False, "current": None, "error": "Set the host's Sunshine credentials first"}
        return await asyncio.to_thread(host_display.current_output, api)

    async def host_diagnostics(self, uuid: str) -> dict[str, Any]:
        """Checks explaining why streaming from a host fails."""
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host", "checks": []}
        api = self._sunshine_api_for(host)
        info, local, resolved = await asyncio.gather(
            network.probe_any(self._host_addresses(host), timeout=2.0),
            diagnostics.local_ipv4(decky.DECKY_USER_HOME),
            self._resolve_moonlight(force=True),
        )
        if info and info.get("mac"):
            # Same as a regular probe: remember the MAC for Wake-on-LAN.
            self._probe_cache[uuid] = info
        login = (await api.test()) if api and info else None
        checks = diagnostics.host_checks(
            host, info, bool(resolved.get("installed")), local, self._host_mac(host), login if api else None,
        )
        if api and not info:
            # Cannot be checked while the PC does not answer.
            for check in checks:
                if check["id"] == "login":
                    check.update({"label": "The PC's Sunshine login works", "ok": None, "hint": "Checked once the PC answers."})
        return {"ok": True, "error": None, "checks": checks}

    async def test_host_credentials(self, uuid: str) -> dict[str, Any]:
        host = self._find_host(uuid)
        api = self._sunshine_api_for(host) if host else None
        if not api:
            return {"ok": False, "error": "No Sunshine credentials configured"}
        return await api.test()

    # ------------------------------------------------------------------ library shortcuts

    async def add_library_entry(self, uuid: str, app_name: str) -> dict[str, Any]:
        host = self._find_host(uuid)
        if not host:
            return {"ok": False, "error": "Unknown host"}
        library = self.settings.data["shortcuts"]["library"]
        for entry_id, entry in library.items():
            if entry["hostUuid"] == uuid and entry["appName"] == app_name and entry.get("steamAppId"):
                return {"ok": True, "id": entry_id, "existingAppId": entry["steamAppId"], "error": None}
        entry_id = secrets.token_hex(4)
        self.settings.update({"shortcuts": {"library": {entry_id: {
            "hostUuid": uuid, "hostName": host["name"], "appName": app_name, "steamAppId": 0,
        }}}})
        app = next((a for a in host["apps"] if a["name"] == app_name), None)
        return {
            "ok": True,
            "id": entry_id,
            "existingAppId": 0,
            "name": f"{app_name} ({host['name']})",
            "launchOptions": f"\"{os.path.join(decky.DECKY_PLUGIN_DIR, 'bin', 'eclipse_launcher.py')}\" --library {entry_id}",
            "boxartAppId": app["id"] if app else "",
            "error": None,
        }

    async def set_library_app_id(self, entry_id: str, steam_app_id: int) -> None:
        self.settings.update({"shortcuts": {"library": {entry_id: {"steamAppId": int(steam_app_id)}}}})

    async def remove_library_entry(self, entry_id: str) -> None:
        self.settings.update({"shortcuts": {"library": {entry_id: None}}})

    async def get_boxart(self, uuid: str, app_id: str) -> Optional[str]:
        path = find_boxart(decky.DECKY_USER_HOME, uuid, str(app_id))
        if not path or os.path.getsize(path) > MAX_BOXART_BYTES:
            return None

        def read() -> str:
            with open(path, "rb") as fh:
                return base64.b64encode(fh.read()).decode()

        return await asyncio.to_thread(read)

    # ------------------------------------------------------------------ install

    async def _install(self, target: str, job: Any, operation: str = "install", cancellable: bool = True) -> dict[str, Any]:
        """Run an install/update/uninstall job with throttled progress events.

        One operation runs at a time; it is cancellable via :meth:`cancel_install`.
        """
        if self._installing:
            return {"ok": False, "error": "Another installation or update is running", "warning": None}
        self._installing = target
        self._operation = operation
        self._cancel_requested = False
        self._cancellable = cancellable
        loop = asyncio.get_running_loop()
        last_emit = float("-inf")  # monotonic() has an arbitrary origin
        # Newest line not sent yet because of the throttle, and the timer that sends it.
        pending: Optional[str] = None
        flush_timer: Optional[asyncio.TimerHandle] = None

        def emit(line: str) -> None:
            nonlocal last_emit
            last_emit = time.monotonic()
            loop.create_task(self._emit("eclipse_install_progress", target, line[:160]))

        def flush() -> None:
            nonlocal pending, flush_timer
            flush_timer = None
            if pending is not None:
                emit(pending)
                pending = None

        def on_line(line: str) -> None:
            nonlocal pending, flush_timer
            line = _clean_progress(line)
            if not line:
                return
            log.info("[%s %s] %s", operation, target, line)
            # Throttle progress events, flatpak can print many lines per second.
            # A throttled line is sent later, so the UI always ends on the newest one.
            wait = PROGRESS_INTERVAL_S - (time.monotonic() - last_emit)
            if wait <= 0:
                pending = None
                emit(line)
            else:
                pending = line
                if flush_timer is None:
                    flush_timer = loop.call_later(wait, flush)

        self._install_task = asyncio.ensure_future(job(on_line))
        try:
            result = await self._install_task
        except asyncio.CancelledError:
            if not self._cancel_requested:
                raise  # The backend itself is shutting down.
            log.info("[%s %s] cancelled by the user", operation, target)
            result = {"ok": False, "cancelled": True, "error": f"{operation.capitalize()} cancelled", "warning": None}
        finally:
            self._installing = None
            self._operation = None
            self._install_task = None
            # The final status below must not be overtaken by a delayed line.
            if flush_timer is not None:
                flush_timer.cancel()
        if target == "moonlight":
            await self._resolve_moonlight(force=True)
        else:
            self.local_sunshine.invalidate()
        status = "Done" if result["ok"] else ("Cancelled" if result.get("cancelled") else f"{operation.capitalize()} failed")
        await self._emit("eclipse_install_progress", target, status)
        return result

    async def cancel_install(self) -> dict[str, Any]:
        """Stop the running installation (kills the flatpak process group)."""
        if not self._install_task:
            return {"ok": False, "error": "No installation is running"}
        if not self._cancellable:
            return {"ok": False, "error": "Sunshine for Gaming Mode is installed as root; this step cannot be cancelled"}
        self._cancel_requested = True
        self._install_task.cancel()
        return {"ok": True, "error": None}

    async def install_moonlight(self) -> dict[str, Any]:
        async def job(on_line: Any) -> dict[str, Any]:
            ok = await moonlight.install_flatpak(on_line, decky.DECKY_USER_HOME)
            ok = ok and (await self._resolve_moonlight(force=True))["installed"]
            return {"ok": ok, "error": None if ok else "Installation failed, see plugin logs", "warning": None}

        return await self._install("moonlight", job)

    async def _install_native_sunshine(self, password: str, operation: str, repair: bool = False) -> dict[str, Any]:
        """Install, update or repair Sunshine for Gaming Mode (needs the sudo password)."""

        async def job(on_line: Any) -> dict[str, Any]:
            if repair:
                result = await self.local_sunshine.repair(password, on_line)
            else:
                result = await self.local_sunshine.install(password, on_line)
            if result["ok"]:
                # The service is only enabled when the user turned on autostart.
                await self.local_sunshine.apply_autostart(bool(self.settings.data["sunshine"].get("autostart")))
            return result

        return await self._install("sunshine", job, operation, cancellable=False)

    async def install_sunshine(self, password: str) -> dict[str, Any]:
        return await self._install_native_sunshine(password, "install")

    async def repair_sunshine(self, password: str) -> dict[str, Any]:
        """Reinstall the installed Sunshine version (capability lost, or helpers updated with the plugin)."""
        return await self._install_native_sunshine(password, "install", repair=True)

    async def uninstall_sunshine_flatpak(self, delete_data: bool) -> dict[str, Any]:
        """Remove a Flatpak Sunshine left next to Sunshine for Gaming Mode."""
        info = await self._app_info("sunshine")
        if not info:
            return {"ok": False, "error": "The Sunshine Flatpak is not installed", "warning": None}

        async def job(on_line: Any) -> dict[str, Any]:
            return await self.local_sunshine.uninstall_flatpak(info["installation"], on_line, bool(delete_data))

        return await self._install("sunshine", job, "uninstall")

    # ------------------------------------------------------------------ app management

    async def _app_info(self, feature: str) -> Optional[dict[str, Any]]:
        app_id = FLATPAK_IDS.get(feature)
        return await flatpak.app_info(app_id, decky.DECKY_USER_HOME) if app_id else None

    async def apps_status(self) -> dict[str, Any]:
        """Install state of Moonlight and Sunshine, and whether Eclipse can manage them.

        Only Flatpak installs are managed. Native binaries or a custom command
        are shown as "managed outside Eclipse".
        """
        moonlight_info, sunshine_info = await asyncio.gather(self._app_info("moonlight"), self._app_info("sunshine"))
        resolved = await self._resolve_moonlight()
        sunshine_detected = await self.local_sunshine.detect()
        native_state = self.local_sunshine.native_state()

        def entry(info: Optional[dict[str, Any]], installed_elsewhere: bool) -> dict[str, Any]:
            if info:
                return {"installed": True, "managed": True, **info}
            return {"installed": installed_elsewhere, "managed": False, "version": None, "installation": None}

        if native_state["installed"]:
            sunshine_entry = {
                "installed": True, "managed": True, "version": native_state["version"],
                "installation": "eclipse", "alsoFlatpak": sunshine_info is not None,
            }
        else:
            sunshine_entry = entry(sunshine_info, sunshine_detected["installed"])
        return {
            "moonlight": entry(moonlight_info, resolved["installed"] and resolved["source"] != "flatpak"),
            "sunshine": sunshine_entry,
            "operation": self._operation,
            "busy": self._installing,
        }

    async def check_app_update(self, feature: str) -> dict[str, Any]:
        if feature == "sunshine" and self.local_sunshine.native_state()["installed"]:
            return await self._check_native_sunshine_update()
        info = await self._app_info(feature)
        if not info:
            return {"ok": False, "available": False, "version": None, "error": "Not installed as a Flatpak"}
        return await flatpak.check_update(FLATPAK_IDS[feature], info["installation"], decky.DECKY_USER_HOME)

    async def _check_native_sunshine_update(self) -> dict[str, Any]:
        current = self.local_sunshine.native_state()["version"] or "0"
        try:
            release = await asyncio.to_thread(updates.fetch_json, sunshine_native.release_api_url(None))
        except (OSError, ValueError) as exc:
            return {"ok": False, "available": False, "version": None, "error": updates.describe_error(exc)}
        asset = sunshine_native.pick_deb_asset(release if isinstance(release, dict) else {})
        if not asset:
            return {"ok": False, "available": False, "version": None, "error": "The latest Sunshine release has no Ubuntu 22.04 build"}
        return {"ok": True, "available": updates.is_newer(asset["version"], current), "version": asset["version"], "error": None}

    async def update_app(self, feature: str, password: Optional[str] = None) -> dict[str, Any]:
        if feature == "sunshine" and self.local_sunshine.native_state()["installed"]:
            return await self._install_native_sunshine(password or "", "update")
        info = await self._app_info(feature)
        if not info:
            return {"ok": False, "error": "Not installed as a Flatpak", "warning": None}

        async def job(on_line: Any) -> dict[str, Any]:
            on_line(f"Updating {FLATPAK_IDS[feature]}...")
            ok = await flatpak.update(FLATPAK_IDS[feature], info["installation"], on_line, decky.DECKY_USER_HOME)
            warning = None
            if not ok and info["installation"] != "user":
                warning = "System-wide installs may need Desktop Mode (Discover) to update."
            return {"ok": ok, "error": None if ok else "Update failed, see the plugin log", "warning": warning}

        return await self._install(feature, job, "update")

    async def uninstall_app(self, feature: str, delete_data: bool, password: Optional[str] = None) -> dict[str, Any]:
        if feature == "sunshine" and self.local_sunshine.native_state()["installed"]:

            async def native_job(on_line: Any) -> dict[str, Any]:
                return await self.local_sunshine.uninstall(password or "", on_line, bool(delete_data))

            return await self._install("sunshine", native_job, "uninstall", cancellable=False)
        info = await self._app_info(feature)
        if not info:
            return {"ok": False, "error": "Not installed as a Flatpak", "warning": None}

        async def job(on_line: Any) -> dict[str, Any]:
            if feature == "sunshine":
                return await self.local_sunshine.uninstall_flatpak(info["installation"], on_line, bool(delete_data))
            await moonlight.kill_running(decky.DECKY_USER_HOME)
            ok = await flatpak.uninstall(FLATPAK_MOONLIGHT, info["installation"], on_line, bool(delete_data), decky.DECKY_USER_HOME)
            return {"ok": ok, "error": None if ok else "Uninstall failed, see the plugin log", "warning": None}

        return await self._install(feature, job, "uninstall")

    # ------------------------------------------------------------------ SteamOS password

    def _passwd_cmd(self) -> list[str]:
        return [proc.which("passwd", decky.DECKY_USER_HOME) or "/usr/bin/passwd"]

    async def sudo_password_state(self) -> dict[str, Any]:
        """Whether this device's user has a password, which sudo needs: "set", "none" or "unknown"."""
        # Imported on use: a module missing from Decky's Python must not stop the plugin.
        from eclipse import account

        env = proc.clean_env(decky.DECKY_USER_HOME)
        state = await asyncio.to_thread(account.password_state, self._passwd_cmd(), env)
        return {"state": state}

    async def create_sudo_password(self, password: str) -> dict[str, Any]:
        """Set the first password of this device's user (only when none is set yet)."""
        from eclipse import account

        env = proc.clean_env(decky.DECKY_USER_HOME)
        error = await asyncio.to_thread(account.set_first_password, password or "", self._passwd_cmd(), env)
        if error:
            log.info("Setting the SteamOS password failed: %s", error)
        else:
            log.info("SteamOS password set")
        return {"ok": error is None, "error": error}

    # ------------------------------------------------------------------ plugin updates

    async def check_plugin_update(self) -> dict[str, Any]:
        result = await asyncio.to_thread(updates.check, decky.DECKY_PLUGIN_DIR)
        self.settings.update({"updates": {"lastCheck": int(time.time())}})
        return result

    # ------------------------------------------------------------------ local Sunshine

    def _local_api(self) -> Optional[SunshineApi]:
        cfg = self.settings.data["sunshine"]
        if not cfg.get("user") or not cfg.get("password"):
            return None
        return SunshineApi("127.0.0.1", cfg["user"], cfg["password"], int(cfg.get("port") or 0))

    async def _autostart_sunshine(self) -> None:
        # Let the session (PipeWire, gamescope) settle after boot.
        await asyncio.sleep(5)
        status = await self.local_sunshine.status()
        if status["installed"] and not status["running"]:
            result = await self.local_sunshine.start()
            log.info("Sunshine autostart: %s", "started" if result["ok"] else result["error"])

    async def _session_state(self) -> dict[str, Any]:
        """Streaming sessions of the Sunshine on this device (see sunshine_session.py)."""
        async with self._session_lock:
            # Sunshine's HTTP (pairing/serverinfo) port is the web UI port minus one.
            web_port = int(self.settings.data["sunshine"].get("port") or 47990)
            server = await network.probe("127.0.0.1", web_port - 1, timeout=1.5)
            if server:
                self._session_misses = 0
            else:
                self._session_misses += 1
                # A busy Sunshine can miss one probe; that is not the end of a stream.
                if self._session_last and self._session_misses < SESSION_MISSES_OFFLINE:
                    return self._session_last
            log_path = diagnostics.sunshine_log_path(decky.DECKY_USER_HOME)
            state = await asyncio.to_thread(self._sessions.update, server, log_path)
            if server:
                own = frozenset(a["address"] for a in await self._lan_addresses())
                peers = await asyncio.to_thread(read_tcp_peers, client_tcp_ports(web_port - 1), own)
            else:
                peers = set()
            addresses = self._peers.update(peers, state["clients"], time.monotonic())
            state["devices"] = [{"address": a, "name": self._peer_name(a)} for a in addresses]
            self._session_last = state
            return state

    def _peer_name(self, address: str) -> Optional[str]:
        """Name of a device from reverse DNS; looked up in the background, None until known."""
        now = time.monotonic()
        cached = self._peer_names.get(address)
        if cached and now - cached[0] < PEER_NAME_CACHE_S:
            return cached[1]
        # Marks the lookup as running, so it is started once.
        self._peer_names[address] = (now, cached[1] if cached else None)
        if len(self._peer_names) > 64:
            oldest = min(self._peer_names, key=lambda a: self._peer_names[a][0])
            self._peer_names.pop(oldest, None)
        self._spawn(self._lookup_peer_name(address))
        return cached[1] if cached else None

    async def _lookup_peer_name(self, address: str) -> None:
        try:
            host, _, _ = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, address), PEER_NAME_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError):
            return
        # "Phone.lan" -> "Phone"; an address echoed back is no name.
        name = host.split(".")[0] if host and not host.replace(".", "").isdigit() else None
        self._peer_names[address] = (time.monotonic(), name)

    async def _watch_sunshine_sessions(self) -> None:
        """Tell the frontend when a device starts or stops streaming from here, even with the UI closed."""
        last: Optional[tuple[Any, ...]] = None
        while True:
            enabled = bool(self.settings.data["features"].get("sunshine"))
            try:
                state = await self._session_state() if enabled else None
                key = (
                    (state["clients"], state["appOpen"], state["appName"], state["reachable"],
                     tuple((d["address"], d["name"]) for d in state.get("devices", [])))
                    if state else None
                )
                if key != last:
                    last = key
                    if state:
                        await self._emit(SESSION_EVENT, state)
                if state:
                    await self._apply_auto_off(state)
                    await self._apply_battery_guard(state)
                    if state["reachable"]:
                        await self._drop_duplicate_clients()
                        await self._announce_pairings()
                    else:
                        # A restarted (maybe updated) Sunshine is asked again.
                        self._pairings_supported = None
                        self._pairings_seen.clear()
                await self._sync_mdns(state)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the watcher must keep running
                log.exception("Sunshine session check failed")
            await asyncio.sleep(SESSION_POLL_S if enabled else SESSION_IDLE_POLL_S)

    async def _drop_duplicate_clients(self) -> None:
        """Sunshine 2026.914 keeps the old record when a device pairs again, and then rejects
        that device (Error 401). Remove the older records as soon as the state file changes."""
        path = sunshine_data.state_path(decky.DECKY_USER_HOME)
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return
        if mtime == self._state_checked:
            return
        api = self._local_api()
        if not api:
            return  # Retried once a login is saved; fixed at the next start meanwhile.
        state = await asyncio.to_thread(sunshine_data.read_state, path)
        duplicates = sunshine_data.duplicate_clients(state) if state else []
        failed = False
        for uuid in duplicates:
            result = await api.unpair(uuid)
            if result["ok"]:
                log.info("Removed a duplicate paired-device record (%s)", uuid)
            else:
                failed = True
                log.warning("Could not remove a duplicate paired-device record: %s", result["error"])
        if not failed:
            # Unpairing rewrites the file: that new mtime is checked once more, finding nothing.
            self._state_checked = mtime

    async def _apply_auto_off(self, state: dict[str, Any]) -> None:
        option = str(self.settings.data["sunshine"].get("autoOff") or "never")
        reason = self._auto_off.update(state, option, time.monotonic())
        # Never in the middle of an install or update of Sunshine.
        if not reason or self._installing:
            return
        log.info("Turning Sunshine off: %s", reason)
        self._auto_off.reset()
        result = await self.local_sunshine.stop()
        if result["ok"]:
            await self._emit(AUTO_OFF_EVENT, f"Sunshine turned off: {reason}.")

    async def _announce_pairings(self) -> None:
        """Tell the frontend about devices that just asked to pair, even with the UI closed."""
        if self._pairings_supported is False or not self._local_api():
            return
        result = await self.sunshine_pending_pairings()
        if not result.get("ok"):
            return
        self._pairings_supported = bool(result.get("supported"))
        items = result.get("items") or []
        waiting = {item["id"] for item in items}
        for item in items:
            if item["id"] not in self._pairings_seen:
                await self._emit(PAIRING_EVENT, item)
        # Forget finished requests, so a device asking again is announced again.
        self._pairings_seen = waiting

    async def _apply_battery_guard(self, state: dict[str, Any]) -> None:
        option = str(self.settings.data["sunshine"].get("batteryGuard") or "off")
        level = int(option) if option.isdigit() else 0
        if not state["reachable"]:
            self._battery_guard.reset()
            return
        if self._installing:
            return
        info = await asyncio.to_thread(battery.read)
        action = self._battery_guard.update(level, info, state["clients"])
        if not action or not info:
            return
        percent = info["percent"]
        if action == "warn":
            await self._emit(AUTO_OFF_EVENT, f"Battery at {percent}%: Sunshine turns off when this stream ends, or at {battery.FLOOR_PERCENT}%.")
            return
        log.info("Turning Sunshine off: battery at %s%%", percent)
        self._auto_off.reset()
        result = await self.local_sunshine.stop()
        if result["ok"]:
            await self._emit(AUTO_OFF_EVENT, f"Sunshine turned off: battery at {percent}%.")

    async def _lan_addresses(self) -> list[dict[str, str]]:
        """Physical LAN IPv4 addresses of this device (cached briefly)."""
        at, cached = self._addresses
        if time.monotonic() - at < ADDRESSES_CACHE_S:
            return cached
        found = [
            entry for entry in await diagnostics.local_ipv4(decky.DECKY_USER_HOME)
            if not entry["interface"].startswith(diagnostics.VIRTUAL_IFACES)
        ]
        self._addresses = (time.monotonic(), found)
        return found

    async def _sunshine_publish_failed(self) -> bool:
        at, failed = self._mdns_failed
        if time.monotonic() - at < MDNS_CHECK_S:
            return failed
        path = diagnostics.sunshine_log_path(decky.DECKY_USER_HOME)
        # Sunshine rewrites its log at every start and publishes right away: the start of the file tells.
        text = await asyncio.to_thread(_head, path, 256 * 1024) if path else ""
        failed = mdns.publish_failed(text)
        self._mdns_failed = (time.monotonic(), failed)
        return failed

    async def _mdns_service(self) -> Optional[mdns.Service]:
        addresses = tuple(entry["address"] for entry in await self._lan_addresses())
        if not addresses:
            return None
        try:
            options = await self.local_sunshine.read_options()
        except OSError:
            options = {}
        hostname = socket.gethostname()
        web_port = int(self.settings.data["sunshine"].get("port") or 47990)
        return mdns.Service(
            instance=mdns.instance_label(str(options.get("sunshine_name") or hostname)),
            # A name of its own: avahi may already publish the plain host name.
            host=mdns.dns_label(f"{hostname}-sunshine"),
            port=web_port - 1,
            addresses=addresses,
        )

    async def _sync_mdns(self, state: Optional[dict[str, Any]]) -> None:
        """Announce Sunshine over mDNS while it runs and could not do so itself."""
        wanted = (
            bool(state and state["reachable"])
            and bool(self.settings.data["sunshine"].get("announce", True))
            and await self._sunshine_publish_failed()
        )
        service = await self._mdns_service() if wanted else None
        if not wanted:
            self._mdns_failed_start = False
        if self._mdns and self._mdns.service != service:
            await self._mdns.stop()
            self._mdns = None
        if not service or self._mdns or time.monotonic() < self._mdns_retry_at:
            return
        responder = mdns.Responder(service)
        try:
            service.records()  # A name that cannot be encoded fails here, not on every query.
            await responder.start()
        except (OSError, ValueError) as exc:
            log.warning("mDNS: cannot announce Sunshine: %s", exc)
            self._mdns_retry_at = time.monotonic() + MDNS_RETRY_S
            self._mdns_failed_start = True
            return
        self._mdns = responder
        self._mdns_failed_start = False

    async def sunshine_session(self) -> dict[str, Any]:
        return await self._session_state()

    async def sunshine_end_session(self, restart: bool = False) -> dict[str, Any]:
        """End every stream from this device.

        Without ``restart``, the open app is closed through Sunshine's API with
        the saved login, which disconnects its clients. When that is not
        possible, ``needsRestart`` asks the user to allow a restart instead.
        With ``restart``, Sunshine is restarted, which drops every connection.
        """
        if not restart:
            api = self._local_api()
            if not api:
                return {"ok": False, "error": None, "method": "closed", "needsRestart": True}
            result = await api.close_app()
            if result["ok"]:
                return {"ok": True, "error": None, "method": "closed"}
            log.info("Closing the Sunshine app failed: %s", result["error"])
            return {"ok": False, "error": result["error"], "method": "closed", "needsRestart": True}
        result = await self.local_sunshine.restart()
        return {"ok": result["ok"], "error": result["error"], "method": "restarted"}

    async def sunshine_overview(self) -> dict[str, Any]:
        """What the Quick Access menu shows about sharing: this device's address and paired devices."""
        addresses = await diagnostics.local_ipv4(decky.DECKY_USER_HOME)
        api = self._local_api()
        paired: Optional[int] = None
        if api:
            clients = await api.clients()
            if clients["ok"]:
                paired = len(clients["items"])
        ts = await tailscale.status(decky.DECKY_USER_HOME)
        return {
            "address": addresses[0]["address"] if addresses else None,
            "paired": paired,
            "remoteAddress": ts["self"],
            "announced": self._mdns is not None,
            "announce": await self._announce_state(),
        }

    async def _announce_state(self) -> str:
        """How other devices find this one: "active" (Eclipse announces it), "notNeeded" (Sunshine
        does it), "failed" (Eclipse could not either), "off" (turned off) or "unknown"."""
        if self._mdns is not None:
            return "active"
        if not self.settings.data["sunshine"].get("announce", True):
            return "off"
        if not (self._session_last or {}).get("reachable"):
            return "unknown"
        path = diagnostics.sunshine_log_path(decky.DECKY_USER_HOME)
        text = await asyncio.to_thread(_head, path, 256 * 1024) if path else ""
        if not mdns.publish_failed(text):
            # Sunshine logs its own announcement once it is set up.
            return "notNeeded" if "successfully established" in text else "unknown"
        return "failed" if self._mdns_failed_start else "unknown"

    async def sunshine_status(self) -> dict[str, Any]:
        status = await self.local_sunshine.status()
        status["credentials"] = self._local_api() is not None
        status["autostart"] = bool(self.settings.data["sunshine"].get("autostart"))
        return status

    async def sunshine_start(self) -> dict[str, Any]:
        return await self.local_sunshine.start()

    async def sunshine_stop(self) -> dict[str, Any]:
        return await self.local_sunshine.stop()

    async def _options_result(self, values: dict[str, Any]) -> dict[str, Any]:
        status = await self.local_sunshine.status()
        return {
            "ok": True,
            "error": None,
            "values": values,
            "defaults": sunshine_config.defaults(),
            "running": status["running"],
            # Only a Sunshine that has been running since the change still uses the old options.
            "needsRestart": status["running"] and self.local_sunshine.config_pending,
        }

    async def sunshine_get_options(self) -> dict[str, Any]:
        """Options of the Sunshine on this device (sunshine.conf), with Sunshine's defaults."""
        try:
            values = await self.local_sunshine.read_options()
        except OSError as exc:
            return {"ok": False, "error": f"Could not read Sunshine's config: {exc}"}
        return await self._options_result(values)

    async def sunshine_set_options(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Change options of the Sunshine on this device. They apply when Sunshine (re)starts."""
        if not isinstance(patch, dict) or not patch:
            return {"ok": False, "error": "Nothing to change"}
        status = await self.local_sunshine.status()
        if (status.get("native") or {}).get("legacy"):
            # That build reads its config from the legacy folder; a repair moves it.
            return {"ok": False, "error": "Repair Sunshine first: this copy keeps its config in the wrong folder."}
        try:
            values = await self.local_sunshine.write_options(patch, status["running"])
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"Could not save Sunshine's config: {exc}"}
        return await self._options_result(values)

    async def sunshine_restart(self) -> dict[str, Any]:
        """Restart Sunshine so changed options apply. Ends every stream from this device."""
        return await self.local_sunshine.restart()

    # ------------------------------------------------------------------ Steam games in Sunshine

    async def _editable_sunshine(self) -> tuple[Optional[str], dict[str, Any]]:
        """Sunshine's config dir and status, or an error text when its files cannot be changed now."""
        status = await self.local_sunshine.status()
        if not status["installed"]:
            return "Sunshine is not installed", status
        if (status.get("native") or {}).get("legacy"):
            return "Repair Sunshine first: this copy keeps its config in the wrong folder.", status
        return None, status

    async def sunshine_games(self) -> dict[str, Any]:
        """Installed Steam games and the apps Sunshine offers to other devices."""
        error, _ = await self._editable_sunshine()
        if error:
            return {"ok": False, "error": error, "games": [], "apps": []}
        config_dir = await self.local_sunshine.config_dir()
        try:
            games, apps = await asyncio.gather(
                asyncio.to_thread(sunshine_apps.installed_games, decky.DECKY_USER_HOME),
                asyncio.to_thread(sunshine_apps.list_apps, config_dir),
            )
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "games": [], "apps": []}
        return {"ok": True, "error": None, "games": games, "apps": apps,
                "needsRestart": self.local_sunshine.config_pending}

    async def sunshine_game_cover(self, app_id: int) -> dict[str, Any]:
        """The cover of a Steam game as Steam caches it (usually JPEG), for the frontend to convert."""
        path = await asyncio.to_thread(sunshine_apps.cover_source, decky.DECKY_USER_HOME, int(app_id))
        if not path:
            return {"ok": False, "error": "No cover in Steam's cache"}
        try:
            return {"ok": True, "error": None, **await asyncio.to_thread(sunshine_apps.read_cover, path)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    async def sunshine_save_game_cover(self, app_id: int, png_base64: str) -> dict[str, Any]:
        config_dir = await self.local_sunshine.config_dir()
        try:
            await asyncio.to_thread(sunshine_apps.save_cover, config_dir, int(app_id), png_base64)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"Could not save the cover: {exc}"}
        return {"ok": True, "error": None}

    async def sunshine_add_games(self, games: list[dict[str, Any]]) -> dict[str, Any]:
        """Offer these Steam games (``{"appId", "name"}``) to other devices."""
        error, status = await self._editable_sunshine()
        if error:
            return {"ok": False, "error": error}
        config_dir = await self.local_sunshine.config_dir()
        try:
            added = await asyncio.to_thread(
                sunshine_apps.add_games, config_dir, list(games or []), await self.local_sunshine.is_flatpak()
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": f"Could not change Sunshine's app list: {exc}"}
        if added:
            self.local_sunshine.mark_changed(status["running"])
            # The open app's name is matched against apps.json: drop what was cached.
            self._sessions = SessionTracker()
        return {"ok": True, "error": None, "added": added,
                "needsRestart": status["running"] and self.local_sunshine.config_pending}

    async def sunshine_remove_games(self, app_ids: list[int]) -> dict[str, Any]:
        error, status = await self._editable_sunshine()
        if error:
            return {"ok": False, "error": error}
        config_dir = await self.local_sunshine.config_dir()
        try:
            removed = await asyncio.to_thread(sunshine_apps.remove_games, config_dir, [int(i) for i in app_ids or []])
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": f"Could not change Sunshine's app list: {exc}"}
        if removed:
            self.local_sunshine.mark_changed(status["running"])
            self._sessions = SessionTracker()
        return {"ok": True, "error": None, "removed": removed,
                "needsRestart": status["running"] and self.local_sunshine.config_pending}

    # ------------------------------------------------------------------ backup

    async def backup_list(self) -> dict[str, Any]:
        home = decky.DECKY_USER_HOME
        items, places = await asyncio.gather(
            asyncio.to_thread(backup.list_backups, home), asyncio.to_thread(backup.locations, home)
        )
        return {"ok": True, "error": None, "items": items, "locations": places, "folder": backup.backup_dir(home)}

    async def backup_create(self, include_passwords: bool = False, location_id: str = backup.HOME_LOCATION) -> dict[str, Any]:
        options: Optional[dict[str, Any]] = None
        games: list[dict[str, Any]] = []
        status = await self.local_sunshine.status()
        if status["installed"] and not (status.get("native") or {}).get("legacy"):
            try:
                values = await self.local_sunshine.read_options()
                defaults = sunshine_config.defaults()
                options = {k: v for k, v in values.items() if v != defaults.get(k)}
                apps = await asyncio.to_thread(sunshine_apps.list_apps, await self.local_sunshine.config_dir())
                games = [{"appId": a["steamAppId"], "name": a["name"]} for a in apps if a["steamAppId"]]
            except (OSError, ValueError) as exc:
                log.warning("Backup without Sunshine's options: %s", exc)
        data = backup.build(
            self.settings.data, options, games, bool(include_passwords),
            updates.read_package(decky.DECKY_PLUGIN_DIR).get("version"),
        )
        try:
            path = await asyncio.to_thread(backup.write, decky.DECKY_USER_HOME, data, str(location_id))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"Could not write the backup: {exc}"}
        log.info("Backup written to %s", path)
        return {"ok": True, "error": None, "path": path, "name": os.path.basename(path)}

    async def _restore_sunshine_login(self, status: dict[str, Any], warnings: list[str]) -> None:
        """A restored login is only useful if Sunshine has it: a fresh Sunshine gets it created."""
        cfg = self.settings.data["sunshine"]
        if not status["running"] or not cfg.get("user") or not cfg.get("password"):
            return
        if (await self.sunshine_login_state())["state"] != "needs_setup":
            return
        result = await self.sunshine_create_login(cfg["user"], cfg["password"])
        if not result["ok"]:
            warnings.append(f"Sunshine's login was not created: {result['error']}")

    async def backup_delete(self, location_id: str, name: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(backup.delete, decky.DECKY_USER_HOME, str(location_id), str(name))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"Could not delete the backup: {exc}"}
        return {"ok": True, "error": None}

    async def backup_restore(self, location_id: str, name: str) -> dict[str, Any]:
        """Restore settings and Sunshine's options from a backup. The Steam games it lists come
        back in ``games`` (those installed here), for the frontend to add with their covers."""
        try:
            data = await asyncio.to_thread(backup.read, decky.DECKY_USER_HOME, str(location_id), str(name))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self.settings.replace(backup.merge_settings(self.settings.data, data["settings"], DEFAULT_SETTINGS))
        self._moonlight = None
        warnings: list[str] = []
        # The systemd service follows the restored autostart choice.
        await self.local_sunshine.apply_autostart(bool(self.settings.data["sunshine"].get("autostart")))
        games: list[dict[str, Any]] = []
        status = await self.local_sunshine.status()
        sunshine_ready = status["installed"] and not (status.get("native") or {}).get("legacy")
        restored = data.get("sunshineOptions")
        if sunshine_ready and isinstance(restored, dict):
            # Options the backup does not list were at their defaults then.
            patch = {}
            for key, default in sunshine_config.defaults().items():
                try:
                    patch[key] = sunshine_config.validate(key, restored.get(key, default))
                except ValueError:
                    warnings.append(f"Skipped the Sunshine option {key}")
            try:
                await self.local_sunshine.write_options(patch, status["running"])
            except (OSError, ValueError) as exc:
                warnings.append(f"Sunshine's options were not restored: {exc}")
        backup_games = data.get("sunshineGames") or []
        if sunshine_ready:
            installed = {g["appId"] for g in await asyncio.to_thread(sunshine_apps.installed_games, decky.DECKY_USER_HOME)}
            for game in backup_games:
                try:
                    app_id = int(game["appId"])
                except (KeyError, TypeError, ValueError):
                    continue
                if app_id in installed:
                    games.append({"appId": app_id, "name": str(game.get("name") or "")})
            skipped = len(backup_games) - len(games)
            if skipped:
                warnings.append(
                    f"{skipped} game{'s are' if skipped > 1 else ' is'} not installed here: add "
                    f"{'them' if skipped > 1 else 'it'} to Sunshine once installed"
                )
        elif isinstance(restored, dict) or backup_games:
            warnings.append("Install Sunshine, then restore again to also bring back its options and games")
        await self._restore_sunshine_login(status, warnings)
        log.info("Restored settings from %s", name)
        return {"ok": True, "error": None, "games": games, "warnings": warnings,
                "settings": self.settings.public()}

    async def sunshine_set_autostart(self, enabled: bool) -> dict[str, Any]:
        """Persist the autostart option and sync the systemd user service with it."""
        self.settings.update({"sunshine": {"autostart": bool(enabled)}})
        return await self.local_sunshine.apply_autostart(bool(enabled))

    async def sunshine_diagnostics(self) -> dict[str, Any]:
        """Checks explaining why other devices cannot find or reach Sunshine here."""
        status = await self.local_sunshine.status()
        return await diagnostics.run(status, decky.DECKY_USER_HOME, announced=self._mdns is not None)

    async def get_sunshine_log(self) -> str:
        """Tail of Sunshine's own log, falling back to the output of a plugin-started Sunshine."""
        path = diagnostics.sunshine_log_path(decky.DECKY_USER_HOME) or self.local_sunshine.log_path
        return await asyncio.to_thread(_tail, path, LOG_TAIL_LINES)

    async def sunshine_login_state(self) -> dict[str, Any]:
        """State of the web UI login of Sunshine on this device.

        ``state``: "ok" (saved login works), "needs_setup" (Sunshine has no
        login yet), "invalid" (saved login rejected), "missing" (Sunshine has a
        login but none is saved here) or "unreachable" (Sunshine not running).
        """
        cfg = self.settings.data["sunshine"]
        api = SunshineApi("127.0.0.1", cfg.get("user") or "", cfg.get("password") or "", int(cfg.get("port") or 0))
        result = await api.test()
        if result["ok"]:
            state = "ok"
        elif result["needsSetup"]:
            state = "needs_setup"
        elif result["status"] == 401:
            state = "invalid" if self._local_api() else "missing"
        else:
            state = "unreachable"
        return {"state": state, "error": result["error"]}

    async def sunshine_create_login(self, user: str, password: str) -> dict[str, Any]:
        """Create the first login of Sunshine on this device and save it here."""
        user, password = (user or "").strip(), password or ""
        if not user or not password:
            return {"ok": False, "error": "Enter a username and a password"}
        port = int(self.settings.data["sunshine"].get("port") or 0)
        result = await SunshineApi("127.0.0.1", "", "", port).create_login(user, password)
        if result["ok"]:
            self.settings.update({"sunshine": {"user": user, "password": password}})
        return {"ok": result["ok"], "error": result["error"]}

    async def sunshine_create_login_auto(self) -> dict[str, Any]:
        """Create the first Sunshine login with a generated password, so nothing has to be typed.

        The login is returned once, for the user to note for Sunshine's web page.
        """
        user = getattr(decky, "DECKY_USER", None) or "deck"
        # Easy to type on another device: no look-alike characters, in groups.
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        password = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        result = await self.sunshine_create_login(user, password)
        if not result["ok"]:
            return {"ok": False, "error": result["error"], "user": None, "password": None}
        return {"ok": True, "error": None, "user": user, "password": password}

    async def sunshine_reveal_login(self) -> dict[str, Any]:
        """The saved Sunshine login, on the user's explicit request (to open Sunshine's web page)."""
        cfg = self.settings.data["sunshine"]
        if not cfg.get("user") or not cfg.get("password"):
            return {"ok": False, "error": "No Sunshine login saved", "user": None, "password": None}
        return {"ok": True, "error": None, "user": cfg["user"], "password": cfg["password"]}

    async def sunshine_test_login(self) -> dict[str, Any]:
        """Check the saved login against the Sunshine web UI on this device."""
        api = self._local_api()
        if not api:
            return {"ok": False, "error": "No Sunshine login saved"}
        return await api.test()

    async def sunshine_submit_pin(self, pin: str, client_name: str, pairing_id: Optional[str] = None,
                                  address: Optional[str] = None) -> dict[str, Any]:
        """Pair a client with this device's Sunshine. ``paired`` reflects the real outcome.

        Without a name, the device is saved as "Device <address>" rather than Moonlight's placeholder.
        """
        api = self._local_api()
        if not api:
            return {"ok": False, "paired": False, "error": "Set the Sunshine username and password first"}
        pin = "".join(ch for ch in str(pin) if ch.isdigit())
        if len(pin) != 4:
            return {"ok": False, "paired": False, "error": "The PIN must have 4 digits"}
        name = pairing_device_name(client_name) or (f"Device {address}" if address else "Moonlight client")
        return await pair_client(api, pin, name[:64], pairing_id or None)

    async def sunshine_pending_pairings(self) -> dict[str, Any]:
        """Devices waiting for a PIN. Moonlight sends no real device name, so ``name`` is a guess from
        the network (reverse DNS) or empty, and ``label`` is what to show."""
        api = self._local_api()
        if not api:
            return {"ok": False, "supported": False, "items": [], "error": "Set the Sunshine username and password first"}
        result = await api.pending_pairings()
        items = result.get("items") or []
        guesses = await asyncio.gather(
            *(self._resolve_name(item["address"]) if not item["name"] and item["address"] else _none() for item in items)
        )
        for item, guess in zip(items, guesses):
            item["name"] = item["name"] or guess or ""
            item["label"] = item["name"] or (f"A device at {item['address']}" if item["address"] else "A device")
        return result

    async def _resolve_name(self, address: str) -> Optional[str]:
        """Reverse DNS name of a device, waiting briefly (cached like the streaming devices)."""
        cached = self._peer_names.get(address)
        if cached and cached[1]:
            return cached[1]
        await self._lookup_peer_name(address)
        found = self._peer_names.get(address)
        return found[1] if found else None

    async def sunshine_clients(self) -> dict[str, Any]:
        api = self._local_api()
        if not api:
            return {"ok": False, "items": [], "error": "Set the Sunshine username and password first"}
        return await api.clients()

    async def sunshine_unpair(self, uuid: str) -> dict[str, Any]:
        api = self._local_api()
        if not api:
            return {"ok": False, "error": "Set the Sunshine username and password first"}
        return await api.unpair(uuid)

    # ------------------------------------------------------------------ diagnostics

    async def get_plugin_log(self) -> str:
        """Tail of the backend log (install output, API errors...)."""
        path = getattr(decky, "DECKY_PLUGIN_LOG", None)
        if not path or not os.path.isfile(path):
            # Fall back to the newest file in the plugin log directory.
            try:
                entries = [os.path.join(decky.DECKY_PLUGIN_LOG_DIR, f) for f in os.listdir(decky.DECKY_PLUGIN_LOG_DIR)]
                path = max((e for e in entries if os.path.isfile(e)), key=os.path.getmtime)
            except (OSError, ValueError):
                return ""
        return await asyncio.to_thread(_tail, path, LOG_TAIL_LINES)

    async def get_launcher_log(self) -> str:
        path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, LAUNCHER_LOG)
        return await asyncio.to_thread(_tail, path, LOG_TAIL_LINES)


async def _none() -> None:
    return None


def _head(path: str, size: int) -> str:
    """The first ``size`` bytes of a file as text."""
    try:
        with open(path, "rb") as fh:
            return fh.read(size).decode(errors="replace")
    except OSError:
        return ""


def _tail(path: str, lines: int) -> str:
    """Last ``lines`` lines of a file, reading only its end."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 256 * 1024))
            data = fh.read().decode(errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])
