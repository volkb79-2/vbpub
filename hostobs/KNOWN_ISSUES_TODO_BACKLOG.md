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
