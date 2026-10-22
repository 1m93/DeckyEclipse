"""Sunshine web API client and local Sunshine service management."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from . import DEFAULT_SUNSHINE_WEB_PORT, FLATPAK_SUNSHINE
from . import flatpak, proc
from .log import get_logger

_SYSTEMD_UNITS = ("sunshine.service", "app-dev.lizardbyte.app.Sunshine.service")
_PROCESS_NAMES = ("sunshine", "Sunshine")
_DETECT_CACHE_S = 60.0


class SunshineApi:
    """Minimal client for the Sunshine web UI API (HTTPS + basic auth).

    Sunshine uses a self-signed certificate, so verification is disabled.
    Requests are made in a worker thread to keep the event loop responsive.
    """

    def __init__(self, host: str, user: str, password: str, port: int = DEFAULT_SUNSHINE_WEB_PORT) -> None:
        self.host = host
        self.port = port or DEFAULT_SUNSHINE_WEB_PORT
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = f"Basic {token}"
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE

    def _url(self, path: str) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host}:{self.port}{path}"

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]],
        timeout: float,
        limit: int = 256 * 1024,
        raw_text: bool = False,
    ) -> tuple[int, Any]:
        """Blocking request. Returns ``(status, payload)``; status 0 means unreachable.

        Requests carry no Origin/Referer header, which Sunshine's CSRF check
        accepts for non-browser clients.
        """
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._url(path), data=data, method=method)
        req.add_header("Authorization", self._auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ssl) as res:
                raw = res.read(limit)
                status = res.status
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            get_logger().info("Sunshine API %s %s failed: %s", method, path, exc)
            return 0, None
        if raw_text:
            return status, raw.decode(errors="replace")
        try:
            return status, json.loads(raw.decode() or "null")
        except ValueError:
            return status, None

    request_sync = _request

    async def request(self, method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: float = 5.0) -> tuple[int, Any]:
        return await asyncio.to_thread(self._request, method, path, body, timeout)

    @staticmethod
    def _ok(status: int, payload: Any) -> bool:
        if status != 200:
            return False
        if isinstance(payload, dict) and "status" in payload:
            return str(payload["status"]).lower() == "true"
        return True

    async def submit_pin(self, pin: str, name: str) -> dict[str, Any]:
        status, payload = await self.request("POST", "/api/pin", {"pin": pin, "name": name})
        return self._result(status, payload)

    async def close_app(self) -> dict[str, Any]:
        status, payload = await self.request("POST", "/api/apps/close", {})
        return self._result(status, payload)

    async def test(self) -> dict[str, Any]:
        status, payload = await self.request("GET", "/api/apps")
        return {"ok": status == 200, "status": status, "error": None if status == 200 else describe_status(status)}

    def _result(self, status: int, payload: Any) -> dict[str, Any]:
        ok = self._ok(status, payload)
        return {"ok": ok, "status": status, "error": None if ok else describe_status(status)}


def describe_status(status: int) -> str:
    if status == 0:
        return "Sunshine web UI is unreachable"
    if status == 401:
        return "Invalid Sunshine username or password"
    if status == 200:
        return "Sunshine rejected the request"
    return f"Sunshine returned HTTP {status}"


class LocalSunshine:
    """Controls a Sunshine server installed on this device."""

    def __init__(self, home: str, log_dir: str) -> None:
        self.home = home
        self.log_path = os.path.join(log_dir, "sunshine.log")
        self._spawned_pid: Optional[int] = None
        self._detected: Optional[dict[str, Any]] = None
        self._detected_at = 0.0

    async def _systemctl(self, *args: str) -> tuple[int, str, str]:
        return await proc.run(["systemctl", "--user", *args], timeout=10, home=self.home)

    async def _find_unit(self) -> Optional[str]:
        for unit in _SYSTEMD_UNITS:
            rc, out, _ = await self._systemctl("list-unit-files", "--no-legend", unit)
            if rc == 0 and out.strip():
                return unit
        return None

    async def _flatpak_installed(self) -> bool:
        return await flatpak.is_installed(FLATPAK_SUNSHINE, self.home)

    def invalidate(self) -> None:
        self._detected = None

    async def detect(self) -> dict[str, Any]:
        """Find how Sunshine is installed. Cached, since installs rarely change."""
        if self._detected and time.monotonic() - self._detected_at < _DETECT_CACHE_S:
            return self._detected
        unit = await self._find_unit()
        flatpak = await self._flatpak_installed()
        native = proc.which("sunshine", self.home)
        installed = bool(unit or flatpak or native)
        kind = "systemd" if unit else "flatpak" if flatpak else "native" if native else None
        self._detected = {"installed": installed, "kind": kind, "unit": unit}
        self._detected_at = time.monotonic()
        return self._detected

    async def status(self) -> dict[str, Any]:
        info = dict(await self.detect())
        running = False
        unit_enabled = False
        if info["unit"]:
            rc, _, _ = await self._systemctl("is-active", "--quiet", info["unit"])
            running = rc == 0
            rc, _, _ = await self._systemctl("is-enabled", "--quiet", info["unit"])
            unit_enabled = rc == 0
        if not running:
            running = bool(proc.find_processes(_PROCESS_NAMES))
        return {**info, "running": running, "unitEnabled": unit_enabled}

    async def start(self) -> dict[str, Any]:
        info = await self.detect()
        if info["unit"]:
            rc, _, err = await self._systemctl("start", info["unit"])
            if rc != 0:
                return {"ok": False, "error": err.strip() or "systemctl start failed"}
        elif info["kind"] == "flatpak":
            self._spawned_pid = proc.spawn_detached(["flatpak", "run", FLATPAK_SUNSHINE], self.home, self.log_path)
        elif info["kind"] == "native":
            self._spawned_pid = proc.spawn_detached(["sunshine"], self.home, self.log_path)
        else:
            return {"ok": False, "error": "Sunshine is not installed on this device"}
        return {"ok": True, "error": None}

    async def stop(self) -> dict[str, Any]:
        info = await self.detect()
        if info["unit"]:
            await self._systemctl("stop", info["unit"])
        if info["kind"] == "flatpak":
            await proc.run(["flatpak", "kill", FLATPAK_SUNSHINE], timeout=10, home=self.home)
        for pid in proc.find_processes(_PROCESS_NAMES):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self._spawned_pid = None
        return {"ok": True, "error": None}

    async def apply_autostart(self, enabled: bool) -> dict[str, Any]:
        """Make the systemd user service (if any) match the autostart option.

        Without a unit there is nothing to change: the plugin itself starts
        Sunshine on load when the option is on.
        """
        unit = await self._find_unit()
        if not unit:
            return {"ok": True, "error": None}
        rc, _, err = await self._systemctl("enable" if enabled else "disable", unit)
        return {"ok": rc == 0, "error": None if rc == 0 else err.strip()}

    async def install(self, on_line: Callable[[str], None]) -> dict[str, Any]:
        """Install the Sunshine Flatpak and try to set up its user service.

        The service setup script may need root for udev rules (virtual input).
        That cannot be confirmed from Gaming Mode, so a failure there is a
        warning: Sunshine still runs, but setup may need finishing on the desktop.
        """
        if not await flatpak.install(FLATPAK_SUNSHINE, on_line, self.home):
            return {"ok": False, "error": "Installation failed, see plugin logs", "warning": None}
        on_line("Setting up the Sunshine service...")
        rc = await proc.run_streaming(
            ["flatpak", "run", "--command=additional-install.sh", FLATPAK_SUNSHINE],
            on_line, timeout=90, home=self.home,
        )
        self.invalidate()
        warning = None
        if rc != 0:
            warning = (
                "Sunshine is installed, but its service setup did not finish. Run "
                "'flatpak run --command=additional-install.sh dev.lizardbyte.app.Sunshine' in Desktop Mode "
                "for controller input support."
            )
        return {"ok": True, "error": None, "warning": warning}

    async def uninstall(self, installation: Optional[str], on_line: Callable[[str], None], delete_data: bool) -> dict[str, Any]:
        """Stop Sunshine, undo its service setup, then remove the Flatpak."""
        on_line("Stopping Sunshine...")
        await self.stop()
        await self.apply_autostart(False)
        on_line("Removing the Sunshine service...")
        # Counterpart of additional-install.sh; failures are not fatal.
        await proc.run_streaming(
            ["flatpak", "run", "--command=remove-additional-install.sh", FLATPAK_SUNSHINE],
            on_line, timeout=60, home=self.home,
        )
        ok = await flatpak.uninstall(FLATPAK_SUNSHINE, installation, on_line, delete_data, self.home)
        self.invalidate()
        return {"ok": ok, "error": None if ok else "Uninstall failed, see the plugin log", "warning": None}
