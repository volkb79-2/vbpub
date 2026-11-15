# Using the hostobs Rust lanes

`model`, `proto`, `kernel` and `store` are functional; the remaining three library crates and
two binaries are still the M0 scaffold. From `hostobs/`, run the registered lanes:

```sh
run-gate lint
run-gate selftest
```

Both use `tester-unified-rust` with Docker networking disabled. The image contains the
stable Rust toolchain, the cargo tools, Python for the cache/floor helpers, and the crate
archive closure for this exact `Cargo.lock`. Do not run Cargo from the cockpit or use a
network fallback. The Cargo wrapper refuses before invoking Cargo if the mounted lock,
baked lock digest, crate set, lock checksums, or cached archive bytes disagree.

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

Changing a Rust dependency or `Cargo.lock` requires rebuilding `tester-unified-rust`
from the reviewed lock before the lanes can pass. This initial image build is a local
bootstrap; CMRU-owned version resolution and image refresh belong to the later R-5 work
package. Do not edit `Cargo.lock` by hand to satisfy a lane: the baked lock digest is
the authority, and a mismatch is a signal to rebuild the image, not to hand-edit the
lock.
