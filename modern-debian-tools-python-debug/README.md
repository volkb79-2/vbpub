# Modern Debian Tools + Python Debug (mdt)

A curated, reproducible Debian + Python environment with modern CLI tooling, an AI-agent
toolchain, and a debug cockpit venv — published as two GHCR package families for use in
local development, CI, and VS Code devcontainers.

```json
{
  "image": "ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-latest",
  "remoteUser": "vscode"
}
```

Consumer repos should reference a published image rather than building from this
Dockerfile. A ready-made devcontainer definition — mounts, host cgroup placement, and the
bootstrap hook — is in [`templates/`](templates/README.md); the lifecycle it implements is
explained in [DEVCONTAINER-LIFECYCLE.md](DEVCONTAINER-LIFECYCLE.md).

## Repository map

| Path | What lives there |
|---|---|
| [`Dockerfile`](Dockerfile), [`docker-bake.hcl`](docker-bake.hcl) | The image and the build matrix — bake is the source of truth for what gets built |
| [`build-push.py`](build-push.py), [`cmru.build.toml`](cmru.build.toml) | Release entry point and its step/env configuration |
| [`scripts/`](scripts/) | Release-time machinery: base resolver, bake/repack lanes, tool staging, OCI validation, benchmarks |
| [`requirements/`](requirements/), [`apt/`](apt/), [`pip/`](pip/), [`ai-cli-tools.list`](ai-cli-tools.list) | What goes *into* the image: venv toolkit, Debian packages, first-party wheels, AI CLIs |
| [`customization/`](customization/README.md) | Shipped shell/profile/editor assets (`zshrc`, `aliases.sh`, `ai.env.example`, `htoprc`, …) |
| [`templates/`](templates/README.md) | The consumer-facing `devcontainer.json` + bootstrap script |
| [`host-setup/`](host-setup/README.md) | **Host-side** companion: cgroup v2 tiers that govern devcontainers and the containers they spawn |
| [`docs/`](#documentation) | Architecture and doctrine documents — see the index below |
| [`package-manifests-versioned/`](package-manifests-versioned/README.md) | Generated GHCR-facing release pages, one per published tag |
| [`benchmarks/`](benchmarks/) | Recorded measurement runs (recompression, time-to-connect) |

## Image families

1. **`modern-debian-tools-python-debug`** — base `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`.
   A plain Python image with the custom tool stack.
2. **`modern-debian-tools-python-debug-vsc-devcontainer`** — base
   `mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}`.
   Microsoft devcontainer behavior plus the same tooling.

PHP 8.5 is **not** a third family — it is a tag variant of both, see
[Tags and variants](#tags-and-variants).

Both families share the same project-owned tool layer. The difference is the base image and
the runtime integration around it:

| Capability | Native image | VSC devcontainer image |
|---|---|---|
| Intended use | Docker `run`, CI, SSH, or a plain shell | VS Code Dev Containers with `remoteUser: vscode` |
| zsh + interactive helpers | Installed by this project | Installed by this project, extending the base's Oh My Zsh rather than replacing it |
| Docker CLI, Compose, Buildx | Present; no daemon or socket provided | Present; the template adds `docker-outside-of-docker`, mounts the host socket, sets `DOCKER_HOST` |
| VS Code server / features | Not included | Inherited from the base and the consumer `devcontainer.json` |
| Oh My Zsh | Not installed | Inherited from the base |

The Docker tools are **clients only** — neither image runs a daemon. In the native image
`docker version` needs an explicitly configured remote `DOCKER_HOST` or a mounted socket.

Both families create the `vscode` user when the base does not, so consumers can use
`remoteUser: "vscode"` with either.

## Tags and variants

```text
<debian>-py<python>[-<flavor>]-<YYYYMMDD>[-<n>]
```

| Example | Meaning |
|---|---|
| `trixie-py3.14-20260511` | immutable dated build |
| `trixie-py3.14-20260707-10` | same-day rebuild, counter suffix |
| `trixie-py3.14-latest` | floating tag for that Debian/Python pair |
| `trixie-py3.14-php8.5-20260707-10` | PHP 8.5 flavor, same package family |
| `trixie-py3.14-php8.5-latest` | floating PHP 8.5 tag |
| `latest` | family-wide floating tag — see the caveat below |

Tags produced by the `*_tag` / `*_latest_tag` helpers always follow the pattern above. The
bare family-wide `latest` is different: it is **hardcoded inline** on three targets in
`docker-bake.hcl` rather than emitted by a helper —

| Target | Also tagged |
|---|---|
| `trixie-py314` | `modern-debian-tools-python-debug:latest` |
| `trixie-py314-vsc` | `…-vsc-devcontainer:latest` |
| `trixie-py314-py311-py39-vsc` | `…-vsc-devcontainer:latest` |

— so the last two **collide**: whichever builds later owns the VSC family's floating
`latest`, and which one that is depends on bake ordering, not on intent. Pin a
`<debian>-py<python>-latest` tag instead of the bare one if it matters to you.
(`latest-vsc` additionally publishes `…-vsc-devcontainer:latest-stable`, the alias for the
resolver-selected stable base.)

### PHP 8.5 flavor

PHP 8.5 (CLI/FPM, Composer, Xdebug, and the common web-debugging extensions) is built by
the `trixie-py314-php85` / `trixie-py314-php85-vsc` bake targets and publishes into the two
package names above, distinguished only by the `-php8.5-` tag segment. Adding a future
flavor should follow the same pattern — a new tag segment, never a new package name.

## What is in the image

### Python environments

| Venv | Path | Contents |
|---|---|---|
| Primary | `/home/vscode/.venv` | Full toolkit — see below |
| Secondary 3.11 | `/home/vscode/.venv-py311` | Lean: `pip`, `uv`, `debugpy`, `ruff` |
| Secondary 3.9 | `/home/vscode/.venv-py39` | Lean: `pip`, `uv`, `debugpy`, `ruff` |

Secondary venvs exist only in the multi-Python targets and are baked at build time — no
post-create download. VS Code discovers them automatically (`Python: Select Interpreter`),
or activate one directly:

```bash
source /home/vscode/.venv-py311/bin/activate
uv pip install -r requirements.txt --python /home/vscode/.venv-py311/bin/python
```

The primary toolkit is defined in [`requirements/toolkit.txt`](requirements/toolkit.txt) —
the single source of truth, every entry annotated. It splits into dev/build tooling
(`ruff`, `mypy`, `pytest`, `build`, `uv`, `tox`, …) and **inspection / AI-scratch
libraries** (`httpx`, `asyncpg`, `redis`, `hvac`, `sqlalchemy`, `dnspython`,
`websockets`, …) for talking to running services interactively. `ruff` supersedes `black`
and `isort`, so neither is installed.

> This venv is a **debug cockpit**, not a gating test environment. "Green here" is never a
> ship signal — a project's gating tests belong in a test image built `FROM <app-base>`.
> See [docs/CONTAINER-DOCTRINE.md](docs/CONTAINER-DOCTRINE.md), and
> [docs/CONSUMER-AI-GUIDANCE.md](docs/CONSUMER-AI-GUIDANCE.md) for what to put in your
> repo's AI instruction files.

Debian packages come from [`apt/packages.list`](apt/packages.list); first-party wheels
(`ciu`, `cmru`) are staged from their GitHub releases into [`pip/`](pip/).

### AI CLI tools

Enabled by default, each controlled by an `INSTALL_*` build arg:

- `aider-chat` (`INSTALL_AIDER`) — in the venv.
- `reasonix`, `openclaw`, `opencode`, `copilot` — npm-based, installed into the user-owned
  `/home/vscode/.local` prefix on the image's Node 26 toolchain. OpenCode uses its resolved
  platform package directly, because the `opencode-ai` meta-package can select mutually
  incompatible glibc and musl optional packages under npm 11.
- `codex` — the official user-local standalone installer.
- `claude-code`, `antigravity` — image-owned binaries.

These are deliberately installed **for the `vscode` user**, so their normal upgrade paths
need no `sudo`: `codex update`, `reasonix upgrade`, `opencode upgrade`. Rebuild the image
to refresh the pinned build manifest.

Node 26 comes from NodeSource because Debian 13 ships Node 20, which is too old for the
npm-based CLIs.

Startup guidance for consumer repos: the shipped
`/usr/local/share/modern-debian-tools-python-debug/AGENTS.md.example`. The cross-CLI
adapter pattern is in [docs/AI-AGENT-TOOL-DISCOVERY.md](docs/AI-AGENT-TOOL-DISCOVERY.md).

Pin versions or opt out at build time:

```bash
CODEX_VERSION=0.34.0 CLAUDE_CODE_VERSION=1.0.27 AIDER_VERSION=0.64.0 ./build-push.py --build
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_AIDER=false ./build-push.py --build
```

`AIDER_VERSION` defaults to `main` (upstream git branch); `AIDER_VERSION=latest` resolves
the current PyPI release at staging time, which keeps the manifest concrete.
`ANTIGRAVITY_VERSION` follows upstream `latest` manifest resolution.

### Shell and CLI tooling

Both variants install zsh, Debian's zsh completion support, `zsh-autosuggestions`, and
`zsh-syntax-highlighting`. The shared config at
`~/.config/modern-debian-tools-python-debug/zshrc` enables standalone fzf integration
(`fzf --zsh`), Ctrl-R fuzzy history, Ctrl-T file search, Alt-C directory search,
completions, inline suggestions, and syntax highlighting. The VSC base `.zshrc` is
*extended*, not replaced, so its prompt and Oh My Zsh behavior survive.

**On Powerlevel10k:** Oh My Zsh is a configuration framework (plugins, themes, startup);
p10k is a prompt theme. p10k is not required for history search, completions, fzf,
autosuggestions, or highlighting — installing it by default would add a second prompt
policy and make the two families less predictable. Install it in your own `~/.zshrc` if you
want it; the shared integrations still apply.

Container and system inspection, in-image:

| Tool | For |
|---|---|
| `dtop` | live per-container CPU, memory, I/O, network drilldown |
| `lazydocker` | Docker/Compose TUI — lifecycle, logs, exec, stats |
| `glances` | broader host resource view with Docker awareness |
| `dive` | image-layer inspection |
| `syft` | SBOM generation, image/package inventory |
| `htop` | GitHub-sourced build with a shipped default config |
| `zswap-status` | prints kernel zswap counters directly from sysfs/debugfs |

Neovim ships as `nvim` with the NvChad `v2.5` starter config staged into
`/home/vscode/.config/nvim`; the shell defaults prefer `nvim` as `$EDITOR` when present.
GitHub-release tools keep their manpages and completions where upstream ships them; `fd` is
installed as both `fd` and Debian-compatible `fdfind`.

### Customization roots

One visible home for shipped behavior, one for user overrides:

- `/usr/local/share/modern-debian-tools-python-debug/` — shipped profile files, alias
  templates, manifest support files.
- `/home/vscode/.config/modern-debian-tools-python-debug/` — user-editable bootstrap state:
  `ai.env`, `aliases.sh`, `shell.env`, `htoprc`, `mc.ini`, `nanorc`, `lesspipe.sh`.

The shell bootstrap sources the user files once per session, which makes **`ai.env` the
single source of truth** for Aider, Claude Code, Codex, Reasonix, OpenClaw, and OpenCode.
Tool-specific auth files that need a local path are symlinked back to it rather than
duplicating secrets. Supported keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`.

To survive rebuilds, keep these persistent (the shipped mount layout already does):
the workspace root, `/home/vscode/.claude`, `.codex`, `.reasonix`, `.openclaw`, and
`/home/vscode/.config/modern-debian-tools-python-debug`.

### Canonical manifest

Every image writes a Markdown manifest to
`/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
`/home/vscode/mdt-manifest.md` is a compatibility symlink, and `/etc/os-release` carries
`IMAGE_MANIFEST=<that path>` so scripts can find it without hardcoding a second location.
The repository-hosted versioned page under
[`package-manifests-versioned/`](package-manifests-versioned/README.md) is regenerated on
each build and is intended to match the in-image file, so OCI labels can point at
repository-hosted Markdown instead of flattened GHCR description text.

Sections, and why one component can appear in two of them without duplication:

| Section | View |
|---|---|
| `Base` | Debian + Python version, `OCI_VERSION`, computed tag pattern, devcontainers release (`v0.4.x`) and image version (`3.0.x`) |
| `First-Party Wheels` | image-owned releases: `ciu`, `cmru` |
| `AI CLI Tools` | agent CLI inventory: `aider`, `reasonix`, `openclaw`, `opencode`, `copilot`, `codex`, `claude`, `antigravity` |
| `Custom Tooling` | operational view — release-managed executables and their runtime `--version` output |
| `Python packages` | venv inventory |
| `System packages` | Debian package names and versions |

For example `aider` appears under `Custom Tooling` as the version `aider --version`
reports, while `postgresql-client=…` appears under `System packages` as the Debian package
providing `psql` — `psql` itself stays out of `Custom Tooling` so apt-shipped packages stay
grouped together.

### `pip install` vs `pipx`

Both are valid; they solve different needs. `python -m pip install` puts everything in one
environment — good for a curated, integrated toolset. `pipx` isolates each CLI in its own
venv — better when tool dependency trees must not interact. This image deliberately uses a
shared curated environment (`/home/vscode/.venv`) for consistency across tools.

## Governing the container on the host

Placement into a cgroup tier is **create-time only** and cannot be expressed from inside an
image, so it lives in two places that must agree:

- **Container side** — `templates/devcontainer.json` ships
  `"--cgroup-parent=interactive.slice"` in `runArgs`. Safe everywhere: if the host has no
  such unit, systemd creates a transient unlimited slice and the container starts normally.
- **Host side** — [`host-setup/`](host-setup/README.md) installs and maintains the tiers
  themselves: `interactive.slice` for devcontainers, `besteffort.slice` for the test/build
  stacks they spawn, IO caps derived from a measured device baseline, and a health check.

```bash
sudo host-setup/install.sh --with-baseline    # ~4 min of saturated disk — quiet window
sudo mdt-host-check.sh
```

[`host-setup/CGROUP-NOTES.md`](host-setup/CGROUP-NOTES.md) explains what a slice unit
fundamentally cannot express — and the BFQ caveats, where `IOWeight` does not mean what it
says. Read it before changing any weight.

BuildKit builders are governed separately; see
[docs/BUILD-ARCHITECTURE.md](docs/BUILD-ARCHITECTURE.md).

## Building and publishing

### Entry point

```bash
./build-push.py --build      # resolve env + build locally
./build-push.py --push       # push previously built images
./build-push.py --rebuild    # build then push
```

Step and environment configuration is [`cmru.build.toml`](cmru.build.toml); the build
matrix is [`docker-bake.hcl`](docker-bake.hcl). Release builds run on the resource-confined
named builder selected by `BUILDX_BUILDER`, with limits defined in `cmru.build.toml`.

### Bake groups

| Group | Contents |
|---|---|
| `all` | the release/publish matrix used by `build-push.py` and the release-doc generator |
| `everything` | exhaustive local superset: `all` + `base` + `multi` + `php` |
| `base`, `vsc`, `multi`, `php` | the individual slices `everything` composes |
| `detection` | resolver-only probe (`latest-vsc`) |

There is **no `default` group** — a bare `docker buildx bake` fails with
`failed to find target default`. Always name a group or target:

```bash
docker buildx bake -f docker-bake.hcl detection --load
```

The matrix is explicit, never inferred from filenames. If you add or remove a Python
variant, update the target block and its tag/helper docs together.

### Tag helper functions

| Helper | Produces |
|---|---|
| `base_tag` / `base_latest_tag` | base-family immutable and floating tags |
| `vsc_tag` / `vsc_latest_tag` | single-Python VSC devcontainer tags |
| `base_tag_variant` / `base_latest_tag_variant` | base-family tags with a flavor segment before the date/`latest` |
| `vsc_tag_variant` / `vsc_latest_tag_variant` | the same for VSC devcontainer tags |
| `vsc_multi_tag` / `vsc_multi_latest_tag` | multi-Python VSC devcontainer tags |
| `package_manifest_relpath` / `package_manifest_url` | versioned release-page path and URL |
| `package_manifest_relpath_variant` / `..._url_variant` | same for flavor builds — keeps the filename collision-free with the plain build sharing that (debian, python) |
| `package_docs_readme_relpath` / `..._url` | family index path and URL |
| `package_latest_relpath` / `package_latest_url` | stable landing-page path and URL |
| `description_with_manifest_docs[_variant]` | OCI description helpers that append the release-page URL |

### Base-image freshness gate

`scripts/resolve-devcontainers-release.py` **always pulls** the configured base
devcontainer image before reading labels, so stale local-cache metadata cannot leak into a
build. During `--build` it additionally performs a live MCR tag-inventory check.

Default behavior is **fail-fast**: if a newer stable Python *or Debian* stream is detected,
the build stops before bake starts, with an actionable message and no traceback.

```text
[WARN] Newer stable devcontainers/python tag(s) detected for trixie: 1-3.15-trixie,
       3.15-trixie. Current base: 3.14-trixie. Recommended newest stable: 3.15-trixie.
```

Continue intentionally with `--ignore-new-releases`. Detection is dynamic — no future
version numbers are hardcoded — and covers newer Python for your current Debian, newer
Debian for your current Python, additional Debian codenames for your Python stream, and
newer Python streams already visible on other Debian variants.

The resolver exports `DEVCONTAINERS_RELEASE_STABLE`, `DEVCONTAINERS_VERSION_STABLE`,
`DEVCONTAINERS_BASE_DYNAMIC_LATEST`, `DEVCONTAINERS_DYNAMIC_LATEST_PYTHON`, and
`DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN`; `cmru.build.toml` passes them into bake args, then
into Dockerfile metadata and manifest content.

`docker-bake.hcl` declares `LATEST_KNOWN_DEBIAN` (`trixie`) and `LATEST_KNOWN_PYTHON`
(`3.14`), from which `DEVCONTAINERS_BASE_PINNED` is composed. This does not replace live
detection — it makes local intent explicit and removes scattered hardcoded values. Keep
`LATEST_KNOWN_*` aligned with the adopted stable baseline; when upstream moves ahead, the
gate fails until you either adopt the new baseline or pass `--ignore-new-releases`.

Override the checked/resolved base, e.g. to validate gate behavior:

```bash
DEVCONTAINERS_BASE_PINNED=mcr.microsoft.com/devcontainers/python:3.13-trixie ./build-push.py --build
```

To see what upstream currently offers:

```bash
./check-mcr-devcontainer-tags.py
```

```text
debian    3.12   3.13   3.14   3.15   3.16
--------  -----  -----  -----  -----  -----
bookworm  1pd    1pd    pd     .      .
trixie    pd     pd     pd     .      .
forky     .      .      .      .      .

Legend: 1 = 1- prefix, p = plain, d = dev- prefix, . = missing
```

### GHCR credentials and visibility

A classic PAT with `write:packages` and `read:packages`, supplied via environment (for
example a `.env` loaded by your release tooling). The release pipeline mirrors each GHCR
package's visibility to the source repository after push, so a new release should not need
a manual package-settings toggle. Note that **new GHCR packages default to private** — see
[GHCR-ACCESS-GUIDE.md](GHCR-ACCESS-GUIDE.md) § "When is public/private decided?". This is
one reason flavors are tag variants rather than new package names: no new family means no
new visibility state to sync.

The canonical direct-push release path publishes through BuildKit and does **not** call
`skopeo`. When using the cmru oci-image handler (`cmru.toml [project.xxx.oci]`), cmru
handles Docker login. The `.ghcr-auth.json` fallback (`REGISTRY_AUTH_FILE`) is only for
manual/local use outside cmru, such as the historical compression benchmark — keep that
file untracked.

Built images carry `net.volkb79.base-devcontainers-release` and
`net.volkb79.base-devcontainers-version` labels:

```bash
docker image inspect <image> \
  --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-release" }}'
```

### Compression and layer topology

BuildKit's generic registry exporter defaults to gzip. `cmru.build.toml` overrides that:
release publication uses OCI media types, forced **zstd level 3**, and the original layer
topology. The level is deliberately modest — cold time-to-connect and governed export cost
matter more than the last few compressed bytes.

Cold tests on both Docker stores showed native zstd level 3 faster than gzip, while a
three-layer 2 GB repack was substantially slower despite the smaller download. Methodology,
the historical target-size sweep, and the policy decision are in
[docs/IMAGE-DELIVERY-BENCHMARKS.md](docs/IMAGE-DELIVERY-BENCHMARKS.md).

`docker-repack` therefore remains **experimental** and is not in the canonical release
flow. It controls how deduped content is sliced into layers: a smaller `--target-size`
gives more, smaller layers with better parallel-download and cache granularity but more
per-layer compression-dictionary overhead; larger gives fewer fat layers with a slightly
better ratio and coarser caching. The benchmark is `scripts/benchmark-docker-repack.sh`;
the optional release lane is `RELEASE_IMAGE_FLOW=repack`, whose target size is configured
independently.

That optional path is OCI-layout-native: bake writes one OCI tar per target, the tar is
extracted into disk-backed scratch, `docker-repack` writes a second OCI layout, and the
governed BuildKit builder validates it by importing and unpacking before publication — no
daemon round-trip, no `skopeo` copy. It currently trips the fail-closed gate because of the
repacker defect recorded in the architecture guide; use the default `push` lane rather than
copying an invalid layout.

Counting layers, source vs target:

```bash
docker buildx imagetools inspect --raw <img> | jq '.layers | length'   # registry manifest
digest=$(jq -r '.manifests[0].digest' <oci-layout>/index.json)          # OCI layout dir
jq '.layers | length' "<oci-layout>/blobs/sha256/${digest#sha256:}"
docker inspect <img> --format '{{len .RootFS.Layers}}'                  # local daemon image
```

## Multi-Python variants

Standard single-Python targets ship one Python version in a full primary venv. Multi-Python
targets additionally bake lean secondary environments at image build time.

| Target | Primary | Secondaries | Tag example |
|---|---|---|---|
| `trixie-py314-vsc` | 3.14 (full) | — | `trixie-py3.14-latest` |
| `trixie-py314-py311-vsc` | 3.14 (full) | 3.11 (lean) | `trixie-py314-py311-latest` |
| `trixie-py314-py311-py39-vsc` | 3.14 (full) | 3.11, 3.9 (lean) | `trixie-py314-py311-py39-latest` |

Multi-Python tag format: `<debian>-<primary>-<secondary…>-<YYYYMMDD>` plus a matching
`-latest`.

To add a combination, copy an existing multi-Python target and update
`SECONDARY_PYTHON_VERSIONS` (space-separated dotted versions), the target name, and the
tags:

```hcl
target "trixie-py314-py313-py311-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    SECONDARY_PYTHON_VERSIONS = "3.13 3.11"
  }
  tags = [vsc_multi_tag("trixie", "py314-py313-py311"),
          vsc_multi_latest_tag("trixie", "py314-py313-py311")]
}
```

`vsc_multi_tag` / `vsc_multi_latest_tag` accept any `<debian>` and `<pythons_label>`, so
naming is fully flexible.

### Python version support

CPython has no odd/even stability distinction — all released minor versions are
production-quality. Windows as of June 2026:

| Version | EOL | Status |
|---|---|---|
| 3.9 | Oct 2025 | **EOL** — legacy compatibility testing only |
| 3.11 | Oct 2027 | Active — production staple |
| 3.13 | Oct 2029 | Active — previous stable |
| 3.14 | Oct 2030 | **Current stable** — primary base |

## Documentation

| Document | Covers |
|---|---|
| [USAGE.md](USAGE.md) | Consuming the image day to day |
| [DEVCONTAINER-LIFECYCLE.md](DEVCONTAINER-LIFECYCLE.md) | Build → create → start ordering, what runs when, host resource governance |
| [host-setup/README.md](host-setup/README.md) | Installing the host cgroup tiers |
| [host-setup/CGROUP-NOTES.md](host-setup/CGROUP-NOTES.md) | What slice units can't express; BFQ caveats |
| [docs/CONTAINER-DOCTRINE.md](docs/CONTAINER-DOCTRINE.md) | Which concerns belong to the image, the orchestration, and the host |
| [docs/CONSUMER-AI-GUIDANCE.md](docs/CONSUMER-AI-GUIDANCE.md) | What to put in a consumer repo's AI instruction files |
| [docs/AI-AGENT-TOOL-DISCOVERY.md](docs/AI-AGENT-TOOL-DISCOVERY.md) | Cross-CLI adapter pattern; why exact versions stay in the generated inventory |
| [docs/BUILD-ARCHITECTURE.md](docs/BUILD-ARCHITECTURE.md) | Build/repack/publication flow, cgroup boundaries, load attribution |
| [docs/OCI-IMAGE-TOOLING.md](docs/OCI-IMAGE-TOOLING.md) | Human-manifest vs OCI-manifest, registry clients, layer trade-offs, CMRU reuse boundary |
| [docs/IMAGE-DELIVERY-BENCHMARKS.md](docs/IMAGE-DELIVERY-BENCHMARKS.md) | Compression and time-to-connect measurements and policy |
| [docs/DOCKER-IMAGE-STORE.md](docs/DOCKER-IMAGE-STORE.md) | Docker image store behavior |
| [GHCR-ACCESS-GUIDE.md](GHCR-ACCESS-GUIDE.md) | Registry auth, package visibility |
| [TODO.md](TODO.md) | Backlog |

External references:

- Upstream devcontainers images — <https://github.com/devcontainers/images>
- Python manifest — <https://raw.githubusercontent.com/devcontainers/images/main/src/python/manifest.json>
- MCR tag list — <https://mcr.microsoft.com/v2/devcontainers/python/tags/list>
- Awesome Docker — <https://github.com/veggiemonk/awesome-docker>
