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
| Neovim / NvChad | `/etc/xdg/nvim/` (tree + `sysinit.vim`) | none (a starter-style tree would keep its plugins in `~/.local/share/nvim`) | Neovim itself starts `$XDG_CONFIG_DIRS/nvim/init.lua` while the user has no `init.lua`/`init.vim`, so creating one replaces the default; `sysinit.vim` only redirects a `configs.lazy` lockfile into `~/.local/state/nvim` (proven with a fixture tree, not with the shipped one - MDT-007) |
| less | `LESSOPEN` in `shell.env` | none | environment variable |
| MDT shell files | `/usr/local/share/mdt-cockpit/{shell.env,aliases.sh}` | none | the user's `~/.config/mdt-cockpit/` files are sourced *after* and may override; `ai.env` has no image default |
| lnav formats | `/etc/lnav/formats/` | none | already system-wide |
| image manifest | `/usr/local/share/mdt-cockpit/manifest.md` | none (no `~/mdt-manifest.md`) | `IMAGE_MANIFEST` in `/etc/os-release` |

Neovim note: the shipped `NVCHAD_VER` archive is the `NvChad/NvChad` core
repository, whose `init.lua` is a placeholder that prints one hint line
(`use this along starter!!!`) on every start, not a ready-to-run starter
configuration. The move keeps that behaviour as it was (tracked as MDT-007; the
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

**The OCI records are advisory: not consumed by the build (MDT-009).** The base image is still chosen by
`scripts/resolve-devcontainers-release.py` (a floating `mcr.microsoft.com/devcontainers/python` tag passed as
`BASE_IMAGE`), so a record with a digest and a `released_at` is NOT a 14-day certification of the cockpit's
base; it only keeps `cmru versions resolve` working and shows what a vetted base would be. Each target in
`cmru.toml` carries an `ADVISORY` comment, and `scripts/test_ai_cli_versions.py` fails if any build input
(Dockerfile, bake, `build-push.py`, the staging scripts) names a record or its digest while the label stands.
MDT-009 makes the build take the devcontainers record as the `FROM` digest, with an equality test like
`tester-base/tests/test_pins.py`; the label, these paragraphs and that test change together then.

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
