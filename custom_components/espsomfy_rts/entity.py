"""ESPSomfy parent entity class."""

from __future__ import annotations

import logging

from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, VERSION
from .controller import ESPSomfyController

_LOGGER = logging.getLogger(__name__)


class ESPSomfyEntity(CoordinatorEntity[ESPSomfyController], Entity):
    """Base entitly for the ESPSomfy controller."""

    _entity_id_suffix: str | None = None

    def __init__(self, *, data: any, controller: ESPSomfyController) -> None:
        """Initialize the entity."""
        super().__init__(coordinator=controller)
        self.controller = controller
        self._data = data

    @property
    def should_poll(self) -> bool:
        """Indicates that the entity should not poll."""
        return False

    async def async_added_to_hass(self) -> None:
        """Register entity and sync room-based identity."""
        await super().async_added_to_hass()
        # Defer so the entity finishes registering before entity_id changes.
        self.hass.async_create_task(self._async_sync_room_identity())

    async def _async_sync_room_identity(self) -> None:
        """Apply device name and suggested area from the controller."""
        if not self._data or (
            "shadeId" not in self._data
            and "groupId" not in self._data
            and "roomId" not in self._data
        ):
            return

        api = self.controller.api
        desired_name = api.format_entity_name(self._data)
        # Feature entities (config numbers/switches) keep their own name via
        # translation_key / explicit _attr_name. Overwriting with the device
        # title makes every row on the device page read as the shade name.
        if not self.has_entity_name:
            self._attr_name = desired_name
        room_name = api.get_room_name(api.get_room_id(self._data))

        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(self.entity_id)
        if entry is None:
            return

        # Clear a stale registry name that previously copied the device title
        # onto has_entity_name feature entities.
        if (
            self.has_entity_name
            and entry.name
            and entry.name == desired_name
        ):
            ent_reg.async_update_entity(entry.entity_id, name=None)
            entry = ent_reg.async_get(self.entity_id) or entry

        if entry.device_id:
            device_reg = dr.async_get(self.hass)
            device = device_reg.async_get(entry.device_id)
            device_kwargs: dict = {"name": desired_name}
            if room_name and device is not None and device.area_id is None:
                area_reg = ar.async_get(self.hass)
                area = area_reg.async_get_or_create(room_name)
                device_kwargs["area_id"] = area.id
            device_reg.async_update_device(entry.device_id, **device_kwargs)

        domain = self.entity_id.split(".", 1)[0]
        object_id = api.suggest_object_id(self._data, self._entity_id_suffix)
        desired_entity_id = f"{domain}.{object_id}"

        if entry.entity_id == desired_entity_id:
            self.async_write_ha_state()
            return

        if ent_reg.async_get(desired_entity_id) is not None:
            suffix = str(entry.unique_id).rsplit("_", maxsplit=1)[-1]
            desired_entity_id = f"{domain}.{object_id}_{suffix}"
            if (
                entry.entity_id == desired_entity_id
                or ent_reg.async_get(desired_entity_id) is not None
            ):
                self.async_write_ha_state()
                return

        try:
            ent_reg.async_update_entity(
                entry.entity_id, new_entity_id=desired_entity_id
            )
            _LOGGER.info("Renamed %s -> %s", entry.entity_id, desired_entity_id)
        except ValueError as err:
            _LOGGER.warning(
                "Could not rename %s to %s: %s",
                entry.entity_id,
                desired_entity_id,
                err,
            )
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo | None:
        """Device info."""
        api = self.controller.api
        if self._data and "groupId" in self._data:
            group_id = self._data["groupId"]
            room_name = api.get_room_name(api.get_room_id(self._data))
            return DeviceInfo(
                identifiers={
                    (DOMAIN, f"group_{self.controller.unique_id}_{group_id}")
                },
                name=api.format_entity_name(self._data),
                manufacturer=MANUFACTURER,
                model="ESPSomfy RTS Group",
                via_device=(DOMAIN, self.controller.unique_id),
                suggested_area=room_name,
            )

        if (
            self._data
            and "roomId" in self._data
            and "shadeId" not in self._data
            and "groupId" not in self._data
        ):
            room_id = self._data["roomId"]
            room_name = api.get_room_name(api.get_room_id(self._data))
            return DeviceInfo(
                identifiers={
                    (DOMAIN, f"room_{self.controller.unique_id}_{room_id}")
                },
                name=api.format_entity_name(self._data),
                manufacturer=MANUFACTURER,
                model="ESPSomfy RTS Room",
                via_device=(DOMAIN, self.controller.unique_id),
                suggested_area=room_name,
            )

        if self._data and "shadeId" in self._data:
            shade_id = self._data["shadeId"]
            room_name = api.get_room_name(api.get_room_id(self._data))
            return DeviceInfo(
                identifiers={
                    (DOMAIN, f"shade_{self.controller.unique_id}_{shade_id}")
                },
                name=api.format_entity_name(self._data),
                manufacturer=MANUFACTURER,
                model="ESPSomfy RTS Device",
                via_device=(DOMAIN, self.controller.unique_id),
                suggested_area=room_name,
            )

        # Fixed-code RF switch payload uses "id" (not shadeId/groupId).
        if (
            self._data
            and "id" in self._data
            and "shadeId" not in self._data
            and "groupId" not in self._data
        ):
            fc_id = self._data["id"]
            return DeviceInfo(
                identifiers={
                    (DOMAIN, f"fixedcode_{self.controller.unique_id}_{fc_id}")
                },
                name=self._data.get("name") or f"RF Switch {fc_id}",
                manufacturer=MANUFACTURER,
                model="ESPSomfy Fixed-Code RF Switch",
                via_device=(DOMAIN, self.controller.unique_id),
            )

        return DeviceInfo(
            configuration_url=self.controller.api.get_config_url(),
            identifiers={(DOMAIN, self.controller.unique_id)},
            name=self.controller.device_name,
            manufacturer=MANUFACTURER,
            model=f"ESPSomfy RTS Integration {VERSION}",
            sw_version=self.controller.version,
            hw_version=None,
        )
