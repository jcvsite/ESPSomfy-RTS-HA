"""Shared helpers for ESPSomfy RTS HA."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from packaging.version import InvalidVersion, Version, parse as version_parse

from .const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_ALWAYS,
    DEFAULT_CONTROL_MODE,
)


def as_bool(value: Any) -> bool:
    """Parse device JSON bools that may arrive as bool, 0/1, or strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def parse_firmware_version(version: str | None) -> Version:
    """Parse firmware strings that may include a leading v or build suffix.

    Device/API versions can look like ``v2.5.11-fc``, which is not PEP 440.
    Feature gates only need major/minor/micro, so normalize to a parseable form.
    """
    if not version:
        return Version("0.0.0")
    text = str(version).strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    try:
        return version_parse(text)
    except InvalidVersion:
        pass
    # e.g. 2.5.11-fc / 2.5.11_dev → 2.5.11+fc / 2.5.11+dev
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-_](.+))?$", text)
    if match:
        base, suffix = match.group(1), match.group(2)
        if suffix:
            local = re.sub(r"[^0-9A-Za-z.]+", ".", suffix).strip(".")
            if local:
                try:
                    return version_parse(f"{base}+{local}")
                except InvalidVersion:
                    pass
        try:
            return version_parse(base)
        except InvalidVersion:
            pass
    return Version("0.0.0")


def controls_always_enabled(entry: ConfigEntry | None) -> bool:
    """Return True when cover controls stay enabled regardless of status."""
    if entry is None:
        return True
    mode = entry.options.get(
        CONF_CONTROL_MODE,
        entry.data.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
    )
    return mode == CONTROL_MODE_ALWAYS


def room_shades(shades: list[dict] | None, room_id: int) -> list[dict]:
    """Return motor shades in a room (skip dry-contact types 9/10)."""
    out: list[dict] = []
    for shade in shades or []:
        try:
            if int(shade.get("roomId") or 0) != int(room_id):
                continue
            if int(shade.get("shadeType", 0)) in (9, 10):
                continue
            out.append(shade)
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def room_all_awnings(shades: list[dict] | None, room_id: int) -> bool:
    """True when every motor shade in the room is an awning (type 3)."""
    members = room_shades(shades, room_id)
    return bool(members) and all(int(s.get("shadeType", 0)) == 3 for s in members)


def room_rf_command(
    shades: list[dict] | None, room_id: int, command: str
) -> str:
    """Map HA open/close to RF up/down for a room.

    Firmware ``/roomCommand`` sends literal Up/Down with no awning swap.
    Per-shade covers swap awning open↔close; rooms must do the same when
    every member is an awning. Explicit ``up``/``down`` stay literal RF.
    """
    cmd = (command or "").strip().lower()
    if cmd in ("open", "close"):
        awning = room_all_awnings(shades, room_id)
        if cmd == "open":
            return "down" if awning else "up"
        return "up" if awning else "down"
    return cmd
