"""Sunshine's data directory on this device: moving it, and repairing paired clients.

Two problems are handled here:

* Binaries patched by earlier Eclipse versions kept Sunshine's data in
  ``~/.config/pse-sun`` instead of ``~/.config/sunshine`` (see
  sunshine_native.py). ``migrate`` moves it back, with Sunshine stopped, so the
  login, paired devices and server certificate are kept.
* Sunshine 2026.914 adds a new record each time a device pairs again, without
  dropping the old one for the same certificate. With two records for one
  certificate, Sunshine rejects that device ("Client certificate identity is
  not enabled", Error 401 in Moonlight). ``duplicate_clients`` finds the older
  records; they are removed from the file (Sunshine stopped) or through the
  API (Sunshine running).

Runs as the normal user, standard library only.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Optional

from . import sunshine_native as native

STATE_FILE = "sunshine_state.json"
CONFIG_FILE = "sunshine.conf"
# Files that lost a conflict while moving are kept here, inside the data directory.
BACKUP_DIR = "moved-by-eclipse"


def config_dir(home: str) -> str:
    return os.path.join(home, native.CONFIG_DIR)


def state_path(home: str) -> str:
    return os.path.join(config_dir(home), STATE_FILE)


def _cert_key(cert: Any) -> str:
    return "".join(str(cert or "").split())


def _devices(state: Any) -> list[dict[str, Any]]:
    root = state.get("root") if isinstance(state, dict) else None
    devices = root.get("named_devices") if isinstance(root, dict) else None
    return [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []


def duplicate_clients(state: Any) -> list[str]:
    """UUIDs of paired-client records whose certificate has a newer record (Sunshine appends)."""
    newest: dict[str, str] = {}
    for device in _devices(state):
        key = _cert_key(device.get("cert"))
        if key and device.get("uuid"):
            newest[key] = str(device["uuid"])
    return [
        str(device["uuid"])
        for device in _devices(state)
        if device.get("uuid") and newest.get(_cert_key(device.get("cert"))) not in (None, str(device["uuid"]))
    ]


def read_state(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def dedupe_state_file(path: str) -> int:
    """Drop duplicate paired-client records from the state file (Sunshine must be stopped)."""
    state = read_state(path)
    drop = set(duplicate_clients(state)) if state else set()
    if not state or not drop:
        return 0
    state["root"]["named_devices"] = [d for d in _devices(state) if str(d.get("uuid")) not in drop]
    temp = path + ".eclipse-tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=4)
    os.chmod(temp, os.stat(path).st_mode & 0o777)
    os.replace(temp, path)
    return len(drop)


def _keep_aside(path: str, target_dir: str, origin: str) -> None:
    backup = os.path.join(target_dir, BACKUP_DIR, origin)
    os.makedirs(backup, exist_ok=True)
    os.replace(path, os.path.join(backup, os.path.basename(path)))


def migrate(home: str) -> list[str]:
    """Move Sunshine's data from the legacy directories back to ``~/.config/sunshine``.

    Sunshine must be stopped. Where both have a file, the newer one wins and the
    other is kept in ``moved-by-eclipse``. Returns what was done, for the log.
    """
    target = config_dir(home)
    done: list[str] = []
    for relative in native.LEGACY_CONFIG_DIRS:
        legacy = os.path.join(home, relative)
        if os.path.islink(legacy) or not os.path.isdir(legacy):
            continue
        os.makedirs(target, exist_ok=True)
        origin = os.path.basename(legacy)
        for name in os.listdir(legacy):
            source = os.path.join(legacy, name)
            dest = os.path.join(target, name)
            if name == CONFIG_FILE and os.path.isfile(dest):
                # The legacy config holds the user's settings; Eclipse's copy only its own lines,
                # which are added again afterwards (prepare_config).
                _keep_aside(dest, target, "sunshine")
            elif os.path.lexists(dest):
                newer_source = os.lstat(source).st_mtime >= os.lstat(dest).st_mtime
                if newer_source:
                    _keep_aside(dest, target, "sunshine")
                else:
                    _keep_aside(source, target, origin)
                    continue
            os.replace(source, dest)
        try:
            os.rmdir(legacy)
        except OSError:
            # Something appeared meanwhile: leave it, nothing is lost.
            pass
        done.append(f"Moved Sunshine's data from {legacy} to {target}")
    dropped = dedupe_state_file(state_path(home)) if os.path.isfile(state_path(home)) else 0
    if dropped:
        done.append(f"Removed {dropped} duplicate paired-device record(s)")
    return done


def remove_all(home: str) -> None:
    """Delete Sunshine's data, in every place it may be."""
    for relative in (native.CONFIG_DIR, *native.LEGACY_CONFIG_DIRS):
        path = os.path.join(home, relative)
        if os.path.islink(path):
            os.unlink(path)
        else:
            shutil.rmtree(path, ignore_errors=True)
