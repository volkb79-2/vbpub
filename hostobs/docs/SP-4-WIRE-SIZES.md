# SP-4: wire sizes of the column codec against the sampler's varint TLV

Measured for plan v4 §F.3 (spike SP-4) to settle decision **D4** (fixed-width typed
columns instead of the sampler's varint TLV). The numbers come from a deterministic,
criterion-free measurement in the lane image:

```sh
tools/cargo-offline.sh run -p hostobs-proto --example sp4_wire_size --offline --locked
```

`crates/proto/examples/sp4_wire_size.rs` builds both encodings for the *same facts* and
counts bytes. There is no clock, no host data and no benchmark harness in it, so the
number is a property of the two formats rather than of a machine (estate rule: a test
result never depends on host load).

## The exact shapes

One live tick, 8 columns per cgroup, one row per cgroup:

| metric | unit | kind | stored type |
|---|---|---|---|
| `cpu.usage_usec` | usec | counter | u64 |
| `mem.current` | bytes | gauge | u64 |
| `mem.anon` | bytes | gauge | u64 |
| `pids.current` | count | gauge | u64 |
| `io.rbytes` | bytes | counter | u64 |
| `io.wbytes` | bytes | counter | u64 |
| `psi.cpu.some.avg10` | ratio | gauge | f64 |
| `psi.io.some.total` | usec | counter | u64 |

Counter rows are plausible cumulative readings (`1_000_000_000 + row * 1_000 + column *
17`); the gauge row is a fractional PSI average stored as `f64` bits. The entity labels
are `/system.slice/unit-<inc>.scope`, one per incarnation.

## The four measurements (bytes)

| cgroups | sampler TLV, per-tick deltas | sampler TLV, raw counters | columns, with dictionary | columns, steady state |
|---|---|---|---|---|
| 100 | 5,013 | 6,213 | 12,360 | 8,272 |
| 1,000 | 50,887 | 62,887 | 120,328 | 80,240 |

Per-row cost:

| cgroups | sampler TLV (deltas) | sampler TLV (raw) | columns, steady state |
|---|---|---|---|
| 100 | 50.1 B | 62.1 B | 82.7 B |
| 1,000 | 50.9 B | 62.9 B | 80.2 B |

The 100-cgroup dictionary-bearing frame is 12,360 B and its dictionary section is
4,080 B (33 %).

## How to read this

- **A live tick carries the dictionary only when the generation changed.** The steady-state
  comparison is the honest one: 8,272 B versus 5,013 B at 100 cgroups (1.65×), 80,240 B
  versus 50,887 B at 1,000 cgroups (1.58×).
- **The like-for-like comparison is the raw-counter line.** The sampler stores per-tick
  *deltas*, so its frames are smaller; but the plan's whole reason for raw counters is that
  deltas cannot be re-aggregated and lose the baseline. Carrying the same information (raw
  absolute counters) costs the sampler TLV 62.1/62.9 B per row against 82.7/80.2 B for the
  columns: a ratio of 1.33× and 1.28×. The remaining difference is the `u8` quality column
  (8 B/row at 8 columns = 1 B/column/row) plus the `t_off_us` row column, which the sampler
  does not carry at all.
- **The sampler cannot express the quality column.** It stores values or a state code per
  *value*, but nothing per *row and column* that says "absent" without writing a zero, and
  the plan's §C.2 rule ("zero is a value; absence is never zero") requires exactly that. No
  sampler TLV variant carries this; the 1.3× is the price of the contract, not overhead.
- **Per-row cost is nearly flat in cgroups** for the columns (82.7 → 80.2 B) because the
  dictionary is amortised; it grows slightly for the TLV because varint magnitudes vary.
  Both stay well under the frame bound: 1,000 cgroups at 80 KB is 0.5 % of the 16 MiB
  maximum.

## Verdict for D4

The numbers confirm **D4: fixed-width typed columns for live frames, history replies and
disk segments**. SP-4's fallback trigger was "the custom codec's maintenance cost exceeds
`flatc` plus a runtime dependency"; the byte comparison is not that criterion, and it does
not disqualify the columns either — a 1.3× like-for-like size for an exact `u64` value, a
mandatory quality column and a one-`struct.unpack_from` Python read is a favourable trade
against a per-value varint loop plus a schema-specific Python decoder. FlatBuffers remains
unnecessary: it would need a `flatc` toolchain, a runtime dependency and an image rebuild,
and it still has no zero-cost quality column.

What this measurement does **not** cover, and what would decide the fallback instead:
allocation counts and encode/decode CPU are not measured here (a bench with a regression
gate is deferred by the Rust methodology), and the maintenance cost is a judgement call at
review. Both are recorded as backlog entry `HOB-2`.

## Exact command output (this run)

```text
SP-4 wire-size measurement (deterministic, no clock, no host data)

shape: one live tick, 8 columns per cgroup, per-cgroup rows
sampler codec: the sampler's varint TLV, carrying per-tick DELTAS (8-byte f64 for gauges)
column codec: raw u64 counters + u8 quality per value, dictionary in the frame

 cgroups   sampler B   columns B  sampler/row  columns/row       ratio
     100        5013       12360        50.1       123.6       2.466
    1000       50887      120328        50.9       120.3       2.365

steady-state live tick (no dictionary section in the frame):
 cgroups   sampler B   columns B  columns/row       ratio
     100        5013        8272        82.7       1.650
    1000       50887       80240        80.2       1.577

like-for-like: the sampler TLV carrying RAW u64 counters (the same information,
without a quality column, which the sampler cannot represent at all):
 cgroups  sampler raw B   columns B  sampler raw/row  columns/row
     100          6213        8272          62.1        82.7
    1000         62887       80240          62.9        80.2

at 100 cgroups the dictionary-bearing frame is 12360 B, of which the dictionary is 4080 B (33 %)
per-row cost without the dictionary: 82 B (1,000 cgroups: 80 B)
```

## P3 decoder measurements

**Gate image.** The registered `conformance` lane (`HOB-4`) runs these tests on `tester-unified-rust`,
whose `python3` is Debian's apt package: CPython 3.13.5 on Debian 13. All numbers below were measured on
CPython 3.14.10 only. On 3.13.5 the absolute-peak test is skipped (its reason names the pinned version)
and only the growth and line-count gates run; WIRE.md §7.8 requires the absolute bound to be re-measured
and re-set in the change that moves the lane's image to a CPython the bound was not measured on.

Measured with the real decoder `python/hostobs_client/wire.py` (WIRE.md §7.4, §7.8) by
`python/tests/test_client_budgets.py` (gated) and `python/bench_decode.py` (not gated), one run each,
2026-11-18, devcontainer (8 cores, shared with a production game server, load average about 1.7),
`nice -n 19 python3 -I`, `sys.version` = `3.14.10 (main, Nov  5 2026, 09:34:56) [GCC 14.2.0]`
(`Linux-7.2.12+deb13-amd64-x86_64-with-glibc2.41`). Frames come from `python/tests/sp4_frames.py` (8
columns, entity labels `/system.slice/unit-<inc>.scope`); steady-state frames are 8,272 and 80,240 B as
in the table above, the dictionary-bearing ones are 12,568 and 121,440 B here (longer labels than SP-4's
12,360 and 120,328). Every steady-state decode alternates two distinct rows bodies.

**Gated (deterministic):**

| Measurement | 100 rows | 1,000 rows | Bound |
|---|---|---|---|
| `sys.settrace` `line` events in one `decode_block` (bare block, held dictionary) | 510 | 510 | equal |
| `tracemalloc` peak of one `decode_block` | 1,865 B | 3,395 B | growth 1,530 B <= 4,096 B; absolute <= 16,384 B (CPython 3.14.10 only) |

**Trend evidence only (CPU, min of 7 x 200 decodes, never gated):**

| Frame | us per decode |
|---|---|
| steady state, 100 rows (8,272 B) | 22.1 |
| steady state, 1,000 rows (80,240 B) | 87.9 |
| dictionary-bearing, 100 rows (12,568 B; dictionary decoded each time) | 148.5 |
| dictionary-bearing, 1,000 rows (121,440 B; dictionary decoded each time) | 1,256.7 |
| `measured_list()` of one 1,000-row column | 26.8 |

The steady-state 1,000-row decode is far under the 250 us target; the dictionary decode is a per-entry
loop that runs once per generation, and a dictionary section byte-identical to the held one is reused.
