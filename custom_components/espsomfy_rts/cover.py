"""Support for ESPSomfy RTS Shades and Blinds."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from typing import Any, Final

import voluptuous as vol

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.group.cover import CoverGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform as ep, entity_registry as er
from homeassistant.helpers.config_validation import make_entity_service_schema
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVT_CONNECTED,
    EVT_SHADECOMMAND,
    EVT_SHADEREMOVED,
    EVT_SHADESTATE,
)
from .controller import ESPSomfyController
from .entity import ESPSomfyEntity
from .helpers import as_bool, controls_always_enabled, room_rf_command, room_shades
from .shade_visuals import (
    esp_position_from_ha,
    ha_position_from_esp,
    shade_entity_picture,
    shade_mdi_icon,
    visual_openness_from_esp,
)

SVC_OPEN_SHADE = "open_shade"
SVC_CLOSE_SHADE = "close_shade"
SVC_STOP_SHADE = "stop_shade"
SVC_SET_SHADE_POS = "set_shade_position"
SVC_TILT_OPEN = "tilt_open"
SVC_TILT_CLOSE = "tilt_close"
SVC_SET_TILT_POS = "set_tilt_position"
SVC_SET_CURRENT_POS = "set_current_position"
SVC_SET_CURRENT_TILT_POS = "set_current_tilt_position"
SVC_SET_SUNNY = "set_sunny"
SVC_SET_WINDY = "set_windy"
SVC_SEND_COMMAND = "send_command"
SVC_SEND_STEP_COMMAND = "send_step_command"

KEY_OPEN_CLOSE = "open_close"
KEY_STOP = "stop"
KEY_POSITION = "position"
ATTR_SUNNY = "sunny"
ATTR_WINDY = "windy"
ATTR_STEP_SIZE = "step_size"
ATTR_COMMAND = "command"
ATTR_DIRECTION = "direction"
ATTR_REPEAT = "repeat"

ALLOWED_COMMAND = [
    "Up",
    "My",
    "Down",
    "Toggle",
    "Prog",
    "UpDown",
    "MyUp",
    "MyDown",
    "MyUpDown",
    "StepUp",
    "StepDown",
    "Flag",
    "SunFlag",
    "Favorite",
    "Stop",
]

POSITION_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {vol.Required(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))}
)
TILT_POSITION_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {
        vol.Required(ATTR_TILT_POSITION): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        )
    }
)
SUNNY_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {vol.Required(ATTR_SUNNY): vol.All(vol.Coerce(bool))}
)
WINDY_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {vol.Required(ATTR_WINDY): vol.All(vol.Coerce(bool))}
)
SEND_COMMAND_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {
        vol.Required(ATTR_COMMAND): vol.In(ALLOWED_COMMAND),
        vol.Optional(ATTR_REPEAT): vol.Range(min=0, max=50),
    }
)
SEND_STEP_COMMAND_SERVICE_SCHEMA: Final = make_entity_service_schema(
    {
        vol.Required(ATTR_DIRECTION): vol.In(["Up", "Down"]),
        vol.Required(ATTR_STEP_SIZE): vol.Range(min=1, max=127),
        vol.Optional(ATTR_REPEAT): vol.Range(min=0, max=50),
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up shades for the shade controller."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    new_shades = []
    data = controller.api.get_config()
    if "serverId" in data:
        for shade in controller.api.shades:
            try:
                # We do not want any of the dry contacts here.
                if "shadeType" in shade and not (
                    int(shade["shadeType"]) == 9 or int(shade["shadeType"]) == 10
                ):
                    new_shades.append(ESPSomfyShade(controller, shade))

            except KeyError:
                pass
        if new_shades:
            async_add_entities(new_shades)

        new_groups = []
        for group in controller.api.groups:
            with contextlib.suppress(KeyError):
                new_groups.append(
                    ESPSomfyGroup(hass=hass, controller=controller, data=group)
                )
        if new_groups:
            async_add_entities(new_groups)

        new_rooms = []
        for room in controller.api.rooms:
            with contextlib.suppress(KeyError, TypeError, ValueError):
                rid = int(room.get("roomId") or 0)
                if not rid:
                    continue
                new_rooms.append(ESPSomfyRoom(controller=controller, data=room))
        if new_rooms:
            async_add_entities(new_rooms)

        platform = ep.async_get_current_platform()
        platform.async_register_entity_service(
            SVC_SET_SHADE_POS,
            POSITION_SERVICE_SCHEMA,
            "async_set_cover_position",
        )
        platform.async_register_entity_service(
            SVC_SET_TILT_POS,
            TILT_POSITION_SERVICE_SCHEMA,
            "async_set_cover_tilt_position",
        )
        platform.async_register_entity_service(SVC_OPEN_SHADE, {}, "async_open_cover")
        platform.async_register_entity_service(SVC_CLOSE_SHADE, {}, "async_close_cover")
        platform.async_register_entity_service(SVC_STOP_SHADE, {}, "async_stop_cover")
        platform.async_register_entity_service(
            SVC_TILT_OPEN, {}, "async_open_cover_tilt"
        )
        platform.async_register_entity_service(
            SVC_TILT_CLOSE, {}, "async_close_cover_tilt"
        )
        platform.async_register_entity_service(
            SVC_SET_CURRENT_POS, POSITION_SERVICE_SCHEMA, "async_set_current_position"
        )
        platform.async_register_entity_service(
            SVC_SET_CURRENT_TILT_POS,
            TILT_POSITION_SERVICE_SCHEMA,
            "async_set_current_tilt_position",
        )
        platform.async_register_entity_service(
            SVC_SET_SUNNY, SUNNY_SERVICE_SCHEMA, "async_set_sunny"
        )
        platform.async_register_entity_service(
            SVC_SET_WINDY, WINDY_SERVICE_SCHEMA, "async_set_windy"
        )
        platform.async_register_entity_service(
            SVC_SEND_COMMAND, SEND_COMMAND_SERVICE_SCHEMA, "async_send_command"
        )
        platform.async_register_entity_service(
            SVC_SEND_STEP_COMMAND,
            SEND_STEP_COMMAND_SERVICE_SCHEMA,
            "async_send_step_command",
        )


class ESPSomfyGroup(CoverGroup, ESPSomfyEntity):
    """A grpi[] that is associated with a controller."""

    def __init__(
        self, hass: HomeAssistant, controller: ESPSomfyController, data
    ) -> None:
        """Initialize a group."""
        ESPSomfyEntity.__init__(self=self, controller=controller, data=data)
        self._hass = hass
        self._attr_available = True
        self._controller = controller
        self._group_id = data["groupId"]
        self._attr_device_class = CoverDeviceClass.SHADE
        entry = hass.config_entries.async_get_entry(controller.config_entry_id)
        self._attr_assumed_state = controls_always_enabled(entry)
        self._linked_shade_ids = []
        # Only awnings need HA-side open/close swap for device class semantics.
        # flipCommands is handled on each shade; for groups with Invert Commands,
        # swap open/close here so RF matches after firmware transform.
        self._flip_position = False
        self._flip_commands = as_bool(data.get("flipCommands"))
        self._process_individual = False
        awning = 0
        not_awning = 0
        if "linkedShades" in data:
            for linked_shade in data["linkedShades"]:
                if (
                    "shadeType" in linked_shade
                    and int(linked_shade["shadeType"]) == 3
                ):
                    awning = awning + 1
                else:
                    not_awning = not_awning + 1
                self._linked_shade_ids.append(int(linked_shade["shadeId"]))
        uuid = f"{controller.unique_id}_group{self._group_id}"
        if awning > 0 and not_awning == 0:
            self._flip_position = True
        elif awning > 0 and not_awning > 0:
            self._process_individual = True
        entities = er.async_get(hass)
        shade_ids: list[str] = []
        for entity in er.async_entries_for_config_entry(
            entities, self._controller.config_entry_id
        ):
            shade_ids.extend(
                [
                    entity.entity_id
                    for cover_id in self._linked_shade_ids
                    if entity.unique_id == f"{self._controller.unique_id}_{cover_id}"
                ]
            )
            # Supposedly according to ruff the above is more readable and succinct.
            # for cover_id in self._linked_shade_ids:
            #    if entity.unique_id == f"{self._controller.unique_id}_{cover_id}":
            #        shade_ids.append(entity.entity_id)
        super().__init__(unique_id=uuid, name=controller.api.format_entity_name(data), entities=shade_ids)

    async def async_added_to_hass(self) -> None:
        """Subscribe to device events."""
        entities = er.async_get(self._hass)
        shade_ids: list[str] = []
        for entity in er.async_entries_for_config_entry(
            entities, self._controller.config_entry_id
        ):
            for cover_id in self._linked_shade_ids:
                if entity.unique_id == f"{self._controller.unique_id}_{cover_id}":
                    if hasattr(self, "_entities"):
                        if entity.entity_id not in self._entities:
                            self._entities.append(entity.entity_id)
                    elif hasattr(self, "_entity_ids"):
                        if entity.entity_id not in self._entity_ids:
                            self._entity_ids.append(entity.entity_id)
                    shade_ids.append(entity.entity_id)
        # self._entities = shade_ids
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: shade_ids}
        await super().async_added_to_hass()
        self.hass.async_create_task(self._async_sync_room_identity())
        self.async_on_remove(
            self.coordinator.async_add_listener(
                self._handle_coordinator_update, self.coordinator_context
            )
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.registry_entry.disabled:
            return
        if (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            self._attr_available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
        elif "groupId" in self._controller.data:
            if self._controller.data["groupId"] == self._group_id:
                if "linkedShades" in self._controller.data:
                    self._linked_shade_ids.clear()
                    for shade in self._controller.data["linkedShades"]:
                        self._linked_shade_ids.append(int(shade["shadeId"]))
                self._attr_available = True
                self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._attr_available

    @property
    def should_poll(self) -> bool:
        """Indicates whether the group should poll for information."""
        return False

    @property
    def icon(self) -> str:
        """Icon for the group."""
        if hasattr(self, "_attr_icon"):
            return self._attr_icon
        return "mdi:table-multiple"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self._process_individual:
            await super().async_open_cover(**kwargs)
        elif self._flip_position:
            await self._controller.api.close_group(self._group_id)
        else:
            await self._controller.api.open_group(self._group_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        if self._process_individual:
            await super().async_close_cover(**kwargs)
        elif self._flip_position:
            await self._controller.api.open_group(self._group_id)
        else:
            await self._controller.api.close_group(self._group_id)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Hold cover."""
        await self._controller.async_stop_group(self._group_id)

    async def async_send_command(self, **kwargs: Any) -> None:
        """Send raw command from SVC."""
        cmd = {"groupId": self._group_id, "command": kwargs[ATTR_COMMAND]}
        if ATTR_REPEAT in kwargs:
            cmd[ATTR_REPEAT] = kwargs[ATTR_REPEAT]
        await self._controller.api.group_command(cmd)

    async def async_send_step_command(self, **kwargs: Any) -> None:
        """Send a raw step command from the service."""
        cmd = {
            "groupId": self._group_id,
            "command": f"Step{kwargs[ATTR_DIRECTION]}",
            "stepSize": kwargs[ATTR_STEP_SIZE],
        }
        if ATTR_REPEAT in kwargs:
            cmd[ATTR_REPEAT] = kwargs[ATTR_REPEAT]
        await self._controller.api.group_command(cmd)


class ESPSomfyRoom(ESPSomfyEntity, CoverEntity):
    """Open/close/stop every shade in a room via the firmware queue."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a room cover."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._room_id = int(data["roomId"])
        self._attr_unique_id = f"{controller.unique_id}_room{self._room_id}"
        self._attr_name = controller.api.format_entity_name(data)
        self._attr_device_class = CoverDeviceClass.SHADE
        self._attr_available = True
        self._attr_assumed_state = True
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

    def _member_shades(self) -> list[dict]:
        return room_shades(self._controller.api.shades, self._room_id)

    def _all_awnings(self) -> bool:
        members = self._member_shades()
        return bool(members) and all(
            int(s.get("shadeType", 0)) == 3 for s in members
        )

    @property
    def current_cover_position(self) -> int | None:
        """Average HA-scale position of shades in this room."""
        poses = []
        for shade in self._member_shades():
            ha = ha_position_from_esp(
                shade.get("position"),
                flip_position=as_bool(shade.get("flipPosition", False)),
            )
            if ha is not None:
                poses.append(ha)
        if not poses:
            return None
        return int(sum(poses) / len(poses))

    @property
    def is_closed(self) -> bool | None:
        """True when every member reports closed (HA scale)."""
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos <= 5

    @property
    def is_opening(self) -> bool:
        """True if any member is moving toward open."""
        if self._all_awnings():
            return any(int(s.get("direction") or 0) > 0 for s in self._member_shades())
        return any(int(s.get("direction") or 0) < 0 for s in self._member_shades())

    @property
    def is_closing(self) -> bool:
        """True if any member is moving toward closed."""
        if self._all_awnings():
            return any(int(s.get("direction") or 0) < 0 for s in self._member_shades())
        return any(int(s.get("direction") or 0) > 0 for s in self._member_shades())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh when a member shade moves."""
        if self.registry_entry and self.registry_entry.disabled:
            return
        data = self._controller.data or {}
        if data.get("event") == EVT_CONNECTED and "connected" in data:
            self._attr_available = bool(data["connected"])
            self.async_write_ha_state()
            return
        if data.get("event") == EVT_SHADESTATE:
            try:
                rid = int(data.get("roomId") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid in (0, self._room_id):
                self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open all shades in the room."""
        cmd = room_rf_command(
            self._controller.api.shades, self._room_id, "open"
        )
        await self._controller.api.room_command(self._room_id, cmd)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close all shades in the room."""
        cmd = room_rf_command(
            self._controller.api.shades, self._room_id, "close"
        )
        await self._controller.api.room_command(self._room_id, cmd)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop all shades in the room."""
        await self._controller.api.room_command(self._room_id, "stop")


class ESPSomfyShade(ESPSomfyEntity, CoverEntity):
    """A shade that is associated with a controller."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new shade."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = data["shadeId"]
        self._position = data["position"]
        self._tilt_position = 100
        self._tilt_direction = 0
        self._attr_unique_id = f"{controller.unique_id}_{self._shade_id}"
        self._attr_name = controller.api.format_entity_name(data)
        self._direction = 0
        self._attr_available = True
        self._has_tilt = False
        self._has_lift = True
        self._tilt_type = 0
        self._state_attributes: dict[str, Any] = {}
        room_name = controller.api.get_room_name(controller.api.get_room_id(data))
        if room_name:
            self._state_attributes["room"] = room_name
        self._shade_type = 1
        self._last_direction = 0
        # ESP flipPosition is applied in the API payload (transformPosition).
        # Firmware API/UI uses HA scale (100%=open · 0%=closed). Cover passes
        # that through so Open/Close buttons and sliders match SomfyController.
        # ESP flipCommands swaps RF Up/Down — HA always sends open→up / close→down.
        self._apply_device_flags(data)

        self._attr_device_class = CoverDeviceClass.SHADE
        # When always-enabled: keep Open/Close/Stop clickable while moving/open.
        # Follow-status: HA greys buttons from is_closed / is_opening / is_closing.
        entry = controller.hass.config_entries.async_get_entry(
            controller.config_entry_id
        )
        self._attr_assumed_state = controls_always_enabled(entry)

        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if "hasTilt" in data and data["hasTilt"] is True:
            self._attr_supported_features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
            self._has_tilt = True
            self._tilt_position = data.get("tiltPosition", 100)
            self._tilt_direction = data.get("tiltDirection", 0)
        if "tiltType" in data:
            self._tilt_type = int(data["tiltType"])
            match int(data["tiltType"]):
                case 1 | 2 | 4:
                    self._has_tilt = True
                    self._attr_supported_features |= (
                        CoverEntityFeature.OPEN_TILT
                        | CoverEntityFeature.CLOSE_TILT
                        | CoverEntityFeature.SET_TILT_POSITION
                    )
                case 3:
                    self._has_tilt = True
                    self._has_lift = False
                    self._attr_supported_features = (
                        CoverEntityFeature.OPEN_TILT
                        | CoverEntityFeature.STOP_TILT
                        | CoverEntityFeature.CLOSE_TILT
                        | CoverEntityFeature.SET_TILT_POSITION
                    )

        if "shadeType" in data:
            self._shade_type = int(data["shadeType"])
            match int(data["shadeType"]):
                case 1:
                    self._attr_device_class = CoverDeviceClass.BLIND
                case 2 | 7 | 8:
                    self._attr_device_class = CoverDeviceClass.CURTAIN
                case 3:
                    self._attr_device_class = CoverDeviceClass.AWNING
                case 4:
                    self._attr_device_class = CoverDeviceClass.SHUTTER
                case 5:
                    self._attr_device_class = CoverDeviceClass.GARAGE
                    self._attr_supported_features = CoverEntityFeature.STOP

                case 6:
                    self._attr_device_class = CoverDeviceClass.GARAGE
                case 11 | 12 | 13:
                    self._attr_device_class = CoverDeviceClass.GATE
                case 14 | 15 | 16:
                    self._attr_device_class = CoverDeviceClass.GATE
                    self._attr_supported_features = (
                        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
                    )
                case _:
                    self._attr_device_class = CoverDeviceClass.SHADE

        self._attr_is_closed: bool = False
        if self._has_lift:
            self._attr_current_cover_position = self.current_cover_position
        if self._has_tilt:
            self._attr_current_cover_tilt_position = self.current_cover_tilt_position
        # print(f"Set up shade {self._attr_unique_id} - {self._attr_name}")

    def _apply_device_flags(self, data: dict) -> None:
        """Refresh invert flags from ESP shade payload."""
        if "flipCommands" in data or not hasattr(self, "_flip_commands"):
            self._flip_commands = as_bool(data.get("flipCommands", False))
        if "flipPosition" in data or not hasattr(self, "_flip_position"):
            self._flip_position = as_bool(data.get("flipPosition", False))
        self._state_attributes["invert_direction"] = self._flip_commands
        self._state_attributes["invert_position"] = self._flip_position
        self._state_attributes["position_scale"] = "100%=open · 0%=closed"

    def _handle_state_update(self, data) -> None:
        """Handle the state update."""
        upd = False

        if "flipCommands" in data or "flipPosition" in data:
            self._apply_device_flags(data)
            upd = True
        if "remoteAddress" in data and self._state_attributes.get(
            "remote_address", 0
        ) != int(data["remoteAddress"]):
            self._state_attributes["remote_address"] = int(data["remoteAddress"])
            upd = True
        if "position" in data and self._position != data.get("position", -1):
            self._position = int(data["position"])
            upd = True
        if "direction" in data and self._direction != data.get("direction", 0):
            self._direction = int(data["direction"])
            upd = True
        if "target" in data and self._state_attributes.get("target", -1) != data.get(
            "target", 0
        ):
            self._state_attributes["target"] = int(data.get("target", 0))
            upd = True
        if "myPos" in data and self._state_attributes.get("my_pos", -1) != data.get(
            "myPos", 0
        ):
            self._state_attributes["my_pos"] = int(data.get("myPos", -1))
            upd = True

        if "hasTilt" in data and self._has_tilt != as_bool(data.get("hasTilt", False)):
            self._has_tilt = as_bool(data["hasTilt"])
        if "tiltType" in self._controller.data:
            match int(self._controller.data["tiltType"]):
                case 1 | 2:
                    self._has_tilt = True
                case 3:
                    self._has_tilt = True
                    self._has_lift = False
                case _:
                    self._has_tilt = False
                    self._has_lift = True
        if self._has_tilt:
            if "tiltPosition" in data and self._tilt_position != data.get(
                "tiltPosition", -1
            ):
                self._tilt_position = int(data["tiltPosition"])
                upd = True
            if "tiltDirection" in data and self._tilt_direction != data.get(
                "tiltDirection", 0
            ):
                self._tilt_direction = int(data["tiltDirection"])
                upd = True
            if "tiltTarget" in data and self._state_attributes.get(
                "tilt_target", 0
            ) != int(data["tiltTarget"]):
                self._state_attributes["tilt_target"] = int(data["tiltTarget"])
                upd = True
            if "myTiltPos" in data and self._state_attributes.get(
                "my_tilt_pos", 0
            ) != int(data["myTiltPos"]):
                self._state_attributes["my_tilt_pos"] = int(data["myTiltPos"])
                upd = True
        if upd:
            if self._has_lift:
                self._attr_current_cover_position = self.current_cover_position
            if self._has_tilt:
                self._attr_current_cover_tilt_position = (
                    self.current_cover_tilt_position
                )
            # Garage/gate toggle covers enable/disable Open/Close/Stop from state.
            self.update_supported_features()
            self.async_write_ha_state()

    def _handle_state_command(self, data) -> None:
        """Handle the state when a frame command is sent."""
        upd = False
        if "remoteAddress" in data and self._state_attributes.get(
            "remote_address", 0
        ) != int(data["remoteAddress"]):
            self._state_attributes["remote_address"] = int(data["remoteAddress"])
            upd = True
        if (
            "cmd" in data
            and self._state_attributes.get("last_cmd", None) != data["cmd"]
        ):
            self._state_attributes["last_cmd"] = data["cmd"]
            upd = True
        if (
            "source" in data
            and self._state_attributes.get("cmd_source", None) != data["source"]
        ):
            self._state_attributes["cmd_source"] = data["source"]
            upd = True
        if "sourceAddress" in data and self._state_attributes.get(
            "cmd_address", 0
        ) != int(data["sourceAddress"]):
            self._state_attributes["cmd_address"] = int(data["sourceAddress"])
            upd = True
        self._state_attributes["cmd_fired"] = dt_util.as_timestamp(dt_util.utcnow())
        bus_data = {
            "entity_id": self.entity_id,
            "event_key": EVT_SHADECOMMAND,
            "name": self.name,
            "source": self._state_attributes.get("cmd_source", ""),
            "remote_address": self._state_attributes.get("remote_address", 0),
            "source_address": self._state_attributes.get("cmd_address", 0),
            "command": self._state_attributes.get("last_cmd", ""),
            "timestamp": self._state_attributes.get("cmd_fired"),
        }
        self.hass.bus.async_fire("espsomfy-rts_event", bus_data)
        if upd:
            self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.registry_entry.disabled:
            return
        evt = self._controller.data.get("event", "")
        if evt == EVT_CONNECTED:
            if "connected" in self._controller.data and self._attr_available != bool(
                self._controller.data["connected"]
            ):
                self._attr_available = bool(self._controller.data["connected"])
                self.async_write_ha_state()
        elif self._controller.data.get("shadeId") == self._shade_id:
            if evt == EVT_SHADESTATE:
                self._handle_state_update(self._controller.data)
            elif evt == EVT_SHADEREMOVED:
                self._attr_available = False
            elif evt == EVT_SHADECOMMAND:
                if "remoteAddress" in self._controller.data:
                    self._state_attributes["remote_address"] = self._controller.data[
                        "remoteAddress"
                    ]
                if "cmd" in self._controller.data:
                    self._state_attributes["last_cmd"] = self._controller.data["cmd"]
                if "source" in self._controller.data:
                    self._state_attributes["cmd_source"] = self._controller.data[
                        "source"
                    ]
                if "sourceAddress" in self._controller.data:
                    self._state_attributes["cmd_address"] = self._controller.data[
                        "sourceAddress"
                    ]
                self._state_attributes["cmd_fired"] = dt_util.as_timestamp(
                    dt_util.utcnow()
                )
                bus_data = {
                    "entity_id": self.entity_id,
                    "event_key": EVT_SHADECOMMAND,
                    "name": self.name,
                    "source": self._state_attributes.get("cmd_source", ""),
                    "remote_address": self._state_attributes.get("remote_address", 0),
                    "source_address": self._state_attributes.get("cmd_address", 0),
                    "command": self._state_attributes.get("last_cmd", ""),
                    "timestamp": self._state_attributes.get("cmd_fired"),
                }
                self.hass.bus.async_fire("espsomfy-rts_event", bus_data)
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._attr_available

    @property
    def should_poll(self) -> bool:
        """Indicates whether the shade should poll for information."""
        return False

    @property
    def icon(self) -> str:
        """Icon by Somfy shade type and open/closed/moving state."""
        openness = visual_openness_from_esp(
            self._position,
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        return shade_mdi_icon(
            self._shade_type,
            ha_position=openness,
            is_opening=bool(self.is_opening),
            is_closing=bool(self.is_closing),
            is_closed=bool(self.is_closed),
        )

    @property
    def entity_picture(self) -> str | None:
        """Window illustration (open / partial / closed) like the Somfy UI."""
        openness = visual_openness_from_esp(
            self._position,
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        return shade_entity_picture(
            self._shade_type,
            ha_position=openness,
            is_opening=bool(self.is_opening),
            is_closing=bool(self.is_closing),
            is_closed=bool(self.is_closed),
            url_prefix=f"/{DOMAIN}/images",
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return HA cover % (100 = open) from ESP API."""
        return ha_position_from_esp(
            self._position,
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return HA tilt % (100 = open) from ESP API."""
        if not self._has_tilt:
            return None
        return ha_position_from_esp(
            self._tilt_position,
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )

    @property
    def is_opening(self) -> bool:
        """Return true if cover is opening."""
        if self._tilt_type == 3:
            if self._tilt_direction == 0:
                return False
            if self._tilt_direction == 1 and self._tilt_position < 50:
                return True
            if self._tilt_direction == 1 and self._tilt_position >= 50:
                return False
            if self._tilt_direction == -1 and self._tilt_position < 50:
                return False
            if self._tilt_direction == -1 and self._tilt_position >= 50:
                return True

        if self._attr_device_class == CoverDeviceClass.AWNING:
            return self._direction == 1
        # Internal direction: -1 toward 0 (open), +1 toward 100 (closed).
        # flipPosition only remaps reported %, not movement direction signs.
        return self._direction == -1 or self._tilt_direction == -1

    @property
    def is_closing(self) -> bool:
        """Return true if cover is closing."""
        if self._tilt_type == 3:
            if self._tilt_direction == 0:
                return False
            if self._tilt_direction == 1 and self._tilt_position < 50:
                return False
            if self._tilt_direction == 1 and self._tilt_position >= 50:
                return True
            if self._tilt_direction == -1 and self._tilt_position < 50:
                return True
            if self._tilt_direction == -1 and self._tilt_position >= 50:
                return False

        if self._attr_device_class == CoverDeviceClass.AWNING:
            return self._direction == -1
        return self._direction == 1 or self._tilt_direction == 1

    @property
    def is_closed(self) -> bool:
        """Return true if cover is closed (API: 0% closed · 100% open)."""
        if self._tilt_type == 3:
            return self._tilt_position in (0, 100)
        closed_at = 100 if self._flip_position else 0
        return (self._position == closed_at or not self._has_lift) and (
            self._tilt_position == closed_at or not self._has_tilt
        )

    @property
    def is_open(self) -> bool:
        """Return true if cover is fully open."""
        if self._tilt_type == 3:
            return self._tilt_position < 100 and self._tilt_position > 0
        open_at = 0 if self._flip_position else 100
        return (self._position == open_at or not self._has_lift) and (
            self._tilt_position == open_at or not self._has_tilt
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return entity specific state attributes."""
        return self._state_attributes

    @property
    def is_toggle(self) -> bool:
        """Determine if the shade type uses a toggle."""
        if self._shade_type in (5, 14, 15, 16):
            return True
        return False

    def _controls_always_enabled(self) -> bool:
        """Return True when Open/Close/Stop stay enabled regardless of status."""
        entry = self.hass.config_entries.async_get_entry(
            self._controller.config_entry_id
        )
        return controls_always_enabled(entry)

    def update_supported_features(self) -> None:
        """Update the supported features."""
        if not self.is_toggle:
            return
        if self._direction != 0:
            self._last_direction = self._direction
        if self._controls_always_enabled():
            # Keep Open/Close/Stop always available (assumed RF control).
            self._attr_supported_features |= (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
            )
            return
        # Follow cover status: enable/disable Open/Close/Stop from state.
        if self.is_opening or self.is_closing:
            self._attr_supported_features |= CoverEntityFeature.STOP
            self._attr_supported_features &= ~CoverEntityFeature.OPEN
            self._attr_supported_features &= ~CoverEntityFeature.CLOSE
        else:
            self._attr_supported_features &= ~CoverEntityFeature.STOP
            if self.is_closed:
                self._attr_supported_features |= CoverEntityFeature.CLOSE
                self._attr_supported_features |= CoverEntityFeature.OPEN
            elif self.is_open:
                self._attr_supported_features |= CoverEntityFeature.OPEN
                self._attr_supported_features |= CoverEntityFeature.CLOSE
            elif self._last_direction == 1:
                self._attr_supported_features |= CoverEntityFeature.OPEN
                self._attr_supported_features &= ~CoverEntityFeature.CLOSE
            elif self._last_direction == -1:
                self._attr_supported_features &= ~CoverEntityFeature.OPEN
                self._attr_supported_features |= CoverEntityFeature.CLOSE

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set tilt from HA % (100 = open) into ESP API %."""
        api_tilt = esp_position_from_ha(
            int(kwargs[ATTR_TILT_POSITION]),
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        if api_tilt is None:
            return
        await self._controller.api.position_tilt(self._shade_id, api_tilt)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the tilt (API 100% unless Invert % reading)."""
        target = 0 if self._flip_position else 100
        await self._controller.api.position_tilt(self._shade_id, target)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the tilt (API 0% unless Invert % reading)."""
        target = 100 if self._flip_position else 0
        await self._controller.api.position_tilt(self._shade_id, target)

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop tilting a tilt only shade."""
        await self._controller.async_stop_shade(self._shade_id)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set cover from HA % (100 = open) into ESP API %."""
        api_pos = esp_position_from_ha(
            int(kwargs[ATTR_POSITION]),
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        if api_pos is None:
            return
        await self._controller.api.position_shade(self._shade_id, api_pos)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self.is_toggle:
            if self._direction in (0, 1):
                await self._controller.api.shade_command(
                    {"shadeId": self._shade_id, "command": "toggle"}
                )
            return
        # Awnings: HA open/close opposite of ESP up/down.
        # flipCommands is applied on the ESP — do not also swap here.
        if self._attr_device_class == CoverDeviceClass.AWNING:
            await self._controller.api.close_shade(self._shade_id)
        else:
            await self._controller.api.open_shade(self._shade_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover."""
        if self.is_toggle:
            await self._controller.api.shade_command(
                {"shadeId": self._shade_id, "command": "toggle"}
            )
            return
        if self._attr_device_class == CoverDeviceClass.AWNING:
            await self._controller.api.open_shade(self._shade_id)
        else:
            await self._controller.api.close_shade(self._shade_id)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Hold cover — RF stop and freeze reported position immediately."""
        if self.is_toggle:
            await self._controller.api.shade_command(
                {"shadeId": self._shade_id, "command": "toggle"}
            )
            return
        await self._controller.async_stop_shade(self._shade_id)

    async def async_set_current_position(self, **kwargs: Any) -> None:
        """Set the current position for the device without moving it."""
        api_pos = esp_position_from_ha(
            int(kwargs[ATTR_POSITION]),
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        if api_pos is None:
            return
        await self._controller.async_set_current_position(self._shade_id, api_pos)

    async def async_set_current_tilt_position(self, **kwargs: Any) -> None:
        """Set the current tilt position for the device without moving it."""
        api_tilt = esp_position_from_ha(
            int(kwargs[ATTR_TILT_POSITION]),
            flip_position=self._flip_position,
            device_class=self._attr_device_class,
        )
        if api_tilt is None:
            return
        await self._controller.async_set_current_tilt_position(self._shade_id, api_tilt)
    async def async_set_sunny(self, **kwargs: Any) -> None:
        """Set the sensor value for the device by sending the appropriate frames."""
        await self._controller.api.set_sunny(self._shade_id, bool(kwargs[ATTR_SUNNY]))

    async def async_set_windy(self, **kwargs: Any) -> None:
        """Set the sensor value for the device by sending the appropriate frames."""
        await self._controller.api.set_windy(self._shade_id, bool(kwargs[ATTR_WINDY]))

    async def async_send_command(self, **kwargs: Any) -> None:
        """Send raw command from SVC."""
        cmd = {"shadeId": self._shade_id, "command": kwargs[ATTR_COMMAND]}
        if ATTR_REPEAT in kwargs:
            cmd[ATTR_REPEAT] = kwargs[ATTR_REPEAT]
        await self._controller.api.shade_command(cmd)

    async def async_send_step_command(self, **kwargs: Any) -> None:
        """Send a step command."""
        cmd = {
            "shadeId": self._shade_id,
            "command": f"Step{kwargs[ATTR_DIRECTION]}",
            "stepSize": kwargs[ATTR_STEP_SIZE],
        }
        if ATTR_REPEAT in kwargs:
            cmd[ATTR_REPEAT] = kwargs[ATTR_REPEAT]
        await self._controller.api.shade_command(cmd)
