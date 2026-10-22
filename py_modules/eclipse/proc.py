"""Subprocess helpers that are safe to use from the Decky backend.

Decky ships a PyInstaller build which injects its own ``LD_LIBRARY_PATH``.
System binaries such as ``flatpak`` or ``systemctl`` may crash or misbehave
with those libraries, so every child process gets a sanitized environment.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from typing import Callable, Optional, Sequence

from .log import get_logger

_STRIPPED_VARS = ("LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "_MEIPASS2")


def clean_env(home: Optional[str] = None) -> dict[str, str]:
    env = dict(os.environ)

    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    for key in _STRIPPED_VARS:
        env.pop(key, None)

    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    if home:
        env["HOME"] = home
    return env


def which(name: str, home: Optional[str] = None) -> Optional[str]:
    path = clean_env(home).get("PATH", "/usr/bin:/bin")
    return shutil.which(name, path=path)


def run_sync(cmd: Sequence[str], timeout: float = 10.0, home: Optional[str] = None) -> tuple[int, str, str]:
    """Run a command synchronously and return ``(returncode, stdout, stderr)``."""
    try:
        res = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_env(home),
            stdin=subprocess.DEVNULL,
        )
        return res.returncode, res.stdout, res.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"


async def run(cmd: Sequence[str], timeout: float = 10.0, home: Optional[str] = None) -> tuple[int, str, str]:
    """Async variant of :func:`run_sync` that never blocks the event loop."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env(home),
        )
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"{cmd[0]}: timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def run_streaming(
    cmd: Sequence[str],
    on_line: Callable[[str], None],
    timeout: float = 1800.0,
    home: Optional[str] = None,
) -> int:
    """Run a long command, forwarding each output line (stdout + stderr) to ``on_line``.

    Cancelling the awaiting task terminates the whole process group, so helper
    processes the command started (e.g. by a setup script) stop as well.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=clean_env(home),
            start_new_session=True,
        )
    except FileNotFoundError:
        on_line(f"{cmd[0]}: not found")
        return 127

    async def pump() -> None:
        assert proc.stdout is not None
        buffer = b""
        while True:
            chunk = await proc.stdout.read(1024)
            if not chunk:
                break
            buffer += chunk
            # Flatpak redraws progress with carriage returns, treat them as line breaks.
            parts = buffer.replace(b"\r", b"\n").split(b"\n")
            buffer = parts.pop()
            for part in parts:
                line = part.decode(errors="replace").strip()
                if line:
                    on_line(line)
        if buffer.strip():
            on_line(buffer.decode(errors="replace").strip())

    try:
        await asyncio.wait_for(pump(), timeout)
    except asyncio.TimeoutError:
        get_logger().warning("Command timed out: %s", cmd)
        await _terminate_group(proc)
    except asyncio.CancelledError:
        get_logger().info("Command cancelled: %s", cmd)
        await _terminate_group(proc)
        raise
    return await proc.wait()


async def _terminate_group(proc: asyncio.subprocess.Process, grace: float = 5.0) -> None:
    """SIGTERM the process group, then SIGKILL whatever is left after ``grace`` seconds."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), grace)
            return
        except asyncio.TimeoutError:
            continue


def spawn_detached(cmd: Sequence[str], home: Optional[str] = None, log_path: Optional[str] = None) -> int:
    """Start a long-lived process that survives the plugin backend. Returns its PID."""
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=clean_env(home),
            start_new_session=True,
        )
    finally:
        if log_path and hasattr(log, "close"):
            log.close()  # type: ignore[union-attr]
    return proc.pid


def find_processes(names: Sequence[str]) -> list[int]:
    """Return PIDs whose process name or executable basename matches one of ``names``."""
    wanted = set(names)
    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", "r") as fh:
                comm = fh.read().strip()
            if comm in wanted:
                pids.append(int(entry))
                continue
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv0 = fh.read().split(b"\0", 1)[0].decode(errors="ignore")
            if os.path.basename(argv0) in wanted:
                pids.append(int(entry))
        except OSError:
            continue
    return pids
