# Using the hostobs Rust lanes

`model`, `proto` and `kernel` are functional; the remaining four library crates and two
binaries are still the M0 scaffold. From `hostobs/`, run the registered lanes:

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
python3 python/reference/make_fixtures.py /tmp/fixtures-expected   # from the descriptions
diff -r fixtures/wire /tmp/fixtures-expected                       # committed bytes are current
python3 -m unittest discover -s python/tests -v                    # decoder + rate reference
```

`python/tests` needs only the standard library and never imports the Rust code. The
regeneration must reproduce the committed `.bin` files byte for byte: the Python encoder
is independent, so a re-encoded difference is a real divergence rather than a cache
artefact. Do not edit `.bin` files by hand — change the case description in
`python/reference/make_fixtures.py` and regenerate.

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

## Changing a Rust dependency

Changing a Rust dependency or `Cargo.lock` requires rebuilding `tester-unified-rust`
from the reviewed lock before the lanes can pass. This initial image build is a local
bootstrap; CMRU-owned version resolution and image refresh belong to the later R-5 work
package. Do not edit `Cargo.lock` by hand to satisfy a lane: the baked lock digest is
the authority, and a mismatch is a signal to rebuild the image, not to hand-edit the
lock.
