# hostobs

The Rust host-observability daemon planned in
[`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md)
as a probe plus an actuator. The workspace has the planned `proto`, `serve`, `model`, `kernel`,
`store`, `eval`, `act`, and `client` library boundaries plus the `probe` and `act`
binary packages. The `client` crate is the Rust client of `hostobs/1` (handshake, JSON requests,
block streams, subscriptions and reconnect over an injected transport and clock; WIRE.md §12 `P5a`).

## Python client

The `hostobs-client` distribution contains the standard-library `hostobs_client`
package and the `hostobs-mark` command. No release exists yet, and this branch
does not build a release wheel. See [packaging](docs/PACKAGING.md) and [consumer
instructions](docs/CONSUMERS.md).

No hostobs probe currently serves `annotate`, and `deploy/` does not install an
observer socket yet. With no observer socket installed, `hostobs-mark` prints a
notice and exits 0; `--require` exits 1. When the observer socket is installed,
callers will need membership in the `hostobs-observer` group.

**What is implemented today.** Five core libraries plus the Rust client described above are
functional:

- **`model`** — the data model: incarnation identity (`(mount id, cgroup inode)` with
  boot/epoch/process keys), the closed eight-code quality vocabulary, good/bad-quality
  values, the metric registry with unit/kind/scope/source/aggregation, and counter
  classification plus rate derivation over the *actual* monotonic interval.
- **`proto`** — the wire codec: frame header, section directory, fixed-width typed
  little-endian columns (raw `u64` counters, a `u8` quality per value), history buckets
  and bounded per-segment dictionaries. No I/O, no clock, no `unsafe`, no dependencies.
- **`serve`** — the sans-IO serving core: request argument readers and classification,
  wire encoders and line framing, the bounded connection/server state machines, the stream
  registry and the one send hub. It depends on `proto` and performs no I/O or clock reads.
- **`kernel`** — the read-only collectors: cgroupfs (`cpu.stat`, `cpu.max`/`weight`,
  `memory.current`/`.stat`/`.events`/`.max` and friends, `io.stat`, `pids.current`/`.max`,
  the three PSI files) and `/proc` (`stat`, `meminfo`, `vmstat`, `loadavg`, one process's
  `stat`/`statm`/`status`/`cmdline`, the boot id and the host `/proc/pressure/*` files),
  plus the identity inputs (`(device, inode)`, boot id, `starttime` ticks) and an
  injectable clock. Every file access goes through one root-anchored, read-only `Fs` trait
  carrying a byte bound, a line bound and an entry bound; every parse failure is a typed
  error carrying a quality code, never a zero.
- **`store`** — the on-disk history and events store: a segment file is a sequence of
  `proto` payloads inside a checksummed record envelope (64-byte header, records with their
  own lengths and body CRCs, a 64-byte footer), with tier directories bounded by `keep_s`
  **and** `cap_bytes` (whichever first) evicting whole segments oldest first, a bounded
  active dictionary that retires incarnations only when no stored segment, queued job or
  unwritten record still names them, a byte-bounded writer queue whose drops are **counted**,
  torn-tail repair and quarantine at open (a zero-filled tail is the unwritten part of a
  crash, a tail of other bytes is corrupt), atomic seal by temp-name + fsync + rename with a
  failed seal leaving neither a temp file nor a lost open handle, repair of the crash window
  where a sealed segment and its stale `.seg.open` twin coexist, bounded range reads that
  never panic on a corrupt segment, and the events/spans store with its own cap, RAM ring and
  open-span table. Files it creates are `0600` and directories `0700`; a file past the read
  bound is quarantined and counted rather than skipped, and an unreadable one is reported and
  left in place rather than bricking the tier.
  **Every write goes through one module**, `crates/store/src/fsio.rs`, and the `lint` lane
  refuses a write anywhere else (plan v4 §A.3). The byte layout is specified in
  [`docs/SEGMENT-FORMAT.md`](docs/SEGMENT-FORMAT.md).

The three crates are pure in the sense that matters: facts in, typed answers out, and every
refusal is a typed error rather than a panic or an invented zero. The codec's refusals cover
the frame's own shape too: a dictionary body that repeats an identity, a metric mask that is
not the mask its kind derives, a row naming a metric, a column naming an incarnation, a
column whose kind contradicts the dictionary, a block shape under any other frame type, a
section directory out of canonical order, and a non-zero row count with neither a rows nor a
time section are all refused with a named error, in both the Rust and the Python decoder.
The commitments they implement — identity by incarnation and never by cgroup path, absence
never written as zero, counters stored raw with rates derived from the real interval, an
unknown device topology reported as a state instead of guessed to be a bottom device, and
unknown future versions refused — are stated in each crate's module docs and reasoned about
in [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).

The `store` crate's writes are bounded in four directions at once, and each of the four
failures plan v4 §C.5 names has a typed answer: a full disk is `no-space` and a counted write
failure, RAM growth is bounded by `writer_queue_bytes` / `dict_active_max` / a query's own two
limits, a torn file after a crash is *detected* and truncated back to its last good record
(or quarantined as `.corrupt` when the bytes at an offset do not hold), and silent loss is
impossible to represent: a dropped job is a `Submit` report and a counter, and a segment is
only ever removed when it is this store's own file by both name and header. `store` uses
`proto` for the fixed payload frame fields and declares `model` for the future HOB-39
decoded-row join; it has no quality or value table of its own. The store adds the two facts
its segments own — the exact frame type and flag policy of the corpus, and the configured
payload bound — before accepting a payload.

The `probe` binary is now the activated process: `hostobs-probe --config <path>` adopts exactly
the named `LISTEN_FDS` sockets configured in `[probe] levels`, starts the sampler, and serves the
sans-IO `Server` from one bounded non-blocking poll loop. It never binds, spawns or installs a
signal handler; `SIGTERM` keeps its default action. `hostobs-probe --version` prints the
package name and version. The installed config path supplied explicitly by the unit is
`/etc/hostobs/hostobsd.toml`; a start-up refusal is one stderr line and exit 2.

### Crate layout

- `proto` owns the pure wire codec, stream reader/dictionary state and JSON envelope scanner.
- `serve` owns the sans-IO protocol core: arguments, classification, encoding, limits,
  connection/server state, selection streams and the send hub.
- `probe` owns activation, configuration, the poll driver, sampler and live backend.

The proven topos sampler
(tag `archive/topos-sampler-poc-20261105`, commit `193bf601f`, path `topos/sampler/`) is the
behavioural source the `kernel` collectors were ported from: its scheduling around a stalled
`/proc/<pid>/cmdline` read is deliberately not ported, and the parts plan v4 requires to
change were changed (crate docs, `docs/DESIGN-GUIDE.md` §"Collecting the host", and
`HOB-20`).

Golden fixtures under `fixtures/` are shared by two independent implementations: the Rust
tests in `crates/model/tests`, `crates/proto/tests`, `crates/serve/tests` and
`crates/store/tests`, and a
standard-library-only Python reference codec in `python/reference/` that decodes and re-encodes
the same bytes (`python3 -I -m unittest discover -s python/tests`); the client package
`python/hostobs_client` (stdlib only: decoder, frame reader, stream dictionaries, `rate()`, the
`Connection`, `require_profiling` and `hostobs-mark`) reproduces the same fixtures. The segment fixtures under
`fixtures/segment/` are a whole file each, generated by `python/reference/make_segment_fixtures.py`
from `docs/SEGMENT-FORMAT.md` and decoded by both implementations. The `kernel` readers are
driven by committed fixture trees under `crates/kernel/tests/fixtures/` (cgroupfs and `/proc`,
copied from the sampler's own fixtures), never by a live host. SP-4's wire-size measurement and
the numbers that confirm decision D4 are in [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md).
The normative `hostobs/1` protocol (connection, frames, verbs, budgets, the Python access path) is
[`docs/WIRE.md`](docs/WIRE.md).

The test-quality claim for these crates is a hand mutation campaign (Rust R2 is not adopted
yet), recorded in [`docs/HAND-MUTANTS-M1.md`](docs/HAND-MUTANTS-M1.md) (twenty-one mutations,
all killed), [`docs/HAND-MUTANTS-M1B.md`](docs/HAND-MUTANTS-M1B.md) (thirty-one mutations, all
killed) and [`docs/HAND-MUTANTS-M1C.md`](docs/HAND-MUTANTS-M1C.md) (fifteen mutations over the
store's bounds and crash-safety guards, all killed). Each is a stored patch in a replayable
script, and every restore is SHA-256-verified. The P0 bug fold-in adds
[`docs/HAND-MUTANTS-M1D.md`](docs/HAND-MUTANTS-M1D.md) (fifty-four stored patches over the
frame-header bound, quarantined files inside the cap, the sealed-footer and name/header rules
and the time-section length); its campaign ran on 2026-11-18: 50 of 54 patches killed on the
first run, and 54 of 54 after the 4 survivors were closed by tests (see its status paragraph).

Open items found while building these packages are in
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md) (`HOB-1` … `HOB-60`; `HOB-8`,
`HOB-42` and `HOB-43` are FIXED by the P0 bug fold-in, `HOB-55` … `HOB-58` by the P0b store
follow-ups, `HOB-46` … `HOB-48` and `HOB-50` … `HOB-53` by the P2a1 wire codec; `HOB-54` is the
open stream-layer work for P2, P3 and P5b, specified in [`docs/WIRE.md`](docs/WIRE.md), and
`HOB-49` is a citation fix in the Python reference): the sharpest still-open ones are
`HOB-21`/`HOB-29` (no `/sys` device-topology source, and `store`'s tmpfs check reads the mount
table rather than `statfs`), `HOB-39` (a store range answer is still the stored payload, not
decoded `model` rows; the dependency exists, but decoding remains the caller's step), `HOB-45`
(Rust R2/R3 are unregistered) and `HOB-30` (no session spools, which are out of this package's
scope). Workspace unification removes the duplication behind `HOB-3`/`HOB-10`/`HOB-17`/`HOB-26`/`HOB-27`:
there is one model quality/value vocabulary, `proto` derives its metric codes from `model`, and
SP-2 calls `model::derive_rate`.

## Developing and debugging

`scripts/controller/dev -p hostobs <verb>` (the generic remote dev tool; [`tools/dev`](tools/dev) is a
shim for it) runs `build`, `check`, `test`, `clippy`, `fmt`, `example`, `gdb`, `strace` and an
interactive shell in a capped, network-less, unprivileged container that shares the gate image's exact
toolchain and offline crate cache. It runs on a gate host, `v1001` by default (your worktree, including
uncommitted changes up to 20 MiB, is synced there per call); local mode is refused. Try
`tools/dev --dry-run test -p hostobs-store` to see the plan. A green dev run is **not** gate evidence;
only the lanes below on a committed head are. The dev image (`tester-unified-rust-dev:rs-20261124b`) exists
and the dev tool runs remotely on the gate host (measured 2026-11-24 with the pre-shim tool:
`test -p hostobs-model`, 53 tests, 79 s wall). Guide:
[`../scripts/controller/docs/DEV.md`](../scripts/controller/docs/DEV.md); the Rust dev image (build,
toolchain, debugging): [`../tester-unified-rust/docs/DEV.md`](../tester-unified-rust/docs/DEV.md).

## Gates

The registered default lanes are `lint`, `selftest`, `assay-r1` and `conformance` (the hand-run
`assay-r2` is registered too, see below). `conformance` builds the probe offline, reads the
cargo target directory from locked offline metadata, exports `HOSTOBS_PROBE_BIN`, checks it is
executable, and runs the Python reference decoders, fixture
checks, `hostobs_client` tests and the real-socket probe test, standard library only; `HOB-4`.
An unset or non-executable `HOSTOBS_PROBE_BIN` fails the real-socket test rather than skipping
it. Run the lanes through the estate's
gate wrappers, not a bare `run-gate` (which skips slot admission and refuses container lanes
on the main checkout). Locally, from this directory in a **linked worktree**:

```sh
. "$HOME/controller/controller.local.env"
<repo>/scripts/controller/gate-run.sh lint        # then selftest, assay-r1, conformance
```

Remotely, one lane at a time on a remote gate worker (`CONTROLLER_REMOTE_GATE_HOSTS=v1001`
must be set):

```sh
scripts/controller/remote-gate.sh v1001 hostobs lint <commit-or-branch>
```

Both are described in `scripts/controller/README.md` ("Running the registered gate", "Run one
lane on a remote gate worker"). The Rust lanes need the `tester-unified-rust` image on the
executing host; see [`docs/CONSUMERS.md`](docs/CONSUMERS.md).

Cargo is offline and locked, and each Cargo invocation checks the mounted `Cargo.lock`
against the image's registry-set identity and cache manifest first (workspace-member lock
edits do not change that identity; the Rust lanes run on `tester-unified-rust:rs-20261124b`,
which carries it). `selftest` is the workspace's own
R0/R1 baseline (nextest, doctests, llvm-cov and the per-crate line/region floors).

**`assay-r1` is the estate-standard coverage verdict**: assay's Rust R1 judge reads the
same `lcov.info` and judges the 61 declared `src` files of the seven functional packages
(the six crates and `bins/probe`) against
a 95.0% line floor (the floor was derived from 96.04% measured over the first 33 files and
floored one point down; the later lanes measure 96.7% to 96.9% — derivation in
[`assay.toml`](assay.toml)). The verdict carries the measured totals, lists the uncovered
lines as findings, and reports branch coverage as **unavailable** (stable Rust emits no
branch arcs; assay B523), never as zero-of-zero. R3 (canary) is not registered for Rust yet.
R2 (mutation) is declared as a separate, **hand-run** lane, `assay-r2` (below). See [`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the lane
commands, what the verdict says, the floor derivation and the Python conformance run, and
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) §"Gates and coverage" for why the numbers
are pinned this way. The lane is a **command lane**, not `kind = "assay"`: it runs the judge
straight from the mounted worktree's own source (`PYTHONPATH={worktree}/assay/src`), so it
needs no install and therefore no `pip` in the Rust gate image (`HOB-44`, closed by exactly
that re-declaration). `selftest` stays as the enforced baseline alongside it. The lane was
proven through Run-Gate in this branch: `PASS` 15100/15723 = 96.0377 % in the lane's own log,
and `FAIL/UNCOVERED_LINES` 14777/15723 = 93.9833 % when one kernel test file is deleted and
that deletion is committed in a temporary worktree.

**`assay-r2` is hand-run, never a default gate (assay B245 U8b).** It judges mutation (R2) of
the lines a change touches in the six judged crates by ingesting cargo-mutants' report
(`cargo-mutants-outcomes`; flags and reasons in the comment block of [`assay.toml`](assay.toml)).
It is not in the lane list above and no sequence chains it. Run it by hand, one run at a time, on a
host that can spend hours (about 20-35 s per mutant is an estimate, not a measurement):

```sh
run-gate assay-r2 --base <ref> --worktree <worktree>   # through the estate gate wrappers, like the other lanes
```

Choose a `<ref>` that yields about 100-300 mutants: count them first with
`cargo mutants --list --in-diff <diff>` (there are no hostobs release tags yet, so the base is any
earlier commit). Only `0` is a pass: a surviving mutant is `FAIL`, a timed-out mutant is
`BUDGET_EXCEEDED` (lane `FAIL`), a crashed mutant is `ERROR`, and a diff with nothing to mutate is
`INCONCLUSIVE/NO_MUTANTS` (lane `FAIL`, never a pass). Assay known issue K15 (no real hostobs-sized
cargo-mutants run yet) stays open until such a run exists; the T3 queue that will run this lane
automatically is later work.
