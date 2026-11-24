# hostobs — known issues, TODO, and backlog

IDs are `HOB-<n>`, allocated sequentially in this file; no other allocator exists.
Uniqueness was checked against `origin/main` and every worktree in this repository
before the first entry was filed (the only other `HOB-` spellings are the
`HOB-WS`/`HOB-FRONT`/`HOB-LEVELS` work-package names in
`docs/design/telemetry-platform/DESIGN-v2-2026-11-07.md`, which carry no number).

Normative behavior lives in [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md).
Only OPEN items are kept here. FIXED and CLOSED items live in
[`archive/2026-Q4.md`](archive/2026-Q4.md); the archive keeps one line for each and git history
holds the full bodies.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| HOB-1 | Counter wrap is never inferred: every backwards raw counter is a reset | Minor | OPEN (by design, awaiting a declared-width policy) |
| HOB-2 | SP-4 measured bytes only: encode/decode CPU, allocations and maintenance cost unmeasured | Minor | OPEN |
| HOB-3 | `model` and `proto` duplicate the quality/metric vocabularies because the locked image cannot take the path dependency; a shared fixture, not a shared type, binds them | Major | OPEN (closed by HOB-64) |
| HOB-5 | Section version 1 has no history min/max/mean columns; `TIME` carries one value per bucket plus the aggregation code | Minor | OPEN (decided: WIRE.md §3.6/§4.9 D-W10, no layout change; lands with P5b `series_batch`) |
| HOB-7 | `IncarnationRegistry` grows without bound and `end_round` is O(all ids ever minted) | Major | OPEN (before the live sampler; HOB-63) |
| HOB-9 | `derive_rate` carries no boot/epoch/incarnation context, so "no rate across a boundary" is a caller contract only | Major | OPEN (store package) |
| HOB-10 | The metric code tables (`unit`, `scope`, `source`, `aggregation`, kind mask) are duplicated in `model` and `proto` and only the quality table is fixture-bound | Major | OPEN (closed by HOB-64) |
| HOB-11 | SP-4's sampler arm is a reproduction with synthetic delta magnitudes, and the example never runs in a lane, so its numbers can drift unnoticed | Minor | OPEN |
| HOB-12 | Rust R2 is still `cargo-mutants`-less: hand mutants are the substitute, and only the fix round's patches are replayable | Minor | OPEN (R-3b) |
| HOB-13 | Small code-hygiene items found at review (indexing, `row + 1`, relabel validation, error names, `O(n×m)` retirement scan, `Unit::Ratio` for load average) | Minor | PARTLY FIXED, rest OPEN |
| HOB-17 | `kernel` carries its own `Reading`/`QualityCode` because a path dependency to `model` rewrites the locked `Cargo.lock`; a shared fixture, not a type, binds them | Major | OPEN (closed by HOB-64) |
| HOB-18 | Cgroup identity uses `st_dev` rather than `statx`'s mount id, and `starttime` ticks need a caller-supplied `CLK_TCK` | Minor | OPEN (deliberate; see the entry) |
| HOB-19 | No package converts `kernel::Reading` into `model::Sample`, so that boundary has no test | Minor | OPEN (HOB-63 sampler package) |
| HOB-21 | `kernel`'s `TopologySource` has no `/sys/dev/block/<dev>/slaves` implementation; the caller supplies one | Minor | OPEN (probe package) |
| HOB-22 | The kernel readers are exercised against fixture trees only: no lane runs them against a live host, and the sampler's worker-pool scheduling is not ported | Minor | OPEN (deliberate) |
| HOB-26 | The SP-2 rate arithmetic is a test-local helper, not `model::derive_rate`, because the crates cannot depend on each other | Minor | OPEN (closed by HOB-64) |
| HOB-27 | `store` cannot depend on `proto`/`model`: a path or dev dependency rewrites the locked `Cargo.lock`, so the crate re-implements four frame-header checks and carries its own quality table | Major | OPEN (closed by HOB-64) |
| HOB-28 | A segment's records are found by scanning every record body, not from a footer index; a range query re-reads each candidate file from the start | Minor | OPEN (folded into the HOB-62 history read-view design) |
| HOB-29 | The `require_tmpfs` check reads `/proc/self/mounts` and matches path prefixes; it does not ask `statfs` what filesystem the directory is actually on | Major | OPEN |
| HOB-30 | No session spools: a crashed probe's rate/span continuation (`§C.4`'s spool) has no store at all | Major | OPEN (out of this package's scope) |
| HOB-31 | `StoreDir`'s symlink refusal is a `symlink_metadata` check followed by a separately-named open, so a swap between the two is not prevented (`O_NOFOLLOW`/`openat` need `unsafe` or a dependency) | Minor | OPEN (needs the `HOB-27` refactor or an `unsafe` decision) |
| HOB-32 | No fault injection: a partial `write`, a failed `fsync` or `ENOSPC` mid-seal is exercised only as the typed `no-space` mapping, not as a real short write | Minor | OPEN |
| HOB-33 | The single writer is a design property (one `Store` owns the queue), not something a test can observe: no lane starts two writers on one directory | Minor | OPEN (daemon package) |
| HOB-34 | Retirement only knows the incarnations this store's own segments name, so a consumer holding an incarnation outside the store (a spool, a reply in flight) cannot prevent `retire` from freeing it | Minor | OPEN (documented limit) |
| HOB-35 | The events ring is RAM-only: its displacement is counted but not persisted, so a consumer that restarts knows how many events it lost, not which | Minor | OPEN |
| HOB-36 | Events are keyed by `(issuer, key)` strings rather than dictionary incarnations, so an event's cgroup identity is a label a caller supplies, not an identity the store can validate | Minor | OPEN (probe package) |
| HOB-37 | Span abandonment is a caller call (`abandon_issuer`); nothing in the store ties it to an incarnation disappearing, so `inferred: true` closes only happen when the caller loops over them | Minor | OPEN (probe package) |
| HOB-38 | A full writer queue always drops the oldest job; there is no blocking or sample-shedding policy, which is a product choice to revisit with the probe's own loop | Minor | OPEN (probe package) |
| HOB-39 | A range answer is the stored payload bytes (a whole `proto` BLOCK) plus the record's time and incarnations, not decoded `model` values with quality codes: the join needs `proto`/`model`, which `HOB-27` forbids | Major | OPEN (folded into the HOB-62 history read-view design) |
| HOB-40 | No focus tier is configured by `plan_v4`; the tier machinery accepts one, but the plan does not yet say what a focus tier keeps | Minor | OPEN (plan decision) |
| HOB-41 | No golden fixture has a non-zero record pad, so the padding rule is pinned by unit tests on each side, not by a cross-language file | Minor | OPEN |
| HOB-45 | Rust R2/R3 remain unregistered for hostobs: no `assay-r2` (cargo-mutants ingestion) or `assay-r3` (canary) lane exists, so test quality rests on the hand-mutation campaigns | Major | OPEN (assay R-3b/R-4) |
| HOB-54 | The probe's serving loop does not exist yet; `FrameReader`, `ModeDetect`, `ConnTimers`, `StreamDicts`, `SendHub`, the envelope checks, the Rust client (`crates/client`) and the Python client (reader, decoder, `StreamDicts`, `rate()`, `conn.py`, `profiling.py`, `mark.py`) exist | Major | OPEN (closes at the HOB-63 live slice) |
| HOB-59 | `Tier::append` and `Store::evict` drop the partial `Evicted` (counts and notes) when an eviction fails after a partial pass | Minor | OPEN |
| HOB-60 | `Store::notes` visibility is decided by name, not by directory, and the store's eviction-notes ring is redundant with the tiers' `scan.notes` through the public API | Minor | OPEN |
| HOB-61 | `json.rs` `skip_ws` / `string_end` `+=` to `*=` are pure infinite loops: suite-level TIMEOUT under `assay-r2` by nature (accepted known limit; expected 0 MISSED + 2 TIMEOUT) | Minor | OPEN (accepted; moot after HOB-68) |
| HOB-62 | No production history, process or provenance source | Major | OPEN (2026-11-24 Opus design review) |
| HOB-63 | M2 ordered consumer-first: P5-live slice before P5b-3 merges | Major | OPEN (2026-11-24 Opus design review) |
| HOB-64 | Workspace unification (crate half of plan P4) | Major | OPEN (2026-11-24 Opus design review) |
| HOB-65 | No server-side pending requests | Major | OPEN (2026-11-24 Opus design review) |
| HOB-66 | Probe process/thread model undocumented | Major | OPEN (2026-11-24 Opus design review) |
| HOB-67 | Serving core in the wrong crates | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-68 | Three hand-written JSON readers | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-69 | FixedBackend call-tuple fakes | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-70 | WIRE.md not protocol-only | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-71 | Budget lattice above workload | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-72 | JSON mode only at observer/control | Minor | OPEN (2026-11-24 Opus design review) |
| HOB-73 | Hub fixtures over-layered | Minor | OPEN (2026-11-24 Opus design review) |

---

## HOB-26 — SP-2's rate arithmetic is a test-local helper

**Where:** `crates/kernel/tests/sp2_evidence.rs` (`rate`), `docs/DESIGN-GUIDE.md`.

The SP-2 tests compute a rate as `delta / (mono_ns / 1e9)` in a local helper. They cannot call
`model::derive_rate`: `kernel` has no dependency on `model` (the `HOB-17` refactor is pending; the registry-set image
now tolerates the lock edit a path dependency causes). So SP-2's
identity and clock claims are evidenced against the kernel's own types and hand-computed
arithmetic, while `model`'s classifier keeps its own `fixtures/rates/rates.json` cases; the two
sides are not joined by a test. The limit is stated in the SP-2 record rather than papered over.

**Exit criteria.** A package that owns both crates (the probe, or a reborn image with a new lock)
derives a rate from a `kernel::Reading` pair through `model::derive_rate` and asserts that the
hand-computed SP-2 values agree with it.

---

## HOB-17 — `kernel` carries its own value and quality types

**Where:** `crates/kernel/src/{value,quality}.rs`; `crates/kernel/Cargo.toml`.

Plan v4 §A.3 says the collectors produce `model` values. A path dependency
`hostobs-model = { path = "../model" }` — even as a *dev*-dependency — makes Cargo rewrite
`Cargo.lock` (the kernel package gains a `dependencies` entry), and the Rust lane image bakes the
whole-lock digest (`/usr/local/share/hostobs-Cargo.lock.sha256`, the identity of an image built
before the registry-set identity), so `cargo --locked` then fails with
"cannot update the lock file". Hand-editing the lock is forbidden, and an image built before the
registry-set identity needs a rebuild. Measured in this package in a throwaway container: adding the dev-dependency rewrote the
lock; removing it restored the digest. The image source now bakes a registry-set digest instead
(`/usr/local/share/hostobs-Cargo.lock.registry-sha256`, which ignores workspace members); the Rust
lanes run on `tester-unified-rust:rs-20261118`, which carries it, so a workspace-only lock edit needs
no rebuild. An image built before the registry-set identity still has the failure described above.

The crate is therefore dependency-free and carries a three-variant `Value` and a `QualityCode`
newtype. They are not parallel vocabularies by accident: `crates/kernel/tests/quality_table.rs`
asserts the table byte-for-byte against `fixtures/wire/quality.json`, the same fixture `model`,
`proto` and the Python decoder assert, so a renumbering fails in four places.

**Exit criteria.** The lanes already run on the registry-set-identity image, which carries a new lock (a workspace-only lock edit needs no rebuild); remaining: make `kernel` depend on
`model`, delete `crates/kernel/src/{value,quality}.rs`, convert `Reading` into `model::Sample` at
that boundary, and delete the local table assertion (the shared fixture stays).

## HOB-18 — `st_dev` where the plan says mount id, and a caller-supplied tick rate

**Where:** `crates/kernel/src/identity.rs` (`CgroupIdentity`, `ClockTicks`).

Plan v4 §C.1 keys a cgroup `(mount id, cgroup inode)`. `stx_mnt_id` is only reachable through
`statx`, i.e. a syscall, which without `unsafe` this crate cannot make (and `#![forbid(unsafe_code)]`
is in force). `Meta` therefore carries `st_dev`, the filesystem instance's id. For cgroupfs that
keeps the rule's purpose: a device id does not change while a cgroup lives, and it separates an inode
number reused on another filesystem. The residual difference is two *separate mounts* of the same
filesystem instance sharing `st_dev`; that matters only when two cgroupfs mounts are visible to one
probe at once, which no consumer does today.

`CLK_TCK` (`sysconf(_SC_CLK_TCK)`, normally 100) is the other host fact this crate does not invent:
`ClockTicks` carries it, the caller supplies it, and `ClockTicks::unknown()` makes every tick-to-ns
conversion a stated absence instead of a guess at 100.

**Exit criteria.** Either a `statx` wrapper confined to one small module under the plan's `unsafe`
allowlist (with the use-case that needs it: two cgroup mounts), or a package that supplies both
inputs from `/proc/self/mountinfo` and a read `CLK_TCK`, with a test that the mismatched-mount case is
distinguished.

## HOB-19 — No package converts a kernel `Reading` into a model `Sample`

**Where:** `crates/kernel/src/value.rs`, `crates/model/src/value.rs`.

The two types are structurally equivalent (a three-variant value plus a closed quality code) and no
code joins them yet: `bins/probe` is still the M0 scaffold. The conversion is therefore untested
rather than wrong, and HOB-17 records why a test cannot simply depend on both crate types today.

**Exit criteria.** The probe (or any package) performs the conversion, with a test asserting every
`Value` variant and every quality code round-trips, including that a `missing` reading never becomes
a measured `Sample`.

## HOB-21 — No `/sys` topology source

**Where:** `crates/kernel/src/cgroup.rs` (`TopologySource`, `UnknownTopology`, `FixedTopology`).

The trait is the seam, not the implementation: `UnknownTopology` and `FixedTopology` exist for tests
and for a caller without `/sys` access, and no reader in this crate reads
`/sys/dev/block/<dev>/slaves`. Until one does, every production `io.*` aggregate is
`unknown-topology` — an honest state, and the reason the fix in HOB-20 (archived) cannot silently
regress.

**Exit criteria.** A `SysTopology` implementation that reads `slaves` (and `/sys/dev/block/<dev>/`
attributes for the device's own kind), with a fixture `/sys` tree and a test that a dm device and its
underlying disk are counted once.

## HOB-22 — The readers are exercised against fixture trees only

**Where:** `crates/kernel/tests/fixtures/` (cgroupfs and proc), `crates/kernel/src/cmdline.rs`.

Every reader runs against committed fixture trees, which is what makes the suite host-independent
(and is the rule this estate requires). What no lane does is run them against a **live** host, so a
kernel that formats a file differently from the fixture would be found only by a consumer. Plan §F.3
and SP-3 want a live acceptance probe with declared bounds; that is the probe package's job.

The sampler's scheduling around `/proc/<pid>/cmdline` (a three-worker pool, a 64-slot queue, one read
in flight per pid, a 2 s deadline) is deliberately **not** ported: `kernel` owns no threads, and a
caller that wants that shape implements it. The privacy contract *is* ported
(`cut_to_word` + `redact`, with the sampler's own test cases), so the part that can leak a password
is the part that came across.

---

## HOB-7 — The incarnation registry grows without bound

**Where:** `crates/model/src/identity.rs` (`IncarnationRegistry`).

`entries` is a `Vec<Option<Entry>>` indexed by id so `snapshot`/`is_active` are O(1), but a
retired slot stays as `None` for the life of the process and `end_round` walks every slot
ever created. Ids are never reused, which is the correctness property; the cost is size and
time. At the plan's 10 cgroup churn per second that is roughly 860k slots per day, and
`end_round` is O(all ids ever minted) once per scan round. Plan §A.4/SP-3 requires a bound
on every structure that grows with churn.

**Fix shape.** Key live entries by id in a `BTreeMap<Incarnation, Entry>` (or a `HashMap`
plus a dense "active id" list for iteration) so retirement actually frees the slot, and keep
a compact reverse index for `incarnation_of`. `end_round` then walks live entries only. The
observable behaviour must not change: ids stay monotonic, a retired key re-minted gets a
fresh id, two absent rounds retire, double `end_round` is a no-op — all covered by existing
tests.

## HOB-9 — `derive_rate` has no boot/epoch/incarnation context

**Where:** `crates/model/src/rate.rs`.

Plan §C says no rate may cross a reset, a boot, an epoch change or an incarnation change.
Inside `derive_rate` the two readings are bare `(Sample, MonoTime)` pairs, so that rule is
the *caller's* contract: the function cannot see a boot id, an epoch or an `inc`. A caller
that pairs readings from two different incarnations gets a plausible, wrong rate.

Related and worth stating together: a sample stored with `Quality::Reset` keeps its raw
bytes, but `Sample::value()` returns `None` for it, so "the current reading is the new
baseline" only works if the caller keeps that reading as an `Ok` sample.

**Fix shape.** A `Baseline { boot, epoch, inc, sample, at }` guard in the store/probe
package (the next package owns the segment), checked before `derive_rate` is called, so a
cross-boundary pair is refused rather than averaged. Document the caller contract in
`docs/DESIGN-GUIDE.md` until the guard exists.

## HOB-10 — The metric code tables are duplicated and only quality is fixture-bound

**Where:** `crates/model/src/metric.rs` (`Unit`, `Scope`, `Source`, `Aggregation`,
`Metric::aggregation_mask`) and `crates/proto/src/dict.rs` (the same closed sets as wire
codes).

The quality vocabulary is bound across the two crates (and the Python decoder) by
`fixtures/wire/quality.json`. The metric tables are not: a unit code added in `proto` and not
in `model` (or a renamed scope) drifts silently, caught only if the corresponding crate's
own tests happen to name the value. This is the same root cause as HOB-3 (no dependency edge
was possible under the old whole-lock image; the refactor is pending) but a different consequence: HOB-3 is about the quality
table, this is about every other code table.

**Fix shape.** A `fixtures/wire/metric-vocabulary.json` listing every `unit`/`scope`/
`source`/`aggregation` name and its wire code plus the per-kind mask, asserted by
`model`, `proto` and the Python decoder — the same three-way binding the quality fixture
already uses. Then a rename in one language is a red test in all three.

## HOB-11 — SP-4's baseline arm is a reproduction, and nothing runs the example

**Where:** `docs/SP-4-WIRE-SIZES.md`, `crates/proto/examples/sp4_wire_size.rs`.

Two caveats the document states, and one it should enforce:

- The sampler byte counts come from a faithful re-implementation of the sampler's varint
  encoding written inside the example, **not** from running the sampler. That is stated in
  the doc; it means the sampler arm is a reproduction, not a measurement of shipped code.
- The sampler's per-tick *delta* magnitudes are synthetic, so its varint size depends on
  invented values. The raw-counter line is the robust like-for-like comparison, and the D4
  verdict rests on that line. This is stated too.
- The example is compiled by `clippy` but **never executed in a lane**, so the published
  numbers can drift silently if either encoding changes.

**Fix shape.** Either a `selftest`-visible assertion on the deterministic column-side byte
counts at 100 and 1,000 cgroups (which is a pure function of the format), or a dedicated
step in a lane that runs the example and compares its output with a committed snapshot. The
CPU/allocation half stays in HOB-2.

## HOB-12 — Hand mutants are recorded as prose, not as replayable patches

**Where:** `docs/HAND-MUTANTS-M1.md`.

The M1 campaign (21 mutations, all killed) is recorded as a table of labels and verdicts
plus SHA-256 restore checks. That is honest evidence of *what* was done, but the exact
mutation text is not stored, so nothing can replay or re-verify it: a later reader has to
trust the prose, and a shifted line number makes a manual replay ambiguous. Rust R2
(`cargo-mutants`, R-3b) remains the real mechanism; this is its substitute until then.

**Progress in the fix round.** The fix round's 15 mutations (Rust and Python, both wiring
directions) are stored as a replayable script rather than prose — see
`docs/hand-mutants/fix-round-mutants.py` and the table in `docs/HAND-MUTANTS-M1.md`. The
M1 campaign's own patches are still prose; converting them is the remaining work.

## HOB-13 — Small code-hygiene items from review

**Status: partly fixed in the fix round.** Items and their state:

| Item | State |
|---|---|
| `&bytes[FRAME_HEADER_LEN..]` in `decode_block` (safe, but an index) | **FIXED** — replaced by `.get(..)` on the same call |
| `Column::sample` does `need: row + 1` (can overflow at `usize::MAX`) | **FIXED** — uses `checked_add` |
| `intern`'s relabel path skips `validate` | OPEN (the encoder still refuses an over-long label, so no bad bytes escape) |
| The relabel/duplicate refusal reused `ConflictingColumn { metric: index }` | **FIXED** — the wire path now has `DuplicateDictionaryEntry { index }`; the live-table relabel conflict keeps `ConflictingColumn` because it is a different condition |
| `retire_unreachable` does `reachable.contains` inside a loop (O(n×m)) | OPEN (bounded by 65,536 entries today) |
| `forbid(unsafe_code)` only via `[lints] workspace = true` | OPEN (belt-and-braces `#![forbid(unsafe_code)]` per crate) |
| `host.load1/5/15` declared with `Unit::Ratio` whose symbol is `%` | OPEN (a load average is not a percentage; needs a unit that says "count per core" without inventing one) |
| `sampler_baseline()` is a subset of the sampler's metrics | OPEN (M1 baseline; disk/procs/per-device lines are later packages) |
| `BootIdLength.len` counts bytes, not characters | OPEN (documented in the variant; the id is ASCII by definition) |
| `SampleRow.value` is a public field, so a placeholder can be read without `measured()` | OPEN (documented hazard; the golden fixture pins the absent case) |

---

## HOB-1 — Counter wrap is never inferred

**Where:** `crates/model/src/rate.rs` (`derive_rate`), crate docs decision 1.

**What.** A raw `u64` counter that decreased is classified `NoRate::CounterReset`
unconditionally. A genuine hardware/kernel wrap (for example a 32-bit device counter
crossing its maximum) is therefore reported as a reset: the current reading becomes the
baseline and no delta is produced.

**Why it is open rather than a bug.** Plan v4 §C.3 forbids invented readings. A wrap
cannot be distinguished from a reset without a *declared* counter width and a
plausibility bound (a decrease of at most `width - 1`, at most once per interval). Any
guess is a silently wrong rate, which is exactly what §C.3 and estate doctrine
("defaults and fallbacks are hazards") forbid. The classifier is the single place such a
policy would be added: one extra parameter per metric in the registry plus one branch
here, and the golden `fixtures/rates/rates.json` cases would grow a declared-width
section.

**Exit criteria.** A metric registry field (`counter_width`), a wrap branch with its own
`Quality`/`NoRate` outcome, fixture cases for "wrapped exactly once", "decreased by more
than the width", and "decreased twice in one interval", and a doc decision that replaces
crate docs decision 1.

## HOB-2 — SP-4 measured bytes only

**Where:** [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md),
`crates/proto/examples/sp4_wire_size.rs`.

SP-4 settled **D4** (fixed-width typed columns) on wire size: 82.7/80.2 B per row
against the sampler TLV's 50.1/50.9 B (per-tick deltas) and 62.1/62.9 B (raw counters,
like for like). CPU time, allocation counts and the maintenance cost of owning a codec
were **not** measured: the Rust methodology defers benches with a regression gate to a
later package, and maintenance cost is a review judgement.

**Exit criteria.** A bench with a regression gate (not a wall-clock assertion; see the
estate rule that a test result may not depend on host load) for encode and decode on the
100- and 1,000-cgroup shapes, plus a written maintenance-cost judgement at review.

## HOB-3 — The two vocabularies are duplicated, not shared

**Where:** `crates/model/src/quality.rs`, `crates/proto/src/quality.rs`, and the
dictionary field codes in `crates/proto/src/dict.rs`.

`hostobs-model` holds the in-memory value vocabulary; `hostobs-proto` holds the same
closed table for wire codes. `hostobs-proto` deliberately does **not** depend on
`hostobs-model`: adding a path dependency changes `Cargo.lock`, and the Rust lane image
built before the registry-set identity caches exactly the committed lock and bakes its
whole-file digest, so `cargo --locked` refuses to build (`cannot update
the lock file`). The two tables are therefore bound by *behavior*, not by a type: both
assert themselves against the committed fixture `fixtures/wire/quality.json`, and the
independent Python decoder asserts the same file. A divergence in either crate is a red
lane.

**Why it is filed.** The binding is real but expensive to keep: a new quality code must be
added in three places (two crates and the fixture), and a rename in one crate is caught
only if that crate's test runs. It is a configuration fact of the current image, not a
design preference.

**Exit criteria.** Decide the shared-type shape (a dependency edge, or a third
`-vocab` crate both depend on); the Rust lanes already run on an image built on the registry-set
identity (a workspace-only `Cargo.lock` edit needs no rebuild). Record it as a decision in
`hostobs/docs/DESIGN-GUIDE.md`.

## HOB-5 — No history min/max/mean columns

**Where:** `crates/proto/src/frame.rs` `TIMESEC`.

Plan v4 §B.2 says history buckets that aggregate gauges carry `min/max/mean` columns
(sample-weighted). Version 1 of this codec carries one value per bucket, the covered
duration, the sample count and the aggregation code — enough to state "this bucket holds
the mean over 5 samples", not enough to carry the other two aggregates. This is a
deliberate M1 scope cut: the live path (which M1 needs) carries raw values and quality,
and history is an M2 query surface.

**Exit criteria.** Either three extra columns per aggregated gauge metric (a per-bucket
value triple) or a second bucket section kind, decided with M2's history query shape, and
a fixture case.

**Decided (WIRE.md §3.6, §4.9, D-W10).** Neither: min, max and mean of a gauge are separate
history blocks of the same subject, one aggregation per block, packed subject-major then by
the request's agg order; the existing bucket `agg` code says which. No layout change. Closes
when P5b's `series_batch` serves it and the P2 fixture `history-agg-not-permitted` plus a
three-agg packing test pass.

## HOB-27 — `store` cannot depend on `proto` or `model`

**Where:** `crates/store/Cargo.toml`, `crates/store/src/{format,quality}.rs`, `docs/SEGMENT-FORMAT.md`.

The documented option was a path or dev dependency on `hostobs-proto` (for the frame header) and
`hostobs-model` (for the quality vocabulary). It was tried and refused: a path dependency makes
`cargo --locked` want to rewrite `Cargo.lock`, and the Rust lane image bakes one lock's crate
closure, so the lane refuses before building (`HOB-17` has the same shape for `kernel`). The
store therefore carries its own four frame-header checks (`format::check_payload` plus the
header checks in `format`) and its own eight-entry quality table (`quality::QualityCode`), and
the two are bound to shared fixtures rather than to shared types: a header fact is asserted
against the real `fixtures/wire/*.bin` payloads in `crates/store/tests/golden_segment.rs`, and
the quality table against `fixtures/wire/quality.json` in `crates/store/tests/quality_table.rs`.

**Exit criteria.** The Rust lanes already run on an image built on the registry-set identity (which
admits a lock whose only change is the workspace's own path dependencies); remaining: `store` calls `proto`'s `FrameHeader::parse` and `model`'s `Quality` and the two
duplicated modules are deleted (their tests move with them). Until then every change to the
frame header or the quality vocabulary has to be made in two places, and the fixture tests are
what force that.

## HOB-28 — A range query scans whole records, not a footer index

**Where:** `crates/store/src/segment.rs` (`scan_bytes`), `crates/store/src/tier.rs` (`range`).

A sealed segment's footer states its record count but carries no per-record index, so
`Tier::range` reads each candidate file from its header to its footer and filters by each
record's recorded `real_ns`. Whole-segment skipping by the ring's `first`/`last` timestamps
keeps an unrelated segment from being opened at all, and both a row bound and a byte bound stop
the walk, so a query's *answer* is bounded — its *work* is proportional to the records in the
window's segments. The footer has room for a small index (`docs/SEGMENT-FORMAT.md` reserves the
body for exactly this), so this is a deliberate M1 cut rather than a missing guard.

**Exit criteria.** A footer record index (or a per-record time summary) once M2's history query
shape is known, with the fixture generator and the Python reference extended together, and a
measurement showing the query cost before and after.

## HOB-29 — The tmpfs requirement is decided from the mount table, not from `statfs`

**Where:** `crates/store/src/tmpfs.rs` (`check_tmpfs`, `MountTable::covers`).

`require_tmpfs` for the hot and warm tiers reads `/proc/self/mounts` and asks whether the
longest component-wise matching mount is `tmpfs`. That is the kernel's own record of what an
administrator mounted, and it is readable without `unsafe`, which `statfs` is not. What it does
*not* catch is a directory reached through a bind mount into a tmpfs (the table shows the
source's mount, not the reachable path's), or a mount whose type a kernel reports under another
name (`ramfs`, an overlay on tmpfs). The reader also refuses to guess: a mount whose arc cannot
be found is `mount-not-found`, not "therefore not tmpfs".

**Exit criteria.** A `statfs`-based check (`f_type == TMPFS_MAGIC`) behind the same decision
point, chosen together with whatever `unsafe`/dependency policy the `HOB-27` refactor settles, and a
test tree with a bind-mounted directory.

## HOB-30 — No session spools

**Where:** the whole crate; plan v4 §C.4.

§C.4 wants a crashed probe's counters and open spans to continue across a restart: a session
spool that remembers the last raw reading per metric and the open spans per issuer, so a restart
neither invents a rate from a fresh baseline nor leaves spans unsealed. This package explicitly
does not build it (the brief puts session spools out of scope alongside the daemon, socket,
probe, act and installer). The store is the machinery the spool will use — a tier is a segment
directory with bounds, and spans are a RAM table with a documented bound — but the spool's own
policy (what to persist, when, and what a restart does with it) has no implementation and no
test.

**Exit criteria.** A spool crate or module with its own file format and bounds, a restart test
(record, kill the writer, reopen, continue) and a statement of the guaranteed window.

## HOB-31 — The symlink refusal is a check, not an atomic open

**Where:** `crates/store/src/fsio.rs` (`StoreDir::symlink_metadata`, every operation).

Every operation refuses a path whose `symlink_metadata` says it is a link, which is what plan
v4 §C.5 requires and what the brief's "never follow a symlink out of the store directory"
mutation pins. Between that `symlink_metadata` and the separately-named `open`/`create_new`, a
concurrent actor that can write the directory could swap a regular file for a link and win the
race. Closing it needs `O_NOFOLLOW` (an `unsafe` `libc` call) or `openat`/`fstatat` relative
opens (also `unsafe`, or a dependency), and both are blocked by the same constraints as
`HOB-27`; `fsio` records the limit in its module docs rather than implying an atomicity it does
not have. The threat model bounds it: the store directory is the probe's own (`/run/hostobs`,
`/var/lib/hostobs`), not a shared or attacker-writable path.

**Exit criteria.** `openat`-relative opens with `O_NOFOLLOW` behind one `fsio` function, chosen
with the same `unsafe`/dependency decision as `HOB-27`, plus a test that a link planted between
the check and the open is still refused.

## HOB-32 — No filesystem fault injection

**Where:** `crates/store/src/fsio.rs`, `crates/store/src/error.rs`.

`ENOSPC` is mapped to a typed `no-space` and counted, and that mapping is tested. What is not
tested is a *short write*, a failed `fsync`, a rename that succeeds on one machine and not on
another, or `ENOSPC` arriving in the middle of the seal's streamed copy. The seal is written so
that any of those leaves the open name intact and the final name absent (temp name, footer,
`fsync`, rename, `fsync` the directory, unlink the open name), but the *order's* correctness
rests on the sequence being what `docs/SEGMENT-FORMAT.md` says rather than on an injected
failure. A fake directory handle would need either a trait in `fsio` (which the write-boundary
lint makes affordable, since the only writer is one module) or a real filesystem that can be
made full.

**Exit criteria.** A `fsio` seam a test can fail at a named step, with tests that assert the
observable state after each failure (which names exist, and what the next open reports).

## HOB-33 — The single writer is a property, not an observable

**Where:** `crates/store/src/queue.rs` (`WriterQueue`), `crates/store/src/store.rs`.

Plan v4 §A.4 asks for a single writer; the implementation gets it structurally — one `Store`
owns the queue and `write_pending`/`write_one` take `&mut self`, so the type system refuses two
concurrent drains of one store. Nothing tests the property *across processes*, and no lane
starts two writers on one directory. Two probes (or a probe and a restarted probe whose
predecessor has not exited) on the same `hot`/`warm` directory would each hold a water mark and
each append to a `*.seg.open` name; the second would refuse at `StoreDir`'s `create_new`, which
is the intended outcome, but that outcome is not asserted anywhere.

**Exit criteria.** A documented directory-lock (a `*.lock` file created with `create_new`, held
for the store's lifetime, refused with a typed error) plus a test that a second `Store::open` on
a live directory is refused, and a statement of what a probe crash leaves behind.

## HOB-34 — Retirement cannot see incarnations held outside the store

**Where:** `crates/store/src/store.rs` (`retire`, `reachable`), `crates/store/src/dictionary.rs`.

`retire` computes reachability as the union of what this store's own history tiers and events
tier still name, then frees every ephemeral dictionary entry nobody names. A consumer that
holds an incarnation *outside* those segments — a session spool (`HOB-30`), a reply in flight
over the socket, a record already handed to a client but not yet written — has no way to keep
it alive. The store's reachability is correct for its own bytes and is not a global claim, which
is what the module docs say; the risk is a caller that assumes otherwise.

**Exit criteria.** `retire` takes a caller-supplied extra reachability set (or a registry
handle), with a test that an externally-held incarnation survives a retirement pass.

## HOB-35 — The events ring's displacement is counted but not persisted

**Where:** `crates/store/src/events.rs` (`EventRing`, `EventStore::stats`).

The RAM ring keeps the most recent events under a byte cap and reports how many it displaced
(`displaced`, `displaced_bytes`), which satisfies "a drop is counted". It does not say *which*
ones, and the counter does not survive a restart: after a reopen a consumer knows the ring is
at its cap, not what it lost before the restart. The persisted events tier is the durable
record, so nothing is lost from the store — but the RAM view's own loss history is only
in-process.

**Exit criteria.** Either a displacement journal (bounded, appended to the events tier) or a
consumer contract that states the count is per-process, plus a test that pins whichever is
decided for a restart.

## HOB-36 — Events are keyed by strings, not by validated incarnations

**Where:** `crates/store/src/events.rs` (`Event::new`, `SpanTable`).

An event's `issuer` is a caller-supplied string bounded to 128 bytes, and spans are keyed by
`(issuer, key)`. §C.4's issuer is a cgroup incarnation, and the store cannot validate one
(it must not depend on `model`'s registry, `HOB-27`), so an issuer string that names a
different incarnation than the caller meant — or that two issuers happen to share — merges
their spans. A consumer that derives the issuer string from the identity (`<boot>-<pid>-
<starttime>`, say) gets the right behaviour; the store cannot check that it did.

**Exit criteria.** An issuer identity the store can validate (a dictionary id, once the probe
owns the join between `model`'s incarnations and the store's dictionary), with a test that two
incarnations reusing one path never merge their spans.

## HOB-37 — Span abandonment is a caller's loop

**Where:** `crates/store/src/events.rs` (`SpanTable::abandon_issuer`), `crates/store/src/store.rs`.

`abandon_issuer` produces the inferred `abandoned` end events for one issuer's open spans, and
`Store` exposes the table, but nothing ties abandonment to an incarnation disappearing: the
probe's scan round has to notice the disappearance and call it. If it does not, spans stay open
until the per-issuer bound refuses new ones, which is a visible refusal rather than silent loss
— but the `inferred: true` record the plan requires then never exists.

**Exit criteria.** A round-taking entry point (`Store::end_round(disappeared: &[issuer])`) that
abandons automatically, with a test that a vanished issuer's spans are closed as inferred and
that a *live* issuer's are not (`HOB-7`'s one-absent-scan rule).

## HOB-38 — A full writer queue drops the oldest job, always

**Where:** `crates/store/src/queue.rs` (`WriterQueue::submit`).

When the queue is at `writer_queue_bytes`, `submit` drops the oldest job, returns it and counts
it; a job larger than the whole bound is refused. That is one policy, chosen because it keeps
the bound after every operation and because the newest record is the more useful one to keep.
Alternatives the plan leaves open — blocking the producer, dropping the *newest*, or shedding
at a lower rate (decimation with a `NOT_SAMPLED` count) — are not implemented, so a probe whose
writer falls behind always loses the oldest measurements rather than the newest.

**Exit criteria.** A policy code in the store's config (`drop_oldest | drop_newest | block`) with
a bounded block timeout and a decimation counter reported as `NOT_SAMPLED` values, decided with
the probe's own scheduling observation.

## HOB-39 — A range answer is payload bytes, not decoded `model` values

**Where:** `crates/store/src/segment.rs` (`ScannedRecord`), `crates/store/src/store.rs` (`StoreRange`).

The brief asks a range query to return "`model` values with quality codes intact". The store
returns the **payload** it was given — a complete `proto` BLOCK frame, byte-identical to what
the writer wrote — plus the record's own time and incarnation list. The quality bytes are
inside that payload, so nothing is lost, but a caller must decode it: the store has no
`build_dispatch`-shaped join to `model`, because it may not depend on `proto` or `model`
(`HOB-27`). `quality::Reading`/`QualityCode` exist as the store's own vocabulary (used by
nothing yet, asserted against `fixtures/wire/quality.json`), so the types are ready for that
join to land on top of them.

Doing the join inside the store would mean a decoder and a value model in this crate — the
duplication `HOB-27` already forces on four header facts, multiplied by the whole column codec,
which is the wrong trade while the `HOB-27` refactor is pending.

**Exit criteria.** After the `HOB-27` refactor (the lanes already run on the registry-set-identity image), `Store::range` (or a layer above
it) returns `model` rows with their quality codes and the payload form becomes an internal
detail; `quality::Reading` is then replaced by `model`'s type.

## HOB-40 — No focus tier is configured by `plan_v4`

**Where:** `crates/store/src/store.rs` (`StoreConfig::plan_v4`), `crates/store/src/tier.rs`.

Plan v4 §A.4 and the brief name "focus tiers" among the per-tier directories. The machinery is
general — `TierConfig::new(name, kind, segment_s, keep_s, cap_bytes, require_tmpfs)` plus
`StoreDirs` accepts any tier set — but `plan_v4` configures exactly hot, warm, cold and events,
because the plan does not yet say which focus tiers exist, what they keep or where they live;
inventing that here would be a default standing in for a product decision. A caller that wants
one adds it to `StoreConfig::tiers` and points `dirs` at a directory (the `tmpfs` field covers
the shared hot/warm directory, so a focus tier outside it needs a `StoreDirs` extension).

**Exit criteria.** The plan states the focus tier's trigger (a queried cgroup, an operator flag,
a window), its `segment_s`/`keep_s`/`cap_bytes` and its directory; `plan_v4` configures it and a
test asserts a record for that cgroup lands there and is retained past `hot`'s window.

## HOB-41 — No golden fixture has a non-zero record pad

**Where:** `fixtures/segment/`, `python/reference/make_segment_fixtures.py`.

Every committed fixture's record bodies are multiples of 8 bytes, so every `pad` byte in the
corpus is `0`. The padding rule (`pad = (8 - body % 8) % 8`, zero bytes, inside the record's
total length) is therefore pinned only by the Rust unit test that walks body lengths 0–63 and by
the Python encoder's own round trip — the two implementations agree on what they each generate,
but no committed pair of files proves they agree with each other on a padded record. A real
cross-language case needs a body (an id table plus a payload) whose length is not a multiple of
8, which the shared `fixtures/wire/*.bin` payloads are not.

**Exit criteria.** One committed fixture with a non-zero `pad`, generated by the Python encoder
and decoded by `golden_segment.rs`, plus the Python decoder reading the Rust writer's padded
output.

## HOB-45 — Rust R2 and R3 are unregistered for hostobs

**Where:** `hostobs/run-gate.toml` and `hostobs/assay.toml` (no `assay-r2`/`assay-r3`
lane); assay `nyxloom-trove/4-backlog.md` B245 (R-3b, cargo-mutants ingestion) and B246
(R-4, Rust canaries).

With `assay-r1` registered the workspace has an assay-judged R1 line verdict. The
remaining two tiers do not exist for Rust in assay at all: R2 needs
the `cargo-mutants-outcomes` report parser and the `ASSAY_CHANGED_DIFF` handoff (B245), and
R3 needs the rustc `UNCOVERED_LINES` injector plus a receipt-writing test-break canary
(B246). hostobs therefore has no mutation or canary lane, and the test-quality claim is the
three hand-mutation campaigns in `docs/HAND-MUTANTS-M1*.md` (eighty-two stored, replayable
mutants). This is recorded once, as a known issue, not as a per-release waiver.

**Exit criteria.** An `assay-r2` lane (diff-scoped cargo-mutants, T3, `--base <previous
release tag>`) and an `assay-r3` lane (test-break canary with a receipt) exist here, pass
their registered gates in `tester-unified-rust`, and the hand-mutation scripts are retired or
reduced to the survivors those lanes cannot express.

**Source.** Filed 2026-11-15 by the hostobs assay-r1 adoption; assay A-501 (Rust R2 not
adopted is a known issue), `assay/docs/RUST-METHODOLOGY.md`.

## HOB-54 — The Rust client and the probe's serving loop do not exist yet

**Where:** `crates/proto/src/stream.rs` (new), `python/hostobs_client/stream.py` (new), the P5b serving
loop. WIRE.md §5.2-§5.4, §12.

`proto` decodes single frames only. The frame reader (WIRE.md §5.2), mode detection and send hub
(§5.3: pin-time `dropped` stamping, coalescing, the `last_pushed_gen` form rule, Live/Query/control
pools with per-uid caps, frame completion and stall closes) and the connection timers (§5.4) are
specified with fake-clock tables but have no code, so no stream property of `hostobs/1` is tested.

**What exists (P2a2).** `crates/proto/src/stream.rs` holds `FrameReader` (the §5.2 machine, one unit
test per row), `ModeDetect` (the §5.3 mode-detection rows) and `ConnTimers` (one test per §5.4 timer
row); `ProtoError::Deadline` is the reader's completion-age error. The 12 reader cases are
`fixtures/stream/reader-*.json` (generator `python/reference/make_stream_fixtures.py`, replayed by
`crates/proto/tests/stream_fixtures.rs`).

**What exists (P2b).** `crates/proto/src/hub.rs` holds `SendHub` (one unit test per §5.3 row and
consequence) and the 19 `fixtures/stream/hub-*.json` files (generator
`python/reference/make_hub_fixtures.py`, replayed by `crates/proto/tests/hub_fixtures.rs`).

**What exists (P2c).** `crates/proto/src/stream_dicts.rs` holds `StreamDicts` (the §6.2 per-stream rules and
`on_hello`; the 6 client cases are `fixtures/stream/client-*.json`, generator
`python/reference/make_client_stream_fixtures.py`, replayed by `crates/proto/tests/client_stream_fixtures.rs`)
and `crates/proto/src/envelope.rs` the REQ/REP/END/EVT shape checks.

**What exists (P3a).** `python/hostobs_client/stream.py` holds the Python `FrameReader` (the §5.2 machine
with the §7.3 `fill()`/`advance()` buffering, replaying the 12 reader cases) and `StreamDicts` (§6.2,
`on_hello`, replaying the 6 client cases); `wire.py` and `rates.py` carry the efficient decoder and `rate()`.
**What exists (P3b).** `conn.py` (`Connection`, the §7.3 receive loop, block streams, subscriptions, reconnect),
`envelope.py`, `profiling.py` (`require_profiling`, the five plan D.2 states) and `mark.py` (`hostobs-mark`),
tested on a scripted fake probe and by a zipimport smoke test: the P3 part is closed.
**What exists (P5a).** `crates/client` (`Client`, `Level`, `ClientInfo`, `HelloResult`, `BlockStream`,
`Subscription`, `ClientError`; the injected `Transport` and `Clock`), tested against scripted servers over a fake
clock: the P5a part is closed.
**What exists (P5b-2c + P5b-2d).** The probe's `Conn` now executes `current`, `subscribe` and
`unsubscribe`, tracks outstanding requests and Draining, and the `Server` owns shared selection
streams, `on_tick` delivery and the retry pass after `wrote` and `poll`. The three verbs are served
at observe/operator with the matching `hello.verbs`, and the block-verb cases of WIRE.md §11.4 run
against the in-memory `Conn`/`Server`. The P5b-2c and P5b-2d parts are code complete; P5b-3
(`snapshot_at`/history and `conn-reply-page-truncated`) and P5b-4 (the actual socket drivers and
the socketpair runs) remain.

**Exit criteria.** P2: the §12 `FrameReader`, `ModeDetect`, `SendHub` and `ConnTimers` APIs in Rust
with the reader and hub cases of WIRE.md §11.4 and one test per §5.2/§5.3 row; P3: the Python
`FrameReader` passes the same reader cases and the client cases; P5b: the 21 connection cases
now listed in §11.4 against a fake clock, with P5b-3 and P5b-4 still required for the remaining
page and socketpair cases.

## HOB-59 — A failing eviction drops the partial `Evicted`

**Where:** `crates/store/src/tier.rs` (`Tier::append`, `Tier::evict`),
`crates/store/src/store.rs` (`Store::write_one`, `Store::evict`, `evict_each`).

`Tier::evict` returns `Err` from `remove_segment` when a live segment cannot be removed, after
earlier entries of the same pass were already removed or reported. The `Evicted` of that partial
pass (its counts, bytes and notes) is lost, so `StoreStats::evicted_*` and `Store::notes()` miss
what the pass did before the refusal, and `Tier::append` returns the error although its record is
on disk. `stats.bytes` itself stays true (it is recomputed from the tiers), as do the tiers'
own `scan.notes`.

**Exit criteria.** The partial result of a failed pass reaches the caller: `Evicted` is returned
with the error (or the counters and notes are applied before the error is propagated), the store
counts the partial pass, and a test makes a removal fail after a successful one and checks the
counts and the notes.

## HOB-60 — `Store::notes` visibility is by name, and the notes ring is redundant through the API

**Where:** `crates/store/src/store.rs` (`is_visible`, `Store::notes`, `keep_notes`).

`Store::notes` hides a scan note whose name parses to a tier of the store other than the tier whose
scan made it. That decision is by name, not by directory: a foreign `warm-0-3.seg` in a directory
warm does not scan is hidden although no tier owns it. The store's eviction-notes ring is
redundant with the tiers' `scan.notes` through the public API (no store call rescans a tier). It
is pinned at unit level by `a_write_path_report_stays_in_the_store_after_its_tier_rescans` and
`an_evict_report_stays_in_the_store_after_its_tier_rescans`, which rescan a tier from the tests
module and read the report from the ring; the mutants that leave the ring out or do not fill it
(`M1d-22`) are killed by them.

**Exit criteria.** `is_visible` hides a cross-tier note only when the producer's directory is also
the directory of the tier the name belongs to (shared directory), and a test plants a foreign
`warm-0-3.seg` in cold's directory and sees it; either a store call rescans a tier (so the ring
matters through the API) or the ring is removed together with the two rescan tests and
`Store::notes` reads `scan.notes` alone.

## HOB-61 — Two client mutants are suite-level timeouts under `assay-r2` by nature

**Where:** `crates/client/src/json.rs` (`skip_ws` line 140, `string_end` line 152).

The mutants `+=` to `*=` at those two lines turn a pure scanning loop into an infinite loop that
no in-process test can stop: the dedicated tests (`skip_ws_always_makes_progress`,
`string_end_always_makes_progress`) run the helper on a worker thread and fail after two seconds,
but existing callers in the same test process (for example `array_strings_accepts_strings_only`
and the hello parsing tests) spin on the mutated helper, so the whole suite ends only at the
lane's timeout. Killing them would need a killable child-process harness per probe. Decision
(controller, 2026-11-18): keep it simple, a documented known limit. The lane's timeout classifies
both as TIMEOUT; today each costs the full 120-second floor (assay.toml), and assay B560 (timeout
floor derived from the baseline, still OPEN) would make them cheap once implemented. The
expected `assay-r2` result for `hostobs/crates/client` is 0 MISSED and 2 TIMEOUT.

**Exit criteria.** Either a child-process probe harness kills both loops with a fast FAIL, or the
two mutants are listed as accepted timeouts in the lane's mutation policy so that the lane
verdict does not count them.

## HOB-62 — No production history, process or provenance source

**Where:** `crates/store/src/store.rs` (`Store::range`), `crates/kernel/src/procfs.rs`, and the
M2 history and process serving surface.

The store lacks a committed watermark, an immutable read index and decoded values; there are no
published `/proc` scans. Numeric ceilings are re-derived in `docs/HISTORY-READ-VIEW.md` (P-15),
and no M2 history claim is made until then.

**Exit criteria.** P-16 implements the bounded history read view and P-17 serves it through the
P5b-3 surface after reconciling the parked branch with D5 applied.

Source: .controller-state/design-review-2026-11-24/hostobs.md F1 (The plan builds three protocol packages against a fake before any real source or socket exists)

## HOB-63 — M2 ordered consumer-first: P5-live slice before P5b-3 merges

**Where:** the M2 package order (P5b-3 → P5b-4 → P5c), `docs/P5B-SERVER-DESIGN.md` §7, and the
HOB-28/HOB-39/HOB-62 producer constraints.

HOB-7 first. Then a minimal driver (`LISTEN_FDS`, `SO_PEERCRED`, poll loop), a `kernel` sampler
feeding `Backend::current`, and `current`/`subscribe` shown on a VM. Then the store history read
view, producer-first. Only then is the parked P5b-3 reconciled. *Closes the HOB-54 umbrella;
HOB-62's ceilings move to the P5c-history design.*

**Exit criteria.** P-11 completes and demonstrates the live slice; P-17 delivers the producer-first
history view and reconciles P5b-3. The live half closes at P-11 and the history half at P-17.

Source: .controller-state/design-review-2026-11-24/hostobs.md F1 (The plan builds three protocol packages against a fake before any real source or socket exists)

## HOB-64 — Workspace unification (crate half of plan P4)

**Where:** `crates/{kernel,store}` and their dependency manifests; `bins/probe/Cargo.toml`.

`kernel`/`store` depend on `model`/`proto`. One quality and one metric vocabulary. Delete the
kernel/store copies and the unused probe deps. *Closes HOB-3/10/17/26/27.*

**Exit criteria.** P-04 unifies the dependency edges and vocabularies, moves the deleted copies'
tests to the surviving types, and removes the unused probe dependencies.

Source: .controller-state/design-review-2026-11-24/hostobs.md F2 (Four disconnected crates and duplicated vocabularies; the blocking reason is gone)

## HOB-65 — No server-side pending requests

**Where:** `bins/probe/src/{conn,server}.rs`, WIRE.md §2.3/§5.1/§5.3, D-W30 and D-W47.

History ends early with `END page` + `next`. One-block replies are refused with
`too-many-requests` or `budget`. Delete the retry pass, uid rotation and D-W30, and most of D-W47;
WIRE §2.3/§5.1/§5.3.

**Exit criteria.** P-05 applies the WIRE rule and deletes the request records, retry pass,
uid-rotation machinery and tests that only pin the removed pending path.

Source: .controller-state/design-review-2026-11-24/hostobs.md F3 (Server-side waiting is the complexity centre; drop it from the protocol)

## HOB-66 — Probe process/thread model undocumented

**Where:** `bins/probe/src/server.rs` (`on_tick` and `Backend::current`), the missing
`docs/PROBE-PROCESS.md`, and the P5b-4 driver design.

`docs/PROBE-PROCESS.md`: sampler, writer and server-loop threads; backend calls never block;
eventfd handoff; fix the stale "one mutex". Write it before P5b-4.

**Exit criteria.** P-02 writes the process model and leaves P-08/P-09/P-10 no open thread,
backend, wakeup, activation or shutdown choice.

Source: .controller-state/design-review-2026-11-24/hostobs.md F10 (The probe's process and thread model is not designed, but P5b-4 and P5c depend on it)

## HOB-67 — Serving core in the wrong crates

**Where:** `crates/proto/src/hub.rs`, `bins/probe/src/{conn,server,classify,args,lines,codes,encode,limits,verbs}.rs`,
and the planned `crates/serve`.

Hub, conn, server and the `Backend` traits move to `crates/serve`, before act serves control and
after HOB-65.

**Exit criteria.** P-12 moves the serving core and backend traits to `crates/serve`, leaving
`proto` with the codec, envelope, `FrameReader` and `StreamDicts`.

Source: .controller-state/design-review-2026-11-24/hostobs.md F8 (The serving core sits in the wrong crates for M2b)

## HOB-68 — Three hand-written JSON readers

**Where:** `proto::envelope`, `bins/probe/src/args.rs`, and `crates/client/src/json.rs`.

One reader in `proto::envelope` with `Surrogates::{Refuse,Replace}`. *Makes HOB-61 moot.*

**Exit criteria.** P-13 adds the typed reader and surrogate policy and deletes the duplicate helpers
from probe `args.rs` and client `json.rs`.

Source: .controller-state/design-review-2026-11-24/hostobs.md F9 (Three hand-written JSON readers)

## HOB-69 — FixedBackend call-tuple fakes

**Where:** `bins/probe/src/backend.rs` (`FixedBackend`), the inline `server.rs`/`conn.rs` test
modules, and the frame-size literals in those tests.

Model fake `MemHistory`; sizes from the encoder helpers; split the `server.rs` tests.

**Exit criteria.** P-14 replaces the call-tuple tables with a model fake, moves the inline tests to
`tests/` by concern, and derives sizes from encoder helpers; P-17 carries the history-specific
fake into the reconciled serving surface.

Source: .controller-state/design-review-2026-11-24/hostobs.md F5 (Test fakes keyed by exact call tuples make the suite brittle and misleading)

## HOB-70 — WIRE.md not protocol-only

**Where:** `hostobs/docs/WIRE.md` §12/§13, `P5B-SERVER-DESIGN.md`, and
`P5B2-LIVE-STREAMS-DESIGN.md`.

§12 to rustdoc, review logs out, P5B/P5B2 marked historical after folding; include HOB-49
(archived).

**Exit criteria.** P-18 reduces WIRE to §1-§11 plus one decisions table, moves the implementation
APIs to rustdoc/DESIGN-GUIDE, and marks the package design notes historical.

Source: .controller-state/design-review-2026-11-24/hostobs.md F6 (Normative protocol spread over several documents, with implementation API inside the spec)

## HOB-71 — Budget lattice above workload

**Where:** `bins/probe/src/limits.rs`, WIRE.md §5.1, the per-level and per-uid Live/Query pools.

Probe-wide unified keys; workload-derived defaults; evaluate dropping per-uid pools.

**Exit criteria.** P-06 makes the unified keys probe-wide, derives the D8 defaults and their
worst-case query-RAM bound from the workload, and decides whether the per-uid pools remain.

Source: .controller-state/design-review-2026-11-24/hostobs.md F7 (The budget lattice is larger than the workload, and some per-level knobs cannot take effect)

## HOB-72 — JSON mode only at observer/control

**Where:** WIRE.md §8, `bins/probe/src/lines.rs`, and the mode dimension of the classify grid.

Serve JSON mode only at observer and control; observe and operator are binary-only.

**Exit criteria.** P-06 restricts JSON to observer/control, refuses JSON block verbs at
observe/operator exactly as WIRE says, and shrinks the classify grid and Python client tests to
match.

Source: .controller-state/design-review-2026-11-24/hostobs.md F11 (JSON mode on block-serving levels doubles the connection layer for little use)

## HOB-73 — Hub fixtures over-layered

**Where:** `python/reference/make_hub_fixtures.py`, `fixtures/stream/hub-*.json`,
`crates/proto/tests/hub_fixtures.rs`, and `python/tests/test_hub_fixtures.py`.

Rust table tests; the Python reference becomes an encoder only.

**Exit criteria.** P-18 converts the hub cases to Rust table tests, drops the generator and its
byte-for-byte Python check, and leaves the Python reference as the fixture encoder.

Source: .controller-state/design-review-2026-11-24/hostobs.md F12 (Over-layered test oracles for Rust-only components, and a third Python decoder)
