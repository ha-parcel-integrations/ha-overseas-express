# Overseas Express Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-overseas-express.svg)](https://github.com/ha-parcel-integrations/ha-overseas-express/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-overseas-express/total.svg)](https://github.com/ha-parcel-integrations/ha-overseas-express/releases)
[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

> **Warning — pre-release.** The tracking data has been confirmed on a single parcel at its first status (`Created`). Every later status is mapped from the carrier's own status list and has not been seen on a real parcel yet. Statuses the integration does not recognise are logged as warnings with a link to report them.

A custom Home Assistant integration that tracks your [Overseas Express](https://overseas.hr) (Croatia) parcels. No account is needed — you enter the tracking code yourself, just like on the Overseas Express tracking page.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Overseas Express parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar (stays empty until Overseas Express reports an expected delivery time)
- `overseas_express.track_parcel` / `overseas_express.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- An Overseas Express parcel and its tracking code (usually 14 digits, from
  the sender's shipping notification) — no account needed
- A network the carrier's tracking page accepts. Some data-centre networks
  are refused; a home or Croatian connection is fine.

## Installation

### Marketplace (recommended)

Click the button below to open this repository in the Marketplace, then install **Overseas Express**:

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-overseas-express&category=integration)

Or add it by hand: in the Marketplace, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-overseas-express` as an **Integration** and install **Overseas Express**.

Restart Home Assistant if it asks you to.

### Manual

Copy `custom_components/overseas_express` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=overseas_express)

Add the integration via **Settings → Devices & Services → Add Integration → Overseas Express**. There is nothing to fill in: the hub is created immediately (Overseas Express tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`overseas_express.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is in the sender's shipping notification.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Overseas Express
  hub out there polls at exactly the same second.

Overseas Express reports no expected delivery time, so a parcel that is out for delivery is polled every 15 minutes straight away.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Overseas Express → ⋮ → Delete**. Nothing is stored on Overseas Express's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.overseas_express_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.overseas_express_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.overseas_express_next_delivery` | Earliest expected delivery moment across all active parcels (empty: Overseas Express gives no estimate yet) |
| `sensor.overseas_express_en_route_to_pickup_point` | Incoming parcels on their way to a pickup point that have not arrived yet |
| `sensor.overseas_express_awaiting_pickup` | Incoming parcels that are ready to collect at a pickup point |
| `sensor.overseas_express_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `calendar.overseas_express_deliveries` | Read-only calendar of expected deliveries, no extra API calls |
| `sensor.overseas_express_last_successful_update` | Diagnostic: when Overseas Express was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced by the sender or awaiting pickup |
| `in_transit` | Picked up and moving through the network |
| `out_for_delivery` | Loaded for delivery on a tour (not yet seen on a real parcel) |
| `at_pickup_point` | Delivered to a parcel shop, terminal, counter or locker |
| `delivered` | Delivered |
| `returning` | Returned or recalled to the sender |
| `problem` | Failed delivery, refused, damaged, lost or cancelled |
| `unknown` | Code not known to Overseas Express, or a status we have not mapped yet |

The parcel's `raw_status` is the carrier's own status name (for example `Created`); each history entry carries the carrier's event text, in Croatian.

## Events

The integration fires these on the event bus (also available as device triggers on the Overseas Express device):

| Event | When |
|---|---|
| `overseas_express_parcel_registered` | A new parcel appears in the active list |
| `overseas_express_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `overseas_express_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `overseas_express.track_parcel` | `tracking_code` | Start tracking a parcel |
| `overseas_express.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/ha-parcel-card](https://github.com/jonisnet/ha-parcel-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.overseas_express: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Overseas Express has not scanned it yet (its tracker answers "not found" for a code it does not know yet), or the code is wrong. It will pick up automatically once the carrier knows the parcel.
- **Every poll fails with "blocked by the network"** — the carrier's gateway refuses some networks (data centres, some VPNs). A home or Croatian connection works.
- **The log shows "Unrecognised Overseas Express response — help us map it"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-overseas-express/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Overseas Express, Home Assistant, or any other third party referenced in this project. Please don't contact Overseas Express for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Overseas Express's terms. Data is sent only to Overseas Express's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking lookup as the Overseas Express tracking page.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
