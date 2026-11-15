# hostobs — known issues, TODO, and backlog

IDs are `HOB-<n>`, allocated sequentially in this file; no other allocator exists.
Uniqueness was checked against `origin/main` and every worktree in this repository
before the first entry was filed (the only other `HOB-` spellings are the
`HOB-WS`/`HOB-FRONT`/`HOB-LEVELS` work-package names in
`docs/design/telemetry-platform/DESIGN-v2-2026-11-07.md`, which carry no number).

Normative behavior lives in [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md).
A `FIXED` entry means code, tests, and docs landed together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| HOB-1 | Counter wrap is never inferred: every backwards raw counter is a reset | Minor | OPEN (by design, awaiting a declared-width policy) |
| HOB-2 | SP-4 measured bytes only: encode/decode CPU, allocations and maintenance cost unmeasured | Minor | OPEN |
| HOB-3 | `model` and `proto` duplicate the quality/metric vocabularies because the locked image cannot take the path dependency; a shared fixture, not a shared type, binds them | Major | OPEN (needs an image rebuild decision) |
| HOB-4 | No registered `conformance` lane: the independent Python fixture check is not gate-enforced | Major | OPEN |
| HOB-5 | Section version 1 has no history min/max/mean columns; `TIME` carries one value per bucket plus the aggregation code | Minor | OPEN (M2 work) |
| HOB-6 | `DICT` frame type is defined but consumed nowhere: the dictionary travels inside the block frame | Minor | OPEN (deliberate) |
| HOB-7 | `IncarnationRegistry` grows without bound and `end_round` is O(all ids ever minted) | Major | OPEN (SP-3 bound) |
| HOB-8 | `FrameHeader::parse` does not bound its declared length; only `decode_block` does | Major | OPEN (next package's streaming reader) |
| HOB-9 | `derive_rate` carries no boot/epoch/incarnation context, so "no rate across a boundary" is a caller contract only | Major | OPEN (store package) |
| HOB-10 | The metric code tables (`unit`, `scope`, `source`, `aggregation`, kind mask) are duplicated in `model` and `proto` and only the quality table is fixture-bound | Major | OPEN |
| HOB-11 | SP-4's sampler arm is a reproduction with synthetic delta magnitudes, and the example never runs in a lane, so its numbers can drift unnoticed | Minor | OPEN |
| HOB-12 | Rust R2 is still `cargo-mutants`-less: hand mutants are the substitute, and only the fix round's patches are replayable | Minor | OPEN (R-3b) |
| HOB-13 | Small code-hygiene items found at review (indexing, `row + 1`, relabel validation, error names, `O(n×m)` retirement scan, `Unit::Ratio` for load average) | Minor | PARTLY FIXED, rest OPEN |
| HOB-14 | `decode_block` accepted a block shape under any frame type, and `encode_block` would emit one | Major | FIXED (M1b) |
| HOB-15 | The section directory's canonical order was checked on encode but not on decode | Major | FIXED (M1b) |
| HOB-16 | A truncated section directory reached the Python decoder's `struct.unpack` and raised `struct.error` instead of a `WireError` | Minor | FIXED (M1b) |
| HOB-17 | `kernel` carries its own `Reading`/`QualityCode` because a path dependency to `model` rewrites the locked `Cargo.lock`; a shared fixture, not a type, binds them | Major | OPEN (needs an image rebuild) |
| HOB-18 | Cgroup identity uses `st_dev` rather than `statx`'s mount id, and `starttime` ticks need a caller-supplied `CLK_TCK` | Minor | OPEN (deliberate; see the entry) |
| HOB-19 | No package converts `kernel::Reading` into `model::Sample`, so that boundary has no test | Minor | OPEN (probe package) |
| HOB-20 | The sampler treated an unknown device topology as a bottom device, double-counting stacked I/O; `kernel` refuses instead | Major | FIXED in `kernel`, sampler worktree unchanged |
| HOB-21 | `kernel`'s `TopologySource` has no `/sys/dev/block/<dev>/slaves` implementation; the caller supplies one | Minor | OPEN (probe package) |
| HOB-22 | The kernel readers are exercised against fixture trees only: no lane runs them against a live host, and the sampler's worker-pool scheduling is not ported | Minor | OPEN (deliberate) |
| HOB-23 | `parse_stat` read `minflt`/`majflt` from proc(5) fields 12/13 instead of 10/12, and the test helper and fixture carried the same wrong table | Major | FIXED (M1b fix round) |
| HOB-24 | `RealFs::read` refused a file by its *reported* length (a sysfs attribute reports one page) and `max_bytes + 1` overflowed at `usize::MAX` | Minor | FIXED (M1b fix round) |
| HOB-25 | `io.stat`'s iocost sums used `saturating_add` (silently clamping at `u64::MAX`) where the counters use `checked_add` | Minor | FIXED (M1b fix round) |
| HOB-26 | The SP-2 rate arithmetic is a test-local helper, not `model::derive_rate`, because the crates cannot depend on each other | Minor | OPEN (documented limit) |

---

## HOB-23 — `minflt`/`majflt` read from the wrong proc(5) fields

**Where:** `crates/kernel/src/identity.rs` (`parse_stat`, `StatLine`), `crates/kernel/tests/fixtures/proc/1/stat`, `crates/kernel/tests/sp2_evidence.rs`.

The reader derived the index correctly (index `i` after `)` is field `i + 3`) and then assigned
`9 => minflt`, `10 => majflt`. Index 9 is field 12 (`majflt`) and index 10 is field 13
(`cmajflt`); the process's own minor faults are field 10 (index 7). A consumer therefore read
the process's major faults as its minor faults and its children's major faults as its major
faults — two wrong numbers that look like measurements. The ported sampler said so explicitly
(`topos/sampler/src/procs.rs:312`, "majflt = field 12"), and the wrong table was reproduced in
the unit-test helper's comments, the committed fixture and the SP-2 assertions, so the tests
pinned the defect rather than catching it.

The fix names fields, not indexes: `parse_stat` reads `10, 12, 14, 15, 20, 22, 24, 42` and
derives every index through `field_index(field)`/`STAT_FIELD_OFFSET` (proc(5) field 3 is index
0), fields 11 (`cminflt`) and 13 (`cmajflt`) are deliberately not read and the doc comments say
so, the fixture and the helper are generated from field numbers, and
`every_stat_field_this_reader_reads_arrives_at_its_proc5_field_number` builds a line where
field `n` carries the value `n`, so every assertion proves its own field. `statm`, `status`,
`loadavg`, `/proc/stat`, `meminfo`, `vmstat`, the PSI files, `io.stat`, `cpu.stat`,
`cpu.max`/`weight`, `memory.stat`/`events` and the limit files were re-audited the same way
(the result is in the fix-round report): the rest are `key value` or `key=value` files whose
field names travel with the value, so a position cannot slip silently, and their tests now use
distinct per-position values as well.

## HOB-24 — A read refused by reported length, and an overflow in the bound

**Where:** `crates/kernel/src/fs.rs` (`RealFs::read`).

The reader checked `metadata.len()` first and refused a file larger than the bound without
reading it. That is a *reported* length, not the fact: a sysfs attribute reports one page
(4096 bytes) whatever its content, so a 20-byte attribute under a 256-byte policy cap was
refused as too large. The same statement computed `max_bytes + 1` unsaturating, so a caller
passing `usize::MAX` (“no bound”) wrapped the read limit to 0 — an empty `Ok`, exactly the
wrong answer that looks like an empty file — or panicked in a debug build.

The fix applies the bound to the bytes actually read (`take(max_bytes + 1)`, then one byte
past the allowance is `TooLarge`) and uses `saturating_add`. `fs::tests::a_file_is_read_within_its_bound_and_a_larger_one_is_refused_unread`
now reads a fixture file with `usize::MAX` and asserts the whole content, and the hand-mutants
campaign replays the wrapping version (`D3`).

## HOB-25 — iocost sums clamped instead of refusing an overflow

**Where:** `crates/kernel/src/cgroup.rs` (`parse_io_stat`).

The device counters summed with `checked_add` and set `bad` on overflow, but the three iocost
sums used `saturating_add`, so an overflowing `cost.usage` was published as `u64::MAX` — a
number the kernel never wrote. Both now use `checked_add` and the same `unreadable` outcome;
`an_overflowing_io_sum_is_unreadable_instead_of_clamped` pins both paths (a counter overflow
and an iocost overflow) and the mutant `D2` replays the clamping.

## HOB-26 — SP-2's rate arithmetic is a test-local helper

**Where:** `crates/kernel/tests/sp2_evidence.rs` (`rate`), `docs/DESIGN-GUIDE.md`.

The SP-2 tests compute a rate as `delta / (mono_ns / 1e9)` in a local helper. They cannot call
`model::derive_rate`: `kernel` has no dependency on `model` (and cannot gain one while the lane
image's `Cargo.lock` is pinned, `HOB-17`), and a dev-dependency would rewrite the lock. So SP-2's
identity and clock claims are evidenced against the kernel's own types and hand-computed
arithmetic, while `model`'s classifier keeps its own `fixtures/rates/rates.json` cases; the two
sides are not joined by a test. The limit is stated in the SP-2 record rather than papered over.

**Exit criteria.** A package that owns both crates (the probe, or a reborn image with a new lock)
derives a rate from a `kernel::Reading` pair through `model::derive_rate` and asserts that the
hand-computed SP-2 values agree with it.

---

## HOB-14 — A block shape decoded under any frame type

**Where:** `crates/proto/src/frame.rs` (`encode_block`, `decode_block`), `python/reference/hostobs_wire_ref.py`.

`decode_block` read the section directory and the bodies without ever checking the frame type it
had just parsed, so a `REQ`, `DICT` or `END` frame carrying a block's sections decoded as a block.
`encode_block` had the mirror defect: it would write a block's sections under a caller-supplied
type. A consumer that dispatches on `FrameType` and then decodes anything with sections would
therefore read a control frame's bytes as measurements. Found by the luna cross-model spot review
(`reports/luna-hostobs-m1-spotreview-REPORT.md`, finding 1).

**Fix (M1b).** `ProtoError::NotABlock { frame_type }` (kind `not-a-block`) on the decode path and
`canonical_rank`-guarded refusal on the encode path; the Python decoder refuses the same case with the
same kind. Fixture `block-as-req` (a valid block with byte 4 patched to `REQ`) pins both, and a Rust
test patches byte 4 to each of the other four types.

## HOB-15 — Section order was enforced on encode only

**Where:** `crates/proto/src/frame.rs` (`check_canonical_order`), `python/reference/hostobs_wire_ref.py`.

Plan v4 §B.3 fixes the section order (header, dictionary, rows-or-time, columns) and the encoder
enforced it, but `decode_block` accepted any directory order: a sender that wrote dictionary after
rows produced a block whose re-encode differed from the bytes it came from, so the "decode then
re-encode is byte-identical" property the fixtures assert was one-sided. Found by the same luna review
(finding 2).

**Fix (M1b).** `ProtoError::SectionOrder { index, kind }` (kind `section-order`) with
`canonical_rank()`; the decoder checks the directory before reading any body and the Python decoder
matches. Fixture `section-order` reorders entries and bodies together, and a Rust test hand-builds a
swapped directory that no encoder would produce.

## HOB-16 — A truncated directory was a `struct.error` in the Python decoder

**Where:** `python/reference/hostobs_wire_ref.py` (`decode_block`).

The Python reference decoder unpacked each directory entry with `struct.unpack_from` after only
checking that the *count* fit, not that each entry did, so a frame whose declared section count
exceeded its bytes raised `struct.error` — a crash, not the `WireError` the Rust decoder returns
(`truncated`). Two decoders that disagree about the *kind* of a refusal are two decoders that do not
bind one contract. Found by the same luna review (finding 3).

**Fix (M1b).** The directory read is bounded before unpacking, with context `block directory`, and
the expected/observed lengths (`need`/`have`) travel in the error. Fixture `truncated-directory` (16
bytes declaring one section, no entry) pins it, and a Python test asserts the refusal for declared
counts 1..4 and that no `struct.error` escapes.

## HOB-17 — `kernel` carries its own value and quality types

**Where:** `crates/kernel/src/{value,quality}.rs`; `crates/kernel/Cargo.toml`.

Plan v4 §A.3 says the collectors produce `model` values. A path dependency
`hostobs-model = { path = "../model" }` — even as a *dev*-dependency — makes Cargo rewrite
`Cargo.lock` (the kernel package gains a `dependencies` entry), and the Rust lane image bakes the
lock digest (`/usr/local/share/hostobs-Cargo.lock.sha256`), so `cargo --locked` then fails with
"cannot update the lock file". Hand-editing the lock is forbidden, and a new crate needs an image
rebuild. Measured in this package in a throwaway container: adding the dev-dependency rewrote the
lock; removing it restored the digest.

The crate is therefore dependency-free and carries a three-variant `Value` and a `QualityCode`
newtype. They are not parallel vocabularies by accident: `crates/kernel/tests/quality_table.rs`
asserts the table byte-for-byte against `fixtures/wire/quality.json`, the same fixture `model`,
`proto` and the Python decoder assert, so a renumbering fails in four places.

**Exit criteria.** The first image rebuild that can carry a new lock: make `kernel` depend on
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

## HOB-20 — Unknown device topology counted as a bottom device in the sampler

**Where:** the sampler's `topos/sampler/src/collect.rs::is_bottom`
(`last.get(dev).copied().unwrap_or(true)`); `crates/kernel/src/cgroup.rs`.

A device the sampler had never classified (its `/sys/dev/block/<dev>/slaves` read failed, or the device
was new this tick) was treated as a **bottom** device, so a stacked device's counters were summed into
the cgroup's `io.*` aggregate and the aggregate double-counted. The plan's §A.5 fix makes "unknown"
a state. `kernel` implements it: the caller supplies a `TopologySource`, an `Unknown` answer makes the
whole aggregate `QualityCode::UNKNOWN_TOPOLOGY` with **no** values (never a partial total), and
`crates/kernel/tests/cgroup_fixtures.rs` pins bottom/stacked/unknown individually and mixed.

The sampler worktree is not edited by this package (it is a read-only behavioral source), so the bug
survives there; nothing consumes the sampler any more.

## HOB-21 — No `/sys` topology source

**Where:** `crates/kernel/src/cgroup.rs` (`TopologySource`, `UnknownTopology`, `FixedTopology`).

The trait is the seam, not the implementation: `UnknownTopology` and `FixedTopology` exist for tests
and for a caller without `/sys` access, and no reader in this crate reads
`/sys/dev/block/<dev>/slaves`. Until one does, every production `io.*` aggregate is
`unknown-topology` — an honest state, and the reason HOB-20's fix cannot silently regress.

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

## HOB-8 — `FrameHeader::parse` does not bound the declared length

**Where:** `crates/proto/src/frame.rs` (`FrameHeader::parse`).

`decode_block` refuses a frame whose declared length exceeds [`MAX_FRAME_BYTES`] *before* it
uses that number, and every later allocation is bounded by the verified frame. `parse` alone
does not: it reads the twelve header bytes and hands the caller the declared length
unchecked. The streaming reader of the next package starts with `parse` and must allocate
`len` bytes, so the safe path today is only the one `decode_block` happens to take.

**Fix shape.** Validate `FRAME_HEADER_LEN <= len <= MAX_FRAME_BYTES` inside `parse` (and keep
`decode_block`'s own check, so a future caller that bypasses `parse` is still bounded), plus
a test at the bound and one past it. No wire change.

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
possible under the locked image) but a different consequence: HOB-3 is about the quality
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
caches exactly the committed lock, so `cargo --locked` refuses to build (`cannot update
the lock file`). The two tables are therefore bound by *behavior*, not by a type: both
assert themselves against the committed fixture `fixtures/wire/quality.json`, and the
independent Python decoder asserts the same file. A divergence in either crate is a red
lane.

**Why it is filed.** The binding is real but expensive to keep: a new quality code must be
added in three places (two crates and the fixture), and a rename in one crate is caught
only if that crate's test runs. It is a configuration fact of the current image, not a
design preference.

**Exit criteria.** Decide the shared-type shape (a dependency edge, or a third
`-vocab` crate both depend on) at the next image rebuild, when `Cargo.lock` may
legitimately change together with the image, and record it as a decision in
`hostobs/docs/DESIGN-GUIDE.md`.

## HOB-4 — No registered `conformance` lane

**Where:** `run-gate.toml` declares `lint` and `selftest` only.

Plan v4 §B.5 and the Rust methodology both name a `conformance` lane for the Python
reference decoder. The Python side exists and passes (`python3 -m unittest discover -s
python/tests`, nine cases, standard library only), and the Rust side asserts the same
fixture files in `crates/proto/tests/golden_fixtures.rs`. What is missing is a *registered*
lane: the Python run has no declared environment, so it is not gate evidence and cannot
fail a merge by itself. The cross-language claim currently rests on two independent test
suites that happen to read the same files, not on one executed lane.

**Exit notes from this package.** The lane was not added here because the brief scoped
this slice to `model` and `proto` and any lane's environment/argv must be reviewed as
gate change. The work is small: `tester-unified` (not `-rust`) with
`python3 -m unittest discover -s python/tests`, no network, plus the fixture
regeneration check (`python3 python/reference/make_fixtures.py <tmpdir>` compared with
the committed bytes).

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

## HOB-6 — `DICT` frame type is defined and unused

**Where:** `crates/proto/src/frame.rs` `FrameType` (type code 4), crate docs decision 2.

`FrameType::Dict` (type code 4) is in the closed vocabulary, but nothing produces or consumes it: the
dictionary travels inside the block frame (section kind 5) so an exported segment is
self-contained and each fixture case is one file. A later package may split it into a
leading `DICT` frame without a wire change, which is why the type stays defined rather
than deleted.

**Exit criteria.** Either the probe emits a standalone `DICT` frame before a first block
of a generation (and the fixtures grow that case), or the type is retired with a note in
the crate docs.
