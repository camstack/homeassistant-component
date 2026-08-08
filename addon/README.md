# CamStack Home Assistant OS add-on

A Supervisor add-on that serves the CamStack web client from Home Assistant
itself, behind Ingress. It exists for Home Assistant OS installations that want
CamStack reachable through the Home Assistant URL rather than at the hub's own
address.

**It is not the integration.** The integration in `custom_components/camstack`
needs nothing from this directory: it talks to the hub over its API, and the
sidebar panel it registers points straight at the hub. This add-on is an
optional convenience for the *web client*.

## Why it lives here, and not in its own repository

The Supervisor discovers add-ons with `path.glob("**/config.*")` under a
repository that has a `repository.yaml` at its root, so a nested `addon/`
directory is discoverable — but this repository deliberately does **not** carry
`repository.yaml`, because the add-on cannot currently be built from what is
committed here:

```dockerfile
COPY dist/. /usr/share/nginx/html/api/hassio_ingress/camstack/
```

`dist/` is the built CamStack web client, produced by `npm run build:ha-addon`
in the CamStack server repository. It is not in this repository and is not
committed. Advertising an add-on repository whose only add-on fails to build is
worse than not advertising one: the failure surfaces to the operator as a
broken install rather than as an absent feature.

So the files are kept here as the **source of the add-on**, versioned in step
with the component (`scripts/sync-version.js` writes `config.yaml`), and the
add-on is not published. Extracting them into `camstack/homeassistant-addon`
with a `repository.yaml` and a CI job that builds `dist/` is the right move the
moment that build exists; nothing here depends on the location.

## Building it by hand

From the CamStack server repository:

```bash
npm run build:ha-addon                 # produces dist/
cp -r dist <this repo>/addon/camstack/ # the Dockerfile copies it verbatim
```

Then add the resulting `addon/camstack` directory to a local add-on repository,
or copy it into `/addons/camstack` on a Home Assistant OS host and reload the
add-on store.
