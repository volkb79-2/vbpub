# cgprofile native deployment

This guide covers the standalone cgprofile wheel, systemd socket activation,
`cgprofiled install` / `uninstall`, upgrades, and the phase-1 cutover contract. The
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
starting workers. If one descriptor is absent, cgprofiled logs the missing level
and continues to serve the other one.

The config is `/etc/cgprofile/cgprofiled.toml`:

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

The socket keys are fixed at service start. Both level groups must be dedicated:
neither may be named `docker`, resolve to GID 0 or the docker GID, or resolve to
the other cgprofile level's GID. `config check` and `install` enforce this when
the groups exist. The same refusal applies to flags, the saved install record,
an existing config, and wizard answers; there is no override. Cross-service
group distinctness is an estate check owned by host-setup, not cgprofiled's installer.
The installer creates the default groups through the rendered sysusers file.
Access to the observer group grants read-only observer operations, not control
access.

Use repeatable `--add-user LEVEL=USER` flags to add existing accounts to a
level group. The installer validates each account, adds it with `gpasswd -a`,
and records the requested members in `install.toml`. Additions are cumulative:
reruns keep the recorded users and append new ones. Membership takes effect at
the user's next login or after a restarted service. Uninstall never removes
users from groups individually; groups remain unless `--remove-groups` is
requested, which deletes only cgprofiled-created groups.

`cgprofiled.service` requires and follows both socket units, uses
`RuntimeDirectory=cgprofile/state` with mode `0700` and preservation enabled,
and keeps `/run/cgprofile/state` in `ReadWritePaths`. The socket directories
are created by systemd from the unit paths and `DirectoryMode=0755`. The VM
acceptance must verify the actual directory ownership and modes on the target
systemd version; do not infer the parent-directory behavior from unit text.
`ExecStartPre` runs `cgprofiled -t`, the config-test alias for `cgprofiled config check`;
it exits non-zero for invalid config before systemd starts the daemon.

## 3. Install and upgrade

The installer is available as `cgprofiled install` from an installed wheel, and as a
zipimport bootstrap from the two pinned wheels. It accepts pins of the form
`<version>@sha256:<64 lowercase hex>`. The wheel source defaults to the
project release base and may be an HTTPS release base or a directory holding
both wheel files.

After installation, `cgprofiled` remains in the active versioned venv at
`/opt/cgprofile/current/venv/bin/cgprofiled`; the installer does not add a global
`cgprofiled` symlink. `cgprofilectl` is linked into `/usr/local/bin` for client use.

For an unattended install, acquire the two wheel files and run:

```bash
CGPROFILE_WHEEL='./cgprofile-2.2.0-py3-none-any.whl'
CLI_EXTENDED_WHEEL='./cli_extended-X.Y.Z-py3-none-any.whl'
CGPROFILE_PIN='2.2.0@sha256:<cgprofile-sha256>'
CLI_EXTENDED_PIN='X.Y.Z@sha256:<cli-extended-sha256>'

PYTHONPATH="$CGPROFILE_WHEEL:$CLI_EXTENDED_WHEEL" \
  python3 -m cgprofile.cli.cgprofiled install \
  --wheel "$CGPROFILE_PIN" \
  --cli-extended-wheel "$CLI_EXTENDED_PIN" \
  --wheel-source ./wheels \
  --control-group cgprofile-control \
  --observer-group cgprofile-observer \
  --add-user "control=$USER" \
  --enable --yes
```

Replace both example pins with the release's verified values. Each input comes
from its flag, else from `/etc/cgprofile/install.toml` written by the previous
run, else (on a TTY only) from a prompt: `cgprofiled install` asks for missing pins,
group creation or mapping, users for each level, and whether to enable the service. A
non-interactive run must provide each required value explicitly; use
`--no-enable` for an install that should not start the service. A rerun with no
flags therefore reapplies the recorded inputs, users included. `install` and
`uninstall` ask for confirmation; `--yes` accepts it, and a pipe without
`--yes` is refused.

`/etc/cgprofile/cgprofiled.toml` is the operator's file once it exists. The
installer creates it only when it is absent. On a rerun a socket group passed
as a flag (`--control-group`, `--observer-group`) is applied; with no flag the
file's value wins over the install record, so a hand edit is never reverted,
and the record is updated from it. The file is kept byte for byte unless a
flag changes something; then only the socket groups and the keys passed as
flags (`--gates-slice`, `--observe-slices`, `--allow-uids`) are merged, the
file is rewritten from its parsed keys (comments are not preserved), and the
install output lists what changed as `config_changes`. The socket paths are
fixed by the layout, so a file that sets another path is refused. A dry run
never prompts and needs no `--enable`/`--no-enable`. `--config` is not supported for `install` and
`uninstall`: they manage `/etc/cgprofile/cgprofiled.toml` only.

Each level group must be dedicated: its name cannot be `docker`, its GID cannot
be 0 or the docker GID, and the control and observer GIDs must differ. Both
`cgprofiled config check` and `cgprofiled install` enforce this rule. Invalid saved records
and existing config are refused even when a flag supplies a replacement; the
wizard validates each answer and the pair before applying. No override exists.
The installer checks only cgprofile's own levels; host-setup checks uniqueness
across services. Repeat `--add-user LEVEL=USER` to add existing users. The
installer validates each account, runs `gpasswd -a`, stores the names in
`install.toml`, and adds recorded users on later installs. Dry-run output lists
each requested membership. Memberships are never individually removed;
`--remove-groups` explicitly deletes only groups created by cgprofiled.

The installer performs these steps:

1. Validate both pins and the old-installation preflight, including that an
   existing `/usr/local/bin/cgprofilectl` is the installer's own link (anything else
   is refused, never replaced). `/opt/cgprofile/PENDING` is written now.
2. Fetch both wheels, verify SHA-256 before unpacking, and create an immutable
   `/opt/cgprofile/<version>/venv` with a `MANIFEST` recording both digests and
   `installer=cgprofiled install <version>`.
3. Render the config and run `cgprofiled config check`; render service, slice, socket
   and sysusers resources with `cgprofiled unit print`.
4. Compare version, config and each resource with the installation. If a
   restart is needed, poll live sessions until drained or the requested
   deadline. A timeout exits 3 and leaves `PENDING`; rerun `cgprofiled install` when
   the sessions have ended. `--force-restart` skips the drain (the verb's
   usual `--yes`/confirmation still applies) and reports the sessions it may
   abort; when the session list cannot be read at all (a wedged daemon, a lost
   socket, a missing `current`) it still proceeds and prints a warning that
   the sessions are unknown. A failure up to this point (fetch,
   pin, config check) removes `PENDING` again, because nothing on the host
   has changed yet.
5. Atomically write config and resources, update `current`, run
   `systemd-sysusers`, reload systemd, and restart only the changed socket
   units or service as required. The groups `systemd-sysusers` is about to
   create are written to `PENDING` (`created_groups=`) before it runs, so
   `cgprofiled uninstall --remove-groups` finds them even if the install fails
   before `install.toml` exists. The wheels are installed with `--no-index
   --no-deps`: exactly the two pinned wheels. From here a failure leaves
   `PENDING` in place; with an install record the next `cgprofiled install` re-applies (it
   reloads and restarts unconditionally), otherwise it refuses and points to
   `cgprofiled uninstall --yes`.
6. Wait for the selected daemon version and clean config status, prune older
   version directories, write `install.toml`, and remove `PENDING` last.

A downgrade is refused unless `--allow-downgrade` is supplied. When rolling
back below the socket-unit release, the installer disables and removes the
socket units and their nodes before starting the older service. That is an
explicit rollback of the whole host stack; clients on the new paths do not
fall back to old paths.

### Old-installation refusal and manual removal

`cgprofiled install` refuses if it finds any of the following: `ctl_socket` or
`observer_socket` in the config; tmpfiles rules or service drop-ins outside
cgprofile that claim the service; `/run/cgprofile` or its expected level/state
directories with the wrong type, owner or mode; a version `MANIFEST` naming
any `installer=mdt...` installer, or an `/opt/cgprofile/<version>` tree without
a cgprofiled `MANIFEST`; cgprofiled or cgprofile units without an install record; leftover
`/opt/cgprofile/PENDING`, `current.new` or a staging directory (with an
install record, `PENDING` and `current.new` mean "re-apply" and are accepted);
or an old-layout socket node at `/run/cgprofile/ctl.sock` or
`/run/cgprofile/observer/resource-observer.sock`. It does not chmod, chown,
migrate, or delete those findings. Leftovers under `/opt/cgprofile` that come
from an unfinished cgprofiled install get their own message: review them and rerun,
or run `cgprofiled uninstall --yes`.
The refusal prints the matching paths and this manual cleanup list:

- Remove `/etc/tmpfiles.d/mdt-cgprofile.conf` and
  `/etc/tmpfiles.d/mdt-cgprofile-observer.conf`.
- Remove host-setup drop-ins in `/etc/systemd/system/cgprofiled.service.d/`.
- Remove `/etc/sysusers.d/mdt-cgprofile.conf`; keep the
  `cgprofile-observer` group and its GID.
- Remove `/usr/local/sbin/mdt-cgprofile-install.sh`, the old
  `/opt/cgprofile` tree, and `/usr/local/bin/cgprofilectl` if it is the old link.
- Remove old `/etc/systemd/system/cgprofiled.service` and
  `/etc/systemd/system/cgprofile.slice` copies, plus the old
  `/etc/cgprofile/cgprofiled.toml`.
- Run `systemctl daemon-reload`, then remove `/run/cgprofile` after stopping
  the old daemon. Keep `/var/lib/cgprofile` unless data purge is intended.

Do not run the new install over an old host-setup tree. After manual removal,
install fresh from the pinned wheels.

## 4. Uninstall and doctor

`cgprofiled uninstall` only touches a host that cgprofiled install owns: it needs
`/etc/cgprofile/install.toml` or `/opt/cgprofile/PENDING`. On any other host
it either reports "nothing installed by cgprofiled install" or, when it finds an old
host-setup install, unknown files under `/opt/cgprofile` or `/run/cgprofile`,
or an old-layout path, refuses before changing anything and prints the manual
removal list. On an owned host it stops and disables both socket units and the
service, stops `cgprofile.slice`, removes the service, slice and socket unit
files, sysusers file, a leftover mdt drop-in, the owned `/usr/local/bin/cgprofilectl`
link, socket nodes, level directories and `/run/cgprofile/state`, then runs
`daemon-reload` and `reset-failed`. Then it removes groups created by the
install (`--remove-groups`; a group that no longer resolves is skipped, and a
group that cannot be removed, for example because it is a user's primary
group, is reported at the end while everything else still goes; the install
record stays for manual cleanup), the
data directory (`--purge-data`), the config, `/opt/cgprofile` and finally the
ownership markers `PENDING` and `install.toml`, preserving the group list until
cleanup is complete. If interrupted while the running version tree still
exists, uninstall can be rerun from it; if the version tree was already
removed, use the manual cleanup steps below. The version tree the command
runs from (`/opt/cgprofile/current/venv/bin/cgprofiled uninstall --yes`) is removed
after the result is printed. If this deferred removal fails, cgprofiled prints
manual steps: remove any remaining `/opt/cgprofile` version tree and `current`
link, run `groupdel` for any requested group that still exists, then remove
`/etc/cgprofile/install.toml` and `/opt/cgprofile/PENDING` after verifying the
owned files are gone. Do not rerun the cgprofiled binary from a version tree that has
already been removed. Groups and memberships remain unless `--remove-groups`
is given; `/var/lib/cgprofile` sessions and overrides remain unless
`--purge-data` is given. `cgprofiled uninstall --dry-run` prints the plan; a
`/usr/local/bin/cgprofilectl` that is not the installer's link is kept and reported.

`cgprofiled doctor` reports the following checks, each with a remedy on failure:

`config-valid`, `units-current`, `groups`, `socket-units`, `socket-nodes`,
`socket-dirs`, `state-dir`, `service`, `connect`, `foreign-rules`, and
`caller-access`. A caller outside one socket group gets `skip` for that
level's connection probe. A non-root caller who cannot read a required path
gets `skip` with a run-as-root note. `cgprofilectl doctor` keeps its client checks
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

`tools/vm-socket-acceptance.sh` automates the cases below inside the guest
(`clean-guest` on the fresh VM, `old-layout-guest` on the seeded one); the TTY
wizard install stays a manual step. Its header lists the exact `run-vm-harness.sh
copy` and `ssh` commands. A VM is not proof of a disposable guest (the live
netcup hosts are VMs), so the script refuses unless `CGP_DISPOSABLE_GUEST=yes`
is set, the guest is a harness guest (hostname `vbpub-test`, user `tester`) and
cgprofile is neither installed nor running; `guard-check` runs only that check. The extra cases beyond the commands in this section:
`ActiveState` of `cgprofile.slice` after uninstall, uninstall run from
`/opt/cgprofile/current/venv/bin/cgprofiled` and again through the zipimport route,
a second uninstall as a no-op, uninstall refusing an mdt-style install, a
rerun of `cgprofiled install --yes` over a record that lists users, and
`systemctl enable cgprofile.slice` for a slice without `[Install]`.

Copy the two pinned wheels into the VM, then verify both the TTY wizard and a
non-interactive install using explicit flags. After install, run:

```bash
sudo /opt/cgprofile/current/venv/bin/cgprofiled doctor --json
sudo cgprofilectl doctor --json
sudo stat -c '%n inode=%i mode=%a owner=%U:%G' \
  /run/cgprofile /run/cgprofile/control /run/cgprofile/observer \
  /run/cgprofile/state /run/cgprofile/control/cgprofile.sock \
  /run/cgprofile/observer/cgprofile.sock
sudo systemd-analyze verify \
  /etc/systemd/system/cgprofiled.service \
  /etc/systemd/system/cgprofile-control.socket \
  /etc/systemd/system/cgprofile-observer.socket
```

Then prove the service and socket restart behavior, comparing socket inodes
before and after each command:

```bash
sudo systemctl restart cgprofiled.service
sudo cgprofilectl status --json
sudo systemctl restart cgprofile-control.socket
sudo cgprofilectl status --json
sudo systemctl restart cgprofile-observer.socket
sudo /opt/cgprofile/current/venv/bin/cgprofiled doctor --check connect --json
```

Also verify that a direct `/opt/cgprofile/current/venv/bin/cgprofiled run` with
`LISTEN_FDS` unset and without `--self-bind` exits 2, while
`/opt/cgprofile/current/venv/bin/cgprofiled run --self-bind` accepts explicitly
provisioned test paths. Create a user in `cgprofile-observer` only; that user
must complete the observer health probe and receive `EACCES` on control, with
`/opt/cgprofile/current/venv/bin/cgprofiled doctor` reporting control `connect` as
`skip`.

On the old-layout VM, run the same install command and verify exit 2 plus the
manual removal paths. After removing those files and runtime tree manually,
verify install succeeds. Also verify a rollback below the socket-unit release
fails without `--allow-downgrade`; do not run an actual rollback as part of
this probe. Finally, run
`sudo /opt/cgprofile/current/venv/bin/cgprofiled uninstall --yes` and assert no service, slice or
socket unit, sysusers file, `/etc/cgprofile`, `/opt/cgprofile`, socket node,
level directory, state directory, or failed unit remains. Confirm the groups
and `/var/lib/cgprofile` remain by default.

The plan's phase-1 acceptance also covers the new Run-Gate and Assay releases
against the new paths and their cutover refusal on an old-layout host. Those
consumer preflights are owned by their respective phase-1 packages.
