# Design: estate lane rework by test tier (2026-11)

**Status: DESIGN.** No executable change. The method is
`nyxloom/reference/TESTING-METHODOLOGY.md` `## Test tiers by event (T0–T3)`;
this document applies it. It lists what the lanes cost today, the few
Run-Gate and Assay features the rework needs, and the per-project work
packages that implementation jobs execute.

Labels: **measured** = a number printed by a release log or lane; **inferred**
= derived from timestamps; **estimated** = arithmetic on the above;
**UNVERIFIED** = not established by any file read for this design. Log paths
`logs/...` and `reports/...` are under `/home/vscode/vbpub-release-program-20261027/`.

Operator rules this design keeps: presets plus documented per-project
deviations, no new config layer, "known issue / by design" is a legitimate
close, tests stay load-independent, R2 and fuzzing never block a release, R4
is the reviewer's call.

## 1. Findings: where the time goes

### 1.1 Measured release-gate times (this release wave)

| Project | Release test steps (measured) | Source |
|---|---|---|
| cmru 7.2.5 | `gate-provisional` **2,989.8 s** (pytest 741.17 s inside it); 7.2.4: 3,269.4 s | `logs/release-cmru-7.2.5.log:30`, `release-cmru-7.2.4.log:30` |
| assay 12.1.1 | `tester-unified` **1,370.3 s** + provisional qualification **1,300.2 s** | `logs/release-assay-12.1.1.log:30,33` |
| run-gate 25.4.1 | build+receipt 123.1 s + `selftest` **299.2 s** (25.4.0-c: 116.9 s + 246.1 s) | `logs/release-run-gate-25.4.1.log:30,33` |
| pwmcp r6 / r7 | Assay R1 **397.5 s** / **391.6 s** | `logs/release-pwmcp-r6.log:39`, `-r7.log:39` |
| ciu 7.24.1 | coverage gate **230.1 s** + enrollment **60.3 s** (attempt 2: 255.6 s) | `logs/release-ciu-7.24.1.log:30,33`, `-attempt2-dind-missing.log:30` |
| iobench 0.2.0 | `r0-r1` **206.8 s** | `logs/release-iobench-0.2.0.log:30` |
| cgprofile 3.0.0 | `r0-r1` **193.5 s** + `r3` **11.0 s** | `logs/release-cgprofile-3.0.0.log:30,33` |
| topos 0.8.0 | parallel suite **110.9 s** | `logs/release-topos-0.8.0.log:30` |
| cli-extended 0.6.0 | `r0-r1` **82.4 s** + `r3` **4.1 s** | `logs/release-cli-extended-0.6.0.log:29,32` |
| augeaswrap 0.2.0 | `r0-r1` **65.6 s** + `r3` **3.1 s** | `logs/release-augeaswrap-0.2.0.log:30,33` |
| tester-base 0.2.0 | contract lane **6.1 s** / **5.4 s** | `logs/release-tester-base-0.2.0.log:36,48` |

The brief quoted "ciu unit 242 s"; no release log line carries 242 s. The
logged CIU figures are 230.1 s and 255.6 s.

### 1.2 Where it goes, by project class

| Class | Projects | Cost driver | Lever |
|---|---|---|---|
| **A. Large suite, several judged runs** | cmru, assay | The same suite runs 3–6 times per release (cmru: four unmodified controls + two transforms, `reports/gate-reuse-REVIEW.md:21`; assay: installed-wheel R0 over `tests gate/tests` plus a source R0/R1/R3 qualification over `tests`, `assay/assay.toml` lanes `tester-unified` and `self-qualification-provisional`). | Remove duplicate runs (§3 WP-CMRU-1, AS-1). |
| **B. Mid suite, one run** | ciu, run-gate, pwmcp, iobench, cgprofile, topos | One suite run of 2–7 min. Gaps are tier placement, not time: CIU's pre-merge pointer runs R2 (`ciu/nyxloom-trove/nyxloom.toml:58` → lane `ciu`, rigor R0–R3, 8 h budget); CIU, run-gate, pwmcp, iobench, topos release without any R3 probe; run-gate's `gate-full` runs the suite twice (`selftest` + `assay-r1`, `run-gate-project/run-gate.toml:168-176`). | Re-point T1/T2, add a cheap R3, drop the duplicate. |
| **C. Small library, sub-2-min** | cli-extended, augeaswrap, test-policy, iogovmon, damon-analysis, debian-install-v2, gstammtisch-guide | `r0-r1` runs the whole suite twice in two capture modes (cli-extended, augeaswrap); `gate` composites include R2 (`r2` inside `bash -c "run-gate r0-r1 && run-gate r2 && run-gate r3"`). | Composite → sequence without R2; second capture run → T3. |
| **D. Single lane, no Assay** | scripts/mounttrace, netcup, telegram, truenas, voice-screen-share/*, plesk-mailbox-create, tls-edge, tester-base, wings-patchstack, shared-ramdisk-depot-manager, libraries/patchseries, rigor-record, vmscreen | Seconds to minutes. | Mapping only; "no R3/R2 adopted" as a documented known issue. |
| **E. Live / host lanes** | truenas `live-*`, truenas/tnt `live-*`, mdt-cockpit `vm-system`/`image-runtime*`, debian-install-v2 `r1-vm-real-commit` | Real hosts, VMs, images. | Never reusable; T2 only when the release step names them, otherwise T3/manual. |

The estate has **36 project `run-gate.toml` files** (fixture excluded). The
lane inventory was read mechanically (`tomllib` over every file) for this
design.

### 1.3 The CMRU gate, phase by phase

`gate-provisional` runs, serially (`cmru/tools/run_release_gate.py:1514-1555`):
`ref-probe` (~2 s), `installed-wheel-live` (~10.5 s), `assay` (~1,249 s),
`coverage` (~751 s), `canary` (~919 s); residual ~58 s. All per-lane values
are **inferred** (`reports/gate-reuse-INVESTIGATION.md:15-22`).

- `assay` = Assay lane `cmru` (`cmru/assay.toml:29-85`): R0/R1/R3, changed
  lines against `base = "cmru-v6.1.0"` (`:69`), import-break canary on
  `src/cmru/config.py`. One baseline run + one control run + one fast-fail
  transformed run. The baseline/control split is not retained (UNVERIFIED).
- `coverage` (`cmru/run-gate.toml:142-158`) re-runs `pytest tests` with
  `--cov-fail-under=100`: the whole-project floor. Its run produces the same
  coverage data the Assay baseline already produced.
- `canary` (`:282-299`, `cmru/tools/coverage_canary.py:86-103`) runs the
  suite twice more: a control and a transformed run that appends an
  unreached function and expects the floor error.

`cmru-first-release` (`cmru/assay.toml:87-163`) already judges the whole
project under Assay (`mode = "whole_target"`, every module as a literal
target, `fail_under = 100`, branch required, no exclusions). The coverage lane
is therefore replaceable by configuration alone; no feature is needed for the
floor. `whole_target` also removes the stale-looking `base = "cmru-v6.1.0"`
pin from the release path (whole-target lanes declare no base;
`run-gate-project/assay.toml:16`).

### 1.4 What verdict reuse (RG-253) would save

RG-253 v1 saves **0 s** on today's CMRU layout
(`DESIGN-verdict-reuse.md:527-541`). Its real use case is a repeat of an
identical tree: a release retry. In this wave, the failed attempts that had
already passed their tests wasted **288 s measured** (CIU attempt 2: 255.6 s;
augeaswrap attempt 1: 28.2 s + 4.2 s). The other failed attempts stopped
before tests (CHANGES.md checks: CIU attempt 1, cli-extended attempt 1, pwmcp
attempt 1) or in a real test failure (cmru 7.2.5 attempt 1). Reuse is
therefore worth little until retries after green tests become common, and it
needs heavy prerequisites (immutable snapshot view, protected evidence root,
`DESIGN-verdict-reuse.md:591-604`). Duplicate removal saves on every release.

## 2. Features needed

### 2.1 Required for the rework

| ID | Tool | What | Why | Smallest implementation | Files / functions | Effort | Blocks |
|---|---|---|---|---|---|---|---|
| **AS-1** | Assay | R3 canary control taken from the lane's own baseline run | Every R3 lane runs one extra full suite only to re-prove the good tree passes; multi-target lanes pay it per target ("two full command runs" per target, `assay/src/assay/canary.py:957-1000` docstring). | Closed key `judge.canary.control = "separate" \| "baseline"` (default `"separate"`, so no lane changes silently). With `"baseline"`: when the baseline unit of the same `assay run` PASSed, left no `post_reason`, and its contention observation is complete, the canary skips its control materialization and records `control_source = "baseline"` in the attempt evidence. Otherwise it falls back to a separate control and says why. | `assay/src/assay/config.py` `_load_canary` (≈:766-806, :2820); `assay/src/assay/runner.py` baseline at :4583 → pass the baseline unit/judgement into the canary call; `assay/src/assay/canary.py` `run_isolated_canary` (:636; control block :766-810) and `run_isolated_canaries` (:957); verdict schema (`attempt.control_source`, schema bump); docs CONSUMERS R3 + DESIGN-GUIDE. | M (3–5 days, incl. one hand mutation per guard) | WP-CMRU-2, WP-ASSAY-1, CIU's Assay R3 option |

Trust argument for AS-1: the control exists to show that the materialized
good tree passes in this environment, so a failing transformed run is caused
by the transformation. The baseline was materialized from the same prepared
snapshot spec (`prepared.materialize`, runner.py:4583 vs canary.py:769), with
the same plan, environment and deadline, in the same process, moments
earlier. What is given up: a second materialization would detect a
nondeterministic materialization. That is accepted as by design and
documented.

No other feature is required. Everything else in §3 is configuration that
existing Run-Gate and Assay already support: `kind = "sequence"` (Run-Gate
R-45, `SPEC.md:624-637`), Assay `whole_target` R1, `accepts_args` focused
lanes, `--base` request bases.

### 2.2 Recommended, not blocking

| ID | Tool | What | Smallest implementation | Files | Effort |
|---|---|---|---|---|---|
| **RG-F1** | Run-Gate | `run-gate policy-check`: read-only audit of a project against the tiers | Extend `validate-pointers` (`run_gate.py:11174`, `cmd_validate_pointers`), so it ports to v8's `ciu check --gates` (CIU v8 proposal line 364). Derive tiers from what already exists, no tier key: **T1** = the `nyxloom-trove/nyxloom.toml` `[gates.*]` with `phase = "implementation"`; **T2** = the lanes `cmru.toml` `[steps.run-tests]` invokes (`run-gate <lane>` argv), expanded through sequences; **T3** = lanes whose Assay rigor contains R2, or named `r2`/`mutation`/`fuzz*`. Rules: P1 T2 contains no R2/fuzz lane; P2 T2 contains one R3 claim (Assay rigor R3 or a lane named `r3`/`canary`); P3 T1 contains no R2; P4 two leaf members of one T2 composite run the same test command (same `pytest <paths>` after dropping coverage flags) → warning; P5 command lanes that call `assay.cli run` pass `--resume --progress`; P6 a T2 composite is a `kind = "sequence"`. Exit 0 pass, 1 violation, 2 unreadable input. Accepted deviations: one optional table `[policy.accepted]` mapping a rule id to one reason string that names its decision reference (`P2 = "no R3 adopted yet; known issue, decision D-xx"`). No owner/date fields: git history and the review are the authority, and the checker cannot authenticate them anyway. | `run_gate.py` (config schema for `[policy]`, CLI subcommand, rules), `SPEC.md`, `CONSUMERS.md`, `LANE-AUTHORING.md`, tests | M (2–3 days) |

### 2.3 Evaluated and not recommended now

| Idea | Verdict | Reason |
|---|---|---|
| A single shared suite execution feeding Assay R0/R1 and the coverage claim | **No feature needed** | Assay `whole_target` already makes the one Assay run the whole-project floor (§1.3). |
| Canary only when judge inputs change / on a schedule | **Policy, not a feature** | Methodology rule 5: one R3 probe at T2; extra probes in T3. A path-triggered skip key would be one more hidden-skip hazard. |
| Tier-aware lane presets in code | **No** | Presets are names plus `kind = "sequence"`; policy-check derives tiers from existing pointers. |
| Read-free lane selection so reuse can hit | **Config, do it now** | Move prep readers (`cmru/tests/test_w3_prep_changelog_preflight.py`) into the live `release-preconditions` lane; no reuse code. |
| RG-253 verdict reuse | **Defer** | 0 s today; 288 s measured retry waste this wave; heavy prerequisites; needs a v8 home. |
| Diff-aware R1 | **Exists** | Assay `changed_lines`, run-gate `coverage_gate.py`, topos `coverage_gate.py`. |
| Diff-aware test *selection* | **T0 only** | R0 at T1/T2 is the project suite. Impact selection needs a provable import graph (methodology guideline 12); a `focused` lane with `accepts_args` already serves T0. |
| Parallel independent lanes | **Defer** | CMRU lanes share one reset gate snapshot (`run_release_gate.py:1294-1322`); small projects would save seconds; it interacts with v8 admission. |
| Assay multi-mechanism canaries in one lane (AS-2) | **Defer** | Only needed if a second probe stays at T2; rule 5 moves it to T3. |
| Prebuilt Assay image | **Rejected** | Same-repo consumers install Assay editable at lane time (review correction, `reports/gate-reuse-INVESTIGATION.md:217`). |

## 3. Per-project lane rework work packages

Every package is "audit and fix in one pass" for one project: lane config,
release pointer, trove pointer, adoption docs and docs-sync tests in the same
reviewed change, gated through the project's registered lane in
`tester-unified`, reviewed on the other route. Host rules for the jobs: one
gate container at a time per host, no cockpit pytest, no big local
processes (devcontainer memory cap); heavy runs go to the hosts.

### 3.1 Table

| WP | Project | Current lanes and time | Target by tier | Expected saving | Risk | Needs |
|---|---|---|---|---|---|---|
| **WP-DOC** | run-gate-project docs | `TEST-POLICY.md` draft (other branch); `LANE-AUTHORING.md` §3 table puts `mutation` inside `gate` (`LANE-AUTHORING.md:83-90`) | Apply §5 corrections to the draft; LANE-AUTHORING §3: `gate` excludes R2; link this design and the methodology section | — | Docs-sync tests (`tests/test_run_gate.py:1387-1522`) | — |
| **WP-CMRU-1** | cmru | T2 `gate-provisional` 2,989.8 s **measured**: ref-probe, installed-wheel-live, assay, coverage, canary | `[lanes.cmru]` → `whole_target` (targets list and its config test exactly as `cmru-first-release`); prep reader test moved from the suite lanes to `release-preconditions` (and `--ignore`d in the Assay argv); `run_release_gate.py` drops `coverage` from the T2 tuple (`:1523`) and moves `canary` to the post-release T3 run with `mutation`; new Assay lane `r0-r1` (R0, R1 only) as T1 | ~1,670 s **estimated** (751 + 919 inferred) → T2 ≈ 1,320 s | Junit-based failure naming (`run_release_gate.py:1023`) must read the Assay verdict tail instead; whole-target refuses excluded lines (`allow_excluded = false`); release-gate tests | — |
| **WP-CMRU-2** | cmru | after WP-CMRU-1 | `judge.canary.control = "baseline"` on `cmru`, `cmru-first-release` | one control suite per release, ~400–600 s **estimated** (split UNVERIFIED) → T2 ≈ 700–900 s | As AS-1 | AS-1 released |
| **WP-CMRU-3** | cmru | `gate` = R2-including conjunction, `gate-provisional` = T2 + KI-79 marker | `gate` := T2 content; R2 lane `mutation` (+ `canary`) is the T3 job; KI-79 windows retired (D4) | — (clarity) | Release-consumer manifest state `provisional` (`cmru/docs/SPEC.md:1986-2006`) must not block | D4 |
| **WP-ASSAY-1** | assay | T2: `tester-unified` 1,370.3 s + `self-qualification-provisional` 1,300.2 s **measured**; T1 trove pointer = `tester-unified` (`assay/nyxloom-trove/nyxloom.toml:178`, 5 h budget) | T1 = `self-qualification-preflight` (R0+R1 whole-target, no canary) or `tooling-tests` for tooling-only changes; T2 unchanged lanes with `control = "baseline"` on the provisional lane; T3 = `self-qualification` (R2), B110 pilots | ~300–500 s **estimated** at T2 (split UNVERIFIED); T1 time UNVERIFIED | Self-hosting: the judge under test judges itself | AS-1 |
| **WP-ASSAY-2** | assay | installed-wheel R0 runs the whole `tests` suite again | One instrumented run measures the phase split; then D5 | up to ~1,000 s **UNVERIFIED** | Packaging regressions that only some tests see | D5 |
| **WP-CIU** | ciu | T1 pointer = `ciu` (R0–R3 incl. R2, 8 h budget); T2 = `run-ciu-tests.py` 230.1 s + enroll 60.3 s via `cmru tester-gate` (`ciu/cmru.toml:77-86`), **no R3** | New command lane `r0-r1` wrapping `run-ciu-tests.py` (100% whole-project, xdist); trove T1 pointer → `r0-r1`; new targeted `r3` (seconds); T2 = sequence `gate` [`r0-r1`, `r3`, `enroll`] and `[steps.run-tests]` names `run-gate gate`; `ciu` (R2) and `ciu-gate` are T3 / on demand | T1: hours → ~4 min **estimated** (CIU R2 duration UNVERIFIED); T2 +R3 cost seconds | `enroll` needs the nested daemon (`--enable-docker`); keep it as its own step if the sequence cannot carry it | — |
| **WP-RG** | run-gate-project | T2 `selftest` 299.2 s **measured**, no R3 (`run-gate-project/cmru.toml:62-88`); `gate-full` = `selftest && assay-r1 && assay-r3` | T2 = sequence [`selftest`, `assay-r3`] (targeted canary, `tools/canary-run.sh`); `gate-full` → that sequence (drop `assay-r1`, a second full suite); `assay-r2` T3 | one suite (~250–300 s **estimated**, selftest as proxy) whenever `gate-full` runs; release +R3 cost UNVERIFIED (canary selectors, small) | The release-pointer rationale comment (`cmru.toml:71-82`) must be rewritten | — |
| **WP-SMALL** | cli-extended, augeaswrap | `r0-r1` runs the suite twice (default + `--capture=sys`): 82.4 s / 65.6 s **measured** | `r0-r1` = one run; new T3 lane `r0-capture-sys`; `gate` → sequence [`r0-r1`, `r3`] | ~35–40 s each **estimated** | Reverts F1's per-release check to T3; reviewer confirms | D2 |
| **WP-SEQ** | test-policy, iobench, cgprofile, damon-analysis, debian-install-v2, gstammtisch-guide, iogovmon | `gate` = `bash -c` conjunction incl. `r2` (iogovmon: `r0-r1` only) | `gate` → `kind = "sequence"` without `r2`; release pointers unchanged | — (correctness) | none | — |
| **WP-R3** | iobench, pwmcp, topos, nyxloom, iogovmon | release has no R3 probe | Add a targeted `r3` canary (cgprofile/cli-extended pattern); topos: release step → `run-gate topos-suite` instead of the inline `cmru tester-gate -- bash -c ...` argv (`topos/cmru.toml:29`); nyxloom: verify its release R1 (`base = "main"`, `nyxloom/assay.toml:57-68`) is not vacuous on a tagged main HEAD (UNVERIFIED) | +seconds each | A canary target that moves must refuse ("target moved", as cli-extended does) | — |
| **WP-MAP** | mdt-cockpit, truenas/tnt, scripts/truenas, wings-patchstack, shared-ramdisk-depot-manager, cowtree, worktree, all class-D projects | as inventoried | Adoption-doc tier mapping + known issues (no R3 / no R2 adopted); mdt-cockpit `assay-full` (R0–R3 incl. R2) = T3; tnt `r2` (25 h) = T3; live lanes = T2 only where the release names them | — | Many small edits; one job per 4–6 projects | — |
| **WP-HOSTSAT** | release program queue | `host-sat-queue.sh` `LANES` (10 entries) | Document as the T3 repetition tier; update entries after the renames | — | — | after the renames |
| **WP-PC** | run-gate | — | Implement RG-F1; run it over all 36 projects as the closing audit | — | New `[policy]` table needs a v8 table-H row | D7 |
| **WP-AS1** | assay | — | Implement AS-1 | enables WP-CMRU-2, WP-ASSAY-1 | Schema bump; self-hosting qualification | D3 |

### 3.2 Order and parallelism

1. **Now, in parallel** (independent projects, no feature needed): WP-DOC,
   WP-CMRU-1, WP-CIU, WP-RG, WP-SEQ, WP-R3, WP-MAP, WP-AS1, and Rust R-0
   (§4). WP-SMALL waits only for D2.
2. **After AS-1 is released**: WP-CMRU-2, WP-ASSAY-1.
3. **After the operator decisions**: WP-CMRU-3 (D4), WP-ASSAY-2 (D5),
   WP-PC (D7).
4. **Last**: WP-PC's estate run and WP-HOSTSAT, so the audit sees the final
   lanes.

Conflicts to avoid: WP-CMRU-1 and WP-CMRU-3 both edit
`cmru/tools/run_release_gate.py`. Run them in sequence, or as one package if
D4 is decided first. WP-RG and WP-PC both edit run-gate-project: WP-RG
touches only lane config and docs, WP-PC only code.

### 3.3 Projected T2 for the two large projects

| Project | Today (measured) | After config-only WPs (estimated) | After AS-1 (estimated) |
|---|---:|---:|---:|
| cmru | 2,989.8 s | ≈ 1,320 s | ≈ 700–900 s |
| assay | 2,670.5 s | unchanged | ≈ 2,170–2,370 s; less after D5 (UNVERIFIED) |

## 4. Rust

### 4.1 Current state (verified)

- **Assay has no Rust support.** The language matrix lists Python,
  JavaScript/TypeScript, Go and SQL (`assay/README.md:544-550`); adapters are
  `base`, `go`, `javascript`, `python`, `sql` (`assay/src/assay/adapters/`);
  a search of `assay/src/assay` for `rust`/`cargo` finds only the word
  "trusted". The lcov parser exists (`coverage_parsers/lcov.py`), and
  DESIGN-GUIDE §11 names Rust as an lcov producer (`docs/DESIGN-GUIDE.md:3275`).
  The backlog mentions `cargo` only in passing (`nyxloom-trove/4-backlog.md:4631`).
  R0 works today for any command (`README.md:506`).
- **Estate Rust code:** none on `main` except
  `legacy-experiments/voice2text-ai/tauri-client/src-tauri` (no gate). The
  topos sampler crate (D-026) and a ratatui PoC exist only on unmerged
  branches (`topos-sampler-poc-20261105`, `topos-ratatui-poc-20261105`,
  `topos-singleview-20261105`). Their `topos-sampler` lane runs `cargo test`
  inside a `docker build` and is marked UNVERIFIED by its own comment (needs
  the Docker socket; TOP-SAMPLER-GATE). No `hostobs/` project exists on
  `main`.
- **Binding design and decisions** already exist outside the repo:
  `reports/assay-RUST-ADAPTER-DESIGN.md` (pure-Python `RustAdapter` like the
  JS one; R1 via cargo-llvm-cov lcov through the existing parser; R2 by
  ingesting cargo-mutants `outcomes.json`; R3 with a panicking
  `#[cfg(test)]` module as import break; sibling image `tester-unified-rust`;
  WPs R-0..R-6, milestones 1–3, known issues K1–K14) and the operator's two
  2026-11-08 Rust blocks in `reports/hostobs-DECISIONS-20261107.md` (the
  second amends the first).

### 4.2 Rust by tier

| Tier | Lanes | Content |
|---|---|---|
| T0 | `lint`, `focused` | `cargo fmt --all --check`; `cargo clippy --workspace --all-targets --locked --offline -- -D warnings`; `cargo deny --offline check`; `cargo nextest run --offline --locked -E <filter>` (`accepts_args`). |
| T1 | sequence [`lint`, `assay-r1`] | nextest under `cargo llvm-cov` (lcov) + `cargo test --doc`; Assay judges 100% changed lines (rigor R0, R1); the lane step checks per-crate floors **90% line / 85% region**; branch coverage on stable is a known issue (K2). |
| T2 | sequence `gate` [`lint`, `assay-r3`, `conformance`] | `assay-r3` is the **one** R0/R1/R3 lane (test-break canary). It replaces `assay-r1` at T2, so the instrumented build and suite run once. |
| T3 | `assay-r2`, `mutants-<crate>-<k>of<n>`, `r3-uncovered`, later `fuzz` | Diff-scoped cargo-mutants with base = previous release tag (when R2 is adopted; waived now); advisory whole-crate shards on the hosts; the uncovered-line probe; `cargo fuzz` later (BL-FUZZ); miri/sanitizers, semver-checks, benchmarks are backlog. |

### 4.3 Where the Rust design conflicts with the tier policy: exact changes

Change these in `assay-RUST-ADAPTER-DESIGN.md` (or in its persisted copy):

1. **§E lane table, `assay-r2`:** "Pre-merge, not in `gate-full`" → "T3:
   asynchronous on the latest release, `--base <previous release tag>`; never
   a merge or release gate; survivors become backlog items." The end-state
   claim stays "diff-scoped 100% killed" (D8).
2. **§E `gate-full = lint && assay-r1 && assay-r3 && conformance`** →
   `gate`, `kind = "sequence"`, members [`lint`, `assay-r3`, `conformance`].
   `assay-r1` stays the T1 lane only; it is not a T2 member.
3. **§E `assay-r3` "both canaries on one declared target per core crate"** →
   Assay declares one `mechanism` per lane (`config.py` canary loader), so
   T2 uses the test-break (import-break) canary. The uncovered-line probe
   is a separate T3 lane (methodology rule 5).
4. **§E "Vendoring", §E "Verification against Cargo.lock", §E "Offline"
   source replacement, §F items 3 and 5, WP R-1's "filtered `vendor/`", R-0's
   spike S1, the §I question 1, and danger (1) of the preamble** → superseded
   by the second 2026-11-08 block: no `vendor/` in git. `tester-unified-rust`
   carries a crate cache that CMRU builds with network (`cargo fetch
   --locked`); lanes run `--offline --locked` with a precheck that fails
   loudly naming the missing crate. Spike S1 becomes "crate-cache size, and a
   negative test: an image/lock mismatch fails the precheck by crate name".
   R-5 becomes "`.crates` source + lock writer + image rebuild trigger", with
   no vendor filter or tripwire. Keep `[net] offline = true`; drop the
   `[source.*]` replacement lines.
5. **§I question 2 floors** → decided: 90% line / 85% region per crate, for
   every crate, plus Assay's 100% changed-line rule.
6. **§G R-6 "release-time"** → T3, advisory, sharded on r1002/v1001.
7. **Per-release R2 waiver** (decision block 2) → not needed under the tiers:
   R2 never gates a release. Record once, as a known issue, "Rust R2 not yet
   adopted (R-3b pending)".

No change is needed for the adapter mapping (§A), R0 (§B), R1 (§C), the
cargo-mutants parsing contract (§D), the image (sibling, stable only, no MSRV
lane), or the known-issue table (§H).

### 4.4 Rust work packages, independence and parallelism

| WP | Content (from the Rust design, amended by §4.3) | Needs | Can start |
|---|---|---|---|
| R-0 | Spikes S2 (llvm-cov facts), S5 (image size, python3), S1' (crate-cache size + mismatch precheck) in a disposable container **on a host**, not the cockpit | — | **now** |
| R-3a | Spikes S3/S4/S7 (cargo-mutants spans, `--in-diff`, baseline failure, schema) in a disposable `rust:*-slim` container | — (the spike does not need the final image) | now, low priority (R2 waived) |
| R-1 | `tester-unified-rust` with crate cache; hostobs workspace skeleton from the sampler; `lint` + `selftest` lanes; nextest/deny/floor configs (**milestone 1**) | R-0 | after R-0 |
| R-2 | Assay `RustAdapter` at R1, failure recognizers, real-toolchain fixtures, `assay-r1` (**milestone 2**) | R-1 | after R-1, parallel with R-5 |
| R-5 | CMRU `.crates` source, lock writer, image rebuild, advisories | R-1 | after R-1, parallel with R-2 |
| R-4 | Canary injectors + oracles; `assay-r3`; T2 `gate` sequence | R-2 | after R-2 |
| R-3b | cargo-mutants ingestion + `result_report` + `ASSAY_CHANGED_DIFF` (**milestone 3**, T3) | R-2, R-3a | deferred while R2 is waived |
| R-6 | Advisory whole-crate shards on hosts (T3) | R-3b | deferred |

Effort follows the Rust design's sizes (R-0 S, R-1 M, R-2 M, R-3b L, R-4
S–M, R-5 M–L, R-6 S). Milestones 1 and 2 give Rust T0/T1; R-4 adds T2.

## 5. Corrections for the draft `run-gate-project/TEST-POLICY.md`

The draft on branch `test-policy-doc-20261109` (head `b8c00b622`) is broadly
right but over-built in places and stale in others. Change:

1. **Tier mapping and deviations (`TEST-POLICY.md:87-121`)**: replace
   `[policy.tiers]` and the six-field `[policy.deviations.*]` (owner,
   accepted_by, accepted_on, evidence) with tiers derived from existing
   pointers (RG-F1) and one `[policy.accepted]` rule-id → reason map. The
   draft itself says the checker cannot authenticate the reviewer (`:118-121`),
   so the extra fields add ceremony without checking anything.
2. **Presets (`:48-78`)**: drop `gate-provisional` as an estate preset; it is
   a CMRU name. Presets are `focused`, `r0-r1`, `r3`, `gate` (sequence), `r2`,
   `fuzz`; `gate` must be a `kind = "sequence"`.
3. **Duplicate work (`:137-146`)**: "keep both required gates and record the
   duplicate cost" → retire CMRU's separate coverage lane at T2 by switching
   the Assay lane to `whole_target`, which `cmru-first-release` already
   proves; no RG-253 dependency.
4. **Second canary (`:147-151`)**: "retain that canary [at T2]" → it is a
   second probe of the instrument and moves to T3 (methodology rule 5,
   decision D2).
5. **RG-253 status (`:152-166`)**: "in-review design on branch
   `gate-reuse-design-20261109`" → merged as a design (`5cd92d24a`), not
   implemented, v1 saves 0 s on today's CMRU layout.
6. **CIU row (`:342`)**: "`ciu-gate` maps T2" is wrong. CIU's release runs
   `run-ciu-tests.py` through `cmru tester-gate` (`ciu/cmru.toml:77-79`), with
   no R3. Its trove pre-merge pointer runs the R2 lane `ciu`
   (`ciu/nyxloom-trove/nyxloom.toml:58`). Both are WP-CIU findings.
7. **Measured table (`:391-404`)**: CIU, augeaswrap, cli-extended and iobench
   are measured (§1.1), not UNVERIFIED.
8. **Audit map (`:339-350`)**: covers 10 of 36 projects; add the rest by
   class (§1.2) and run-gate's `gate-full` duplicate.
9. **Fuzz declaration (`:177-192`)**: shorten to target, corpus (tracked seeds
   vs ignored output under `.assay/fuzz/`), budget; the six-field list and
   "triage owner" are one more record to maintain.
10. **policy-check output (`:266-294`)**: nine columns → one line per rule
    result (`rule`, `status`, `lanes`, `detail`), plus `--format json`.
11. **Open decisions (`:411-432`)**: questions 1, 2, 4 and 6 are answered by
    the methodology section (T2 R1 scope rule 4; RG-253 deferred; second
    canary → T3; fuzz is per-project opt-in). Questions 3, 5 and 7 fold into
    D6, D7 and the R4 rule.

## 6. Decisions for the operator

| # | Decision | Recommended default |
|---|---|---|
| D1 | May Assay's `whole_target` R1 (100% line+branch, every module a target, no exclusions) replace CMRU's separate `coverage` lane as the whole-project floor at T2? | **Yes.** `cmru-first-release` already does it; saves ~751 s (inferred) per release. |
| D2 | Second probes of one instrument go to T3: CMRU's uncovered-line `canary` (~919 s inferred), the `--capture=sys` second runs in cli-extended/augeaswrap, Rust's uncovered-line probe. T2 keeps one R3 probe. | **Yes.** |
| D3 | Build Assay AS-1 (canary control = the lane's own baseline, opt-in per lane)? | **Yes**, M effort; the largest remaining saving for CMRU, Assay and any Assay R3 lane. |
| D4 | `gate` means T2 everywhere: no lane named `gate` contains R2; CMRU's R2-including `gate` is retired and the KI-79 postponement becomes the normal T3 path. | **Yes.** |
| D5 | Assay self-release: may the installed-wheel R0 (`tester-unified`) run a packaging-focused subset, since the source qualification runs `tests` with R1/R3? | **Measure the phase split first** (one instrumented run), then decide; keep both until then. |
| D6 | RG-253 verdict reuse: implement now or defer? | **Defer.** Do the prep-reader split now (free); revisit when retries after green tests recur (288 s this wave). |
| D7 | `run-gate policy-check` with tiers derived from existing pointers and one `[policy.accepted]` rule-id → reason table (no owner/date fields). | **Yes**, after the config packages land. |
| D8 | Rust R2: diff-scoped cargo-mutants at T3 against the previous release tag, not a pre-merge gate (same as Python). | **Yes**; amends the Rust design §E as in §4.3. |

## 7. Verification boundary

Verified by reading files and logs: every release time in §1.1; the CMRU lane
order, commands and the `cmru-first-release` whole-target lane; Assay's
single-mechanism canary config, its control materialization and per-target
control cost; the absence of Rust in Assay; the Rust code only on PoC
branches; CIU's release and trove pointers; run-gate's `gate-full`; the
`--capture=sys` duplicate and its origin commit; the 36-project inventory.

UNVERIFIED: the CMRU Assay lane's baseline/control/transform split (so
WP-CMRU-2's saving is a range); Assay's phase split (WP-ASSAY-1/2); CIU's R2
campaign duration; run-gate's `assay-r3` duration; whether nyxloom's release
R1 with `base = "main"` is vacuous on a tagged main HEAD; whether CMRU's
`base = "cmru-v6.1.0"` pin is intentional; whether WP-CMRU-1's whole-target
switch changes the Assay lane's own time. All per-lane CMRU times are
inferred, not instrumented.
