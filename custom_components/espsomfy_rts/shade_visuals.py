"""Shade type visuals for HA (icons + picture paths), aligned with Somfy UI types."""

from __future__ import annotations

from homeassistant.components.cover import CoverDeviceClass

# Somfy shadeType → (open, closed, partial) MDI icons
_TYPE_ICONS: dict[int, tuple[str, str, str]] = {
    0: ("mdi:roller-shade", "mdi:roller-shade-closed", "mdi:roller-shade"),
    1: ("mdi:blinds-horizontal", "mdi:blinds-horizontal-closed", "mdi:blinds-horizontal"),
    2: ("mdi:curtains", "mdi:curtains-closed", "mdi:curtains"),
    3: ("mdi:storefront", "mdi:storefront-outline", "mdi:storefront"),
    4: ("mdi:window-shutter-open", "mdi:window-shutter", "mdi:window-shutter-open"),
    5: ("mdi:garage-open", "mdi:garage", "mdi:garage-open-variant"),
    6: ("mdi:garage-open", "mdi:garage", "mdi:garage-open-variant"),
    7: ("mdi:curtains", "mdi:curtains-closed", "mdi:curtains"),
    8: ("mdi:curtains", "mdi:curtains-closed", "mdi:curtains"),
    11: ("mdi:gate-open", "mdi:gate", "mdi:gate-arrow-left"),
    12: ("mdi:gate-open", "mdi:gate", "mdi:gate"),
    13: ("mdi:gate-open", "mdi:gate", "mdi:gate-arrow-right"),
    14: ("mdi:gate-open", "mdi:gate", "mdi:gate-arrow-left"),
    15: ("mdi:gate-open", "mdi:gate", "mdi:gate"),
    16: ("mdi:gate-open", "mdi:gate", "mdi:gate-arrow-right"),
}

# Picture stem under /espsomfy_rts/images/{stem}_{open|closed|partial}.svg
_TYPE_PICTURE: dict[int, str] = {
    0: "shade",
    1: "blind",
    2: "curtain",
    3: "awning",
    4: "shutter",
    5: "garage",
    6: "garage",
    7: "curtain",
    8: "curtain",
    11: "gate",
    12: "gate",
    13: "gate",
    14: "gate",
    15: "gate",
    16: "gate",
}


def ha_position_from_esp(
    esp_position: int | float | None,
    *,
    flip_position: bool,
    device_class: CoverDeviceClass | None = None,
) -> int | None:
    """Map ESP API % into HA cover % (100 = open · 0 = closed).

    Firmware v2.5.13-fc+ API already uses HA scale by default. ``flipPosition``
    means the API is inverted from that standard (apply 100−% once).
    """
    del device_class  # API scale is unified across types after transformPosition
    if esp_position is None:
        return None
    try:
        pos = int(esp_position)
    except (TypeError, ValueError):
        return None
    return (100 - pos) if flip_position else pos


def esp_position_from_ha(
    ha_position: int | float | None,
    *,
    flip_position: bool,
    device_class: CoverDeviceClass | None = None,
) -> int | None:
    """Map HA cover % back to ESP API % (symmetric with ha_position_from_esp)."""
    return ha_position_from_esp(
        ha_position, flip_position=flip_position, device_class=device_class
    )


visual_openness_from_esp = ha_position_from_esp


def visual_state(
    *,
    ha_position: int | None,
    is_opening: bool,
    is_closing: bool,
    is_closed: bool,
) -> str:
    """Return open / closed / partial for icons and pictures.

    ``ha_position`` here is openness (100 = open), not raw ESP %.
    """
    if is_opening:
        return "open"
    if is_closing:
        return "closed"
    if is_closed:
        return "closed"
    if ha_position is not None:
        if ha_position >= 95:
            return "open"
        if ha_position <= 5:
            return "closed"
        return "partial"
    return "partial"


def shade_mdi_icon(
    shade_type: int,
    *,
    ha_position: int | None,
    is_opening: bool,
    is_closing: bool,
    is_closed: bool,
) -> str:
    """MDI icon matching Somfy shade type and open/closed state."""
    open_i, closed_i, partial_i = _TYPE_ICONS.get(
        shade_type, ("mdi:window-open", "mdi:window-closed", "mdi:window-open-variant")
    )
    state = visual_state(
        ha_position=ha_position,
        is_opening=is_opening,
        is_closing=is_closing,
        is_closed=is_closed,
    )
    if state == "open":
        return open_i
    if state == "closed":
        return closed_i
    return partial_i


def shade_entity_picture(
    shade_type: int,
    *,
    ha_position: int | None,
    is_opening: bool,
    is_closing: bool,
    is_closed: bool,
    url_prefix: str,
) -> str:
    """URL for a simple window illustration (open / closed / partial)."""
    stem = _TYPE_PICTURE.get(shade_type, "shade")
    state = visual_state(
        ha_position=ha_position,
        is_opening=is_opening,
        is_closing=is_closing,
        is_closed=is_closed,
    )
    return f"{url_prefix}/{stem}_{state}.svg"
