"""Number entities for ESPSomfy shade travel times and position calibrate."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVT_CONNECTED, EVT_SHADEADDED, EVT_SHADESTATE
from .controller import ESPSomfyController
from .entity import ESPSomfyEntity
from .helpers import as_bool
from .shade_visuals import esp_position_from_ha, ha_position_from_esp

_DRY_CONTACT_TYPES = {9, 10}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up travel-time and calibrate-position numbers for each shade."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    new_entities: list[NumberEntity] = []
    known_ids: set[int] = set()
    data = controller.api.get_config()
    if "serverId" not in data:
        return

    for shade in controller.api.shades:
        try:
            shade_type = int(shade.get("shadeType", -1))
            shade_id = int(shade["shadeId"])
        except (KeyError, TypeError, ValueError):
            continue
        if shade_type in _DRY_CONTACT_TYPES:
            continue
        known_ids.add(shade_id)
        new_entities.extend(
            [
                ESPSomfyUpTimeNumber(controller, shade),
                ESPSomfyDownTimeNumber(controller, shade),
                ESPSomfyCalibratePositionNumber(controller, shade),
            ]
        )

    def _on_shade_added() -> None:
        evt = controller.data.get("event")
        if evt != EVT_SHADEADDED:
            return
        payload = controller.data
        try:
            if int(payload.get("shadeType", -1)) in _DRY_CONTACT_TYPES:
                return
            shade_id = int(payload["shadeId"])
        except (KeyError, TypeError, ValueError):
            return
        if shade_id in known_ids:
            return
        known_ids.add(shade_id)
        async_add_entities(
            [
                ESPSomfyUpTimeNumber(controller, payload),
                ESPSomfyDownTimeNumber(controller, payload),
                ESPSomfyCalibratePositionNumber(controller, payload),
            ]
        )

    config_entry.async_on_unload(controller.async_add_listener(_on_shade_added))
    if new_entities:
        async_add_entities(new_entities)


class ESPSomfyTravelTimeNumber(ESPSomfyEntity, NumberEntity):
    """Base travel-time number synced to the ESP (seconds in HA, ms on device)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 1
    _attr_native_max_value = 600
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _setting_key: str

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize travel time number."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = int(data["shadeId"])
        self._available = True
        self._attr_native_value = self._ms_to_seconds(data.get(self._setting_key, 10000))

    @staticmethod
    def _ms_to_seconds(value) -> float:
        try:
            return max(1.0, round(float(value) / 1000.0))
        except (TypeError, ValueError):
            return 10.0

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        event = self._controller.data.get("event")
        if event == EVT_CONNECTED and "connected" in self._controller.data:
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
            return
        if (
            event == EVT_SHADESTATE
            and self._controller.data.get("shadeId") == self._shade_id
            and self._setting_key in self._controller.data
        ):
            self._attr_native_value = self._ms_to_seconds(
                self._controller.data[self._setting_key]
            )
            self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Push travel time to the ESP (ESP stops a move first if needed)."""
        ms = int(round(value * 1000))
        ok = await self._controller.async_update_shade_settings(
            self._shade_id, {self._setting_key: ms}
        )
        if not ok:
            raise HomeAssistantError(
                "Could not update travel time on the ESPSomfy controller."
            )
        self._attr_native_value = float(int(value))
        self.async_write_ha_state()


class ESPSomfyUpTimeNumber(ESPSomfyTravelTimeNumber):
    """Full-open travel time (ESP upTime)."""

    _entity_id_suffix = "open_travel_time"
    _attr_translation_key = "open_travel_time"
    _attr_name = "Full open time"
    _attr_icon = "mdi:timer-outline"
    _setting_key = "upTime"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize open travel time."""
        super().__init__(controller, data)
        self._attr_unique_id = f"uptime_{controller.unique_id}_{self._shade_id}"


class ESPSomfyDownTimeNumber(ESPSomfyTravelTimeNumber):
    """Full-close travel time (ESP downTime)."""

    _entity_id_suffix = "close_travel_time"
    _attr_translation_key = "close_travel_time"
    _attr_name = "Full close time"
    _attr_icon = "mdi:timer-outline"
    _setting_key = "downTime"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize close travel time."""
        super().__init__(controller, data)
        self._attr_unique_id = f"downtime_{controller.unique_id}_{self._shade_id}"


class ESPSomfyCalibratePositionNumber(ESPSomfyEntity, NumberEntity):
    """Slider to set reported position without moving the motor (calibrate)."""

    _entity_id_suffix = "calibrate_position"
    _attr_translation_key = "calibrate_position"
    _attr_name = "Calibrate position (100%=open)"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:arrow-collapse-horizontal"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize calibrate position slider."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = int(data["shadeId"])
        self._flip_position = as_bool(data.get("flipPosition", False))
        self._esp_position = int(data.get("position", 0))
        self._available = True
        self._attr_unique_id = f"calpos_{controller.unique_id}_{self._shade_id}"
        # Calibrate uses same HA scale as covers (100%=open · 0%=closed).
        ha = ha_position_from_esp(
            self._esp_position, flip_position=self._flip_position
        )
        self._attr_native_value = float(ha if ha is not None else self._esp_position)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    def _handle_coordinator_update(self) -> None:
        """Keep slider in sync with ESP API position (HA scale)."""
        event = self._controller.data.get("event")
        if event == EVT_CONNECTED and "connected" in self._controller.data:
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
            return
        if (
            event != EVT_SHADESTATE
            or self._controller.data.get("shadeId") != self._shade_id
        ):
            return
        data = self._controller.data
        if "flipPosition" in data:
            self._flip_position = as_bool(data.get("flipPosition", False))
        if "position" in data:
            self._esp_position = int(data["position"])
        ha = ha_position_from_esp(
            self._esp_position, flip_position=self._flip_position
        )
        self._attr_native_value = float(ha if ha is not None else self._esp_position)
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Calibrate using HA scale; convert like cover set_current_position."""
        api_pos = esp_position_from_ha(
            int(round(value)), flip_position=self._flip_position
        )
        if api_pos is None:
            raise HomeAssistantError("Invalid calibrate position.")
        ok = await self._controller.async_set_current_position(self._shade_id, api_pos)
        if not ok:
            raise HomeAssistantError(
                "Could not calibrate position on the ESPSomfy controller."
            )
        self._esp_position = api_pos
        self._attr_native_value = float(round(value))
        self.async_write_ha_state()
