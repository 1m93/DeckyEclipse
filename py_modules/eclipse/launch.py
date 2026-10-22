"""Launch requests shared between the Decky backend and the Steam launcher.

Gaming Mode only shows windows of processes started by Steam, so Moonlight
cannot be spawned from the plugin backend directly. Instead the backend
writes a one-shot request file and the frontend launches a non-Steam
shortcut that runs ``bin/eclipse_launcher.py``, which consumes the request
and ``exec``s Moonlight.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Optional

from . import REQUEST_FILE, REQUEST_MAX_AGE_S
from .moonlight import build_stream_args
from .presets import effective_stream

ACTIONS = ("stream", "pair", "quit", "gui")


def write_request(settings_dir: str, request: dict[str, Any]) -> None:
    if request.get("action") not in ACTIONS:
        raise ValueError(f"Unknown launch action: {request.get('action')}")
    os.makedirs(settings_dir, exist_ok=True)
    payload = {**request, "createdAt": time.time()}
    fd, tmp = tempfile.mkstemp(prefix=".request-", dir=settings_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, os.path.join(settings_dir, REQUEST_FILE))


def consume_request(settings_dir: str) -> Optional[dict[str, Any]]:
    """Read and delete the pending request. Stale or invalid requests are dropped."""
    path = os.path.join(settings_dir, REQUEST_FILE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            request = json.load(fh)
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not isinstance(request, dict) or request.get("action") not in ACTIONS:
        return None
    if time.time() - float(request.get("createdAt") or 0) > REQUEST_MAX_AGE_S:
        return None
    return request


def build_argv(command: list[str], request: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """Build the full Moonlight argv for a request."""
    action = request.get("action")
    if action == "stream":
        stream = effective_stream(settings, request.get("hostUuid"))
        return [*command, "stream", request["host"], request["app"], *build_stream_args(stream)]
    if action == "pair":
        argv = [*command, "pair", request["host"]]
        if request.get("pin"):
            argv += ["--pin", str(request["pin"])]
        return argv
    if action == "quit":
        return [*command, "quit", request["host"]]
    return list(command)
