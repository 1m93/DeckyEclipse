"""Per-user Flatpak installation helpers.

A per-user install avoids the polkit prompt a system install would need,
which cannot be answered from Gaming Mode.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import proc

FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"


async def is_installed(app_id: str, home: Optional[str] = None) -> bool:
    rc, _, _ = await proc.run(["flatpak", "info", app_id], timeout=10, home=home)
    return rc == 0


async def install(app_id: str, on_line: Callable[[str], None], home: Optional[str] = None) -> bool:
    """Install ``app_id`` from Flathub into the user installation."""
    on_line("Adding the Flathub remote...")
    rc = await proc.run_streaming(
        ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO],
        on_line, timeout=120, home=home,
    )
    if rc != 0:
        on_line("Failed to add the Flathub remote")
        return False
    on_line(f"Installing {app_id}...")
    rc = await proc.run_streaming(
        ["flatpak", "install", "--user", "--noninteractive", "-y", "flathub", app_id],
        on_line, timeout=1800, home=home,
    )
    return rc == 0


def scope_flag(installation: Optional[str]) -> str:
    """Command-line flag selecting the installation an app lives in."""
    if installation in (None, "", "user"):
        return "--user"
    if installation == "system":
        return "--system"
    return f"--installation={installation}"


def _rows(output: str, columns: int) -> list[list[str]]:
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= columns:
            rows.append([p.strip() for p in parts[:columns]])
    return rows


async def app_info(app_id: str, home: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Version and installation of an installed app, or ``None``.

    Uses ``flatpak list --columns`` because its values are not localized,
    unlike the labels printed by ``flatpak info``.
    """
    rc, out, _ = await proc.run(
        ["flatpak", "list", "--app", "--columns=application,version,installation,origin,branch"],
        timeout=15, home=home,
    )
    if rc != 0:
        return None
    for app, version, installation, origin, branch in _rows(out, 5):
        if app == app_id:
            return {"version": version or None, "installation": installation, "origin": origin, "branch": branch}
    return None


async def check_update(app_id: str, installation: Optional[str], home: Optional[str] = None) -> dict[str, Any]:
    """Ask the remote whether a newer build of ``app_id`` exists (needs network)."""
    rc, out, err = await proc.run(
        ["flatpak", "remote-ls", "--updates", "--app", scope_flag(installation), "--columns=application,version"],
        timeout=90, home=home,
    )
    if rc != 0:
        return {"ok": False, "available": False, "version": None, "error": err.strip() or "Update check failed"}
    for app, version in _rows(out, 2):
        if app == app_id:
            return {"ok": True, "available": True, "version": version or None, "error": None}
    return {"ok": True, "available": False, "version": None, "error": None}


async def update(app_id: str, installation: Optional[str], on_line: Callable[[str], None], home: Optional[str] = None) -> bool:
    rc = await proc.run_streaming(
        ["flatpak", "update", scope_flag(installation), "--noninteractive", "-y", app_id],
        on_line, timeout=1800, home=home,
    )
    return rc == 0


async def uninstall(
    app_id: str,
    installation: Optional[str],
    on_line: Callable[[str], None],
    delete_data: bool = False,
    home: Optional[str] = None,
) -> bool:
    cmd = ["flatpak", "uninstall", scope_flag(installation), "--noninteractive", "-y"]
    if delete_data:
        cmd.append("--delete-data")
    rc = await proc.run_streaming([*cmd, app_id], on_line, timeout=600, home=home)
    return rc == 0
