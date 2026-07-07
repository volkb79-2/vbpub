# Modern Debian Python Debug Images

This project builds and publishes four related image families:
- `modern-debian-tools-python-debug`
- `modern-debian-tools-python-debug-vsc-devcontainer`
- `modern-debian-tools-python-debug-php85`
- `modern-debian-tools-python-debug-php85-vsc-devcontainer`

The purpose is to provide a curated, reproducible Debian + Python environment with modern CLI tooling for local development, CI, and VS Code devcontainers.

Rich GHCR-facing package docs live under [package-manifests-versioned](package-manifests-versioned/README.md). The release resolver regenerates those versioned Markdown pages on each build so OCI labels can point to repository-hosted Markdown instead of relying on flattened GHCR description text.

The versioned release pages are copied into the image as `/usr/local/share/modern-debian-tools-python-debug/manifest.md`, which is the canonical manifest path. `/home/vscode/mdt-manifest.md` is kept as a compatibility symlink, and `/etc/os-release` advertises the shared-root location via `IMAGE_MANIFEST=/usr/local/share/modern-debian-tools-python-debug/manifest.md`.

## Image Families

1. `modern-debian-tools-python-debug`
	- Base: `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Use when you want a plain Python image with the custom tool stack.

2. `modern-debian-tools-python-debug-vsc-devcontainer`
	- Base: `mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Use when you want Microsoft devcontainer behavior plus custom tooling.

3. `modern-debian-tools-python-debug-php85`
	- Base: `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Adds PHP 8.5, Composer, Xdebug, and the common web-debugging extensions.

4. `modern-debian-tools-python-debug-php85-vsc-devcontainer`
	- Base: `mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}`
	- Same PHP 8.5 stack as the base flavor, but with Microsoft devcontainer behavior.

## Tagging and Variants

Tag format:

```text
<debian>-py<python>[-php85]-<YYYYMMDD>
```

Examples:
- `trixie-py3.14-20260511`
- `trixie-py3.14-php85-20260630`
- `trixie-py3.14-latest`
- `latest` (family-wide floating tag)

Enabled build group target list is in [docker-bake.hcl](docker-bake.hcl) under `group "all"`; `group "everything"` is the wider local superset.

The docker images use date-based tags (`trixie-py3.14-20260616`, `20260616-2` for same-day rebuilds) plus floating `latest`. 

## Bake Helpers and Build Groups

`docker-bake.hcl` is the source of truth for what gets built.

- `group "all"`: release/publish matrix used by `build-push.py` and by the release-doc generator.
- `group "everything"`: local exhaustive superset (`all` + `base` + `multi`).
- `group "detection"`: resolver-only latest-stable probe target.
- A plain `docker buildx bake` resolves to the default group, which currently points at `everything`.
- Release builds should run through a resource-confined, named builder via `BUILDX_BUILDER` — see [USAGE.md](USAGE.md) § "Builder governance (`BUILDX_BUILDER`)".

Helper functions:

- `base_tag` / `base_latest_tag`: base-family immutable and floating tags.
- `vsc_tag` / `vsc_latest_tag`: single-Python VSC devcontainer tags.
- `php_tag` / `php_latest_tag`: PHP 8.5 base-image tags.
- `php_vsc_tag` / `php_vsc_latest_tag`: PHP 8.5 VSC devcontainer tags.
- `vsc_multi_tag` / `vsc_multi_latest_tag`: multi-Python VSC devcontainer tags.
- `package_manifest_relpath` / `package_manifest_url`: versioned release-page path and URL.
- `package_docs_readme_relpath` / `package_docs_readme_url`: family index path and URL.
- `package_latest_relpath` / `package_latest_url`: stable landing-page path and URL.
- `description_with_manifest_docs`: OCI description helper that appends the release-page URL.

If you add or remove a Python variant, update the matching target block and its tag/helper docs together. The build matrix is explicit; it is not inferred from filenames.

## Canonical Manifest

The user-facing manifest is copied into the image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
`/home/vscode/mdt-manifest.md` is kept as a compatibility symlink, and `/etc/os-release` gets a custom `IMAGE_MANIFEST=/usr/local/share/modern-debian-tools-python-debug/manifest.md` entry so scripts can discover it without hardcoding a second path.

The repository-hosted versioned manifest is intended to match that in-image file.

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
- `reasonix`, `openclaw` — npm-based, installed separately from the venv and backed by the image's upstream Node 24 toolchain
- `codex`, `claude-code`, `antigravity` — installed separately from the venv

Node 24 is sourced from NodeSource because Debian 13 still ships Node 20, and Reasonix/OpenClaw
need a newer runtime.

Container inspection tools are also installed in-image:
- `dtop` - live per-container CPU, memory, I/O, and network drilldown
- `lazydocker` - Docker/Compose TUI for lifecycle, logs, exec, and stats
- `glances` - broader host resource view with Docker awareness
- `dive` - image-layer inspection
- `syft` - SBOM generation and image/package inventory analysis
- `htop` - GitHub-sourced build with a shipped default config

Neovim is also shipped as `nvim`, with the NvChad `v2.5` starter config staged
into `/home/vscode/.config/nvim`. The shell defaults prefer `nvim` as the
editor when it is present.

For zswap specifically, recent `htop` builds can surface compressed-memory/zswap counters, and the
shipped `zswap-status` shell helper prints the kernel counters directly when they are exposed under
sysfs or debugfs.

Both image families create the `vscode` user if the base image does not already provide it, so consumer repos can use the same `remoteUser: "vscode"` setting for either family.

## Customization Roots

The image keeps one visible home for shipped behavior and one visible home for user overrides:

- `/usr/local/share/modern-debian-tools-python-debug/` for shipped profile files, alias templates, and manifest support files
- `/home/vscode/.config/modern-debian-tools-python-debug/` for user-editable bootstrap state, including the central `ai.env`, `aliases.sh`, `shell.env`, `htoprc`, `mc.ini`, `nanorc`, and `lesspipe.sh`

The shell bootstrap sources the user files once at session start. That means `ai.env` becomes the single source of truth for tools such as Aider, Claude Code, Codex, Reasonix, and OpenClaw, while tool-specific auth files that need a local path but should not duplicate secrets are symlinked back to that central file.

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
- `group "all"` includes the released VSC targets only.
- `group "everything"` adds `all`, `base`, and `multi` for an exhaustive local build.
- `group "detection"` keeps a focused entrypoint for resolver-driven validation-only runs.
- `latest-stable` is the floating alias for the resolver-selected stable devcontainer image.

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
- `AI CLI Tools` is the agent CLI inventory view: `aider`, `reasonix`, `openclaw`, `codex`, `claude`, `antigravity`.
- `Custom Tooling` is an operational view: release-managed tool executables and their runtime `--version` output.
- `System packages` is a Debian package inventory view: apt package names and versions.

This means one component can appear in both sections without being duplicated.
The manifest now splits first-party wheels and AI CLI tools into their own sections so the
release inventory reads the same way as the image build pipeline.

Example:
- `aider` in `Custom Tooling` is the resolved release version from `aider --version`.
- `postgresql-client=...` in `System packages` is the Debian package that provides `psql`.
- `psql` stays out of `Custom Tooling` so Debian-shipped packages are grouped together.

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
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_ANTIGRAVITY=false INSTALL_AIDER=false INSTALL_REASONIX=false INSTALL_OPENCLAW=false ./build-images.py
```

Notes:
- `ANTIGRAVITY_VERSION` currently follows upstream `latest` manifest resolution during staging.
- `AIDER_VERSION=latest` resolves to the current PyPI release version during staging.

## GHCR Credentials

Use PAT with package scopes (classic token):
- `write:packages`
- `read:packages`

Configured via environment (for example `.env` loaded by your release tooling).
The release pipeline mirrors each GHCR package's visibility to the source repository after push, so new releases should not need a manual package-settings toggle.

## Persisting AI Tool State

If you want agent state to survive devcontainer rebuilds, keep the workspace mount persistent and
let the shipped mount layout persist the tool homes below:

- Workspace root for `reasonix.toml`, `AGENTS.md`, and any repo-local scratch files
- `/home/vscode/.claude`
- `/home/vscode/.codex`
- `/home/vscode/.reasonix`
- `/home/vscode/.openclaw`
- `/home/vscode/.config/modern-debian-tools-python-debug`
- `/home/vscode/.config/modern-debian-tools-python-debug/aliases.sh`

The central key file is:

- `~/.config/modern-debian-tools-python-debug/ai.env`

Supported key names:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `DEEPSEEK_API_KEY`

Reasonix and OpenClaw also read their tool-local `.env` paths, but those are symlinked back to the same central file so the key values live in one place only.

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
- Awesome Tools List for Docker ecosystem: https://github.com/veggiemonk/awesome-docker/blob/master/README.md

### Critical freshness behavior

`scripts/resolve-devcontainers-release.py` now **always pulls** the configured base devcontainer image before reading labels. This avoids stale local-cache metadata during build/push.

### AIDER_VERSION workaround (Python 3.13/3.14)

The default `AIDER_VERSION=main` installs aider-chat from upstream git `main` branch.
`AIDER_VERSION=latest` now resolves to the current PyPI release version during staging,
which keeps the manifest concrete while still letting you opt into the branch build when
you need it. See `USAGE.md` for details on switching modes.

## Notes

### docker buildx/bake (BuildKit)

docker buildx/bake (BuildKit) defaults to compression=gzip at the gzip library default (~level 6).

Cheaper alternative if you only want the gzip→zstd win without re-layering: build mdt with `docker buildx build --output type=image,compression=zstd,compression-level=14,force-compression=true ....` That re-compresses every layer to zstd-14 in the build itself, with the same layer topology.

### Compression benchmark

Measured on `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260627-2`.

Baseline gzip export from the local daemon to OCI:

- compressed size: `2,776,489,762` bytes
- layer count: `48`

BuildKit-style zstd-only export of the same image structure:

- compressed size: `2,308,920,317` bytes
- layer count: `48`
- reduction vs gzip baseline: `16.8%`

`docker-repack` on the same image with `--target-size 500MB`:

- compressed size: `1,946,927,625` bytes
- layer count: `6`
- reduction vs gzip baseline: `29.9%`
- additional win vs zstd-only export: `15.7%`

`docker-repack` sweep of larger target sizes on the same image:

| target size | compressed size | layer count |
| --- | ---: | ---: |
| `50MB` | `1,883 MiB` | `36` |
| `100MB` | `1,872 MiB` | `22` |
| `200MB` | `1,868 MiB` | `12` |
| `500MB` | `1,857 MiB` | `6` |
| `1GB` | `1,857 MiB` | `4` |
| `2GB` | `1,857 MiB` | `3` |
| `4GB` | `1,856 MiB` | `2` |

Interpretation:

- `docker-repack` is the bigger compression win because it deduplicates and re-slices layers.
- `zstd` is the lower-risk optimization if you want to keep the same layer topology.
- The repack result changes layer hashes and image digest, so the release path pushes the repacked OCI layout instead of the unrepacked BuildKit output.
- The benchmark now covers `50MB`, `100MB`, `200MB`, `500MB`, `1GB`, `2GB`, and `4GB`; the larger slices mostly trade layer count for essentially the same compressed size on this image.
- For this image, `2GB` is the balanced release target: it gets the repacked image down to 3 layers with no size penalty versus `1GB`, while `4GB` only saves another MiB and collapses the topology to 2 layers.

### `docker-repack`

`docker-repack` controls how the deduped content is sliced into layers: smaller `--target-size` means more, smaller layers with better parallel-download/cache granularity but more per-layer compression-dictionary overhead; larger means fewer fat layers with slightly better ratio but coarser caching.

For this image family, the benchmark script is:

`scripts/benchmark-docker-repack.sh`

It is intentionally benchmark-only. The release flow uses the same repack logic via `RELEASE_IMAGE_FLOW=repack` and `REPACK_TARGET_SIZE=2GB`.

(b) How to count layers, source vs target:
```bash
# source (registry, after resolving the amd64 digest):
skopeo inspect --raw docker://<img> | jq '.layers | length'
# target OCI layout dir:
skopeo inspect --raw oci:///tmp/mdt-repacked2 | jq '.layers | length'
# a local daemon image (diff-layer count):
docker inspect <img> --format '{{len .RootFS.Layers}}'
```
