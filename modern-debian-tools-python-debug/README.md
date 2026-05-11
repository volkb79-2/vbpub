# Modern Debian Python Debug Images

This project builds and publishes two related image families:
- `modern-debian-tools-python-debug`
- `modern-debian-tools-python-debug-vsc-devcontainer`

The purpose is to provide a curated, reproducible Debian + Python environment with modern CLI tooling for local development, CI, and VS Code devcontainers.

Rich GHCR-facing package docs live under [package-manifests-versioned](package-manifests-versioned/README.md). The release resolver regenerates those versioned Markdown pages on each build so OCI labels can point to repository-hosted Markdown instead of relying on flattened GHCR description text.

## Image Families

1. `modern-debian-tools-python-debug`
	- Base: `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Use when you want a plain Python image with the custom tool stack.

2. `modern-debian-tools-python-debug-vsc-devcontainer`
	- Base: `mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Use when you want Microsoft devcontainer behavior plus custom tooling.

## Tagging and Variants

Tag format:

```text
<debian>-py<python>-<YYYYMMDD>
```

Examples:
- `trixie-py3.14-20260511`
- `trixie-py3.14-latest`
- `latest` (family-wide floating tag)

Enabled build group target list is in [docker-bake.hcl](docker-bake.hcl) under `group "all"`.

## Build and Push Flow

Entry points:
- [build-images.py](build-images.py)
- [push-images.py](push-images.py)

Step configuration:
- [build-push.toml](build-push.toml)

Bake definition:
- [docker-bake.hcl](docker-bake.hcl)

### Critical freshness behavior

`scripts/resolve-devcontainers-release.py` now **always pulls** the configured base devcontainer image before reading labels. This avoids stale local-cache metadata during build/push.

During `./build-images.py` and `./push-images.py`, the resolver also performs a **dynamic registry check** against live MCR tag inventory.

Default behavior is now **fail-fast**:
- if newer stable Python/Debian streams are detected, build stops before bake starts
- to continue intentionally, run `./build-images.py --ignore-new-releases`

When the gate stops a build, `build-images.py` and `push-images.py` print a clean actionable message (no Python traceback) with explicit next steps.

Example advisory:

```text
[WARN] Newer stable devcontainers/python tag(s) detected for trixie: 1-3.15-trixie, 3.15-trixie. Current base: 3.14-trixie. Recommended newest stable: 3.15-trixie.
```

Resolver checks are dynamic and do not hardcode future version numbers.

Detection scope is dynamic (live registry), and includes:
- newer Python for your current Debian codename (minor and major streams, for example `3.15` or `4.x`)
- additional Debian codenames for your current Python stream (helps detect new Debian variant availability)
- newer Python streams that may already exist on other Debian variants (early visibility)

The script exports:
- `DEVCONTAINERS_RELEASE_STABLE` (example: `v0.4.26`)
- `DEVCONTAINERS_VERSION_STABLE` (example: `3.0.7`)
- `DEVCONTAINERS_BASE_LATEST_STABLE` (example: `mcr.microsoft.com/devcontainers/python:3.15-trixie`)
- `DEVCONTAINERS_LATEST_STABLE_PYTHON` and `DEVCONTAINERS_LATEST_STABLE_DEBIAN`

Those values are passed through `build-push.toml` into bake args and then into Dockerfile metadata and manifest content.

You can change which stable base image is checked (and resolved) by overriding:

```bash
DEVCONTAINERS_BASE_STABLE=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py
```

Ignore release-gate intentionally:

```bash
./build-images.py --ignore-new-releases
```

The same `DEVCONTAINERS_BASE_STABLE` variable is also used by the `trixie-py314-vsc` bake target base image in [docker-bake.hcl](docker-bake.hcl), so warning/check behavior and actual build input stay aligned.

### Known-latest variables in bake file

To make maintenance explicit and readable, [docker-bake.hcl](docker-bake.hcl) defines:
- `LATEST_KNOWN_DEBIAN` (default: `trixie`)
- `LATEST_KNOWN_PYTHON` (default: `3.14`)

`DEVCONTAINERS_BASE_STABLE` is composed from these values. This does not replace live detection, but improves local intent clarity and reduces scattered hardcoded values.

Policy:
- Keep `LATEST_KNOWN_*` aligned with the currently adopted stable baseline.
- If upstream releases move ahead, gate will fail until you either:
	- update `LATEST_KNOWN_*` to adopt the new baseline, or
	- run with `--ignore-new-releases` intentionally.

### Latest dynamic target

`docker-bake.hcl` includes a dynamic target/group:
- target: `latest-vsc`
- group: `detection`

`latest-vsc` uses resolver-exported live values (`DEVCONTAINERS_BASE_LATEST_STABLE`, Python, Debian) so it automatically follows current upstream stable availability.

Target policy:
- `group "all"` stays deterministic and pinned to adopted baseline targets.
- `latest-vsc` stays separate in `group "detection"` for explicit opt-in builds.
- This avoids unexpected automatic upstream jumps in default build outputs.

Example build of the dynamic latest target:

```bash
docker buildx bake -f docker-bake.hcl detection --load
```

You can still force a specific older base to validate gate behavior:

```bash
DEVCONTAINERS_BASE_STABLE=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py
```

Then continue anyway only when explicitly requested:

```bash
DEVCONTAINERS_BASE_STABLE=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py --ignore-new-releases
```

## Manifest Location and Content

Each built image writes a markdown manifest at:

```text
/usr/local/share/modern-debian-tools-python-debug/manifest.md
```

Manifest sections:
- `Base`
- `Custom Tooling`
- `Python packages`
- `System packages`

The base section includes:
- Debian version
- Python runtime version
- image build version (`OCI_VERSION`)
- computed tag pattern (`<debian>-py<python>-<date>`)
- devcontainers release (`v0.4.x` stream)
- devcontainers image version (`3.0.x` stream)

## About `Custom Tooling` vs `System packages`

- `Custom Tooling` is an operational view: tool executables and their runtime `--version` output.
- `System packages` is a package inventory view: selected apt package names and versions.

This means one component can appear in both sections without being duplicated.

Example:
- `psql` in `Custom Tooling` is the executable version output.
- `postgresql-client=...` in `System packages` is the Debian package that provides `psql`.
- It is not installed twice.

## `python -m pip install` vs `pipx`

Both are valid but solve different needs:

1. `python -m pip install`
	- Installs into the active environment.
	- Good for a curated, integrated toolset in one venv.

2. `pipx`
	- Installs each CLI app in its own isolated venv.
	- Better when you need strong separation between tool dependency trees.

Current design here intentionally uses a shared curated environment (`/home/vscode/.venv`) for consistency across tools.

## Base Version Labels on Built Images

Built images include labels:
- `net.volkb79.base-devcontainers-release`
- `net.volkb79.base-devcontainers-version`

Inspect example:

```bash
docker image inspect ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260511 \
  --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-release" }}'

docker image inspect ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260511 \
  --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-version" }}'
```

## Practical Commands

Build all enabled targets:

```bash
./build-images.py
```

Push all enabled targets:

```bash
./push-images.py
```

Override specific build variables:

```bash
B2_VERSION=4.5.0 ./build-images.py
```

## GHCR Credentials

Use PAT with package scopes (classic token):
- `write:packages`
- `read:packages`

Configured via environment (for example `.env` loaded by your release tooling).

## Using in another repository

Use image reference in `.devcontainer/devcontainer.json`:

```json
{
  "image": "ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260511",
  "remoteUser": "vscode"
}
```

Avoid building from Dockerfile in consumer repos when a published image already exists.

## Checking Available Upstream Devcontainer Tags

Run:

```bash
./check-mcr-devcontainer-tags.py
```

This compares discovered tags against upstream manifest data and helps spot newly available variants.

Example output:

```text
debian    3.12   3.13   3.14   3.15   3.16
--------  -----  -----  -----  -----  -----
bookworm  1pd    1pd    pd     .      .
trixie    pd     pd     pd     .      .
forky     .      .      .      .      .

Legend: 1 = 1- prefix, p = plain, d = dev- prefix, . = missing

Secondary manifest variants: 10 (https://raw.githubusercontent.com/devcontainers/images/main/src/python/manifest.json)
```

## References

- Upstream devcontainers images: https://github.com/devcontainers/images
- Python manifest: https://raw.githubusercontent.com/devcontainers/images/main/src/python/manifest.json

