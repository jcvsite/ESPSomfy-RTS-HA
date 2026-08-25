"""Controller for all the devices."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
import json
import logging
import os
import threading
from threading import Timer
from typing import Any

import aiofiles
import aiohttp
import websocket

from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PIN, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    aiohttp_client,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    API_DISCOVERY,
    API_FIXEDCODECOMMAND,
    API_FIXEDCODES,
    API_SAVEFIXEDCODE,
    API_GROUPCOMMAND,
    API_GROUPS,
    API_LOGIN,
    API_ROOMCOMMAND,
    API_SCENECOMMAND,
    API_SETPOSITIONS,
    API_SETSENSOR,
    API_SHADE,
    API_SHADECOMMAND,
    API_SHADES,
    API_TILTCOMMAND,
    DOMAIN,
    EVT_CONNECTED,
    EVT_ETHERNET,
    EVT_FIXEDCODEREMOVED,
    EVT_FIXEDCODESTATE,
    EVT_FWSTATUS,
    EVT_GROUPSTATE,
    EVT_MEMSTATUS,
    EVT_SHADEADDED,
    EVT_SHADECOMMAND,
    EVT_SHADEREMOVED,
    EVT_SHADESTATE,
    EVT_UPDPROGRESS,
    EVT_WIFISTRENGTH,
    MANUFACTURER,
    PLATFORMS,
)

from .helpers import parse_firmware_version

_LOGGER = logging.getLogger(__name__)
logging.getLogger("websocket").setLevel(logging.CRITICAL)


class SocketListener(threading.Thread):
    """A listener of sockets."""

    def __init__(
        self, hass: HomeAssistant, url: str, onpacket, onopen, onclose, onerror
    ) -> None:
        """Initialize a new socket listener."""
        super().__init__()
        self.url = url
        self.onpacket = onpacket
        self.onopen = onopen
        self.onclose = onclose
        self.onerror = onerror
        self.connected = False
        self.main_loop = None
        self.ws_app = None
        self.hass = hass
        self._should_stop = False
        self.filter = None
        self.running_future = None
        self.reconnects = 0
        self._connect_timer = None

    def __enter__(self):
        """Start the thread."""
        self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop and join the thread."""
        self.stop()

    def stop(self):
        """Cancel event stream and join the thread."""
        _LOGGER.debug("Stopping event thread")
        self._should_stop = True
        if self.ws_app:  # and self.connected:
            self.ws_app.close()

        _LOGGER.debug("Joining event thread")
        if self.is_alive():
            self.join()
        _LOGGER.debug("Event thread joined")

    async def connect(self):
        """Start up the web socket."""
        self.main_loop = asyncio.get_event_loop()
        self.ws_app = websocket.WebSocketApp(
            self.url,
            on_message=self.ws_onmessage,
            on_error=self.ws_onerror,
            on_close=self.ws_onclose,
            on_open=self.ws_onopen,
            keep_running=True,
        )
        self.main_loop.run_in_executor(None, self.ws_begin)

    def reconnect(self):
        """Reconnect to the web socket."""
        if self._connect_timer is not None:
            self._connect_timer.cancel()
            self._connect_timer = None
        self.reconnects = self.reconnects + 1
        self.main_loop = self.hass.loop
        try:
            self.ws_app = websocket.WebSocketApp(
                self.url,
                on_message=self.ws_onmessage,
                on_error=self.ws_onerror,
                on_close=self.ws_onclose,
                on_open=self.ws_onopen,
                keep_running=True,
            )
            self.main_loop.run_in_executor(None, self.ws_begin)
            # connected is set only in ws_onopen / "connected" ping reply
        except (
            websocket.WebSocketAddressException,
            websocket.WebSocketTimeoutException,
            websocket.WebSocketConnectionClosedException,
        ):
            delay = min(10 * self.reconnects / 2, 20)

            def _retry() -> None:
                self.hass.loop.call_soon_threadsafe(self.reconnect)

            self._connect_timer = Timer(delay, _retry)
            self._connect_timer.start()

    def set_filter(self, arr: Any) -> None:
        """Filter for the events."""
        self.filter = arr.copy()

    def close(self) -> None:
        """Synonym for stop."""
        self.stop()

    def ws_begin(self) -> None:
        """Begin the socket."""
        self.running_future = self.ws_app.run_forever(ping_interval=20, ping_timeout=15)
        # print("Fell out of run_runforever")
        if not self._should_stop:
            self.hass.loop.call_soon_threadsafe(self.reconnect)

    def ws_onerror(self, wsapp, exception):
        """Socket error."""
        # print(f"We have an error {exception}")
        self.hass.loop.call_soon_threadsafe(self.onerror, exception)

    def ws_onclose(self, wsapp, status, msg):
        """Socket closed."""
        # print(f"The socket was closed {status}")
        self.connected = False
        if not self._should_stop:
            self.hass.loop.call_soon_threadsafe(self.onclose)

    def ws_onopen(self, wsapp):
        """Open the socket."""
        self.connected = True
        self.reconnects = 0
        self.hass.loop.call_soon_threadsafe(self.onopen)

    def ws_onmessage(self, wsapp, message: str):
        """Process the incoming message."""
        try:
            if message is None:
                _LOGGER.debug("Got an empty socket payload")
            elif message.startswith("42["):
                ndx = message.find(",")
                event = message[3:ndx]
                if not self.filter or event in self.filter:
                    payload = message[ndx + 1 : -1]
                    # print(f"Event:{event} Payload:{payload}")
                    data = json.loads(payload)
                    data["event"] = event
                    self.hass.loop.call_soon_threadsafe(self.onpacket, data)
            elif message.lower() == "connected":
                self.reconnects = 0
                self.connected = True
        except Exception as e:  # noqa: BLE001
            # Bad frame must not kill the listener / trigger reconnect storms.
            _LOGGER.warning("ESPSomfy socket payload error: %s", e)

class ESPSomfyController(DataUpdateCoordinator):
    """Data coordinator/controller for receiving from ESPSomfy_RTS."""

    def __init__(
        self, config_entry_id, hass: HomeAssistant, api: ESPSomfyAPI
    ) -> None:
        """Initialize data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=DOMAIN,
            # The setting below is only for polling.
            # update_interval=timedelta(seconds=5),
        )
        self.config_entry_id = config_entry_id
        self.api = api
        self.ws_listener = None

    @property
    def device_name(self) -> str:
        """Get the device name from the host."""
        return self.api.deviceName

    @property
    def server_id(self) -> str:
        """Get the server id from the api."""
        return self.api.server_id

    @property
    def unique_id(self) -> str:
        """Get a unique id for the controller."""
        return f"espsomfy_{self.server_id}"

    @property
    def model(self) -> str:
        """Get the model for the controller."""
        return self.api.model

    @property
    def version(self) -> str:
        """Get the current version for the controller."""
        return self.api.version

    @property
    def latest_version(self) -> str:
        """Get the latest version for the controller."""
        return self.api.latest_version

    @property
    def check_for_update(self) -> bool:
        """Indicate whether the firmware should check for updates."""
        return self.api.check_for_update

    @property
    def internet_available(self) -> bool:
        """Indicates whether the ESPSomfy RTS hardware has internet access."""
        return self.api.internet_available

    @property
    def can_update(self) -> bool:
        """Get a flag that indicates whether the firmware can be updated."""
        return self.api.can_update

    async def ws_close(self) -> None:
        """Close the tasks and sockets."""
        if self.ws_listener is not None:
            self.ws_listener.close()

    async def ws_connect(self):
        """Connect to WebSocket."""
        if self.ws_listener is not None:
            self.ws_listener.close()
        self.ws_listener = SocketListener(
            self.hass,
            self.api.get_sock_url(),
            self.ws_onpacket,
            self.ws_onopen,
            self.ws_onclose,
            self.ws_onerror,
        )
        self.ws_listener.set_filter(
            [
                EVT_CONNECTED,
                EVT_SHADEADDED,
                EVT_SHADEREMOVED,
                EVT_SHADESTATE,
                EVT_SHADECOMMAND,
                EVT_GROUPSTATE,
                EVT_FIXEDCODESTATE,
                EVT_FIXEDCODEREMOVED,
                EVT_FWSTATUS,
                EVT_UPDPROGRESS,
                EVT_WIFISTRENGTH,
                EVT_ETHERNET,
                EVT_MEMSTATUS,
            ]
        )
        await self.ws_listener.connect()

    def _push_shade_state(self, shade_id: int) -> None:
        """Refresh HA entities from cached shade state without waiting for WS."""
        shade = self.api.get_shade(shade_id)
        if shade is None:
            return
        payload = dict(shade)
        payload["event"] = EVT_SHADESTATE
        self.async_set_updated_data(payload)

    async def async_update_shade_settings(
        self, shade_id: int, settings: dict[str, Any]
    ) -> bool:
        """Update shade settings on the ESP and refresh all HA entities."""
        ok = await self.api.update_shade_settings(shade_id, settings)
        if ok:
            self._push_shade_state(shade_id)
        return ok

    async def async_stop_shade(self, shade_id: int) -> bool:
        """Stop shade RF + freeze HA position/direction from the ACK immediately."""
        ok = await self.api.stop_shade(shade_id)
        if ok:
            shade = self.api.get_shade(shade_id)
            if shade is not None:
                shade["direction"] = 0
                shade["tiltDirection"] = 0
                if "position" in shade:
                    shade["target"] = shade["position"]
                if "tiltPosition" in shade:
                    shade["tiltTarget"] = shade["tiltPosition"]
            self._push_shade_state(shade_id)
        return ok

    async def async_stop_group(self, group_id: int) -> bool:
        """Stop group RF and refresh entities."""
        ok = await self.api.stop_group(group_id)
        if ok:
            self.async_set_updated_data(
                {"event": EVT_GROUPSTATE, "groupId": group_id, "direction": 0}
            )
        return ok

    async def async_set_current_position(self, shade_id: int, position: int) -> bool:
        """Calibrate reported position on ESP and refresh HA entities immediately."""
        ok = await self.api.set_current_position(shade_id, position)
        if ok:
            self._push_shade_state(shade_id)
        return ok

    async def async_set_current_tilt_position(
        self, shade_id: int, tilt_position: int
    ) -> bool:
        """Calibrate reported tilt on ESP and refresh HA entities immediately."""
        ok = await self.api.set_current_tilt_position(shade_id, tilt_position)
        if ok:
            self._push_shade_state(shade_id)
        return ok

    async def async_update_fixed_code_settings(
        self, switch_id: int, settings: dict[str, Any]
    ) -> bool:
        """Update FixedCode settings on the ESP and refresh HA entities."""
        ok = await self.api.update_fixed_code_settings(switch_id, settings)
        fc = self.api.get_fixed_code(switch_id)
        if fc is not None:
            payload = dict(fc)
            payload["event"] = EVT_FIXEDCODESTATE
            self.async_set_updated_data(payload)
        return ok

    async def create_backup(self) -> bool:
        """Create a backup of the configuration and stores it in HA."""
        return await self.api.create_backup()

    async def update_firmware(self, version) -> bool:
        """Start the firmware update process."""
        return await self.api.update_firmware(version)

    async def set_host(self, host) -> None:
        """Set a host name and reloads the sockets if the host has changed."""
        if self.api.get_host() != host:
            # Tear down the socket
            self.api.set_host(host)
            await self.ws_connect()

    def ensure_group_configured(self, data):
        """Ensure the group exists on Home Assistant."""
        uuid = f"{self.unique_id}_group{data['groupId']}"
        devices = dr.async_get(self.hass)
        room_name = self.api.get_room_name(self.api.get_room_id(data))
        display_name = self.api.format_entity_name(data)
        device_kwargs: dict[str, Any] = {
            "config_entry_id": self.config_entry_id,
            "identifiers": {(DOMAIN, f"group_{self.unique_id}_{data['groupId']}")},
            "via_device": (DOMAIN, self.unique_id),
            "manufacturer": MANUFACTURER,
            "name": display_name,
            "model": "ESPSomfy RTS Group",
        }
        if room_name:
            device_kwargs["suggested_area"] = room_name
        device = devices.async_get_or_create(**device_kwargs)
        entities = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(entities, self.config_entry_id):
            if entity.unique_id == uuid:
                return
        dev_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )

        # Reload all the shades
        # self.api.load_shades()
        # I have no idea whether this reloads the devices or not.
        entities.async_get_or_create(
            domain=DOMAIN,
            platform=Platform.COVER,
            original_device_class=CoverDeviceClass.SHADE,
            unique_id=uuid,
            device_id=device.id,
            original_name=display_name,
            suggested_object_id=self.api.suggest_object_id(data),
            supported_features=dev_features,
        )

    def ensure_shade_configured(self, data):
        """Ensure the shade exists on Home Assistant."""
        uuid = f"{self.unique_id}_{data['shadeId']}"

        devices = dr.async_get(self.hass)
        room_name = self.api.get_room_name(self.api.get_room_id(data))
        display_name = self.api.format_entity_name(data)
        device_kwargs: dict[str, Any] = {
            "config_entry_id": self.config_entry_id,
            "identifiers": {(DOMAIN, f"shade_{self.unique_id}_{data['shadeId']}")},
            "via_device": (DOMAIN, self.unique_id),
            "manufacturer": MANUFACTURER,
            "name": display_name,
            "model": "ESPSomfy RTS Device",
        }
        if room_name:
            device_kwargs["suggested_area"] = room_name
        device = devices.async_get_or_create(**device_kwargs)

        entities = er.async_get(self.hass)

        for entity in er.async_entries_for_config_entry(entities, self.config_entry_id):
            if entity.unique_id == uuid:
                return
        dev_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        dev_class = CoverDeviceClass.SHADE
        if "shadeType" in data:
            match int(data["shadeType"]):
                case 1:
                    dev_class = CoverDeviceClass.BLIND
                    if "tiltType" in data:
                        match int(data["tiltType"]):
                            case 1 | 2:
                                dev_features |= (
                                    CoverEntityFeature.OPEN_TILT
                                    | CoverEntityFeature.CLOSE_TILT
                                    | CoverEntityFeature.SET_TILT_POSITION
                                )
                    elif "hasTilt" in data and data["hasTilt"] is True:
                        dev_features |= (
                            CoverEntityFeature.OPEN_TILT
                            | CoverEntityFeature.CLOSE_TILT
                            | CoverEntityFeature.SET_TILT_POSITION
                        )
                case 2 | 7 | 8:
                    dev_class = CoverDeviceClass.CURTAIN
                case 3:
                    dev_class = CoverDeviceClass.AWNING
                case 4:
                    dev_class = CoverDeviceClass.SHUTTER
                case 5 | 6:
                    dev_class = CoverDeviceClass.GARAGE
                case 11 | 12 | 13 | 14 | 15 | 16:
                    dev_class = CoverDeviceClass.GATE
                case _:
                    dev_class = CoverDeviceClass.SHADE

        # Reload all the shades
        # self.api.load_shades()
        # I have no idea whether this reloads the devices or not.
        entities.async_get_or_create(
            domain=DOMAIN,
            platform=Platform.COVER,
            original_device_class=dev_class,
            unique_id=uuid,
            device_id=device.id,
            original_name=display_name,
            suggested_object_id=self.api.suggest_object_id(data),
            supported_features=dev_features,
        )

    def ws_onpacket(self, data):
        """Packet from the websocket."""
        # Below doesn't work.  Near as I can tell there is no
        # real way of adding an entity on the fly.  All this
        # does is add an entity that is not really attached.
        # if data["event"] == EVT_SHADEADDED:
        #    self.ensure_shade_configured(data)

        # Catch the fwStatus messages before they go anywhere
        # this will allow us to simply update the latest firmware
        if "event" in data and data["event"] == EVT_FWSTATUS:
            self.api.set_firmware(data)
        if data.get("event") == EVT_SHADESTATE:
            self.api.merge_shade_state(data)

        self.async_set_updated_data(data=data)

    def ws_onopen(self):
        """Websocket is opened."""
        _LOGGER.debug("ESPSomfy RTS Socket was opened")
        if self.api.is_configured:
            _LOGGER.debug("ESPSomfy RTS Already Configured")
            data = {"event": EVT_CONNECTED, "connected": True}
            self.async_set_updated_data(data=data)
        else:
            _LOGGER.debug("ESPSomfy RTS configuring entities")
            loop = asyncio.get_event_loop()
            coro = loop.create_task(self.api.get_initial())

            def handle_connected(_coro):
                data = {"event": EVT_CONNECTED, "connected": True}
                self.async_set_updated_data(data=data)

            coro.add_done_callback(handle_connected)

    def ws_onerror(self, exception):
        """Error on the socket connection."""
        data = {"event": EVT_CONNECTED, "connected": False}
        self.async_set_updated_data(data=data)

    def ws_onclose(self):
        """Socket closed."""
        data = {"event": EVT_CONNECTED, "connected": False}
        self.async_set_updated_data(data=data)


class ESPSomfyAPI:
    """API for sending data to ESPSomfy RTS hardware device."""

    def __init__(self, hass: HomeAssistant, config_entry_id, data) -> None:
        """Initialize the API."""
        self.hass = hass
        self.data = data
        self.set_host(data[CONF_HOST])
        self._config: Any = {}
        self._session = async_get_clientsession(self.hass, verify_ssl=False)
        self._authType = 0
        self._needsKey = False
        self._headers = {"apikey": ""}
        self._canLogin = False
        self._deviceName = data[CONF_HOST]
        self._can_update = False
        self._config_entry_id = config_entry_id
        self._configured = False
        # ESP RF TX is blocking; serialize HTTP so closing N covers all get sent.
        self._radio_lock = asyncio.Lock()

    @property
    def shades(self) -> Any:
        """Return the state shades."""
        if "shades" in self._config:
            return self._config["shades"]
        return []

    def get_shade(self, shade_id: int) -> dict[str, Any] | None:
        """Return cached shade dict by id."""
        for shade in self.shades:
            try:
                if int(shade.get("shadeId", -1)) == int(shade_id):
                    return shade
            except (TypeError, ValueError):
                continue
        return None

    def merge_shade_state(self, data: dict[str, Any]) -> None:
        """Merge a shadeState / shade payload into the cached shade list."""
        try:
            shade_id = int(data["shadeId"])
        except (KeyError, TypeError, ValueError):
            return
        skip = {"event", "ok", "cmdStatus", "stoppedMove", "status", "desc", "code"}
        shade = self.get_shade(shade_id)
        if shade is None:
            self._config.setdefault("shades", []).append(
                {k: v for k, v in data.items() if k not in skip}
            )
            return
        for key, value in data.items():
            if key in skip:
                continue
            shade[key] = value

    def shade_is_idle(self, shade_id: int) -> bool:
        """True when shade is not currently moving (lift or tilt)."""
        shade = self.get_shade(shade_id)
        if shade is None:
            return True
        try:
            return int(shade.get("direction", 0)) == 0 and int(
                shade.get("tiltDirection", 0)
            ) == 0
        except (TypeError, ValueError):
            return True

    @property
    def groups(self) -> Any:
        """Return the state groups."""
        if "groups" in self._config:
            return self._config["groups"]
        return []

    @property
    def fixed_codes(self) -> Any:
        """Return fixed-code RF switches."""
        if "fixedCodes" in self._config:
            return self._config["fixedCodes"]
        return []

    @property
    def rooms(self) -> Any:
        """Return rooms from discovery."""
        if "rooms" in self._config:
            return self._config["rooms"]
        return []

    def get_room_id(self, data: dict | None) -> int:
        """Extract roomId from shade/group payload."""
        if not data:
            return 0
        try:
            return int(data.get("roomId") or 0)
        except (TypeError, ValueError):
            return 0

    def get_room_name(self, room_id: int | None) -> str | None:
        """Resolve a room display name from discovery rooms."""
        if not room_id:
            return None
        for room in self.rooms:
            try:
                if int(room.get("roomId", 0)) == int(room_id):
                    name = room.get("name")
                    if name:
                        return str(name)
            except (TypeError, ValueError, AttributeError):
                continue
        return None

    def format_entity_name(self, data: dict | None) -> str:
        """Return the device name only (room is suggested_area, not part of the name)."""
        if not data:
            return "Unknown"
        base = str(data.get("name") or "").strip()
        return base or "Unknown"

    def suggest_object_id(self, data: dict | None, suffix: str | None = None) -> str:
        """Suggest an entity object id from the device name."""
        from homeassistant.util import slugify

        object_id = slugify(self.format_entity_name(data))
        if suffix:
            object_id = f"{object_id}_{slugify(suffix)}"
        return object_id

    @property
    def server_id(self) -> str | None:
        """Getter for the server id."""
        if "serverId" in self._config:
            return self._config["serverId"]

    @property
    def version(self) -> str:
        """Getter for the api version."""
        if "version" in self._config:
            return self._config["version"]
        if "fwVersion" in self._config:
            return self._config["fwVersion"]
        return "0.0.0"

    @property
    def latest_version(self) -> str | None:
        """Getter for the latest version."""
        if "latest" in self._config:
            if self._config["latest"] == "":
                return None
            return self._config["latest"]
        return None

    @property
    def model(self) -> str:
        """Getter for the model number."""
        return self._config.get("model") or "ESPSomfy RTS"

    @property
    def apiKey(self) -> str:
        """Getter for the api key."""
        return self._config.get("apiKey") or ""

    @property
    def deviceName(self) -> str:
        """Getter for the device name."""
        return self._deviceName

    @property
    def can_update(self) -> bool:
        """Getter for whether the firmware is updatable."""
        return self._can_update

    @property
    def backup_dir(self) -> str:
        """Gets the backup directory for the device."""
        return self.hass.config.path(f"ESPSomfyRTS_{self.server_id}")

    @property
    def check_for_update(self) -> bool:
        """Check for the current update."""
        if "checkForUpdate" in self._config:
            return self._config["checkForUpdate"]
        return self._can_update

    @property
    def internet_available(self) -> bool:
        """Check to see if the ESPSomfy RTS hardware has internet."""
        if "inetAvailable" in self._config:
            return self._config["inetAvailable"]
        # Unknown until fwStatus arrives — do not treat as online.
        return False

    @property
    def is_configured(self) -> bool:
        """Indicates whether the integration has been configured."""
        return self._configured

    def set_host(self, host) -> None:
        """Set the host for the integration."""
        self._host = host
        self._sock_url = f"ws://{self._host}:8080"
        self._api_url = f"http://{self._host}:8081"
        self._config_url = f"http://{self._host}"

    def get_host(self):
        """Get the current host."""
        return self._host

    def get_sock_url(self):
        """Get the socket interface url."""
        return self._sock_url

    def get_api_url(self):
        """Get that url used for api reference."""
        return self._api_url

    def get_config_url(self) -> str:
        """Get the configuration url."""
        return self._config_url

    def get_config(self):
        """Return the initial config."""
        return self._config

    def get_data(self):
        """Return the internal data."""
        return self.data

    def set_firmware(self, data) -> None:
        """Set the firmware data from the socket."""
        cver = "0.0.0"
        if "version" in self._config:
            cver = self._config["version"]
        new_ver = cver
        if "fwVersion" in data:
            new_ver = data["fwVersion"]
            if "name" in new_ver:
                new_ver = new_ver["name"]
        elif "version" in data:
            new_ver = data["version"]
        if "latest" in data:
            latest_ver = data["latest"]
            if "name" in latest_ver:
                latest_ver = latest_ver["name"]
            self._config["latest"] = latest_ver
        if "checkForUpdate" in data:
            self._config["checkForUpdate"] = data["checkForUpdate"]
        if "inetAvailable" in data:
            self._config["inetAvailable"] = data["inetAvailable"]
        if cver != new_ver:
            # print(f"Version: {cver} to {new_ver}")
            dev_registry = dr.async_get(self.hass)
            if dev := dev_registry.async_get_device(
                identifiers={(DOMAIN, f"espsomfy_{self.server_id}")}
            ):
                dev_registry.async_update_device(dev.id, sw_version=new_ver)
        self._config["version"] = new_ver
        v = parse_firmware_version(new_ver)
        if (
            (v.major > 2)
            or (v.major == 2 and v.minor > 2)
            or (v.major == 2 and v.minor == 2 and v.micro > 0)
        ):
            self._can_update = True
        else:
            self._can_update = False

    async def check_address(self, url) -> bool:
        """Send a head to a url to check if it exists."""
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            # We don't care to do anything other than catch expections here.  If anything goes wrong there is an issue
            # with the address and we should not use it.
            pass
        return False

    async def update_firmware(self, version) -> bool:
        "Update to the latest firmware version."
        url = f"{self._api_url}/downloadFirmware?ver={version}"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status == 200:
                return True
            _LOGGER.error(await resp.text())
        return False

    async def create_backup(self) -> bool:
        """Create a backup."""
        try:
            url = f"{self._api_url}/backup?attach=true"
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status != 200:
                    return False

                os.makedirs(self.backup_dir, exist_ok=True)

                data = await resp.read()
                local_dt = dt_util.as_local(datetime.now(dt_util.UTC))
                fpath = self.hass.config.path(
                    f"{self.backup_dir}/{local_dt.strftime('%Y-%m-%dT%H_%M_%S')}.backup"
                )
            async with aiofiles.open(fpath, mode="wb+") as f:
                await f.write(data)
                return True
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("An error occurred while creating backup: %s", e)
            return False

    def get_backups(self) -> list[str] | None:
        """Get a list of all the available backups."""
        f: list[str] = []
        if not os.path.exists(self.backup_dir):
            return None
        files = os.listdir(self.backup_dir)
        f = [
            file
            for file in files
            if os.path.isfile(os.path.join(self.backup_dir, file))
            and file.endswith(".backup")
            and file[:1].isdigit()
        ]
        f.sort(reverse=True)
        return f

    def apply_data(self, data) -> None:
        """Apply the returned data to the configuration."""
        self._config["serverId"] = data["serverId"]
        self._config["model"] = data["model"]
        if "chipModel" in data:
            self._config["chipModel"] = data["chipModel"]
        if "connType" in data:
            self._config["connType"] = data["connType"]
        if "checkForUpdate" in data:
            self._config["checkForUpdate"] = data["checkForUpdate"]
        if "rooms" in data:
            self._config["rooms"] = data["rooms"]
        elif "rooms" not in self._config:
            self._config["rooms"] = []
        if "shades" in data:
            self._config["shades"] = data["shades"]
        elif "shades" not in self._config:
            self._config["shades"] = []
        if "groups" in data:
            self._config["groups"] = data["groups"]
        elif "groups" not in self._config:
            self._config["groups"] = []
        if "fixedCodes" in data:
            self._config["fixedCodes"] = data["fixedCodes"]
        elif "fixedCodes" not in self._config:
            self._config["fixedCodes"] = []
        if "maxFixedCodes" in data:
            self._config["maxFixedCodes"] = data["maxFixedCodes"]
        if "hostname" in data:
            self._config["hostname"] = data["hostname"]
            self._deviceName = data["hostname"]
        if "authType" in data:
            self._config["authType"] = data["authType"]
            self._canLogin = True
        elif "authType" not in self._config:
            self._config["authType"] = 0
            self._canLogin = False
        if "permissions" in data:
            self._config["permissions"] = data["permissions"]
        elif "permissions" not in self._config:
            self._config["permissions"] = 1
        if "memory" in data:
            self._config["memory"] = data["memory"]
        self._needsKey = False
        if self._config["authType"] > 0:
            if self._config["permissions"] != 1:
                self._needsKey = True
        self.set_firmware(data)

    async def discover(self) -> Any | None:
        """Discover the device on the network."""
        url = f"{self._api_url}{API_DISCOVERY}"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                self.apply_data(data)
                return data
            _LOGGER.error(await resp.text())
            raise DiscoveryError(f"{url} - {await resp.text()}")

    async def load_shades(self) -> Any | None:
        """Load all the shades from the controller."""
        async with self._session.get(f"{self._api_url}{API_SHADES}", headers=self._headers) as resp:
            if resp.status == 200:
                self._config["shades"] = await resp.json()
                return self._config["shades"]
            _LOGGER.error(await resp.text())

    async def load_groups(self) -> Any | None:
        """Load all the groups from the controller."""
        async with self._session.get(f"{self._api_url}{API_GROUPS}", headers=self._headers) as resp:
            if resp.status == 200:
                self._config["groups"] = await resp.json()
                return self._config["groups"]
            _LOGGER.error(await resp.text())

    async def load_fixed_codes(self) -> Any | None:
        """Load all fixed-code RF switches from the controller."""
        async with self._session.get(f"{self._api_url}{API_FIXEDCODES}", headers=self._headers) as resp:
            if resp.status == 200:
                self._config["fixedCodes"] = await resp.json()
                return self._config["fixedCodes"]
            _LOGGER.error(await resp.text())

    async def tilt_open(self, shade_id: int):
        """Send the command to open the tilt."""
        await self.tilt_command({"shadeId": shade_id, "command": "up"})

    async def tilt_close(self, shade_id: int):
        """Send the command to close the tilt."""
        await self.tilt_command({"shadeId": shade_id, "command": "down"})

    async def position_tilt(self, shade_id: int, position: int):
        """Send the command to position the shade."""
        # print(f"Setting tilt position to {position}")
        await self.tilt_command({"shadeId": shade_id, "target": position})

    async def sun_flag_off(self, shade_id: int):
        """Send the command to turn off the sun flag."""
        await self.shade_command({"shadeId": shade_id, "command": "flag"})

    async def sun_flag_on(self, shade_id: int):
        """Send the command to turn off the sun flag."""
        await self.shade_command({"shadeId": shade_id, "command": "sunflag"})

    async def sun_flag_group_off(self, group_id: int):
        """Send the command to turn off the sun flag."""
        await self.group_command({"groupId": group_id, "command": "flag"})

    async def sun_flag_group_on(self, group_id: int):
        """Send the command to turn off the sun flag."""
        await self.group_command({"groupId": group_id, "command": "sunflag"})

    async def open_shade(self, shade_id: int):
        """Send the command to open the shade."""
        await self.shade_command({"shadeId": shade_id, "command": "up"})

    async def close_shade(self, shade_id: int):
        """Send the command to close the shade."""
        await self.shade_command({"shadeId": shade_id, "command": "down"})

    async def toggle_shade(self, shade_id: int):
        """Sent the command to toggle."""
        await self.shade_command({"shadeId": shade_id, "command": "toggle"})

    async def stop_shade(self, shade_id: int) -> bool:
        """Send RF stop (never favorite / My-when-idle)."""
        return await self.shade_command({"shadeId": shade_id, "command": "stop"})

    async def open_group(self, group_id: int):
        """Send the command to open the group."""
        await self.group_command({"groupId": group_id, "command": "up"})

    async def close_group(self, group_id: int):
        """Send the command to close the group."""
        await self.group_command({"groupId": group_id, "command": "down"})

    async def stop_group(self, group_id: int) -> bool:
        """Send RF stop for the group (never favorite)."""
        return await self.group_command({"groupId": group_id, "command": "stop"})

    async def position_shade(self, shade_id: int, position: int):
        """Send the command to position the shade."""
        await self.shade_command({"shadeId": shade_id, "target": position})

    async def raw_command(self, shade_id: int, command: str, repeat: int):
        """Send the command to the shade."""
        await self.shade_command(
            {"shadeId": shade_id, "command": command, "repeat": repeat}
        )

    async def shade_command(self, data):
        """Send commands to ESPSomfyRTS via PUT request."""
        return await self.put_command(API_SHADECOMMAND, data)

    async def _fixed_code_command(self, switch_id: int, state: str) -> bool:
        """Transmit fixed-code ON/OFF/toggle (firmware may 429 if too frequent)."""
        return await self.put_command(API_FIXEDCODECOMMAND, {"id": switch_id, "state": state})

    async def fixed_code_on(self, switch_id: int) -> bool:
        """Transmit ON for a fixed-code RF switch."""
        return await self._fixed_code_command(switch_id, "on")

    async def fixed_code_off(self, switch_id: int) -> bool:
        """Transmit OFF for a fixed-code RF switch."""
        return await self._fixed_code_command(switch_id, "off")

    async def fixed_code_toggle(self, switch_id: int) -> bool:
        """Transmit toggle for a single-button fixed-code RF switch."""
        return await self._fixed_code_command(switch_id, "toggle")

    async def update_fixed_code_settings(
        self, switch_id: int, settings: dict[str, Any]
    ) -> bool:
        """Update FixedCode settings on the ESP (invert / single-button)."""
        payload = {"id": switch_id, **settings}
        async with self._session.put(
            f"{self._api_url}{API_SAVEFIXEDCODE}", json=payload, headers=self._headers
        ) as resp:
            text = await resp.text()
            body: Any = text
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                if text:
                    body = json.loads(text)
            ok = self._log_command_result(API_SAVEFIXEDCODE, payload, resp.status, body)
            if ok and isinstance(body, dict) and body.get("id"):
                codes = list(self.fixed_codes or [])
                for i, fc in enumerate(codes):
                    if int(fc.get("id", -1)) == switch_id:
                        codes[i] = {**fc, **body}
                        break
                else:
                    codes.append(body)
                self._config["fixedCodes"] = codes
            return ok

    async def set_current_position(self, shade_id: int, position: int) -> bool:
        """Set the current position without moving the motor; merge device ACK."""
        payload = {"shadeId": shade_id, "position": position}
        async with self._session.put(
            f"{self._api_url}{API_SETPOSITIONS}", json=payload, headers=self._headers
        ) as resp:
            text = await resp.text()
            body: Any = text
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                if text:
                    body = json.loads(text)
            ok = self._log_command_result(API_SETPOSITIONS, payload, resp.status, body)
            if ok and isinstance(body, dict):
                self.merge_shade_state(body)
            return ok

    async def set_current_tilt_position(
        self, shade_id: int, tilt_position: int
    ) -> bool:
        """Set the current tilt position without moving the motor; merge device ACK."""
        payload = {"shadeId": shade_id, "tiltPosition": tilt_position}
        async with self._session.put(
            f"{self._api_url}{API_SETPOSITIONS}", json=payload, headers=self._headers
        ) as resp:
            text = await resp.text()
            body: Any = text
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                if text:
                    body = json.loads(text)
            ok = self._log_command_result(API_SETPOSITIONS, payload, resp.status, body)
            if ok and isinstance(body, dict):
                self.merge_shade_state(body)
            return ok

    async def set_sunny(self, shade_id: int, sunny: bool):
        """Set the sunny condition for the motor."""
        await self.put_command(API_SETSENSOR, {"shadeId": shade_id, "sunny": sunny})

    async def set_windy(self, shade_id: int, windy: bool):
        """Set the windy condition for the motor."""
        await self.put_command(API_SETSENSOR, {"shadeId": shade_id, "windy": windy})

    def _log_command_result(self, endpoint: str, data: dict, status: int, body: Any) -> bool:
        """Interpret device ACK; return False if command was not accepted."""
        if status == 409 or (
            isinstance(body, dict) and body.get("cmdStatus") == "busy"
        ):
            desc = body.get("desc") if isinstance(body, dict) else None
            _LOGGER.warning(
                "Device rejected %s %s (busy%s)",
                endpoint,
                data,
                f": {desc}" if desc else "",
            )
            return False
        if status == 429 or (
            isinstance(body, dict)
            and (
                body.get("ok") is False
                or body.get("cmdStatus") == "rate_limited"
            )
        ):
            retry = None
            if isinstance(body, dict):
                retry = body.get("retryAfterMs")
            _LOGGER.warning(
                "Device rejected %s %s (HTTP %s, rate_limited%s)",
                endpoint,
                data,
                status,
                f", retry_after_ms={retry}" if retry is not None else "",
            )
            return False
        if status != 200:
            _LOGGER.error("Device error on %s %s: HTTP %s %s", endpoint, data, status, body)
            return False
        if isinstance(body, dict) and body.get("ok") is False:
            _LOGGER.warning("Device reported failure on %s %s: %s", endpoint, data, body)
            return False
        return True

    async def put_command(self, command, data):
        """Send a put command to the device and honor ok/rate_limited ACK.

        Radio TX on the ESP is blocking; one lock so multi-cover Close all get
        through.
        """
        async with self._radio_lock:
            async with self._session.put(
                f"{self._api_url}{command}", json=data, headers=self._headers
            ) as resp:
                text = await resp.text()
                body: Any = text
                with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                    if text:
                        body = json.loads(text)
                ok = self._log_command_result(command, data, resp.status, body)
                # Shade command ACK includes live position/direction — merge so HA
                # does not keep animating until the next WebSocket shadeState.
                if (
                    ok
                    and command == API_SHADECOMMAND
                    and isinstance(body, dict)
                    and body.get("shadeId") is not None
                ):
                    merged = dict(body)
                    if str(data.get("command", "")).lower() == "stop":
                        merged["direction"] = 0
                        merged["tiltDirection"] = 0
                        if "position" in merged:
                            merged["target"] = merged["position"]
                        if "tiltPosition" in merged:
                            merged["tiltTarget"] = merged["tiltPosition"]
                    self.merge_shade_state(merged)
                return ok

    def get_fixed_code(self, switch_id: int) -> dict[str, Any] | None:
        """Return cached fixed-code switch dict by id."""
        for fc in self.fixed_codes or []:
            try:
                if int(fc.get("id", -1)) == int(switch_id):
                    return fc
            except (TypeError, ValueError):
                continue
        return None

    async def update_shade_settings(
        self, shade_id: int, settings: dict[str, Any]
    ) -> bool:
        """Update shade config on the device (invert / travel times).

        ESP stops any in-progress move, then applies settings. ESP is source of
        truth; HA must not also invert commands/positions.
        """
        payload = {"shadeId": shade_id, **settings}
        async with self._session.put(
            f"{self._api_url}{API_SHADE}", json=payload, headers=self._headers
        ) as resp:
            text = await resp.text()
            body: Any = text
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                if text:
                    body = json.loads(text)
            ok = self._log_command_result(API_SHADE, payload, resp.status, body)
            if ok and isinstance(body, dict):
                self.merge_shade_state(body)
                if body.get("stoppedMove"):
                    _LOGGER.info(
                        "Stopped shade %s before applying settings %s",
                        shade_id,
                        list(settings),
                    )
            return ok

    async def login(self, data):
        """Log in to the ESPSomfy hardware device."""
        async with self._session.put(
            f"{self._api_url}{API_LOGIN}", json=data
        ) as resp:
            if resp.status != 200:
                _LOGGER.error("Error logging in: %s", await resp.text())
                raise LoginError(CONF_HOST, "login_error")
            data = await resp.json()
            if data.get("success"):
                if "apiKey" in data:
                    self._config["apiKey"] = self._headers["apikey"] = data["apiKey"]
                return
            if data.get("type") == 1:
                raise LoginError(CONF_PIN, "invalid_pin")
            if data.get("type") == 2:
                raise LoginError(CONF_USERNAME, "invalid_password")
            raise LoginError(CONF_HOST, "invalid_login")

    async def group_command(self, data):
        """Send commands to ESPSomfyRTS via PUT request."""
        return await self.put_command(API_GROUPCOMMAND, data)

    async def room_command(self, room_id: int, command: str):
        """Open/close/my/stop every shade in a room (queued on the ESP)."""
        cmd = str(command or "").lower()
        if cmd == "open":
            cmd = "up"
        elif cmd == "close":
            cmd = "down"
        return await self.put_command(
            API_ROOMCOMMAND, {"roomId": int(room_id), "command": cmd}
        )

    async def scene_command(self, scene_id: int):
        """Apply a named scene stored on the controller."""
        return await self.put_command(API_SCENECOMMAND, {"id": int(scene_id)})

    async def tilt_command(self, data):
        """Send tilt commands to ESPSomfyRTS via PUT request."""
        return await self.put_command(API_TILTCOMMAND, data)

    async def get_initial(self):
        """Get the initial config from ESPSomfy RTS."""
        try:
            self._session = aiohttp_client.async_get_clientsession(self.hass)
            creds = {
                "username": self.data.get(CONF_USERNAME, ""),
                "password": self.data.get(CONF_PASSWORD, ""),
                "pin": self.data.get(CONF_PIN, ""),
            }
            if any(creds.values()):
                with contextlib.suppress(LoginError, aiohttp.ClientError):
                    await self.login(creds)
            async with self._session.get(
                f"{self._api_url}{API_DISCOVERY}", headers=self._headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.apply_data(data)
                    entry = self.hass.config_entries.async_get_entry(
                        self._config_entry_id
                    )
                    if not self._configured:
                        _LOGGER.debug("ESPSomfy RTS Setting up entities")
                        await self.hass.config_entries.async_forward_entry_setups(
                            entry, PLATFORMS
                        )
                        self._configured = True
                else:
                    _LOGGER.error(await resp.text())
        except aiohttp.ClientError:
            pass


class InvalidHost(HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""


class DiscoveryError(HomeAssistantError):
    """Error that occurred during discovery."""


class LoginError(HomeAssistantError):
    """Error that occurs when login fails."""
