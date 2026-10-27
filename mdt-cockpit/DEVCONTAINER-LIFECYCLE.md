# mdt devcontainer lifecycle — hooks and persistent home

This is the **canonical** reference for the lifecycle hooks an mdt-based devcontainer wires up,
the complete persistent home, and moving from the previous layout. Consuming repos point here and keep only
their project-specific notes. For the whole picture in one place (host setup through terminal
startup, shell selection included) read [docs/DEVCONTAINER-MECHANISMS.md](docs/DEVCONTAINER-MECHANISMS.md).

The shipped template uses `shutdownAction: "none"`; the devcontainer remains running when its
attached Dev Containers session disconnects. `CODEX_SQLITE_HOME` points at the shared SQLite
database beneath the complete persistent home.

## The two lifecycle hooks (set in the consuming repo's `devcontainer.json`)

| Hook | Runs | Where | Script |
|---|---|---|---|
| `initializeCommand` | **before** the container is created | on the **host** | `initialize_container_environment.py` (the "pre" script) |
| `postCreateCommand` | **after** the container is created | **inside** the container | `finalize_container_environment.py` (the "post" script — **baked into the mdt image**) |

> **Why one is vendored and the other is baked in.** `initializeCommand` is the *only* devcontainer
> hook that runs on the **host**, *before* the container exists — at that moment the mdt image
> filesystem isn't available, so its script (`initialize_container_environment.py`) **cannot** come
> from the image; it must be a file in your repo checkout. `postCreateCommand` runs **inside** the
> container, so its script (`finalize_container_environment.py`) **is** baked into the mdt image and
> you reference it by name. `devcontainer.json` itself is inherently repo-specific, so it is also
> vendored. Hence mdt ships two **templates** to copy (`devcontainer.json`,
> `initialize_container_environment.py`) and one **baked-in** script
> you just call (finalize).

### Pre script — `initialize_container_environment.py` (host)
Runs on the host before Docker starts and prepares `$HOME` bind-mount sources. It is stdlib-only and
idempotent. It parses the sibling `devcontainer.json` and inspects every `type=bind` source under
`$HOME` by its actual filesystem type. Existing regular files and directories are accepted without
name or suffix heuristics. Under the default policy, MDT creates its known directory sources as
**real dirs** with the right mode, chosen by the mount's **target**, not by the source's name: the source
mounted at the container home (`/home/<remoteUser>`) gets `0700`, the source mounted at `/tmp` gets `1777`
(re-asserted on every run), and other directories get `0755`. A consumer that spells its own sources, such as
`~/mdt-cockpit-vbpub-home/` and `~/mdt-cockpit-vbpub-tmp/`, therefore gets the same modes and the old-state
notice names the source it actually mounts. For missing custom directories, a trailing `/` on the source is an MDT bootstrap hint
that selects directory creation under the default `create-by-spelling` policy; a missing source
without `/` becomes an empty regular file (`0600`). The host-setup wizard can select
`DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` to require every `$HOME` source managed by the
bootstrap to exist first. There is no migration: when the previous `~/mdt--mounted-folders/` still
holds files and the new home is absent or empty, it prints one informational line pointing at the
[manual-copy section](docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug) and
carries on (the exit status never depends on it). Keep the trailing slash on
every directory source in the JSON; Docker itself does not interpret it as a source-kind setting. See the
[consumer example](docs/CONSUMERS.md#optional-git-config-mount).

### Post script — `finalize_container_environment.py` (container, baked into mdt)
Lives in the mdt image at `/usr/local/bin/finalize_container_environment.py`; a consuming repo wires
only `"postCreateCommand": "finalize_container_environment.py"`. Symmetric to the host-side
`initialize_container_environment.py`. stdlib-only, idempotent.

It does the **generic, ciu-AGNOSTIC** setup every mdt devcontainer wants — the comment-only
`~/.zshrc` stub (only when no zsh startup file exists, so zsh's new-user wizard stays quiet),
`.vscode/settings.json` (global python), the managed-BuildKit check, and a base-image tool check — and
**brackets** that with the consumer's own hooks. mdt **never** imports or calls ciu: mdt *ships and
encourages* ciu, but a repo that doesn't use it still gets a fully working devcontainer.

#### Consumer hook contract
finalize discovers and runs hooks from your repo's `.devcontainer/` (no need to fork the script):

```
.devcontainer/finalize.pre.d/*.sh     # run (sorted) BEFORE the generic mdt steps
<generic mdt steps>
.devcontainer/finalize.post.d/*.sh    # run (sorted) AFTER  the generic mdt steps
```

- Single-file forms `finalize.pre.sh` / `finalize.post.sh` are also honoured (run after the `.d` dir).
- Order hooks with a numeric prefix (`10-…`, `20-…`). Executable hooks run directly; others via `bash`.
- Each hook inherits the environment plus: `MDT_FINALIZE=1`, `MDT_ENV_TYPE`, `MDT_WORKSPACE_DIR`,
  `MDT_DEVCONTAINER_DIR`, `MDT_USER`/`MDT_UID`/`MDT_GID`/`MDT_DOCKER_GID`.
- **Enforcement boundary:** mdt's own steps only *warn* on failure (they never fail the build). A
  **consumer** hook that exits non-zero is reported and makes finalize's exit non-zero — because that
  hook is where *you* put *your* critical setup. By default the remaining hooks still run; set
  `MDT_FINALIZE_STRICT=1` to abort on the first failing hook.
- Flags: `--no-hooks` (generic only), `--hooks-only` (skip generic), `--devcontainer-dir PATH`.

**This is where ciu (if you use it) goes** — e.g. a repo that deploys with ciu drops
`.devcontainer/finalize.post.d/10-<repo>-ciu.sh` that exports `REPO_ROOT`, runs `ciu env generate`,
installs repo deps, etc. mdt provides the bracket; the consumer owns the contents.

## Complete persistent home — `mdt-cockpit-<checkout>-home`

The template binds `${localEnv:HOME}/mdt-cockpit-${localWorkspaceFolderBasename}-home/` (one
directory per checkout; the initializer expands the variable to the checkout's folder name) to
`/home/vscode`.
It persists the entire home: agent credentials and sessions, `.config`, `.local`, shell startup
files, caches, worktrees stored below home, and any other user-created files. A new host source is
created as a real directory with mode `0700`, and **a new home starts empty**.

| Host source | Container target | Notes |
|---|---|---|
| `~/mdt-cockpit-<checkout>-home` | `/home/vscode` | complete persistent home, mode `0700` on creation |
| `~/mdt-cockpit-<checkout>-tmp` | `/tmp` | independent host-backed scratch/worktree storage, mode `1777` |
| `~/.ssh` (native host) | `/run/mdt/host-ssh` (ro) | host SSH keys remain available outside the home mount |

The image keeps executables and runtimes outside the home mount, in the `mdt` group tree:
`/opt/mdt/bin`, `/opt/mdt/venv`, `/opt/mdt/venvs/pyNN`, `/opt/mdt/npm`, `/opt/mdt/python`,
`/opt/mdt/oh-my-zsh`, and `/srv/pwmcp-instance`. Defaults are wired **system-wide** - `/etc/bash.bashrc`,
`/etc/zsh/zshrc`, `/etc/nanorc`, `/etc/htoprc`, `/etc/mc/mc.ini`, `/etc/xdg/nvim`, `LESSOPEN` - and the
MDT shell files, examples and manifest live under `/usr/local/share/mdt-cockpit`. The finalizer copies
nothing into the home, creates no links, and never replaces a user path; creating your own file
overrides the matching default. `PATH` is the image's one definition with `~/.local/bin` last.

The user-editable central API key file is `~/.config/mdt-cockpit/ai.env`, a file **you** create from
`/usr/local/share/mdt-cockpit/ai.env.example`. Shell startup sources it once, so the keys are in every
tool's environment. The same directory may carry optional `aliases.sh` and `shell.env` overrides,
sourced after the image's own.

**Why a persisted host-backed `/tmp`:** `/tmp` git worktrees then survive rebuilds AND are visible to
**sibling containers** (e.g. the `test-runner`) that bind the same host dir — so `/tmp`-based worktrees
can be gated. (Without this, `/tmp` is a container-local overlay with no host path; a sibling container
can't see it — `.worktrees/` under the repo bind mount was the prior workaround.)

**`HOST_MDT_TMP`** (the host path of that persisted `/tmp`) is **autodetected by `ciu env generate`**
(it inspects the devcontainer's own `/tmp` bind-mount source) and written to `.env.ciu`; sibling
containers read it from there. No hardcoded host path, no raw `containerEnv` var.

## Host resource governance (cgroups/slices)

On hosts that tier resources via systemd slices (cgroup v2) — for example a shared host that also
runs a production workload alongside devcontainers and best-effort test/build containers — this
devcontainer must land in its own tier instead of the host's default cgroup.
`templates/devcontainer.json` ships a `--cgroup-parent=dev-interactive.slice` runArg for this
(plus `containerEnv` vars naming three dev-tier slices — `CGROUP_PARENT_DEV_INTERACTIVE`,
`CGROUP_PARENT_DEV_BACKGROUND`, and, for gate/lane containers, `CGROUP_PARENT_DEV_GATES` — see
below — for any in-container tool that spawns its own containers. Gate launchers fail closed when
the gates value is absent; long-running stacks use the background value. See
[host-setup/README.md](host-setup/README.md) "dev-gates: why").
See also [docs/CONTAINER-DOCTRINE.md](docs/CONTAINER-DOCTRINE.md) for how this fits the doctrine's
layering (host/orchestration concern, not image content).

### Cgroup placement is CREATE-TIME only

Docker's `--cgroup-parent` (and the compose/Kubernetes equivalents) is fixed at container **create**
time. There is no supported way to move a running container into a different slice afterwards —
placement can only be declared where the container is created (`runArgs` here, `cgroup_parent:` in
compose, a Wings/panel setting, etc.), never applied post-hoc by a script running inside or beside
the container. This is one of several things a slice unit fundamentally cannot express — the full
list, and the BFQ caveats that come with IO weights, are in
[host-setup/CGROUP-NOTES.md](host-setup/CGROUP-NOTES.md).

### Host prerequisite: the slice unit must actually exist

`--cgroup-parent=dev-interactive.slice` only produces a governed container if the host has
installed a systemd slice unit at `/etc/systemd/system/dev-interactive.slice` with real limits.
**The [`host-setup/`](host-setup/README.md) companion installs and maintains exactly this** — the
interactive tier for devcontainers, the dedicated `dev-gates.slice` for gate/lane containers, and
`dev-background.slice` for long-running stacks and the Docker daemon-wide fallback via
`/etc/docker/daemon.json`, all nested under one shared `dev.slice` IOPS/bandwidth ceiling and
rendered from a per-host env file, plus a measured-baseline IO-cap service and a health check
(`mdt-host-check.sh`). Hand-rolled
minimal equivalent:

```ini
# /etc/systemd/system/dev-interactive.slice — illustrative values, tune to your host
[Unit]
Description=Interactive devcontainers — responsive, bounded, below any production tier
Before=slices.target

[Slice]
MemoryHigh=5G
MemoryMax=7G
CPUWeight=200
```

After installing or editing the unit: `systemctl daemon-reload` (no restart required — systemd
activates the slice on demand the first time something references it).

Docker and systemd still accept an unknown parent by creating a **transient, unlimited** slice.
The MDT template prevents that silent fallback: its host-side `initializeCommand` derives the
interactive, background, and gates names from `containerEnv`, checks that the interactive name
matches the `runArgs` parent, and verifies each unit's exact ID, `LoadState=loaded`, and installed
`FragmentPath` before creating the devcontainer. Missing, runtime-generated, or unqueryable units
stop startup. Install the host policy and run `mdt-host-check.sh` before rebuilding. See the
[preflight design](docs/DESIGN-GUIDE.md#host-cgroup-preflight) and
[consumer steps](docs/CONSUMERS.md#host-cgroup-preflight).

### What the container can (and can't) see about its own placement

Containers run with `cgroupns=private` by default, so **a process inside the container cannot see or
change which slice it landed in** — there is no file that names the parent slice, and nothing inside
can move the container to a different one. What the container *can* do is read its own **effective**
resource limits, because the private cgroup namespace maps `/sys/fs/cgroup` directly onto the
container's own leaf cgroup (no host path traversal needed):

```bash
cat /sys/fs/cgroup/memory.max     # hard memory ceiling, or "max" if unbounded
cat /sys/fs/cgroup/memory.high    # soft throttle threshold, or "max" if unset
cat /sys/fs/cgroup/cpu.weight     # proportional CPU share (systemd default: 100)
cat /sys/fs/cgroup/io.max         # per-device IOPS/bandwidth caps; empty if none set
```

The devcontainer's login shell prints a compact one-line summary of these automatically on every
interactive login — see the cgroup banner in `customization/profile.sh`. This is display-only: it
lets you see at a glance whether you're on a governed host, it cannot change placement.

The same login banner also reports KSM (kernel same-page merging) opt-in status — whether *this*
container's processes actually got `PR_SET_MEMORY_MERGE`, which depends on the host kernel and is
therefore just as un-knowable from a build as cgroup placement is. See "KSM opt-in (kernel
same-page merging)" in `README.md` for the build-time toggle and the per-command runtime opt-out.

### BuildKit/buildx builders are governed separately

The above covers the devcontainer *itself*. Release builds run through `docker buildx bake` /
`scripts/release-bake.sh`, which use their own BuildKit builder container. See
[MDT build and release architecture](docs/BUILD-ARCHITECTURE.md) for its hard limits, why its host
cgroup normally appears as a separate scope in `system.slice`, and how the local repack workers
inherit the caller's slice.

## Moving to the fresh home

There is no migration tool, no completion marker and no startup refusal. Copy the two host-side
files (`devcontainer.json`, `initialize_container_environment.py`) from `templates/` into the
consumer's `.devcontainer/` directory and rebuild; the container starts with an empty home.

If you used the previous split-mount template, stop the container (so shell agents, ClaudeLink and
other database writers are quiescent) and copy what you want by hand. The bootstrap prints one line
pointing at the recipe whenever `~/mdt--mounted-folders/` still holds files and the new home is empty:
[Moving from modern-debian-tools-python-debug](docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
lists what is worth copying (agent state, keys, selected `.config` directories), gives a pasteable
`cp -a` example, and names what must **not** be copied (`~/.venv*`, `~/.oh-my-zsh`, `~/.local/bin`,
`~/.local/lib/node_modules`, `~/.codex/packages/standalone`, and seeded copies of image defaults).

Verify the rebuilt container before deleting the old directory yourself:

```sh
docker exec "$container_name" sh -lc 'test -d "$HOME/.claude"; test -d "$HOME/.pi"; test -d "$HOME/.claudelink"; test -x /opt/mdt/venv/bin/python; command -v python3; command -v pwmcp'
```

Check agent login state, ClaudeLink's `nexus.db` and any WAL/SHM sidecars, and user shell config.
If ClaudeLink uses SQLite WAL mode, copy `nexus.db`, `nexus.db-wal` and `nexus.db-shm` together from
the same stopped-container snapshot; never copy only the main database or mix sidecars from
different snapshots. The old directory is never touched by mdt-cockpit, so it remains your rollback.
