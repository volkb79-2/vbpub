# hostobs

The Rust host-observability daemon planned in
[`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md)
as a probe plus an actuator. The workspace has the planned `proto`, `model`, `kernel`,
`store`, `eval`, `act`, and `client` library boundaries plus the `probe` and `act`
binary packages.

**What is implemented today.** Four of the seven library crates are functional:

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
only ever removed when it is this store's own file by both name and header. `store` does not
depend on `proto`/`model` (a path dependency would rewrite the locked `Cargo.lock`, `HOB-27`), so
it validates the four payload frame-header facts itself and binds its quality table to
`fixtures/wire/quality.json` instead of to a type.

The `probe` binary is the next package and is still the M0 scaffold. The proven topos sampler (`topos-sampler-poc-20261105`, `topos/sampler/`) is the
behavioural source the `kernel` collectors were ported from: its scheduling around a stalled
`/proc/<pid>/cmdline` read is deliberately not ported, and the parts plan v4 requires to
change were changed (crate docs, `docs/DESIGN-GUIDE.md` §"Collecting the host", and
`HOB-17`/`HOB-20`).

Golden fixtures under `fixtures/` are shared by two independent implementations: the Rust
tests in `crates/model/tests`, `crates/proto/tests` and `crates/store/tests`, and a
standard-library-only Python reference codec in `python/reference/` that decodes and re-encodes
the same bytes (`python3 -I -m unittest discover -s python/tests`). The segment fixtures under
`fixtures/segment/` are a whole file each, generated by `python/reference/make_segment_fixtures.py`
from `docs/SEGMENT-FORMAT.md` and decoded by both implementations. The `kernel` readers are
driven by committed fixture trees under `crates/kernel/tests/fixtures/` (cgroupfs and `/proc`,
copied from the sampler's own fixtures), never by a live host. SP-4's wire-size measurement and
the numbers that confirm decision D4 are in [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md).

The test-quality claim for these crates is a hand mutation campaign (Rust R2 is not adopted
yet), recorded in [`docs/HAND-MUTANTS-M1.md`](docs/HAND-MUTANTS-M1.md) (twenty-one mutations,
all killed), [`docs/HAND-MUTANTS-M1B.md`](docs/HAND-MUTANTS-M1B.md) (thirty-one mutations, all
killed) and [`docs/HAND-MUTANTS-M1C.md`](docs/HAND-MUTANTS-M1C.md) (fifteen mutations over the
store's bounds and crash-safety guards, all killed). Each is a stored patch in a replayable
script, and every restore is SHA-256-verified.

Open items found while building these packages are in
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md) (`HOB-1` … `HOB-40`): the
sharpest still-open ones are `HOB-17`/`HOB-27` (the `kernel` and `store` crates carry their
own value and quality types until the Rust lane image can be rebuilt against a new
`Cargo.lock`), `HOB-21`/`HOB-29` (no `/sys` device-topology source, and `store`'s tmpfs check
reads the mount table rather than `statfs`), `HOB-39` (a store range answer is the stored
payload, not decoded `model` rows — the join waits for the same rebuild), `HOB-4` (the Python
conformance run is still a manual check, not a registered lane) and `HOB-30` (no session
spools, which are out of this package's scope). `HOB-26` states the SP-2 limit plainly: the
kernel-side rate arithmetic is a test-local helper, because `kernel` cannot depend on `model`
(`HOB-17`).

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
