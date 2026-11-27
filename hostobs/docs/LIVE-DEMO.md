# Live hostobs demo

This is the first live vertical slice: an activated `hostobs-probe` reads the real
cgroup tree and `/proc`, two clients connect to its named sockets, and the Rust
example decodes `current` and a stream of subscription ticks. It runs inside
`tools/dev` (part a). The units under [`deploy/`](../deploy/) are the matching
systemd contract for part b's Debian VM.

Binding process rules are in
[`PROBE-PROCESS.md`](PROBE-PROCESS.md) §(d)-(h). The process and socket boundary
is plan v4
[§A.1 and §A.2](../../../docs/design/hostobs/PLAN-v4-2026-11-08.md).

## Why one PID namespace

The probe reads raw `SO_PEERCRED` bytes. An invisible peer pid reported as 0
becomes `pid: None`, and the peer remains accepted without constructing a rustix
`Pid`. The probe and both `watch` children share the launcher's PID namespace so
their advisory process labels stay visible through the probe's `/proc` view;
this is a visibility choice for the demo, not a safety requirement.
Separately, `tools/live-demo.py` checks the Rust gate/dev image marker
`/usr/local/share/hostobs-Cargo.lock.registry-sha256` and refuses before
starting or signalling any process when the marker is absent.

## Part (a): run in the capped dev container

From the worktree root, activate the controller environment and run this single
remote command. It resolves the target directory, builds the real probe and the
watch example, then runs the launcher with ten one-second ticks against the
container's private cgroup-namespace view:

```sh
. "$HOME/controller/controller.local.env"
hostobs/tools/dev run -- bash -c 'set -eu
target_dir="$(
  tools/cargo-offline.sh metadata --locked --offline --no-deps --format-version 1 |
  python3 -I -c '"'"'import json, sys; print(next(json.loads(line)["target_directory"] for line in sys.stdin if line.lstrip().startswith("{")))'"'"'
)"
tools/cargo-offline.sh build -p hostobs-probe --locked --offline
tools/cargo-offline.sh build -p hostobs-client --example watch --locked --offline
python3 -I tools/live-demo.py \
  --probe "$target_dir/debug/hostobs-probe" \
  --watch "$target_dir/debug/examples/watch" \
  --ticks 10 \
  --cgroup-root /sys/fs/cgroup \
  --out /tmp/hostobs-live-demo.txt'
```

The launcher creates a private temporary directory for the durable store and a
unique tier directory under `/dev/shm`, writes a strict config with both levels
and all four store keys, binds two filesystem `AF_UNIX` listeners, and passes
them as fds 3 and 4 (`LISTEN_FDS=2`, `LISTEN_FDNAMES=observe:operator`). The
`/dev/shm` tier directory is removed in the launcher's `finally` block. It runs
`watch` for the requested number of observe ticks and once at operator, then sends
`SIGTERM` to its own probe pid and reaps it with a bound.

The transcript records the probe argv and `LISTEN_*` environment, the launcher's
`/proc/self/cgroup`, the config, both complete watch outputs, the probe stderr
and exit status, and the final verdict. `--out` is optional; the transcript is
always printed.

## PASS

PASS requires all of the following:

- exactly N observe ticks, with strictly increasing and **consecutive** sequence numbers
  (`2, 3, 4 …`, no gap);
- **the operator stream has exactly one tick block (one distinct sequence) with at least one
  full row**;
- **each `(entity, metric)` pair appears once per tick block in either stream**, and every tick
  line is a full client row;
- **every observe tick has the same row count as the first one**, so a truncated or duplicated
  tick block is a `FAIL` rather than a pass. This rule assumes a quiet target: a cgroup created or
  removed during the run changes the row count and fails the verdict; repeat the run on a quiet
  target;
- **each stream's `closed` line is exactly `closed<TAB>blocks=<n><TAB>reason=<reason>`**, with
  `<n>` a base-10 integer at least as large as the requested ticks (N observe ticks for observe,
  1 for operator). `unsubscribe` can count blocks that arrive before its reply, so this count may
  be higher (`crates/client/src/client.rs:550-558`). The reason must be one of the two live reasons
  the client accepts, `unsubscribed` or `client-eof` (`crates/client/src/client.rs` `end_reason_ok`);
  a query reason such as `complete` is refused;
- at least one `cpu.usage_usec` value increases from that entity's first to last
  observe tick;
- **the observe and the operator streams are both held to the same quality rules**: every
  printed quality code is one of WIRE's eight codes, every non-`ok` quality renders
  `<not-measured>` (never a numeric value or zero), and the operator stream carries full
  client rows (its compact empty-block form, `tick<TAB>seq=N<TAB>rows=0<TAB>columns=0`, is not
  a measurement and fails);
- the probe exits by `SIGTERM`, not status 0/1/2 and not a panic.

The transcript states `PASS` or `FAIL <reason>`; the launcher exits 0 only on
PASS and 1 on FAIL. An argument refusal or an attempt outside a dev/lane
container exits 2 before sending a signal.

In the dev container, `/sys/fs/cgroup` is the container's own cgroup-namespace
root, not the VM's full hierarchy. The quality explanation for that run
belongs with the live evidence in the package report: `ok` means the
corresponding cgroupfs file/key was read and parsed; `absent` means the
container does not expose that file/key; `unreadable` means the read or parse
failed; `unsupported` means the kernel does not provide the fact. These states
are never folded into numeric zero.

## Part (b): Debian VM/systemd outline

Follow these steps in order, for a fresh install and for an upgrade.
On an upgrade, complete steps 1–3 in one sitting, through the stop and restart
in step 3.

The three socket units and the `levels` list in `hostobsd.toml` are one set.
Install all of them. If a configured level has no socket, or a passed socket has
no configured level, the probe exits 2 before it serves anything, so every level
stops until an operator corrects the set
(`systemctl status hostobs-probe.service` shows the refusal).
`RestartPreventExitStatus=2` stops the automatic restart, but a client that
connects to a listening socket makes systemd start the service again, until a
start limit marks the units failed (documented systemd behaviour, not measured
here). After such a failure, correct the cause, run `systemctl reset-failed` on
the failed units and repeat step 3.

**1. Create the service user and the three access groups.** This is part of
setup, not a committed sysusers file:

- service user `hostobs`, primary group `hostobs` (no login shell, no home
  needed by this slice);
- access groups `hostobs-observe`, `hostobs-operator` and `hostobs-observer`;
- all four group IDs are distinct and neither 0 nor the host's Docker GID.
  Prefer an allocator such as `systemd-sysusers` that assigns system IDs; if
  `groupadd`/`useradd` are used, verify the resulting numbers with `getent`.

Add only the intended human or service identities to the appropriate groups:

- `hostobs-observe` grants reads; command-line details are not served at any level yet;
- `hostobs-operator` is reserved for reads with command-line details (`procs`, not served yet);
  today it returns the same data as `hostobs-observe`;
- `hostobs-observer` grants annotation writes attributed to the caller's uid;
  this socket serves `hello` and `annotate` only.

The probe runs as `hostobs` and never binds the sockets itself: systemd passes
the three root-owned listening fds by their `FileDescriptorName`.

**2. Install the files.** `/usr/local/bin/hostobs-probe`,
`/etc/hostobs/hostobsd.toml`, all four units from
[`deploy/systemd/`](../deploy/systemd/) (three socket units and the service
unit) in `/etc/systemd/system/`, and
[`deploy/tmpfiles.d/hostobs.conf`](../deploy/tmpfiles.d/hostobs.conf)
as `/etc/tmpfiles.d/hostobs.conf`. The socket paths and modes from plan v4 §A.2
are:

- `/run/hostobs`, directory `root:root 0755`;
- `/run/hostobs/observe/hostobs.sock`, directory `root:hostobs-observe 0750`,
  socket `root:hostobs-observe 0660`;
- `/run/hostobs/operator/hostobs.sock`, directory `root:hostobs-operator 0750`,
  socket `root:hostobs-operator 0660`;
- `/run/hostobs/observer/hostobs.sock`, directory `root:hostobs-observer 0750`,
  socket `root:hostobs-observer 0660`.

The directory ownership comes from the tmpfiles file, not systemd's
`DirectoryMode=`: when the socket unit creates a missing parent itself, systemd
uses `root:root` and ignores the `SocketGroup=` for that directory. All three
socket units order after `systemd-tmpfiles-setup.service` so boot-time creation
happens first.

**3. Create the directories, then restart the sockets and the probe.** All three
groups must exist first: `systemd-tmpfiles` fails on a line whose group is
unknown and does not create that directory.

```sh
systemd-tmpfiles --create /etc/tmpfiles.d/hostobs.conf
systemctl daemon-reload
systemctl stop hostobs-observe.socket hostobs-operator.socket hostobs-observer.socket hostobs-probe.service
systemctl enable --now hostobs-observe.socket hostobs-operator.socket hostobs-observer.socket
systemctl enable hostobs-probe.service
systemctl start hostobs-probe.service
systemctl status hostobs-probe.service
```

Enabling the service makes the probe sample from boot; with only the sockets
enabled, nothing is sampled after a reboot until a client connects. `start`
returns success even if the probe then exits 2, so read the `status` output: it
must say `active (running)`, also when you run it again a few seconds later.

The `stop` matters on an upgrade. A running probe keeps the sockets it was
started with, and systemd refuses to start a socket unit while its service is
active.

For a 60-second observe check, run the same example installed as
`/usr/local/bin/hostobs-watch` from an identity in the `hostobs-observe` group.
`watch` performs one `current`, then the requested subscription ticks:

```sh
hostobs-watch \
  --socket /run/hostobs/observe/hostobs.sock \
  --level observe \
  --ticks 60 \
  --interval-ms 1000
```

The probe unit keeps `ProtectControlGroups=yes`, `PrivateNetwork=yes` and
`RestrictAddressFamilies=AF_UNIX`; it does not set `ProtectProc=invisible` or
`ProcSubset=pid`, because the live slice reads host-wide `/proc` and other
processes' cgroup facts. The unit creates `RuntimeDirectory=hostobs/tiers` with
`RuntimeDirectoryMode=0700` and `RuntimeDirectoryPreserve=restart`, plus
`StateDirectory=hostobs/probe` with `StateDirectoryMode=0700`. `CAP_SYS_PTRACE`
and the future act socket are deliberately absent from this part-(a)/(b) contract.

### `absent` cells in the recorded Debian 13 guest

The first 60-tick stream from a Debian 13 guest (`.controller-state/hostobs/evidence/p11b-vm-20261124/transcript.txt`)
shows two cgroups with `absent` cells. The observer socket did not exist in that recorded run.
Both cells are the kernel's cgroup v2 layout, not a
defect and not the unit sandbox:

| cgroup | `cpu.usage_usec` | `mem.current`, `pids.current` | why |
|---|---|---|---|
| `/` (the root) | `ok`, and it changes | `absent` on every tick | `memory.current` (`mm/memcontrol.c`) and `pids.current` (`kernel/cgroup/pids.c`) are registered `CFTYPE_NOT_ON_ROOT`, so the root has no such file; `cpu.stat` is a cgroup-core file on every cgroup, root included |
| `/system.slice/systemd-udevd.service/udev` | `ok` | `absent` on every tick | systemd 257 runs udevd with `Delegate=`/`DelegateSubgroup=udev`, and nothing writes `memory`/`pids` into `systemd-udevd.service/cgroup.subtree_control`, so the leaf has core files only; its parent `systemd-udevd.service` still shows `pids.current=1`, because that file is hierarchical |

All the other 32 entities of that transcript are `ok` on all three metrics (102 rows per
tick, 4 `absent` cells per tick, 240 of 6120 total cells). Everything else stays `ok`:
`ProtectControlGroups=yes` is
a read-only bind mount of `/sys/fs/cgroup`, not a hidden view, and the probe sees the whole
guest tree. A permission-denied read would render `unreadable`, never `absent`
(`crates/kernel/src/fs.rs:160-164`, `crates/kernel/src/cgroup.rs:851-857`), so the two cells above
are the files being absent, not the sandbox denying them.

**The model table has no host-wide memory metric.** There is no `/proc/meminfo` metric, and the
host `/proc` readings this slice publishes appear only on the root row (WIRE §4.7). A consumer
that needs host memory has no hostobs row for it today; do not read `/`'s `mem.current` as a
fallback, because on a real cgroup v2 root that cell is `absent`.

In part (a) the container's `/sys/fs/cgroup` is the container's cgroup-namespace root, a non-root
cgroup of the real hierarchy, so there `mem.current` and `pids.current` are present and `ok`.

### Reading `systemd-analyze security` on the shipped units

`systemd-analyze security hostobs-probe.service` on the guest reported an overall exposure level
of **`1.8 OK`**, over **81 scored entries: 17 with the ✗ prefix and 64 with ✓**
(`.controller-state/hostobs/evidence/p11b-vm-20261124/transcript.txt:6418` is the summary line and
`:6336-6416` the 81-row table; the checker captured it with the exact command at
`hostobs-p11b.sh:175`). Reviewers should expect exactly these 17 flagged entries, in this order, and
none of the 17 is policy drift:

| # | at | expected ✗ entry | why it is expected |
|---|---|---|---|
| 1 | `:6337` | `SystemCallFilter=~@resources` | `SystemCallFilter=@system-service` is the contract's allow list (`deploy/systemd/hostobs-probe.service:22-23`, plan v4 §A.1); `@resources` stays reachable inside it |
| 2 | `:6340` | `SystemCallFilter=~@privileged` | the same allow list |
| 3 | `:6347` | `RemoveIPC=` | not set on the service user |
| 4 | `:6348` | `RootDirectory=/RootImage=` | the probe runs in the host's root, as designed |
| 5 | `:6352` | `AmbientCapabilities=` | `CAP_DAC_READ_SEARCH` is deliberately ambient (`deploy/systemd/hostobs-probe.service:24-25`) |
| 6 | `:6353` | `ProtectClock=` | not set |
| 7 | `:6354` | `ProtectKernelLogs=` | not set |
| 8 | `:6355` | `CapabilityBoundingSet=~CAP_(DAC_*\|FOWNER\|IPC_OWNER)` | the set is bounded to that one capability (`deploy/systemd/hostobs-probe.service:26-27`), which is in this group |
| 9 | `:6358` | `RestrictSUIDSGID=` | not set |
| 10 | `:6359` | `ProtectHostname=` | not set |
| 11 | `:6360` | `RestrictAddressFamilies=~AF_UNIX` | `RestrictAddressFamilies=AF_UNIX` is the contract (`deploy/systemd/hostobs-probe.service:20-21`); only unix sockets are allowed |
| 12 | `:6361` | `RestrictRealtime=` | not set |
| 13 | `:6376` | `ProtectProc=` | unset on purpose, so host-wide `/proc` stays readable |
| 14 | `:6377` | `ProcSubset=` | unset on purpose, the same reason |
| 15 | `:6378` | `PrivateUsers=` | not set; the probe reads `/sys/fs/cgroup`, owned by root |
| 16 | `:6382` | `IPAddressDeny=` | no IP address filtering is needed: `PrivateNetwork=yes` (`deploy/systemd/hostobs-probe.service:18-19`) removes the network |
| 17 | `:6416` | `UMask=` | not set |

A different count, a missing entry, or a score other than `1.8 OK` means the unit file changed;
the table is a drift alarm, not a to-do list. The sockets (`hostobs-observe.socket`,
`hostobs-operator.socket` and `hostobs-observer.socket`) carry no service body of their own and
were not scored: the checker's
`systemd-analyze` call names `hostobs-probe.service` only.

The trap this table exists to prevent: a hardening checklist reports a deliberate contract choice
as `✗`. `AmbientCapabilities=`, `CapabilityBoundingSet=`, `RestrictAddressFamilies=` and
`SystemCallFilter=` are flagged *because* the contract pins the narrowest useful value, while
`ProtectProc=` and `ProcSubset=` are flagged because the live slice needs host-wide `/proc`. A
reviewer who "fixes" these breaks the probe's job.
