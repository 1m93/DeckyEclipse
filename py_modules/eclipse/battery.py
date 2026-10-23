"""Battery of this device, for turning Sunshine off when it runs low.

Sharing keeps the GPU encoding, which drains a handheld quickly. With the
``batteryGuard`` option set, Sunshine turns off once the battery falls to that
level while not charging (after the current stream, when one is running).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

POWER_SUPPLY = "/sys/class/power_supply"
# Hysteresis: after acting, the guard re-arms once the battery is this much above the level.
REARM_MARGIN = 5


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def read(root: str = POWER_SUPPLY) -> Optional[dict[str, object]]:
    """``{"percent": int, "charging": bool}`` of the first system battery, or None without one."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        base = os.path.join(root, name)
        if _read(os.path.join(base, "type")) != "Battery" or _read(os.path.join(base, "scope")) == "Device":
            continue
        capacity = _read(os.path.join(base, "capacity"))
        if not capacity or not capacity.isdigit():
            continue
        status = (_read(os.path.join(base, "status")) or "").lower()
        return {"percent": int(capacity), "charging": status in ("charging", "full", "not charging")}
    return None


# Below this, Sunshine turns off even during a stream: the device is about to shut down.
FLOOR_PERCENT = 5


@dataclass
class BatteryGuard:
    """Decides when the battery is too low to keep sharing.

    When Sunshine starts (``fresh``), the guard arms only if the battery is
    above the level, so Sunshine started on purpose at a low battery is left
    on. After acting, it re-arms once charging or clearly above the level.
    During a stream it warns once and acts when the stream ends, unless the
    battery reaches the floor.
    """

    armed: bool = False
    warned: bool = False
    fresh: bool = True

    def reset(self) -> None:
        """Sunshine stopped: the next start is judged anew."""
        self.armed = self.warned = False
        self.fresh = True

    def update(self, level: Optional[int], battery: Optional[dict[str, object]], clients: int) -> Optional[str]:
        """Returns "stop" (turn Sunshine off now), "warn" (tell the user, stop after the stream) or None."""
        if not level or not battery:
            # Turned on later, the guard judges the battery as at a start.
            self.reset()
            return None
        percent = int(battery["percent"])  # type: ignore[call-overload]
        charging = bool(battery["charging"])
        if self.fresh:
            self.fresh = False
            self.armed = charging or percent > level
        if charging or percent >= level + REARM_MARGIN:
            self.armed = True
            self.warned = False
            return None
        if not self.armed or percent > level:
            return None
        if clients > 0 and percent > FLOOR_PERCENT:
            if self.warned:
                return None
            self.warned = True
            return "warn"
        self.armed = self.warned = False
        return "stop"
