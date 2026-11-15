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
