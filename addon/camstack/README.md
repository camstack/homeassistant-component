# CamStack Add-on

Serve the CamStack PWA via Home Assistant Ingress for embedded mode.

## Build

Before building the addon, generate the dist from the main [camstack](https://github.com/apocaliss92/camstack) repo:

```bash
cd camstack
npm run build:ha-addon
```

This builds CamStack with the correct base path and copies to `camstack-homeassistant/addon/camstack/dist/`. Ensure camstack-homeassistant is a sibling of camstack.

## Installation

1. Add this repository to the Home Assistant add-on store:
   - **URL:** `https://github.com/apocaliss92/camstack-homeassistant`

2. Install the "CamStack" add-on

3. Start the add-on

4. Configure the CamStack integration in Home Assistant with mode "Embedded" and URL `/api/hassio_ingress/camstack`

## Configuration

No configuration required. The add-on serves CamStack at `/api/hassio_ingress/camstack/` via Ingress.

## Support

- [CamStack GitHub](https://github.com/apocaliss92/camstack)
- [Issues](https://github.com/apocaliss92/camstack/issues)
