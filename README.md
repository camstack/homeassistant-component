# CamStack for Home Assistant

Integration and addon for [CamStack](https://github.com/apocaliss92/camstack) — universal video feed aggregator for surveillance cameras.

## Versioning

Version is in `VERSION`. Sync to manifest and addon config:

```bash
npm run version:sync
```

Bump and sync:

```bash
npm run version:patch   # 0.1.0 → 0.1.1
npm run version:minor   # 0.1.0 → 0.2.0
npm run version:major   # 0.1.0 → 1.0.0
```

Release: create a Git tag (e.g. `v0.1.0`) and GitHub release. HACS and the addon store use tags for versioning.

## Contents

- **custom_components/camstack/** — Integration (HACS)
- **addon/camstack/** — Addon for embedded mode (Add-on store)

## Installation

### Integration (HACS)

1. Add this repository to HACS: **Integrations** → **⋮** → **Custom repositories**
2. Add: `https://github.com/apocaliss92/camstack-homeassistant`
3. Search for "CamStack" and install
4. Restart Home Assistant
5. Go to Settings → Devices & Services → Add Integration → CamStack

### Addon (Embedded mode)

1. Add this repository to the Add-on store
2. Install the "CamStack" addon
3. Run `npm run build:ha-addon` in the main [camstack](https://github.com/apocaliss92/camstack) repo before building the addon (or use pre-built releases)

## Modes

- **Embedded** — CamStack served by the addon via Ingress
- **Proxy external** — External CamStack URL (nginx, etc.)
- **Proxy Scrypted** — Scrypted instance with CamStack (Advanced Notifier plugin), camera import supported
