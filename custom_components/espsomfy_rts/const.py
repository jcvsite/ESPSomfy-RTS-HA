"""Constants for the ESPSomfy RTS integration."""

from homeassistant.const import Platform

VERSION = "2.6.1"
DOMAIN = "espsomfy_rts"
MANUFACTURER = "ESPSomfy"

# Cover Open/Close/Stop: always clickable, or greyed by HA from open/closed/moving.
CONF_CONTROL_MODE = "control_mode"
CONTROL_MODE_ALWAYS = "always"
CONTROL_MODE_FOLLOW_STATUS = "follow_status"
DEFAULT_CONTROL_MODE = CONTROL_MODE_ALWAYS
API_CONTROLLER = "/controller"
API_SHADES = "/shades"
API_GROUPS = "/groups"
API_FIXEDCODES = "/fixedCodes"
API_SHADE = "/shade"
API_SHADECOMMAND = "/shadeCommand"
API_GROUPCOMMAND = "/groupCommand"
API_ROOMCOMMAND = "/roomCommand"
API_SCENECOMMAND = "/sceneCommand"
API_TILTCOMMAND = "/tiltCommand"
API_FIXEDCODECOMMAND = "/fixedCodeCommand"
API_SAVEFIXEDCODE = "/saveFixedCode"
API_DISCOVERY = "/discovery"
API_LOGIN = "/login"
API_SETPOSITIONS = "/setPositions"
API_SETSENSOR = "/setSensor"
API_BACKUP = "/backup"
API_REBOOT = "/reboot"
EVT_CONTROLLER = "controller"
EVT_SHADESTATE = "shadeState"
EVT_GROUPSTATE = "groupState"
EVT_FIXEDCODESTATE = "fixedCodeState"
EVT_FIXEDCODEREMOVED = "fixedCodeRemoved"
EVT_SHADECOMMAND = "shadeCommand"
EVT_SHADEADDED = "shadeAdded"
EVT_SHADEREMOVED = "shadeRemoved"
EVT_CONNECTED = "connected"
EVT_FWSTATUS = "fwStatus"
EVT_UPDPROGRESS = "updateProgress"
EVT_WIFISTRENGTH = "wifiStrength"
EVT_ETHERNET = "ethernet"
EVT_MEMSTATUS = "memStatus"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]
