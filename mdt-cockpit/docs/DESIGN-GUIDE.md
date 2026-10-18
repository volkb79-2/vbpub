# MDT testing design

This document records why the MDT test surface is split between
`tester-unified` and the shared QEMU harness.

## Two environments, two claims

The ordinary Python lane runs in `tester-unified`. It owns parser behavior,
configuration validation, pure sizing invariants, mocked command boundaries,
coverage, mutation, and the canary. Assay runs that lane at its declared R0,
R1, R2, and R3 levels and keeps the resume and progress evidence under the
git-ignored `.assay/` directory.

The system lane runs the product assertions inside a disposable QEMU guest.
The guest owns PID 1, systemd, cgroup v2, Docker, service files, and the
kernel-visible configuration. The cockpit or gate container only drives the
VM. This boundary is required because a Docker container shares the host
kernel; a privileged container would still be able to affect host-wide
systemd, loop, swap, or device state.

The VM controller is itself governed by `CGROUP_PARENT_DEV_GATES`, probes the
interactive tier before starting, and builds through the declared
`BUILDX_BUILDER`. It maps a worktree source through Docker's authoritative
mount table and refuses an unmapped or missing source. This prevents a
container path from being passed to the daemon as if it were a host path.

## VM system prerequisites live in Run-Gate

The VM lane is a Run-Gate command lane. Its five host inputs are declared once
in `run-gate.toml`, so Run-Gate can refuse before starting the controller when
the environment is incomplete. The controller inherits the caller's selected
Docker endpoint and Buildx configuration. Wrapping this R0 orchestration
receipt in Assay would require a second environment allowlist to pass the same
inputs to the shell command without adding judgment beyond Run-Gate's own
record. Systemd, Docker, and cgroup assertions remain inside the QEMU guest;
the Python wizard lane supplies the deeper R1-R3 evidence for behavior that
can be reproduced safely in `tester-unified`.

MDT has no owned HTTP/OpenAPI contract. Hypothesis is used for pure
configuration invariants where generated values are meaningful; Schemathesis
would add no evidence to this product.

## PWMCP service in the image

The MDT Dockerfile installs PWMCP from the same committed CMRU `get.py` and
contract that its build inputs identify. The build helper records both source
URLs at the exact repository commit and their SHA-256 digests; the helper
rejects a mismatched download before running the installer. The installer
then fetches the versioned PWMCP bundle, verifies its checksum and wheel
manifest, and creates `/srv/pwmcp-instance` as a `vscode`-owned user-scope
install. PWMCP is a declared release dependency, so the matching bundle exists
before an MDT image build can consume it.

The instance is preinstalled but does not start during image build. A user runs
`ciu up` when browser service is needed. CIU's standalone root gives the
service its own internal network and attaches the current devcontainer; this
keeps the unauthenticated internal-network policy identical to PWMCP's other
internal deployments. No host port is published. The installed `pwmcp` CLI
uses the version-matched client and Playwright wheel in the same private venv.
See the [pasteable consumer steps](CONSUMERS.md#pwmcp-browser-service).

The image puts `/srv/pwmcp-instance/bin` on `PATH` ([One PATH order](#one-path-order)),
and the devcontainer template exports `PWMCP_MCP_URL`. The CLI lives outside
`/home/vscode`, so a persisted home mount cannot hide it, and `/srv` is group
`mdt` so the instance can be updated without `sudo`. The endpoint variable makes
the network resource explicit; each agent harness still owns its MCP registration
format.

## The home is user state; image assets live outside it

The devcontainer persists one host directory at `/home/vscode`
(`~/mdt-cockpit-home/`, mode `0700`). A list of individual home mounts made
state discoverability depend on knowing which tools wrote which dot
directories, and image-built files in unmounted parts of the home disappeared
on rebuild. A single home source makes persistence complete and visible in one
host path.

That mount hides every file baked below `/home/vscode`, so the image has a
strict ownership boundary:

- `/opt/mdt` contains image-owned Python environments, managed Python runtimes,
  npm CLIs, standalone CLI binaries and the Oh My Zsh distribution; `/srv`
  holds the PWMCP instance. Both are group `mdt` ([below](#the-mdt-group)).
- `/usr/local/share/mdt-cockpit` contains the shipped shell defaults,
  examples and manifests. `/etc` holds the defaults wired into each tool
  ([System-wide defaults](#system-wide-defaults)).
- `/home/vscode` contains only user state at runtime. `/tmp` remains separate
  scratch state (`~/mdt-cockpit-tmp/`, `1777`).
- `/run/mdt/host-ssh` is the readonly native-host SSH integration, outside the
  mounted home.

The build deletes what the base image left in the home and fails if any child
remains, so the invariant is "the image's home is empty", not "we remembered to
clean up". Because nothing the image ships needs to reach the home, **the
finalizer has almost nothing to do**: a new home stays empty, a second run
changes nothing, and the one stub it may write (a comment-only `~/.zshrc`,
because zsh's new-user wizard cannot be disabled system-wide) is only ever
created, never edited.

**Why a fresh home and no migration.** mdt-cockpit is an opt-in fork, not an
upgrade of the original image. A migrator has to decide, file by file, which of
the old home's contents are user state and which are the old image's payload
(venvs, CLIs, seeded defaults), and a wrong guess silently shadows the new
image. Copying by hand puts that decision with the one person who knows, and
removes a stopped-container tool, a marker file, and a startup refusal from the
critical path. The bootstrap keeps only an informational line so the
choice is never silent ([Moving from modern-debian-tools-python-debug](HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)).

Named volumes were not chosen as the default because operators need to see and
back up the complete user home as an ordinary host directory. Individual
mounts were not retained because each new tool would require another persistence
decision and could again leave user state behind.

## The `mdt` group

Image-managed trees must be updatable by the runtime user without `sudo` -
`npm install -g`, `pip install` into the primary venv, a tool's self-update -
but the user is not stable: Dev Containers' `updateRemoteUserUID` re-numbers
`vscode` (here uid 1003 instead of 1000) and re-owns **only** `$HOME`, so any
tree owned by the build-time `vscode` uid is simply not writable at runtime.
Ownership by a *group* survives that, because the step edits the user's uid and
primary gid but leaves membership of a separately numbered group alone.

- Group `mdt`, GID from the build argument `MDT_GID` (default **2750**). The GID
  is a *policy choice*, not derived from anything: a fixed, high number that is
  stable across rebuilds and unlikely to collide with a base image. The build
  **fails** if the base already uses that GID or the name. `vscode` is a member.
- `/opt/mdt` and `/srv` and everything beneath them: group `mdt`, directories
  `2775` (setgid, so new entries inherit the group), regular files group-writable.
- **Construction rule.** The roots are created `root:mdt 2775` *before* any
  content, and every RUN that writes beneath them runs with `umask 0002`, so new
  entries come out right the first time. A fix-up is allowed only on content
  created in the *same* RUN, and only through `mdt-perms fix`, whose predicates
  select offenders only. A recursive `chgrp`/`chmod` over earlier-layer content
  is forbidden: on overlayfs even a no-op metadata change copies the file up into
  a new layer, doubling the image (a test pins that no such command appears in
  the Dockerfile).
- **`/srv` is claimed late.** Debian's `base-files` postinst runs `install_directory srv 755 root`
  whenever apt (re)configures it, which silently re-modes `/srv` to `755` (the first real build
  hit this: the unprivileged PWMCP install was refused). `/srv` is therefore set to `root:mdt
  2775` after the last apt step, immediately before the PWMCP install, and the assertion below
  proves it survived. The same postinst re-modes it again if a root user upgrades `base-files`
  inside a running container; `mdt-perms fix /srv` repairs that.
- **`get.py` creates its instance directories `0755`.** The PWMCP installer's `_mkdir_secure`
  makes the instance root, `releases/`, `shared/` and `bin/` mode `0755`, so the build ends that
  RUN with `mdt-perms fix /srv`, which turns them into group-writable `mdt` directories. A
  `pwmcp update` run later at runtime has not been exercised by the lane.
- **Assertion.** The last build step is `mdt-perms check /opt/mdt /srv`: no entry
  (symlinks excluded) outside group `mdt`, no directory lacking setgid and group
  rwx, no regular file lacking group write. The image-runtime lane re-checks the
  same contract in a running container, so the build step and the shipped image
  cannot disagree.
- Not world-writable: the lane's negative control shows that a uid *outside*
  `mdt` gets `EACCES` on the same writes, which proves the group (not a loosened
  mode) grants access.

*Security note.* Group-writable image tools grant no new privilege: `vscode`
already has passwordless sudo in this image, so anyone who can write these trees
could already become root. The group exists for convenience and uid-stability,
not as a boundary. Runtime changes to these trees live in the container
filesystem and are replaced by the image on recreate.

## One PATH order

`PATH` is defined **once**, in the Dockerfile:

```text
/opt/mdt/bin : /opt/mdt/venv/bin : /opt/mdt/npm/bin : /srv/pwmcp-instance/bin : <base image PATH> : /home/vscode/.local/bin
```

Image tools always win; `~/.local/bin` is **last**, for the user's own additions
only. (The previous layout put it first so a user install could shadow an image
tool - and a stale copy of an old image CLI in the persisted home silently
did.) The base image's own entries (nvm, pipx, `/usr/local/bin`, ...) stay in the
middle, unchanged; the Microsoft base already appends `~/.local/bin` last for
interactive shells, which this order simply makes true for every shell.

Debian's `/etc/profile` *replaces* `PATH` in every login shell, so the Dockerfile
also records the finished value in `MDT_IMAGE_PATH`, and
`/etc/profile.d/50-mdt-cockpit.sh` puts exactly that back - the list is never
spelled a second time. `profile.sh`, which interactive shells also source, never
touches `PATH` (rewriting it would undo an activated venv). The devcontainer
template sets neither `PATH` nor `NPM_CONFIG_PREFIX`. The base image's own PATH
contains `/usr/local/bin` twice; it is carried over verbatim so every shell sees
the identical string, and the managed entries each appear once.

## Environment variables

Where each tool puts things is a fact of the image, so it is set once, here. This
is the result of auditing every tool the image installs for manager prefixes,
caches and config directories.

| Variable | Value | Why | Set in |
|---|---|---|---|
| `PATH` | the order above | image tools win, `~/.local/bin` last | Dockerfile `ENV` |
| `MDT_PATH_PREFIX`, `MDT_PATH_SUFFIX` | the two ends of `PATH` | the one spelling of the list | Dockerfile `ENV` |
| `MDT_IMAGE_PATH` | snapshot of the final `PATH` | login shells restore it after `/etc/profile` | Dockerfile `ENV` |
| `NPM_CONFIG_PREFIX` | `/opt/mdt/npm` | global npm installs/updates land in the group tree, not `~/.local` | Dockerfile `ENV` |
| `UV_PYTHON_INSTALL_DIR` | `/opt/mdt/python` | uv-managed interpreters are image-owned | Dockerfile `ENV` |
| `VIRTUAL_ENV` | **unset** | the primary venv is first on `PATH`; nothing claims shell activation | - |
| `ZSH` | `/opt/mdt/oh-my-zsh` | Oh My Zsh (VS Code family) is image-owned | `profile.sh`, when the directory exists |
| `ZSH_CUSTOM` | unset (OMZ default `$ZSH/custom`) | custom themes ship there; the tree is group-writable | - |
| `ZSH_CACHE_DIR` | `~/.cache/oh-my-zsh` | OMZ's cache is state, so it goes to the home | `zshrc` |
| `ZSH_COMPDUMP` | `$ZSH_CACHE_DIR/.zcompdump-...` | keeps the home root free of a stray dump file | `zshrc` |
| `ZSH_DISABLE_COMPFIX` | `true` | the group-writable tree would make `compaudit` ask about "insecure directories" on every start | `zshrc` |
| OMZ auto-update | disabled (`zstyle ':omz:update' mode disabled`) | the image owns the framework version | `zshrc` |
| `LESSOPEN` | `|/usr/local/share/mdt-cockpit/lesspipe.sh %s` | system-wide syntax-highlighting preprocessor | `shell.env` |
| `HTOPRC`, `LESSHISTFILE` | **unset** (were pointed into the home) | htop falls back to `/etc/htoprc`; less keeps its own history | - |
| `XDG_CONFIG_DIRS` | unset (default `/etc/xdg`) | Neovim finds the system NvChad tree there | - |
| `EDITOR`, `VISUAL`, `PAGER`, `LESS`, `LESSCHARSET` | `nvim`/`nano`, `less`, `-R -M`, `utf-8` | defaults a user file may override | `shell.env` |
| `MDT_CUSTOMIZATION_ROOT` | `/usr/local/share/mdt-cockpit` | where the image's shell files live | `profile.sh` |
| `PIPX_HOME`, `PIPX_BIN_DIR`, `NVM_DIR`, `NVM_SYMLINK_CURRENT` | base image values (VS Code family) | the Microsoft base's own group-owned trees (`pipx`, `nvm`) - same pattern as `mdt` | inherited |
| `LD_PRELOAD` | the KSM shim | build-time opt-in | Dockerfile `ENV` |
| `CODEX_SQLITE_HOME`, `BUILDX_CONFIG` | under `/home/vscode` | tool **state**, so deliberately in the home | template `containerEnv` |
| `DOCKER_HOST`, `BUILDX_BUILDER`, `BUILDKIT_HOST`, `CGROUP_PARENT_DEV_*` | host sockets and slices | facts of the host, not the image | template `containerEnv` |

Everything else (`~/.aws`, `~/.docker`, `~/.config/gh`, pip/uv/npm caches, ...)
is user state and is deliberately left at each tool's own home default.

## System-wide defaults

Every default the image ships is installed where its tool reads *system-wide*
configuration, not copied into the home (which would shadow later image updates
and make "what is mine" unanswerable). Each lookup below was verified in the
built image (`strace` of the real tool), and the image-runtime lane asserts it
with an empty home.

| Tool | System location | Home write? | Why / lookup order |
|---|---|---|---|
| bash (interactive) | hook appended to `/etc/bash.bashrc` -> `/usr/local/share/mdt-cockpit/bashrc` | none | `~/.bashrc` runs *after* it and wins |
| bash (login) | `/etc/profile.d/50-mdt-cockpit.sh` | none | restores `PATH`, then `profile.sh` |
| zsh | hook appended to `/etc/zsh/zshrc` -> `.../zshrc` | **stub**: comment-only `~/.zshrc` | zsh runs `zsh-newuser-install` on a terminal when none of `.zshenv/.zprofile/.zshrc/.zlogin` exists, and there is no system-wide switch; the stub is a no-op because the defaults arrive via `/etc/zsh/zshrc` (so image updates still reach the user) |
| Oh My Zsh | `/opt/mdt/oh-my-zsh`, loaded by the zsh hook | none (cache in `~/.cache/oh-my-zsh`) | skipped when the user's own `~/.zshrc` already loads it |
| nano | `include` appended to `/etc/nanorc` | none | `/etc/nanorc`, then `~/.nanorc`/`~/.config/nano/nanorc` (per-setting override) |
| htop | `/etc/htoprc` | none (htop writes its own file when you save settings) | `~/.config/htop/htoprc`, `~/.htoprc`, then `/etc/htoprc` |
| mc | `/etc/mc/mc.ini` | none | `~/.config/mc/ini`, then `/etc/mc/mc.ini`, then `/usr/share/mc/mc.ini` |
| Neovim / NvChad | `/etc/xdg/nvim/` (tree + `sysinit.vim`) | none (plugins in `~/.local/share/nvim`, lazy lockfile redirected to `~/.local/state/nvim`) | `sysinit.vim` starts the tree only while the user has no `init.lua`/`init.vim`, so creating one replaces the default entirely |
| less | `LESSOPEN` in `shell.env` | none | environment variable |
| MDT shell files | `/usr/local/share/mdt-cockpit/{shell.env,aliases.sh}` | none | the user's `~/.config/mdt-cockpit/` files are sourced *after* and may override; `ai.env` has no image default |
| lnav formats | `/etc/lnav/formats/` | none | already system-wide |
| image manifest | `/usr/local/share/mdt-cockpit/manifest.md` | none (no `~/mdt-manifest.md`) | `IMAGE_MANIFEST` in `/etc/os-release` |

Neovim note: the shipped `NVCHAD_VER` archive is the `NvChad/NvChad` core
repository, whose `init.lua` is a placeholder that prints a hint, not a
ready-to-run starter configuration. The move keeps that behaviour as it was
(tracked as MDT-007); the lockfile redirection in `sysinit.vim` takes effect as
soon as a starter-style `configs/lazy` module is present.

## OCI names keep runtime paths stable

The fork publishes the native and VSC image families as `mdt-cockpit` and
`mdt-cockpit-devcontainer`, and the runtime namespace now matches: the image's
support files live in `/usr/local/share/mdt-cockpit` and the user's overrides in
`~/.config/mdt-cockpit`. The original project's paths
(`modern-debian-tools-python-debug`) are not carried over. That is possible
exactly because a fork user starts with an empty home and copies old content by
hand ([Moving from modern-debian-tools-python-debug](HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug));
there is no persisted file that depends on the old name, so keeping it would
only leave two products sharing one path convention.

## Local-dev builds are explicit, verified and unpublishable

Testing an image sometimes needs artifacts that are not published yet - a wheel
built in a worktree, a PWMCP bundle whose release URL does not exist. The
default build must never quietly substitute for a missing release (a *default
that stands in for a fact that exists elsewhere* is the estate's recurring
hazard), so local-dev is a **separate, explicit mode**:
`./build-push.py --build --local-dev local-dev-artifacts.toml`.

- **Fail-closed manifest.** The manifest lists each local artifact (`name`,
  absolute `path`, `sha256`, `version`). Before any network or build work every
  entry is checked: the name must be known (a typo does not fall back to the
  release), the file present and readable, the hash equal, the wheel's file name
  consistent with its version, and a PWMCP entry equal to the pinned release. The
  first failure refuses the build and names the entry. The resolver re-verifies
  the same manifest, and the in-image PWMCP installer re-hashes what it was given.
- **Nothing local reaches a layer by accident.** Local wheels and the PWMCP files
  travel through the bake named context `mdt-local-dev` (`build/local-dev/`; an
  empty `FROM scratch` stage in a release build), mounted into the RUNs that need
  them. They are **never** written into the shared, untracked `pip/wheels/`: a
  local wheel left there would be installed by the next *release* build and
  labelled `release`. That directory holds only what the resolver reports in
  `pip/wheels/RESOLVED.json` for the current build, and the Dockerfile's wheel
  step (`scripts/check_wheel_set.py`) requires equality with that report (a
  leftover file is an error, not something to install), takes local-dev wheels
  only from the context in local-dev mode, refuses any in release mode, requires
  the union to be exactly the distributions `pip/wheels.list` declares, and
  records the installed wheels with their sha256 in
  `/usr/local/share/mdt-cockpit/first-party-wheels.json`. Staging copies a file,
  hashes the *copy* against the manifest, then sets modes `0644`/`0755`, so a
  file rebuilt after verification or an umask-077 checkout cannot slip through. `get.py` is the unchanged
  pinned installer: only its release-asset download is replaced, so its checksum,
  manifest-vs-tag and wheel verification still run.
- **Recorded.** `net.volkb79.mdt.build-mode=local-dev` and
  `net.volkb79.mdt.local-artifacts` (JSON of name, version, sha256, original
  path) are image labels, and the image manifest has a `Build Provenance` section.
  Release images carry `build-mode=release`.
- **Tagged apart.** `<debian>-py<python>[-<flavor>]-dev-<YYYYMMDD>[-<n>]` plus the
  floating `...-dev-latest`. Release tags, `...-latest` and bare `latest` are
  dropped by construction, not by convention.
- **Unpublishable, three ways.** The image is only *loaded* into the local daemon
  (no registry or OCI-layout export exists for this mode); `release-bake.sh` forces
  the local-dev flow whenever `MDT_BUILD_MODE=local-dev` (cmru re-applies the
  project's `RELEASE_IMAGE_FLOW=load` over the environment, so the mode - not the
  flow variable - is authoritative) and has no push step for it; and the publish
  path refuses any layout whose build-mode label is not `release` (fail-closed:
  unreadable labels refuse too). The mode keeps its own state file so a local-dev
  build can neither clobber nor be mistaken for the saved release state.

## How the AI CLIs update themselves (measured, not designed)

Measured 2026-10-18 on the VS Code image with network, as a re-numbered uid (4242, member of
`mdt`) and an empty home volume; each tool's own update command was run once, then the same volume
was mounted in a second container. Every tool was already at its latest release, so a *download*
was never observed - only what each command writes and where it points. The design did not change
because of this table.

| tool | installed at | update command | what it wrote | works as `vscode` via `mdt` | `command -v` after |
|---|---|---|---|---|---|
| claude | `/opt/mdt/bin/claude` (copy) | `claude update` | `~/.local/share/claude`, `~/.local/bin/claude` (symlink), `~/.claude.json`, `~/.claude/backups/` | nothing to download; it records a *native* install in the home, so a real update would land in `~/.local/share/claude` | `/opt/mdt/bin/claude` (image copy shadows the home one: `~/.local/bin` is last) |
| codex | `/opt/mdt/npm` | `codex update` (runs `npm install -g @openai/codex`) | `/opt/mdt/npm/lib` (48 files), `/opt/mdt/npm/bin`, `~/.npm` cache | yes | `/opt/mdt/npm/bin/codex` |
| copilot | `/opt/mdt/npm` | `copilot update` | `~/.cache/copilot/pkg`, `~/.local/state/gh`, `~/.copilot/logs` | self-update downloads into the home cache, not `/opt/mdt` | `/opt/mdt/npm/bin/copilot` |
| opencode | `/opt/mdt/npm` | `opencode upgrade` | `~/.local/share/opencode` | prompts "installed ... may be managed by a package manager - Install anyways?" and waits (blocks a non-tty run); `-m npm` skips the prompt and would use `npm -g` | `/opt/mdt/npm/bin/opencode` |
| pi | `/opt/mdt/npm` | `pi update self` | with `--force`: `/opt/mdt/npm/lib` (15033 files, `npm --prefix /opt/mdt/npm install -g`), `~/.npm` | yes (forced reinstall, 36 s) | `/opt/mdt/npm/bin/pi` |
| reasonix | `/opt/mdt/npm` | `reasonix upgrade` | `~/.reasonix/locks/` only | `--force` refuses: "installed through a package manager ... npm install -g reasonix@latest" | `/opt/mdt/npm/bin/reasonix` |
| openclaw | `/opt/mdt/npm` | `openclaw update --yes --no-restart` | `~/.openclaw/update-reports/`, `~/.openclaw/state/openclaw.sqlite`, `~/.openclaw.update-captures/`, `~/.npm` | update mode `npm` (global prefix `/opt/mdt/npm`); already current | `/opt/mdt/npm/bin/openclaw` |
| claudelink | `/opt/mdt/npm` | none (no update subcommand, no `--version`); `npm install -g claudelink@latest` | `/opt/mdt/npm/lib` (3854 files, 131 packages), `~/.npm` | yes | `/opt/mdt/npm/bin/claudelink` |
| antigravity / agy | `/opt/mdt/bin` (two copies) | `antigravity update` / `agy update` | `~/.gemini/antigravity-cli/updater` (2 files) | "already on the latest version"; the target of a real update is unmeasured | `/opt/mdt/bin/antigravity`, `/opt/mdt/bin/agy` |
| aider | `/opt/mdt/venv` | `aider --upgrade` (runs `pip install --upgrade aider-chat`) | `/opt/mdt/venv/lib` (1426 files), `~/.cache/pip`, `~/.aider/analytics.json` | yes; the version stayed `0.86.3.dev53+g5dc9490bb` | `/opt/mdt/venv/bin/aider` |

Survival across a container recreate follows from where the write landed: everything under `/opt/mdt`
and `/srv` is the container's writable layer, so a recreate restores the image's version of that tool;
everything under the home is in the named volume and persists (the second container saw the same
`command -v` and versions). Two consequences worth knowing: a tool that records a *home* install
(claude, copilot) is shadowed by the image copy on `PATH` until the image is rebuilt, and the
npm-based tools update in place for the life of the container only.

## Built-image acceptance is a separate test boundary

Source tests in `tester-unified` cannot certify the image filesystem or its
runtime environment, and the Dockerfile's own install-time probes only run
version commands for some tools. The **`image-runtime`** lane (`run-gate.toml`,
driver `tests/runtime/`, MDT-006) is the acceptance stage that runs the exact
built artifacts.

- **`bare-host`, like `vm-system`.** The driver only orchestrates; the subject runs
  in its own containers, so the tests do not depend on what the tester image
  happens to contain. Every container is started with `--cgroup-parent
  "$CGROUP_PARENT_DEV_GATES"`, `--init`, `--network none` (the lane is offline) and
  a unique name `mdt-cockpit-rt-<pid>-<n>`, and removed **by exact name** in a
  `finally` (never by filter or ancestor: lane and gate containers share images).
- **No default image.** `MDT_RUNTIME_IMAGE_NATIVE` and `MDT_RUNTIME_IMAGE_VSC` are
  `required_env`; an unset variable is a refusal, never a `:latest` tag. The lane
  prints each image's ID and `build-mode` label at start so a run is bound to the
  artifact it tested.
- **Oracles are behavioural.** Expected CLI locations are derived from
  `ai-cli-tools.list` and `pip/wheels.list`, the `PATH` is compared across six
  shell forms, the group contract is re-implemented independently of
  `mdt-perms`, and the remapped-uid scenario derives a throw-away image with the
  same step the Dev Containers CLI applies (`tests/runtime/updateUID.Dockerfile`,
  mirrored from devcontainers/cli v0.89.0) and installs a locally packed npm
  tarball and a locally built wheel, with a negative control for a uid outside
  `mdt`. Waits are synchronous (`docker run` returns when its process exits); the
  only timeouts are generous failsafes, never a verdict.
- **Each scenario has been seen RED** against a deliberately broken image (a file
  left in the home, a reordered `PATH`, a file lacking group-write, a user outside
  the group, a world-writable shortcut, a missing system default, a finalizer that
  adds/clobbers/is not idempotent, a wrong provenance label, a recursive-chmod
  layer); the controlled breaks are recorded with the change that introduced the lane.

What it does **not** cover: anything needing the network (updates, first-run plugin
bootstraps), GUI/IDE behaviour, and the host-side bootstrap (unit-tested separately).

## The estate venv is selected by PATH

The MDT image puts `/opt/mdt/venv/bin` right after `/opt/mdt/bin` in the one
image-defined `PATH` ([One PATH order](#one-path-order)), for image and login
shells alike. This selects the estate Python for all cockpit processes, including
noninteractive agent commands and children started by release tools. It does
not pretend that every process has activated a shell venv: `VIRTUAL_ENV` is
reserved for actual activation, while the selected interpreter and its
installed packages are determined by `PATH`.

## DAMON_STAT is an explicit host choice

`DAMON_STAT` and cgprofile's generic DAMON sysfs sessions are mutually
exclusive kernel consumers. MDT asks the host operator whether to disable
`DAMON_STAT`, recommends disabling it for the development host, and applies
the choice through a boot-time tmpfiles rule. The non-wizard example keeps the
kernel default. This keeps a host-wide monitoring policy out of cgprofile's
runtime and gives the operator a clear way to preserve `DAMON_STAT` when it is
needed for other workloads.

## Managed BuildKit socket access

The rootless BuildKit process creates its Unix socket with its container
uid/gid, which may be translated by Docker `userns-remap` and need not match
the host `docker` group. A setgid parent alone does not repair that: a live
rootless BuildKit probe created the socket as `1000:1000`. Host setup waits for
the socket, makes its root-owned parent non-writable, rechecks that the
endpoint is a non-symlink socket, then compares socket owner and daemon uid
inside the same container namespace. Only then does it change the socket to
host group `docker`, mode `0660`. Locking the parent before the privileged
`chgrp`/`chmod` closes a path-replacement race; the next start temporarily
restores directory mode `01777` for socket creation. This shares the BuildKit
API only with principals already trusted for Docker access; it does not make
the socket world-accessible.

## Bootstrap uses the source filesystem type

Devcontainer bind-mount syntax declares a source and target but has no
source-kind field. Names and suffixes do not identify a missing source's type:
`.gitconfig` is usually a file, while `.ssh` is a directory, and custom dotfiles
can be either. The host bootstrap checks existing host sources by their actual
filesystem type, regardless of name or suffix. For missing sources, the
`DEVCONTAINER_MISSING_BIND_SOURCE_POLICY` setting controls whether MDT creates
the path or refuses it. The shipped default, `create-by-spelling`, interprets a
trailing `/` as a directory and no trailing `/` as an empty regular file. The
`fail` option requires every `$HOME` source managed by the bootstrap to exist
before Docker starts. The marker is an MDT bootstrap convention, not Docker
mount metadata; the shipped template marks every directory source with `/`. See the
[consumer example](CONSUMERS.md#optional-git-config-mount).

The template puts Codex's shared SQLite database under the persistent home's `.codex` with
`CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared`, so profile data survives a rebuild.
It sets `shutdownAction: "none"` so closing the attached Dev Containers session leaves the
container available. Adoption details are in the
[consumer guide](CONSUMERS.md#codex-profile-state-and-container-lifetime).

## Host cgroup preflight

Docker accepts an unknown `--cgroup-parent` by creating a transient, unlimited
systemd slice. A check performed from a container launched under that parent is
too late: the check has already caused the unsafe placement it is supposed to
prevent. The vendored `initializeCommand` runs on the Docker host before the
devcontainer starts, so it verifies `dev-interactive.slice`,
`dev-background.slice`, and `dev-gates.slice` are each loaded from an installed
unit file. Missing, transient, malformed, or unqueryable units stop startup.

This check is part of the host setup contract, not a fallback policy. The host
installer creates and verifies the unit files; the bootstrap confirms the
running systemd manager loaded them before `runArgs` can use the interactive
slice. Gate launchers separately verify the gates slice before launching a lane.
The initializer parses Docker's option/value boundaries in `runArgs` before
matching the effective `--cgroup-parent` with the interactive tier. If an
earlier value-taking option consumes a token that looks like a cgroup parent,
or an option cannot be classified, startup refuses instead of accepting a
decoy.
See the [consumer setup](CONSUMERS.md#host-cgroup-preflight) for the required
install and rebuild steps.

## BuildKit guard policy

The guard defaults to `report-only` because a reserved Buildx worker may be
legitimate during a development build or image rebuild. Automatically
removing it can abort work during export, which Docker reports as an RPC
unexpected EOF. The report mode leaves the worker available and records the
identity mismatch. Operators who intentionally want the guard to remove
unapproved workers can select `terminate`; the wizard requires a second
confirmation because this action stops a live container.
An existing `terminate` value is not confirmation for a later plain install:
the installer stops and disables the guard and requires the wizard to confirm
termination again.

## Per-container memory policy

Parent slices already bound the combined tier, while callers such as CIU may
set their own container memory limit or `MemoryMin` floor. An unconditional
1G leaf cap can stop an otherwise valid workload and may conflict with that
floor. The inotify watcher therefore ships with both per-container memory
values set to `-` for each tier. Operators can configure a leaf cap where a
workload needs one, then explicitly confirm enabling it in the wizard; the
wizard keeps it disabled when that confirmation is declined. Any configured
cap must remain compatible with the other memory properties on that scope.
`WATCHER_MEMORY_POLICY=disabled` is the shipped setting and requires all six
leaf values to be `-`; `WATCHER_MEMORY_POLICY=enabled` requires at least one
configured value. The installer rejects mismatched policy/value combinations
instead of ignoring configured values or starting an empty watcher. During
upgrades, if an older host config has leaf values but no policy key, the wizard
carries those values forward for review and confirmation.

## Shared io.cost generator

MDT and `debian-install-v2` use one generated copy of the Linux kernel's
`iocost_coef_gen.py`. The pristine vendor file stays byte-for-byte intact;
explicit patches carry the local LVM, file-target, no-COW, and partition
sysfs behavior. The generator records hashes for its source and patch series,
and `tools/build-iocost-generator.py --check` verifies the checked-in runtime
artifact. This keeps calibration behavior aligned without copying one
consumer's local fork over another. MDT always uses a persistent file target
on the Docker-data filesystem; automated scratch-partition setup remains a
separate reviewed change.

The file target is explicitly disposable benchmark scratch. Calibration
rewrites its contents, including when an existing exact-size file is reused.
The generator refuses to replace a symlink, non-regular file, multiply linked
file, or file with a different size, so changing a target's shape requires an
explicit operator action instead of silently unlinking the prior path.

## Host namespace access

An MDT devcontainer cannot inspect or change the host's cgroup tree through
its private cgroup namespace. `host-escape` provides one documented route for
host diagnostics: it starts a privileged helper and enters the selected host
process's mount, UTS, network, IPC, and cgroup namespaces. This keeps operators
from improvising several subtly different escape commands, but it is not a
sandbox: every command runs with host-root authority. Since that helper can
touch the host cgroup mount namespace, it runs `mdt doctor` on exit to restore
required cgroup2 flags if needed; `mdt doctor --check-only` provides a
read-only check.

## Release relationship

The `release` run-gate lane contains the image release-flow tests and is the
single declaration consumed by `cmru.toml`. The image build and push remain
CMRU steps, while the acceptance checks stay in `tester-unified`; this keeps
the release transaction from carrying a second hand-written test recipe.

See the [README testing section](../README.md#testing) for the user-facing
lane list and [CONSUMERS.md](CONSUMERS.md) for commands and environment
requirements.
