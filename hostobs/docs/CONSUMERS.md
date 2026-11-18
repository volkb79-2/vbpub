# Using the hostobs Rust lanes

`model`, `proto`, `kernel` and `store` are functional; the remaining three library crates and
two binaries are still the M0 scaffold. From `hostobs/`, run the registered lanes:

```sh
run-gate lint
run-gate selftest
run-gate assay-r1
```

All three use `tester-unified-rust` with Docker networking disabled. The image contains the
stable Rust toolchain, the cargo tools, Python for the cache/floor helpers, and the crate
archive closure for this exact `Cargo.lock`. Do not run Cargo from the cockpit or use a
network fallback. An image BUILT FROM THIS SOURCE has a Cargo wrapper that refuses before
invoking Cargo if the mounted lock's registry-set digest, crate set, lock checksums, or cached
archive bytes disagree with the image (the registry set is the downloaded packages plus the
lock version; workspace members' own entries are not part of it). The currently deployed
`tester-unified-rust:local` is older: it still checks the sha256 of the whole `Cargo.lock`
file, so on it any lock byte change is refused until the lanes move to a newly built tag.

## What `assay-r1` judges, and what it says

`assay-r1` is a `kind = "command"` lane: it runs the judge straight from this worktree's
own source (`PYTHONPATH={worktree}/assay/src`, plus `assay/analysis/src` for the
post-checks) and the judge runs the lane declared in [`assay.toml`](../assay.toml)
(`[lanes.assay-r1]`). `assay` is stdlib-only, so nothing is installed and the Rust image
needs no `pip` — that is the whole reason the lane has this shape instead of
`kind = "assay"`, which would have Run-Gate `pip install` the worktree first and fail
here (see [`HOB-44`](#hob-44-closed-the-lanes-own-declaration-shape) below). Its
own argv is the same offline/locked cargo-llvm-cov + nextest + doctest + crate-floor
command `selftest` runs, so the two cannot disagree about what was executed; what changes
is who renders the coverage verdict. The artifacts are `lcov.info` and `cov-summary.json` in
this directory (git-ignored), and the verdict is
`hostobs/.assay/verdict-assay-r1.json`.

The verdict's R1 claim carries measured totals, not a restated floor:

```json
{"covered": 15100, "executable": 15723, "considered": 33,
 "branch_capability": "unavailable", "branches_total": 0,
 "missing_lines": {"hostobs/crates/kernel/src/cgroup.rs": [270, 319, ...], ...}}
```

Read it as: 33 declared `src` files of `model`, `proto`, `kernel` and `store` were judged,
15100 of their 15723 executable lines ran (96.04 %), the floor in the lane file is 95.0 %,
and every uncovered executable line is listed by file as a finding (`missing_lines`).
`branch_capability: "unavailable"` and `branches_total: 0` are the honest answer for stable
Rust (cargo-llvm-cov writes no `BRDA:` arcs), not a zero-of-zero pass: the crate's R1 claim
is line-only, and the lane declares `require_branch = false` rather than pretending.

Fields to look at, in order: `outcome` (`PASS`/`FAIL`/`NO_MEASUREMENT`/`ERROR`),
`claims[].coverage.missing_lines`, `claims[].coverage.covered/executable`, and
`commit` (must be the head you meant to judge). `assay verify` and
`assay analyze report` read the same file independently.

### Floors and where they come from

The floor is `judge.fail_under = 95.0`, one full point BELOW the measured 96.04 % (15100
/ 15723 executable lines on this workspace at head `3b46df06`). Per crate at that head:
`model` 98.09 % (2004/2043), `proto` 99.71 % (3454/3464), `kernel` 93.52 % (2627/2809),
`store` 94.71 % (7015/7407). The derivation, the one-point margin and the rule that a
floor is never lowered live in [`assay.toml`](../assay.toml)'s header. The per-crate line and
region floors in [`coverage-floors.toml`](../coverage-floors.toml) stay in the lane's argv and
remain the tighter bound; Assay's line claim does not replace them, and region coverage is
not part of Assay's R1 claim.

### Proof it is a real judge, not a renamed floor

Proven through Run-Gate in this workspace's own lane (no throwaway copies), the same 33
targets returned:

| run | nextest | assay R1 |
|---|---|---|
| `run-gate assay-r1` in this workspace's registered lane | 333 tests pass | `PASS`, 15100/15723 = 96.0377 % |
| the same lane, one kernel test file deleted, committed in a temporary worktree | 322 tests pass (all green) | `FAIL/UNCOVERED_LINES`, 14777/15723 = 93.9833 % |

So the verdict is the measured number and the failure is the coverage claim itself; the
test command stays green in the negative case, which is exactly the difference between a
coverage verdict and a test result. The negative lane logged both its claims and its
totals (`assay-r1 claim: R1 FAIL UNCOVERED_LINES`, `lines 14777/15723 = 93.9833%`), which
is why the lane captures the judge's exit status instead of aborting on it. Earlier, the
same deletion was also measured in throwaway copies with the lane's crate-floor step
neutralized (`true` in place of `tools/crate-floors.py`), so nothing but the Assay R1
claim could have failed it; re-running that step over a negative copy confirms it refuses
the deletion independently (`ERROR: crate crates/kernel line coverage is 81.24%
(2364/2910); floor is 92.00%`). The registered lane needs no such neutralization to fire:
R0 FAILs first (`COMMAND_FAILED`) and R1 FAILs on `UNCOVERED_LINES`, both in one log.

### `HOB-44` (closed): the lane's own declaration shape

`tester-unified-rust` carries `python3` but no `pip`, no `ensurepip` and no
`python3-venv`. Under the original `kind = "assay"` declaration that was fatal: Run-Gate
installed `assay` from the bind-mounted worktree before the judge ran, so the lane stopped
at `run-gate: editable assay install failed` / `/usr/bin/python3: No module named pip` and
reported `ERROR` with no verdict artifact. The fix is a declaration change, not an image
change: the
lane is now a **command lane** that runs `python3 -m assay.cli run assay-r1` with
`PYTHONPATH` pointing at the worktree's own `assay/src` — which is exactly how assay is
consumed from inside this monorepo — so there is nothing to install. The image is unchanged
and should stay that way (it is offline and lock-bound by design). `HOB-44` is CLOSED; assay
B549 records the same finding from the judge's side.

### What is not covered

- **Branches** — unavailable on stable Rust (see above). A branch number here would be invented.
- **Mutation (R2)** — Rust R2 needs cargo-mutants report ingestion (assay B245 / design R-3b); no
  `assay-r2` lane exists for Rust. The hand-mutation campaigns in
  [`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md), [`HAND-MUTANTS-M1B.md`](HAND-MUTANTS-M1B.md) and
  [`HAND-MUTANTS-M1C.md`](HAND-MUTANTS-M1C.md) remain the test-quality evidence.
- **Canaries (R3)** — needs a receipt-writing test-break canary (assay B246 / R-4); not registered.
- **Inline `#[cfg(test)] mod tests` blocks inside a judged file** are measured (LCOV has no
  per-region exclusion, assay K5). They are NOT a small effect here: counting from each file's
  `#[cfg(test)]` line to its end, roughly a third of the four crates' source lines (about 10.5k
  of 28.7k) are inline test code, whose executable lines sit in the 15723 denominator and are
  covered whenever the tests run. The 96.04 % therefore overstates product-code coverage, and
  a drop of the 629-line headroom lands almost entirely on product code, so 95.0 % is looser
  on product code than it reads (the sampled uncovered line `store/src/tier.rs:1553` is a test
  `assert!` message argument). The per-crate floors in the lane's argv carry the same bias.
- **Three of the four `crates/*/src/lib.rs` files** (`model`, `proto`, `store`) are module
  declarations and `//!` documentation only, so cargo-llvm-cov emits no LCOV record for them and
  they are not declared targets; Assay refuses a declared target it cannot measure rather than
  excusing it. `crates/kernel/src/lib.rs` does carry `Bounds` (7 executable lines, all covered),
  so it is one of the 33 targets.

## The independent Python cross-check run

The fixtures in `fixtures/` are the contract between two implementations written from the
same specification. Regenerate and check them with:

```sh
python3 python/reference/make_fixtures.py /tmp/fixtures-expected     # wire cases
python3 python/reference/make_segment_fixtures.py /tmp/segment-expected  # segment files
diff -r fixtures/wire /tmp/fixtures-expected                         # committed bytes are current
diff -r fixtures/segment /tmp/segment-expected                       # ditto, whole files
python3 -I -m unittest discover -s python/tests -v                   # decoders + rate reference
```

`python/tests` needs only the standard library and never imports the Rust code. The
regeneration must reproduce the committed bytes exactly: the Python encoders are
independent of the Rust ones, so a difference is a real divergence rather than a cache
artefact. Do not edit a `.bin` or a `.seg` by hand — change the case description in the
matching `make_*.py` and regenerate. The segment layout they implement is specified in
[`SEGMENT-FORMAT.md`](SEGMENT-FORMAT.md), and the Rust side asserts the same files in
`crates/store/tests/golden_segment.rs`.

**This run is not yet a registered lane.** It is a documented manual check
(`HOB-4`), so its result is developer feedback, not gate evidence. Adding it as a
`conformance` lane is the tracked follow-up.

## Working with the model in a probe or test

Identity is an incarnation, never a path:

```rust
use hostobs_model::identity::{CgroupKey, IncarnationRegistry};

let key = CgroupKey::new(31, 0x1234)?;        // (mount id, cgroup inode); zeros are refused
let mut registry = IncarnationRegistry::new();
registry.begin_round();
let inc = registry.observe(key, Some("/system.slice/unit.scope"))?;
registry.end_round();                          // one absent scan is not deletion
```

Rates come from two readings and two monotonic instants; the interval is the real one:

```rust
use hostobs_model::rate::{derive_rate, MonoTime};
use hostobs_model::value::{Sample, Value};

let outcome = derive_rate(
    Sample::measured(Value::U64(1_000)),
    MonoTime::from_nanos(1_000_000_000),
    Sample::measured(Value::U64(3_000)),
    MonoTime::from_nanos(3_000_000_000),
    1_000_000_000,                             // nominal interval: reports gaps only
);
assert_eq!(outcome.rate().expect("a rate").per_second(), 1_000.0);
```

A missing reading is built with `Sample::missing(quality)?`, which refuses `Quality::Ok`;
read a value with the `measured_*` accessors and it returns `None` unless the sample is
real.

## Encoding and decoding a block

```rust
use hostobs_proto::dict::{Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit, MetricValueType};
use hostobs_proto::frame::{encode_block, decode_block, Block, BlockHeaderFacts, Column, FrameType, Row};
use hostobs_proto::quality::Quality;

let mut dictionary = Dictionary::new(0);
dictionary.intern(Entry::Entity { inc: 11, label: "/a".into() })?;
dictionary.intern(Entry::Metric {
    facts: MetricFacts {
        unit: MetricUnit::Microseconds,
        kind: MetricKind::Counter,
        scope: MetricScope::Cgroup,
        source: MetricSource::CgroupFs,
    },
    name: "cpu.usage_usec".into(),
})?;

let block = Block {
    frame_type: FrameType::Block,
    corr: 7,
    header: BlockHeaderFacts { /* boot_id, epoch, seq, monotonic bounds, real_ns, dict_gen, n_rows, dropped, flags */ },
    rows: vec![Row { ent: 0, t_off_us: 0 }],
    time: Vec::new(),
    columns: vec![Column::new(1, MetricValueType::U64, MetricKind::Counter, vec![1234], vec![Quality::Ok.code()])?],
    dictionary: Some(dictionary),
    skipped_sections: Vec::new(),
};

let bytes = encode_block(&block)?;            // canonical bytes
let decoded = decode_block(&bytes)?;          // the same typed block, or one typed error
```

`decode_block` returns either a complete validated `Block` or one `ProtoError`; it never
panics on untrusted bytes, never returns a partial result, and reports an unknown optional
section in `skipped_sections` while refusing an unknown required one.

The refusals a producer will meet while assembling blocks, with their `kind()` names:

| Condition | Refusal |
|---|---|
| the dictionary body repeats one identity (an `inc`, a metric name, a label, free text) | `duplicate-dictionary-entry` |
| a metric entry's aggregation mask is not the mask its kind derives | `dictionary-entry-mask` |
| a row names a dictionary entry that is not an incarnation | `row-reference-not-entity` |
| a column names a dictionary entry that is not a metric | `column-reference-not-metric` |
| a column's kind contradicts the dictionary's metric facts | `column-kind-mismatch` |
| a non-zero row count with neither a rows nor a time section | `no-rows-or-time` |
| both a rows and a time section, even when both are empty | `rows-and-time` |

A dictionary body is reproduced position for position: `Dictionary::decode` refuses a
repeated identity instead of merging it, because merging would shift every later index away
from the sender's numbering. Use `Dictionary::encode_with` to write an exact list of entries
when a test needs to express a wire list a live table would never build.

## Writing and reading history through the store

A store is configured, opened, fed from the writer queue, and queried by time. The layout it
writes is specified in [`SEGMENT-FORMAT.md`](SEGMENT-FORMAT.md); nothing below writes a file
directly, because `fsio` is the only module that may.

```rust
use hostobs_store::queue::Job;
use hostobs_store::store::{Store, StoreConfig};

// plan v4's hot/warm/cold/events set, with every bound set explicitly (no defaults).
let config = StoreConfig::plan_v4(Path::new("/run/hostobs"), 7, 1)?; // epoch, dict generation
let mut store = Store::open(config)?;         // rescans the directories and repairs tails

// Submit is bounded. An oversize job is refused as `job-too-large`; a full queue drops the
// oldest job and *says* which bytes it lost.
let report = store.submit(Job::new(payload, real_ns, vec![incarnation]))?;
if report.dropped_anything() {
    // lost records are a report, never silence
}

store.write_pending(now_ns, 64)?;   // bounded drain; a write failure is counted, not fatal
store.evict(now_ns)?;               // keep_s first, then cap_bytes, whole segments oldest first
store.seal_all()?;                   // temp name -> write -> fsync -> rename -> fsync dir

// A range query is bounded in records and bytes across the whole store, and answers with the
// stored payload byte for byte. Decoding it is the caller's step: the store carries no column
// codec (`HOB-39`), only the quality vocabulary.
let answer = store.range(from_ns, to_ns, 4096, 1 << 20)?;
for routed in &answer.records {      // `routed.tier` says which tier it came from
    // routed.record.payload, routed.record.real_ns, routed.record.incarnations
}
```

Every refusal a caller will meet, with its `kind()` name:

| Condition | Refusal |
|---|---|
| a tier directory the config requires to be tmpfs is not (`require_tmpfs`) | `not-tmpfs` |
| the mount table cannot be read while such a tier is required | `mount-table-unreadable` |
| a write fails with `ENOSPC` | `no-space` |
| a job is larger than the whole queue bound | `job-too-large` |
| a segment's header declares a payload its tier kind does not carry | `tier-kind-mismatch` |
| a record's envelope kind is not the segment's corpus | `record-corpus` |
| a record's payload is not the frame type its corpus requires | `payload-frame-type` |
| the dictionary is at `dict_active_max` | `dictionary-full` |
| a segment still names an incarnation the retirement pass was about to free, a queued job's payload names it, or a minted record has not yet reached a segment | (not retired; `retired` count) |
| a range asks for zero rows or zero bytes | `query-limit` |
| a tier directory the config requires to be tmpfs is spelled with `..`, is relative, or crosses a link | `not-tmpfs` (the check resolves first; the mount table lists what the kernel resolved) |

The four failures that must never be silent have their own answers, and a test each:

| failure | what a caller sees |
|---|---|
| disk fill | `no-space` from the write, counted in `StoreStats::write_failures`; nothing is lost silently because the job is still queued |
| RAM growth | a full queue drops the **oldest** job and returns it; the dictionary and the query limits are refusals |
| torn file | `torn_repaired` (truncated back to the last good record) or `quarantined` (renamed `.corrupt`), both counted and both reported by `notes()`. A tail of pure zeros is the unwritten part of a crash and is truncated, not quarantined; a tail of any other bytes is corrupt. |
| a file bigger than the read bound | quarantined `.corrupt` and counted in `TierScan::oversized`; never skipped, because a file outside the ring is outside the byte total and eviction would never reach it |
| a segment whose header cannot be read (EACCES, EIO, a race) | counted in `TierScan::unreadable`, reported in `notes()`, left in place; the rest of the tier still opens |
| a seal that fails before its rename | the open handle and the ring entry come back, no `.tmp` is left behind, and the next write goes to the same open segment |
| silent loss | `Submit::dropped_jobs`/`dropped_records`/`dropped_bytes` and the matching `StoreStats` counters; `StoreStats::lossless()` is true only when every one is zero |

A store only ever deletes a file that is its own by name **and** header kind. A file that
looks like a segment but holds another corpus (or a link, or a subdirectory) is reported in
`notes()` and left in place — which is what lets hot and warm share a directory. The one file
it removes without reading it is its own stale `<n>.seg.open` twin beside an already-sealed
`<n>.seg`: the crash window between the seal's rename and its unlink. Removing the twin is
safe only because the name parse and the sealed file's header both prove it is the store's,
and keeping it would count its bytes twice and reopen a file whose records already left.

## Changing a Rust dependency

Adding, removing or changing a registry or git package (name, version, checksum, source)
or the top-level lock `version` requires rebuilding `tester-unified-rust` from the reviewed
lock before the lanes can pass. An edit that only touches workspace members (a path
dependency between workspace crates rewrites their `dependencies` arrays and adds no
downloaded crate) does not change the image identity and needs no rebuild. This initial
image build is a local bootstrap; CMRU-owned version resolution and image refresh belong to
the later R-5 work package. Do not edit `Cargo.lock` by hand to satisfy a lane: the baked
registry-set digest is the authority, and a mismatch is a signal to rebuild the image, not
to hand-edit the lock.

Which image runs what: the registry-set identity exists only in an image built from this
source (a NEW tag, built on a host later). The currently deployed `tester-unified-rust:local`
still carries the whole-lock identity and rejects ANY lock byte change, workspace-only edits
included, so a path dependency cannot land until the Rust lanes are switched to the new tag
(a separate `run-gate.root.toml` commit, after the new tag exists). The old tag stays until
every Rust lane has passed on the new one; only then is `:local` re-pointed.
