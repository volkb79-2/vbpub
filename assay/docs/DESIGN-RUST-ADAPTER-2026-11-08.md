<!-- origin: 2026-11-08; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/assay-RUST-ADAPTER-DESIGN.md -->
<!-- relation: Rust adapter design -->

# assay adopts Rust: adapter design and Rust test methodology for hostobs (2026-11-08)

Opus design agent, read-only. Scope: how assay judges Rust (R0-R3), the test methodology for `hostobs` and the
sampler crate (`.worktrees/topos-sampler-poc-20261105/topos/sampler`), and what cmru must learn (CMRU-NATIVE).
Tags: **[V]** verified by reading code or the tool's own docs/source today; **[K]** general knowledge, not checked
here; **[U]** unknown, needs the named spike. No cargo/rustc is installed in the cockpit (`which` found none), so no
Rust output was produced for this report.

Binding operator decisions (hostobs-DECISIONS-20261107.md "Rust methodology", [V]) are taken as given. Dangers I
flag, without re-opening them: (1) in-repo vendoring in a monorepo whose `.git` is already 682 MB [V] (§E);
(2) cargo-mutants states its `mutants.out` format "is subject to change in future versions" [V mutants.rs], while
cmru moves it forward every 14 days, so a format change turns R2 red until a fixture refresh (fail-closed, but it
happens); (3) the sampler crate uses exact `=` pins in `Cargo.toml` [V] and fetches crates from the network inside
its Dockerfile [V], both contrary to the decisions; hostobs must not inherit either.

## 0. The shape in one paragraph

Rust follows the **JavaScript precedent, not the Go one** [V `DESIGN-GUIDE.md` §11 table]: a pure-Python adapter
(`external_tools = ()`), R1 through the **existing lcov parser** (cargo-llvm-cov writes lcov; zero parser code),
R2 **by ingestion** of cargo-mutants' report through a new FORMAT module beside `mutation-report-json`, and R3 by
two small pure text injectors. No shipped Rust helper (`syn`), no tree-sitter: nothing in the verdict needs a Rust
parser, because llvm-cov already reports line truth and cargo-mutants already did the parsing that mutation needs.
The core stays language-free; the registry gets `rust` at R1, then R2, then R3 (`registry.py` keys by
(language, rigor), [V]).

## A. Adapter mapping (`assay/src/assay/adapters/rust.py`, new)

| Protocol member ([V] `adapters/base.py`) | Rust implementation | Notes |
|---|---|---|
| `name` | `"rust"` | |
| `source_globs` | `("*.rs",)` | |
| `excluded_dir_names` | `{"vendor", "target"}` | cited source: cargo-llvm-cov's own default ignore set (vendored sources, target dir) [V README]. Same §5 citation rule Go applied. |
| `requires_span_attribution` | `False` | llvm-cov gives a line the count of the region that WRAPS it, so interior lines of a multi-line expression get `DA:` records [K]; confirm in S2. |
| `requires_statement_attribution` | `False` | lcov from llvm-cov is line-granular truth, not a block extent like Go's coverprofile [K]; no `statement_blocks` oracle needed. |
| `external_tools` | `()` | the adapter never shells out. cargo, cargo-llvm-cov, cargo-nextest, cargo-mutants are the LANE's argv tools; a missing one fails the argv (R0 `COMMAND_FAILED`), not a silent pass. |
| `for_project` | `self` | no derived fact needed (lcov keys are paths, not crate names). |
| `is_test_path` | mirror cargo-llvm-cov's default report exclusion exactly: a `tests/`, `examples/` or `benches/` segment, or basename `tests.rs`, `*_tests.rs`, `*-tests.rs` [V README] | Mirroring both ways matters: if assay calls a file source but llvm-cov excluded it, the file has no lcov record and `has_executable_code` makes it a loud failure; never a silent excuse. Pin with a real-toolchain fixture. Convention for hostobs: unit tests live in `src/<mod>/tests.rs` (`#[cfg(test)] mod tests;`), as the sampler already does (`src/tests.rs` [V]). |
| `has_executable_code` | pure-Python masking lexer (Go's `go.py` lexer is the template [V]): mask `//`, NESTED `/* */`, `"..."`, raw `r#".."#` (n hashes), byte/C strings, char literals vs lifetimes (`'a'` vs `'a`); then True iff an `fn` token's signature reaches `{` before `;` at paren/bracket depth 0. Any lexing doubt returns True. | Consulted only for a changed file with NO lcov record. Trait method declarations (`fn f(&self);`) and type/const/use-only files return False. A file with fns that are cfg'd out on the gate platform or not in the module tree returns True, so it fails loudly (correct). |
| `normalize_coverage_key` | identity | llvm-cov's `SF:` paths are absolute snapshot paths [K, S2]; the core already maps an absolute key under `repo_top` to its repo-relative form (`evaluate.py` `_to_repo_relative_key`, [V]). |
| `statement_spans` / `statement_blocks` | return `None` | both attribution flags are False. |
| `inject_uncovered_line` | insert, after leading inner attributes / `//!` docs, `#[allow(dead_code)] fn assay_canary_unreached(value: u64) -> u64 { let doubled = value.wrapping_mul(2); doubled.rotate_left(1) }` (rustfmt layout, 4 lines) | `#[allow(dead_code)]` is required: rustc warns on an unused private fn [K] and gate lanes run with warnings denied. Inserted at the top, not appended, so clippy's `items_after_test_module` cannot fire [K]. No `let x = ..; x` (clippy `let_and_return`) [K]. rustc emits zero-count coverage for unused non-generic fns [K, S2]: the body lines are executable and uncovered, the R1 axis. |
| `inject_import_break` | insert at the same point `#[cfg(test)] #[test] fn assay_canary_import_break() { if let Some(p) = std::env::var_os("ASSAY_CANARY_RECEIPT") { let _ = std::fs::write(p, "<nonce>"); } panic!("assay-canary-import-break:<nonce>"); }` | Rust has no executable module top level (like Go). A panicking unit test in the module is the honest equivalent: it runs whenever the gate runs that module's unit-test binary, and it writes the per-attempt receipt before failing, so the existing receipt attribution ([V] DESIGN-GUIDE "R3 import-break receipt attribution") works unchanged. Rejected: `compile_error!` (fails at compile time, cannot write a receipt). Document the semantic as "test-break" in the description string. |
| `generate_mutation_sites` | `"UNSUPPORTED"` | native mutation stays off; R2 is ingested (§D). The seam stays open for a later native engine (operator decision 1). |

**Statement attribution risk, stated plainly.** llvm-cov derives a line's count from the regions on it: when
several regions start on one line, the line is reported executed if any of them ran [K]. `let v = if a { f() }
else { g() };` on one line with the else arm never taken is a COVERED line. That is assay's existing Go ruling,
B055/A-413 ("an uncovered statement sharing a physical line with a covered one is laundered; a documented
line-granularity limit") [V backlog], and it gets the same ruling here: known issue K1, partly closed by the crate
region floor (§C) and by R2 (a mutant on the untaken arm survives, loudly). A diff-level region check is backlog
(§H, BL-RUST-REGION), not a first-milestone item.

Rejected parsing options: a shipped `syn` helper (needs its own vendored crates inside assay's wheel and a
toolchain at judge time, the Go helper's whole cost [V `go_stmtpos.py`]) buys nothing, because no protocol member
needs a Rust AST; tree-sitter adds a native dependency to a stdlib-only tool [V DESIGN-GUIDE §9/§14 posture].

## B. R0: test exit code and its witness

- **Lane command** (R0+R1 lane): `cargo llvm-cov nextest --workspace --locked --offline --profile ci --no-tests=fail
  --lcov --output-path lcov.info && cargo test --doc --workspace --locked --offline`, followed by the crate-floor
  check (§C). R0 is the exit code of that whole argv, A-073's default ([V] DESIGN-GUIDE B078 section).
- **No `result_report` for this lane.** B078's report override exists for vitest's exit-code defect (RG-45) [V].
  nextest's codes are specific and trustworthy: 0 OK, 4 NO_TESTS_RUN, 100 TEST_RUN_FAILED, 101 BUILD_FAILED, 1
  unknown [V docs.rs `NextestExitCode`]. Nothing to override. nextest can write JUnit; keep it as a disclosed
  artifact for humans only, not as evidence.
- **The "trusted failed-call witness"** is a pytest-specific mechanism of NATIVE R2 ([V] DESIGN-GUIDE §6 B114 text).
  It has no Rust counterpart because Rust R2 is ingested; the per-mutant kill witness is defined in §D instead.
- **Doctests:** nextest does not run them [V nexte.st]. They run in R0 via `cargo test --doc` and count for R0
  only; doctest coverage needs nightly in cargo-llvm-cov [V README] (known issue K4). Policy: doctests are
  examples, not the coverage carrier.
- **First failing test (B146):** add two recognizers to `failure_summary.py` ([V] it knows pytest/go/vitest/jest):
  nextest `FAIL [ 0.012s] crate::bin path::test`, libtest `test path ... FAILED`. Small, part of WP R-2.
- **Flake policy:** retries are zero, everywhere. `.config/nextest.toml` `[profile.ci] retries = 0`, the argv also
  passes `--retries 0`, and `NEXTEST_RETRIES` is never in `env_passthrough`. Nextest otherwise treats a pass on
  retry as success (exit 0) [V nexte.st retries]. Slow tests get `slow-timeout` warnings only, never
  `terminate-after`: a wall-clock kill under host load is the load dependence the estate forbids. Tests use
  injected clocks and fake cgroupfs/proc trees (astra §G [V]); no sleep-and-assert.

## C. R1: coverage via cargo-llvm-cov

| Topic | Decision |
|---|---|
| Format | `judge.coverage.format = "lcov"`, existing parser [V `coverage_parsers/lcov.py`, which already names Rust as an lcov producer]. Producer vocabulary for lcov is closed and empty [V]; nothing to declare. |
| Branch | stable writes no `BRDA` (branch needs nightly `--branch` [V README]), so the parser reports branch capability unavailable [V lcov.py] and assay judges lines. `require_branch` stays false. Known issue K2. If someone ever enables nightly branch output, assay judges it automatically (A-258) [V]. |
| Diff rule | `mode = "changed_lines"`, `fail_under = 100.0`, `allow_excluded = false` (lcov has no exclusion field; `excluded` is `None` [V]). `base_source = "request"` like run-gate-project [V `assay.toml`]. |
| Crate floor (line + region) | assay judges diff lines only. The floor is a lane step, not an assay claim: `cargo llvm-cov report --json --summary-only --output-path cov-summary.json` (re-uses the profile data, no second test run [V README `report`]) then `tools/crate-floors.py` (stdlib, ~60 lines) aggregates per-file line and region summaries by crate directory against committed `coverage-floors.toml`. Fails R0 with the crate named. cargo-llvm-cov's own `--fail-under-*` flags are workspace totals [V README], hence the script. |
| Test code | files excluded by convention (§A `is_test_path`). Inline `#[cfg(test)] mod tests { }` blocks inside a source file ARE measured; their lines run when tests run, so they rarely hurt; an unused test helper there fails loudly (K5). Convention: test modules in their own `tests.rs`. |
| Generated code | derives: rustc does not instrument `#[automatically_derived]` impls [K, S2], serde marks its impls so; a derive line therefore has no `DA:`. Proc-macro and `macro_rules!` bodies: code is attributed to the call site, lines inside a local `macro_rules!` body get no record [K, S2] and cargo-mutants does not mutate token trees [K], so logic inside `macro_rules!` is unjudged at both tiers (K6, review rule: no logic in macros). `include!`d or build-script-generated code is out of scope; hostobs has no `build.rs` (policy). |
| `--ignore-filename-regex` | not used. Defaults already drop vendor/target/tests [V README]; every extra regex is an unreviewed exclusion. |
| Workspaces | one lane per workspace; `cwd` = workspace root; `source_roots = ["crates", "bins"]`. Absolute `SF:` keys resolve through the core [V]. |
| Platform `cfg` | code compiled out on the gate target has no `DA:` records and is invisible to R1 (K7). Mitigation is R2: cargo-mutants mutates syntactically, so a mutant in cfg'd-out code compiles, nothing catches it, and it survives loudly [K]. hostobs is Linux-only. |

## D. R2: cargo-mutants by ingestion

**Contract read from upstream ([V] mutants.rs pages and `src/outcome.rs`, `src/mutant.rs`, `src/process.rs`):**
exit 0 all caught, 1 usage, 2 missed mutants, 3 timeouts, 4 baseline failing, 5 `--in-diff` new side does not
match the tree, 6 invalid diff, 70 internal. `outcomes.json` = `{outcomes[], total_mutants, missed, caught,
timeout, unviable, success, start_time, end_time|null, cargo_mutants_version}`. Each outcome is `{scenario:
"Baseline" | {"Mutant": {...}}, summary, log_path, diff_path, phase_results[{phase: Check|Build|Test, duration,
process_status, argv}]}`. `summary` ∈ {Success, CaughtMutant, MissedMutant, Unviable, Failure, Timeout};
`process_status` is serde-default `"Success" | {"Failure": n} | "Timeout" | {"Signalled": n} | "Other"` [V enum,
JSON shape K]. A mutant is `{name, package, file, function, span, replacement, genre}`, genre ∈ {FnValue,
BinaryOperator, UnaryOperator, MatchArm, MatchArmGuard, StructField} [V]. Span column units are [U] (S3).

**New format module** `mutation_parsers/cargo_mutants_outcomes.py` (keyed by format, per the registry rule [V]):

| cargo-mutants | assay | Rule |
|---|---|---|
| `CaughtMutant` with Test-phase `{"Failure": 100}` (nextest TEST_RUN_FAILED) | `killed` | This is the Rust kill witness: a test failed in a completed run. `--test-tool nextest` is mandatory in the lane. |
| `CaughtMutant` with any other Test status (`Signalled`, `Other`, 101, ...) | `crashed` | a crash is not a kill. |
| `MissedMutant` | `survived` | |
| `Timeout` | `budget_exceeded` | Stryker precedent [V `INGESTED_STATUS_BUCKETS`]: outside the score, listed for triage. |
| `Unviable` | `discarded`, reason compile error | B070 precedent [V]. |
| `Success`/`Failure` on a Mutant, `Timeout`/`Failure` on Baseline, unknown summary or key, `end_time` null | refuse report (`UNREADABLE_ARTIFACT`) | closed vocabulary, incomplete evidence is not evidence [V parser doctrine]. |

- **Identity**: `operator = "cargo-mutants:<Genre>"` (new namespace in `vocabulary.INGESTED_OPERATOR_NAMESPACES`
  [V]; schema impact [U] S7), `replacement_sha256` = sha256 of `replacement`, byte span from `span` converted
  against the committed blob.
- **What differs from Stryker, and how it stays fail-closed.** The report embeds no `source` and no `projectRoot`
  [V]. (1) Freshness rests on the existing artifact reservation, "never accepts a report it did not watch being
  produced" [V CONSUMERS B046]. (2) The content tier is per mutant: its `diff/*.diff` minus-lines must equal the
  committed blob at those lines; any mismatch refuses. (3) `sources` for `lines_without_candidates` come from the
  committed blobs assay already reads. This needs a small ingest-path change: the reader takes the directory and a
  blob reader, not a text. Reservation of a directory artifact (`mutants.out/`) and the dirty-tree check against
  it are [U] S3.
- **R0 of the R2 lane.** cargo-mutants exits 2/3 when mutants are missed or time out, which A-073 would turn into
  R0 `COMMAND_FAILED` before assay reads the report (Stryker needs `thresholds.break: null` for the same reason
  [V]). cargo-mutants has no such switch [K]. Fix through the existing B078 seam [V]: a `result_report` reader
  `cargo-mutants-outcomes` reading `outcomes.json`; complete = `end_time` set and a Baseline outcome present;
  failures = Baseline summary is not `Success`. Exit 2/3 then no longer decide R0; exit 4 (baseline failed) is a
  FAIL; exit 1/70 leave no complete report and fall back to the exit code (FAIL). Rejected: a per-lane `bash`
  wrapper mapping exit codes, which is hand-written fail-open territory.
- **Diff scoping.** assay owns the base (`base_source = "request"`) and currently exports nothing about it to the
  command [V: no `ASSAY_BASE`-like variable exists]. Add `ASSAY_CHANGED_DIFF`: a path, outside the snapshot, to
  `git diff <resolved base>..<commit>` for ingested changed-lines lanes. The lane runs `cargo mutants --in-diff
  "$ASSAY_CHANGED_DIFF"`, which keeps mutants that "overlap with regions changed in the diff" [V] and refuses a
  diff whose new side does not match the tree (exit 5) [V]. assay still scopes by its own rule (mutant start line
  is an added line [V `ingest_mutation_report`]); overlap is a superset of start-line, so `--in-diff` is only a
  cost filter and can never shrink assay's scope. Known gap K8: an `FnValue` mutant whose span starts on an
  unchanged line of an edited function is run but not judged (span start [U] S3).
- **Flags**: `--test-tool nextest --cargo-arg=--locked --jobs 2 --timeout-multiplier 10 --minimum-test-timeout 120
  --output <lane dir>` with `.cargo/config.toml` forcing offline. `--test-workspace=false` (test only the mutated
  package) [K, S3]: cheaper, forces per-crate tests, and can only add survivors (the loud direction). No build
  timeout (off by default [V]; build-time kills are load-dependent). `--shard k/n` is refused by assay on
  ingested lanes [V CONSUMERS], so shards exist only in the advisory whole-crate lanes (§E); every shard runs its
  own baseline [V]. No `--baseline=skip`.
- **Load independence**: timeouts are 10x baseline with a 120 s floor and land in `budget_exceeded`, never
  `killed` or `survived`. An OOM-kill inside a test would read as Test `{"Failure": 100}` (nextest reports a
  signalled test as a failure [K]); the lane's cgroup OOM event already forces the run-gate verdict to ERROR [V
  run-gate.toml comment], which is the campaign-level guard. Known issue K9.
- **Survivors and equivalent mutants**: diff lanes gate at `fail_under = 100.0`. `equivalence_artifact` is refused
  on ingested lanes [V], so an equivalent mutant is handled by an exclusion. Exclusions live in exactly one
  reviewed file, `.cargo/mutants.toml` `exclude_re`, one regex per entry, each with a `# reason:` comment;
  `#[mutants::skip]` is forbidden (lint-lane grep) because it scatters suppressions and requires the `mutants`
  crate as a regular dependency [V mutants.rs attrs]. Suppressed mutants vanish from the report, so assay's
  `lines_without_candidates` [V] is where a reviewer sees them. The lint lane also refuses `exclude_re` entries
  without a reason.
- **Rust noise**: derives are not mutated (no source fn) [K]. Hand-written `Debug::fmt` produces FnValue survivors:
  policy is "derive Debug, never hand-write it". `Display` is user-visible (errors, CLI output) and is tested by
  goldens, not excluded. Logging/`eprintln!` arguments are macro token trees and not mutated [K]. `-> ()` side-effect
  fns ("replace f with ()") are real signal; exclude only with a reason.
- **Cost estimate for hostobs core crates** [K estimate, measure in S3]: the sampler is ~8.5k lines with ~2k of
  tests [V `wc`], so ~6.5k non-test lines; cargo-mutants yields roughly one mutant per 6-12 such lines, i.e.
  ~550-1100 for the whole set. With an incremental rebuild of one crate plus a test run at ~10-30 s per mutant and
  `--jobs 2`: whole-workspace campaign ~1.5-5 h wall, diff-scoped run for an S change (<300 lines, 20-60 mutants)
  ~5-20 min. Disk: one tree copy plus target dir per job, ~2-4 GB for 2 jobs. Each lane also pays one full
  instrumented build (fresh snapshot, no warm target dir): ~2-4 min [K].

## E. Lanes, tester image, vendoring, offline, reproducibility

**Lanes** (`hostobs/run-gate.toml`, names mirror run-gate-project [V]; `hostobs/assay.toml` holds the judged ones):

| run-gate lane | kind | Content |
|---|---|---|
| `lint` | command | `cargo fmt --all --check`; `cargo clippy --workspace --all-targets --locked --offline -- -D warnings`; `RUSTDOCFLAGS=-D warnings cargo doc --workspace --no-deps --locked --offline`; `cargo deny --offline check licenses bans sources`; `tools/check-lints.sh` (every crate has `[lints] workspace = true` with `unsafe_code = "forbid"` except allowlisted `kernel`/`act`, no `#[mutants::skip]`, every `exclude_re` has a reason). |
| `selftest` | command | M0 only, before the adapter: R0+R1 command of §B plus crate floors. Retired into `assay-r1` once R-2 lands. |
| `assay-r1` | assay → `[lanes.r1]` | rigor `["R0","R1"]`, `language = "rust"`, lcov, `fail_under = 100.0`, base from request. |
| `assay-r2` | assay → `[lanes.r2]` | rigor `["R0","R2"]`, ingested `cargo-mutants-outcomes`, `fail_under = 100.0`, `result_report` of §D. Pre-merge, not in `gate-full` (run-gate-project precedent [V]). |
| `assay-r3` | assay → `[lanes.r3]` | rigor `["R0","R1","R3"]`, both canaries on one declared target per core crate. |
| `mutants-<crate>-<k>of<n>` | command, advisory | whole-crate `cargo mutants --package <c> --shard k/n --sharding round-robin`; release-time, on r1002/v1001 later; humans read `missed.txt`. |
| `conformance` | command | existing BuildKit multi-stage pattern (release binary vs Python goldens) [V sampler Dockerfile], moved onto vendored sources. |
| `gate-full` | command | `lint && assay-r1 && assay-r3 && conformance`. |
| `vm-*` | per privileged verb | out of scope here (plan §7.3 [V]). |

**Image `tester-unified-rust`**, a sibling of `tester-unified` exactly like `tester-unified-go` [V its Dockerfile
header: adding a toolchain to tester-unified "would rebuild and re-risk the gate of" every Python project].
`FROM rust:<X.Y.Z>-slim-trixie@sha256:<digest>`; `rustup component add clippy rustfmt llvm-tools-preview`;
`rustup target add x86_64-unknown-linux-musl`; `cargo install --locked --version <v>` of cargo-nextest,
cargo-llvm-cov, cargo-mutants, cargo-deny in a builder stage (network allowed at image build, never in gates);
`apt-get install python3` for assay's zipapp (slim has none [K, S5]; assay needs >=3.11 [V go Dockerfile note]);
run user uid 1003 as in the Go image [V]. Size ~1.1-1.4 GB [K, S5] versus tester-unified 2.99 GB [V `docker
images`]. Versions are build args fed by cmru (§F), not literals edited by hand. No pytest here; Python
conformance keeps running in Python images.

**Vendoring.** Decision (within operator decision 3): `hostobs/vendor/` **in git**, produced only by cmru, filtered
to Linux targets. Why: zero distribution machinery, offline by checkout, works inside assay snapshots and
cargo-mutants' tree copies (`directory = "vendor"` is relative to the config file [K]). The danger: `cargo vendor`
copies every platform's crates. proptest (via rand/getrandom), tempfile and ratatui/crossterm pull
`windows-sys`/`windows-targets`, whose `windows_*` crates carry prebuilt import libraries [K]; unfiltered that is
tens of MB per version, and every bump stays in history forever. Mitigation: `cargo vendor-filterer
--platform=x86_64-unknown-linux-gnu --platform=x86_64-unknown-linux-musl` (empty stubs for foreign-platform crates)
[K, S1], plus `.gitattributes` `hostobs/vendor/** -diff linguist-vendored`. Tripwire: if filtered `vendor/` exceeds
25 MB, or one bump adds more than 5 MB, cmru refuses and the operator re-decides (fallback: a content-addressed
`vendor-<sha256(Cargo.lock)>.tar.zst` outside git, mounted read-only into the lane).

**Verification against `Cargo.lock`.** Each vendored crate carries `.cargo-checksum.json` (`package` = the
registry `.crate` sha256, `files` = per-file sha256) [K]. With source replacement and `--locked`, cargo refuses a
package whose checksum disagrees with `Cargo.lock` and a file whose hash disagrees with the manifest [K, S1, which
must include a tampered-file negative test]. So every gate build verifies the vendor tree; cmru additionally runs
`cargo vendor` into a scratch dir and diffs it against the committed tree (catches extra or stale dirs).
`cargo deny check sources` restricts sources to crates.io; `licenses` allowlists MIT, Apache-2.0, BSD-2/3,
ISC, Zlib, Unicode-3.0 (the set confirmed by the first real run); `bans` reports duplicate versions. Vendored
crates keep their own LICENSE files; the release tarball ships a generated third-party notice.

**Offline.** `hostobs/.cargo/config.toml`: `[source.crates-io] replace-with = "vendored-sources"`,
`[source.vendored-sources] directory = "vendor"`, `[net] offline = true`. Lanes set `CARGO_NET_OFFLINE=true` and
pass `--locked --offline`. Whether a run-gate lane can additionally declare no network is [U] S6.

**Reproducible build** (release `artifact` target only, not test lanes, so coverage RUSTFLAGS are not perturbed):
`--locked --offline --release --target x86_64-unknown-linux-musl`, profile `lto = true`, `codegen-units = 1`,
`strip = true` (as the sampler [V]), `CARGO_INCREMENTAL=0`, `SOURCE_DATE_EPOCH` from the commit time,
`RUSTFLAGS=--remap-path-prefix=$PWD=/build`, digest-pinned base image. Proof: build twice in separate containers,
compare sha256 (a lane, not a promise).

## F. CMRU-NATIVE, minimal

What exists [V cmru README/DESIGN-GUIDE "Supply-chain age-windowed versions"]: `cmru versions check|resolve` with
`.pypi`, `.npm`, `.go`, `.oci` sources, 14-day window, exact overrides with reason (+ expiry when inside the
window), native lock writers, no build/gate/scheduled hook. What Rust needs:

1. **Toolchain**: nothing new. The rust image is an `.oci` target with semver selection; a new stable is adopted
   14 days after release, which also lets the usual `X.Y.1` regression fix land first.
2. **`.crates` source** (new): crates.io API version list with `created_at` and `yanked` [K]; a contact
   User-Agent is required by crates.io policy [K]. Covers direct dependencies and the four cargo tools (image build
   args).
3. **cargo writer** (new): `cargo update`, then for every locked crates.io package newer than the cutoff, `cargo
   update -p <name>@<ver> --precise <newest eligible>`, repeated to a fixed point (the sampler author did exactly
   this by hand [V README "Two transitive crates are pinned below their newest release"]). No eligible version
   that satisfies the constraints is a refusal naming the crate, resolved only by an exact override. If cargo
   gains a publish-time cutoff, use it instead [U] S8. Then `cargo vendor-filterer`, the size tripwire, the
   scratch re-vendor diff, and `cargo metadata --offline --locked` as the smoke test.
4. **Advisories (fast CVE path)**: `cmru versions check` also runs `cargo deny check advisories` with a fetched
   advisory DB (network allowed there, never in gates). An advisory against a locked version prints the exact
   override to add (`version`, `reason = "RUSTSEC-..."`, `expires`), which is cmru's existing exception shape.
5. **Manifest policy**: `Cargo.toml` uses `[workspace.dependencies]` with plain caret floors and a reason comment
   per floor (estate version policy); `Cargo.lock` + `vendor/` are the single moving state, which is what
   `--locked` needs (matches the interpretation in the decisions file [V]). The sampler's `=` pins go away.
6. **Flow**: resolve runs in a worktree, produces one commit (`Cargo.lock`, `vendor/`, cmru resolved record,
   image build args), the normal lanes gate it, a Sonnet review reads the lock diff plus the deny output, and the
   controller merges. Agents never push, merge or release. A bump of cargo-mutants or cargo-llvm-cov must re-run
   assay's Rust qualification test in the image; a format change fails it, which is the intended alarm.

## G. Work packages (dependency order)

Owners: Codex luna implements, Sonnet reviews (one Fable review per tool per standing rule), Opus designs. Sizes:
S < 300 changed lines, M < 900, L more.

| WP | Content | Size | Owner | Gate | Needs |
|---|---|---|---|---|---|
| R-0 | Spikes S1, S2, S5 in a disposable container (vendor size and checksum negative test; llvm-cov facts: unused fn, derives, macro bodies, absolute `SF:`, inline test mods; image size and python) | S | Codex, Opus reads | written measurements with commands | — |
| R-1 | `tester-unified-rust` image; hostobs workspace skeleton from the sampler; `.cargo/config.toml`, `.config/nextest.toml`, `deny.toml`, `coverage-floors.toml`, `tools/crate-floors.py`, `tools/check-lints.sh`; filtered `vendor/` produced by hand with the documented commands; `lint` + `selftest` lanes | M | Codex / Sonnet | both lanes green on the real gate; an offline run with `--network=none` passes | R-0 |
| **MILESTONE 1** | **hostobs M0/M1 run on the real gate: fmt, clippy, deny, nextest, doctests, crate line+region floors.** Not yet assay-judged. | | | | R-1 |
| R-2 | assay `RustAdapter` registered at R1; nextest/libtest first-failure recognizers; real-toolchain fixtures (`tests/fixtures/rust/`, generated in the image, A-234 discipline [V go.py header]); `tests/qualification/test_rust_r1_real.py` run inside the image; CONSUMERS "Rust lanes" + DESIGN-GUIDE §11 row; hostobs `assay-r1` replaces `selftest` | M | Codex / Fable once, then Sonnet | assay's own lanes + qualification in the image | R-1 |
| **MILESTONE 2** | **100% diff-line coverage judged by assay on hostobs.** | | | | R-2 |
| R-3a | S3/S4/S7 spikes (span units, zero-mutant `--in-diff`, baseline-failure report, directory artifact reservation, schema impact) | S | Codex | measurements | R-1 |
| R-3b | `cargo-mutants-outcomes` mutation format + per-mutant diff content check; `cargo-mutants-outcomes` result_report reader; `ASSAY_CHANGED_DIFF`; namespace; R2 registered for rust; fixtures from real runs incl. unviable/timeout/missed | L (two M halves: parser+ingest, env+result_report) | Codex / Sonnet | assay lanes + qualification | R-2, R-3a |
| **MILESTONE 3** | **diff-scoped cargo-mutants gate (`assay-r2`) on hostobs.** | | | | R-3b |
| R-4 | canary injectors + real-toolchain oracles (import-break gives `COMMAND_FAILED` with receipt, uncovered-line gives `UNCOVERED_LINES`); `assay-r3` lane | S-M | Codex / Sonnet | qualification in the image | R-2 |
| R-5 | cmru `.crates` source, cargo writer, vendor filter + tripwire + re-vendor diff, advisories in `check` | M-L | Codex / Sonnet (Fable once) | cmru own gate; a dry resolve against hostobs | R-1 (parallel to R-2..R-4) |
| R-6 | advisory whole-crate mutation lanes; host sharding on r1002/v1001 | S | Codex / Sonnet | one complete campaign per core crate, timings recorded | R-3b |

R-5 runs in parallel; until it lands, dependency bumps follow its documented manual commands. Every WP updates
the README/DESIGN-GUIDE/CONSUMERS of the tool it touches in the same change.

## H. Known issues / by design (document up front) and backlog

| ID | Known issue | Why accepted | Who catches it instead |
|---|---|---|---|
| K1 | several regions on one line: covered if any ran | line granularity, same ruling as Go B055/A-413 [V] | crate region floor; R2 survivors |
| K2 | no branch coverage on stable | operator decision 2 | R2; `require_branch` stays false |
| K3 | diff-level region coverage not judged | needs a new `llvm-cov-json` format (backlog) | crate region floor |
| K4 | doctests run (R0) but give no coverage, and mutants killed only by doctests count as missed | stable limit [V cargo-llvm-cov, mutants.rs nextest page] | unit tests carry coverage |
| K5 | inline `#[cfg(test)]` modules are measured | lcov has no per-region exclusion | `tests.rs` convention |
| K6 | logic inside `macro_rules!` bodies is unjudged at R1 and R2 | coverage maps to call sites; mutation skips token trees [K] | review rule; `lines_without_candidates` |
| K7 | code compiled out by `cfg` on the gate target is invisible to R1 | not compiled, not instrumented | R2 survivors (syntactic mutation) |
| K8 | `FnValue` mutants starting on an unchanged line are run but not judged | assay's start-line scope rule [V] | R1 100% diff lines |
| K9 | an OOM-killed test reads as a kill inside cargo-mutants | no per-mutant cgroup view for ingested R2 | run-gate lane OOM event forces ERROR [V] |
| K10 | async/timing tests | flaky by nature under load | injected clocks; no wall-clock asserts; retries 0 |
| K11 | `budget_exceeded` (timeouts) is outside the score | existing assay policy [V CONSUMERS B098] | generous timeouts; triage list |
| K12 | cargo-mutants output format has no stability promise [V] | upstream choice | strict parser + qualification test on every bump |
| K13 | full instrumented build per lane run (fresh snapshot) | correctness over cache reuse | measured cost, ~minutes |
| K14 | `unsafe` allowed in `kernel`/`act` without miri/sanitizers | operator decision 5 | review; VM lanes; BL-RUST-MIRI |

Backlog entries to file (assay: `assay/nyxloom-trove/4-backlog.md`, next free id after B201 [V]; run-gate and cmru in
their own backlogs per the cross-repo convention): **BL-RUST-SEMVER** cargo-semver-checks for `proto`/`client`
once they have external consumers; **BL-RUST-MIRI** miri + ASan/TSan in a VM lane for `kernel`/`act` (nightly
image, advisory); **BL-RUST-BENCH** benchmark regression gate with load-independent metrics (instruction counts,
not wall time); **BL-FUZZ** fuzzing as its own assay/run-gate topic (cargo-fuzz corpus as Tier-3 attested evidence
is a candidate shape); **BL-RUST-REGION** `llvm-cov-json` format with region-strict line truth (a line is
executed only if every region starting on it ran), closing K1/K3; **BL-RUST-BRANCH** judge stable branch output
when it exists (no code change needed, A-258); **BL-RUST-NATIVE-R2** assay-native Rust mutation only if
cargo-mutants' format churn costs more than an engine.

## I. Five questions for the operator

1. **Vendor location**: in-git `hostobs/vendor/`, filtered to Linux, with a 25 MB / 5 MB-per-bump tripwire that
   reopens the choice (fallback: a content-addressed archive outside git). Accept?
2. **"Line + region"** = 100% diff LINES judged by assay plus per-crate line AND region floors as a lane step;
   region at diff level is backlog (K3). Accept, and set the floors (proposal: `proto`, `eval`, `act` 95 line / 90
   region; `store`, `kernel`, `client` 90/85; bins 80/75)?
3. **R2 policy**: diff-scoped `fail_under = 100` as a pre-merge gate; exclusions only in `.cargo/mutants.toml`
   with reasons; `#[mutants::skip]` forbidden; whole-crate campaigns advisory at release time. Which crates get
   whole-crate campaigns (proposal: `proto`, `eval`, `act`)?
4. **Image**: a separate `tester-unified-rust` sibling (Go precedent) rather than +1.2 GB on `tester-unified` for
   every Python lane. And: stable toolchain only, MSRV = the image's rustc, no separate MSRV lane?
5. **cmru scope for Rust**: do dev-dependencies (proptest) and the four cargo tools move under the same 14-day
   rule and the same commit as runtime crates (proposal: yes, one `cmru versions resolve` commit per cycle), and
   is the import-break canary as a panicking module test (§A) acceptable as Rust's "import-break"?

## Spikes referenced ([U])

S1 vendor size (filtered/unfiltered) and cargo's refusal of a tampered vendored file under `--locked`; S2
llvm-cov facts (unused-fn zero counts, `#[automatically_derived]` skip, macro bodies, absolute `SF:`, wrapped
lines); S3 cargo-mutants span columns (1-based? chars or bytes?), `FnValue` span start, zero-mutant `--in-diff`
output, `--test-workspace=false`, directory artifact under assay's reservation and dirty-tree check; S4 report
completeness after baseline failure (exit 4); S5 image size and python3; S6 run-gate per-lane network-none and the
`tester-unified-rust` environment entry in `run-gate.root.toml`; S7 assay schema bump or widening for the new
operator namespace; S8 a cargo/crates.io publish-time cutoff (`pubtime` in the sparse index) usable by cmru.
