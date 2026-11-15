# hostobs

The Rust host-observability daemon planned in
[`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md)
as a probe plus an actuator. The workspace has the planned `proto`, `model`, `kernel`,
`store`, `eval`, `act`, and `client` library boundaries plus the `probe` and `act`
binary packages.

**What is implemented today.** Three of the seven library crates are functional:

- **`model`** — the data model: incarnation identity (`(mount id, cgroup inode)` with
  boot/epoch/process keys), the closed eight-code quality vocabulary, good/bad-quality
  values, the metric registry with unit/kind/scope/source/aggregation, and counter
  classification plus rate derivation over the *actual* monotonic interval.
- **`proto`** — the wire codec: frame header, section directory, fixed-width typed
  little-endian columns (raw `u64` counters, a `u8` quality per value), history buckets
  and bounded per-segment dictionaries. No I/O, no clock, no `unsafe`, no dependencies.
- **`kernel`** — the read-only collectors: cgroupfs (`cpu.stat`, `cpu.max`/`weight`,
  `memory.current`/`.stat`/`.events`/`.max` and friends, `io.stat`, `pids.current`/`.max`,
  the three PSI files) and `/proc` (`stat`, `meminfo`, `vmstat`, `loadavg`, one process's
  `stat`/`statm`/`status`/`cmdline`, the boot id and the host `/proc/pressure/*` files),
  plus the identity inputs (`(device, inode)`, boot id, `starttime` ticks) and an
  injectable clock. Every file access goes through one root-anchored, read-only `Fs` trait
  carrying a byte bound, a line bound and an entry bound; every parse failure is a typed
  error carrying a quality code, never a zero.

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

The `store` segments and the `probe` binary are the next package and are still the M0
scaffold. The proven topos sampler (`topos-sampler-poc-20261105`, `topos/sampler/`) is the
behavioural source the `kernel` collectors were ported from: its scheduling around a stalled
`/proc/<pid>/cmdline` read is deliberately not ported, and the parts plan v4 requires to
change were changed (crate docs, `docs/DESIGN-GUIDE.md` §"Collecting the host", and
`HOB-17`/`HOB-20`).

Golden fixtures under `fixtures/` are shared by two independent implementations: the Rust
tests in `crates/model/tests` and `crates/proto/tests`, and a standard-library-only Python
reference codec in `python/reference/` that decodes and re-encodes the same bytes
(`python3 -m unittest discover -s python/tests`). The `kernel` readers are driven by
committed fixture trees under `crates/kernel/tests/fixtures/` (cgroupfs and `/proc`, copied
from the sampler's own fixtures), never by a live host. SP-4's wire-size measurement and the
numbers that confirm decision D4 are in [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md).

The test-quality claim for these crates is a hand mutation campaign (Rust R2 is not adopted
yet), recorded in [`docs/HAND-MUTANTS-M1.md`](docs/HAND-MUTANTS-M1.md) (twenty-one mutations,
all killed) and [`docs/HAND-MUTANTS-M1B.md`](docs/HAND-MUTANTS-M1B.md) (thirty-one mutations,
all killed).

Open items found while building these packages are in
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md) (`HOB-1` … `HOB-26`): the
sharpest still-open ones are `HOB-17` (the `kernel` crate carries its own value and quality
types until the Rust lane image can be rebuilt against a new `Cargo.lock`), `HOB-21` (no
`/sys` device-topology source, so a production `io.*` aggregate is `unknown-topology` until
the probe supplies one) and `HOB-4` (the Python conformance run is still a manual check, not
a registered lane). `HOB-26` states the SP-2 limit plainly: the kernel-side rate arithmetic
is a test-local helper, because `kernel` cannot depend on `model` (`HOB-17`).

## Gates

Use the registered `lint` and `selftest` lanes from this directory:

```sh
run-gate lint
run-gate selftest
```

Cargo is offline and locked, and each Cargo invocation checks the mounted `Cargo.lock`
against the image's lock-specific cache manifest first. See
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the lane commands, the Python conformance
run and the "changing a dependency" path, and
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the boundary decisions.
