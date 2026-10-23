"""Options of the Sunshine on this device, read from and written to sunshine.conf.

Sunshine reads its config file only when it starts, so a change applies after
a restart. Editing the file directly works without a Sunshine login and keeps
every line Eclipse does not know about (apps, prep commands, monitor choice).

Only options that make sense for a handheld sharing its own screen are
offered; see https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html.
A value equal to Sunshine's default is removed from the file instead of being
written, so the file only holds what the user changed.
"""

from __future__ import annotations

from typing import Any, Optional

# kind: "bool" | "int" | "choice" | "str". Ints may have a range, choices a list of values.
OPTIONS: dict[str, dict[str, Any]] = {
    # General
    "sunshine_name": {"kind": "str", "default": ""},
    "min_log_level": {
        "kind": "choice",
        "default": "info",
        "choices": ["verbose", "debug", "info", "warning", "error", "fatal", "none"],
    },
    # Video
    "encoder": {"kind": "choice", "default": "", "choices": ["", "vaapi", "vulkan", "software"]},
    "max_bitrate": {"kind": "int", "default": 0, "min": 0, "max": 500000},
    "minimum_fps_target": {"kind": "int", "default": 0, "min": 0, "max": 1000},
    "hevc_mode": {"kind": "int", "default": 0, "min": 0, "max": 3},
    "av1_mode": {"kind": "int", "default": 0, "min": 0, "max": 3},
    "fec_percentage": {"kind": "int", "default": 20, "min": 0, "max": 100},
    # Audio
    "stream_audio": {"kind": "bool", "default": True},
    # Input
    "controller": {"kind": "bool", "default": True},
    # The types Sunshine can emulate on Linux.
    "gamepad": {"kind": "choice", "default": "auto", "choices": ["auto", "xone", "ds5", "switch"]},
    "motion_as_ds4": {"kind": "bool", "default": True},
    "touchpad_as_ds4": {"kind": "bool", "default": True},
    "back_button_timeout": {"kind": "int", "default": -1, "min": -1, "max": 10000},
    "keyboard": {"kind": "bool", "default": True},
    "mouse": {"kind": "bool", "default": True},
    "high_resolution_scrolling": {"kind": "bool", "default": True},
    "native_pen_touch": {"kind": "bool", "default": True},
    # Network
    "upnp": {"kind": "bool", "default": False},
    "address_family": {"kind": "choice", "default": "ipv4", "choices": ["ipv4", "both"]},
    "origin_web_ui_allowed": {"kind": "choice", "default": "lan", "choices": ["pc", "lan", "wan"]},
    "lan_encryption_mode": {"kind": "int", "default": 0, "min": 0, "max": 2},
    "wan_encryption_mode": {"kind": "int", "default": 1, "min": 0, "max": 2},
    "ping_timeout": {"kind": "int", "default": 10000, "min": 1000, "max": 120000},
}

# Spellings Sunshine accepts for booleans (config.cpp, to_bool).
_TRUE = {"true", "yes", "enable", "enabled", "on", "1"}
_FALSE = {"false", "no", "disable", "disabled", "off", "0"}


def defaults() -> dict[str, Any]:
    return {key: spec["default"] for key, spec in OPTIONS.items()}


def _parse(key: str, raw: str) -> Optional[Any]:
    """Value of ``key`` as written in the file, or None when Sunshine would not accept it either."""
    spec = OPTIONS[key]
    # Sunshine ignores the rest of a line after "#".
    raw = raw.partition("#")[0].strip()
    if spec["kind"] == "bool":
        lowered = raw.lower()
        return True if lowered in _TRUE else False if lowered in _FALSE else None
    if spec["kind"] == "int":
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if spec.get("min", value) <= value <= spec.get("max", value) else None
    if spec["kind"] == "choice":
        return raw if raw in spec["choices"] else None
    return raw


def validate(key: str, value: Any) -> Any:
    """Normalized ``value`` for ``key``; raises ValueError when it is not allowed."""
    spec = OPTIONS.get(key)
    if spec is None:
        raise ValueError(f"Unknown Sunshine option: {key}")
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be on or off")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"{key} must be a whole number")
        value = int(value)
        if not spec.get("min", value) <= value <= spec.get("max", value):
            raise ValueError(f"{key} must be between {spec['min']} and {spec['max']}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} must be a single line")
    if kind == "choice" and value not in spec["choices"]:
        raise ValueError(f"{key} cannot be {value!r}")
    if kind == "str" and len(value) > 64:
        raise ValueError(f"{key} is too long")
    # Sunshine reads "#" as the start of a comment and a leading "[" as a list over several lines.
    if kind == "str" and ("#" in value or value.startswith("[")):
        raise ValueError(f"{key} cannot contain # or start with [")
    return value


def _format(key: str, value: Any) -> str:
    if OPTIONS[key]["kind"] == "bool":
        return "enabled" if value else "disabled"
    return str(value)


def _key_of(line: str) -> Optional[str]:
    if line.lstrip().startswith("#"):
        return None
    key, sep, _ = line.partition("=")
    return key.strip() if sep else None


def read(text: str) -> dict[str, Any]:
    """Every known option: the file's value, or Sunshine's default when unset or invalid."""
    values = defaults()
    for line in text.splitlines():
        key = _key_of(line)
        if key in OPTIONS:
            parsed = _parse(key, line.partition("=")[2])
            # Sunshine uses the last occurrence, like this loop.
            values[key] = OPTIONS[key]["default"] if parsed is None else parsed
    return values


def apply(text: str, patch: dict[str, Any]) -> str:
    """``text`` with the options of ``patch`` changed. Raises ValueError for an invalid value."""
    changes = {key: validate(key, value) for key, value in patch.items()}
    lines: list[str] = []
    written: set[str] = set()
    for line in text.splitlines():
        key = _key_of(line)
        if key not in changes:
            lines.append(line)
            continue
        # One line per option, in the place of its first occurrence; defaults are dropped.
        if key not in written and changes[key] != OPTIONS[key]["default"]:
            lines.append(f"{key} = {_format(key, changes[key])}")
        written.add(key)
    for key, value in changes.items():
        if key not in written and value != OPTIONS[key]["default"]:
            lines.append(f"{key} = {_format(key, value)}")
    return "\n".join(lines) + "\n" if lines else ""
