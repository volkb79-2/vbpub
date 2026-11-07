# Host service sockets, groups and installers: cross-tool implementation plan

Status: PLAN, ready for distribution (2026-11-05; amended 2026-11-07 for the operator
ruling that no access level maps to the `docker` group, D2 and P-5). Nothing below is
implemented. Reviewed by an independent reviewer; the findings are folded in. systemd semantics
marked "recheck" were reasoned from the man pages, not run, because `sd_listen_fds(3)` is
not installed here. Verify them in the phase-1 VM acceptance before relying on them.
Owner of the plan: operator. Implementation is distributed by a cross-tool agent.
Tools touched: topos, cgroup-profiler (cgpd/cgpctl), mdt-cockpit (host-setup and
devcontainer templates), run-gate-project, assay tools, the Netcup runner script,
vbpub `.devcontainer`, debian-install-v2 (optional phase), cli-extended (optional helper).

All file:line references are against `main` at the time of writing (after `3ff802c75`).
Re-check them before editing; other streams are active in topos and mdt-cockpit.

---

## 1. Why

Host daemons (topos, cgpd) are used from mdt devcontainers today, but they are also
meant to run standalone, on hosts without mdt host-setup, for consumers other than the
cockpit. The current split of responsibilities breaks that:

- **Two owners for one directory.** topos ships `d /run/topos 0750 root topos -`
  (`topos/src/topos/assets/systemd/topos.tmpfiles:3`) and `RuntimeDirectory=topos`
  with `Group=topos` (`topos.service`). mdt host-setup ships a competing
  `d /run/topos 0750 root docker -` (`mdt-cockpit/host-setup/units/mdt-topos.conf`)
  and a drop-in that rewrites the service's own `Group=` to `docker`
  (`units/mdt-topos-service.conf`, installed by `host-setup/install.sh:1324-1326`).
  Which tmpfiles rule wins depends on file-name order. topos's own preflight then sees
  the wrong group.
- **host-setup creates a service's runtime tree whether or not the service exists.**
  On v1001, `/run/topos` exists (`root:docker 0750`) and is empty: no daemon. Every
  devcontainer mounts that empty directory.
- **cgpd's runtime tree is also owned by host-setup.** `units/mdt-cgprofile.conf`
  (`d /run/cgprofile 0770 root docker`), `units/mdt-cgprofile-observer.conf`,
  `units/mdt-cgprofile.sysusers`, `install.sh` `provision_cgprofile_observer`
  (`:150-250`). The daemon itself chmods the directory to 0770 and chowns the
  control socket to the directory's gid (`scripts/cgroup-profiler/src/cgprofile/serve.py:3393-3440`).
- **The cgpd installer lives in mdt-cockpit** (`host-setup/scripts/mdt-cgprofile-install.sh`),
  so a standalone host cannot install cgpd without mdt host-setup.
- **topos has no installer at all.** `topos daemon install-plan` only prints steps
  (`topos/src/topos/daemon/deploy.py:356-357`, rendered by `str.replace` on template
  literals at `:548-558`).
- **Daemon state shares the consumers' directory.** topos keeps `bpf/`
  (`config.py:166`) and `squeeze/` (`actions/squeeze.py:123`) under `/run/topos`, which
  is bind-mounted into containers. cgpd keeps `damon.lock` under `/run/cgprofile`.
- **The socket inode is recreated on every daemon restart.** That is why consumers must
  bind directories, never files, and why the daemons carry stale-socket probes,
  identity-checked unlinks and a rebind watchdog (`serve.py:3132-3187`, `:3509`).

## 2. Decisions (operator, 2026-11-05)

| # | Decision | Reason |
|---|---|---|
| D1 | Each service receives its listening sockets from **systemd `.socket` units**, one unit per access level. | systemd creates the socket with the configured owner, group and mode, and holds it across daemon restarts (no inode churn, no stale-socket logic). No tmpfiles, `RuntimeDirectory=` or drop-in conflicts. |
| D2 | **One dedicated group per service per access level** (`topos-observe`, `cgprofile-control`, `cgprofile-observer`). **No level ever maps to the `docker` group**, on any host (ruling 2026-11-07, superseding "cockpit hosts: `docker`"). A principal is granted a level by membership: host users (the host login user, the runner user) are added to the group, and a devcontainer gets the host GID as a numeric `--group-add`. | Least privilege; `docker` is root-equivalent, and a socket level must not grant, or be granted by, docker access. |
| D3 | **Per-level layout** `/run/<svc>/<level>/<svc>.sock`. **No legacy support** (ruling later on 2026-11-05, superseding the transition release): no dual paths, no client fallbacks, no migration flag, no `legacy` doctor check. A host gets a fresh install, or the old installation is removed by hand first (section 4). | A consumer bind-mounts exactly the level directory it is entitled to and cannot see the others. |
| D4 | Each service owns its **install verb**, an optional **TTY wizard** for missing values, and a **`doctor` verb**, all on **cli-extended**. mdt host-setup and debian-install-v2 only call these verbs with their values. | Standalone hosts get the same installer; one owner per path. |
| D5 | **dockerd configuration stays out of scope.** debian-install-v2 and host-setup already merge disjoint `daemon.json` keys (D-F1: `scripts/debian-install-v2/debian_install_v2/installer.py:991-998`, `:1070-1073`; host-setup owns only `cgroup-parent` and the `docker.socket` drop-in). | Measured: no duplication, only a documented key split. |
| D6 | Delivery is distributed by a cross-tool agent (this plan is the handoff). | Operator instruction. |

### Decisions made in this plan (revisit only with the operator)

- **P-1. Levels are separate APIs, not a hierarchy.** cgpd's control socket does not
  serve the observer API (`tests/test_serve_resource_observer.py::test_dedicated_socket_serves_observer_but_control_socket_does_not`,
  observer handling `serve.py:4207-4343`, answered from the peer's own cgroup). A
  principal that needs both levels joins both groups. Do not merge the APIs.
- **P-2. topos has one level today: `observe`.** The daemon is read-only: mutating ops
  are not in `CAPABILITIES`, and the auth hook always allows
  (`topos/src/topos/daemon/api.py:181`, `:302`). Add a `control` level only when a
  mutating op exists **on the socket**. topos's client-side root actions
  (`actions/squeeze.py`, `topos damon stop`, `paddr start`, `cli.py:818-846`) are not
  mediated by the daemon and are not a reason for a `control` level.
- **P-3. State lives in `/run/<svc>/state/` (0700 root), never in a level directory.**
  It is created by the service unit's `RuntimeDirectory=<svc>/state` with
  `RuntimeDirectoryMode=0700` and `RuntimeDirectoryPreserve=yes`.
- **P-4. The level groups are declared by the service's own sysusers file**, which its
  install verb installs. The group name of a level is configurable (default: the
  tool-specific name). A group that already exists keeps its GID, so a host whose
  `cgprofile-observer` GID is in use by running lanes keeps it.
- **P-5. No level group may be the `docker` group** (by name or by GID), GID 0, or the
  group of another level of the same service. This generalises the existing observer
  refusal (`host-setup/install.sh:193-201`) to every level and moves it into
  `cgpd install` and `toposd install`. run-gate and assay rely on it for the observer
  (`run_gate.py:8800`; `assay/tools/resource-observer-gid.sh:44-47`), and from
  phase 1 also for the control level (5.5, 5.6).
  - Groups are tool-specific, so no two estate socket levels share a group, across
    services too (e.g. `topos-observe` ≠ `cgprofile-observer`, `mdt-buildkit` ≠
    `cgprofile-control`). A service installer can only check its own levels; the
    cross-service check lives in host-setup's wizard and `check.sh` (5.3.4, 5.3.8).
  - The installers run on the host as root, so they take the docker GID from
    `getent group docker`. Consumers inside a container cannot trust their own group
    table (`run_gate.py:8796-8798`). They take it from a numeric value in their
    environment: `MDT_HOST_DOCKER_GID`, set by the devcontainer (5.4), the runner
    container (5.7) and, for host shells, host-setup's environment exports (5.3.8);
    and assay's existing `ASSAY_GATE_HOST_DOCKER_GID` (`resource-observer-gid.sh:59`).
    Today's fallback,
    the GID of `/run/cgprofile` with mode 0770, goes away: that directory is
    `root:root 0755` in the new layout.
- **P-5a. The same rule binds every estate host socket.** host-setup's own BuildKit
  socket is chgrped to `docker` today (`units/mdt-buildkitd.service.in:112`); it moves
  to a dedicated `mdt-buildkit` group (5.3.8). The group name, the
  `MDT_BUILDKIT_USERS` key and the access change for other BuildKit users are this
  plan's choices under the ruling's "mdt-*" scope; confirm them with the operator
  before phase 2. iogovmon has no socket today and runs as its own `iogovmon` system
  user (`iogovmon/README.md:182`); a future socket follows this rule.
  `/run/docker-api` stays on `docker`: it is docker access itself.
- **P-6. topos gets a cli-extended admin entry point `toposd`** (daemon run, install,
  doctor, unit print/check, config check), mirroring `cgpd`. `topos` itself stays on
  argparse for now (`topos/src/topos/cli.py:122-124`). Rewriting the whole topos CLI
  is out of scope and would collide with the console redesign
  (`topos/docs/PLAN-2026-11-CONSOLE.md`). `topos daemon serve|preflight|install-plan|status`
  stay as aliases for one release, with a deprecation line.
- **P-7. Install verbs run from the wheel itself through zipimport**, before any venv
  exists, exactly as debian-install-v2 bootstraps cli-extended (CX-D3,
  `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md:24-35`). The
  install and doctor modules may import only the stdlib and cli-extended; a test
  enforces this.
  - **They live outside the daemon package.** For topos that means a new
    `topos/src/topos/service/` package, because importing anything under
    `topos.daemon` runs `topos/daemon/__init__.py`, which eagerly imports the api,
    broker and bpf modules.
  - **cgprofile's venv** is built from its own wheel plus cli-extended with
    `--no-index`, as `mdt-cgprofile-install.sh` already does.
  - **topos's venv also needs third-party wheels** (`textual>=8.2.8` and its
    dependencies, `topos/pyproject.toml`).
    - The topos release publishes a hash-pinned lock, `topos-<ver>-daemon.lock`, as a
      release asset next to the wheel. It is installed with
      `pip install --require-hashes -r`, from the configured index, or with
      `--find-links <dir>` when `--wheel-source` is a directory.
    - The lock is generated by the release and follows the estate freshness rule:
      newest stable release, at least 14 days old.
    - `--require-hashes` needs a hash for every requirement. Install the topos and
      cli-extended wheels in a second `pip install --no-deps` step, after their own
      sha256 check, rather than listing them in the lock.
    - Making `textual` an extra was rejected, because the `topos` command is the TUI.
- **P-8. Not one commit.** See section 6 (landing).
- **P-9. Explicit daemon start mode.** A daemon serves passed sockets (`LISTEN_FDS`) or,
  only with an explicit `--self-bind` flag, binds its configured paths itself (tests,
  development). With neither, it refuses to start, naming the socket units. This
  prevents a service started without its socket units from silently self-binding with
  wrong ownership.
- **P-10. Phase-1 releases are not deployable through the old host-setup installer.**
  `mdt-cgprofile-install.sh` renders only `cgpd.service` and `cgprofile.slice`. A
  2.2.0 service would `Requires=` socket units that do not exist, and P-9 would refuse
  self-bind.
  - A host that has not been cut over keeps running its current (pre-phase-2)
    host-setup, with `CGPROFILE_WHEEL` below 2.2.0, and never receives the phase-2
    host-setup or `check.sh`. The latter calls `cgpd doctor`, which the old cgpd lacks.
  - The pin is raised only in cutover step 4, by installing the phase-2 host-setup.
- **P-11. Install verbs refuse an old installation instead of migrating it.**
  - `cgpd install` and `toposd install` refuse when they find leftovers of the old
    layout:
    - old config keys (`ctl_socket`, `observer_socket` in `/etc/cgprofile/cgpd.toml`);
    - host-setup's tmpfiles rules or drop-ins for their tree;
    - a `/run/<svc>` tree whose owner or mode differs from 3.1;
    - `/opt/cgprofile` entries whose `MANIFEST` names the mdt installer
      (`installer=mdt host-setup`), or a leftover `PENDING` / `current.new`.
  - The refusal lists the manual removal steps (section 4, "Manual removal"). Nothing
    is rewritten or chmodded in place.
  - A re-run on a host the verb itself installed (owner and modes match 3.1) is a
    normal upgrade.

## 3. Target design

### 3.1 Layout

```
/run/topos/                          0755 root:root   (socket units' DirectoryMode)
/run/topos/observe/                  0755 root:root
/run/topos/observe/topos.sock        0660 root:topos-observe
/run/topos/state/                    0700 root:root   (RuntimeDirectory=topos/state)
/run/topos/state/bpf/                (was /run/topos/bpf)
/run/topos/state/squeeze/            (was /run/topos/squeeze)

/run/cgprofile/                      0755 root:root
/run/cgprofile/control/              0755 root:root
/run/cgprofile/control/cgprofile.sock    0660 root:cgprofile-control
/run/cgprofile/observer/             0755 root:root
/run/cgprofile/observer/cgprofile.sock   0660 root:cgprofile-observer
/run/cgprofile/state/                0700 root:root   (damon.lock moves here)
```

The old paths (`/run/topos/topos.sock`, `/run/cgprofile/ctl.sock`,
`/run/cgprofile/observer/resource-observer.sock`) are not served at all.

Access control is the socket file's group and mode; directories are root-owned and
not writable by anyone else, so a consumer cannot replace a socket. Directories are
0755 (not 0751) so a standalone member of the level group can reach the socket
without extra traversal grants.

### 3.2 Socket units (rendered by each service's `unit print`)

```ini
# cgprofile-control.socket
[Unit]
Description=cgprofile daemon, control access level
# No PartOf=: it would propagate a service restart to the socket and recreate the inode.

[Socket]
ListenStream=/run/cgprofile/control/cgprofile.sock
SocketUser=root
# from config [sockets.control].group
SocketGroup=cgprofile-control
SocketMode=0660
DirectoryMode=0755
FileDescriptorName=control
RemoveOnStop=no
Service=cgpd.service

[Install]
WantedBy=sockets.target
```

`cgprofile-observer.socket` has the same shape, with `FileDescriptorName=observer` and
`SocketGroup=cgprofile-observer`. `topos-observe.socket` has
`FileDescriptorName=observe` and `Service=topos.service`. Each socket unit has exactly
one `ListenStream=`.

Ordering lives in the **service** unit:

```ini
# cgpd.service (excerpt)
[Unit]
Requires=cgprofile-control.socket cgprofile-observer.socket
After=cgprofile-control.socket cgprofile-observer.socket

[Service]
Sockets=cgprofile-control.socket cgprofile-observer.socket
```

What these semantics give (`man systemd.unit`, `systemd.socket`; recheck on the target
systemd version):
- `Requires=` propagates a stop or restart of a socket unit to the service. So
  `systemctl restart cgprofile-control.socket` makes the daemon pick up the new fd.
- A `systemctl restart cgpd` leaves the sockets, and their inodes, untouched.

Both service units keep `WantedBy=multi-user.target`. The daemons collect continuously,
so they start at boot, not on first connect.

- cgpd keeps `PrivateNetwork=yes`. Its path sockets are created by systemd in the host
  namespace and passed in as fds.
- topos drops `Group=topos`, `RuntimeDirectory=topos` and `RuntimeDirectoryMode=0750`
  in favour of `RuntimeDirectory=topos/state` with mode 0700 and preserve.

### 3.3 Daemon side: adopting passed sockets

Both daemons implement the same contract:

1. When `LISTEN_PID == getpid()` and `LISTEN_FDS` is set, read `LISTEN_FDNAMES`.
   Build `{level: socket}` from fds 3..3+N-1. Exactly one fd per level; a second fd
   with the same name is an error at start.
2. In activated mode, the daemon:
   - never binds, chmods, chowns or unlinks a socket node;
   - skips the stale-socket probe;
   - disables the rebind watchdog (cgpd `serve.py:3509`). doctor reports a missing node
     instead, and the remedy is `systemctl restart <svc>-<level>.socket`, which
     restarts the service too through `Requires=`. Restarting the socket while the
     service keeps running would leave the daemon accepting on a dead fd.
   - After adopting the fds, it unsets `LISTEN_PID`, `LISTEN_FDS` and
     `LISTEN_FDNAMES`, so child processes never inherit them.
3. If a configured level has no fd, the daemon logs an error and serves the others.
   That matches cgpd's current observer behaviour (`serve.py:3207`). An unknown fd name
   is an error at start.
4. **Self-bind mode only with `--self-bind`** (P-9), for tests and development. The
   production unit never passes it. In self-bind mode cgpd stops chmod/chowning the
   parent directory (`serve.py:3393-3440`): the directory is the installer's or the
   test's job. Without `LISTEN_FDS` and without the flag, start is refused.
5. `doctor` and `status` report the mode (`activated` or `self-bind`) and the fd count
   per level.

### 3.4 Service configuration

Socket levels become fixed (non-reloadable) keys in each service's own config file. The
config file is the single source for `unit print`, `install` and `doctor`.

```toml
# /etc/cgprofile/cgpd.toml (new keys)
[sockets.control]
path   = "/run/cgprofile/control/cgprofile.sock"
# dedicated group; never the docker group (P-5)
group  = "cgprofile-control"
mode   = "0660"

[sockets.observer]
path   = "/run/cgprofile/observer/cgprofile.sock"
group  = "cgprofile-observer"
mode   = "0660"
```

- Today's `ctl_socket` and `observer_socket` keys (`daemon_config.py:62-64`) are
  removed. An old key in a config file gets a named refusal with the new spelling, in
  the style of `STALE_ENV` at `daemon_config.py:91`. That is an error message, not a
  migration (P-11).
- `damon_lock` moves to `/run/cgprofile/state/damon.lock`.
- Validation:
  - the control group differs from the observer group, by name and by GID;
  - no level group is `docker`, GID 0, or the docker GID (P-5; the GID checks run
    where the groups exist: `config check` on the host and `install`);
  - paths are absolute and distinct;
  - `mode` is an octal string.
- topos gets `/etc/topos/toposd.toml` with `[sockets.observe]` of the same shape, plus
  `state_dir` defaulting to `/run/topos/state`. Its `config.py` user config stays
  client-side.

### 3.5 Install, wizard and doctor (cli-extended)

What cli-extended provides (`libraries/cli-extended`):
- `VerbSpec(mutating=True, dry_run=True)` gives shared `--yes` and `--dry-run`
  (`parser.py:349`, `:416`, `:621`, `:630`). A dry run never prompts.
- `runtime.prompts` (`prompts.py:81`): text, confirm, select, checkbox. It is TTY-only
  and exits 130 on cancel, and `runtime.output.is_interactive` tells you whether to
  prompt (README:195-203).
- `register_doctor` / `DoctorCheck` / `CheckResult` (`doctor.py:41`, `:75`, `:225`;
  README:581-606; SPEC §15): ok/warn/fail/skip, `--check NAME`, `--json`.
- Constraints (`constraints.py`) and `SelectorList` (`values.py:19`).
- There is no "prompt for missing values" helper and no runtime config layer: both are
  consumer-owned. Implement them in cgprofile first. If topos needs the identical code,
  upstream a small helper to cli-extended as an optional package (section 5.9).

**Install verb** (`cgpd install`, `toposd install`), mutating with dry-run:

Inputs: wheel pin (`<ver>@sha256:<hex>`) and source (https base or directory), the
cli-extended wheel pin, the per-level group name (default the tool-specific name), the
users to add per level (`--add-user LEVEL=USER`, repeatable), unit options (cgpd:
slices, drain timeout, `allow_uids`). They come from flags, or from an install record
`/etc/<svc>/install.toml` written by the previous run.

A level group of `docker`, GID 0, the docker GID, or the same GID as another level is
refused with exit 2 on every path (flag, install record, prompt answer). There is no
override flag (P-5).

On a TTY, missing values are prompted:
- for each level: confirm the default group name or enter another dedicated one;
- which users to add to each level;
- whether to enable now.

On a pipe, a missing required value is an error that names the flag.

Steps:
1. Verify and fetch the wheels (sha256 before unpacking).
2. Create `/opt/<svc>/<ver>/venv` (immutable once its `MANIFEST` exists).
3. Render the config and run `config check`.
4. Render the units with `unit print`: service, slice, socket units, plus
   `/etc/sysusers.d/<svc>.conf`.
5. Detect drift by comparison: version, config, each unit, sysusers.
6. For cgpd: drain or refuse when a restart is needed while live sessions exist (exit
   3, `PENDING`). `--force-restart` overrides.
7. Apply atomically: install files, swap the `current` symlink, `systemd-sysusers`,
   add the listed users to their level groups (`gpasswd -a`; members are only ever
   added, never removed), then `daemon-reload`.
   - A new membership applies to a user's next login session or a restarted service,
     not to running ones. The install output says so and names the users added.
   - Restart a socket unit only when its own unit text changed; its service follows
     through `Requires=`. Otherwise enable/start/restart/reload only the service.
   - **No in-place normalization** (P-11). systemd creates parent directories only when
     they are absent and never chmods an existing one. So before applying anything, the
     installer checks `/run/<svc>`, its level directories and `state/` against 3.1, and
     refuses when one exists with another owner or mode. Directories created by
     host-setup's old tmpfiles rules (`0770 root:docker`, `0750 root:docker`,
     `0751 root:cgprofile-observer`) fall into that case. The refusal names the manual
     removal steps.
   - **Refuse a downgrade** to a version without socket units while socket units are
     installed, unless `--allow-downgrade`, which first disables and removes them. An
     old daemon would unlink and rebind the node under systemd's socket.
     `supported_keys` (the version-gated key table) also gates which units are
     rendered.
8. Wait for ready (`status --json` reports the expected version and a clean config).
9. Prune old versions.
10. Write `/etc/<svc>/install.toml`.

Exit codes: 0 done or nothing to do, 2 refused or failed, 3 drain deadline (cgpd).

**Uninstall verb** (both). Stop and disable the service and socket units, then:
- remove the units: service, slice and socket units;
- remove the sysusers file, `/etc/<svc>/` (config and `install.toml`), `/opt/<svc>`
  including the `current` symlink, and `/usr/local/bin` symlinks;
- remove the socket nodes, level directories and `/run/<svc>/state`. They survive a
  stop because of `RemoveOnStop=no` and `RuntimeDirectoryPreserve=yes`.
- remove any leftover `mdt` drop-in for the service (for example
  `topos.service.d/50-mdt-socket-group.conf`);
- `daemon-reload`, then `reset-failed` for the removed units.

Groups and their memberships are kept unless `--remove-groups`. cgpd's
`/var/lib/cgprofile` (sessions and overrides) is kept unless `--purge-data`. This is
required so mdt host-setup can leave a role cleanly.

**Doctor verb** (`cgpd doctor`, `toposd doctor`: service side). Checks are kebab-case,
and each failure carries a `remedy`:

`config-valid`, `units-current` (`unit check` drift), `groups` (each level group
exists; no level GID is 0 or the docker GID; the level GIDs differ; members listed,
including the users recorded in `install.toml`), `socket-units` (enabled and active), `socket-nodes` (each path: is a
socket, owner root, group and mode per config), `socket-dirs` (root-owned, not
group/other-writable), `state-dir` (0700 root), `service` (active, version =
installed), `connect` (per level: connect plus a health or contract probe),
`foreign-rules` (fail if any tmpfiles rule or drop-in outside the service claims its
tree; detects the old `mdt-topos*.conf` and `mdt-cgprofile*.conf` files; the remedy is
the manual removal), `caller-access` (does the caller belong to each level group;
informational for root).

Doctor limits:
- `connect` for a level the caller does not belong to is `skip` (with a note), not
  `fail`: EACCES there is expected.
- Checks that need root (a config or state directory the caller cannot read) report
  `skip` with "run as root" instead of failing.

`cgpctl doctor` (client side, `cgpctl.py:727-736`) keeps its client checks, updated to
the new paths and groups.

## 4. Release and cutover sequence

There is no legacy support (D3). Old and new components do not interoperate: a new
client finds no socket on an old host, and an old client finds no socket on a new host.
Safety therefore comes from the order below, with each host switched in one bounded
**cutover**. Lanes and devcontainers on that host are down during the cutover.

| Phase | Content | Lands as | Gate to next phase |
|---|---|---|---|
| **1. Services and clients** | topos and cgpd: socket units on the new paths only, level groups, state dirs, install/uninstall/doctor with the P-11 refusal, `--self-bind`. Clients use the new paths only: topos attach/mcp/status, `cgpctl`/`ctlwire`, run-gate (preflight rules of 5.5), assay tools (in-lane basename, 5.6). | One integration branch, per-tool commits, **one `--no-ff` merge**; then releases in orchestration order: (cli-extended if touched) → topos, cgroup-profiler → run-gate-project → assay. **Not deployed to cockpit hosts** (P-10), and lanes on cockpit hosts keep their current run-gate/assay pins until that host's cutover. | Releases published; test suites and doctors green in a VM-harness host without mdt host-setup (`scripts/debian-install-v2/testing/vm/`). |
| **2. Consumers ready** | mdt host-setup becomes a consumer (5.3): it calls `cgpd install` / `toposd install` with host-setup.env values (every level on its own dedicated group; the host login user and the runner user added as members) and refuses while its own old files exist. host-setup also exports the host GIDs for the devcontainers' numeric `--group-add` (5.4) and moves the BuildKit socket off `docker` (5.3.8). Devcontainer templates and vbpub `.devcontainer` (5.4) mount only the level directories, and `initializeCommand` checks the declared sockets and a floor on the in-container `cgpctl`/`topos` versions. Netcup runner (5.7). | Second merge; mdt-cockpit release. | `modern-debian-tools-python-debug` retired on main (5.10). |
| **3. Per-host cutover** | r1002, then v1001, then the controller host (never nano1), one host at a time, following the cutover steps below. | No merge: operations, done by a Sonnet agent or the controller with the operator's go per host. | That host's doctors and `check.sh` are green; a lane and a rebuilt devcontainer work on it. |

**Cutover steps (per host)**:
1. Drain:
   - Wait for, or stop, running lanes, cgpd live sessions and BuildKit builds. Never
     force without the operator.
   - Stop the devcontainers on that host, and the runner cockpit container
     (`$COCKPIT_NAME` in `start-cockpit-runner.sh`, which binds `/run/cgprofile` and
     `/run/mdt-buildkitd` at `:19-20`). Their bind mounts of `/run/cgprofile` and
     `/run/topos` die in step 3, and so do their terminals and the agent sessions inside
     them. Announce this to the operator before starting, naming the containers.
2. Stop and disable the old `cgpd` and `topos` units.
3. Manual removal, listed below.
4. Install the phase-2 mdt host-setup, which installs the services, creates the level
   groups and the `mdt-buildkit` group, adds the users from `host-setup.env`, writes
   the GID exports (5.3.8) and restarts `mdt-buildkitd` so its socket moves to
   `mdt-buildkit`.
5. Check the memberships for the roles enabled on that host: `getent group` on
   `cgprofile-control`, `cgprofile-observer`, `topos-observe` and `mdt-buildkit` lists
   the host login user and the runner user where `host-setup.env` says so, and none
   of these GIDs is the docker GID.
6. Make the new memberships live:
   - the host login user logs out and in again, and the VS Code server on the host is
     restarted (kill it, or "Remote-SSH: Kill VS Code Server on Host"): a Remote-SSH
     server can outlive the login session and keep its old environment. Only then
     does `${localEnv:...}` see the GID exports (5.3.8, 5.4);
   - restart the runner service and recreate the runner cockpit container
     (`scripts/netcup/runner/start-cockpit-runner.sh`, 5.7).
7. Pin the phase-1 run-gate and assay releases for that host's lanes.
8. Recreate the devcontainers on that host from the phase-2 templates. Each one gets
   the level GIDs as numeric `--group-add` values.

**Manual removal (old installation)**. This is documented in cgprofile's
`docs/NATIVE-DEPLOYMENT.md`, topos's `docs/DAEMON.md` and host-setup's README, and the
P-11 refusals print it. Remove:
- `/etc/tmpfiles.d/mdt-topos.conf`, `/etc/tmpfiles.d/mdt-cgprofile.conf`,
  `/etc/tmpfiles.d/mdt-cgprofile-observer.conf`;
- `/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf`;
- `/etc/sysusers.d/mdt-cgprofile.conf`. The `cgprofile-observer` group and its GID stay;
  cgprofile's own sysusers file declares it from now on.
- `/usr/local/sbin/mdt-cgprofile-install.sh`;
- `/opt/cgprofile` (the versioned venvs with `installer=mdt host-setup` MANIFESTs,
  `current`, `current.new`, `PENDING`) and the `/usr/local/bin/cgpctl` symlink;
- the old units `/etc/systemd/system/{cgpd.service,cgprofile.slice}` and topos's old
  `topos.service` / `topos.slice` copies;
- the old `/etc/cgprofile/cgpd.toml`.
- Keep `/var/lib/cgprofile` unless the operator wants it purged. Its `overrides.toml` is
  read by the new cgpd, so 5.1 keeps that file's format compatible, or refuses it by
  name.
- then `systemctl daemon-reload`, and `rm -rf /run/cgprofile /run/topos`. Containers
  that bind-mounted these lose them; they are recreated in steps 6 (runner) and 8
  (devcontainers).

Phases 1 and 2 may be merged separately in either order, but no host is cut over before
both are released.

## 5. Per-tool work items

Each item names its seam and the tests and docs that must change. Under the estate
rule, user-facing docs ship in the same change as the code.

### 5.1 cgroup-profiler (`scripts/cgroup-profiler`, project `cgroup-profiler`, 2.1.0 → 2.2.0 in phase 1)

1. **Activated mode** in `src/cgprofile/serve.py`:
   - adopt fds by name (`control`, `observer`; several per name);
   - skip `_bind_control_socket` (`:3189`), `_bind_observer` (`:3207`, `:3220-3307`),
     `_assert_socket_permissions` (`:3393-3440`), the unlinks on stop (`:3489`,
     `:3309`, `_close_socket` `:3547`) and the watchdog (`:3509`, `EXIT_SOCKET_LOST`
     `:206`) when activated.
   - In self-bind mode, drop the parent-directory chmod/chown.
2. **Config** `src/cgprofile/daemon_config.py`:
   - `[sockets.control|observer]` tables (path, group, mode) replace
     `ctl_socket` and `observer_socket` (`:62-64`), with named refusals for the old keys;
   - `damon_lock` default moves to `/run/cgprofile/state/damon.lock` (also `damon.py:63`,
     `:187`);
   - validation: absolute paths, distinct paths, mode is an octal string, and the P-5
     group rules for both levels (not `docker`, not GID 0 or the docker GID, control
     and observer GIDs differ);
   - update `data/cgpd.toml.default`.
3. **Units** `src/cgprofile/unitfiles.py` and `data/*.in`:
   - render `cgprofile-control.socket`, `cgprofile-observer.socket` and the sysusers file;
   - `cgpd.service.in` gets `Sockets=` and `RuntimeDirectory=cgprofile/state`
     (0700, preserve);
   - keep `ReadWritePaths` for `/run/cgprofile/state`;
   - fix the stale `RuntimeDirectory` hint (`src/cgprofile/cli/cgpd.py:152`);
   - update the goldens in `tests/fixtures/units`.
4. **`cgpd install` / `cgpd uninstall`**: port `mdt-cockpit/host-setup/scripts/mdt-cgprofile-install.sh`
   step by step:
   - pin parsing, sha256 fetch, immutable venv plus `MANIFEST`, config render with the
     version-gated key table (`supported_keys` `:110`), `config check`, `unit print`,
     drift by comparison, `drain_or_refuse` (`:149`), atomic apply, `wait_ready`
     (`:187`), `prune_versions`.
   - Drop the mdt-only parts: the `host-setup.env` parser and keys, `MDT_CGP_*` names,
     the `installer=mdt host-setup (MDT-055)` MANIFEST line.
   - Keep `DEFAULT_RELEASE_BASE` as the default source.
   - Keep the test seams (systemctl, fetch, poll intervals) as injectable parameters.
   - Add the old-installation refusal (P-11; config keys, foreign rules, directory
     owner and mode) and the downgrade refusal (3.5 step 7). There is no migration flag.
   - `MANIFEST` records `installer=cgpd install <ver>`. host-setup's `check.sh`
     currently pins the `installer=mdt host-setup (MDT-055)` line; phase 2 replaces that
     check with `cgpd doctor`.
   - Port the generic cases of `mdt-cockpit/host-setup/tests/test_cgprofile_install.py`
     (pin, fetch, venv, drift, drain, apply, readiness, prune) into cgprofile's tests.
     The env-parsing, wizard-validator and `install.sh`/`check.sh` wiring cases
     (`:348-399`, `:468-522`) stay in host-setup.
   - Add a `--self-bind` daemon flag (P-9) to `cgpd run`; tests use it.
5. **`cgpd doctor`** (new, service side) with the checks in 3.5. Update `cgpctl doctor`
   (`cli/cgpctl.py:727-736`, `register_doctor` `:883`): its `socket`, `caller-group`
   and `observer-dir` checks move to the new paths and groups.
6. **Client defaults**: `ctlwire.py:16` (and `CGPCTL_SOCKET` `:17`, remedy `:28`) and
   `tools/observer-live-acceptance.py:31` use the new paths only, with no fallback.
7. **Zipimport bootstrap** (P-7): `python3 -m cgprofile.cli.cgpd install ...` with
   `PYTHONPATH=<cgprofile.whl>:<cli-extended.whl>`. Add a test that imports the install
   and doctor modules with only those two on the path.
8. **Tests to change**:
   - `tests/test_serve_socket_carrier.py`: `:71-72` constants, `:344`, `:1021`, `:1123`,
     the root-only-note test, `allow_uids` `:832-880`;
   - `tests/test_serve_resource_observer.py` (bind, refuse, permission, unlink cases):
     convert to activated-mode cases and keep self-bind cases;
   - `tests/test_docs_sync.py:119-136`, `test_daemon_config.py:393`,
     `test_cgpd.py:238-243`, `:370-372`, `test_ctlwire.py`, `test_cgpd_units.py`,
     `test_deployment_contract.py`.
   - New: an fd-passing test (spawn the daemon with `LISTEN_FDS`/`LISTEN_FDNAMES` and
     pre-bound sockets), two fds with one name (refused), missing-level fd, unknown fd
     name; the P-11 refusal cases.
9. **Docs**: `docs/NATIVE-DEPLOYMENT.md` (`:109`, the deployment story, and the manual
   removal of an old installation from section 4), README, CONSUMERS, CHANGES
   `[Unreleased]` (a breaking change: new paths only).
10. **Legacy 1.x container files** (`ciu.defaults.toml.j2:28`, `:50`,
    `ciu.compose.yml.j2:107-112`): confirm unused; delete or mark legacy.

### 5.2 topos (`topos`, project `topos`, 0.6.0 → 0.7.0 in phase 1)

1. **Activated mode** in `daemon/api.py` `serve_versioned_unix_socket` (`:773-785`) and
   `daemon/broker.py` `serve_unix_socket` (`:573-591`):
   - accept the passed fd by name (`observe`, exactly one);
   - construct the server with `bind_and_activate=False` on the passed socket;
   - do not unlink on close (`broker.py:500-502`) or probe stale sockets
     (`broker.py:37-67`) in activated mode;
   - the unused `mode` kwarg (`api.py:666`) is removed or used.
2. **Paths**:
   - `DEFAULT_DAEMON_SOCKET` (`deploy.py:13`) becomes `/run/topos/observe/topos.sock`;
   - clients use the new path only, with no fallback: `cli.py:26`, `65-70`, `138`,
     `152`, `178`, `187`, `202`, `218`, `244`, guidance at `:350`, `daemon/status.py:24`,
     `126`, `168`, `mcp/server.py:35`, `39`, `466`;
   - `state_dir` default `config.py:166` becomes `/run/topos/state/bpf` (also `:322`,
     `:482`, `:648`; `cli.py:133` help);
   - `DEFAULT_RECOVERY_DIR` (`actions/squeeze.py:123`) becomes
     `/run/topos/state/squeeze`. Records in the old directory are not read; the manual
     removal clears them.
3. **Assets** `src/topos/assets/systemd/`:
   - `topos.service`: drop `Group=topos`, `RuntimeDirectory=topos` and
     `RuntimeDirectoryMode=0750`; add `RuntimeDirectory=topos/state`, mode 0700,
     `RuntimeDirectoryPreserve=yes` and `Sockets=topos-observe.socket`;
   - new `topos-observe.socket` and `topos.sysusers` (`g topos-observe`);
   - delete `topos.tmpfiles`;
   - package them (`pyproject.toml:45-46`).
4. **`toposd` entry point on cli-extended** (P-6), new console script in `pyproject.toml`.
   Verbs:
   - `run`, the same as today's `daemon serve`;
   - `install`, `uninstall`, `doctor`;
   - `unit print|check`, `config check`.
   Replace the `str.replace` rendering (`deploy.py:548-558`) with structured rendering
   from `/etc/topos/toposd.toml`. `preflight_daemon_deployment` (`deploy.py:54`) becomes
   the basis of `toposd doctor`, and `topos daemon preflight|install-plan|status` delegate
   to it with a deprecation line. Zipimport bootstrap as in 5.1.7. The install and
   doctor code lives in a new `topos/src/topos/service/` package whose `__init__`
   imports nothing from `topos.daemon` or `topos.ui` (P-7), and a test imports it with
   only the topos and cli-extended wheels on the path.
   - The venv gets third-party dependencies from the hash-pinned
     `topos-<ver>-daemon.lock` release asset (P-7). The topos cmru release must produce
     and publish that asset: add it to `topos/cmru.toml` artifacts with its generation
     step.
   - `toposd run` gets `--self-bind` (P-9).
   - Check that `net_bpf.py:77`, which reads `state_dir/snapshot.json`, runs only inside
     the daemon, because `/run/topos/state` becomes 0700 root.
5. **Tests to change**:
   - `tests/test_daemon_deploy.py`: `:94`, `:153`, `:159-160`, `:168`, `:204`, `:232-242`,
     `:249`, `:267`, `:289`, `:310`, `:436`;
   - `tests/test_p106_daemon_deploy.py`: remedy strings `:167`, `:281`, `:427`;
   - `tests/test_daemon_broker.py:79-82`;
   - `tests/test_snapshot_and_socket_hardening.py:85`, `:98`;
   - `tests/test_daemon_robustness.py:647`;
   - `tests/test_acceptance.py:~1266`;
   - `tests/test_daemon_bpf_snapshot.py:894-906`;
   - `tests/test_attach_cli.py:231`, `258`, `271`;
   - `tests/test_daemon_cli_deployment_boundary.py`, `tests/test_daemon_p52.py`.
   - New: fd-passing tests as in 5.1.8.
6. **Docs**:
   - `docs/DAEMON.md`: 17, 279, 341, 374, 382-384, 488-498, 511-531;
   - `docs/OPERATIONS.md`: 170, 432;
   - `TUI-SPEC.md:209`;
   - `docs/PLAN-2026-11-CONSOLE.md:29`;
   - `ciu.global.defaults.toml.j2:3`;
   - CHANGES `[Unreleased]`.
7. **Coordination**: the topos console-redesign branch is in review. Rebase this work
   on main after it merges, or agree file ownership with that stream (`cli.py` is hot).

### 5.3 mdt-cockpit host-setup (`mdt-cockpit/host-setup`, phase 2)

1. **Delete** `units/mdt-topos.conf`, `units/mdt-topos-service.conf`,
   `units/mdt-cgprofile.conf`, `units/mdt-cgprofile-observer.conf`,
   `units/mdt-cgprofile.sysusers`.
2. **Delete from `install.sh`**:
   - `provision_cgprofile_observer` (`:150-250`) and its preflight call (`:1051`);
   - the tmpfiles installs (`:1291-1322`), the topos drop-in install (`:1324-1326`);
   - `--observer-only` (`:14`, `:101-104`, `:330-351`).
   **Replace** `--cgprofile-only` (`:355-379`) and the full-install service step
   (`:1519-1530`) with calls to `cgpd install` (zipimport bootstrap from the pinned wheel)
   using the `host-setup.env` values.
3. **No automatic migration.**
   - A run of the new host-setup that enables the cgprofile or topos role refuses while
     any of the old files below exist. Runs for unrelated roles (slices, buildkit, IO
     governance) are not blocked. The files:
     - `/etc/tmpfiles.d/mdt-topos.conf`, `mdt-cgprofile.conf`, `mdt-cgprofile-observer.conf`;
     - `/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf`;
     - `/etc/sysusers.d/mdt-cgprofile.conf`;
     - `/usr/local/sbin/mdt-cgprofile-install.sh` (installed at `:1418-1419`; that
       install step is deleted).
   - The refusal prints the manual removal steps from section 4, and the operator runs
     them in the cutover.
   - The service installers also refuse on their own leftovers (P-11).
4. **New env keys** in `host-setup.env.example` (`:903-947`) and the wizard validators
   (`mdt-host-setup-wizard.py:915-922`, `:1051-1053`, `:1408`):
   - `CGPROFILE_CONTROL_GROUP` (default `cgprofile-control`);
   - `CGPROFILE_OBSERVER_GROUP` (default `cgprofile-observer`);
   - `TOPOS_ENABLE`, `TOPOS_WHEEL`, `TOPOS_WHEEL_SOURCE`, `TOPOS_OBSERVE_GROUP`
     (default `topos-observe`);
   - `CGPROFILE_CONTROL_USERS`, `CGPROFILE_OBSERVER_USERS`, `TOPOS_OBSERVE_USERS`
     (space-separated; default: the invoking host login user, from `SUDO_USER`),
     passed as `--add-user` flags. The operator lists further users, such as the
     Netcup runner user (`scripts/netcup/runner/runner.env.example:12`), explicitly;
     host-setup does not read the runner's env file.
   The wizard validators refuse `docker` (and an existing group whose GID is the docker
   GID) for every `*_GROUP` key, and the same group for two levels, across services
   (P-5). Remove
   `CGPROFILE_*` keys that move into `cgpd install` flags only if they are unused
   afterwards.
5. **`scripts/check.sh`**: replace `:463-488` (cgprofile dir), `:491-590` (observer),
   `:567-587` (topos dir) and the socket part of `:597-709` with `cgpd doctor --json`
   and `toposd doctor --json`, rendered as host-check lines. Keep the pin and `MANIFEST`
   checks only if the doctors do not cover them.
6. **Tests**: remove the moved installer tests and keep the wiring tests
   (`tests/test_cgprofile_install.py:348-399`, `:468-522` adapt). Update
   `test_install_runtime_blocks.py` and `test-render.sh`.
7. **Docs**: README `### Provision the cgprofile observer directory` (`:680`), and
   `### Install, pin and upgrade the cgprofile service` (`:708`) becomes "host services:
   what host-setup passes to each service's install verb". The README's group-access
   text (`:558`, "the existing `--group-add ${localEnv:DOCKER_GID}` runArg is" enough)
   is rewritten for the level groups. Also `CGROUP-NOTES.md:889` and CHANGES.
8. **Host GID exports and the BuildKit group** (P-5, P-5a):
   - Exported variables: `DOCKER_GID`, `MDT_HOST_DOCKER_GID` (same value),
     `CGPROFILE_CONTROL_GID`, `CGPROFILE_OBSERVER_GID`, `TOPOS_OBSERVE_GID` and
     `MDT_BUILDKIT_GID`, resolved with `getent group` on the host for the configured
     group names at each host-setup run.
   - Two routes, because `/etc/profile.d` reaches only sh/bash login shells
     (`host-setup/etc/profile.d/mdt-buildkit.sh:9-11` says so for its own variable):
     - a host-setup-managed block of literal `KEY=VALUE` lines in `/etc/environment`
       (pam_env), which covers ssh sessions (including a Remote-SSH VS Code server),
       sudo and cron;
     - `/etc/profile.d/mdt-host-gids.sh` with the same values, for login shells that
       do not go through pam_env.
     A desktop-launched local VS Code sees neither; that path keeps the manual export
     the templates document today (`templates/devcontainer.json:216-220`), and
     `initializeCommand` names it when the values are missing.
   - A group of a role that is not enabled has no GID. Its variable gets an inert
     value instead, the GID of `nogroup` (`getent group nogroup`, 65534 on Debian; else
     `nobody`; host-setup refuses when neither group exists), so `--group-add` never
     receives an empty string (Dev Containers substitutes an unset `localEnv` as empty,
     and Docker refuses `--group-add ""`). That group conventionally owns nothing
     sensitive (NFS-squashed files are `nobody:nogroup`), so the value grants nothing
     of note; `check.sh` warns if a socket or directory under `/run` has that group. A
     user's own primary GID would not do: `/etc/environment` applies to every user.
   - add `g mdt-buildkit` to host-setup's sysusers file and `MDT_BUILDKIT_USERS` (same
     default as above). Every other host user that builds through `mdt-managed`
     (`mdt-buildkit.sh` sets `BUILDKIT_HOST` for all login shells) must be listed
     there, or loses access at the cutover; the README says so.
   - `units/mdt-buildkitd.service.in:112`: chgrp the socket to `mdt-buildkit`, verify
     against its GID instead of `docker`, and reword the final error ("did not become
     safe and docker-group-accessible");
   - `check.sh` checks the BuildKit socket group the same way, and the cross-service
     distinct-group rule (P-5);
   - tests: `host-setup/tests/test-render.sh:251-254`, `:278` pin `chgrp docker` and
     move to the new group; the unit render tests too.

### 5.4 mdt-cockpit devcontainer templates and vbpub `.devcontainer` (phase 2)

1. **Mounts**:
   - `templates/devcontainer.json:96`, `templates/host-folder/devcontainer.json:111`
     and `.devcontainer/devcontainer.json:96` become
     `source=/run/cgprofile/control,target=/run/cgprofile/control,type=bind`;
   - topos `-v` (`templates/devcontainer.json:242`, host-folder `:266`,
     `.devcontainer` `:250`) becomes `/run/topos/observe:/run/topos/observe:ro`. Keep the
     `-v` (optional) form and its comment (`:229-241`).
   - Also bind `/run/cgprofile/observer` read-only. run-gate's observer preflight runs
     in the devcontainer and `lstat`s that directory and socket (`run_gate.py:8757-8763`)
     before it mounts them into a lane. `lstat` needs only traversal of the 0755
     directory, no connect, so the devcontainer does not get the observer GID and
     cannot connect there; the lane gets it (5.5).
   - Rewrite the comments that say the sockets ship `root:docker` (`:84-88`,
     `:238-241`, and the host-folder and `.devcontainer` copies).
   **Group access: one numeric `--group-add` per level** (D2). Container group tables
   do not know the host's groups, so names are useless; the GIDs come from the host:
   - `runArgs` keeps `--group-add ${localEnv:DOCKER_GID}` (docker socket access only)
     and adds `--group-add ${localEnv:CGPROFILE_CONTROL_GID}`,
     `--group-add ${localEnv:TOPOS_OBSERVE_GID}` and
     `--group-add ${localEnv:MDT_BUILDKIT_GID}`;
   - containerEnv gets `MDT_HOST_DOCKER_GID: ${localEnv:DOCKER_GID}` for run-gate and
     assay (P-5). It is the host's docker GID; the container-side `MDT_DOCKER_GID`
     that `finalize_container_environment.py:41`, `:745` exports is a different
     variable and stays as it is;
   - the values come from host-setup's exports (5.3.8). Dev Containers resolves
     `${localEnv:...}` from the environment of the process that reads
     devcontainer.json (the VS Code server or the `devcontainer` CLI), and
     `initializeCommand` cannot change that environment. It reads the same variables
     from its own `os.environ` and verifies them (item 3).
2. **Declared sockets**: new containerEnv key `MDT_HOST_SERVICE_SOCKETS`, for example
   `cgprofile/control:required topos/observe:optional`.
3. **`templates/initialize_container_environment.py`**: new
   `verify_host_service_sockets`, called in `main()` after `verify_host_cgroup_slices`
   (`:487-548`, call site `~:782`), in the same stderr `[mdt-bootstrap] ERROR` /
   return-1 style. First the GIDs: for each level, `getent group <name>` on the host
   must match the `*_GID` variable in its environment (the same group names
   host-setup uses, read from `host-setup.env` when present, else the defaults).
   - A missing variable, or one that differs from `getent`, is an error with the
     remedy "log out and in again on the host, or run host-setup; a desktop-launched
     VS Code needs the exports in its own environment" (the `--group-add` would
     otherwise get an empty or stale value).
   - A level whose group does not exist on the host is an error only when that level
     is declared `required`; for an `optional` one the variable must equal the inert
     value (5.3.8), which is accepted with a warning. Any other value is stale and an
     error.
   - A level GID equal to the docker GID is an error (P-5).
   Then, for each declared socket:
   - path exists and is a socket;
   - its gid is the GID of that level's group, never the docker GID;
   - `connect()` succeeds when the host user can.
   Required: error. Optional: warning. It also checks a floor on the in-container
   `cgpctl`/`topos` versions: an older client in an image looks for the old socket
   names, which no longer exist.
4. **In-container**: optionally add a `cgpctl doctor`/`toposd doctor --check connect`
   line to the finalize summary (`scripts/finalize_container_environment.py` already
   lists both CLIs at `:261`).
5. **Tests and docs**: `mdt-cockpit/scripts/test_devcontainer_audit.py:158-161` pins
   the single `DOCKER_GID` runArg and its export comment and moves to the new runArgs;
   template comments, `templates/README.md`, `docs/DEVCONTAINER-MECHANISMS.md:54`,
   `docs/CONSUMERS.md:540`, CHANGES.

### 5.5 run-gate-project (phase 1)

1. `PROFILE_SOCKET_PATH_DEFAULT` (`run_gate.py:727`; uses at `:1724`, `:1738`,
   `:3806`, `:4014`, `:6068`, `:15425`, `:15781`, `:15989`) becomes the new path only.
2. **Observer mount** (`:506-512`). run-gate bind-mounts the observer **directory**, so
   the file name inside the lane is the host's. The in-lane constant becomes
   `/run/cgprofile-observer/cgprofile.sock`, in step with assay (5.6). There is no
   fallback.
3. **Observer preflight** (`:8750-8800`; `:2802` is only an error message). Phase 1
   replaces three rules and adds one:
   - `:8779` "the socket gid equals its directory's gid" goes away (the new directories
     are `root:root`).
   - `:8785-8793` derive the docker gid from `/run/cgprofile` and require mode exactly
     0770. Replace this with: the docker gid is the numeric `MDT_HOST_DOCKER_GID` from
     the host launcher (P-5); when it is absent or not a positive integer, the
     preflight fails naming the variable. The observer socket's gid is non-zero and
     differs from both the docker gid and the control socket's gid.
   - `--group-add observed.st_gid` keeps using the socket's gid.
   - New, for the control level: wherever run-gate connects to the control socket
     (`PROFILE_SOCKET_PATH_DEFAULT` users above, e.g. the version preflight at
     `:15987`), it first checks that the socket's gid is non-zero, differs from the
     docker gid, and is one of the process's supplementary groups, or that the process
     runs as uid 0 (the runner container runs `--user 0:0`). A control socket owned by
     the docker group is refused, not
     accepted as an equivalent: control == docker is no longer a valid setup. An
     EACCES names the `cgprofile-control` group and the remedy (recreate the
     devcontainer with its `--group-add`, 5.4).
   - The `socket_unreachable_cause` hint (`:2800-2803`) tells the user to bind-mount
     `/run/cgprofile`; it names `/run/cgprofile/control` instead.
   - `:8832` compares the daemon-advertised observer path (`serve.py:1018`) with the old
     constant. Compare it with the new path.
   - The rules target the new layout only. A phase-1 run-gate on a host that has not
     been cut over fails its preflight with a message naming the cutover, so lanes on
     that host stay on their previous run-gate pin (section 4).
4. Docs: `CONSUMERS.md`, `SPEC.md`, `README.md`, CHANGES.

### 5.6 assay tools (phase 1)

- **Source path.** In `tools/resource-observer-gid.sh:6`, `:57`,
  `tools/tester-unified-gate.sh:38-46`, `:1514` and
  `tools/self-qualification-container.sh:25-42`, `:351`, `:562`, the observer source
  path becomes `/run/cgprofile/observer/cgprofile.sock`, with no fallback.
- **In-container basename.** A directory bind keeps the host file name, so the
  container-side default of `ASSAY_RESOURCE_OBSERVER_SOCKET` becomes
  `/run/cgprofile-observer/cgprofile.sock`. `src/assay/resource_limits.py:1088` reads
  that variable. Keep it in step with run-gate (5.5.2).
- **Docker GID.** `assay_host_docker_gid` (`resource-observer-gid.sh:56-75`) drops the
  `/run/cgprofile` directory fallback (mode 0770, gid = docker). It reads
  `ASSAY_GATE_HOST_DOCKER_GID`, else `MDT_HOST_DOCKER_GID`, and fails naming both when
  neither is a positive integer. Its directory argument goes away, and the callers
  stop passing `/run/cgprofile` (`tester-unified-gate.sh:46`, `:1514`;
  `self-qualification-container.sh:42`, `:351`). On a plain host shell the value comes
  from host-setup's exports (5.3.8).
- **Directory gid rule.** Drop `socket_gid != parent_gid` (`resource-observer-gid.sh:38-41`):
  the new observer directory is `root:root 0755`, so the rule would fail on every
  healthy host (the run-gate twin at `run_gate.py:8779` goes too, 5.5.3).
- The observer rule at `:44-47` stays, and a control socket whose gid is the docker
  gid is refused the same way.
- **Docs.** `docs/CONSUMERS.md:538` ("never mount the control socket") stays true; update
  the basename there.

### 5.7 Netcup runner (phase 2)

`scripts/netcup/runner/start-cockpit-runner.sh`:
- `:19` bind-mounts `/run/cgprofile/control` and `/run/cgprofile/observer` (read-only)
  instead of the whole `/run/cgprofile`;
- next to the docker `--group-add` (`:14`), add numeric `--group-add` values from the
  host's `getent group` for `cgprofile-control`, `topos-observe` (when topos is
  mounted) and `mdt-buildkit`, and `-e MDT_HOST_DOCKER_GID=<docker gid>`. The script
  runs on the host, so it resolves the names itself; it fails when a group is missing
  or its GID equals the docker GID.

### 5.8 debian-install-v2 (optional, after phase 2)

A `run_host_services` component in the existing `run_*` style (`config.py:76-152`,
validation `:334-503`, stage call next to `_install_docker` in `_stage1` `:4723-4745`
or in `_stage2` `:4879`):
- config `host_services` lists the services with wheel pins, level group names (P-5
  rules, validated in `config.py`) and users to add per level;
- the stage calls `<svc> install --yes` through the zipimport bootstrap;
- the `vanilla` profile keeps it off (README `:100-113`).
It has no cmru project; bump its `VERSION`.

### 5.9 cli-extended (optional)

If cgpd and toposd end up with the same "resolve value from flag, install record, or
prompt on TTY" code, upstream it as a small helper next to `PromptAPI` (e.g.
`resolve_or_prompt`) with tests, and release a 0.x minor. Do not block phases 1 and 2
on it.

### 5.10 modern-debian-tools-python-debug: retired (operator, 2026-11-05)

The operator retired it; `mdt-cockpit` is its successor. It holds older copies of
`check.sh`, `mdt-cgprofile.conf` and `mdt-cgprofile.sysusers`, so a host installed from it
would reintroduce the foreign rules that the cutover removes.

The retirement runs as its own package on branch `retire-mdt-legacy-20261105`:
- remove the directory and its cmru registration;
- repoint the live references;
- move `nyxloom/nyxloomd/Dockerfile`'s base image to the mdt-cockpit image.

Its merge to main is a **gate for phase 2**. It is not part of this plan's packages.

## 6. Landing (answer to "one commit on main?")

No single commit. Use one integration branch per phase, with one commit per tool. Each
tool's change is reviewed and gated on its own, and the phase lands on main as **one
`--no-ff` merge**. Main therefore changes all tools of a phase at the same moment, and
each tool's cmru release range stays scoped to its own project. Releases follow
`cmru.orchestration.toml` order.

Deployment safety does not come from commit atomicity (hosts run releases). It comes
from section 4:
- services, clients and consumers are released before any host changes;
- each host then switches in one drained cutover, with its lanes' run-gate/assay pins
  and its devcontainers moved in the same step.

## 7. Acceptance per phase

- **Phase 1**: in a VM-harness host with no mdt host-setup:
  - `toposd install` and `cgpd install` (wizard run on a TTY, and a non-interactive run
    with flags) produce green doctors;
  - a user added to `cgprofile-observer` only can use the observer socket and gets
    EACCES on control (doctor reports `connect` for control as `skip`);
  - `systemctl restart <service>` keeps every socket inode (`stat -c %i` before and
    after) and the daemon answers on each level afterwards;
  - `systemctl restart <svc>-<level>.socket` restarts the service as well, and the
    daemon answers on the new inode;
  - a start without `LISTEN_FDS` and without `--self-bind` is refused;
  - the old paths do not exist after install;
  - install on a VM prepared with the old host-setup layout (old tmpfiles rules,
    `/run/cgprofile 0770 root:docker`, old `cgpd.toml` keys) is refused, and lists the
    manual removal; after the manual removal it succeeds;
  - a downgrade below the socket-unit release is refused without `--allow-downgrade`;
  - `install` with any level group set to `docker`, to a group with the docker GID or
    GID 0, or to the other level's group is refused with exit 2, from flags, from an
    install record and from a wizard answer;
  - `--add-user` adds the user to the level group, and a re-run without it keeps the
    membership;
  - uninstall leaves nothing from the list in 3.5: no service, slice or socket unit, no
    sysusers file, no `/etc/<svc>`, no `/opt/<svc>`, no socket node, level directory or
    state directory, and no failed units. Groups survive without `--remove-groups`, and
    `/var/lib/cgprofile` survives without `--purge-data`.
  - New run-gate and assay releases pass their observer and control preflight on the
    VM host, and fail with the cutover message on a host with the old layout. They
    refuse a control or observer socket chgrped to the docker group, and fail when
    `MDT_HOST_DOCKER_GID` is missing.
- **Phase 2**:
  - the new host-setup refuses on a VM with its old files and lists the manual removal;
  - after the removal it installs both services through their install verbs;
  - a devcontainer from the new templates has only the level directories mounted, and
    its supplementary groups are the docker GID plus the `cgprofile-control`,
    `topos-observe` and `mdt-buildkit` host GIDs of the enabled roles (inert values
    for the others), never the observer GID;
  - a host with topos not enabled still starts a devcontainer, with a warning;
  - a devcontainer started through a Remote-SSH session and one started with the
    `devcontainer` CLI from a non-login shell both get every `*_GID` value;
  - the BuildKit socket has group `mdt-buildkit` and mode 0660, and a build
    through `mdt-managed` works from the devcontainer;
  - `initializeCommand` fails when a `*_GID` variable is missing or differs from the
    host's `getent group`;
  - `initializeCommand` fails for a declared-required socket that is absent, warns for
    an optional one, and refuses an in-container client below the floor.
- **Phase 3**, per host (r1002, then v1001, then the controller host):
  - the drain completed before any stop;
  - no `mdt-topos*` or `mdt-cgprofile*` tmpfiles rules or drop-ins remain;
  - the directories have the 3.1 modes;
  - no socket under `/run/cgprofile`, `/run/topos` or `/run/mdt-buildkitd` has the
    docker GID, and the host login user and runner user are members as configured;
  - `foreign-rules` is ok and `check.sh` is green through the doctors;
  - a lane on the phase-1 run-gate/assay pins passes its preflight;
  - a recreated devcontainer reaches both sockets.

## 8. Execution notes for the distributing agent

- Routing (operator, 2026-11-05):
  - implementation goes to Codex `gpt-6-luna` effort max via
    `/home/vscode/vbpub-release-program-20261027/codex/codex-impl.sh`, with briefs from
    `IMPLEMENTER-BRIEF-TEMPLATE.md`;
  - reviews go to Sonnet agents;
  - host, ssh, docker and gate work goes to Sonnet agents or the controller;
  - escalation goes to Opus.
- Host load rule: shared with a production game server. Run serial pytest under
  nice/ionice, one gate container at a time.
- Live hosts are r1002 and v1001 only, never nano1. A cgpd restart drains live
  sessions first; never force without the operator.
- Remove containers by exact name only. Inspect only the task's named containers.
- Every merged change gets an independent review; "docs-only" is not an exemption.
- Each package's brief must carry the exact seams above, re-verified against current
  main.
