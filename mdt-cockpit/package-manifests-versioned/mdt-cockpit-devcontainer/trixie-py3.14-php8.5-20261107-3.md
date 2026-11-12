# mdt-cockpit VS Code Devcontainer

Versioned package manifest for `mdt-cockpit-devcontainer`.

## Release

- Build date: `20261107-3`
- Target: `trixie-py314-php85-vsc`
- Immutable image tag: `trixie-py3.14-php8.5-20261107-3`

## Manifest

This manifest is generated at build time from live probes inside the Docker image. The file is extracted post-build by ``build-push.py`` from the freshly built image and committed here so the repo and image always agree.

To view the manifest for this release, pull the image and run:

```bash
docker pull ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-20261107-3
docker run --rm ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-20261107-3 \n  cat /usr/local/share/mdt-cockpit/manifest.md
```
