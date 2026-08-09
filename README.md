# CamStack for Home Assistant

The Home Assistant component for a [CamStack](https://github.com/camstack) hub.
**CamStack pushes; Home Assistant never polls it for a value.** Detections,
zone counts, camera controls and every base device kind arrive over one
authenticated HTTP endpoint this component registers, and live video comes
straight from the hub over native WebRTC. The CamStack web client is available
as a sidebar panel and as a Lovelace card. No MQTT broker, no YAML, and the hub
address is asked for exactly once.

> **Nothing is imported until you export it.** Devices reach Home Assistant
> only when they are exposed in the CamStack admin UI, under the device's
> **Export → Home Assistant Export** panel. A fresh install with nothing
> exported creates no entities, says so in the log, and that is the integration
> working — see [Which devices are imported](#which-devices-are-imported).

> **This repository is the home of the `camstack` Home Assistant domain.**
> A second component briefly existed at `camstack/homeassistant`; it was merged
> into this one in 0.2.0 and archived. Home Assistant loads only one
> `custom_components/camstack`, so there is only one, and it is this.

## What you get

The hub decides what exists. This component builds whatever it is sent, so the
list below describes today's hub rather than a limit of the integration.

| Surface | What it covers |
| --- | --- |
| `camera` | One entity per exported CamStack camera. Stills from the hub's snapshot capability, live video over **native WebRTC** |
| `binary_sensor` | `triggered`, `online`, per-macro `person`/`vehicle`/`animal`/`package`/`motion`/`audio` detection, per-zone detection, battery charging and sleeping |
| `sensor` | Last detection and last identification per macro and per zone, per-zone object counts, battery, and every reading a base device's capability models — temperature, humidity, pressure, illuminance, power |
| `image` | The last crop, per camera, per macro class and per zone. Fetched on demand from a signed, expiring URL — pictures never cross the push transport |
| `switch` | The per-camera function switches, plus writable capabilities on base devices such as a lock or a siren |
| `button` | Reboot, and the PTZ steps, on cameras that declare the capability |
| `select` | Snooze durations and PTZ presets |
| `number` | Writable numeric capabilities, such as a light's brightness |
| Sidebar panel | The CamStack web client, full screen, in the Home Assistant sidebar |
| Lovelace card | `custom:camstack-grid-card` — a CamStack camera grid inside a dashboard |

**Most entities arrive switched off.** A camera with three zones carries ~73 of
them, and the hub marks everything an operator would not automate on
`enabled_by_default: false`. `*_detected`, `triggered`, `online`, `last_image`
and the controls are enabled; the rest are registered and waiting for you to
enable them in the entity settings.

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

**CamStack pushes.** The hub's `homeassistant-export` addon POSTs to
`/api/camstack/push`, a view this component registers inside Home Assistant and
which sits behind Home Assistant's own authentication — the hub uses the
long-lived token you already gave it, so there is no second secret to configure
or rotate. Four message types, and each carries its own guarantee:

| Message | What it is |
| --- | --- |
| `heartbeat` | Liveness, every 30 seconds, and the **only** availability signal. When it stops, every entity goes unavailable — deliberately no second source that could disagree about whether camstack is alive |
| `batch` | State updates accumulate hub-side and arrive together. Across ~880 entities that batching is the difference between an installation and a storm |
| `state_update` | One topic, one value, always a string. Deduped hub-side, so an unchanged value never arrives twice |
| `entity_change` | A device's **complete** component set, never a delta, re-sent in full on every reconcile. This component diffs against it and never accumulates, so a dropped message repairs itself instead of leaving half a device behind |

**Commands go the other way** as `{topic, value}` POSTed to the export addon's
own route on the hub. The hub refuses an unroutable command rather than
approximating it, and the refusal reaches you as an error instead of a toggle
that moved and did nothing.

**Pictures never cross the transport.** An `image` entity receives a signed,
expiring URL and fetches the bytes on demand, at most once every ten seconds
per entity. A detection crop is hundreds of kilobytes and a camera has ten image
entities; sending bytes would turn every detection into a multi-megabyte POST.

### Two details that look like bugs and are not

**The first push after a reload is answered 503, on purpose.** The hub sends a
value only when it changed, and it drops that per-topic cache when the link
returns — which it detects as a failed POST followed by a successful one.
Reloading this integration produces no such edge, because a Home Assistant view
cannot be unregistered and keeps answering 200 while the entities behind it are
new and empty. They would then stay blank until each value happened to change
on its own. One deliberate refusal manufactures the edge; the hub re-pushes
everything on its next reconcile.

**Structure and last values are kept on disk.** The hub re-announces on its
reconcile, up to five minutes away, so without this every entity would sit
blank after each Home Assistant restart. They come back immediately, and
unavailable, until the first heartbeat proves camstack is alive.

### Which devices are imported

**The hub decides, not this integration.** Only devices exported to Home
Assistant are imported. Membership lives on the hub's `device-export`
capability — the same interface the Alexa and HomeKit exporters implement.

Choose them in the CamStack admin UI, on each device's **Export → Home
Assistant Export** panel. Nothing is stored on this side: a second opt-in list
here could disagree with the hub's, and two switches that disagree are worse
than one.

Consequences worth knowing:

- **An empty selection creates nothing.** That is the correct answer to "the
  operator has exported nothing yet", and it is never widened into "import
  everything" — which is what this integration used to do, producing entities
  for every device on the hub. The log says so, and names the panel to use.
- **Devices you exposed to Alexa or HomeKit are not included.** The read is
  pinned to the Home Assistant exporter. `device-export` is a collection
  capability and an unpinned read merges every exporter, which would import
  somebody else's selection while looking entirely healthy.
- **A pin the hub cannot resolve fails setup loudly**, with the hub's own
  message naming the exporters that do exist. A broken integration that looks
  like an empty one is the single most expensive failure here.
- **A device imported *from* Home Assistant is refused even if exported**,
  because sending it back mirrors it across the bridge. Both sides enforce it.

### The component is generic

It builds entities out of the component set the hub sends and knows nothing
about cameras, zones or detection classes. All of that lives on the camstack
side, which is what lets the hub add an entity — or a whole device kind —
without this component being released. It has its own HACS release cadence, and
two shipping chains that must move together are two shipping chains that drift.

A platform this version cannot build costs that one entity and logs a line
naming it. Everything else on the device is unaffected.

### One CamStack device, one Home Assistant device

Identity is the hub's `device_id` (`camstack-<stableId>`), which is also what
the camera entity uses — so the live view and the sensors land on one device
instead of two that look like duplicates. It is derived from the **stable** id
rather than the numeric one: numeric ids are reallocated by a re-sync, and an
entity keyed on one would follow whichever camera inherited the number.

### Switches store nothing

Every switch is a mirror. The hub renders it from the authority that already
owns the function and applies it back through the same one, so a toggle here
writes exactly what the CamStack UI writes and the two cannot drift.

- A switch the hub reports as **unavailable** produces **no entity at all** —
  a control defaulted to "on" because a source did not answer is exactly the
  lie that flag exists to prevent.
- A write the hub refuses raises an error and confirms nothing. An accepted one
  shows immediately and is corrected by the hub's next push if it disagreed.

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
  an opaque id and whose history cannot be graphed or templated. The per-zone
  object **count** and the last-detection timestamp are exported instead,
  because those *are* things with a current state.
- **Clips.** Recorded segments and embeddings are bytes addressed by handle. A
  still is displayable and is displayed; a clip has no Home Assistant type.
- **Geometry.** Motion zones, privacy masks and detection regions are polygons.
  Home Assistant has no polygon entity. The derived per-zone occupancy count is
  a sensor; the shape that defines the zone stays in the CamStack UI.

## The MQTT exporter is retired

CamStack used to ship `addon-export-ha-mqtt`, which published MQTT discovery
topics. It was **deleted on 2026-08-09** and Home Assistant is now served over
one path only, by this component. Nothing was migrated: its exposed set was
empty. If you pinned anything to the `export-ha-mqtt` addon id, the hub now
refuses that id outright and names `homeassistant-export` in its place.

## The Home Assistant OS add-on

`addon/` holds a Supervisor add-on that serves the CamStack web client behind
Ingress. It is optional and unrelated to the integration, which needs nothing
from it. It is **not published as an add-on repository** — see
[addon/README.md](addon/README.md) for why, and for how to build it by hand.

## Roadmap

- The synthetic devices from the design: a **Notification Center** carrying one
  switch per notification rule, and a **CamStack Server** device carrying node
  health, addon health and the liveness monitor's findings
- `alarm_control_panel`, which the hub maps but does not yet project a state for
- Doorbell presses and classified detections on Home Assistant's `event` platform
- A range on the wire for `number` entities; Home Assistant's 0-100 default
  applies until then

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
