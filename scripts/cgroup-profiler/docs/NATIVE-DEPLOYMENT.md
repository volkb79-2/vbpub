# cgprofile native deployment

This guide covers the standalone cgprofile wheel, systemd socket activation,
`cgpd install` / `uninstall`, upgrades, and the phase-1 cutover contract. The
user-facing feature list is in [`README.md`](../README.md); the rationale for
the socket ownership model is in [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md); worked
commands are in [`CONSUMERS.md`](CONSUMERS.md).

## 1. Rollout boundary

cgprofile 2.2.0 is the phase-1 service and client release. It serves only the
new per-level layout below. It does not probe or serve old paths and it does
not migrate an mdt host-setup installation. The install command refuses old
state and prints the operator's manual removal list.

Phase 1 is not deployed to cockpit hosts. Keep `CGPROFILE_WHEEL` below
`2.2.0` until phase 2 updates mdt host-setup and the operator performs each
host's drained cutover. Old and new clients and services do not interoperate.

## 2. Runtime layout and access

```text
/run/cgprofile/                         root:root 0755
├── control/                            root:root 0755
│   └── cgprofile.sock                  root:cgprofile-control 0660
├── observer/                           root:root 0755
│   └── cgprofile.sock                  root:cgprofile-observer 0660
└── state/                              root:root 0700 (systemd RuntimeDirectory)
    └── damon.lock
```

Each socket has exactly one `ListenStream=` in its own systemd socket unit.
The units pass descriptors named `control` and `observer`; the daemon adopts
one descriptor per name. The service never binds, changes permissions on, or
unlinks an activated socket. It also clears the activation environment before
starting workers. If one descriptor is absent, cgpd logs the missing level
and continues to serve the other one.

The config is `/etc/cgprofile/cgpd.toml`:

```toml
[sockets.control]
path = "/run/cgprofile/control/cgprofile.sock"
group = "cgprofile-control"
mode = "0660"

[sockets.observer]
path = "/run/cgprofile/observer/cgprofile.sock"
group = "cgprofile-observer"
mode = "0660"

damon_lock = "/run/cgprofile/state/damon.lock"
```

The socket keys are fixed at service start. `sockets.control.group` may map
to an existing group such as `docker`; `sockets.observer.group` must be a
different group and its GID must not equal the docker GID. The installer
creates the default groups through the rendered sysusers file and can add
named users to them. Access to the observer group grants read-only observer
operations, not control access.

`cgpd.service` requires and follows both socket units, uses
`RuntimeDirectory=cgprofile/state` with mode `0700` and preservation enabled,
and keeps `/run/cgprofile/state` in `ReadWritePaths`. The socket directories
are created by systemd from the unit paths and `DirectoryMode=0755`. The VM
acceptance must verify the actual directory ownership and modes on the target
systemd version; do not infer the parent-directory behavior from unit text.

## 3. Install and upgrade

The installer is available as `cgpd install` from an installed wheel, and as a
zipimport bootstrap from the two pinned wheels. It accepts pins of the form
`<version>@sha256:<64 lowercase hex>`. The wheel source defaults to the
project release base and may be an HTTPS release base or a directory holding
both wheel files.

For an unattended install, acquire the two wheel files and run:

```bash
CGPROFILE_WHEEL='./cgprofile-2.2.0-py3-none-any.whl'
CLI_EXTENDED_WHEEL='./cli_extended-X.Y.Z-py3-none-any.whl'
CGPROFILE_PIN='2.2.0@sha256:<cgprofile-sha256>'
CLI_EXTENDED_PIN='X.Y.Z@sha256:<cli-extended-sha256>'

PYTHONPATH="$CGPROFILE_WHEEL:$CLI_EXTENDED_WHEEL" \
  python3 -m cgprofile.cli.cgpd install \
  --wheel "$CGPROFILE_PIN" \
  --cli-extended-wheel "$CLI_EXTENDED_PIN" \
  --wheel-source ./wheels \
  --control-group cgprofile-control \
  --observer-group cgprofile-observer \
  --enable --yes
```

Replace both example pins with the release's verified values. For a TTY run,
`cgpd install` prompts for missing pins, group creation or mapping, users, and
whether to enable the service. A non-interactive run must provide each
required value explicitly; use `--no-enable` for an install that should not
start the service.

The installer performs these steps:

1. Validate both pins and the old-installation preflight.
2. Fetch both wheels, verify SHA-256 before unpacking, and create an immutable
   `/opt/cgprofile/<version>/venv` with a `MANIFEST` recording both digests and
   `installer=cgpd install <version>`.
3. Render the config and run `cgpd config check`; render service, slice, socket
   and sysusers resources with `cgpd unit print`.
4. Compare version, config and each resource with the installation. If a
   restart is needed, poll live sessions until drained or the requested
   deadline. A timeout exits 3 and records `PENDING`; inspect the sessions,
   remove that marker manually, and retry. `--force-restart` requires explicit
   confirmation and reports the sessions it may abort.
5. Atomically write config and resources, update `current`, run
   `systemd-sysusers`, reload systemd, and restart only the changed socket
   units or service as required.
6. Wait for the selected daemon version and clean config status, record the
   install inputs, and prune older version directories.

A downgrade is refused unless `--allow-downgrade` is supplied. When rolling
back below the socket-unit release, the installer disables and removes the
socket units and their nodes before starting the older service. That is an
explicit rollback of the whole host stack; clients on the new paths do not
fall back to old paths.

### Old-installation refusal and manual removal

`cgpd install` refuses if it finds any of the following: `ctl_socket` or
`observer_socket` in the config; tmpfiles rules or service drop-ins outside
cgprofile that claim the service; `/run/cgprofile` or its expected level/state
directories with the wrong type, owner or mode; a version `MANIFEST` naming
`installer=mdt host-setup (MDT-055)`; or leftover `/opt/cgprofile/PENDING` or
`current.new`, or an old-layout socket node at `/run/cgprofile/ctl.sock` or
`/run/cgprofile/observer/resource-observer.sock`. It does not chmod, chown,
migrate, or delete those findings.
The refusal prints the matching paths and this manual cleanup list:

- Remove `/etc/tmpfiles.d/mdt-cgprofile.conf` and
  `/etc/tmpfiles.d/mdt-cgprofile-observer.conf`.
- Remove host-setup drop-ins in `/etc/systemd/system/cgpd.service.d/`.
- Remove `/etc/sysusers.d/mdt-cgprofile.conf`; keep the
  `cgprofile-observer` group and its GID.
- Remove `/usr/local/sbin/mdt-cgprofile-install.sh`, the old
  `/opt/cgprofile` tree, and `/usr/local/bin/cgpctl` if it is the old link.
- Remove old `/etc/systemd/system/cgpd.service` and
  `/etc/systemd/system/cgprofile.slice` copies, plus the old
  `/etc/cgprofile/cgpd.toml`.
- Run `systemctl daemon-reload`, then remove `/run/cgprofile` after stopping
  the old daemon. Keep `/var/lib/cgprofile` unless data purge is intended.

Do not run the new install over an old host-setup tree. After manual removal,
install fresh from the pinned wheels.

## 4. Uninstall and doctor

`cgpd uninstall` stops and disables the service and both socket units, then
removes the service, slice and socket unit files, sysusers file, config and
install record, `/opt/cgprofile`, the owned `/usr/local/bin/cgpctl` link,
socket nodes, level directories, and `/run/cgprofile/state`. It reloads
systemd and resets failed unit state. Groups and memberships remain unless
`--remove-groups` is given; `/var/lib/cgprofile` sessions and overrides remain
unless `--purge-data` is given. `cgpd uninstall --dry-run` prints the plan.

`cgpd doctor` reports the following checks, each with a remedy on failure:

`config-valid`, `units-current`, `groups`, `socket-units`, `socket-nodes`,
`socket-dirs`, `state-dir`, `service`, `connect`, `foreign-rules`, and
`caller-access`. A caller outside one socket group gets `skip` for that
level's connection probe. A non-root caller who cannot read a required path
gets `skip` with a run-as-root note. `cgpctl doctor` keeps its client checks
and uses the new control and observer paths.

## 5. Phase-1 VM acceptance

Run these checks in the existing Debian VM harness, not on a cockpit host.
Use a fresh VM without mdt host-setup for the successful install, and a
separate disposable VM prepared with the old files for the refusal case. The
controller runs the VM commands after the reviewed code is available:

```bash
cd scripts/debian-install-v2/testing/vm
REPO_ROOT="$(git rev-parse --show-toplevel)"
export MDT_VM_SOURCE_DIR="$REPO_ROOT"
./run-vm-harness.sh prepare-base case-b
./run-vm-harness.sh start cgprofile-sockets --case case-b
./run-vm-harness.sh wait cgprofile-sockets
```

Copy the two pinned wheels into the VM, then verify both the TTY wizard and a
non-interactive install using explicit flags. After install, run:

```bash
sudo cgpd doctor --json
sudo cgpctl doctor --json
sudo stat -c '%n inode=%i mode=%a owner=%U:%G' \
  /run/cgprofile /run/cgprofile/control /run/cgprofile/observer \
  /run/cgprofile/state /run/cgprofile/control/cgprofile.sock \
  /run/cgprofile/observer/cgprofile.sock
sudo systemd-analyze verify \
  /etc/systemd/system/cgpd.service \
  /etc/systemd/system/cgprofile-control.socket \
  /etc/systemd/system/cgprofile-observer.socket
```

Then prove the service and socket restart behavior, comparing socket inodes
before and after each command:

```bash
sudo systemctl restart cgpd.service
sudo cgpctl status --json
sudo systemctl restart cgprofile-control.socket
sudo cgpctl status --json
sudo systemctl restart cgprofile-observer.socket
sudo cgpd doctor --check connect --json
```

Also verify that a direct `cgpd run` with `LISTEN_FDS` unset and without
`--self-bind` exits 2, while `cgpd run --self-bind` accepts explicitly
provisioned test paths. Create a user in `cgprofile-observer` only; that user
must complete the observer health probe and receive `EACCES` on control, with
`cgpd doctor` reporting control `connect` as `skip`.

On the old-layout VM, run the same install command and verify exit 2 plus the
manual removal paths. After removing those files and runtime tree manually,
verify install succeeds. Also verify a rollback below the socket-unit release
fails without `--allow-downgrade`; do not run an actual rollback as part of
this probe. Finally, run `cgpd uninstall` and assert no service, slice or
socket unit, sysusers file, `/etc/cgprofile`, `/opt/cgprofile`, socket node,
level directory, state directory, or failed unit remains. Confirm the groups
and `/var/lib/cgprofile` remain by default.

The plan's phase-1 acceptance also covers the new Run-Gate and Assay releases
against the new paths and their cutover refusal on an old-layout host. Those
consumer preflights are owned by their respective phase-1 packages.
