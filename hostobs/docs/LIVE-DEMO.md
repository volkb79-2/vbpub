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

The probe obtains the peer identity with `SO_PEERCRED`. Across PID namespaces
the kernel reports peer pid 0 when that pid is invisible, and rustix 1.1.6 turns
0 into an invalid `Pid`, which is undefined behaviour. The probe and both
`watch` children therefore share the launcher's PID namespace. This is also why
`tools/live-demo.py` checks the Rust gate/dev image marker
`/usr/local/share/hostobs-Cargo.lock.registry-sha256` and refuses before
starting or signalling any process when it is absent.

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

The launcher creates a private temporary directory, writes a strict config with
both levels, binds two filesystem `AF_UNIX` listeners, and passes them as fds 3
and 4 (`LISTEN_FDS=2`, `LISTEN_FDNAMES=observe:operator`). It runs `watch` for
the requested number of observe ticks and once at operator, then sends
`SIGTERM` to its own probe pid and reaps it with a bound.

The transcript records the probe argv and `LISTEN_*` environment, the launcher's
`/proc/self/cgroup`, the config, both complete watch outputs, the probe stderr
and exit status, and the final verdict. `--out` is optional; the transcript is
always printed.

## PASS

PASS requires all of the following:

- exactly N observe ticks, with strictly increasing sequence numbers;
- at least one `cpu.usage_usec` value that increased from that entity's first
  to last observe tick;
- every printed quality code is one of WIRE's eight codes;
- every non-`ok` quality renders `<not-measured>`, never a numeric value or zero;
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

Install `/usr/local/bin/hostobs-probe`, `/etc/hostobs/hostobsd.toml`, the three
units from [`deploy/systemd/`](../deploy/systemd/), and
[`deploy/tmpfiles.d/hostobs.conf`](../deploy/tmpfiles.d/hostobs.conf) as
`/etc/tmpfiles.d/hostobs.conf`. The socket paths and modes from plan v4 §A.2 are:

- `/run/hostobs`, directory `root:root 0755`;
- `/run/hostobs/observe/hostobs.sock`, directory `root:hostobs-observe 0750`,
  socket `root:hostobs-observe 0660`;
- `/run/hostobs/operator/hostobs.sock`, directory `root:hostobs-operator 0750`,
  socket `root:hostobs-operator 0660`.

The directory ownership comes from the tmpfiles file, not systemd's
`DirectoryMode=`: when the socket unit creates a missing parent itself, systemd
uses `root:root` and ignores the `SocketGroup=` for that directory. Both socket
units order after `systemd-tmpfiles-setup.service` so boot-time creation happens
first.

Create the service user and access groups as part (b)'s setup, not in a
committed sysusers file:

- service user `hostobs`, primary group `hostobs` (no login shell, no home
  needed by this slice);
- access groups `hostobs-observe` and `hostobs-operator`;
- all three group IDs are distinct and neither 0 nor the host's Docker GID.
  Prefer an allocator such as `systemd-sysusers` that assigns system IDs; if
  `groupadd`/`useradd` are used, verify the resulting numbers with `getent`.

Add only the intended human or service identities to `hostobs-observe` or
`hostobs-operator`; group membership is the access-control boundary. The probe
runs as `hostobs` and never binds the sockets itself: systemd passes the two
root-owned listening fds by their `FileDescriptorName`.

After the users and groups exist, create the directories and enable the socket
units:

```sh
systemd-tmpfiles --create /etc/tmpfiles.d/hostobs.conf
systemctl daemon-reload
systemctl enable --now hostobs-observe.socket hostobs-operator.socket
```

Create `hostobs-observer` by the same system-group procedure above, with a GID
distinct from `hostobs`, `hostobs-observe`, `hostobs-operator`, 0 and the host's
Docker GID. Add only intended observer clients to it, and add
`hostobs-observer.socket` to the `systemctl enable --now` command above.

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
processes' cgroup facts. State directories, `CAP_SYS_PTRACE` and the future act
socket are deliberately absent from this part-(a)/(b) contract.
