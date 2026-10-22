"""Sunshine for Gaming Mode: a native Sunshine that can capture SteamOS Gaming Mode.

The Flatpak build of Sunshine cannot (KMS capture is compiled out), so Eclipse
installs Sunshine's official Ubuntu 22.04 build instead:

* Everything lives in ``ROOT``, owned by root. ``/home`` survives SteamOS
  updates and is not mounted nosuid, so file capabilities work there.
* The Ubuntu build needs only glibc 2.35 and Qt 6.2, older than SteamOS ships.
  The two libraries SteamOS lacks are taken from Ubuntu, checked by SHA-256.
* Sunshine reads its web UI and shaders from the compiled-in
  ``/usr/share/sunshine``, read-only on SteamOS. That prefix is replaced in
  the binary by ``ROOT`` plus a slash: the same length, and a doubled slash is
  harmless in a path.
* ``bin/eclipse-sunshine`` (native/sunshine_launcher.c) starts Sunshine with
  the capabilities KMS needs and preloads ``lib/libeclipse-sunshine.so``
  (native/sunshine_preload.c), which avoids the black picture of Sunshine
  2026.906+ in Gaming Mode.

Installing needs root and runs through ``bin/eclipse_sunshine_root.py`` with
sudo. This module only holds what both sides share; it must not need root to
import and uses the standard library only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from typing import Any, Iterator, Optional

ROOT = "/home/.eclipse-sun"
BIN_DIR = ROOT + "/bin"
LIB_DIR = ROOT + "/lib"
LAUNCHER = BIN_DIR + "/eclipse-sunshine"
BINARY = BIN_DIR + "/sunshine"
PRELOAD = LIB_DIR + "/libeclipse-sunshine.so"
MANIFEST = ROOT + "/eclipse.json"
HELPERS = ("eclipse-sunshine", "libeclipse-sunshine.so")

ASSETS_OLD = b"/usr/share/sunshine"
ASSETS_NEW = (ROOT + "/").encode()
assert len(ASSETS_OLD) == len(ASSETS_NEW), "the asset prefix must keep its length"

CAPABILITIES = "cap_sys_admin,cap_sys_nice+p"
UNIT = "eclipse-sunshine.service"

RELEASES_API = "https://api.github.com/repos/LizardByte/Sunshine/releases"
TAG_RE = re.compile(r"^v\d{4}\.\d{1,4}\.\d{1,6}$")
# The asset was renamed over time: sunshine-ubuntu-22.04-amd64.deb, then
# sunshine_<version>-1+ubuntu22.04_amd64.deb.
_DEB_ASSET_RE = re.compile(r"ubuntu-?22\.04[-_]amd64\.deb$")
DOWNLOAD_PREFIX = "https://github.com/LizardByte/Sunshine/releases/download/"

UBUNTU_MIRROR = "http://archive.ubuntu.com/ubuntu/"
# Ubuntu 22.04 (jammy) packages whose libraries SteamOS lacks: (path, sha256).
UBUNTU_LIBS = (
    (
        "pool/main/m/miniupnpc/libminiupnpc17_2.2.3-1build1_amd64.deb",
        "3431052123068697448c7b644f040bec836b65b7c1fa4a80b652957269dc8bac",
    ),
    (
        "pool/main/n/numactl/libnuma1_2.0.14-3ubuntu2_amd64.deb",
        "0721c89001fbbd1ada23e89da5d60e762763c1a7b3dc814a2e9a518480a8043d",
    ),
)

# Files of the Sunshine package used outside ROOT: (path in the package, destination).
SYSTEM_FILES = (
    ("lib/udev/rules.d/60-sunshine.rules", "/etc/udev/rules.d/60-sunshine.rules"),
    ("usr/lib/modules-load.d/60-sunshine.conf", "/etc/modules-load.d/60-sunshine.conf"),
)


# ---------------------------------------------------------------- releases


def pick_deb_asset(release: dict[str, Any]) -> Optional[dict[str, str]]:
    """The Ubuntu 22.04 amd64 package of a GitHub release, or None."""
    tag = str(release.get("tag_name") or "")
    if not TAG_RE.match(tag):
        return None
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if _DEB_ASSET_RE.search(name) and url.startswith(DOWNLOAD_PREFIX + tag + "/"):
            return {"tag": tag, "version": tag[1:], "name": name, "url": url}
    return None


def release_api_url(tag: Optional[str]) -> str:
    if tag is None:
        return RELEASES_API + "/latest"
    if not TAG_RE.match(tag):
        raise ValueError(f"not a Sunshine release tag: {tag}")
    return f"{RELEASES_API}/tags/{tag}"


# ---------------------------------------------------------------- packages


def ar_members(data: bytes) -> dict[str, bytes]:
    """Members of an ar archive (the outer format of a .deb)."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not a .deb package")
    members: dict[str, bytes] = {}
    pos = 8
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        name = header[:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        start = pos + 60
        members[name] = data[start:start + size]
        pos = start + size + (size & 1)  # Members are 2-byte aligned.
    return members


def _unzstd(data: bytes) -> bytes:
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+

        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        res = subprocess.run(["zstd", "-dcq"], input=data, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot decompress zstd data: {exc}") from exc
    if res.returncode != 0:
        raise ValueError("cannot decompress zstd data: " + res.stderr.decode(errors="replace").strip())
    return res.stdout


def deb_data(deb: bytes) -> tarfile.TarFile:
    """The data archive (installed files) of a .deb."""
    for name, payload in ar_members(deb).items():
        if name.startswith("data.tar"):
            if name.endswith(".zst"):
                payload = _unzstd(payload)
            return tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")
    raise ValueError("the .deb package has no data archive")


def member_path(member: tarfile.TarInfo) -> str:
    """Normalized path of a package member: ``./usr/bin/x`` -> ``usr/bin/x``."""
    name = member.name
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _safe_relative(path: str) -> bool:
    parts = path.split("/")
    return bool(path) and not path.startswith("/") and ".." not in parts and "" not in parts


def sunshine_files(tar: tarfile.TarFile) -> Iterator[tuple[tarfile.TarInfo, str]]:
    """(member, destination relative to ROOT) for what Sunshine needs from its package."""
    for member in tar.getmembers():
        path = member_path(member)
        if path == "usr/bin/sunshine" and member.isfile():
            yield member, "bin/sunshine"
        elif path.startswith("usr/share/sunshine/") and (member.isfile() or member.isdir()):
            rel = path[len("usr/share/sunshine/"):].rstrip("/")
            if _safe_relative(rel):
                yield member, rel


def library_files(tar: tarfile.TarFile) -> Iterator[tuple[tarfile.TarInfo, str]]:
    """(member, destination relative to ROOT) for the shared libraries of a package."""
    for member in tar.getmembers():
        path = member_path(member)
        base = os.path.basename(path)
        if not path.startswith("usr/lib/x86_64-linux-gnu/") or ".so" not in base:
            continue
        if member.isfile():
            yield member, "lib/" + base
        elif member.issym() and "/" not in member.linkname:
            # Soname links such as libnuma.so.1 -> libnuma.so.1.0.0.
            yield member, "lib/" + base


def extract(tar: tarfile.TarFile, files: Iterator[tuple[tarfile.TarInfo, str]], dest: str) -> int:
    """Write the selected members below ``dest``. Returns how many were written.

    Only regular files, directories and same-directory symlinks are written,
    with plain permissions, so a package cannot place anything outside ``dest``.
    """
    count = 0
    for member, rel in files:
        target = os.path.join(dest, rel)
        if member.isdir():
            os.makedirs(target, mode=0o755, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
        if os.path.lexists(target):
            os.unlink(target)
        if member.issym():
            os.symlink(member.linkname, target)
        else:
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as out:
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
        count += 1
    return count


def patch_assets_prefix(data: bytes) -> tuple[bytes, int]:
    """Point the compiled-in asset directory at ROOT. Returns (data, places)."""
    count = data.count(ASSETS_OLD)
    return data.replace(ASSETS_OLD, ASSETS_NEW), count


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return sha256(fh.read())
    except OSError:
        return None


# ---------------------------------------------------------------- state


def has_capabilities(path: str = LAUNCHER) -> bool:
    """Whether the launcher carries file capabilities (cleared when it is replaced)."""
    try:
        return bool(os.getxattr(path, "security.capability"))
    except (OSError, AttributeError):
        return False


def read_manifest(path: str = MANIFEST) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def state(helpers_dir: Optional[str] = None, root: str = ROOT) -> dict[str, Any]:
    """What is installed in ``root``.

    ``captureReady``: the launcher has its capabilities. ``helpersOutdated``:
    the plugin ships newer helpers than the installed ones (after a plugin
    update), so a repair installs them.
    """
    launcher = os.path.join(root, "bin", "eclipse-sunshine")
    installed = os.path.isfile(launcher) and os.path.isfile(os.path.join(root, "bin", "sunshine"))
    if not installed:
        return {"installed": False, "version": None, "captureReady": False, "helpersOutdated": False}
    outdated = False
    if helpers_dir:
        installed_paths = {"eclipse-sunshine": launcher, "libeclipse-sunshine.so": os.path.join(root, "lib", "libeclipse-sunshine.so")}
        for name in HELPERS:
            shipped = file_sha256(os.path.join(helpers_dir, name))
            if shipped and shipped != file_sha256(installed_paths[name]):
                outdated = True
    return {
        "installed": True,
        "version": read_manifest(os.path.join(root, "eclipse.json")).get("version"),
        "captureReady": has_capabilities(launcher),
        "helpersOutdated": outdated,
    }


def unit_text() -> str:
    """systemd user service running Sunshine for Gaming Mode."""
    return "\n".join([
        "# Written by the Eclipse Decky plugin (Sunshine for Gaming Mode).",
        "[Unit]",
        "Description=Sunshine for Gaming Mode (Eclipse)",
        "StartLimitIntervalSec=500",
        "StartLimitBurst=5",
        "",
        "[Service]",
        f"ExecStart={LAUNCHER}",
        # Gaming Mode has no desktop session for Sunshine's tray icon.
        "Environment=QT_QPA_PLATFORM=offscreen",
        "Restart=on-failure",
        "RestartSec=5s",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def prepare_config(text: str) -> str:
    """Sunshine config (sunshine.conf) adjusted for Gaming Mode.

    * ``system_tray = disabled``: without a desktop session a tray icon can
      make Sunshine exit at startup.
    * Drops the gamescope composition prep command of earlier test scripts:
      the preload library makes it unnecessary. Other prep commands stay.
    """
    lines = []
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        name = key.strip() if sep else ""
        if name == "system_tray":
            continue
        if name == "global_prep_cmd":
            try:
                commands = json.loads(value.strip() or "[]")
            except ValueError:
                lines.append(line)
                continue
            if isinstance(commands, list):
                kept = [c for c in commands if "GAMESCOPE_COMPOSITE_FORCE" not in json.dumps(c)]
                if kept != commands:
                    if kept:
                        lines.append("global_prep_cmd = " + json.dumps(kept, separators=(",", ":")))
                    continue
        lines.append(line)
    lines.append("system_tray = disabled")
    return "\n".join(lines) + "\n"
