# mdt-cockpit — a curated Debian + Python devcontainer cockpit

A curated, reproducible Debian + Python environment with modern CLI tooling, an AI-agent
toolchain, and a debug cockpit venv. The fork defines two GHCR package families for local
development, CI, and VS Code devcontainers; its first release has not been published yet.

```json
{
  "image": "ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-latest",
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
| [`build-push.py`](build-push.py), [`cmru.toml`](cmru.toml) | Release entry point and its complete project contract |
| [`scripts/`](scripts/) | Release-time machinery: base resolver, bake/repack lanes, tool staging, OCI validation, benchmarks |
| [`requirements/`](requirements/), [`apt/`](apt/), [`pip/`](pip/), [`ai-cli-tools.list`](ai-cli-tools.list) | What goes *into* the image: venv toolkit, Debian packages, first-party wheels, AI CLIs |
| [`customization/`](customization/README.md) | Shipped shell/profile/editor assets (`zshrc`, `aliases.sh`, `ai.env.example`, `htoprc`, …) |
| [`templates/`](templates/README.md) | The consumer-facing `devcontainer.json` + bootstrap script |
| [`host-setup/`](host-setup/README.md) | **Host-side** companion: cgroup v2 tiers that govern devcontainers and the containers they spawn |
| [`docs/`](#documentation) | Architecture and doctrine documents — see the index below |
| [`package-manifests-versioned/`](package-manifests-versioned/README.md) | Generated GHCR-facing release pages, one per published tag |
| [`benchmarks/`](benchmarks/) | Recorded measurement runs (recompression, time-to-connect) |

## Image families

1. **`mdt-cockpit`** — base `python:${PYTHON_VERSION}-${DEBIAN_VERSION}`.
   A plain Python image with the custom tool stack.
2. **`mdt-cockpit-devcontainer`** — base
   `mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}`.
   Microsoft devcontainer behavior plus the same tooling.

The fork uses its own runtime namespace: user overrides under `~/.config/mdt-cockpit` and
image support files under `/usr/local/share/mdt-cockpit`. Its published OCI package names are
`mdt-cockpit` and `mdt-cockpit-devcontainer`. Nothing is carried over from the original
project's paths; see [Moving from modern-debian-tools-python-debug](docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
and the [namespace rationale](docs/DESIGN-GUIDE.md#oci-names-keep-runtime-paths-stable).

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

## PWMCP browser service

MDT images include a PWMCP instance installed at `/srv/pwmcp-instance` and the
`pwmcp` client CLI on `PATH`. Start the service from its CIU root when an agent
or project needs browser automation:

```bash
cd /srv/pwmcp-instance
ciu up
pwmcp inspect https://example.com --format snapshot
```

Agents in the connected devcontainer can use the MCP endpoint
`http://pwmcp:8931/mcp`; Playwright clients use `ws://pwmcp:3000/`. The service
uses PWMCP's existing internal policy: no authentication and no host-published
ports. The generated installer and exact client wheels are verified and
versioned with PWMCP. The vendored devcontainer template exposes the CLI path
and `PWMCP_MCP_URL`; consumers using another harness must register that HTTP
MCP endpoint in their harness configuration. See [MDT's PWMCP consumer guide](docs/CONSUMERS.md#pwmcp-browser-service).
and [PWMCP's install and design notes](../pwmcp/README.md#standalone-instance-for-mdt-and-other-devcontainers).

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
| `trixie-py3.14-php8.5-dev-20261018` | **local-dev** build (`--local-dev`), local daemon only — see [Local-dev builds](#local-dev-builds) |
| `trixie-py3.14-php8.5-dev-latest` | floating local-dev alias; never pushed |

Tags produced by the `*_tag` / `*_latest_tag` helpers always follow the pattern above. The
bare family-wide `latest` is different: it is **hardcoded inline** on three targets in
`docker-bake.hcl` rather than emitted by a helper —

| Target | Also tagged |
|---|---|
| `trixie-py314` | `mdt-cockpit:latest` |
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
| Primary | `/opt/mdt/venv` | Full toolkit — see below; first on `PATH`, `python` resolves here (no `VIRTUAL_ENV`, no home symlink) |
| Secondary 3.11 | `/opt/mdt/venvs/py311` | Lean: `pip`, `uv`, `debugpy`, `ruff` |
| Secondary 3.9 | `/opt/mdt/venvs/py39` | Lean: `pip`, `uv`, `debugpy`, `ruff` |

Secondary venvs exist only in the multi-Python targets and are baked at build time — no
post-create download. VS Code discovers them automatically (`Python: Select Interpreter`),
or activate one directly:

```bash
source /opt/mdt/venvs/py311/bin/activate
uv pip install -r requirements.txt --python /opt/mdt/venvs/py311/bin/python
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
(`ciu`, `cmru`, `topos`, and `nyxloom`) are staged from their GitHub releases into
[`pip/`](pip/). Their runtime dependencies are deliberately installed in the primary
toolkit before the wheel layer, because that layer uses `pip --no-index`; the
`rich>=15.0.0` and `pygments>=2.21.0` floors in `requirements/toolkit.txt` are part of
that offline closure for Nyxloom.

### Headless VM tooling

The cockpit includes the userland needed to provision and boot disposable x86
test guests:

- `qemu-system-x86` — headless x86 guest execution, with KVM acceleration when
  the host exposes `/dev/kvm`;
- `qemu-utils` — create and inspect disk images with `qemu-img`;
- `ovmf` — UEFI firmware for x86 guests;
- `cloud-image-utils` — create cloud-init seed media such as `cloud-localds`.

These are command-line tools, not a VM service: MDT does not install or start
libvirt, a system-wide QEMU daemon, or a guest agent. Package installation does
not grant a container access to KVM or TAP networking. The default test shape is
an unprivileged runner using QEMU's TCG software emulation and user-mode
networking; it needs neither `/dev/kvm` nor `/dev/net/tun`. If a runner opts in
to KVM or TAP, it must request those host devices explicitly and remain in the
host's governed slice. Keep VM images and firmware scratch files on disposable
storage.

Do not use a privileged container as the isolation boundary for loop devices,
partition devices, `mkswap`, `swapon`, `swapoff`, initramfs, reboot, or other
host-kernel state. Docker containers share the host kernel; loop and swap are
not container-namespaced. Put those tests in a QEMU guest, whose own kernel
owns its loop devices and swap. A privileged container is suitable only for
tests that do not create/access such devices or mutate global kernel state.

### AI CLI tools

Enabled by default, each controlled by an `INSTALL_*` build arg:

- `aider-chat` (`INSTALL_AIDER`) — in the venv, from an exact commit of upstream `main`
  (`aider-main-pin.toml`) - the one tool that is not a vetted release, because the newest release needs
  Python <3.13 and this image is 3.14; exit condition: until an aider release supports Python 3.14
  ([why](docs/DESIGN-GUIDE.md#how-the-ai-clis-update-themselves)).
- `reasonix`, `openclaw`, `opencode`, `copilot`, `claudelink`, `pi` — npm-based, installed
  into the `mdt`-group prefix `/opt/mdt/npm` (the image's `NPM_CONFIG_PREFIX`) on the image's
  Node 26 toolchain; the runtime user updates them in place without `sudo`. OpenCode
  uses its resolved platform package directly, because the `opencode-ai` meta-package can
  select mutually incompatible glibc and musl optional packages under npm 11. `claudelink`
  (npm package `claudelink`) is an MCP server that coordinates multiple already-installed
  agent CLIs (Claude Code, Codex, etc.) as one mesh; `pi` (npm package
  `@earendil-works/pi-coding-agent`) is the Pi coding-agent CLI.
- `codex` — the official `@openai/codex` npm package under `/opt/mdt/npm`; its user data remains
  under `~/.codex`.
- `claude` — the official `@anthropic-ai/claude-code` npm package under `/opt/mdt/npm` (staged as the exact registry `latest`, or an explicit pin, and recorded in the manifest; user-updatable tools follow upstream and the image pins what it shipped); `claude update` and its background updater run `npm install -g` into that prefix, so an update is the `claude` on `PATH`. `antigravity` (`agy`) — an image-owned binary in `/opt/mdt/bin`.

Image-managed tools live in the `mdt` group tree (`/opt/mdt`, `/srv`): group-writable, so the
runtime user (a member of `mdt`) can `npm install -g @openai/codex@latest` or `pip install`
into the primary venv without `sudo`, even though Dev Containers re-numbers the `vscode` uid.
Such in-place updates under `/opt/mdt` last for the life of the container only: a recreate
restores the image's version, so **rebuilding the image is the durable way to change a tool**.
OpenCode is updated with `npm install -g opencode-linux-x64-baseline@latest` (its own `upgrade`
command cannot be used, see the [update table](docs/DESIGN-GUIDE.md#how-the-ai-clis-update-themselves));
Reasonix with `npm install -g reasonix@latest`. `PATH` is one definition ([One PATH order](docs/DESIGN-GUIDE.md#one-path-order)):
`/opt/mdt/bin`, the primary venv, `/opt/mdt/npm/bin`, PWMCP, the base image's own entries, and
`~/.local/bin` **last** — so a tool in `~/.local/bin` never silently shadows an image tool.
Rebuild the image to refresh its pinned tool versions.

Node 26 comes from NodeSource because Debian 13 ships Node 20, which is too old for the
npm-based CLIs.

Startup guidance for consumer repos: the shipped
`/usr/local/share/mdt-cockpit/AGENTS.md.example`. The cross-CLI
adapter pattern is in [docs/AI-AGENT-TOOL-DISCOVERY.md](docs/AI-AGENT-TOOL-DISCOVERY.md).

Pin versions or opt out at build time:

```bash
CODEX_VERSION=0.34.0 CLAUDE_CODE_VERSION=1.0.27 ./build-push.py --build
INSTALL_CODEX=false INSTALL_CLAUDE_CODE=false INSTALL_AIDER=false ./build-push.py --build
```

`copilot`, `openclaw` and `claudelink` are not pinned by environment: they install the newest release at
least 14 days old, recorded in `cmru.toml` by `cmru versions resolve` (a reasoned `version` + `reason`
override on the target is the supported pin; see [CONSUMERS](docs/CONSUMERS.md#ai-cli-versions-copilot-openclaw-claudelink)).
`AIDER_VERSION` and `AIDER_COMMIT` are refused: aider's commit is `aider-main-pin.toml`.
`ANTIGRAVITY_VERSION` follows upstream `latest` manifest resolution.

### Shell and CLI tooling

Both variants install zsh, Debian's zsh completion support, `zsh-autosuggestions`, and
`zsh-syntax-highlighting`. The shared config, loaded system-wide from `/etc/zsh/zshrc`
(your `~/.zshrc` runs after it and wins), enables standalone fzf integration
(`fzf --zsh`), Ctrl-R fuzzy history, Ctrl-T file search, Alt-C directory search,
completions, inline suggestions, and syntax highlighting. On the VSC image it also loads the
image-owned Oh My Zsh (`ZSH=/opt/mdt/oh-my-zsh`) with the devcontainers prompt, unless your own
`~/.zshrc` already loads it.

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
| `gdu` | interactive disk-usage analyzer (static build, pinned 5.38.0) |
| `dust` | `du` with a tree view and bars (static musl build, pinned 1.2.6) |
| `syft` | SBOM generation, image/package inventory |
| `htop` | GitHub-sourced build with a shipped default config |
| `zswap-status` | prints kernel zswap counters directly from sysfs/debugfs |
| `lnav` | ncurses log/JSONL viewer with SQL queries over parsed fields — see below |

Debian `testing` is configured as a **preview archive**: its package lists are visible to `apt`
but pinned to priority 100 (as are `oldstable` and `oldstable-backports`), so nothing is ever
upgraded or installed from it implicitly. Take a package from it deliberately with
`sudo apt install -t testing <pkg>` — see [Installing a package from testing](USAGE.md#installing-a-package-from-debian-testing)
and the rationale in [DESIGN-GUIDE](docs/DESIGN-GUIDE.md#debian-testing-preview-archive).

Neovim ships as `nvim` with the NvChad tree in `/etc/xdg/nvim` (system-wide, started by
Neovim's own `$XDG_CONFIG_DIRS` fallback, only while you have no `~/.config/nvim/init.lua` or `init.vim`; `sysinit.vim` redirects the lazy lockfile into your state directory);
the shell defaults prefer `nvim` as `$EDITOR` when present. Every default is wired where its
tool reads system-wide configuration — see [System-wide defaults](docs/DESIGN-GUIDE.md#system-wide-defaults).
GitHub-release tools keep their manpages and completions where upstream ships them; `fd` is
installed as both `fd` and Debian-compatible `fdfind`.

#### `lnav`: JSONL inspection

`lnav` ([tstack/lnav](https://github.com/tstack/lnav), latest release resolved at build time
via `LNAV_VERSION`, default `latest`) works on any JSON-lines file out of the box — point it
at one and its generic JSON format auto-detects timestamp-shaped and message-shaped fields:

```bash
lnav path/to/whatever.jsonl
```

On top of that, the image ships one **bundled format** at
`/etc/lnav/formats/nyxloom/nyxloom_events.json`, matched automatically by filename against
nyxloom's `events.jsonl` (or a `nyxloom events <project>` dump saved to a file ending the same
way) — no `-f`/`-i` flag needed. It maps nyxloom's `Event` schema
(`nyxloom/src/nyxloom/types.py`) into:

- a compact summary line — `TYPE  project  actor_kind:actor_id  seq=N  payload`
- proper timestamp parsing off the `timestamp` field
- `error`/`warning` **levels** derived from `type` (e.g. `ATTEMPT_FAILED`, `TICK_ERROR` →
  error; `TASK_BLOCKED`, `NEEDS_OPERATOR` → warning), so lnav's level-based navigation,
  highlighting, and `:filter-in`/`:filter-out` all work meaningfully on the event stream
- a queryable `nyxloom_events` SQL table, e.g. from lnav's `;`-prompt:
  `;SELECT type, count(*) FROM nyxloom_events GROUP BY type`

Any other JSONL file — including a different project's event log — still falls back to
lnav's stock generic-JSON handling; the bundled format only activates for files whose path
matches `events.jsonl`.

### KSM opt-in (kernel same-page merging)

Every process in the image opts into KSM by default: a tiny `LD_PRELOAD` shim
(`customization/ksm-optin.c`) calls `PR_SET_MEMORY_MERGE` in a constructor that runs on every
exec, so identical private-anonymous pages across containers become mergeable by the host's
`ksmd` (`/sys/kernel/mm/ksm/run=1`) — a deliberate CPU-for-RAM trade on hosts that run many
containers built from the same layers.

- **Build option:** `ENABLE_KSM_OPTIN` (default `true`). Set to `false` to leave `LD_PRELOAD`
  unset in the built image; the shim still compiles (a few KB, negligible build cost) but
  never activates. Reported in the generated manifest as `ksm-optin=true|false`.
- **Per-command opt-out at runtime**, regardless of the build-time default: `LD_PRELOAD= <cmd>`
  (or unset it in your own environment). Useful when running sanitizers/valgrind, which set
  their own `LD_PRELOAD` — compose as `LD_PRELOAD="theirs:/usr/local/lib/ksm-optin.so"` or drop
  ours for that invocation.
- **Runtime status banner:** since success depends on the *host's* kernel (needs 6.4+,
  `CONFIG_KSM`, and effectively `CAP_SYS_RESOURCE` in the container's user namespace) and not
  on anything decided at build time, every interactive shell login prints whether *this*
  container actually opted in — see the `ksm` line next to the cgroup banner in
  `customization/profile.sh`, and set `KSM_OPTIN_VERBOSE=1` for a line on every single exec.

### Complete persistent home

The vendored template mounts one host directory, `${localEnv:HOME}/mdt-cockpit-home/` (mode
`0700`), over `/home/vscode`. Agent logins, keys, histories, shell preferences, caches,
worktrees, and arbitrary user-created home data therefore survive image rebuilds without
maintaining a list of individual mount points. `/tmp` is a second host-backed source,
`${localEnv:HOME}/mdt-cockpit-tmp/` (mode `1777`); any further source is named
`${localEnv:HOME}/mdt-cockpit-<purpose>`.

**A new home starts empty and stays that way.** The image leaves `/home/vscode` empty and ships
no copy of any default into it: image-owned runtimes and CLIs live under `/opt/mdt` and `/srv`,
and every default (bash, zsh, nano, htop, mc, Neovim, `less`, the MDT shell files) is wired
where its tool reads system-wide configuration, so you override a default by creating your
own file. The in-container finalizer's only home write is a comment-only `~/.zshrc` stub (only
if you have no zsh startup file) that keeps `zsh-newuser-install` from taking over your first
terminal. The native host SSH directory remains a separate read-only mount at
`/run/mdt/host-ssh`.

There is no migration. If you used the previous split-mount template, the host bootstrap prints
one informational line while `~/mdt--mounted-folders/` still holds files and the new home is
empty, and you copy what you want by hand —
[Moving from modern-debian-tools-python-debug](docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
lists what is worth copying and what is not. See the
[path policy and rationale](docs/DESIGN-GUIDE.md#the-home-is-user-state-image-assets-live-outside-it)
and the [home path inventory](docs/HOME-PERSISTENCE.md).

The template sets `CODEX_SQLITE_HOME` to `/home/vscode/.codex/sqlite-shared` and keeps the
container running when its Dev Containers session disconnects with `shutdownAction: "none"`.
See the [Codex profile and container-lifetime notes](docs/CONSUMERS.md#codex-profile-state-and-container-lifetime).

### Customization roots

Image defaults, user config, and secrets have distinct roles:

- `/usr/local/share/mdt-cockpit/` — image-owned profile files, default shell/editor config,
  examples (`ai.env.example`, `aliases.sh.example`) and manifest support files. Nothing copies
  these into the home.
- `/home/vscode/.config/mdt-cockpit/` — **yours, created by you**: `ai.env` (keys), plus
  optional `shell.env` and `aliases.sh` that are sourced after the image's own and can override
  them. The image never writes this directory.

The shell profile sources `ai.env` once per session, which makes it the single source of truth
for Aider, Claude Code, Codex, Reasonix, OpenClaw, and OpenCode: the keys are exported into
every shell, so tools need no per-tool secret file. Supported keys: `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`.

Consuming repos can add file mounts with any filename. Existing sources use their actual host type;
missing sources follow the configured policy and source spelling. See the
[consumer example](docs/CONSUMERS.md#optional-git-config-mount) and the
[design rationale](docs/DESIGN-GUIDE.md#bootstrap-uses-the-source-filesystem-type).

### Canonical manifest

Every image writes a Markdown manifest to
`/usr/local/share/mdt-cockpit/manifest.md` (there is no copy or link in the home), and a
local-dev image adds a `Build Provenance` section. `/etc/os-release` carries
`IMAGE_MANIFEST=<that path>` so scripts can find it without hardcoding a second location.
The repository-hosted versioned page under
[`package-manifests-versioned/`](package-manifests-versioned/README.md) is regenerated on
each build and is intended to match the in-image file, so OCI labels can point at
repository-hosted Markdown instead of flattened GHCR description text.

Sections, and why one component can appear in two of them without duplication:

| Section | View |
|---|---|
| `Base` | Debian + Python version, `OCI_VERSION`, computed tag pattern, devcontainers release (`v0.4.x`) and image version (`3.0.x`) |
| `First-Party Wheels` | image-owned releases: `cli-extended`, `ciu`, `cmru`, `topos`, `nyxloom` |
| `AI CLI Tools` | agent CLI inventory: `aider`, `reasonix`, `openclaw`, `opencode`, `copilot`, `codex`, `claude`, `antigravity`, `claudelink`, `pi` |
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
shared curated environment (`/opt/mdt/venv`) for consistency across tools.

## Governing the container on the host

Placement into a cgroup tier is **create-time only** and cannot be expressed from inside an
image, so it lives in two places that must agree:

- **Container side** — `templates/devcontainer.json` ships
  `"--cgroup-parent=dev-interactive.slice"` in `runArgs`, plus `containerEnv` vars
  (`CGROUP_PARENT_DEV_INTERACTIVE`/`CGROUP_PARENT_DEV_BACKGROUND`/`CGROUP_PARENT_DEV_GATES`) any in-container tool
  reads instead of hardcoding a slice name. The host-side `initializeCommand`
  verifies all three units are loaded from installed files before Docker creates
  the devcontainer. A missing or runtime-generated unit stops startup because
  systemd otherwise accepts the name by creating a transient unlimited slice.
  The initializer reads the effective Docker option/value pairs in `runArgs`;
  an ambiguous or unsupported option stops startup instead of certifying a
  `--cgroup-parent` token used as another option's value.
  Install the host setup and verify it with `mdt-host-check.sh` before rebuilding.
- **Host side** — [`host-setup/`](host-setup/README.md) installs and maintains the tiers
  themselves: `dev-interactive.slice` for devcontainers, `dev-gates.slice` for gate/lane
  containers, and `dev-background.slice` for long-running stacks plus the Docker daemon-wide
  fallback via `/etc/docker/daemon.json`, all under one shared `dev.slice` IOPS/bandwidth
  ceiling and a health check. Run this part from the Docker host shell only;
  the installer, wizard, and baseline benchmark refuse a devcontainer because container
  root cannot modify the host's `/etc`, systemd, or cgroup tree.

The host wizard also asks whether to disable the kernel-wide `DAMON_STAT`
monitor. mdt defaults to disabling it (`DAMON_STAT_DISABLE_DEFAULT="yes"`), which lets
cgprofile use the DAMON sysfs interface to collect per-session samples; choosing no
preserves the kernel default. See the [host policy guide](host-setup/README.md#optional-damon_stat-policy).

The shared `dev.slice` IO ceiling is verified, not assumed: the reconcile reads
the kernel `io.max` back after every apply and fails the service on a mismatch,
accepts operator-pinned `DEV_IO_RBPS_MAX`/`DEV_IO_WBPS_MAX`/`DEV_IO_RIOPS_MAX`/
`DEV_IO_WIOPS_MAX` caps (recommended on a virtual disk, where a benchmark can
read the hypervisor's cache), warns `UNCAPPED SWAP`, and ships
`mdt-cgroup-io-limits.sh` to list what is really in force. Swap allowances are
yours to size (`DEV_SWAP_MAX`, `DEV_GATES_MEMORY_SWAP_MAX`). A kernel update
invalidates the measured baseline. Why: [DESIGN-GUIDE](docs/DESIGN-GUIDE.md#devslice-io-governance-must-be-verified-not-asserted);
how: [CONSUMERS](docs/CONSUMERS.md#dev-tier-io-governance-pin-verify-swap).

```bash
sudo host-setup/install.sh --with-baseline    # ~12 min of saturated disk — quiet window
sudo mdt-host-check.sh
```

On a host that already has `/run/cgprofile` configured, provision only
cgroup-profiler's read-only resource-observer directory with
`sudo host-setup/install.sh --observer-only`. It creates the dedicated
`cgprofile-observer` group and applies `/run/cgprofile/observer` permissions;
the host checker verifies the group is distinct from `docker`, the directory
is `root:<observer-gid> 0751`, and both required sysusers/tmpfiles rules are
installed as the complete active rule sets (blank lines and comments are
ignored; extra active directives fail the check):

```bash
sudo mdt-host-check.sh
```

see the [observer design](docs/DESIGN-GUIDE.md#narrow-cgprofile-observer-provisioning)
and [consumer steps](docs/CONSUMERS.md#cgprofile-resource-observer-host-setup).

For host inspection from an MDT devcontainer, `host-escape -- <command>` runs
the command in the host's namespaces with host-root authority; `host-escape`
opens a host shell. Review commands before running them. The helper runs
`mdt doctor` afterward and may remount cgroup2 to restore required flags.
See the [operator examples and safety details](docs/CONSUMERS.md#host-commands-from-an-mdt-devcontainer)
and the [host-escape design](docs/DESIGN-GUIDE.md#host-namespace-access).

The reason for checking the slice units before container creation is documented
in the [host cgroup preflight design](docs/DESIGN-GUIDE.md#host-cgroup-preflight).

[`host-setup/CGROUP-NOTES.md`](host-setup/CGROUP-NOTES.md) explains what a slice unit
fundamentally cannot express — and the BFQ caveats, where `IOWeight` does not mean what it
says. Read it before changing any weight.

All MDT devcontainer and release builds use the host-managed `mdt-managed`
Buildx remote backed by `mdt-buildkitd.service`; the template supplies
`BUILDX_BUILDER` and `BUILDKIT_HOST` explicitly. The accidental-worker guard
defaults to report-only; termination is an explicit opt-in. Per-container
leaf caps from the inotify watcher default off while parent slice limits
remain active. The host wizard asks whether that optional memory watcher is
enabled and requires explicit values for it. See the [memory policy rationale](docs/DESIGN-GUIDE.md#per-container-memory-policy)
and [pasteable host configuration](docs/CONSUMERS.md#per-container-memory-watcher).
Host installation and the guard policy are documented in
[the managed BuildKit architecture](docs/BUILD-ARCHITECTURE.md#managed-buildkit-backend).
The service exposes its socket only to the host `docker` group; see the
[socket-access design](docs/DESIGN-GUIDE.md#managed-buildkit-socket-access).
The host wizard also writes `DEV_BUILDKITD_MAX_PARALLELISM` into the managed
daemon configuration; it limits one BuildKit daemon's internal solver work and
is independent of release repack concurrency.
The `io.cost` coefficient generator is shared with `debian-install-v2` and
built from the pristine Linux source plus a checked patch series; see the
[generator design](docs/DESIGN-GUIDE.md#shared-iocost-generator) and
[host usage](docs/CONSUMERS.md#host-benchmark-file-targets). Its file target
must be dedicated benchmark scratch space because calibration overwrites the
file contents.

## Building and publishing

### Entry point

```bash
./build-push.py --build      # resolve env + build local OCI layouts (load flow)
./build-push.py --push       # publish saved layouts with digest verification (load flow)
./build-push.py --rebuild    # build then publish; bypasses the CMRU gate
```

Step and environment configuration is [`cmru.toml`](cmru.toml); the build
matrix is [`docker-bake.hcl`](docker-bake.hcl). Release builds require the
host-managed remote selected by `BUILDX_BUILDER=mdt-managed`; `cmru.toml` also
records its `BUILDKIT_HOST` endpoint, and the host slice owns its resource
limits. The configured CMRU release flow is `load`: build and manifest
extraction run first, then the gate and source promotion, then the post-gate
layout publication. The alternate `push` and `repack` flows publish during the
build and therefore do not provide source-first publication ordering.

### Local-dev builds

To test an image built from artifacts that are not (yet) released — a wheel built in a
worktree, a PWMCP bundle whose release URL 404s — ask for it explicitly:

```bash
cp local-dev-artifacts.toml.example local-dev-artifacts.toml     # git-ignored; list only what is local
./build-push.py --build --local-dev local-dev-artifacts.toml     # group 'all', or add --target NAME
```

The manifest names every local artifact (`name`, absolute `path`, `sha256`, `version`) for
the first-party wheels in `pip/wheels.list` and the PWMCP installer/bundle. It is a
**fail-closed opt-in, never a fallback**: an unknown, missing, unreadable or hash-mismatched
entry refuses the build and names the entry, and an artifact you do not list still comes from
its release. The result is loaded into the local Docker daemon as
`<debian>-py<python>[-<flavor>]-dev-<YYYYMMDD>[-<n>]` plus a floating `...-dev-latest`
(never a release tag, never bare `latest`), labelled `net.volkb79.mdt.build-mode=local-dev`
with every artifact recorded in `net.volkb79.mdt.local-artifacts` and the image manifest
(`Build Provenance`), and **can never be pushed** — see
[Local-dev builds in the design guide](docs/DESIGN-GUIDE.md#local-dev-builds-are-explicit-verified-and-unpublishable)
and the [worked example](docs/CONSUMERS.md#local-dev-build).

### Release caches

Release preparation is repeatable without repeatedly fetching unchanged tool archives.
It resolves live discovery inputs first, then stages a clean per-build directory from a
local artifact cache shared by all Git worktrees. Cache entries are keyed by source URL,
carry a SHA-256 record, and are re-hashed before reuse; each tool's existing upstream
checksum/digest validation still runs afterwards. Mutable discovery documents (`latest`
responses and platform manifests) are deliberately never reused.

The default artifact and BuildKit caches live under the repository's common Git directory,
not a disposable cmru release worktree. Override them only for a deliberate isolated cache:

```bash
MDT_ARTIFACT_CACHE_DIR=/var/tmp/mdt-artifacts ./build-push.py --build
MDT_BUILDKIT_CACHE_DIR=/var/tmp/mdt-buildkit ./build-push.py --build
```

The staging API is intentionally DRY: all tool download paths flow through the same
`_download()` cache, atomic-write, retry, and digest-record machinery; individual tool
adapters supply only their release URL(s), expected archive shape, and upstream integrity
rule. Timestamped `metadata.json` is kept out of the tool-install Docker cache boundary,
so an otherwise identical release can reuse the costly installation layers.

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
`DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN`; `cmru.toml` passes them into bake args, then
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

The shipped source-first `RELEASE_IMAGE_FLOW=load` path builds through the governed
BuildKit remote. After the CMRU gate and source promotion, `build-push.py --push`
copies the exact OCI layouts with `regctl` and verifies each remote digest with
`crane`; it does **not** call `skopeo`. `RELEASE_IMAGE_FLOW=push` is an optional
direct-push path that publishes through BuildKit during the build. CMRU supplies
the release identity and performs Docker login. Manual release commands must
export the same explicit `GITHUB_USERNAME`, `GITHUB_REPO`,
`GITHUB_OWNER_TYPE`, and `GITHUB_PUSH_PAT` inputs; workspace-local credential-file
fallbacks are not supported.

Built images carry `net.volkb79.base-devcontainers-release` and
`net.volkb79.base-devcontainers-version` labels:

```bash
docker image inspect <image> \
  --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-release" }}'
```

### Docker Hub login

Anonymous Docker Hub pulls are limited to 100 per hour per IP, and every session on this host shares
that IP: a build, a version resolve and a few `docker pull`s exhaust it (`429 Too Many Requests`). Log in
once with a read-only Docker Hub access token:

```bash
docker login docker.io --username <your-hub-user> --password-stdin <<< "$HUB_READ_TOKEN"
```

The login lives only in Docker's standard credential store (`~/.docker/config.json` in the persisted
home, or a credential helper). `docker`, `buildx` including the remote `mdt-managed` builder, `crane`,
`regctl` and, once its backlog item lands, `cmru versions` all read it from there. `mdt doctor` prints a
non-fatal notice when no `docker.io` entry exists (it checks the config's `auths`, `credHelpers` and
`credsStore` keys and, for a store, the helper's `list` verb (URLs only), and never reads a secret). Why one holder:
[Docker Hub login](docs/DESIGN-GUIDE.md#docker-hub-login-has-one-holder); how:
[CONSUMERS](docs/CONSUMERS.md#docker-hub-login).

### Compression and layer topology

BuildKit's generic registry exporter defaults to gzip. `cmru.toml` overrides that:
release publication uses OCI media types and **zstd level 3** while preserving the
original layer topology. Forced recompression is disabled so large, already-compressed
layers can pass through the exporter without an unnecessary second compression pass. Its
persistent BuildKit cache is owned by the managed remote and is reused without exporting a
second cache stream through the client API. The level is deliberately modest — cold
time-to-connect and governed export cost matter more than the last few compressed bytes.

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
repacker defect recorded in the architecture guide; use the default `load` lane rather than
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
| [host-setup/README.md](host-setup/README.md) | Installing the host cgroup tiers and narrow cgprofile observer setup |
| [host-setup/CGROUP-NOTES.md](host-setup/CGROUP-NOTES.md) | What slice units can't express; BFQ caveats |
| [docs/CONTAINER-DOCTRINE.md](docs/CONTAINER-DOCTRINE.md) | Which concerns belong to the image, the orchestration, and the host |
| [docs/CONSUMER-AI-GUIDANCE.md](docs/CONSUMER-AI-GUIDANCE.md) | What to put in a consumer repo's AI instruction files |
| [docs/AI-AGENT-TOOL-DISCOVERY.md](docs/AI-AGENT-TOOL-DISCOVERY.md) | Cross-CLI adapter pattern; why exact versions stay in the generated inventory |
| [docs/BUILD-ARCHITECTURE.md](docs/BUILD-ARCHITECTURE.md) | Build/repack/publication flow, cgroup boundaries, load attribution |
| [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) | Why the Python, tester-unified, and QEMU lanes are separate; managed socket access and host namespace access |
| [docs/CONSUMERS.md](docs/CONSUMERS.md) | Pasteable host governance, host escape, managed BuildKit, release adoption, the fresh-home template, and local-dev builds |
| [docs/HOME-PERSISTENCE.md](docs/HOME-PERSISTENCE.md) | The fresh-home design, the home path inventory, and moving from the original project |
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

## Testing

`run-gate` is the canonical test entrypoint — `run-gate --list`
discovers the declared lanes; definitions live in `run-gate.toml`.
See [`../run-gate-project/CONSUMERS.md`](../run-gate-project/CONSUMERS.md).

The smoke, release, and Assay lanes run in `tester-unified`, not in the cockpit
devcontainer. The `vm-system` command lane runs its QEMU controller on the
host, with product assertions inside the guest:

- `run-gate smoke` runs Python syntax checks, the host-setup renderer and
  the complete `scripts/` and `host-setup/tests/` pytest suites.
- `run-gate release` is the image-release gate for the Docker/OCI release
  flow tests; CMRU consumes this declaration instead of carrying a second
  `tester-gate` command.
- `run-gate assay-full` runs the wizard's assay lane at R0, R1, R2 and R3.
  R2 generates and runs the full native mutation campaign for the current
  source, and resumes from the git-ignored `.assay/` progress stream after an
  interrupted session.
- `run-gate image-runtime` is the **runtime acceptance of the built images**:
  it starts real containers from the exact native and VS Code image refs it is given
  (`MDT_RUNTIME_IMAGE_NATIVE=<ref> MDT_RUNTIME_IMAGE_VSC=<ref>`; no default, never a
  `:latest` fallback) and checks the empty home, the `mdt` group contract, every CLI at
  its location, one `PATH` in six shell forms, writes through the group under a
  re-numbered uid (and `EACCES` without it), the finalizer on a fresh home, silent
  interactive shells, every system-wide default with an empty home, and the provenance
  of a local-dev image (for which `MDT_RUNTIME_LOCAL_DEV_MANIFEST=<the manifest it was built
  from>` is required; release images do not need it). Its driver is stdlib `unittest` in `tests/runtime/`; every
  container is offline, in the gate tier, and removed by exact name.
- `run-gate oci-push` runs the OCI-layout build-once-and-push test and the bake-file evaluation test (a real `registry:2`,
  buildx export, `crane`/`regctl` digest verification) **inside the image the release just built**,
  because that image ships those tools and tester-unified does not. Nobody types a ref: the driver imports
  the OCI layout the `prepare` step left under `build/oci-layouts/` and FAILS if the release built no
  image. A missing tool or unreachable registry is a failure, not a skip. `cmru release` runs it after
  the `release` lane. See the [placement note](docs/DESIGN-GUIDE.md#the-oci-push-test-runs-where-its-tools-are).
- `run-gate vm-system` runs the MDT host setup acceptance in the shared
  QEMU/TCG harness. The bare-host lane only drives the runner; its assertions
  execute inside the guest and verify systemd PID 1, cgroup v2, Docker,
  rendered slice units, daemon configuration, and the installed host check.

`vm-system` preflights the caller-provided `CGROUP_PARENT_DEV_GATES`,
`CGROUP_PARENT_DEV_INTERACTIVE`, `BUILDX_BUILDER`, `BUILDX_CONFIG`, and
`DOCKER_HOST` before the controller starts. The gate declaration is the single
source for those command prerequisites. See the [test boundary design](docs/DESIGN-GUIDE.md#vm-system-prerequisites-live-in-run-gate).

The Dockerfile runs install-time checks and records version output for a subset
of tools; `smoke` and `release` do not start the final OCI image artifacts. That
boundary is covered by the `image-runtime` lane above (see
[MDT-010](KNOWN_ISSUES_TODO_BACKLOG.md) for what it does and does not cover and
[the design note](docs/DESIGN-GUIDE.md#built-image-acceptance-is-a-separate-test-boundary)).

`assay.toml` is the judgment contract for the wizard. The
shell installer and renderer remain command-tested because assay's Python
adapter cannot judge shell behavior. The source checkout's `assay/` package is
imported at lane runtime, as required for same-repository vbpub consumers; no
stale vendored assay copy is used. MDT has no HTTP/OpenAPI surface, so
Schemathesis is not applicable; Hypothesis covers pure configuration invariants
in the Python R0-R3 lane.

### CLI diagnostics

The shipped MDT Python parser scripts prefix help, usage, missing-argument,
unknown-argument, and configuration diagnostics at every parser depth with
`MDT <version> — mdt-cockpit` as line 1. The value is
read from the existing `MDT_VERSION` or `MDT_IMAGE_VERSION` runtime
environment; when neither is supplied the diagnostic says `unknown` rather
than inventing a release version.
The declared operator entrypoints also accept top-level `--version` and print
exactly one `MDT <version>` line on stdout with exit 0 and no stderr output.
Normal command output is unchanged. This
surface covers `build-push.py`, `check-mcr-devcontainer-tags.py`, the host
setup wizard, `mdt-io-baseline.py`, and `mdt_buildkit_builder.py`; service
watchers and build-time helper scripts remain internal interfaces.
