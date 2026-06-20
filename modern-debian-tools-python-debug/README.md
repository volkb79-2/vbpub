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

The docker images use date-based tags (`trixie-py3.14-20260616`, `20260616-2` for same-day rebuilds) plus floating `latest`. 

## Multi-Python Devcontainer Variants

Standard single-Python targets ship one Python version (from the base image) in a full primary venv. Multi-Python targets additionally bake in one or more lean secondary environments at image build time — no post-create download needed.

### Python Environments in the Image

| Venv | Path | Contents |
|------|------|----------|
| Primary | `/home/vscode/.venv` | Full toolkit — see [Primary Venv Packages](#primary-venv-packages) below |
| Secondary 3.11 | `/home/vscode/.venv-py311` | Lean: `uv`, `debugpy`, `ruff` |
| Secondary 3.9 | `/home/vscode/.venv-py39` | Lean: `uv`, `debugpy`, `ruff` |

VS Code discovers all venvs automatically. Switch via `Python: Select Interpreter` or:

```bash
source /home/vscode/.venv-py311/bin/activate
```

### Available Multi-Python Targets

| Target | Primary Python | Secondaries | Tag example |
|--------|---------------|-------------|-------------|
| `trixie-py314-vsc` | 3.14 (full) | — | `trixie-py314-latest` |
| `trixie-py314-py311-vsc` | 3.14 (full) | 3.11 (lean) | `trixie-py314-py311-latest` |
| `trixie-py314-py311-py39-vsc` | 3.14 (full) | 3.11, 3.9 (lean) | `trixie-py314-py311-py39-latest` |

Tag format for multi-Python variants: `<debian>-<primary>-<secondary...>-<YYYYMMDD>` with a matching `-latest` floating tag.

### Primary Venv Packages

The full toolkit installed into `/home/vscode/.venv` is defined in
[`requirements/toolkit.txt`](requirements/toolkit.txt) — the single source of truth, with every entry
annotated for what it's for. It splits into dev/build tooling (`ruff`, `mypy`, `pytest`, `build`,
`uv`, `tox`, …) and **inspection / AI-scratch libraries** (`httpx`, `asyncpg`, `redis`, `hvac`,
`sqlalchemy`, `dnspython`, `websockets`, …) used to talk to running services interactively.

`ruff` supersedes `black` and `isort` (formatting + import sorting), so those are no longer installed.

> This venv is a **debug cockpit**, not a gating test environment. "Green here" is never a ship
> signal — a project's gating tests run in a test image built `FROM <app-base>`. See
> [docs/CONTAINER-DOCTRINE.md](docs/CONTAINER-DOCTRINE.md), and
> [docs/CONSUMER-AI-GUIDANCE.md](docs/CONSUMER-AI-GUIDANCE.md) for what to put in your repo's AI
> instruction files. The apt system-package list lives in [`apt/packages.list`](apt/packages.list).

Optional (controlled by `INSTALL_*` build args, all enabled by default):
- `aider-chat` — `INSTALL_AIDER=true`
- `reasonix`, `openclaw` — npm-based, installed separately from the venv and backed by the image's `nodejs` / `npm`
- `deepcode` — pip-based (`deepcode-hku`), installed separately from the venv
- `codex`, `claude-code`, `antigravity` — installed separately from the venv

### Lean Venv Packages (Secondary Pythons)

Each secondary venv (`/home/vscode/.venv-py{nodot}`) contains only:

```
pip (latest)   uv   debugpy   ruff
```

Install project-specific dependencies on demand:

```bash
uv pip install -r requirements.txt --python /home/vscode/.venv-py311/bin/python
```

### Adding More Secondary Versions or Targets

Copy an existing multi-Python target in `docker-bake.hcl`, update `SECONDARY_PYTHON_VERSIONS` (space-separated dotted versions) and the target name and tags to match. Example — adding 3.13 alongside 3.11:

```hcl
target "trixie-py314-py313-py311-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    SECONDARY_PYTHON_VERSIONS = "3.13 3.11"
    ...
  }
  tags = [vsc_multi_tag("trixie", "py314-py313-py311"), vsc_multi_latest_tag("trixie", "py314-py313-py311")]
}
```

The `vsc_multi_tag` / `vsc_multi_latest_tag` HCL functions accept any `<debian>` and `<pythons_label>` string, so naming is fully flexible.

### Python Version Support Status

CPython has no odd/even stability distinction (unlike the Linux kernel). All released minor versions are production-quality. Support windows as of June 2026:

| Version | EOL | Notes |
|---------|-----|-------|
| 3.9 | Oct 2025 | **EOL** — include only for legacy compatibility testing |
| 3.11 | Oct 2027 | Active — production staple, matches netcup-api-filter deploy target |
| 3.13 | Oct 2029 | Active — previous stable release |
| 3.14 | Oct 2030 | **Current stable** — primary base image |

## Build and Push Flow

Entry point:
- [build-push.py](build-push.py) — unified script (`--build`, `--push`, `--rebuild`)

Step configuration:
- [build-push.toml](build-push.toml)

Bake definition:
- [docker-bake.hcl](docker-bake.hcl)

### Critical freshness behavior

`scripts/resolve-devcontainers-release.py` now **always pulls** the configured base devcontainer image before reading labels. This avoids stale local-cache metadata during build/push.

During `./build-push.py --build`, the resolver also performs a **dynamic registry check** against live MCR tag inventory.

Default behavior is now **fail-fast**:
- if newer stable Python **or Debian** streams are detected, build stops before bake starts
- to continue intentionally, run `./build-push.py --build --ignore-new-releases`

When the gate stops a build, `build-push.py` prints a clean actionable message (no Python traceback) with explicit next steps.

Example advisory:

```text
[WARN] Newer stable devcontainers/python tag(s) detected for trixie: 1-3.15-trixie, 3.15-trixie. Current base: 3.14-trixie. Recommended newest stable: 3.15-trixie.
```

Resolver checks are dynamic and do not hardcode future version numbers.

Detection scope is dynamic (live registry), and includes:
- newer Python for your current Debian codename (minor and major streams, for example `3.15` or `4.x`)
- **newer Debian codename for your current Python version** (e.g. `forky` when you have `bookworm`)
- additional Debian codenames for your current Python stream (helps detect new Debian variant availability)
- newer Python streams that may already exist on other Debian variants (early visibility)

The script exports:
- `DEVCONTAINERS_RELEASE_STABLE` (example: `v0.4.26`)
- `DEVCONTAINERS_VERSION_STABLE` (example: `3.0.7`)
- `DEVCONTAINERS_BASE_DYNAMIC_LATEST` (example: `mcr.microsoft.com/devcontainers/python:3.15-trixie`)
- `DEVCONTAINERS_DYNAMIC_LATEST_PYTHON` and `DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN`

Those values are passed through `build-push.toml` into bake args and then into Dockerfile metadata and manifest content.

You can change which stable base image is checked (and resolved) by overriding:

```bash
DEVCONTAINERS_BASE_PINNED=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py
```

see also (https://mcr.microsoft.com/v2/devcontainers/python/tags/list)

Ignore release-gate intentionally:

```bash
./build-images.py --ignore-new-releases
```

The same `DEVCONTAINERS_BASE_PINNED` variable is also used by the `trixie-py314-vsc` bake target base image in [docker-bake.hcl](docker-bake.hcl), so warning/check behavior and actual build input stay aligned.

### Known-latest variables in bake file

To make maintenance explicit and readable, [docker-bake.hcl](docker-bake.hcl) defines:
- `LATEST_KNOWN_DEBIAN` (default: `trixie`)
- `LATEST_KNOWN_PYTHON` (default: `3.14`)

`DEVCONTAINERS_BASE_PINNED` is composed from these values. This does not replace live detection, but improves local intent clarity and reduces scattered hardcoded values.

Policy:
- Keep `LATEST_KNOWN_*` aligned with the currently adopted stable baseline.
- If upstream releases move ahead, gate will fail until you either:
	- update `LATEST_KNOWN_*` to adopt the new baseline, or
	- run with `--ignore-new-releases` intentionally.

### Latest dynamic target

`docker-bake.hcl` includes a dynamic target/group:
- target: `latest-vsc`
- group: `detection`

`latest-vsc` uses resolver-exported live values (`DEVCONTAINERS_BASE_DYNAMIC_LATEST`, Python, Debian) so it automatically follows current upstream stable availability.

Target policy:
- `group "all"` includes both pinned targets and `latest-vsc`.
- `group "detection"` keeps a focused entrypoint for resolver-driven validation-only runs.
- This allows one default build invocation to publish the pinned baseline plus one dynamic-latest candidate.

Example build of the dynamic latest target:

```bash
docker buildx bake -f docker-bake.hcl detection --load
```

You can still force a specific older base to validate gate behavior:

```bash
DEVCONTAINERS_BASE_PINNED=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py
```

Then continue anyway only when explicitly requested:

```bash
DEVCONTAINERS_BASE_PINNED=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-images.py --ignore-new-releases
```

## Manifest Location and Content

Each built image writes a markdown manifest at:

```text
/usr/local/share/modern-debian-tools-python-debug/manifest.md
```

Manifest sections:
- `Base`
- `First-Party Wheels`
- `AI CLI Tools`
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

- `First-Party Wheels` is the wheel inventory view: image-owned releases (`ciu`, `cmru`).
- `AI CLI Tools` is the agent CLI inventory view: `aider`, `reasonix`, `deepcode`, `openclaw`, `codex`, `claude`, `antigravity`.
- `Custom Tooling` is an operational view: tool executables and their runtime `--version` output.
- `System packages` is a package inventory view: selected apt package names and versions.

This means one component can appear in both sections without being duplicated.
The manifest now splits first-party wheels and AI CLI tools into their own sections so the
release inventory reads the same way as the image build pipeline.

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

Pin AI tooling versions when needed:

```bash
CODEX_VERSION=0.34.0 CLAUDE_CODE_VERSION=1.0.27 AIDER_VERSION=0.64.0 ./build-images.py
```

Disable optional AI tooling across all image variants:

```bash
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_ANTIGRAVITY=false INSTALL_AIDER=false INSTALL_REASONIX=false INSTALL_DEEPCODE=false INSTALL_OPENCLAW=false ./build-images.py
```

Notes:
- `ANTIGRAVITY_VERSION` currently follows upstream `latest` manifest resolution during staging.
- `AIDER_VERSION=latest` resolves at image build time via pip.

## GHCR Credentials

Use PAT with package scopes (classic token):
- `write:packages`
- `read:packages`

Configured via environment (for example `.env` loaded by your release tooling).
The release pipeline mirrors each GHCR package's visibility to the source repository after push, so new releases should not need a manual package-settings toggle.

## Persisting AI Tool State

If you want agent state to survive devcontainer rebuilds, keep the workspace mount persistent and
add the tool-specific home directories below:

- Workspace root for `reasonix.toml`, `deepcode_config.json`, `AGENTS.md`, and any repo-local scratch files
- `/home/vscode/.config/reasonix`
- `/home/vscode/.deepcode`
- `/home/vscode/.openclaw`

The key files and directories are:

- Reasonix user config: `~/.config/reasonix/`
- DeepCode sessions: `~/.deepcode/sessions/`
- OpenClaw config: `~/.openclaw/openclaw.json`

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

### Critical freshness behavior

`scripts/resolve-devcontainers-release.py` now **always pulls** the configured base devcontainer image before reading labels. This avoids stale local-cache metadata during build/push.

### AIDER_VERSION workaround (Python 3.13/3.14)

The default `AIDER_VERSION=main` installs aider-chat from upstream git `main` branch
because the latest PyPI release (0.86.2) pins `Python <3.13`. PR
[Aider-AI/aider#4899](https://github.com/Aider-AI/aider/pull/4899) added 3.13/3.14 support
but no release has been cut yet. See `USAGE.md` for details on switching modes.
