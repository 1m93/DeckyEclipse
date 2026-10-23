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
from . import sunshine_config, sunshine_data
from . import sunshine_native as native
from .log import get_logger

# Units of other Sunshine installs (the Flatpak, or a distribution package).
_OTHER_UNITS = ("sunshine.service", "app-dev.lizardbyte.app.Sunshine.service")
_SYSTEMD_UNITS = (native.UNIT, *_OTHER_UNITS)
_PROCESS_NAMES = ("sunshine", "Sunshine")
_DETECT_CACHE_S = 60.0
# How long a stopped Sunshine may take to exit before a restart starts it again.
_RESTART_STOP_WAIT_S = 10.0


# Sunshine answers every API call with this redirect (to /welcome) while no
# web UI login has been created yet.
NEEDS_SETUP_STATUS = 307


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects as errors: following Sunshine's first-run redirect to
    /welcome would turn "no login exists" into a misleading 200."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class SunshineApi:
    """Minimal client for the Sunshine web UI API (HTTPS + basic auth).

    Sunshine uses a self-signed certificate, so verification is disabled.
    Requests are made in a worker thread to keep the event loop responsive.
    """

    def __init__(self, host: str, user: str, password: str, port: int = DEFAULT_SUNSHINE_WEB_PORT) -> None:
        self.host = host
        self.port = port or DEFAULT_SUNSHINE_WEB_PORT
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = f"Basic {token}" if user else None
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=self._ssl))

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
        if self._auth:
            req.add_header("Authorization", self._auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=timeout) as res:
                raw = res.read(limit)
                status = res.status
        except urllib.error.HTTPError as exc:
            # Sunshine explains rejections in a JSON body ({"error": "..."}).
            try:
                return exc.code, json.loads(exc.read(64 * 1024).decode() or "null")
            except (OSError, ValueError):
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

    async def submit_pin(self, pin: str, name: str, pairing_id: Optional[str] = None) -> dict[str, Any]:
        """Send a pairing PIN.

        Newer Sunshine needs the ``pairing_id`` of a pending request and only
        answers once pairing finished, so ``ok`` means "paired". Older Sunshine
        takes just the PIN and ``ok`` only means "PIN accepted".
        """
        body: dict[str, Any] = {"pin": pin, "name": name}
        if pairing_id:
            body["pairing_id"] = pairing_id
        status, payload = await self.request("POST", "/api/pin", body, timeout=60.0 if pairing_id else 5.0)
        result = self._result(status, payload)
        if not result["ok"] and isinstance(payload, dict) and payload.get("error"):
            result["error"] = str(payload["error"])
        return result

    async def pending_pairings(self) -> dict[str, Any]:
        """Devices waiting for a PIN (newer Sunshine). ``supported`` is False on older versions."""
        status, payload = await self.request("GET", "/api/pin")
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("pairings"), list):
            items = [
                {"id": str(p.get("id", "")), "name": str(p.get("name") or "Unknown device"), "address": str(p.get("address") or "")}
                for p in payload["pairings"] if isinstance(p, dict) and p.get("id")
            ]
            return {"ok": True, "supported": True, "items": items, "error": None}
        # Older versions have no GET /api/pin: depending on the version they
        # answer with the web UI page (200, not JSON) or 404/405. That just
        # means "use the PIN-only pairing API", not a failure.
        legacy = status in (200, 404, 405)
        return {
            "ok": legacy,
            "supported": False,
            "items": [],
            "status": status,
            "error": None if legacy else describe_status(status),
        }

    async def clients(self) -> dict[str, Any]:
        """Paired clients (``named_certs``): name, uuid and, on newer versions, enabled."""
        status, payload = await self.request("GET", "/api/clients/list")
        if status != 200 or not isinstance(payload, dict):
            return {"ok": False, "items": [], "error": describe_status(status)}
        items = [
            {"uuid": str(c.get("uuid", "")), "name": str(c.get("name") or "Unnamed device"), "enabled": c.get("enabled", True)}
            for c in payload.get("named_certs") or [] if isinstance(c, dict) and c.get("uuid")
        ]
        return {"ok": True, "items": items, "error": None}

    async def unpair(self, uuid: str) -> dict[str, Any]:
        status, payload = await self.request("POST", "/api/clients/unpair", {"uuid": uuid})
        return self._result(status, payload)

    async def close_app(self) -> dict[str, Any]:
        status, payload = await self.request("POST", "/api/apps/close", {})
        return self._result(status, payload)

    async def test(self) -> dict[str, Any]:
        """Check the login. ``needsSetup`` means Sunshine has no login at all yet."""
        status, _ = await self.request("GET", "/api/apps")
        return {
            "ok": status == 200,
            "status": status,
            "needsSetup": status == NEEDS_SETUP_STATUS,
            "error": None if status == 200 else describe_status(status),
        }

    async def create_login(self, user: str, password: str) -> dict[str, Any]:
        """Create the first web UI login of a Sunshine that has none.

        Sunshine accepts POST /api/password without authentication only while
        no login exists; afterwards the current login is required.
        """
        status, payload = await self.request(
            "POST", "/api/password",
            {"newUsername": user, "newPassword": password, "confirmNewPassword": password},
        )
        result = self._result(status, payload)
        if not result["ok"] and isinstance(payload, dict) and payload.get("error"):
            result["error"] = str(payload["error"])
        if status == 401:
            result["error"] = "Sunshine already has a login. Enter that login instead."
        return result

    def _result(self, status: int, payload: Any) -> dict[str, Any]:
        ok = self._ok(status, payload)
        return {"ok": ok, "status": status, "error": None if ok else describe_status(status)}


def describe_status(status: int) -> str:
    if status == 0:
        return "Sunshine web UI is unreachable"
    if status == NEEDS_SETUP_STATUS:
        return (
            "Sunshine has no login yet. Create one in Sunshine's web page "
            "(for Sunshine on this device: Eclipse → Settings → Sunshine → Create login)."
        )
    if status == 401:
        return "Invalid Sunshine username or password"
    if status == 200:
        return "Sunshine rejected the request"
    return f"Sunshine returned HTTP {status}"


LEGACY_PAIR_WAIT_S = 20.0


async def pair_client(api: SunshineApi, pin: str, name: str, pairing_id: Optional[str] = None) -> dict[str, Any]:
    """Pair a Moonlight client with a PIN and report whether it really paired.

    Works with both Sunshine pairing APIs: the newer one (pending requests
    with ids, the POST returns the final result) and the older one (PIN only;
    success is confirmed by the client appearing in the paired list).
    """
    pending = await api.pending_pairings()
    if not pending["ok"]:
        # No login, wrong login or unreachable: no point in sending a PIN.
        return {"ok": False, "paired": False, "error": pending["error"]}
    if pending["supported"]:
        if not pairing_id:
            if not pending["items"]:
                return {"ok": False, "paired": False, "error": "No device is waiting to pair. Start pairing in Moonlight on the other device first."}
            if len(pending["items"]) > 1:
                return {"ok": False, "paired": False, "error": "Several devices are waiting to pair. Choose one first.", "choose": pending["items"]}
            pairing_id = pending["items"][0]["id"]
        device = next((p for p in pending["items"] if p["id"] == pairing_id), None)
        result = await api.submit_pin(pin, name, pairing_id)
        if result["ok"]:
            return {"ok": True, "paired": True, "client": device["name"] if device else name, "error": None}
        error = result["error"] if result["status"] != 200 else "Pairing failed: wrong PIN, or the device stopped waiting."
        return {"ok": False, "paired": False, "error": error}

    # Older Sunshine: remember who is paired, send the PIN, then watch for a new client.
    before = await api.clients()
    known = {c["uuid"] for c in before["items"]}
    result = await api.submit_pin(pin, name)
    if not result["ok"]:
        error = result["error"] if result["status"] != 200 else "Sunshine did not accept the PIN. Is a device waiting to pair?"
        return {"ok": False, "paired": False, "error": error}
    if not before["ok"]:
        # Cannot confirm without the client list; the PIN was accepted at least.
        return {"ok": True, "paired": True, "client": name, "error": None}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LEGACY_PAIR_WAIT_S
    while loop.time() < deadline:
        await asyncio.sleep(1.5)
        now = await api.clients()
        new = [c for c in now["items"] if c["uuid"] not in known]
        if new:
            return {"ok": True, "paired": True, "client": new[0]["name"], "error": None}
    return {"ok": False, "paired": False, "error": "The PIN was sent but the device did not finish pairing. Check the PIN and try again."}


class LocalSunshine:
    """Controls the Sunshine server on this device.

    Eclipse installs "Sunshine for Gaming Mode" (see sunshine_native.py). A
    Sunshine Flatpak, or one from a package, is still detected and controlled.
    """

    def __init__(self, home: str, log_dir: str, plugin_dir: str) -> None:
        self.home = home
        self.log_path = os.path.join(log_dir, "sunshine.log")
        self.root_script = os.path.join(plugin_dir, "bin", "eclipse_sunshine_root.py")
        self.helpers_dir = os.path.join(plugin_dir, "bin", "native")
        self.unit_path = os.path.join(home, ".config", "systemd", "user", native.UNIT)
        self.config_path = os.path.join(home, ".config", "sunshine", "sunshine.conf")
        self.flatpak_config_path = os.path.join(
            home, ".var", "app", FLATPAK_SUNSHINE, "config", "sunshine", "sunshine.conf"
        )
        self._spawned_pid: Optional[int] = None
        # The config file changed while Sunshine was running: it applies after a restart.
        self.config_pending = False
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

    def native_state(self) -> dict[str, Any]:
        return native.state(self.helpers_dir)

    def invalidate(self) -> None:
        self._detected = None

    async def detect(self) -> dict[str, Any]:
        """Find how Sunshine is installed. Cached, since installs rarely change.

        Sunshine for Gaming Mode wins over other installs: only it can capture
        Gaming Mode.
        """
        if self._detected and time.monotonic() - self._detected_at < _DETECT_CACHE_S:
            return self._detected
        eclipse = self.native_state()["installed"]
        if eclipse and not os.path.isfile(self.unit_path):
            await self._write_unit()
        unit = await self._find_unit()
        flatpak_installed = await self._flatpak_installed()
        path_binary = proc.which("sunshine", self.home)
        installed = bool(eclipse or unit or flatpak_installed or path_binary)
        if eclipse:
            kind = "eclipse"
        else:
            kind = "systemd" if unit else "flatpak" if flatpak_installed else "native" if path_binary else None
        self._detected = {"installed": installed, "kind": kind, "unit": unit, "flatpak": flatpak_installed}
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
        state = self.native_state() if info["kind"] == "eclipse" else None
        return {**info, "running": running, "unitEnabled": unit_enabled, "native": state}

    async def _migrate_data(self) -> None:
        """Move legacy data back and drop duplicate paired devices. Sunshine must be stopped."""
        if self.native_state().get("legacy"):
            # The legacy binary still uses the legacy place until it is repaired.
            return
        for line in await asyncio.to_thread(sunshine_data.migrate, self.home):
            get_logger().info(line)

    async def start(self) -> dict[str, Any]:
        info = await self.detect()
        if info["kind"] == "eclipse" and not proc.find_processes(_PROCESS_NAMES):
            try:
                await self._migrate_data()
            except OSError as exc:
                get_logger().warning("Could not tidy Sunshine's data before starting: %s", exc)
        if info["kind"] == "eclipse" and info["flatpak"]:
            # Both would use the same ports.
            await proc.run(["flatpak", "kill", FLATPAK_SUNSHINE], timeout=10, home=self.home)
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
        self.config_pending = False
        return {"ok": True, "error": None}

    async def stop(self) -> dict[str, Any]:
        info = await self.detect()
        if info["unit"]:
            await self._systemctl("stop", info["unit"])
        if info["flatpak"]:
            await proc.run(["flatpak", "kill", FLATPAK_SUNSHINE], timeout=10, home=self.home)
        for pid in proc.find_processes(_PROCESS_NAMES):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self._spawned_pid = None
        self.config_pending = False
        return {"ok": True, "error": None}

    async def restart(self) -> dict[str, Any]:
        """Stop Sunshine, wait until it has exited (its ports are then free), and start it again."""
        pending = self.config_pending
        await self.stop()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESTART_STOP_WAIT_S
        while proc.find_processes(_PROCESS_NAMES):
            if loop.time() >= deadline:
                # Still the old process: the changes have not been applied.
                self.config_pending = pending
                return {"ok": False, "error": "Sunshine did not stop. Try again, or turn it off and on."}
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.5)
        return await self.start()

    # ------------------------------------------------------------ options (sunshine.conf)

    async def _config_file(self) -> str:
        info = await self.detect()
        return self.flatpak_config_path if info["kind"] == "flatpak" else self.config_path

    async def read_options(self) -> dict[str, Any]:
        path = await self._config_file()

        def read() -> str:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return fh.read()
            except FileNotFoundError:
                return ""

        return sunshine_config.read(await asyncio.to_thread(read))

    async def write_options(self, patch: dict[str, Any], running: bool) -> dict[str, Any]:
        """Change options in sunshine.conf. Raises ValueError for an invalid value."""
        path = await self._config_file()

        def write() -> dict[str, Any]:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except FileNotFoundError:
                text = ""
            new_text = sunshine_config.apply(text, patch)
            if new_text != text:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                temp = path + ".eclipse-tmp"
                with open(temp, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                if os.path.exists(path):
                    os.chmod(temp, os.stat(path).st_mode & 0o777)
                os.replace(temp, path)
            return {"changed": new_text != text, "values": sunshine_config.read(new_text)}

        result = await asyncio.to_thread(write)
        if result["changed"] and running:
            self.config_pending = True
        return result["values"]

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

    # ------------------------------------------------------------ Sunshine for Gaming Mode

    async def _write_unit(self) -> None:
        def write() -> None:
            os.makedirs(os.path.dirname(self.unit_path), exist_ok=True)
            with open(self.unit_path, "w", encoding="utf-8") as fh:
                fh.write(native.unit_text())

        await asyncio.to_thread(write)
        await self._systemctl("daemon-reload")

    def _prepare_config(self) -> None:
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            text = ""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write(native.prepare_config(text))

    async def _run_root(self, args: list[str], password: str, on_line: Callable[[str], None]) -> dict[str, Any]:
        """Run the root installer with sudo. The password only goes to sudo's stdin."""
        password = (password or "").replace("\r", "").replace("\n", "")
        if not password:
            return {"ok": False, "error": "Enter your password", "passwordRejected": True}
        ready = False
        sudo_lines: list[str] = []
        errors: list[str] = []

        def handle(line: str) -> None:
            nonlocal ready
            if line == ROOT_READY:
                ready = True
            elif not ready:
                sudo_lines.append(line)
            else:
                if line.startswith("ERROR: "):
                    errors.append(line[len("ERROR: "):])
                on_line(line)

        cmd = ["sudo", "-S", "-k", "-p", "", "--", "/usr/bin/python3", "-I", self.root_script, *args]
        rc = await proc.run_streaming(cmd, handle, timeout=1800, home=self.home, input=(password + "\n").encode())
        if not ready:
            get_logger().warning("sudo failed (exit %s): %s", rc, " | ".join(sudo_lines))
            return {"ok": False, "error": describe_sudo_failure(sudo_lines), "passwordRejected": True}
        if rc != 0:
            return {"ok": False, "error": errors[-1] if errors else f"The installer failed (exit {rc})"}
        return {"ok": True, "error": None}

    async def install(self, password: str, on_line: Callable[[str], None], tag: Optional[str] = None) -> dict[str, Any]:
        """Install or update Sunshine for Gaming Mode (``tag`` None: latest release)."""
        state = self.native_state()
        was_running = state["installed"] and bool(proc.find_processes(_PROCESS_NAMES))
        if was_running:
            on_line("Stopping Sunshine...")
            await self.stop()
        result = await self._run_root(["install", *(["--tag", tag] if tag else [])], password, on_line)
        self.invalidate()
        if not result["ok"]:
            if was_running:
                await self.start()
            return {**result, "warning": None}

        on_line("Setting up the Sunshine service... (98%)")
        await self._write_unit()
        warning = None
        try:
            # Before the config is adjusted: the user's own config may still be in the legacy place.
            await self._migrate_data()
            await asyncio.to_thread(self._prepare_config)
        except OSError as exc:
            warning = f"Sunshine is installed, but its config could not be updated: {exc}"
        if await self._flatpak_installed():
            # Two Sunshine servers cannot share the ports: keep the Flatpak from starting.
            for unit in _OTHER_UNITS:
                await self._systemctl("disable", unit)
            on_line("The Flatpak Sunshine no longer starts on its own")
        self.invalidate()
        if was_running:
            await self.start()
        return {"ok": True, "error": None, "warning": warning}

    async def repair(self, password: str, on_line: Callable[[str], None]) -> dict[str, Any]:
        """Reinstall the installed version: restores the capability and updates the helpers."""
        tag = native.installed_manifest().get("tag")
        return await self.install(password, on_line, tag if isinstance(tag, str) and native.TAG_RE.match(tag) else None)

    async def uninstall(self, password: str, on_line: Callable[[str], None], delete_data: bool) -> dict[str, Any]:
        on_line("Stopping Sunshine...")
        await self.stop()
        result = await self._run_root(["uninstall"], password, on_line)
        self.invalidate()
        if not result["ok"]:
            return {**result, "warning": None}
        await self._systemctl("disable", native.UNIT)
        try:
            os.unlink(self.unit_path)
        except FileNotFoundError:
            pass
        await self._systemctl("daemon-reload")
        if delete_data:
            await asyncio.to_thread(sunshine_data.remove_all, self.home)
        self.invalidate()
        return {"ok": True, "error": None, "warning": None}

    # ------------------------------------------------------------ Flatpak

    async def uninstall_flatpak(self, installation: Optional[str], on_line: Callable[[str], None], delete_data: bool) -> dict[str, Any]:
        """Stop the Flatpak Sunshine, undo its service setup, then remove it."""
        on_line("Stopping Sunshine...")
        await proc.run(["flatpak", "kill", FLATPAK_SUNSHINE], timeout=10, home=self.home)
        for unit in _OTHER_UNITS:
            await self._systemctl("disable", "--now", unit)
        on_line("Removing the Sunshine service...")
        # Counterpart of the Flatpak's additional-install.sh; failures are not fatal.
        await proc.run_streaming(
            ["flatpak", "run", "--command=remove-additional-install.sh", FLATPAK_SUNSHINE],
            on_line, timeout=60, home=self.home,
        )
        ok = await flatpak.uninstall(FLATPAK_SUNSHINE, installation, on_line, delete_data, self.home)
        self.invalidate()
        return {"ok": ok, "error": None if ok else "Uninstall failed, see the plugin log", "warning": None}


# First line of the root installer (bin/eclipse_sunshine_root.py) once sudo has accepted the password.
ROOT_READY = "ECLIPSE-ROOT-READY"


def describe_sudo_failure(lines: list[str]) -> str:
    """Explain why sudo did not run the installer, from its output."""
    text = " ".join(lines).lower()
    if "incorrect password" in text or "sorry, try again" in text:
        return (
            "Wrong password. After several wrong tries SteamOS locks sudo for about 10 minutes, "
            "even for the right password."
        )
    if "not in the sudoers" in text or "not allowed" in text:
        return "This user may not use sudo."
    if "no password" in text or "password is required" in text:
        return "No password was accepted. If you never set one, run passwd in Konsole (Desktop Mode)."
    detail = lines[-1] if lines else "no output"
    return f"sudo failed: {detail}"
