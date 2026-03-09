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

## Configuration

Enter the **external URL** of your CamStack instance (e.g. `https://camstack.example.com` or `http://192.168.1.100:8080`). The integration adds a sidebar panel that embeds CamStack in an iframe.

## Troubleshooting

### Font preload warnings (Roboto-Regular.woff2, Roboto-Medium.woff2)

These warnings appear when CamStack is served from the same origin as Home Assistant (e.g. via reverse proxy on the same host). They are harmless and do not affect functionality. To avoid them, serve CamStack from a different host or port.

### button-card-action-handler already used

This error occurs when the button-card Lovelace resource is loaded multiple times. Go to **Settings → Dashboard → Resources** (enable advanced mode first) and remove any duplicate button-card entries. Clear the browser cache after fixing.
