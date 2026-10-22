"""Eclipse - Moonlight & Sunshine control for SteamOS Gaming Mode (Decky backend)."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import sys
import time
from typing import Any, Optional

import decky

# Decky adds py_modules to sys.path for us, but keep this for local tooling.
sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))

from eclipse import LAUNCHER_LOG, SETTINGS_FILE  # noqa: E402
from eclipse import FLATPAK_MOONLIGHT, FLATPAK_SUNSHINE  # noqa: E402
from eclipse import display, flatpak, host_display, launch, moonlight, network, updates  # noqa: E402
from eclipse.log import set_logger  # noqa: E402
from eclipse.moonlight_config import MoonlightConfig, find_boxart  # noqa: E402
from eclipse.presets import AUTO_PRESET, CUSTOM_PRESET, INHERIT_PRESET, all_presets  # noqa: E402
from eclipse.settings import SettingsStore  # noqa: E402
from eclipse.sunshine import LocalSunshine, SunshineApi  # noqa: E402

set_logger(decky.logger)
log = decky.logger

FLATPAK_IDS = {"moonlight": FLATPAK_MOONLIGHT, "sunshine": FLATPAK_SUNSHINE}
PROBE_CACHE_S = 3.0
LOG_TAIL_LINES = 200
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
    _pending_creds: dict[str, dict[str, Any]]
    _tasks: set[asyncio.Task]

    # ------------------------------------------------------------------ lifecycle

    async def _main(self) -> None:
        home = decky.DECKY_USER_HOME
        self.settings = SettingsStore(os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SETTINGS_FILE))
        self.config = MoonlightConfig(home)
        self.local_sunshine = LocalSunshine(home, decky.DECKY_PLUGIN_LOG_DIR)
        self._probe_cache = {}
        self._probe_at = float("-inf")
        self._probe_lock = asyncio.Lock()
        self._moonlight = None
        self._moonlight_at = 0.0
        self._installing = None
        self._operation = None
        self._install_task = None
        self._cancel_requested = False
        self._pending_creds = {}
        self._tasks = set()
        log.info("Eclipse backend started (home=%s)", home)

        features = self.settings.data["features"]
        if features.get("sunshine") and self.settings.data["sunshine"].get("autostart"):
            self._spawn(self._autostart_sunshine())

    async def _unload(self) -> None:
        for task in list(self._tasks):
            task.cancel()
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
            results = await asyncio.gather(*(network.probe_any(h["addresses"]) for h in hosts))
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
            if await network.probe_any(host["addresses"], 2.0):
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
        status = await network.probe_any(host["addresses"])
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
                return {"ok": False, "error": switched["error"]}

        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {
            "action": "stream",
            "host": host["name"],
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
        # Moonlight needs a moment to start and initiate pairing; retry until it does.
        await asyncio.sleep(3)
        result: dict[str, Any] = {"ok": False, "error": "Timed out"}
        for _ in range(10):
            result = await api.submit_pin(pin, name)
            if result["ok"] or result["status"] == 401:
                break
            await asyncio.sleep(2)
        await self._emit("eclipse_pair_result", result["ok"], result.get("error"))

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
        launch.write_request(decky.DECKY_PLUGIN_SETTINGS_DIR, {"action": "quit", "host": host["name"]})
        return {"ok": True, "launch": True, "error": None}

    async def stop_stream(self) -> None:
        await moonlight.kill_running(decky.DECKY_USER_HOME)

    async def detect_host_displays(self, uuid: str) -> dict[str, Any]:
        """List the host's displays from its Sunshine log (needs credentials)."""
        host = self._find_host(uuid)
        api = self._sunshine_api_for(host) if host else None
        if not api:
            return {"ok": False, "displays": [], "error": "Set the host's Sunshine credentials first"}
        return await asyncio.to_thread(host_display.detect_displays, api)

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

    async def _install(self, target: str, job: Any, operation: str = "install") -> dict[str, Any]:
        """Run an install/update/uninstall job with throttled progress events.

        One operation runs at a time; it is cancellable via :meth:`cancel_install`.
        """
        if self._installing:
            return {"ok": False, "error": "Another installation or update is running", "warning": None}
        self._installing = target
        self._operation = operation
        self._cancel_requested = False
        loop = asyncio.get_running_loop()
        last_emit = float("-inf")  # monotonic() has an arbitrary origin

        def on_line(line: str) -> None:
            nonlocal last_emit
            line = _clean_progress(line)
            if not line:
                return
            log.info("[%s %s] %s", operation, target, line)
            # Throttle progress events, flatpak can print many lines per second.
            now = time.monotonic()
            if now - last_emit > 0.5:
                last_emit = now
                loop.create_task(self._emit("eclipse_install_progress", target, line[:160]))

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
        self._cancel_requested = True
        self._install_task.cancel()
        return {"ok": True, "error": None}

    async def install_moonlight(self) -> dict[str, Any]:
        async def job(on_line: Any) -> dict[str, Any]:
            ok = await moonlight.install_flatpak(on_line, decky.DECKY_USER_HOME)
            ok = ok and (await self._resolve_moonlight(force=True))["installed"]
            return {"ok": ok, "error": None if ok else "Installation failed, see plugin logs", "warning": None}

        return await self._install("moonlight", job)

    async def install_sunshine(self) -> dict[str, Any]:
        async def job(on_line: Any) -> dict[str, Any]:
            result = await self.local_sunshine.install(on_line)
            if result["ok"]:
                # The setup script may enable the service; honor the user's choice.
                await self.local_sunshine.apply_autostart(bool(self.settings.data["sunshine"].get("autostart")))
            return result

        return await self._install("sunshine", job)

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

        def entry(info: Optional[dict[str, Any]], installed_elsewhere: bool) -> dict[str, Any]:
            if info:
                return {"installed": True, "managed": True, **info}
            return {"installed": installed_elsewhere, "managed": False, "version": None, "installation": None}

        return {
            "moonlight": entry(moonlight_info, resolved["installed"] and resolved["source"] != "flatpak"),
            "sunshine": entry(sunshine_info, sunshine_detected["installed"]),
            "operation": self._operation,
            "busy": self._installing,
        }

    async def check_app_update(self, feature: str) -> dict[str, Any]:
        info = await self._app_info(feature)
        if not info:
            return {"ok": False, "available": False, "version": None, "error": "Not installed as a Flatpak"}
        return await flatpak.check_update(FLATPAK_IDS[feature], info["installation"], decky.DECKY_USER_HOME)

    async def update_app(self, feature: str) -> dict[str, Any]:
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

    async def uninstall_app(self, feature: str, delete_data: bool) -> dict[str, Any]:
        info = await self._app_info(feature)
        if not info:
            return {"ok": False, "error": "Not installed as a Flatpak", "warning": None}

        async def job(on_line: Any) -> dict[str, Any]:
            if feature == "sunshine":
                return await self.local_sunshine.uninstall(info["installation"], on_line, bool(delete_data))
            await moonlight.kill_running(decky.DECKY_USER_HOME)
            ok = await flatpak.uninstall(FLATPAK_MOONLIGHT, info["installation"], on_line, bool(delete_data), decky.DECKY_USER_HOME)
            return {"ok": ok, "error": None if ok else "Uninstall failed, see the plugin log", "warning": None}

        return await self._install(feature, job, "uninstall")

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

    async def sunshine_status(self) -> dict[str, Any]:
        status = await self.local_sunshine.status()
        status["credentials"] = self._local_api() is not None
        status["autostart"] = bool(self.settings.data["sunshine"].get("autostart"))
        return status

    async def sunshine_start(self) -> dict[str, Any]:
        return await self.local_sunshine.start()

    async def sunshine_stop(self) -> dict[str, Any]:
        return await self.local_sunshine.stop()

    async def sunshine_set_autostart(self, enabled: bool) -> dict[str, Any]:
        """Persist the autostart option and sync the systemd user service with it."""
        self.settings.update({"sunshine": {"autostart": bool(enabled)}})
        return await self.local_sunshine.apply_autostart(bool(enabled))

    async def sunshine_submit_pin(self, pin: str, client_name: str) -> dict[str, Any]:
        api = self._local_api()
        if not api:
            return {"ok": False, "error": "Set the Sunshine username and password first"}
        pin = "".join(ch for ch in str(pin) if ch.isdigit())
        if len(pin) != 4:
            return {"ok": False, "error": "The PIN must have 4 digits"}
        return await api.submit_pin(pin, client_name.strip() or "Moonlight client")

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
