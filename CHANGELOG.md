# Changelog — ESPSomfy-RTS-HA (community fork)

Fork of [rstrouse/ESPSomfy-RTS-HA](https://github.com/rstrouse/ESPSomfy-RTS-HA) for use with [jcvsite/ESPSomfy-RTS](https://github.com/jcvsite/ESPSomfy-RTS) firmware **v3.4.5+**.

---

## 2.6.1 — 2026-08-16

Initial public release of this Home Assistant integration fork (**v2.6.1**). Pair with firmware **[jcvsite/ESPSomfy-RTS](https://github.com/jcvsite/ESPSomfy-RTS) v3.4.5+** (room covers / scenes need **v3.4.0+**).

### Covers and control
- Position scale **100% open / 0% closed** (matches device UI and HA covers)
- Cover Stop uses API **`stop`** (actually stops; not favorite My while moving)
- Invert open/close, invert position %, travel times, and calibrate — config entities **synced to the ESP**
- Cover pictures by type (curtain, blind, shutter, awning, garage, gate) with open / partial / closed graphics
- Entity names use the device name only; room is a suggested HA area (not a name prefix)
- **Always enable controls** option (default) so Open/Close/Stop stay usable while moving; optional follow-status greying
- Multi-cover Close is serialized to the ESP so parallel requests do not race the RF TX

### Rooms, scenes, and FixedCode
- **One cover per room** (Open / Stop / Close; firmware RF queue)
- Services **`apply_scene`** and **`room_command`** (`host=` when more than one hub is configured)
- Fixed-code RF switches (learn / TX 433 MHz ON/OFF), invert ON/OFF, single-button remotes, anti-spam matching firmware

### Reliability and packaging
- Manifest, issues, docs, and firmware-update links point at this fork (`jcvsite`)
- Brand icon/logo for HACS / HA
- API calls send the login apikey; reload logs in when credentials are stored
- Firmware Install uses the selected version; release notes link to `jcvsite/ESPSomfy-RTS`
- WebSocket: mark connected only after the socket opens; bad frames no longer kill the listener
- Room covers honor awning semantics and Invert %; calibrate number uses the same HA↔ESP mapping as the cover service
- Translations: en / de / es / fr
- Docs: Alexa via HA Cloud; firmware Hue bridge under Network → Alexa (no-HA alternative)
- Cover registry `device_class` aligns with shade types (shutter / garage / gate)
