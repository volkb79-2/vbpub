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
- **Shared golden fixtures:** `fixtures/wire/` (16 cases: u64 boundaries, `-0.0`/NaN
  placeholder, Unicode dictionary, every quality code, empty selection, unknown optional
  section, and nine refusal cases) and `fixtures/rates/rates.json` (18 hand-computed
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
