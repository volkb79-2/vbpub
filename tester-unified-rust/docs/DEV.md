# Developer container for hostobs

`hostobs/tools/dev` gives an implementer or reviewer a place to compile, test, debug and run
user-space Rust against the offline fixtures, with a persistent incremental target directory and
a debugger. Until now the only way to run Rust was a remote gate lane
(`scripts/controller/remote-gate.sh v1001 hostobs <lane> <commit>`): clean checkout, batch only,
no debugger. The dev container reuses the gate image's exact toolchain and crate cache and adds
what a person needs and a gate does not.

**A green dev run is NOT gate evidence.** Only `remote-gate.sh` / `gate-run.sh` lanes on a
committed head are. Use `dev` to iterate, then let the lanes judge.

> **Status: source only.** No `tester-unified-rust-dev` image exists yet and the wrapper has
> never run against one; see "Verification status" at the end. Nothing in this file claims
> otherwise.

## How it works

```
this devcontainer                              gate host (v1001 only)
-----------------                              ----------------------
hostobs/tools/dev test -p hostobs-store
  1. admission slot (remote-gate's flock)
  2. preflight over scripts/controller/hx.sh -> uid, dev-gates.slice memory, PSI, one-at-a-time
  3. git bundle of HEAD (thin) + patch + untracked tar --->  ~/hostobs-dev/<12-hex>/work   (reset to HEAD, patch applied)
  4. run over hx.sh  ---------------------------------->  docker run ... tester-unified-rust-dev:local
     stdout/stderr streamed back, exit code propagated    named volume hostobs-dev-target-<12-hex> = target dir
```

* **REMOTE is the default and the only recommended mode.** The container runs on `v1001` (the
  wrapper accepts no other alias). The devcontainer is capped (8 GiB, OOM-killed once) and shares a
  production host, so nothing heavy runs here.
* **LOCAL (`dev --local ...`) exists but is refused** unless `HOSTOBS_DEV_ALLOW_LOCAL=1`: it uses
  this machine's docker daemon (bind source resolved through `docker inspect` of this container).
  It is not recommended on the shared devcontainer.
* The transport is the controller's own: `scripts/controller/hx.sh` (host table, provider identity
  check and on-host guard on every call). There is no second transport and no secret is read by
  the wrapper. The host alias is `HOSTOBS_DEV_HOST` (default: the only alias in
  `CONTROLLER_REMOTE_GATE_HOSTS`), and it must be `v1001`. Controller settings come from
  `$CONTROLLER_HOME/controller.local.env` exactly as for `remote-gate.sh`.
* Every docker call on the host is by exact container or volume name. The only listing is by the
  name prefix of our own containers (`docker ps --filter name=^hostobs-dev-`).

### What is synced (and the cap)

Per call, the host checkout `~/hostobs-dev/<12-hex>/work` (`<12-hex>` = first 12 hex digits of the
sha256 of this worktree's absolute path) is **reset to the bundled `HEAD`** (`checkout -f`,
`reset --hard`, `clean -ffdx`) and then the uncommitted state is applied: `git diff HEAD --binary`
for tracked changes and a tar of `git ls-files -o --exclude-standard` for untracked files. It never
accumulates stale state. The bundle is thin against what the host already holds (its last synced
head, else a seed checkout under `~/checkouts` as `remote-gate.py` uses); a first use without a
seed ships the full history, which can be large.

**The uncommitted part is capped at 20 MiB** (tracked patch bytes plus untracked file bytes).
Past it the wrapper refuses with exit 2 before contacting the host: commit the change (a commit
travels in the bundle) or drop it. The host checkout is its own git repository (detached at your
`HEAD`), so git works in the container but anything done there is wiped by the next call; commit
here, in the devcontainer.

### Where the build output lives

The target directory is not synced and not in the worktree. It is the named volume
`hostobs-dev-target-<12-hex>` on the host, mounted at `/home/gate/.cache/hostobs-target`, which is
exactly where `hostobs/.cargo/config.toml` already points (`target-dir`). Nothing sets
`CARGO_TARGET_DIR`; there is one place. Incremental builds therefore persist between calls.

## One-time setup (HOST WORK, needs the controller's go)

The controller gives the go; a controller-run implementer or the controller does the work on
`v1001`. None of this runs from the devcontainer.

1. Build the base gate image from current main on the host (existing procedure):
   `nice -n 19 tester-unified-rust/build-image.sh` (needs `CGROUP_PARENT_DEV_GATES`).
2. Build the developer layer on top of it, on the same host, with a record for vetting:

   ```sh
   nice -n 19 tester-unified-rust/build-dev-image.sh \
     --cgroup-parent "$CGROUP_PARENT_DEV_GATES" --record /tmp/dev-image-versions.tsv
   ```

   Options: `--tag IMAGE` (default `tester-unified-rust-dev:local`), `--base IMAGE` (default
   `tester-unified-rust:local`), `--builder NAME` (default `mdt-managed`), `--record FILE`. The
   build needs the network for apt and rustup only. It refuses an ungoverned build like
   `build-image.sh`, checks the base label afterwards and prints `dpkg-query -W` version lines into
   the build log.
3. Because the image is built on the host that runs it, no image transfer is needed. If it must be
   built elsewhere, `docker save IMAGE | docker load` from the host that holds it moves it, but it
   is a multi-GB transfer; only the controller decides that.

### Same toolchain, guaranteed by the build

`Dockerfile.dev` does not install or upgrade `rustc` or `cargo`. It adds `rust-src` and
`rust-analyzer` to the **already installed** toolchain (`rustup component add --toolchain
"$(rustup show active-toolchain | cut -d' ' -f1)" ...`) and a `RUN` fails the build unless
`rustc -vV` is byte-identical before and after. It does not touch the baked crate cache or its
identity files; the last step runs the gate image's own read-only `verify-crate-cache`. Every
`dev build|check|clippy|test|fmt|example` goes through `tools/cargo-offline.sh`, so the same
offline, locked, cache-verified invocation as the gate lanes stays in force.

### Third-party downloads and the 14-day vetting rule

The only new downloads are the apt packages below (from the base image's Debian suite) and the two
rustup components for the existing toolchain. The estate freshness rule applies: prefer the latest
release but adopt only versions at least 14 days old. The release date cannot be checked offline,
so the version is **recorded at first build** and the date column is filled in by the host work
from the Debian package tracker:

| package | version as installed by the suite | release date |
|---|---|---|
| gdb | recorded by `--record` (`dpkg-query -W`) | to be filled by the host work |
| strace | recorded by `--record` | to be filled by the host work |
| procps | recorded by `--record` | to be filled by the host work |
| less | recorded by `--record` | to be filled by the host work |
| ca-certificates | recorded by `--record` | to be filled by the host work |
| rust-src, rust-analyzer | the base toolchain's own version (`rustup component list --installed`) | same as the toolchain |

`--record FILE` writes `name<TAB>version<TAB>build date (UTC)` lines (plus `rust-analyzer` and
`rustc`) for the controller to vet. A version younger than 14 days is a reason to hold the image.

## Daily use

All examples run from anywhere inside the worktree (the wrapper locates its worktree by its own
path). Output streams back; the exit code is the command's.

```sh
hostobs/tools/dev build                         # whole workspace, offline, locked
hostobs/tools/dev check -p hostobs-store
hostobs/tools/dev test -p hostobs-store         # cargo nextest run --offline --locked -p hostobs-store
hostobs/tools/dev test -p hostobs-store my_test # one test by name filter
hostobs/tools/dev test --no-nextest -p hostobs-proto            # plain cargo test (also runs doctests with --doc)
hostobs/tools/dev test -p hostobs-store --no-capture            # nextest: print test output
hostobs/tools/dev test --no-nextest -p hostobs-store -- --nocapture   # plain cargo test: libtest flag after `--`
hostobs/tools/dev clippy                        # default: --workspace --all-targets -- -D warnings
hostobs/tools/dev clippy -p hostobs-kernel --all-targets -- -D warnings
hostobs/tools/dev fmt --check                   # cargo fmt --all --check; `dev fmt` formats in the host checkout only
hostobs/tools/dev example hostobs-proto sp4_wire_size           # the SP-4 wire-size example
hostobs/tools/dev run -- ls /work/hostobs/fixtures
hostobs/tools/dev status                        # container name, running?, volume, checkout, other dev containers
hostobs/tools/dev --dry-run test -p hostobs-store               # print the whole plan and the exact docker argv, call nothing
```

`dev fmt` without `--check` rewrites files in the **host's** checkout, which is reset on the next
call: the change does not come back to your worktree. Use `dev fmt --check` to see what is wrong
and fix it by hand.

`dev shell` (and `fixtures-shell`) open an interactive bash. They are the same container; the name
only documents that `hostobs/fixtures` and the kernel crate's test fixture trees
(`hostobs/crates/kernel/tests/fixtures`) are in the synced worktree and are the offline stand-in
for `/proc` and `/sys`.

### Interactive use over ssh

`-t` is passed to docker (and a pty is allocated on the host with `script`) **only when stdin is a
terminal**; otherwise the call is batch. The host table's ssh command has no `-t`, so interactive
`shell` and `gdb` are best-effort and **unmeasured**: line editing and Ctrl-C go through a
host-side pty with the local terminal in no-echo mode. The dependable way to use the debugger is
batch: `dev gdb -batch -ex ...`.

### Debugging

```sh
# 1. build the test binary (nextest/cargo put it under the target volume, per crate with a hash suffix)
hostobs/tools/dev test --no-nextest -p hostobs-store --no-run
hostobs/tools/dev run -- sh -c 'ls -t /home/gate/.cache/hostobs-target/debug/deps/hostobs_store-* | grep -v "\.d$" | head -3'
# 2. run it under gdb with rust pretty printers (rust-gdb); the debuggee's cwd is the target volume,
#    use `set cwd /work/hostobs/crates/store` inside gdb when a test reads relative paths
hostobs/tools/dev gdb --args /home/gate/.cache/hostobs-target/debug/deps/hostobs_store-<hash> my_test --nocapture
# batch backtrace of a failing test
hostobs/tools/dev gdb -batch -ex run -ex bt --args /home/gate/.cache/hostobs-target/debug/deps/hostobs_store-<hash> my_test
```

* **Core files.** Only `gdb` gets an unlimited core limit (`--ulimit core=-1:-1`; every other
  subcommand gets `core=0:0`). The kernel's `core_pattern` is host-wide and cannot be set from
  the container, so the dependable way is `gcore` from inside gdb:
  `(gdb) gcore /home/gate/.cache/hostobs-target/core.my_test`. Open it later with
  `dev gdb /home/gate/.cache/hostobs-target/debug/deps/hostobs_store-<hash> /home/gate/.cache/hostobs-target/core.my_test`.
  The inferior's cwd defaults to the target volume (`set cwd`), so a plain-file `core_pattern`
  lands the core there too. Core files can be large: remove them with `dev run -- rm ...`.
* **rust-analyzer** and `rust-src` are installed for the toolchain. An editor language server over
  this sync model is not supported (unmeasured). Batch use works:
  `dev run -- rust-analyzer diagnostics /work/hostobs`.
* **Profiling without `perf`:** `dev strace -c -f -o /home/gate/.cache/hostobs-target/strace.sum <bin> <args>`
  for a syscall summary, `dev strace -T -f -e trace=file -o ... <bin>` for per-call time, then
  `dev run -- cat /home/gate/.cache/hostobs-target/strace.sum`.
* `SYS_PTRACE` is granted to `gdb` and `strace` only. The container runs as the unprivileged
  `gate` user (1003:1003) with all other capabilities dropped; tracing your own children works
  without the capability, and the capability does not widen what an unprivileged user can attach
  to. Attaching to an arbitrary pid is therefore not expected to work.

## Limits and how to raise them

| limit | value | override |
|---|---|---|
| memory | 3g, swap equal (no swap) | `HOSTOBS_DEV_MEMORY=4g` |
| cpus | 2 | `HOSTOBS_DEV_CPUS=3` |
| pids | 1024 | fixed |
| priority | `nice -n 10 ionice -c3` | fixed |
| network | none | fixed |
| capabilities | none (`SYS_PTRACE` for `gdb`/`strace`) | fixed |

Do not raise memory or cpus casually. `v1001` has 4 cores and 8 GiB and runs the gate slice; the
host is shared with production load. A higher limit makes the OOM killer pick the wrong victim and
makes your neighbours' lanes inconclusive. The container is placed in `$CGROUP_PARENT_DEV_GATES`
(required; the wrapper refuses without it), the same slice the gate lanes use.

### Pressure gate and one at a time

Before every start the host-side preflight refuses (exit **75**, the same meaning as
`remote-gate.sh`: the host said not yet, nothing ran; retry later) when:

* `dev-gates.slice` `memory.current` is above `HOSTOBS_DEV_SLICE_MEM_MAX_MB` (default 3584 MiB,
  about 3.5 GiB; the file is `/sys/fs/cgroup/dev.slice/dev-gates.slice/memory.current`, the
  slice `remote-gate.sh` already reads for its OOM counter) or cannot be read, or
* memory PSI `some avg10` or io PSI `full avg10` is above 25 (the limit and the rule of
  `scripts/netcup/runner/remote-gate.py`; CPU PSI is not a signal).

It refuses with exit **3** when any `hostobs-dev-*` container already runs on the host
(`HOSTOBS_DEV_ALLOW_PARALLEL=1` overrides; only for different worktrees, one worktree has one
container name). Exit 3 also covers identity-check, guard or ssh failures. The wrapper also takes
**one admission slot** (weight 1) of the same flock slots `remote-gate.sh` uses
(`$CONTROLLER_HOME/remote-gate-locks/<host>.slotN`, `CONTROLLER_REMOTE_SLOTS` default 4, wait
`CONTROLLER_REMOTE_SLOT_WAIT_S` default 7200 then exit 75), so a dev run and gate lanes queue
together; the slot is released when the wrapper exits. The memory/PSI/one-at-a-time checks run
again inside the final run step.

## What is on the host and how to clean it

* checkout `~/hostobs-dev/<12-hex>/work` (git, one per worktree),
* volume `hostobs-dev-target-<12-hex>` (build output, cores, strace files),
* the container `hostobs-dev-<12-hex>` while a call runs (removed by exact name on exit).

```sh
hostobs/tools/dev status                  # shows all of the above
hostobs/tools/dev clean                   # removes THIS worktree's target volume (prints its name first; refuses while running)
hostobs/tools/dev clean --checkout        # also removes ~/hostobs-dev/<12-hex> on the host
```

Remove the dev state when a worktree is merged or abandoned (worktree sprawl applies to the host
too). `status` shows the checkout size; the volume's size is shown only while a container runs
(or `dev run -- du -sh /home/gate/.cache/hostobs-target`).

## Latency

Unmeasured. Expect, per call: ssh and identity checks (three `hx.sh` calls), the bundle and
overlay upload, then the build or test itself. A warm target volume makes the build part
incremental; the first call per worktree compiles everything.

## What is deliberately impossible, and where to do it instead

The container is unprivileged, network-less and capability-free; there is no `--privileged`, no
host pid/net/cgroup namespace, no docker socket, no `/sys`, `/proc/sys`, cgroup, device or
mount-propagation bind. So it cannot do host-impacting work: **cgroup writes, DAMON, peak windows,
`act`**. Those run only in the VM lanes (hostobs plan v4 section F.2) and must stay impossible
here. Anything that needs the network (new crates, `cargo fetch`, `rustup`) is image-build work.
Unit-level behaviour against fixtures belongs in the dev container; the full lane run on a
committed head belongs to `remote-gate.sh`.

## Environment variables

`HOSTOBS_DEV_HOST`, `HOSTOBS_DEV_IMAGE` (default `tester-unified-rust-dev:local`),
`HOSTOBS_DEV_MEMORY`, `HOSTOBS_DEV_CPUS`, `HOSTOBS_DEV_ALLOW_PARALLEL`, `HOSTOBS_DEV_ALLOW_LOCAL`,
`HOSTOBS_DEV_SLICE_MEM_MAX_MB`; the controller's `CONTROLLER_HOME`, `CONTROLLER_REMOTE_GATE_HOSTS`,
`CONTROLLER_REMOTE_SLOTS`, `CONTROLLER_REMOTE_SLOT_WAIT_S`; `CGROUP_PARENT_DEV_GATES` (required).
Test-only seams (never set them in use): `HOSTOBS_DEV_HX`, `HOSTOBS_DEV_TEST_SLICE_MEM_FILE`,
`HOSTOBS_DEV_TEST_PSI_DIR`, `HOSTOBS_DEV_SELF_CONTAINER`.

## Verification status

* **Verified (offline):** the wrapper's argv and its refusals, the remote sync (bundle, patch,
  untracked files, reset, size cap), the admission slot, the memory/PSI gate at, below and above
  its limit, the one-at-a-time rule, exit codes and exact-name docker use, with stub `docker`,
  `id` and transport executables (`tester-unified-rust/tests/test_dev_environment.py`, run in the
  `selftest` lane together with `bash -n` of the scripts), plus the structure of `Dockerfile.dev`
  and `build-dev-image.sh`.
* **Not verified:** no image was built, so the wrapper has never run against a real image, a real
  host, `hx.sh` or ssh; the Dockerfile has never been built (the `rust-analyzer` proxy, the
  `mdt-managed` builder's ability to see a local-only base image, gdb under the default seccomp
  profile are all open until the first build); interactive ttys over ssh and latency are
  unmeasured.
* **First-run checks for a human** once the image exists on `v1001`:
  `hostobs/tools/dev run -- cargo --version`, `hostobs/tools/dev test -p hostobs-proto`,
  `hostobs/tools/dev gdb --version`, then `hostobs/tools/dev status` and
  `hostobs/tools/dev clean`. Also compare `dev run -- rustc -vV` with the same command in the gate
  image.
