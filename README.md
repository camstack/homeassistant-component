# CamStack for Home Assistant

The Home Assistant component for a [CamStack](https://github.com/camstack) hub.
Your cameras, motion, sensors and per-camera function switches become
first-class Home Assistant entities over the hub's own API — and the CamStack
web client is available as a sidebar panel and as a Lovelace card. No MQTT
broker, no YAML, and the hub address is asked for exactly once.

> **This repository is the home of the `camstack` Home Assistant domain.**
> A second component briefly existed at `camstack/homeassistant`; it was merged
> into this one in 0.2.0 and archived. Home Assistant loads only one
> `custom_components/camstack`, so there is only one, and it is this.

## What you get

| Surface | What it covers |
| --- | --- |
| `camera` | One entity per CamStack camera. Stills from the hub's snapshot capability, live video over **native WebRTC** |
| `binary_sensor` | Motion, reachability, privacy-mask state, and a diagnostic per-slot streaming sensor |
| `sensor` | Temperature, humidity, pressure, generic numeric and enum readings, battery, per-frame detected-object counts, **per-zone object counts**, last motion and last doorbell press |
| `switch` | The per-camera function switches: camera, object detection, privacy mask, microphone, audio distribution, audio detection, recording, notifications |
| Sidebar panel | The CamStack web client, full screen, in the Home Assistant sidebar |
| Lovelace card | `custom:camstack-grid-card` — a CamStack camera grid inside a dashboard |

## Install

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/camstack/homeassistant-component`, category **Integration**
3. Install **CamStack**, then restart Home Assistant
4. **Settings → Devices & services → Add integration → CamStack**

### Manual

Copy `custom_components/camstack` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Everything is done in the UI, and the flow starts by asking **how** you want to
connect.

### Link with the hub (recommended)

You give the hub's **Host**, **Port** and **Verify SSL**, and nothing else. Home
Assistant sends you to the hub's own login and consent page — passkey, password,
two-factor, whatever your hub is set up for — and you come back with a token
Home Assistant refreshes on its own.

No password is stored. The link shows up in the hub's admin UI under the OAuth
sessions, where revoking it cuts Home Assistant off immediately, and accounts
with two-factor authentication work normally because the hub, not this
integration, runs the login.

This needs a hub new enough to know about Home Assistant. The flow asks the hub
first, and says so plainly if the answer is no — pick the other option in that
case, and try again after updating the hub.

### Use a username and password

The original path, unchanged and still supported:

- **Host** and **Port** — your hub, e.g. `192.168.1.9` and `4443`
- **Username** and **Password** — a CamStack account
- **Verify SSL** — leave off if the hub uses a self-signed certificate

The credentials are validated against the hub before the entry is created, and
the integration re-authenticates by itself when its session token expires. If
the password later changes, Home Assistant raises a normal re-auth prompt.

Accounts with two-factor authentication enabled **cannot** be used this way: a
stored password alone can never answer a TOTP challenge, so the flow refuses
rather than creating an entry that could never load. Link with the hub instead.

### The panel and the card do not ask for the address again

The sidebar panel and the grid card both point at the host and port above. The
card reads them from the integration (`/api/camstack/config`), so a dashboard
never carries a second copy of your hub URL. Two addresses that can drift apart
is a failure nothing reports: the panel keeps working while the entities go
stale, or the reverse.

The one case where they legitimately differ is a browser that reaches the hub
at a different address than Home Assistant does — a reverse proxy, or a hub on
a network only some clients see. **Settings → Devices & services → CamStack →
Configure** has a *Panel URL override* for exactly that, and it is empty by
default.

Also in **Configure**: the sidebar title and icon, and a switch to remove the
panel from the sidebar without losing any entity.

### Upgrading from 0.1.x (the panel-only component)

Nothing is lost and nothing needs redoing.

An entry created by the panel-only component holds a URL and no credentials. On
upgrade it is migrated to the merged shape and **keeps serving the panel it was
already serving** — the URL is carried forward verbatim, not rebuilt, so a
panel pointing at a reverse proxy keeps pointing at it. Home Assistant then
raises a normal re-authentication prompt asking for a hub address and an
account; the address is pre-filled with what could be derived from the old URL,
and it is editable because a derivation is a guess. Entities appear as soon as
the hub accepts the credentials.

Until then the entry shows as needing attention, the sidebar keeps working, and
no entity exists. That is the intended state, not a failure.

## How it works

Two feeds, and they are not interchangeable.

**The event stream is the live path.** The integration holds a Server-Sent
Events subscription to the hub's `live.onEvent`, filtered server-side to
`device.state-changed`. A motion pulse can auto-clear after three seconds, so
any poll interval slow enough to be polite is slow enough to miss it.

**The reconcile is the correctness backstop.** Once a minute — and on every
stream reconnect, which is when a drop is most likely — the integration reads
`deviceExport.listExposedDevices`, `deviceManager.listAll` and
`deviceState.getAllSnapshots` and republishes everything. Hub events are
telemetry and may be dropped, so a pump alone is not a contract.

### Which devices are imported

**The hub decides, not this integration.** Only devices exported to Home
Assistant are imported. Membership lives on the hub's `device-export`
capability — the same interface the Alexa and HomeKit exporters implement — and
is read with `deviceExport.listExposedDevices`.

Choose them in the CamStack admin UI, on each device's **Export → Home
Assistant Export** panel. Nothing is stored on this side: a second opt-in list
here could disagree with the hub's, and two switches that disagree are worse
than one.

Consequences worth knowing:

- **An empty selection creates no entities.** That is the correct answer to
  "the operator has exported nothing yet", and it is never widened into "import
  everything" — which is what this integration used to do, producing an entity
  for every one of ~300 hub devices.
- **A device's unexported parents are still read**, because grouping walks the
  parent chain. A camera's siren can be exported while the container above it
  is not, and the tree must still collapse into one Home Assistant device.
- **A device imported *from* Home Assistant is refused even if exported**,
  because sending it back mirrors it across the bridge.

### An entity comes from a capability, not a device type

The hub returns each device's state as `capName → slice`, and this integration
maps **slices** to entities. A device type tells you almost nothing — a dozen
capabilities are bound to every device regardless of what it is — so the map is
an allowlist keyed by capability name. An unmapped capability produces no
entity at all rather than one that never receives a value. A wrong value is a
worse failure than a missing one: nothing about it looks broken.

For the same reason units are read from the **live slice** wherever the
capability carries one. A Fahrenheit feed is not relabelled as °C.

### One device tree, one Home Assistant device

Entities attach to the root of the CamStack parent chain, stopping before a
`hub`-type ancestor. On a live 300-device cluster that yields tens of Home
Assistant devices rather than hundreds of orphans: a camera's siren becomes a
control on the camera's card, while the sub-stations under a weather gateway
stay distinct devices instead of collapsing into it.

### Switches store nothing

The per-camera switches mirror the hub's own switch group, which is itself a
view onto whatever authority already owned each function. A switch here writes
exactly what the CamStack UI writes, so the two surfaces cannot drift.

Two behaviours follow and both matter:

- a switch the hub reports as **unavailable** is rendered unavailable, never as
  "off" — "this camera cannot do that" and "an operator turned that off" are
  different facts and an automation must be able to tell them apart;
- a write that the hub refuses raises an error and confirms nothing. The
  optimistic value is corrected by the next reconcile rather than silently
  accepted.

`privacy-mask` is the one switch whose **on** means the picture is deliberately
obscured, rather than that a function is working. That polarity is the hub's,
and mirroring it verbatim is what keeps this toggle agreeing with every other
CamStack client.

### Devices that came from Home Assistant are not sent back

CamStack can import Home Assistant devices. Those are excluded here — exporting
them back would create a device that mirrors itself across the bridge.

## The Lovelace card

Add the card from the dashboard editor ("CamStack Grid"), or in YAML:

```yaml
type: custom:camstack-grid-card
title: Ingresso
entities:
  - camera.videocamera_ingresso
  - camera.videocamera_giardino
height: 420
```

| Option | Default | Meaning |
| --- | --- | --- |
| `entities` | — | Camera entities to show. Their friendly names are what the hub grid is asked for |
| `grid_id` | — | An existing CamStack grid, instead of `entities` |
| `height` | `400` | Card height in pixels |
| `audio` | `true` | Set `false` to mute |
| `resolution` | — | Passed through to the hub |
| `url_base` | — | Override the hub address. Leave empty: the card asks the integration |

The card resource is registered automatically when Lovelace stores its
resources. In **YAML mode** the resource list is your file and this component
does not write to it — add it yourself:

```yaml
lovelace:
  resources:
    - url: /camstack-frontend/camstack-grid-card.js
      type: module
```

## What is deliberately not an entity

- **A track.** A CamStack track is a time-bounded observation, not a thing with
  a current state. There is no Home Assistant entity whose state is "an
  occurrence between t0 and t1"; forcing one gives you an entity whose state is
  an opaque id and whose history cannot be graphed or templated. The per-frame
  object **count** is exported instead, because that *is* a thing with a
  current state.
- **Media.** Clips, recorded segments, crops, embeddings. These are bytes
  addressed by handle. A still is displayable and is displayed; a clip has no
  Home Assistant type.
- **Geometry.** Motion zones, privacy masks and detection regions are polygons.
  Home Assistant has no polygon entity. The derived per-zone occupancy count is
  a sensor; the shape that defines the zone stays in the CamStack UI.

## Relationship to the MQTT exporter

CamStack also ships `addon-export-ha-mqtt`, which publishes MQTT discovery
topics. The two are complementary, not competing:

| | MQTT exporter | This component |
| --- | --- | --- |
| Lives in | the CamStack server monorepo | this repository |
| Needs | an MQTT broker both sides can reach | nothing but the hub |
| Direction | CamStack pushes | Home Assistant pulls and subscribes |
| Live video | no | yes, native WebRTC |
| Best for | broad read-only coverage of every device | cameras and the controls around them |

Both derive entities from capability slices and follow the same grouping and
non-goal rules, so a device looks the same whichever path you use.

**They share one device selection.** The MQTT exporter's addon is the hub's
`device-export` provider for Home Assistant, so the per-device "Expose to Home
Assistant" switch governs both paths. With no broker linked nothing is
published over MQTT and the selection simply acts as this integration's
allowlist. The addon id still says `mqtt` for historical reasons — it is baked
into the addon's settings store, npm package name and deployed directory — but
the list it holds names devices, not topics.

## The Home Assistant OS add-on

`addon/` holds a Supervisor add-on that serves the CamStack web client behind
Ingress. It is optional and unrelated to the integration, which needs nothing
from it. It is **not published as an add-on repository** — see
[addon/README.md](addon/README.md) for why, and for how to build it by hand.

## Roadmap

- Doorbell presses and classified detections on Home Assistant's `event` platform
- The remaining command surfaces: covers, locks, climate, fan, vacuum, siren,
  lawn mower, alarm panel
- Per-zone occupancy sensors (the aggregate exists; the per-zone breakout does not)
- A bridge device carrying node health, and `via_device` grouping under it
- Snapshot cadence driven by the hub's per-device etag rather than on demand

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest -q
uv tool run ruff check . && uv tool run ruff format --check .
```

The test fixtures are verbatim payloads recorded from a live hub, not shapes
invented to match this code. CI runs the suite against **two** Home Assistant
generations — the Python version is what selects them — plus hassfest, HACS
validation and ruff.

### Versioning

`VERSION` is the source of truth; `npm run version:sync` copies it into
`custom_components/camstack/manifest.json`, `addon/camstack/config.yaml` and
`package.json`.

```bash
npm run version:patch   # 0.2.0 → 0.2.1
npm run version:minor   # 0.2.0 → 0.3.0
npm run version:major   # 0.2.0 → 1.0.0
```

CI releases whatever version `VERSION` names the first time it sees it, and
bumps the patch on every later push to `main`. Every release gets a `vX.Y.Z`
tag and a GitHub Release, because HACS installs from releases and refuses a
repository that has none.

## Licence

MIT — see [LICENSE](LICENSE).
