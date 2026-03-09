# CamStack for Home Assistant

Integration for [CamStack](https://github.com/apocaliss92/camstack) — universal video feed aggregator for surveillance cameras.

## Versioning

Version is in `VERSION`. Sync to manifest:

```bash
npm run version:sync
```

Bump and sync:

```bash
npm run version:patch   # 0.1.0 → 0.1.1
npm run version:minor   # 0.1.0 → 0.2.0
npm run version:major   # 0.1.0 → 1.0.0
```

**Release (required for HACS):** Create a GitHub Release with a semver tag (e.g. `v0.1.0`). Without a release, HACS uses the commit SHA and will fail with "version can not be used with HACS".

```bash
git tag v0.1.0
git push origin v0.1.0
# Then create a GitHub Release from the tag at github.com/.../releases/new
```

## Contents

- **custom_components/camstack/** — Integration (HACS)

## Installation

1. Add this repository to HACS: **Integrations** → **⋮** → **Custom repositories**
2. Add: `https://github.com/apocaliss92/camstack-homeassistant`
3. Search for "CamStack" and install
4. Restart Home Assistant
5. Go to Settings → Devices & Services → Add Integration → CamStack

## Modes

- **Embedded** — CamStack URL (e.g. addon ingress, self-hosted)
- **Proxy external** — External CamStack URL (nginx, etc.)
- **Proxy Scrypted** — Scrypted instance with CamStack (Advanced Notifier plugin), camera import supported
