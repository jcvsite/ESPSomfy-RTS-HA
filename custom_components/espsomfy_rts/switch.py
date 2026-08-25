"""Switches related to ESPSomfy-RTS-HA."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    EVT_CONNECTED,
    EVT_FIXEDCODEREMOVED,
    EVT_FIXEDCODESTATE,
    EVT_GROUPSTATE,
    EVT_SHADEADDED,
    EVT_SHADESTATE,
)
from .controller import ESPSomfyController
from .entity import ESPSomfyEntity
from .helpers import as_bool


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up shades for the shade controller."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    new_entities = []
    known_invert_ids: set[int] = set()
    data = controller.api.get_config()
    if "serverId" in data:
        for shade in controller.api.shades:
            try:
                if "shadeType" in shade and (
                    int(shade["shadeType"]) == 9 or int(shade["shadeType"]) == 10
                ):
                    new_entities.append(
                        ESPSomfyBinarySwitch(controller=controller, data=shade)
                    )
                elif "sunSensor" in shade:
                    if shade["sunSensor"] is True:
                        new_entities.append(
                            ESPSomfySunSwitch(controller=controller, data=shade)
                        )
                elif "shadeType" in shade:
                    match shade["shadeType"]:
                        case 3:
                            new_entities.append(
                                ESPSomfySunSwitch(controller=controller, data=shade)
                            )
                # Invert / travel settings sync to ESP (not dry contacts).
                if "shadeType" in shade and not (
                    int(shade["shadeType"]) == 9 or int(shade["shadeType"]) == 10
                ):
                    shade_id = int(shade["shadeId"])
                    known_invert_ids.add(shade_id)
                    new_entities.extend(
                        [
                            ESPSomfyInvertDirectionSwitch(controller, shade),
                            ESPSomfyInvertPositionSwitch(controller, shade),
                        ]
                    )
            except KeyError:
                pass

        for group in controller.api.groups:
            try:
                if "sunSensor" in group:
                    if group["sunSensor"] is True:
                        new_entities.append(
                            ESPSomfySunSwitch(controller=controller, data=group)
                        )

            except KeyError:
                pass

        known_fc_ids: set[int] = set()
        for fc in controller.api.fixed_codes:
            try:
                if "id" in fc and int(fc["id"]) > 0:
                    fc_id = int(fc["id"])
                    known_fc_ids.add(fc_id)
                    new_entities.append(
                        ESPSomfyFixedCodeSwitch(controller=controller, data=fc)
                    )
                    new_entities.extend(
                        [
                            ESPSomfyFixedCodeInvertSwitch(controller, fc),
                            ESPSomfyFixedCodeSingleButtonSwitch(controller, fc),
                        ]
                    )
            except (KeyError, TypeError, ValueError):
                pass

        def _on_fixed_code_added() -> None:
            evt = controller.data.get("event")
            if evt != EVT_FIXEDCODESTATE:
                return
            payload = controller.data
            try:
                fc_id = int(payload["id"])
            except (KeyError, TypeError, ValueError):
                return
            if fc_id <= 0 or fc_id in known_fc_ids:
                return
            if not payload.get("ready") and not payload.get("name"):
                return
            known_fc_ids.add(fc_id)
            async_add_entities(
                [
                    ESPSomfyFixedCodeSwitch(controller, payload),
                    ESPSomfyFixedCodeInvertSwitch(controller, payload),
                    ESPSomfyFixedCodeSingleButtonSwitch(controller, payload),
                ]
            )

        config_entry.async_on_unload(
            controller.async_add_listener(_on_fixed_code_added)
        )

        def _on_shade_added() -> None:
            evt = controller.data.get("event")
            if evt != EVT_SHADEADDED:
                return
            payload = controller.data
            try:
                if int(payload.get("shadeType", -1)) in (9, 10):
                    return
                shade_id = int(payload["shadeId"])
            except (KeyError, TypeError, ValueError):
                return
            if shade_id in known_invert_ids:
                return
            known_invert_ids.add(shade_id)
            async_add_entities(
                [
                    ESPSomfyInvertDirectionSwitch(controller, payload),
                    ESPSomfyInvertPositionSwitch(controller, payload),
                ]
            )

        config_entry.async_on_unload(
            controller.async_add_listener(_on_shade_added)
        )

    if new_entities:
        async_add_entities(new_entities)


class ESPSomfySunSwitch(ESPSomfyEntity, SwitchEntity):
    """A sun flag switch for toggling sun mode."""

    _entity_id_suffix = "sun_flag"

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new SunSwitch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = None
        self._group_id = None
        self._attr_icon = "mdi:white-balance-sunny"
        self._attr_name = controller.api.format_entity_name(data)
        self._attr_has_entity_name = False
        self._sunswitch_type = None
        self._available = True
        if "groupId" in data:
            self._group_id = data["groupId"]
            self._attr_unique_id = (
                f"sunswitch_group_{controller.unique_id}_{self._group_id}"
            )
            self._sunswitch_type = "group"
        else:
            self._shade_id = data["shadeId"]
            self._attr_unique_id = f"sunswitch_{controller.unique_id}_{self._shade_id}"
            self._sunswitch_type = "motor"

        if "flags" in data:
            self._attr_is_on = bool((int(data["flags"]) & 0x01) == 0x01)
        else:
            self._attr_is_on = False

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
        elif (
            self._sunswitch_type == "motor"
            and "shadeId" in self._controller.data
            and self._controller.data["shadeId"] == self._shade_id
        ):
            if (
                self._controller.data["event"] == EVT_SHADESTATE
                and "flags" in self._controller.data
            ):
                self._attr_is_on = bool(
                    (int(self._controller.data["flags"]) & 0x01) == 0x01
                )
                self.async_write_ha_state()
        elif (
            self._sunswitch_type == "group"
            and "groupId" in self._controller.data
            and self._controller.data["groupId"] == self._group_id
        ):
            if (
                self._controller.data["event"] == EVT_GROUPSTATE
                and "flags" in self._controller.data
            ):
                self._attr_is_on = bool(
                    (int(self._controller.data["flags"]) & 0x01) == 0x01
                )
                self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self._sunswitch_type == "motor":
            await self.coordinator.api.sun_flag_on(self._shade_id)
            return
        await self.coordinator.api.sun_flag_group_on(self._group_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        if self._sunswitch_type == "motor":
            await self.coordinator.api.sun_flag_off(self._shade_id)
            return
        await self.coordinator.api.sun_flag_group_off(self._group_id)

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._available


class ESPSomfyBinarySwitch(ESPSomfyEntity, SwitchEntity):
    """A binary switch for toggling a dry contact."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new BinarySwitch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = None
        self._group_id = None
        self._attr_name = controller.api.format_entity_name(data)
        self._attr_has_entity_name = False
        self._binaryswitch_type = data["shadeType"]
        self._shade_id = data["shadeId"]
        self._available = True
        self._attr_unique_id = f"binaryswitch_{controller.unique_id}_{self._shade_id}"
        self._flip_commands = False
        if "flipCommands" in data:
            self._flip_commands = bool(data["flipCommands"])
        if "position" in data:
            self._attr_is_on = bool((int(data["position"])) > 0)
        else:
            self._attr_is_on = False

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.registry_entry.disabled:
            return
        if (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
        elif (
            "position" in self._controller.data
            and self._controller.data["shadeId"] == self._shade_id
        ):
            self._attr_is_on = bool((int(self._controller.data["position"])) > 0)
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self._binaryswitch_type == 10:
            if self._flip_commands:
                await self.coordinator.api.close_shade(self._shade_id)
            else:
                await self.coordinator.api.open_shade(self._shade_id)
        else:
            await self.coordinator.api.toggle_shade(self._shade_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        if self._binaryswitch_type == 10:
            if self._flip_commands:
                await self.coordinator.api.open_shade(self._shade_id)
            else:
                await self.coordinator.api.close_shade(self._shade_id)
        else:
            await self.coordinator.api.toggle_shade(self._shade_id)


class ESPSomfyFixedCodeSwitch(ESPSomfyEntity, SwitchEntity):
    """A fixed-code 433 MHz RF ON/OFF switch."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new fixed-code RF switch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._switch_id = int(data["id"])
        self._attr_name = data.get("name") or f"RF Switch {self._switch_id}"
        self._attr_has_entity_name = False
        self._attr_icon = "mdi:light-switch"
        self._attr_unique_id = f"fixedcode_{controller.unique_id}_{self._switch_id}"
        self._connected = True
        self._ready = bool(data.get("ready", False))
        self._single_button = bool(data.get("singleButton", False))
        self._attr_is_on = bool(data.get("state", False))

    def _apply_payload(self, data: dict[str, Any]) -> None:
        if "name" in data and data["name"]:
            self._attr_name = data["name"]
        if "ready" in data:
            self._ready = bool(data["ready"])
        if "singleButton" in data:
            self._single_button = bool(data["singleButton"])
        if "state" in data:
            self._attr_is_on = bool(data["state"])

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        event = self._controller.data.get("event")
        if event == EVT_CONNECTED and "connected" in self._controller.data:
            self._connected = bool(self._controller.data["connected"])
            self.async_write_ha_state()
            return
        if (
            event == EVT_FIXEDCODEREMOVED
            and self._controller.data.get("id") == self._switch_id
        ):
            self._ready = False
            self.async_write_ha_state()
            return
        if (
            event == EVT_FIXEDCODESTATE
            and self._controller.data.get("id") == self._switch_id
        ):
            self._apply_payload(self._controller.data)
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available when connected and codes have been learned."""
        return self._connected and self._ready

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Transmit ON (or toggle for single-button remotes)."""
        if self._single_button:
            ok = await self.coordinator.api.fixed_code_toggle(self._switch_id)
        else:
            ok = await self.coordinator.api.fixed_code_on(self._switch_id)
        if ok:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Transmit OFF (or toggle for single-button remotes)."""
        if self._single_button:
            ok = await self.coordinator.api.fixed_code_toggle(self._switch_id)
        else:
            ok = await self.coordinator.api.fixed_code_off(self._switch_id)
        if ok:
            self._attr_is_on = False
            self.async_write_ha_state()


class ESPSomfyFixedCodeConfigSwitch(ESPSomfyEntity, SwitchEntity):
    """FixedCode setting synced to the ESP (flipCommands / singleButton)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _setting_key: str

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize FixedCode config switch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._switch_id = int(data["id"])
        self._available = True
        self._attr_is_on = as_bool(data.get(self._setting_key, False))

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    def _handle_coordinator_update(self) -> None:
        """Mirror ESP fixedCodeState (including Somfy UI changes)."""
        event = self._controller.data.get("event")
        if event == EVT_CONNECTED and "connected" in self._controller.data:
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
            return
        if (
            event == EVT_FIXEDCODEREMOVED
            and self._controller.data.get("id") == self._switch_id
        ):
            self._available = False
            self.async_write_ha_state()
            return
        if (
            event == EVT_FIXEDCODESTATE
            and self._controller.data.get("id") == self._switch_id
            and self._setting_key in self._controller.data
        ):
            self._attr_is_on = as_bool(self._controller.data[self._setting_key])
            self.async_write_ha_state()

    async def _async_set(self, value: bool) -> None:
        ok = await self._controller.async_update_fixed_code_settings(
            self._switch_id, {self._setting_key: value}
        )
        if not ok:
            raise HomeAssistantError(
                "Could not update RF switch setting on the ESPSomfy controller."
            )
        self._attr_is_on = value
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable setting on the ESP."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable setting on the ESP."""
        await self._async_set(False)


class ESPSomfyFixedCodeInvertSwitch(ESPSomfyFixedCodeConfigSwitch):
    """Fix reversed ON/OFF (ESP flipCommands). HA buttons stay On/Off."""

    _entity_id_suffix = "invert_on_off"
    _attr_translation_key = "invert_on_off"
    _attr_name = "Invert ON/OFF"
    _attr_icon = "mdi:swap-horizontal"
    _setting_key = "flipCommands"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize FixedCode invert switch."""
        super().__init__(controller, data)
        self._attr_unique_id = (
            f"fc_invert_{controller.unique_id}_{self._switch_id}"
        )


class ESPSomfyFixedCodeSingleButtonSwitch(ESPSomfyFixedCodeConfigSwitch):
    """Single-button remote mode (ESP singleButton). Off = dual ON/OFF codes."""

    _entity_id_suffix = "single_button"
    _attr_translation_key = "single_button_remote"
    _attr_name = "Single-button remote"
    _attr_icon = "mdi:gesture-tap-button"
    _setting_key = "singleButton"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize single-button mode switch."""
        super().__init__(controller, data)
        self._attr_unique_id = (
            f"fc_single_{controller.unique_id}_{self._switch_id}"
        )


class ESPSomfyInvertSwitchBase(ESPSomfyEntity, SwitchEntity):
    """Invert setting synced to the ESP (flipCommands / flipPosition)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _setting_key: str

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize invert switch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = int(data["shadeId"])
        self._available = True
        self._attr_is_on = as_bool(data.get(self._setting_key, False))

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    def _handle_coordinator_update(self) -> None:
        """Mirror ESP shadeState (including changes made in the Somfy UI)."""
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
            self._attr_is_on = as_bool(self._controller.data[self._setting_key])
            self.async_write_ha_state()

    async def _async_set(self, value: bool) -> None:
        ok = await self._controller.async_update_shade_settings(
            self._shade_id, {self._setting_key: value}
        )
        if not ok:
            raise HomeAssistantError(
                "Could not update shade setting on the ESPSomfy controller."
            )
        self._attr_is_on = value
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable setting on the ESP."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable setting on the ESP."""
        await self._async_set(False)


class ESPSomfyInvertDirectionSwitch(ESPSomfyInvertSwitchBase):
    """Swap Open↔Close RF direction (ESP flipCommands).

    Use when Open moves the wrong way (curtain left↔right, shade up↔down).
    HA buttons stay Open/Stop/Close; ESP swaps the RF. Does not change %.
    """

    _entity_id_suffix = "invert_direction"
    _attr_translation_key = "invert_direction"
    _attr_name = "Invert direction"
    _attr_icon = "mdi:swap-horizontal"
    _setting_key = "flipCommands"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize direction invert switch."""
        super().__init__(controller, data)
        self._attr_unique_id = (
            f"invert_dir_{controller.unique_id}_{self._shade_id}"
        )


class ESPSomfyInvertPositionSwitch(ESPSomfyInvertSwitchBase):
    """Flip percentage numbers only (ESP flipPosition).

    ESP scale is always 0%=open · 100%=closed. Use only if % is still
    reversed after Invert direction and calibration.
    """

    _entity_id_suffix = "invert_position"
    _attr_translation_key = "invert_position"
    _attr_name = "Invert % reading"
    _attr_icon = "mdi:percent-outline"
    _setting_key = "flipPosition"

    def __init__(self, controller: ESPSomfyController, data: dict) -> None:
        """Initialize position invert switch."""
        super().__init__(controller, data)
        self._attr_unique_id = (
            f"invert_pos_{controller.unique_id}_{self._shade_id}"
        )
