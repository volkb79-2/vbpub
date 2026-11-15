# Changelog

## [Unreleased]

### Added

- **`model` crate (M1):** incarnation identity (`BootId`, `Epoch`, `CgroupKey` keyed by
  `(mount id, inode)`, `ProcessKey`, `IncarnationRegistry` with two-absent-scan retirement
  and rename-preserving relabel), the closed eight-code quality vocabulary, good/bad-quality
  values (`Sample::missing` refuses `Quality::Ok`), the metric registry with
  unit/kind/scope/source/aggregation (aggregation derived from kind), and counter
  classification plus rate derivation over the actual monotonic interval (`dt = 0`,
  `dt < 0`, reset, missing and wrong-type outcomes as typed refusals).
- **`proto` crate (M1):** the wire codec — frame header, section directory, fixed-width
  typed little-endian columns (raw `u64` counters and a mandatory `u8` quality per value),
  live rows, history buckets, and bounded per-segment dictionaries with generation
  retirement. Every decode failure is one typed `ProtoError`; nothing panics, nothing
  returns a partial block, and unknown or future section versions are refused.
- **Shared golden fixtures:** `fixtures/wire/` (23 cases: u64 boundaries, `-0.0`/NaN
  placeholder, Unicode dictionary, every quality code, empty selection, unknown optional
  section, and sixteen refusal cases) and `fixtures/rates/rates.json` (18 hand-computed
  classifier and rate cases). Bind the Rust tests and an independent stdlib-only Python
  reference codec (`python/reference/`, `python/tests/`) to one contract.
- **SP-4 measurement** (`crates/proto/examples/sp4_wire_size.rs`, deterministic,
  criterion-free) with published numbers in `docs/SP-4-WIRE-SIZES.md`: at 100/1,000
  cgroups, 82.7/80.2 B per row for the column codec against the sampler TLV's 50.1/50.9 B
  (per-tick deltas) and 62.1/62.9 B (raw counters). Confirms decision D4.
- **`KNOWN_ISSUES_TODO_BACKLOG.md`** with the first six `HOB-` entries (counter wrap
  policy, unmeasured CPU/allocations, the duplicated-vocabulary consequence of the locked
  image, the missing registered `conformance` lane, history min/max/mean, and the unused
  `DICT` frame type).
- **Hand-mutation record** `docs/HAND-MUTANTS-M1.md`: twenty-one mutations across the
  classifier boundaries and codec bounds, all killed in the lane image, each restored and
  SHA-256-verified; the two first-pass survivors became tests.
- **Docs:** `README.md`, `docs/DESIGN-GUIDE.md` and `docs/CONSUMERS.md` rewritten for the
  functional crates (contracts, rejected alternatives, paste-ready examples).

### Fixed

- **Dictionary decode no longer de-duplicates the wire list** (`duplicate-dictionary-entry`).
  `intern` merges a repeated identity (correct for a live table, wrong on the wire), so a
  body repeating an incarnation, metric name, label or free text came back with fewer
  entries and every later index shifted. The decoder now refuses when the entry it interned
  is not at the position the sender wrote; the Python reference decoder refuses the same
  input with the same kind name, and `duplicate-dictionary-entry` is a committed fixture.
- **A metric aggregation mask must equal the mask its kind derives**
  (`dictionary-entry-mask`). Previously only extra bits were refused, so a strict subset
  decoded and was silently widened on re-encode. Both languages now refuse a subset or an
  extra bit, pinned by the `bad-metric-mask` fixture.
- **Dictionary references are checked for kind, not only for range.** A row must name an
  incarnation (`row-reference-not-entity`), a column a metric
  (`column-reference-not-metric`), and a column's declared kind must agree with the
  dictionary's metric facts (`column-kind-mismatch`); fixtures pin all three in both
  languages.
- **Values without an address are refused** (`no-rows-or-time`): a non-zero row count
  requires a rows or a time section, on the encoder and the decoder side. Rows and buckets
  are now exclusive **by presence**, so a block carrying both sections is refused even
  when both are empty (`n_rows == 0`), which the count-based check missed.
- `Column::sample` computes its reported need with `checked_add` (a caller-supplied
  `usize::MAX` row was a debug-build overflow), and `decode_block` takes the directory slice
  with `.get(..)` instead of an index.
- The refusal vocabulary grows from 34 to 40 names; the count test, the display test, the
  fixture comparison in both languages and the crate docs were updated together.
- `Dictionary::encode_with` writes an exact entry list (tests and fixtures need to express
  wire lists a live table would never build); `Dictionary::encode` is unchanged.
- Fix-round hand-mutation campaign stored as a replayable script
  (`docs/hand-mutants/fix-round-mutants.py`, 16 patches, all killed) and the backlog grew
  to `HOB-13`.

### Changed

- `coverage-floors.toml`: `crates/model` raised to 97% line / 96% region and
  `crates/proto` to 98% line / 96% region (measured 98.06%/97.90% and 99.43%/97.69%).
  No floor was lowered and no code is excluded from coverage.

## [M0]

### Added

- Added the minimal hostobs Cargo workspace skeleton for the planned sampler crate split.
- Added offline Cargo settings and a lock-bound launcher that checks the baked crate cache before invoking Cargo with `--locked`.
- Added zero-retry nextest configuration, cargo-deny policy, source lint tooling, and per-crate coverage-floor checks at 90% line and 85% region.
- Added the `lint` and `selftest` Run-Gate lanes using the `tester-unified-rust` image; both run without network access.
