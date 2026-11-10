# Rust methodology

**Status: planning only.** Assay has no registered Rust adapter. A lane that
declares `judge.language = "rust"` is still refused as an unknown language.
This document preserves the binding operator decisions for the planned Rust
adapter and the Rust projects that will consume it.

The decisions came from the 2026-11-07 and 2026-11-08 operator interviews
recorded in the release program's `hostobs-DECISIONS-20261107.md`. The second
Rust-methodology block on 2026-11-08 amends the first: in particular, it
supersedes in-repository dependency vendoring. The adapter design examined by
the same program is `assay-RUST-ADAPTER-DESIGN.md`; the adopted host program is
`hostobs-PLAN-v4.md`.

## Dependency and toolchain model

- Rust dependencies are **not vendored in Git**. There is no committed
  `vendor/` tree and no cargo-vendor writer.
- A sibling image, `tester-unified-rust`, carries the stable Rust toolchain,
  the cargo tools, and a pre-fetched crate cache for every Rust project's
  `Cargo.lock`. `tester-unified` itself is not expanded with Rust.
- The Rust image follows stable only. There is no separate MSRV lane.
- cmru builds the image with network access and `cargo fetch --locked`.
  `Cargo.lock` checksums are the dependency authority.
- Every gate runs offline and locked. The image/lock precheck fails loudly and
  names the missing crate when the baked cache and `Cargo.lock` disagree,
  instead of falling back to the network or inventing a substitute.
- Rust runtime dependencies, dev-dependencies, cargo tools, and the stable
  toolchain move together in one cmru-driven, reviewed update under the
  14-day freshness rule. Compatibility and fast CVE exceptions remain explicit
  overrides with a reason.
- Docker serves the image locally on each host. A `Cargo.lock` change, a
  dependency-adding branch, or a scheduled update requires an image rebuild.
  A bare checkout outside the image is not expected to build offline.

The 2026-11-08 amendment therefore replaces the earlier vendor-tree proposal,
including its vendor-size and re-vendor-difference tripwires. The binding
tripwire is cache/lock agreement at the start of a Rust lane.

## Assay rigor and coverage

- R0 remains the exit status of the declared command, with nextest and
  doctests in the baseline command. There is no Rust result-report override:
  nextest's documented exit statuses are the R0 witness.
- R1 is planned through `cargo llvm-cov` LCOV output and assay's existing LCOV
  parser. It judges changed lines at **100%**.
- Each crate separately carries an initial floor of **90% line coverage and
  85% region coverage**. The region floor is a lane step over
  `cargo llvm-cov report --json`; assay itself continues to judge line truth
  from LCOV.
- Stable Rust produces no LCOV branch records. Branch coverage on stable is a
  documented known issue, not a hidden requirement; `require_branch` stays
  false.
- R2 is planned as ingested evidence from `cargo-mutants` `outcomes.json`, not
  as an assay-native Rust mutator.
- R3 is planned through the existing canary mechanism. The import-break
  equivalent is a panicking `#[cfg(test)]` test that writes the per-attempt
  receipt before failing.
- The target remains the full R1-R3 contract. R2 is **waived for now**, with a
  per-release waiver recorded in release evidence. Tests, reviews, and R1
  remain required while that waiver is in force; it is not an R2 pass.

## Mutation policy

- The end-state diff-scoped R2 gate requires **100% killed**.
- Mutation exclusions live only in `.cargo/mutants.toml`; every exclusion
  carries a reviewed reason.
- `#[mutants::skip]` is forbidden. It scatters suppression across source and
  requires the `mutants` crate as a runtime dependency.
- Whole-crate mutation campaigns are advisory, run on the test hosts after the
  release work, and are sharded for wall-clock scheduling. Shards never change
  a candidate's classification.
- Timeouts are `budget_exceeded`, never kills or survivors. The mutation
  command uses a baseline-derived timeout floor so host load cannot decide a
  verdict.
- The current per-release waiver does not relax the mutation policy above when
  an R2 gate is actually run.

## Baseline checks and unsafe code

Every Rust project commit has a baseline containing:

- `cargo fmt --all --check`;
- `cargo clippy ... -- -D warnings`;
- `cargo nextest` with zero retries;
- `cargo test --doc`;
- the R1 diff-line and per-crate line/region floors;
- `cargo deny` for licenses, bans, and sources;
- property round trips where the project declares them;
- golden fixtures shared independently with the Rust and Python
  implementations.

`#![forbid(unsafe_code)]` applies everywhere except the explicitly reviewed
`kernel` and `act` crates in the hostobs design.

## Deferred work

These are backlog items after the first releases, not current gate claims:

- `cargo-semver-checks` once public API consumers exist;
- Miri and sanitizers in a VM lane for the allowlisted unsafe crates;
- load-independent benchmark regression gates;
- region-strict LCOV analysis, which requires an `llvm-cov-json` format rather
  than extending LCOV past its line-granular truth;
- judging branch data if a stable producer ever emits it;
- an assay-native Rust mutator only if cargo-mutants format churn costs more
  than owning an engine.

Fuzzing is its own later assay/run-gate topic. It belongs to the estate's T3
asynchronous tier as an R2-class activity: fuzz findings become tracked backlog
work and, where a regression is claimed, an R4 red-first test. Fuzzing does not
block a release.

## Hostobs control surface

`hostobsctl` and `hostobsd` are Python on `cli-extended` now. A Rust ctl
client becomes work only if measurement shows that it is needed; there is no
Rust contract crate at present.

## Work-package sequence

The durable work packages are filed as assay backlog entries:

- `R-0`: disposable-container spikes, currently in progress in another job;
- `R-1`: `tester-unified-rust`, the hostobs workspace skeleton, and the initial
  lint/selftest lanes;
- `R-2`: the Rust R1 adapter, first-failure recognizers, fixtures, and hostobs
  R1 lane;
- `R-3a`: mutation/report/reservation spikes;
- `R-3b`: cargo-mutants ingestion, changed-diff export, and the R2 lane;
- `R-4`: Rust canary injectors and R3;
- `R-5`: cmru's crates.io source, cargo lock/image update path, cache/lock
  tripwire, and advisory check;
- `R-6`: advisory whole-crate mutation lanes and host sharding.

The R2 waiver controls when R-3b enters a release gate; it does not erase the
planned work.
