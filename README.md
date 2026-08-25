# ESPSomfy RTS — Home Assistant

Fork of [rstrouse/ESPSomfy-RTS-HA](https://github.com/rstrouse/ESPSomfy-RTS-HA). **This is the recommended Home Assistant integration** for the [community ESPSomfy-RTS firmware](https://github.com/jcvsite/ESPSomfy-RTS). Same REST **:8081** + WebSocket **:8080** controller, with invert, travel times, calibrate, cover pictures, and fixed-code RF switches.

The original HA component still does open / stop / close. Use this fork instead — it is the improved version and matches the forked firmware.

[![GitHub Release](https://img.shields.io/github/release/jcvsite/ESPSomfy-RTS-HA.svg?style=for-the-badge)](https://github.com/jcvsite/ESPSomfy-RTS-HA/releases)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/jcvsite/ESPSomfy-RTS-HA.svg?style=for-the-badge)](LICENSE)

Integration **v2.6.1**. Details: [CHANGELOG.md](CHANGELOG.md). Requires firmware **[jcvsite/ESPSomfy-RTS](https://github.com/jcvsite/ESPSomfy-RTS) v3.4.5+** (room covers / scenes: **v3.4.0+**).

**Repository:** [github.com/jcvsite/ESPSomfy-RTS-HA](https://github.com/jcvsite/ESPSomfy-RTS-HA)

## What changed vs the original

| | Original HA | This fork |
|---|---|---|
| Open / Stop / Close | Yes | Yes |
| Position scale | Motor-native | **100% open / 0% closed** (same as the device UI and HA covers) |
| Stop while moving | `my` (can go to favorite) | API **`stop`** — actually stops |
| Invert / travel / calibrate | No (or HA-only) | Config entities **synced to the ESP** |
| Cover pictures | Generic MDI | Type + open / partial / closed graphics (curtain, blind, shutter, awning, garage, gate) |
| Entity names | Often `Room-Name` | Device name only; room is a suggested HA area |
| Room covers | No | **One cover per room** (Open / Stop / Close, queued on the ESP) |
| Scenes | No | Service **`apply_scene`** (scenes are saved on the controller) |
| Fixed-code RF switches | No | Yes (learn / TX 433 MHz ON/OFF) |
| Required firmware | stock ESPSomfy-RTS | [jcvsite/ESPSomfy-RTS](https://github.com/jcvsite/ESPSomfy-RTS) |

Unchanged: mDNS/SSDP discovery, shade/group/sun/dry-contact entities, and the original wiki for first-time pairing. Shade services and events are documented in the [upstream README](https://github.com/rstrouse/ESPSomfy-RTS-HA).

## Requirements

- ESP32 + CC1101 running the [community firmware](https://github.com/jcvsite/ESPSomfy-RTS)
- Home Assistant with HACS, or a manual `custom_components` install

Do not mix this integration with stock firmware, or stock HA with this firmware, if you want invert, calibrate, pictures, or RF switches.

## Installation

Copy `custom_components/espsomfy_rts` into your HA `config/custom_components/` directory (or add this repo as a HACS custom repository), then restart Home Assistant.

Configure the device under **Settings → Devices & Services** (auto-discovery via mDNS/SSDP still works).

After adding or learning RF switches on the controller web UI, **reload the ESPSomfy RTS integration** so new switch entities appear.

**MQTT-only alternative:** if MQTT discovery is enabled on the controller, RF switches can appear as MQTT `switch` entities without this custom component. This fork is for the native ESPSomfy HA integration.

## Entity naming and rooms

Shades, groups, and related entities use the **device name only** (as set on the controller). The room is applied as a Home Assistant **suggested area**, not as a name prefix.

Reload the integration after changing names or rooms. Existing entity IDs may be rewritten to match the bare device name when possible.

## Cover visuals and position

Each shade cover shows:

- **Open / Stop / Close** plus a **position slider** (move the motor)
- An **icon and window picture** that change with shade type (curtain, blind, roller, shutter, awning, garage, gate) and open / partial / closed — similar to the Somfy web UI
- **Calibrate position** (Configuration) — a **slider** that only updates the reported % (no motor move). Use when the curtain is open in reality but HA says closed.

## Shade settings (synced with the controller)

Pair motors and do first-time setup in the ESPSomfy web UI. After that, these config entities on each shade device stay in sync with the controller.

### Remote layout (never changes)

HA always shows **Open / Stop / Close** (same as Up / Stop / Down on a Somfy remote):

| Button | Meaning | HA → ESP command |
|---|---|---|
| **Open** | Open the shade | `up` |
| **Stop** | Stop | `stop` (while moving) / `my` |
| **Close** | Close the shade | `down` |

The button layout does **not** swap when you invert. Only the underlying RF (and/or position scale) on the ESP changes.

### Invert switches

| HA entity | ESP setting | What it fixes |
|---|---|---|
| **Invert open/close** | `flipCommands` | Open was closing / Close was opening **and** status showed the wrong opening/closing. ESP swaps RF Up↔Down. HA still sends Open=`up`, Close=`down`. |
| **Invert position scale** | `flipPosition` | Open/closed **percentage** still wrong after open/close is correct. |
| **Full open / close time** | `upTime` / `downTime` | Travel time in seconds (HA) ↔ ms (ESP). |

**Keep them independent — do not auto-link.**

| Symptom | Use |
|---|---|
| Open closes + UI shows **closing** | **Invert open/close** only |
| % still wrong when fully open/closed | Then **Invert position scale** |

### Opening / closing text vs position %

These are different signals from the ESP:

- **Opening / closing / stopped** ← movement `direction` (`-1` / `+1` / `0`), driven by Open/Close/Stop
- **How far open (0–100%)** ← `position` (HA cover always uses 100 = open, 0 = closed)

So **position is not the same thing as opening/closing direction**. Invert direction fixes which way the motor runs when you press Open/Close. Invert position adjusts the % scale if that reading is backwards.

### Your typical fix (reversed open/close + wrong status)

> Open closed the shade, and while it moved the UI showed **closing** instead of **opening**.

That is one setting in HA:

1. Open the shade device → **Configuration**
2. Turn on **Invert open/close** (writes `flipCommands` to the ESP)
3. Press **Open** — motor should open and status should show **opening**
4. Leave **Invert position scale** off unless the 0–100% reading is still wrong when fully open/closed

Buttons stay **Open / Stop / Close**. Only the ESP RF mapping changes.

You can change these anytime. If the shade is moving, the controller **stops it first**, then saves. Somfy UI and HA stay in sync over the WebSocket.

## RF switch behaviour

- Entities are HA `switch` devices named from the controller
- Unavailable until ON/OFF codes are learned (`ready`)
- Single-button remotes use toggle TX for both ON and OFF
- State is software-tracked after transmit (no RF feedback)
- Anti-spam matches firmware (1.5 s same command, 0.4 s ON↔OFF reverse)
- Configuration (synced to ESP):
  - **Invert ON/OFF** — when On turns the device Off
  - **Single-button remote** — on = one learned code; off = use both ON and OFF codes

## Firmware

Use **[jcvsite/ESPSomfy-RTS](https://github.com/jcvsite/ESPSomfy-RTS) v3.4.5+**. First install from the original or another firmware fork needs a **USB / full flash** (new partition map) — see the firmware README. Hardware, pairing, and MQTT basics remain in the [original wiki](https://github.com/rstrouse/ESPSomfy-RTS/wiki).

## Alexa / voice

Expose this integration’s `cover` entities via **Home Assistant Cloud** Alexa (or a manual Alexa Smart Home skill), then discover devices in the Alexa app. Device class (blind/shade/curtain vs shutter/awning) controls Interior vs Exterior Blind in Alexa.

If you do **not** use Home Assistant, the firmware can optionally run a **fake Philips Hue bridge** on the Router (**Network → Alexa**: master toggle + shade list). Those appear as lights (“turn on” / brightness), not as blinds — see the firmware README.
