# hostobs design decisions

This guide records *why* hostobs is built the way it is, including the alternatives
that were rejected, so a later agent does not re-argue a settled question. Normative
behavior lives in [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../../docs/design/hostobs/PLAN-v4-2026-11-08.md);
adoption steps live in [`CONSUMERS.md`](CONSUMERS.md); open items live in
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md).

## Package boundaries

`proto` and `model` are pure libraries: no I/O, no clock reads, no threads, no `unsafe`,
no dependencies. The probe reads the host and hands facts to them; the store writes bytes
they produce; the client decodes bytes they describe. Purity is what makes the property
tests deterministic and what keeps a wire bug from hiding inside a filesystem call.

The workspace denies unsafe Rust by default. Only `kernel` and `act` declare the planned
unsafe-code allowance. Cargo rejects combining a workspace-lint inheritance stanza with a
package-level override, so those two manifests declare their allowance directly while the
other seven inherit the workspace lint.

## Wire: why fixed-width typed columns (D4)

The topos sampler stores varint TLV values. `proto` stores fixed-width typed little-endian
columns instead: one `u32` metric index, one `u8` type, one `u8` kind, a `u16` reserved
field, then `n` eight-byte values and `n` quality bytes, padded to eight.

The measured trade is in [`SP-4-WIRE-SIZES.md`](SP-4-WIRE-SIZES.md). Like for like — both
formats carrying raw absolute counters, which is the whole point of §C.3 — the columns
cost 82.7 B per row against 62.1 B at 100 cgroups and 80.2 B against 62.9 B at 1,000: a
ratio of about 1.3×. That difference is the mandatory `u8` quality column and the row
offset column, both of which the sampler cannot express at all. The rejected alternative
was FlatBuffers: it needs a `flatc` toolchain, a runtime dependency and an image rebuild,
and it still has no zero-cost place to put "absent, not zero". Varints were rejected
because a per-value parse loop is the log2-of-magnitude cost the plan wants to stop paying
and because a varint `u64` above 2^53 invites an `f64` round trip.

## Wire: layout choices the plan left open

- **Section bodies are length-prefixed and eight-byte aligned**, and a reader requires a
  body's declared length to equal what its content needs. There are no inter-body pad
  bytes: `16 + 8 × n_sec` is divisible by eight, so alignment is a property of the layout
  rather than a rule to police at every body.
- **The dictionary travels inside the block frame** (section kind 5) rather than as a
  separate `DICT` frame on the block path. §B.3 allows a complete generation to precede
  the values that use it; carrying it inside the block is what makes an exported segment
  self-contained (§C.5) and a fixture a single file per case. The `DICT` frame type stays
  defined and unconsumed (`HOB-6`), so a later package can split it out without a wire
  change.
- **An unknown *optional* section is skipped by its declared length and reported** in
  `Block::skipped_sections`; an unknown *required* section fails the block. A **known**
  section carrying any version this codec does not implement — older or newer — is
  refused even when optional: decoding an older body as version 1 would report values the
  sender never wrote.
- **The header's row count is the single authoritative count** for rows, columns and
  history buckets. A section that disagrees is a typed refusal, never a truncation or a
  guess.
- **Oversize is refused before allocation.** A frame declaring more than 16 MiB is
  rejected without using that number as an allocation size, and exactly 16 MiB is legal.

## Data model: why identities are incarnations

Plan v4 §C.1: a cgroup's identity is an incarnation id minted for `(mount id, cgroup
inode)`. The path is a *label*. The reasons, in the order they matter:

1. A path can be re-used by a different cgroup (a re-created container), so a path key
   silently joins two different lifetimes and produces a rate across a boundary.
2. A path can be renamed while the cgroup lives on, so a path key silently splits one
   lifetime into two.
3. Matching by inode survives the rename and separates the re-creation.

The registry therefore retires after **two consecutive absent scans** (missing one scan is
not deletion), a rename keeps its id with an updated label, and a retired key that
reappears is minted again with a fresh id and never reused (`Incarnation::UNASSIGNED` is
0 and ids start at 1). Process identity is `(boot_id, pid, start_ticks)` for the same
reason: a bare pid is recycled.

## Data model: why quality is a mandatory column

§C.2: zero is a value; absence is never zero. The sampler stores a value or a state code
per value, but nothing that says "absent" without writing a zero, so a consumer reading a
missing file as `0` is indistinguishable from a real zero. `proto` therefore writes a
`u8` quality for every value and never omits a row: a missing sample keeps its eight
bytes and the quality code says not to read them. `model::Sample::missing` refuses
`Quality::Ok`, so an absence cannot masquerade as a measurement even in memory.

## Data model: rates over the actual interval

§C.3 and SP-2: counters stay raw `u64` on the wire and on disk. `model::derive_rate`
divides the delta by the **actual** monotonic interval between the two readings, so a
missed sample widens the interval and yields the average over the whole gap instead of
inflating one nominal bucket. `dt = 0` and `dt < 0` produce no rate at all (the second is
refused, not clamped). A decreased raw counter is `NoRate::CounterReset`: the current
reading is the new baseline and no delta of either sign is invented.

**Counter wrap is deliberately not inferred** (`HOB-1`). Without a declared counter width
and a plausibility policy, a wrap and a reset are indistinguishable, and guessing one is
an invented rate — exactly what §C.3 forbids. The classifier is the single place a
declared-width policy would be added.

The no-rate outcome maps onto the closed quality vocabulary as: no baseline or zero-length
interval → `not-sampled`; reversed interval → `stale`; reset → `reset`; a missing sample
propagates its own code; a value of the wrong type → `unsupported` (a caller defect
visible in the quality column instead of a panic).

## Dictionary decoding: the wire list is the sender's numbering

`Dictionary::intern` de-duplicates and re-labels, which is correct for a *live* table
(plan v4 §C.1: an incarnation's label is not its identity, so a rename keeps the entry and
its index) and wrong for a *wire* body. `decode` therefore never trusts `intern`'s index: it
checks that the entry it just interned came back at the position the sender wrote it and
refuses with `duplicate-dictionary-entry` otherwise. Deduplicating instead would shorten the
table and shift every later index, which is precisely the meaning change §B.3 forbids, and
would make the two decoders disagree with no fixture to catch it.

The aggregation mask is treated the same way: it is **derived** from the metric kind, so the
decoder requires equality, not "no extra bits". A strict subset used to decode and then be
re-widened by `encode`, which silently discarded the sender's restriction; a subset is now
`dictionary-entry-mask`.

A reference is also checked for **kind**, not only for range: a row must name an
incarnation, a column a metric, and a column's declared arithmetic kind must agree with the
dictionary's facts. Range alone would let a consumer print a metric name as a cgroup, or
aggregate a gauge by counter rules, while every individual field looked valid.

Finally, the block's shape rules are about **presence**, not about counts: rows and time
sections are mutually exclusive even when both are empty, and a non-zero row count with
neither section is `no-rows-or-time` on both the encode and the decode path. There is no
"rowless host-scope block" in this codec: values that carry neither an entity nor a bucket
cannot be addressed, so inventing a layout for them would be a new mode with no specification
behind it. If a later package needs host-scope values that are not per-incarnation, that is
a deliberate schema addition (a new section kind with its own fixture), not a relaxation of
this rule.

## Cross-language conformance without a shared type

`fixtures/wire/*.bin` are canonical bytes and `*.json` their decoded typed values.
`fixtures/rates/rates.json` pins the arithmetic of the rate rules with the computation
written out in each case's `doc`. The contract binds **three** implementations: the Rust
encoder/decoder, the Rust rate derivation, and a standard-library-only Python reference
codec written from the layout, not from the Rust code.

`hostobs-proto` does **not** depend on `hostobs-model`. A path dependency changes
`Cargo.lock`, and the Rust lane image caches exactly the committed lock, so `cargo
--locked` refuses to build ("cannot update the lock file"). Rather than hand-edit the
lock (forbidden: the image's baked digest is the authority), the two crates are bound by
behavior: both assert their vocabulary against `fixtures/wire/quality.json`, and the
Python decoder asserts the same file. The cost of this choice is recorded as `HOB-3`
together with the decision point that would remove it (the next image rebuild).

The same constraint is why the tiny test-only JSON reader lives in `test-support/json.rs`
(outside the workspace members) instead of pulling `serde_json` in as a dev-dependency.

## Gates and coverage

`lint` runs formatting, Clippy with `-D warnings`, rustdoc with `-D warnings`, cargo-deny
and the source lints; `selftest` runs nextest, doctests, llvm-cov and the per-crate
line/region floors from `coverage-floors.toml`. Both are offline and locked, in the
`tester-unified-rust` image, with Docker networking disabled.

`model` and `proto` now carry behavioral floors (97% line/96% region and 98%/96%) raised
from the M0 bootstrap 90%/85% after their own tests reached 98.06%/97.90% and
99.43%/97.69%. The other seven packages keep the bootstrap floor because they are still
the compilable scaffold; their identity test is not a product-coverage claim. Floors are
never lowered and code is never excluded from coverage.

Rust R2 (`cargo-mutants` judged by assay) is not adopted yet (R-3b in
[`assay/docs/RUST-METHODOLOGY.md`](../../assay/docs/RUST-METHODOLOGY.md)), so the test-quality
claim for this package rests on a hand mutation campaign: one mutation at a time, in the lane
image, each restored and digest-verified. Twenty-one mutations, all killed, with the two
first-pass survivors converted into tests, are recorded in
[`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md).

`cargo deny` keeps duplicate-version and external wildcard checks strict. Its single
`winnow@0.7.15` skip is tied to the imported sampler lock: `toml 0.9.8` and its
`toml_parser 1.1.5` resolve `winnow` 0.7.15 and 1.0.4, so the skip names only the older
exact version and becomes an unused-entry warning when that edge disappears. Workspace
packages are explicitly unpublished; their license metadata is deferred until a
publication decision, while registry dependencies remain license-checked.

Dependencies remain outside Git: the sampler's measured dependency floors are caret
constraints in the workspace manifest, `Cargo.lock` pins the resolved graph, and the
sibling tester image caches its `.crate` archives. Every lane is offline and locked, with
a precheck tying the mounted lock bytes and each registry archive to the image build; a
lane refuses before invoking Cargo when any of them disagree.
