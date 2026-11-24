# Developer container for hostobs

The developer container is now the generic remote dev tool `scripts/controller/dev`, profile `hostobs`
(guide: [`scripts/controller/docs/DEV.md`](../../scripts/controller/docs/DEV.md); profiles:
`scripts/controller/dev-profiles.toml`; contract: `docs/design/agent-dev/DESIGN.md`).
`hostobs/tools/dev` stays as a thin shim for it: `hostobs/tools/dev <verb> ...` is
`scripts/controller/dev -p hostobs <verb> ...`, `HOSTOBS_DEV_{HOST,MEMORY,CPUS,PIDS,MAX_S}` map to
`AGENT_DEV_*`, any other `HOSTOBS_DEV_*` exits 2, and `dev clean [--checkout]` is `dev gc --this`.

This file keeps what is specific to the Rust dev image: how it is built (`Dockerfile.dev`,
`build-dev-image.sh`), why the toolchain is the gate's own, the vetting rule for what it adds, and
debugging with gdb and strace. How a call syncs the worktree, the memory-PSI admission (`host-admission/1`,
`scripts/netcup/runner/README.md`), limits, exit codes (2, 3, 75, 124), the environment variables and
`dev gc` are in the guide above; nothing about them is repeated here.

**A green dev run is NOT gate evidence.** Only `remote-gate.sh` / `gate-run.sh` lanes on a
committed head are. Use `dev` to iterate, then let the lanes judge.

> **Status.** The image layer is source in this tree; see "Verification status" at the end.
> Nothing in this file claims more than that.

## Day to day

```sh
scripts/controller/dev -p hostobs test -p hostobs-store         # cargo nextest run --offline --locked -p hostobs-store
scripts/controller/dev -p hostobs clippy                        # default: --workspace --all-targets -- -D warnings
scripts/controller/dev -p hostobs build | check | fmt --check | example CRATE NAME | run -- CMD | shell | status | stop
scripts/controller/dev --dry-run -p hostobs test -p hostobs-store   # the plan and the exact docker argv, nothing called
```

The Rust verbs go through `tools/cargo-offline.sh` in the container (offline, locked, cache-verified,
as in the gate lanes), with the target directory in the per-worktree cache volume mounted at
`/home/gate/.cache/hostobs-target` (where `hostobs/.cargo/config.toml` already points), so incremental
builds persist between calls. `dev fmt` without `--check` rewrites files in the **host's** checkout, which is
reset on the next call: use `dev fmt --check` and fix by hand. `gdb` and `strace` are the only verbs that get
`SYS_PTRACE`.

## One-time setup (HOST WORK, needs the controller's go)

The controller gives the go; a controller-run implementer or the controller does the work on
`v1001`. None of this runs from the devcontainer.

1. Build the base gate image from current main on the host (existing procedure):
   `nice -n 19 tester-unified-rust/build-image.sh` (needs `CGROUP_PARENT_DEV_GATES`).
2. Build the developer layer on top of it, on the same host, with a record for vetting:

   ```sh
   nice -n 19 tester-unified-rust/build-dev-image.sh \
     --base tester-unified-rust:rs-20261124 --tag tester-unified-rust-dev:rs-20261124 \
     --cgroup-parent dev-gates.slice --builder default --record /tmp/dev-image-versions.tsv
   ```

   Options: `--tag IMAGE` (default `tester-unified-rust-dev:local`), `--base IMAGE` (default
   `tester-unified-rust:local`), `--builder NAME` (default `default`; precedence option, then
   `TESTER_UNIFIED_RUST_BUILDER`, then the default), `--record FILE`. Use as `--base` the image the
   lanes name in `run-gate.root.toml` (`tester-unified-rust:rs-20261124`); the default `:local`
   is that image only while `:local` has not been moved back to an older whole-lock image (a
   rollback of the tag would silently put the dev layer on the old image: the label
   `org.vbpub.tester-unified-rust-dev.base-image-id` shows which id it was built on). The default builder is the
   docker driver, the kind that can see a base image that exists only in the local daemon; a
   docker-container builder cannot. The build needs the network for apt and rustup only. It
   requires `CGROUP_PARENT_DEV_GATES` or `--cgroup-parent` like `build-image.sh` (the estate rule), builds `FROM` a **content-named tag of the exact
   base image id**: before the build it runs `docker tag <id> tester-unified-rust-base:<64 hex of
   the id>` (build arg `BASE_IMAGE`), so a moved `tester-unified-rust:local` cannot race the build and
   the reference is a plain one every builder accepts. The script creates that tag (exact name, the
   same id on every re-run) and never removes it; remove it by hand when the base image is gone. The
   original tag is kept only as a label (`org.vbpub.tester-unified-rust-dev.base-image`, with
   `...base-image-id` next to it). After the build the labels are compared with the build args (that
   comparison only echoes them) and, independently of them, the dev image's layer list must start with
   every layer of the base image, in order. The script then prints the packages the layer added or
   upgraded.

   **One input, two spellings; with the builder `default` on v1001 the build is governed
   (measured).** The input is the systemd slice NAME
   (`--cgroup-parent dev-gates.slice` / `CGROUP_PARENT_DEV_GATES`); the script derives the cgroup
   PATH itself (systemd dash expansion: `a-b-c.slice` becomes `a.slice/a-b.slice/a-b-c.slice`,
   `dev-gates.slice` becomes `dev.slice/dev-gates.slice`) and refuses a malformed name with exit 2
   before any docker call. Measured on `v1001` (2026-11-18, docker cgroup driver systemd) with the
   docker-driver builder `default` ONLY: `docker buildx build --cgroup-parent
   dev.slice/dev-gates.slice` (the path form, which the script passes) puts the RUN-step processes in
   `/sys/fs/cgroup/dev.slice/dev-gates.slice/buildkit/<id>`, inside the governed slice (MemoryMax
   6.5 GiB). The governed slice's `memory.current` rose with the build: with probe builds, during a
   `sleep` RUN step, 1,298,403,328 -> 1,299,955,712 B; with the real dev-image build, 1,329,975,296 B
   -> a peak of 1,533,550,592 B, while the stray root-level cgroup stayed at 300,556,288 B. Any other
   builder was not measured: verify its placement (`/proc/<pid>/cgroup` of a RUN-step process). With the plain name
   `--cgroup-parent dev-gates.slice` BuildKit would put them in a root-level, **ungoverned**
   `/sys/fs/cgroup/dev-gates.slice/buildkit/<id>` (`memory.max` is `max`). Conversely `docker run`
   takes only the slice NAME (the daemon refuses the path form: `invalid slice name`), so the
   probe containers the script starts afterwards (package list, versions) and the CMRU preflight get
   the name. The script prints a NOTE with both spellings on every build (the measured sentence only
   for the builder `default`; for any other builder it says the placement was not measured). The build is started only
   with the controller's go; still watch the governed slice's `memory.current` (the observer should
   abort above 3.5 GB).
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

The five named packages pull in a dependency closure (the Python and debuginfod libraries of
gdb, for example). `--record FILE` therefore records the **diff of `dpkg-query -W` between the base
image and the dev image**: every package the layer added or upgraded, with its version, plus
`rustc`, `rust-analyzer` and the installed components, as `name<TAB>version<TAB>build date (UTC)`
lines, and a header with the base tag and the base image id. The date column is the **build** date:
a release date cannot be checked offline and is filled in by the host work. A version younger than
14 days is a reason to hold the image.

## Debugging

`dev` below is `hostobs/tools/dev`, the shim for `scripts/controller/dev -p hostobs`. Interactive `shell` and `gdb`
over ssh are best-effort and unmeasured; the dependable way to use the debugger is batch: `dev gdb -batch -ex ...`.

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

* **Core files.** Automatic core dumps are off in every subcommand (`--ulimit core=0:0`): the
  kernel's `core_pattern` is host-wide, and a piped pattern would send up to several GiB to a host
  handler outside the container's cgroup. Get a core on purpose with `gcore` from inside gdb, into
  the target volume: `(gdb) gcore /home/gate/.cache/hostobs-target/core.my_test`. Open it later with
  `dev gdb /home/gate/.cache/hostobs-target/debug/deps/hostobs_store-<hash> /home/gate/.cache/hostobs-target/core.my_test`.
  Core files can be large: remove them with `dev run -- rm ...`.
* **rust-analyzer** and `rust-src` are installed for the toolchain. An editor language server over
  this sync model is not supported (unmeasured). Batch use works:
  `dev run -- rust-analyzer diagnostics /work/hostobs`.
* **Profiling without `perf`:** `dev strace -c -f -o /home/gate/.cache/hostobs-target/strace.sum <bin> <args>`
  for a syscall summary, `dev strace -T -f -e trace=file -o ... <bin>` for per-call time, then
  `dev run -- cat /home/gate/.cache/hostobs-target/strace.sum`.
* `SYS_PTRACE` is added for `gdb` and `strace` only, as the least that could matter for tracing.
  The container runs as the unprivileged `gate` user (1003:1003); the capability is added to the
  bounding set, not to the permitted set of a non-root process, so it is **not expected to make a
  difference**: debugging your own children (`gdb --args`, `strace <cmd>`) works because they are
  your children, and attaching to other pids is not expected to work. Under Docker's default
  seccomp profile gdb may warn that it cannot disable address-space randomization; the wrapper
  passes `set disable-randomization off` to avoid the warning. All of this is unmeasured until the
  first real run.

## Verification status

* **Verified offline:** the generic tool's argv, quoting, admission, exit codes and exact-name docker use,
  with stub `hx`, `docker` and `id` (`scripts/controller/tests/test_dev.py`, first step of the `suite` lane of
  `scripts/controller`, plus eight hand mutants); the `hostobs/tools/dev` shim's argv and variable mapping and the
  static structure of `Dockerfile.dev` and `build-dev-image.sh` (`tester-unified-rust/tests/test_dev_environment.py`,
  `selftest` lane, together with `bash -n` of the scripts).
* **Not verified here:** a real image build, a real host, the real `hx.sh` and ssh, the `rust-analyzer` proxy and
  gdb under the default seccomp profile, interactive ttys over ssh, the runtime limit against a real container
  and latency; these are the controller's live smoke on `v1001`.
* **First-run checks for a human** once the image exists on `v1001`:
  `scripts/controller/dev -p hostobs run -- cargo --version`, `scripts/controller/dev -p hostobs test -p hostobs-proto`,
  `scripts/controller/dev -p hostobs gdb --version`, then `scripts/controller/dev -p hostobs status` and
  `scripts/controller/dev gc --dry-run`. Also compare `dev -p hostobs run -- rustc -vV` with the same command in
  the gate image.
