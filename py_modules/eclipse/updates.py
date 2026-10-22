"""Check GitHub Releases for a newer version of the plugin.

The repository comes from the ``repository`` field of the packaged
``package.json``. Releases are expected to be tagged with the version
(``v1.2.3`` or ``1.2.3``) and to carry the plugin zip as an asset.

Installing the update is left to Decky (store, or *Install from URL* in
developer mode). The plugin directory is owned by root, so the plugin
cannot replace its own files.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any, Optional

_GITHUB_RE = re.compile(r"(?:github:|github\.com[/:])([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
# Bundled Python builds may not find the system CA store on their own.
_CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")
_MAX_NOTES = 1500


def read_package(plugin_dir: str) -> dict[str, Any]:
    try:
        with open(os.path.join(plugin_dir, "package.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_repository(value: Any) -> Optional[tuple[str, str]]:
    """Accept ``github:owner/repo``, a GitHub URL, or ``{"url": ...}``."""
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    match = _GITHUB_RE.search(value.strip())
    return (match.group(1), match.group(2)) if match else None


def version_tuple(version: str) -> tuple[int, ...]:
    """Numeric parts of a version (``v1.2.10-beta`` -> ``(1, 2, 10)``)."""
    core = version.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = []
    for piece in core.split("."):
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    for bundle in _CA_BUNDLES:
        if os.path.isfile(bundle):
            context.load_verify_locations(bundle)
            break
    return context


def fetch_json(url: str, timeout: float = 10.0) -> Any:
    """GET a GitHub API URL (blocking).

    Raises urllib.error.HTTPError, urllib.error.URLError, OSError or ValueError.
    """
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "DeckyEclipse",
    })
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as res:
        return json.loads(res.read(4 * 1024 * 1024).decode())


def describe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"GitHub returned HTTP {exc.code}"
    return f"Could not reach GitHub: {getattr(exc, 'reason', exc)}"


def check(plugin_dir: str, timeout: float = 10.0) -> dict[str, Any]:
    """Blocking check against the latest GitHub release."""
    package = read_package(plugin_dir)
    current = str(package.get("version") or "0.0.0")
    result: dict[str, Any] = {
        "ok": False, "current": current, "latest": None, "available": False,
        "url": None, "downloadUrl": None, "notes": None, "error": None,
    }
    repo = parse_repository(package.get("repository"))
    if not repo:
        result["error"] = "No GitHub repository is configured in package.json"
        return result

    url = f"https://api.github.com/repos/{repo[0]}/{repo[1]}/releases/latest"
    try:
        release = fetch_json(url, timeout)
    except urllib.error.HTTPError as exc:
        result["error"] = "No release published yet" if exc.code == 404 else describe_error(exc)
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result["error"] = describe_error(exc)
        return result

    latest = str(release.get("tag_name") or "").strip()
    zip_asset = next(
        (a for a in release.get("assets") or [] if str(a.get("name", "")).endswith(".zip")),
        None,
    )
    notes = str(release.get("body") or "").strip()
    result.update({
        "ok": True,
        "latest": latest.lstrip("vV") or None,
        "available": bool(latest) and is_newer(latest, current),
        "url": release.get("html_url"),
        "downloadUrl": zip_asset.get("browser_download_url") if zip_asset else None,
        "notes": notes[:_MAX_NOTES] if notes else None,
    })
    return result
