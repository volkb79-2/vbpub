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
