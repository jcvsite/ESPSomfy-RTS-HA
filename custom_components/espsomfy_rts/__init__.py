"""The ESPSomfy RTS integration."""

from __future__ import annotations

from enum import IntFlag
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntry
import voluptuous as vol

from .const import DOMAIN, PLATFORMS
from .controller import ESPSomfyAPI, ESPSomfyController
from .helpers import room_rf_command

_IMAGES_REGISTERED = False


class ESPSomfyRTSEntityFeature(IntFlag):
    """Supported features of ESPSomfy Entities."""

    REBOOT = 1
    BACKUP = 2


def _controllers_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[ESPSomfyController]:
    """Resolve target hub(s); require host when more than one is configured."""
    controllers: dict = hass.data.get(DOMAIN, {})
    host = call.data.get(CONF_HOST)
    if host:
        host_l = str(host).strip().lower()
        matched = [
            c
            for c in controllers.values()
            if str(c.api.get_host() or "").strip().lower() == host_l
        ]
        if not matched:
            raise HomeAssistantError(f"No ESPSomfy hub matched host {host}")
        return matched
    if len(controllers) <= 1:
        return list(controllers.values())
    raise HomeAssistantError(
        "Multiple ESPSomfy hubs are configured — pass host= to target one"
    )


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up shared static image path once."""
    global _IMAGES_REGISTERED
    if _IMAGES_REGISTERED:
        return True
    images = Path(__file__).parent / "images"
    url_path = f"/{DOMAIN}/images"
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(url_path, str(images), False)]
        )
    except Exception:  # noqa: BLE001 - fall back for older HA
        hass.http.register_static_path(url_path, str(images), cache_headers=False)
    _IMAGES_REGISTERED = True

    async def _apply_scene(call: ServiceCall) -> None:
        scene_id = int(call.data["scene_id"])
        for controller in _controllers_for_call(hass, call):
            await controller.api.scene_command(scene_id)

    async def _room_command(call: ServiceCall) -> None:
        room_id = int(call.data["room_id"])
        command = str(call.data["command"])
        for controller in _controllers_for_call(hass, call):
            cmd = room_rf_command(controller.api.shades, room_id, command)
            await controller.api.room_command(room_id, cmd)

    if not hass.services.has_service(DOMAIN, "apply_scene"):
        hass.services.async_register(
            DOMAIN,
            "apply_scene",
            _apply_scene,
            schema=vol.Schema(
                {
                    vol.Required("scene_id"): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=8)
                    ),
                    vol.Optional(CONF_HOST): str,
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, "room_command"):
        hass.services.async_register(
            DOMAIN,
            "room_command",
            _room_command,
            schema=vol.Schema(
                {
                    vol.Required("room_id"): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=24)
                    ),
                    vol.Required("command"): vol.In(
                        ["up", "down", "my", "stop", "open", "close"]
                    ),
                    vol.Optional(CONF_HOST): str,
                }
            ),
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPSomfy RTS from a config entry."""
    await async_setup(hass, {})
    api = ESPSomfyAPI(hass, entry.entry_id, entry.data)
    controller = ESPSomfyController(entry.entry_id, hass, api)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    await api.get_initial()
    if not api.is_configured:
        raise ConfigEntryNotReady(
            f"Could not find ESPSomfy RTS device with address {api.get_api_url()}"
        )

    if entry.title != api.deviceName:
        hass.config_entries.async_update_entry(entry, title=api.deviceName)

    async def _async_ws_close(_: Event) -> None:
        await controller.ws_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_ws_close)
    )
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    await controller.ws_connect()
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when integration options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    controller: ESPSomfyController = hass.data[DOMAIN].get(entry.entry_id)
    if controller is not None:
        await controller.ws_close()
        if controller.api.is_configured:
            if unload_ok := await hass.config_entries.async_unload_platforms(
                entry, PLATFORMS
            ):
                hass.data[DOMAIN].pop(entry.entry_id)
            return unload_ok
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True
