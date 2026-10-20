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
  `2775` (setgid, so new entries inherit the group), regular files group-readable and group-writable (`g+rw`: a `0600` file would otherwise become `0620`, unreadable to a remapped uid that is not its owner).
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
  rwx, no regular file lacking group read or write. The shipped script pins the group `mdt` (no environment override) and refuses to run where that group does not exist - `find -group nosuch` fails, which used to read as "no violations". The image-runtime lane re-checks the
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

Login shells need two things, both done at build time:

1. **`/etc/profile` keeps an inherited `PATH`.** Stock Debian `/etc/profile` assigns `PATH`
   unconditionally (root and non-root branch), which throws away every runtime addition - a
   consumer `containerEnv.PATH`, a Dev Containers feature, the `userEnvProbe` login shell that feeds
   VS Code - before any `/etc/profile.d` script runs. `scripts/patch_etc_profile.py` turns exactly
   those two assignments into `PATH="${PATH:-<the same Debian default>}"` (the approach of the Dev
   Containers common-utils feature); a cleared environment still gets the Debian default. The build
   **fails** unless `/etc/profile` contains the stock block exactly once - another distribution, a
   changed base or an already patched file is never patched blindly. The Microsoft
   `00-restore-env.sh` (VS Code family) prepends its own fixed list to `$PATH` after `/etc/profile`
   and is unaffected, but with an inherited `PATH` it prepends on *every* login shell. Measured on the VS Code
   image before the hook de-duplicated: 16 entries in the image `PATH`, 21 in a login shell, 26 in a login shell
   inside it, 31 in a third (VS Code terminals are a login shell inside the `userEnvProbe` login environment, so
   users would see this). The hook's first-occurrence de-duplication makes every nesting level identical.
   `/etc/profile` is a **base-files conffile**. dpkg never overwrites a locally modified conffile
   silently: a non-interactive upgrade keeps the local (patched) file, an *interactive* `apt upgrade`
   of `base-files` in a running container asks whether to keep it or take the maintainer's version, and
   taking the maintainer's version restores the unconditional assignments (runtime additions are then
   dropped by login shells again until the patch is re-applied or the image is rebuilt). The same drift
   re-moded `/srv` once, so the build ends with an assertion that the two patched lines are still in
   `/etc/profile` (and the lane re-checks it in the running image).
2. **The hook merges.** The build writes the managed prefix and suffix (not a whole `PATH`) to
   `/usr/local/share/mdt-cockpit/path-entries`. `path-hook.sh` removes those entries wherever they
   occur in the current `PATH`, prepends the prefix in the order above, appends the suffix, and keeps
   every other entry in its relative order **and drops repeats, keeping the first occurrence of each** (a
   later duplicate can never win a command lookup, so resolution is unchanged); sourcing it twice changes nothing. A cleared environment
   yields prefix + the default + suffix: `su -l`, `sudo -i`, ssh and cron get a `PATH` from the login
   machinery (`ENV_PATH`), and a bare `env -i bash -l` gets *bash's own* compiled-in default
   (`/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:.`, trailing `.` included) because
   bash defines `PATH` before `/etc/profile` runs, so `${PATH:-...}` never sees an empty value. bash and sh reach it through
   `/etc/profile.d/50-mdt-cockpit.sh`; **zsh does not read `/etc/profile` on Debian**
   (`/etc/zsh/zprofile` is only comments), so the hook is also sourced from `/etc/zsh/zprofile`.
   If the entries file is missing the hook prints one line to stderr and leaves `PATH` as the login
   shell set it; it never skips silently.

`profile.sh`, which interactive shells also source, never touches `PATH` (rewriting it would undo an
activated venv). The devcontainer template sets neither `PATH` nor `NPM_CONFIG_PREFIX`; a consumer
adds an entry with `containerEnv.PATH` (see CONSUMERS.md) and it survives login shells. The base
image's own PATH contains `/usr/local/bin` twice; non-login shells carry it over verbatim, login shells
show it once (repeats are dropped), and the managed entries each appear once. The lane asserts the same *managed shape* in every shell and the identical
full `PATH` among shells that inherit the same environment.

## Environment variables

Where each tool puts things is a fact of the image, so it is set once, here. This
is the result of auditing every tool the image installs for manager prefixes,
caches and config directories.

| Variable | Value | Why | Set in |
|---|---|---|---|
| `PATH` | the order above | image tools win, `~/.local/bin` last | Dockerfile `ENV` |
| `MDT_PATH_PREFIX`, `MDT_PATH_SUFFIX` | the two ends of `PATH` | the one spelling of the list | Dockerfile `ENV` |
| (file) `/usr/local/share/mdt-cockpit/path-entries` | the managed `PATH` prefix and suffix, written at build time | the login-shell hook merges them into whatever `PATH` the shell has - also a cleared one | Dockerfile `RUN` |
| `NPM_CONFIG_PREFIX` | `/opt/mdt/npm` | global npm installs/updates land in the group tree, not `~/.local` | Dockerfile `ENV` |
| `UV_PYTHON_INSTALL_DIR` | `/opt/mdt/python` | uv-managed interpreters are image-owned | Dockerfile `ENV` |
| `VIRTUAL_ENV` | **unset** | the primary venv is first on `PATH`; nothing claims shell activation | - |
| `ZSH` | `/opt/mdt/oh-my-zsh` | Oh My Zsh (VS Code family) is image-owned | `profile.sh`, when the directory exists |
| `ZSH_CUSTOM` | unset (OMZ default `$ZSH/custom`) | custom themes ship there; the tree is group-writable | - |
| `ZSH_CACHE_DIR` | `~/.cache/oh-my-zsh` | OMZ's cache is state, so it goes to the home | `zshrc` |
| `ZSH_COMPDUMP` | `$ZSH_CACHE_DIR/.zcompdump-...` | moves *Oh My Zsh's* completion dump out of the home root. It does not keep the root free: the Microsoft base image's `/etc/zsh/zshrc` sources nvm's `bash_completion`, whose bare `compinit` writes `~/.zcompdump` (measured; zsh offers no variable for that path other than relocating `ZDOTDIR`, which would move every zsh dotfile) | `zshrc` |
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
| bash (login) | `/etc/profile.d/50-mdt-cockpit.sh` | none | merges the managed `PATH` entries, then `profile.sh` |
| zsh | hook appended to `/etc/zsh/zshrc` -> `.../zshrc` | **stub**: comment-only `~/.zshrc` | zsh runs `zsh-newuser-install` on a terminal when none of `.zshenv/.zprofile/.zshrc/.zlogin` exists, and there is no system-wide switch; the stub is a no-op because the defaults arrive via `/etc/zsh/zshrc` (so image updates still reach the user) |
| Oh My Zsh | `/opt/mdt/oh-my-zsh`, loaded by the zsh hook | none (cache in `~/.cache/oh-my-zsh`) | skipped when the user's own `~/.zshrc` already loads it |
| nano | `include` appended to `/etc/nanorc` | none | `/etc/nanorc`, then `~/.nanorc`/`~/.config/nano/nanorc` (per-setting override) |
| htop | `/etc/htoprc` | none (htop writes its own file when you save settings) | `~/.config/htop/htoprc`, `~/.htoprc`, then `/etc/htoprc` |
| mc | `/etc/mc/mc.ini` | none | `~/.config/mc/ini`, then `/etc/mc/mc.ini`, then `/usr/share/mc/mc.ini` |
| Neovim / NvChad | `/etc/xdg/nvim/` (tree + `sysinit.vim`) | none (a starter-style tree would keep its plugins in `~/.local/share/nvim`) | Neovim itself starts `$XDG_CONFIG_DIRS/nvim/init.lua` while the user has no `init.lua`/`init.vim`, so creating one replaces the default; `sysinit.vim` only redirects a `configs.lazy` lockfile into `~/.local/state/nvim` (proven with a fixture tree, not with the shipped one - MDT-011) |
| less | `LESSOPEN` in `shell.env` | none | environment variable |
| MDT shell files | `/usr/local/share/mdt-cockpit/{shell.env,aliases.sh}` | none | the user's `~/.config/mdt-cockpit/` files are sourced *after* and may override; `ai.env` has no image default |
| lnav formats | `/etc/lnav/formats/` | none | already system-wide |
| image manifest | `/usr/local/share/mdt-cockpit/manifest.md` | none (no `~/mdt-manifest.md`) | `IMAGE_MANIFEST` in `/etc/os-release` |

Neovim note: the shipped `NVCHAD_VER` archive is the `NvChad/NvChad` core
repository, whose `init.lua` is a placeholder that prints one hint line
(`use this along starter!!!`) on every start, not a ready-to-run starter
configuration. The move keeps that behaviour as it was (tracked as MDT-011; the
lane pins "exit 0, exactly that one stderr line, tree run once" so the day it is
fixed the test must be tightened). What the lane proves for Neovim is therefore
the wiring - the tree is started exactly once, a user `init.lua` replaces it, and
a fixture tree with a `configs/lazy` module gets its lockfile redirected - not a
working NvChad. Neovim's own fallback to `$XDG_CONFIG_DIRS/nvim/init.lua` starts the
tree; an earlier `sysinit.vim` also `dofile`d it and so ran it twice per start, which
the fixture's run counter now catches.

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

## How the AI CLIs update themselves

**Policy.** Everything under `/opt/mdt` and `/srv` is group `mdt`, so the runtime user updates
the image's tools in place without `sudo`. Those updates live in the **container's writable
layer**: they last for the life of the container, a recreate restores the image's version, and
**rebuilding the image is the durable way to change a version.** **Version policy for the
user-updatable tools (claude, codex, reasonix, opencode, pi):** the user can update them in place
anyway, so they follow upstream - no freshness window - and the image pins and records what it
shipped. Staging resolves the registry `latest` (or an explicit pin) to an EXACT version, writes it to
`tool-versions.env`, and the image manifest lists it. The installers of all five accept only that staged,
exact version (never empty, never `latest`; a build ARG cannot stand in for it), an explicit pin of any
of the npm ones must be a published release,
and with `INSTALL_CLAUDE_CODE=false` the registry is not consulted. The tools that are *not*
user-updatable in this sense follow the estate's 14-day rule, enforced by `cmru versions` rather than by
a resolver of our own: copilot, openclaw and claudelink (npm targets in `cmru.toml`, which needs a
`package.json` beside it in cmru 6.1.0) install the newest release at least 14 days old, and staging reads
the committed record offline (`cmru versions check` queries every target of the project, including
rate-limited OCI registries, so a build would fail on an unrelated 429). Environment pins are refused, not
silently ignored; cmru's reasoned `version` + `reason` override is the one supported pin. The OCI targets of
the project are literal patch tags (a rolling family tag younger than the window makes `resolve` refuse the
whole project; known issue, `handoff/CMRU-GAPS-2026-10-19.md`).

**The OCI records are advisory: not consumed by the build (MDT-013).** The base image is still chosen by
`scripts/resolve-devcontainers-release.py` (a floating `mcr.microsoft.com/devcontainers/python` tag passed as
`BASE_IMAGE`), so a record with a digest and a `released_at` is NOT a 14-day certification of the cockpit's
base; it only keeps `cmru versions resolve` working and shows what a vetted base would be. Each target in
`cmru.toml` carries an `ADVISORY` comment, and `scripts/test_ai_cli_versions.py` fails if any build input
(Dockerfile, bake, `build-push.py`, the staging scripts) names a record or its digest while the label stands.
MDT-013 makes the build take the devcontainers record as the `FROM` digest, with an equality test like
`tester-base/tests/test_pins.py`; the label, these paragraphs and that test change together then.

**An override released inside the window waives it for that tool's dependencies.** The pinned release is
younger than the cutoff, so `--before` has to be its release date to stay installable; its dependencies are
then NOT 14-day vetted. This is stated, not hidden: staging records `<TOOL>_WAIVED`, and the manifest row
reads "dependency window waived by override: <reason>". An override of a release older than the cutoff
keeps the normal cutoff and is not waived. aider's hand-written `deps_before` is refused by staging when it
is in the future or newer than the staging time minus `age_window_days` (read from
`cmru.orchestration.toml`, never defaulted).

**Transitive dependencies obey the window too.** `npm install -g pkg@ver` pins only the top-level version
(and ignores `package-lock.json`), so the three npm installs also pass `--before <cutoff>`: the record's own
`age_cutoff` (resolved_at minus the window - cmru's definition of "old enough", so the whole tree is judged
by the rule that judged the package; for a reasoned override younger than the cutoff, its release date, so
the pinned release itself stays installable). Measured: npm 11.20.0 in the image honours `--before` with `-g`
(a date before the release gives `notarget`). `package-lock.json` is therefore produced by `cmru versions
resolve` (it is how cmru derives the constraints) and never consumed by the image build. aider's PyPI
dependencies are held to `deps_before` in `aider-main-pin.toml` with `pip install --uploaded-prior-to`
(pip 26.2 in the venv). The cutoffs, the aider commit and any override reason are written to
`tool-versions.env` and listed in the image manifest ("Dependency age cutoffs"). Staging also cross-checks
every record (resolved version equals its source's, release not after the cutoff, an override equals the
resolved value and has a reason), so a hand-edited record or an override changed without `cmru versions
resolve` fails.

**aider is a documented exception.** Its newest vetted release (0.86.2) declares `Requires-Python <3.13`
and cannot be installed into this image's Python 3.14 venv (pip: no matching distribution, also with
`--ignore-requires-python`); 3.14 support exists only on upstream `main`. So `aider-main-pin.toml` pins an
exact COMMIT of `main` and the version that commit builds; the installer refuses an unpinned `main`, and
fails when the built version differs from the record. Both values reach `tool-versions.env`, the build
metadata and the image manifest. **Exit condition: until an aider release supports Python 3.14** - then the
file is deleted and `pypi.aider-chat` becomes a normal vetted target. (The `rich` pin conflict between
aider-chat and the toolkit targets, which made `cmru versions resolve` unsatisfiable, disappeared with it.)
The release tools keep their current resolution.

**Claude Code is an npm-global install.** The image installs the official
`@anthropic-ai/claude-code` package into `/opt/mdt/npm`. For such an install `claude update`
(and the background updater) run `npm install -g` into that prefix, so the updated binary is
the one `command -v claude` resolves to in every shell. The previous layout copied a native
binary into `/opt/mdt/bin`; `claude update` then wrote a *native* install into
`~/.local/share/claude` and `~/.local/bin/claude`, and the image copy shadowed it on `PATH`, so
updates silently never took effect. Measured 2026-10-18 as the remapped uid 4242 (member of
`mdt`, no sudo, empty home), starting from 2.1.286: `claude update` reported "Installation
method set to: global", updated to 2.1.302, and wrote only `/opt/mdt/npm/lib/node_modules`,
`/opt/mdt/npm/bin/claude`, the npm cache and logs under `~/.npm`, and Claude's own state
(`~/.claude.json`, `~/.claude/`); nothing under `~/.local`. The documented alternative, the
native installer, has no install-location setting. The background updater is the same npm path
and was not exercised separately.

Measured 2026-10-18 on the VS Code image with network, same uid and an empty home volume, each
tool's own update command run once. Unless noted a tool was already at its latest release, so a
*download* was not observed - only what the command writes and where it points.

| tool | installed at | update command | what it wrote | works as `vscode` via `mdt` | `command -v` after |
|---|---|---|---|---|---|
| claude | `/opt/mdt/npm` (`@anthropic-ai/claude-code`) | `claude update` | **updated 2.1.286 to 2.1.302:** `/opt/mdt/npm/lib/node_modules` (11 files), `/opt/mdt/npm/bin/claude`, `~/.npm`, `~/.claude.json`, `~/.claude/` | yes | `/opt/mdt/npm/bin/claude` (the only `claude` on `PATH`) |
| codex | `/opt/mdt/npm` | `codex update` (runs `npm install -g @openai/codex`) | `/opt/mdt/npm/lib` (48 files), `/opt/mdt/npm/bin`, `~/.npm` cache | yes | `/opt/mdt/npm/bin/codex` |
| copilot | `/opt/mdt/npm` | `copilot update` | `~/.cache/copilot/pkg`, `~/.local/state/gh`, `~/.copilot/logs` | its self-update downloads into the home cache, not `/opt/mdt` | `/opt/mdt/npm/bin/copilot` |
| opencode | `/opt/mdt/npm` (platform package `opencode-linux-x64-baseline`) | `npm install -g opencode-linux-x64-baseline@latest` (use the `opencode-linux-*` package `opencode_platform_package()` selects for your architecture) | `/opt/mdt/npm/lib` | yes (1.19.3 to 1.19.2 and back, measured). **Not** `opencode upgrade`: it cannot tell how it was installed, asks "Install anyways?" and waits for a terminal; `opencode upgrade -m npm` installs the `opencode-ai` meta-package and failed with "Upgrade failed for npm (exit code 1)" | `/opt/mdt/npm/bin/opencode` |
| pi | `/opt/mdt/npm` | `pi update self` | with `--force`: `/opt/mdt/npm/lib` (15033 files, `npm --prefix /opt/mdt/npm install -g`), `~/.npm` | yes (forced reinstall, 36 s) | `/opt/mdt/npm/bin/pi` |
| reasonix | `/opt/mdt/npm` | `npm install -g reasonix@latest` (`reasonix upgrade` only checks: with `--force` it refuses, "installed through a package manager ... npm install -g reasonix@latest") | `~/.reasonix/locks/` from the check | yes | `/opt/mdt/npm/bin/reasonix` |
| openclaw | `/opt/mdt/npm` | `openclaw update --yes --no-restart` | `~/.openclaw/update-reports/`, `~/.openclaw/state/openclaw.sqlite`, `~/.openclaw.update-captures/`, `~/.npm` | update mode `npm` (global prefix `/opt/mdt/npm`); already current | `/opt/mdt/npm/bin/openclaw` |
| claudelink | `/opt/mdt/npm` | none (no update subcommand, no `--version`); `npm install -g claudelink@latest` | `/opt/mdt/npm/lib` (3854 files, 131 packages), `~/.npm` | yes | `/opt/mdt/npm/bin/claudelink` |
| antigravity / agy | `/opt/mdt/bin` (two copies) | `antigravity update` / `agy update` | `~/.gemini/antigravity-cli/updater` (2 files) | "already on the latest version"; the target of a real update is unmeasured | `/opt/mdt/bin/antigravity`, `/opt/mdt/bin/agy` |
| aider | `/opt/mdt/venv` | `aider --upgrade` (runs `pip install --upgrade aider-chat`) | `/opt/mdt/venv/lib` (1426 files), `~/.cache/pip`, `~/.aider/analytics.json` | yes; the version stayed `0.86.3.dev53+g5dc9490bb` | `/opt/mdt/venv/bin/aider` |

Everything above that wrote under `/opt/mdt` is gone after a container recreate (the image's version
returns); everything written under the home (Claude's own state, caches) is in the named volume and
persists.

## Docker Hub login has one holder

Docker's standard credential store (`~/.docker/config.json` in the persisted home, or a credential helper)
is the single holder of the Docker Hub login. Every client that talks to Docker Hub already knows how to read
it: `docker`, `buildx` (the remote `mdt-managed` builder is driven through the client's credentials), `crane`
and `regctl`; cmru is meant to join them (gap recorded in `handoff/CMRU-GAPS-2026-10-19.md`). `mdt doctor`
only reports whether an entry exists, by the config's `auths`/`credHelpers`/`credsStore` keys (for a `credsStore`: the helper's `list` verb, URLs only, offline-tolerant - an absent helper reads "cannot confirm"), and never
reads or prints a secret; the notice is non-fatal because anonymous pulls are slow-failing, not impossible.
Credential helpers differ: `pass` and `secretservice` may prompt (a GPG passphrase, a keyring unlock) or block
on `list` until the doctor's 10 s timeout, in which case the result is "cannot confirm", never a failure. A
`credsStore` name outside `[A-Za-z0-9_.-]{1,64}` is reported as "invalid credsStore name" without echoing the
config value, and is never executed.
Rejected alternatives: **cmru-only env vars** (`username_env`/`token_env`) - a second copy of the secret
that only one tool reads; **an mdt env file** - a plaintext secret in the persisted home next to the real
store; **ciu secrets** - the login is a property of the host user's Docker client, not of any stack.

## The oci-push test runs where its tools are

`scripts/test_oci_layout_push.py` exercises the active `RELEASE_IMAGE_FLOW=load` path against a
real registry, and so needs docker, buildx, `crane` and `regctl`. tester-unified (built on
`tester-base`) carries none of buildx/crane/regctl by design, and a test that silently skips
there certifies nothing. It therefore lives in its own lane, `oci-push`, which starts the exact
image the release just built from a bare-host driver (`scripts/run_oci_push_lane.py`): gate tier,
`--init`, uniquely named and removed by exact name, the host docker socket mounted, the tracked
sources streamed in on stdin (no devcontainer path is bind-mounted). `MDT_OCI_PUSH_REQUIRED=1` turns
every missing precondition into a failure; outside the lane (a developer's shell) the same test still
skips when a tool is missing.

**The image is derived, not typed (D-006).** In the shipped `RELEASE_IMAGE_FLOW=load` the `prepare`
step leaves one OCI layout per bake target under `build/oci-layouts/` (the directory `build-push.py`
resolves). The driver exports the first with `regctl image export --name <private tag> | docker load`
(this daemon cannot load an OCI layout directly), runs the lane in it, and removes that tag by exact
name. With no layout the lane fails with "no image was built in this transaction": it never falls
back to another image, and the `required_env` of the lane is only the gate tier.

**The layout is bound to the build, and the target is explicit.** `build-push.py` writes
`build/oci-layouts/transaction.json` after a successful load-flow build: the build date and timestamp
(the same values saved in `.build-env.json`), the commit it ran at and each layout's top index digest.
The lane names its target (`--target trixie-py314-php85`, no default) and accepts the layout only when
all of these hold: the record names the target; the layout's digest equals the recorded one; no layout
sits beside it unrecorded (ambiguous); `.build-env.json` carries the same date and timestamp; and the
build commit is an ancestor of `HEAD` with nothing but the declared `commit_generated` paths changed
since. A stale, edited or ambiguous layout fails the lane. The container and the private tag are
removed by exact name even when `docker load` fails partway.

## Built-image acceptance is a separate test boundary

Source tests in `tester-unified` cannot certify the image filesystem or its
runtime environment, and the Dockerfile's own install-time probes only run
version commands for some tools. The **`image-runtime`** lane (`run-gate.toml`,
driver `tests/runtime/`, MDT-010) is the acceptance stage that runs the exact
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
`DAMON_STAT`. mdt defaults to disabling it (operator decision 2026-10-20: the cgroup-profiler
daemon needs the DAMON sysfs interface), `DAMON_STAT_DISABLE_DEFAULT="yes"` also in the
non-wizard example, and applies the choice through a boot-time tmpfiles rule. This keeps a host-wide monitoring policy out of cgprofile's
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

## Narrow cgprofile observer provisioning

The resource observer uses a read-only Unix socket whose group is deliberately
separate from `docker`. A normal MDT host install creates its group and runtime
directory as part of the complete host policy workflow. Contract-2 adoption on
an already configured host also needs a small additive path that prepares the
socket directory without re-running unrelated host policy, Docker, or BuildKit
steps. `install.sh --observer-only` provides that path: it verifies the
existing `/run/cgprofile` control directory and refuses a pre-existing
observer group that aliases Docker's GID or an observer path with the wrong
type, owner, group, or mode before it writes units or applies tmpfiles. It
refuses drift rather than letting tmpfiles silently re-own a live path. It
creates the observer group, applies only the observer tmpfiles rule, and
validates the resulting owner, group, and mode before returning.

The dedicated tmpfiles rule is separate from the control-socket rule so the
narrow path cannot rewrite the Docker-accessible parent directory. The full
installer uses the same provisioning helper, keeping both host workflows on
one implementation. `mdt-host-check.sh` verifies the live observer group,
directory metadata, and the complete active sysusers/tmpfiles rule sets. It
ignores blank lines and comments but rejects extra active directives, so a
missing or widened rule is reported before a consumer lane tries to mount the socket. See the
[pasteable consumer command](CONSUMERS.md#cgprofile-resource-observer-host-setup).

## BuildKit guard policy

The guard defaults to `report-only` because a reserved Buildx worker may be
legitimate during a development build or image rebuild. Automatically
removing it can abort work during export, which Docker reports as an RPC
unexpected EOF. The report mode leaves the worker available and records the
identity mismatch. Operators who intentionally want the guard to remove
unapproved workers can select `terminate`; the wizard requires a second
confirmation because this action stops a live container.
An existing `terminate` value is not itself a confirmation: the wizard's second
confirmation is recorded durably (`/var/lib/mdt/guard-terminate.confirmed`: policy, BuildKit image,
timestamp, digest of policy + image + machine-id). The record is an audit trail bound to what was
confirmed and where, not an access control (root can write it); the guard re-checks it before it
terminates anything and falls back to report-only. Limit, stated honestly: a hand-edit away from
`terminate` and back to byte-identical settings with no install and no guard restart in between
leaves the host in exactly the confirmed state, and cannot be told apart from never having left it. A plain re-install with an unchanged recorded
confirmation proceeds and keeps the running guard; a new or changed `terminate` refuses
before any mutation (MDT-HS-02: the earlier design disabled and stopped the running,
previously confirmed guard and only then refused, removing live protection). The
installer is split into pre-flight (reads the validated candidate, may refuse, mutates
nothing) and a COMMIT POINT: validate everything first, then act.

### The guard reads `Actor.ID`, and one bad event does not restart it (MDT-HS-03)

From 2026-10-02 the host guard crash-looped (restart counter 107+): `docker events` on Docker 29.9.1
(API 1.56) no longer emits the deprecated top-level `id`/`status`/`from`, only `Actor.ID`, and the
guard's `event.get("id") or event.get("ID")` raised on every container create. The parser now takes
`Actor.ID`; a legacy `id` is accepted only when `Actor` is absent or agrees (a disagreement is refused,
never guessed); `Type` other than `container` is ignored, because the command's `type=container`
filter guarantees container events and a stray event has no container to police. A malformed line is
logged, counted and answered by an immediate `reconcile_all()`: a restart (`RestartSec=5`) is a window in
which created containers are not seen, so a single bad line must not cost one. Only
`MAX_CONSECUTIVE_MALFORMED_EVENTS` (5) in a row exit non-zero, because that means the event shape is not
understood and systemd should show it. A good event resets the streak. Review follow-ups: a rolling window (20 within 60 s) also exits, so bad/good interleaving cannot hide a broken parser; the immediate sweep runs at most once per rescan interval so an interleaved flood cannot storm the Docker API; a container vanishing between listing and inspect (ephemeral `--rm`) is skipped only on Docker's explicit `No such container`/`No such object`, so a daemon outage still fails loudly; event ids must be full 64-hex and the sweep uses `ps -aq --no-trunc` so a container named like another's short id cannot shadow it.

### IO caps are given to systemd as the whole disk's `/dev/<DEVNAME>`, reset-then-set (MDT-HS-04)

**Measured on gstammtisch** (systemd 257.13, kernel 7.2.6, root on a device-mapper LV `dm-0` 253:0
over `vda` 254:0): `systemctl set-property --runtime dev.slice "IOReadBandwidthMax=/dev/mapper/<lv> N"`
left the kernel's `dev.slice/io.max` empty with nothing logged, while `systemctl show` reported the value;
the same call with `/dev/vda` or `/dev/block/254:0` wrote `254:0 rbps=N` at once. For `dev-gates.slice`
the mapper spelling did produce 254:0 entries (see the corrected diagnosis below). With two spellings of one disk in a runtime list (`/dev/vda N` from a manual pin plus
`/dev/mapper/... M` from the reconcile) the kernel ended with the value of the LAST entry.

**Decision.** The reconcile, the per-container watcher and the installer's rendered `dev.slice` static
lines all hand systemd `/dev/<DEVNAME>` of the whole disk (`/dev/vda` on the measured host). It is derived
from the SAME MAJ:MIN `_mdt_dev_majmin` yields for the read-back, then named by `DEVNAME=` in
`/sys/dev/block/<maj:min>/uevent` (`_mdt_io_systemd_dev`, single-sourced); a missing `DEVNAME` is refused,
never invented, and no device name is hard-coded. A mapper or partition node is never passed. Every IO
`set-property` resets each key and then sets it in ONE call (`IOReadBandwidthMax=` then
`IOReadBandwidthMax=/dev/vda N`), so a stale entry under another spelling cannot shadow the cap; the reset
and the set must travel together because an empty assignment alone wipes the unit-file value (MDT-016). Log
lines name the path actually given to systemd (`io.max on /dev/vda (from /dev/mapper/<lv>)`). Rejected:
keeping the configured node and only adding the reset (does not address the stale-entry mechanism below, and the mapper spelling was not itself at fault);
`/dev/block/<maj:min>` (first version of this fix, measured to apply, but a rendered MAJ:MIN is not stable:
dynamic majors such as virtio 254 and device-mapper 253 can swap across kernel or initramfs changes, so the
boot-time static line could name another device, and it relies on udev's `/dev/block` symlinks, which are not
there before udev runs). The kernel name is a devtmpfs node that exists early and follows the disk. Install
messages distinguish "no device discovered" from "device found but its whole disk could not be resolved"
(the static lines are then omitted and the reconcile reports ERROR each run).

**Corrected diagnosis (2026-10-20 06:25-06:35 UTC, systemd 257.13; inferred from these measurements plus the
v257 source `src/core/cgroup.c`, not directly observed inside systemd).** The first version of this section
blamed the `/dev/mapper/<lv>` spelling and called the cause unexplained. Later measurements correct that.
After the IO matrix (its scripts used `IOxxx=` clears and then `/dev/vda` sets) systemd stopped putting values
into the kernel: a changed value via `/dev/vda` on dev.slice left `io.max` empty, a changed dev-gates value
never reached the kernel, and a reconcile run on the mapper path EMPTIED a directly written dev.slice
`io.max`, although `systemd-analyze dump` showed io realized. After a plain `systemctl daemon-reload` the
reconcile's own mapper path applied and verified at once (254:0 83886080/41943040/800/600, exit 0). So the
mapper path itself works. The best-supported model:

- systemd keeps ONE in-memory io-device-limit entry PER PATH STRING.
- An empty assignment resets that limit to max in every entry but does NOT delete the entries.
- `systemctl show` and the runtime drop-in omit all-max entries, so a stale one is invisible.
- `cgroup_apply_io_device_limit()` writes every entry as a complete `MAJ:MIN rbps= wbps= riops= wiops=` line,
  and several spellings resolve to the same 254:0, so the last one applied wins. A stale all-max entry for
  another spelling therefore silently writes `max`, which removes the cap.
- `daemon-reload` rebuilds the list from the drop-in files, which hold only the non-max entries, and clears it.

`lookup_block_device()` parses `/dev/block/M:N` directly and otherwise stats the path and follows
`block_get_originating()` and `block_get_whole_disk()`, so any of the spellings resolves; the earlier
`dev-gates` observation is explained by the same stale-entry mechanism.

**Consequence: single spelling and reset-then-set are necessary but do NOT heal an upgrade.** On the upgrade
from the mapper spelling to `/dev/vda`, the reset turns the old mapper entry into a stale all-max entry that
can win. So `_mdt_io_queue_flush` (with `_mdt_io_queue_add`) self-heals ONCE per run: when the read-back shows the intended disk missing
or at the wrong values while `systemctl show` reports the intended ones, it logs a WARN naming the reason, runs
one `systemctl daemon-reload`, re-applies and re-verifies; a second mismatch fails. **Reload semantics (MDT-HS-06):** the unit of
work is the BATCH, not the unit. On the live host (2026-10-20 10:00 UTC) the spelling migration left stale entries on several units;
the heal reloaded for `dev.slice`, then `dev-gates.slice` failed as "reload suppressed ... did not fix it" and the install exited 1 with
`dev-gates.slice` uncapped. Inferred [I]: a reload rebuilds systemd's lists but does not re-push the kernel state of units applied
earlier in the same run, and a unit reset AFTER the reload still carries its stale entries. So `_mdt_io_queue_add` applies every managed unit
(dev.slice and each capped tier), `_mdt_io_queue_flush` verifies ALL of them, and only then, if any disagrees while systemd reports its
intended values, reloads ONCE (the persisted limiter across runs is kept), re-applies EVERY unit and re-verifies ALL; a mismatch that
survives that single reload plus re-apply is the failure, with a remedy naming what was tried. A single run never fails a unit because of
its own reload. The heal is triggered only
when `systemctl show` reports ALL the intended values. The limiter is persistent, because the timer starts a
fresh process every minute and a per-process flag would reload PID 1 every minute forever when the heal does not
help: a stamp file `/run/mdt/io-selfheal-reload.stamp` holds `<epoch> <reason>`, and no further reload happens
within `MDT_IO_RELOAD_WINDOW_SECONDS` (1800, a documented constant, not a knob). A run that verifies clean
without needing a reload clears the stamp. Refusals say what actually happened: "reload suppressed: last
self-heal reload at T did not fix it", "the stale-entry heal was tried and did not fix it", or "systemd does not
report all the intended values either, no reload was attempted"; the latter two list the other causes to check
(io controller not enabled, wrong device, kernel refusal) instead of telling the operator to reload again. `install.sh` already ends with a `daemon-reload`; it does not delete
stale runtime IO drop-ins, so the install relies on this self-heal on the first reconcile instead of duplicating
its own cleanup (removing `/run/systemd/system.control` files was measured to depend on systemd's naming and
to race its writes; see the MDT-016 note in the reconcile script).

## Writeback and root-queue settings are host-setup keys, not a runbook (MDT-HS-05)

**Why.** On a slow shared disk, fsync and journal commits wait behind the dirty backlog and the device queue. The
measured fix on gstammtisch (2026-10-20, `scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md` sections 4.2 A/B, 4.3 and 4.4
matrices, recommendations in section 5): `vm.dirty_bytes` 16 MiB, `vm.dirty_background_bytes` 4 MiB, root disk
`nr_requests` 16, `max_sectors_kb` 64 took the game thread's D-state maximum from 26.9 s to 0.14 s, the probe fsync maximum
from 39 s to 0.5 s and the gate fio from 5 to 173 iops under the same load. These were applied by hand and by interim files; the operator
asked for them to be permanent and for the wizard to ask. They are now four env keys that install.sh renders, applies and verifies.

**Grammar and the one validator.** A key is a positive integer (bytes; KB for `max_sectors_kb`) or `kernel` (an explicit
policy default: leave the kernel's value untouched, render no line); empty means `kernel`. The grammar and the
`dirty_background < dirty` relation live in ONE place, the Python validator in `mdt-host-setup-wizard.py`, which both the wizard
prompts and `install.sh --validate-config` use; the disk-dependent bound `max_sectors_kb <= max_hw_sectors_kb` is checked by the
same module (`--check-max-sectors-kb`), with the live value read from the derived disk in the installer's read-only preflight.
The keys are runtime-optional (`runtime-optional-keys.re`): an absent line means `kernel`, which cannot leave anything
unbounded, so an old env file is not refused; it gets the usual NOTICE.

**What is rendered, and the systemd/udev ordering.** `90-mdt-io-writeback.conf` (sysctl.d) holds only the set keys.
`61-mdt-io-queue.rules` matches `KERNEL=="<DEVNAME>"` of the whole disk under `/`, derived (`_mdt_root_disk_node`, which reuses
the MDT-HS-04 helpers; never typed). It sorts after `60-mdt-bfq-scheduler.rules` because a scheduler change resets `nr_requests`.
The interim files sort BEFORE the final ones (`-interim` before `.`), so both coexist harmlessly until the interim files are retired;
sysctl.d likewise lets the later file win. install.sh applies them after the BFQ block (`sysctl -p`, udev reload, trigger with
`--sysname-match`, settle), reads the live values back and fails non-zero on any mismatch or unreadable value, naming the file and the
remedy; the rest of the install still completes. An interim file is retired only when MDT's own file carries EVERY key the interim file sets (parsed from it, never
assumed) and each is verified live; otherwise it is kept with a WARN naming the missing keys and the remedy, because with every key
at `kernel` the interim file is the only thing persisting the live values (the state of gstammtisch when this shipped), and
retiring it would silently lose the tuning at the next reboot (review H-46).

**Strict values, derived disk, verified identity.** Values are positive integers without leading zeros. `vm.dirty_bytes` must be
at least two pages: kernel `Documentation/admin-guide/sysctl/vm.rst` (v6.12) says a lower value "will be ignored and the old
configuration will be retained", i.e. a silent no-op. `max_sectors_kb` must be at least one page (`blk_validate_limits`, v6.12, rejects a
smaller user value), at most `max_hw_sectors_kb`, and a multiple of the logical block size: the same function does
`round_down(min(max_hw, user), logical_block_size >> 9)`, so an unaligned value would read back lower and fail the read-back; it is refused up
front. The wizard derives the root disk with `_mdt_root_disk_node`, like install.sh and check.sh, not from `IO_DEV_PATH` (the
Docker-data device may be another disk). A benchmark result is defaulted only when the device number and at least one more IOB-02
identity field (model, serial, size, wwn: read from sysfs, present on both sides) are equal; a device-number-only match is shown as
"not verified for this disk" and not defaulted. `mdt-host-check.sh` separates "live only, not persisted" from OK and notices a later
sysctl.d, `/etc/sysctl.conf` or udev rule that would override MDT's value at boot (later file wins).

**Read-cost trade-off.** `max_sectors_kb` also caps read requests: sequential reads fell from about 1.2-1.4 GB/s to about
0.25-0.3 GB/s at 64 KB (buffered reads were equal across 64-256 KB). That is why the wizard prints the cost next to the key and
proposes `kernel` unless a measurement says otherwise. `vm.dirty_bytes` is host-global and also bounds every production container's
writeback (section 4.5). A setting returned to `kernel` removes MDT's line but cannot restore the kernel's boot value at runtime;
install.sh says so in a NOTICE and never guesses the old value.

**Where proposals come from.** Precedence per key: a current benchmark result, then the existing configured value, then `kernel`;
the gstammtisch values are shown as a labelled example only. The benchmark reader is coupled to the documented IOB-02 `result.json`
schema (schema 1, `run.status`, `quality.plausible`, `identity.disk.devno`, `proposals.writeback`) and nothing else: absent means no
benchmark; a corrupt, newer-schema, aborted, implausible or other-disk result is reported and not used; a formula-only, limited or
target-missed proposal is shown with that label. Full identity validation belongs to `iobench check` once it exists; the reader only
compares the disk's device number.

## io.cost is the weight mechanism and host-setup owns the scheduler (MDT-HS-07)

**Why io.cost over BFQ.** Measured on gstammtisch (2026-10-20, `IO-STALL-TESTING-GUIDE.md` section 11, same load as sections 6 and 10).
C0 = bfq, C1 = mq-deadline, C2 = mq-deadline + io.cost. Game D-state total / max: 1.15 s / 96 ms (C0), 0.61 s / 75 ms (C1), 0 / 0 (C2).
Probe fdatasync p99: 205 ms, 491 ms, 290 ms. Gate-like fio: 208, 174, 199 iops. mq-deadline alone is worse than bfq for sync writes
(the kernel also enables the writeback throttle, wbt, 75 ms, when leaving bfq); io.cost with a measured model and QoS removes the game's IO
waits entirely at no gate cost, and enabling io.cost turns wbt off. **io.latency is not available**: `CONFIG_BLK_CGROUP_IOLATENCY` is not set in
Debian's 7.2.6 kernel, while `CONFIG_BLK_CGROUP_IOCOST=y`. io.cost does not address the zswap shrinker burst (that writeback is charged to
the game's own cgroup). Operator decision 2026-10-20: stop using bfq, switch to io.cost.

**The keys.** `IO_ROOT_SCHEDULER` (mq-deadline | none | bfq | kernel, checked against the root disk's `queue/scheduler` list, which the
wizard reads and never assumes), `IO_COST_MODEL` (`kernel` = `ctrl=auto`, the kernel's rotational/ssd heuristic, or the six linear
coefficients) and `IO_COST_QOS` (`off` = `enable=0`, or `rpct rlat wpct wlat min max`). Bounds are the kernel's, from `block/blk-iocost.c`
`ioc_qos_write` (Linux v6.12): percentiles at most 100.00 with 2 decimals (refused above), latencies unsigned microseconds, `min`/`max`
2-decimal percentages the kernel silently CLAMPS to 1.00..10000.00 (so the validator refuses outside, because a clamp would read back
different) and refuses when `min > max`. The model source is the IOB-02 result's `proposals.iocost_model` (rule R6), used only when the
result is identity-verified by the MDT-HS-05 rules (device number plus at least one of model/serial/size/wwn); an all-zero placeholder is
"not measured", never a model; the QoS has no benchmark source, so its proposal is the existing value or the explicit policy default `off`.
Empty or absent = not managed, deliberately: an old env file must not silently change the scheduler or switch io.cost on or off.

**Scheduler and ordering.** `60-mdt-io-scheduler.rules` (`KERNEL=="<DEVNAME>"` of the derived whole disk) replaces
`60-mdt-bfq-scheduler.rules`, which is retired. Order: scheduler (60), then the queue rule (61, unchanged), then io.cost. A scheduler change
resets `nr_requests`, so install.sh verifies the scheduler once more after the queue settings and the read-back covers all of them. The
module file `mdt-bfq.conf` is installed only for `bfq`. A non-MDT `/etc/udev/rules.d/60-bfq-scheduler.rules` (origin:
`scripts/gstammtisch-guide/files/etc/udev/rules.d/60-bfq-scheduler.rules`) is retired only when byte-identical (sha256) to a known repo copy,
with a NOTICE; any other rule that assigns the scheduler to the disk is kept with a WARN, and one that sorts after MDT's is a refusal to report
the scheduler as verified.

**Loadable schedulers.** `queue/scheduler` lists only the schedulers that are LOADED, so `mq-deadline` can be absent from the list although
its module exists. The choice is accepted when the disk lists it OR it is available as a module for the running kernel (a file under
`/lib/modules/$(uname -r)/kernel/block/` or an entry in `modules.builtin`); it is refused only when neither applies, and the post-apply read-back is
the proof that it really became active.

**What reverts what, and the reconcile.** A disk remove/re-add resets the scheduler, `nr_requests`, `max_sectors_kb` and the per-disk io.cost
state; a scheduler switch resets `nr_requests` (and toggles the writeback throttle); a udev change event re-applies the scheduler and queue RULES
(60, 61) but not io.cost, which has no rule; a reboot is covered by the rules plus `mdt-io-cost.service`. Between those events the minute-by-minute
reconcile (`_mdt_reassert_io_settings`) compares the live scheduler, the queue values and the io.cost model and QoS with the env, with the same
field-normalised comparison, and re-applies on drift with a `WARN: drift:` log line (the same udev trigger and `_mdt_apply_iocost` install uses). A
failure to re-apply, or a mismatch after it, is an ERROR and a non-zero reconcile exit. Empty (not managed) keys are never touched.

**Boot: a oneshot unit, not a udev RUN.** `io.cost.model` and `io.cost.qos` are cgroup-root files keyed by the disk's MAJ:MIN, not properties of a
block-device event. A udev RUN would fire on every change event of the disk (including each MDT install's own trigger), execute a shell inside
udev's restricted sandbox under its timeout, and could not order itself after the scheduler rule it depends on. `mdt-io-cost.service` (Type=oneshot,
RemainAfterExit) runs after `local-fs.target` and `systemd-udev-trigger.service` with an explicit `udevadm settle`, and calls the same
`_mdt_apply_iocost` that install.sh calls, with the values read from `/etc/mdt/host-setup.env` (never literals), so the two paths cannot drift.
The read-back compares field by field with kernel formatting normalised (`95` reads back as `95.00`) and only the keys MDT wrote (after `enable=0`
or `ctrl=auto` the kernel keeps the other fields).

**Weights.** With io.cost the weight is `io.weight` (1..10000, used directly); BFQ used `io.bfq.weight` (1..1000) and systemd compressed values above
100. Every IOWeight comment (env example, the four tier units) is now mechanism-conditional rather than wrong for either. No weight VALUE changed.
Findings, for the operator (not changed here): (1) the "keep <= 100" rule is a BFQ artefact; under io.cost the tier ratios of 100:10:20 can be raised
without rescaling, but the measured gains are with the root weights unchanged (wings.slice 7800, dev.slice 100, system.slice 100) and the probe in
system.slice is NOT protected by weight; (2) dev.slice and system.slice share weight 100 at the root, so under io.cost they split a contended disk
equally while wings.slice gets 7800, which is probably intended but unmeasured for builds; (3) the per-scope `io.bfq.weight` raw writes in
`mdt-container-io-caps.lib.sh` are inert under io.cost (the `IOWeight=1` set-property is what applies); (4) `mdt-host-check.sh` used to claim "IOWeight is
inert" without BFQ, which is false under io.cost; it now derives the active mechanism from the scheduler and `io.cost.qos`.

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

## dev.slice IO governance must be verified, not asserted

The reconcile used to claim governance it did not have (MDT-016). Four decisions
came out of the 2026-10-19 incident; the mechanism is in
`host-setup/CGROUP-NOTES.md` section 3, adoption in CONSUMERS.

- **D-HS-01-1: withdraw by re-asserting, never by emptying.** An empty
  `IO*Max=` assignment resets systemd's whole list, unit-file values included.
  Rejected, by measurement: deleting systemd's runtime drop-in and running
  `daemon-reload`. On the live host (2026-10-19) the unit-file value was NOT
  re-applied to the cgroup (`memory.swap.max` stayed at the old runtime value
  until an explicit `set-property`). Chosen: `set-property --runtime` with the explicit
  fallback numbers; empty assignments only when no device exists. Every apply
  is read back from the kernel `io.max` (device = the originating whole disk) and
  a mismatch fails the service; "authoritative" is only said after a match.
- **D-HS-01-2: baseline plausibility now, redesign later.** The measurement
  reads the hypervisor's cache. A real fix (O_DIRECT + `--fsync`, a working set
  beyond the visible cache, a steady-state tail) changes the shared
  `scripts/debian-install-v2/tools/iocost_coef_gen.py`, outside `mdt-cockpit`,
  so it is MDT-017 (D-NNN filed in both mdt-cockpit and debian-install-v2: the plausibility refusal ships in both consumers, `mdt-io-baseline.py` and `iocost-calibrate.sh`; the generator itself is unchanged). Shipped: schema 3 records the method, old schemas are stale,
  and a rotational-flagged device above 1 GiB/s sequential or 20k random 4 KiB IOPS is refused by ONE definition shared with debian-install-v2 (`iocost_plausibility.py`; sequential 4 KiB IOPS are never judged, merging is off in that run). virtio-blk is `rotational=1` by default, so a fast virtio disk is refused too: fail closed, with an explicit `IO_BASELINE_ROTATIONAL_FLAG=ignore` opt-in (a "looks virtual" heuristic was rejected: it misclassifies both ways). The honest limit:
  a non-rotational device is not bounded, so operator-pinned `DEV_IO_*_MAX` are
  the recommended source on a virtual disk (min of pin and baseline).
- **D-HS-01-3: swap stays ample, the gate tier is throttled (DECIDED by the
  operator, 2026-10-19; MDT-018 closed).** Options were (a) keep 80% of host swap
  cascading, (b) absolute defaults (dev 8G / gates 2G), (c) RAM-relative. Chosen:
  (a), plus a tight IO cap on the gate tier: swap-out IO is charged to the page
  owner's cgroup, so a gate that swaps is throttled by its own cap and cannot
  thrash the shared disk (per kernel source, not measured here: swap writeback bios are associated with the page owner's blkcg via `bio_associate_blkg_from_page` in `mm/page_io.c`). Per-tier keys `DEV_<TIER>_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX`
  (`DEV_GATES_IO_WBPS_MAX` and its siblings); the IOPS sub-ceiling becomes
  min(sub-ceiling, absolute). Cost: a throttled gate stalls and runs slowly, which
  is an infrastructure-classified slowdown, not a product failure. The reconcile
  warns `UNCAPPED SWAP` when swap sits on a disk with no `dev.slice` entry.
- **D-HS-01-4: bytes, base 1024, one conversion.** systemd and MDT could read
  `80M` differently; MDT converts K/M/G/T (base 1024) once and hands systemd and
  the unit file plain byte counts, so unit, runtime and read-back agree.

The size-0 trap: cgroupfs files report size 0, so a size test on one is always
false (the operator's first listing script never showed a limit). MDT reads
content only, ships `mdt-cgroup-io-limits.sh`, and tests it against FIFO-backed
files that report size 0.

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
