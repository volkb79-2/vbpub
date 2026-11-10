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

These source documents remain outside this repository under
`/home/vscode/vbpub-release-program-20261027/reports/`:
`assay-RUST-ADAPTER-DESIGN.md`, `hostobs-DECISIONS-20261107.md`, and
`hostobs-PLAN-v4.md`. The tier amendments are decided in
`/home/vscode/vbpub-release-program-20261027/reports/test-policy-REQUIREMENTS.md`
(D1-D8); their design record is
`run-gate-project/docs/DESIGN-lane-rework-2026-11.md` on branch
`test-methodology-20261110`.

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
- Every crate separately carries an initial floor of **90% line coverage and
  85% region coverage**, in addition to Assay's **100% changed-line rule**.
  The region floor is a lane step over `cargo llvm-cov report --json`; Assay
  itself continues to judge line truth from LCOV.
- Stable Rust produces no LCOV branch records. Branch coverage on stable is a
  documented known issue, not a hidden requirement; `require_branch` stays
  false.
- R2 is planned as ingested evidence from `cargo-mutants` `outcomes.json`, not
  as an Assay-native Rust mutator.
- R3 is planned through the existing canary mechanism. The import-break
  equivalent is a panicking `#[cfg(test)]` test that writes the per-attempt
  receipt before failing.
- The T1 gate is sequence [`lint`, `assay-r1`]. At T2, `gate` is sequence
  [`lint`, `assay-r3`, `conformance`], without `assay-r1`; Assay's release-time
  whole-project R1 floor is enforced by `assay-r3` (D1). `assay-r1` remains a
  T1 lane only.
- Assay declares one `mechanism` per lane. T2 `assay-r3` uses the test-break
  (import-break) canary; the uncovered-line probe is a separate T3 lane (D2).

## Mutation policy

- The end-state diff-scoped R2 gate requires **100% killed**.
- Mutation exclusions live only in `.cargo/mutants.toml`; every exclusion
  carries a reviewed reason.
- `#[mutants::skip]` is forbidden. It scatters suppression across source and
  requires the `mutants` crate as a runtime dependency.
- Rust R2 is a T3 cargo-mutants campaign using `--in-diff` against the latest
  release, with `--base <previous release tag>`. It runs asynchronously and
  never gates a merge or release; survivors become backlog items.
- Rust R2 not yet adopted (R-3b pending) is recorded once as a known issue,
  not as a per-release waiver. The target remains the full R1-R3 contract;
  tests, reviews, R1, and the other release gates stay required.
- Whole-crate mutation campaigns are advisory T3 lanes, run asynchronously on
  the test hosts after the release work, and are sharded for wall-clock
  scheduling (for example, `mutants-<crate>-<k>of<n>`). Shards never change a
  candidate's classification or count as a release R2 pass.
- Timeouts are `budget_exceeded`, never kills or survivors. The mutation
  command uses a baseline-derived timeout floor so host load cannot decide a
  verdict.

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

- `B241 (R-0)`: disposable-container spikes, assigned to the
  `rust-r0-20261110` job;
- `B242 (R-1)`: `tester-unified-rust`, the hostobs workspace skeleton, and the initial
  lint/selftest lanes;
- `B243 (R-2)`: the Rust R1 adapter, first-failure recognizers, fixtures, and hostobs
  R1 lane;
- `B244 (R-3a)`: mutation/report/reservation spikes;
- `B245 (R-3b)`: cargo-mutants ingestion, changed-diff export, and the T3 R2 lane;
- `B246 (R-4)`: Rust canary injectors and R3;
- `B247 (R-5)`: cmru's crates.io source, cargo lock/image update path, cache/lock
  tripwire, and advisory check;
- `B248 (R-6)`: T3 advisory whole-crate mutation lanes and host sharding.

The work-package identifiers and acceptance conditions are in
`nyxloom-trove/4-backlog.md`.
