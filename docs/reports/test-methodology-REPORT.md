<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/test-methodology-REPORT.md -->

# Test methodology (T0-T3 tiers) + lane rework design: REPORT

Status: **REVISED (rev 4)** after re-review 3 (`reports/test-methodology-REVIEW3.md`, FIX-FIRST, N7–N8 + S11–S15 + nits). Rev 3 followed re-review 2 (`reports/test-methodology-REVIEW2.md`) and the operator's round-3 decisions. Final head `ae5c6691a`, selftest **PASS** (contamination-flagged, disclosed below).

## What changed in rev 4

| Finding | Change | Where |
|---|---|---|
| **N7** resume on the same host cannot work: `remote-gate run` deletes its throwaway checkout at job exit (`remote-gate.py:447-451, 484-499`), and every resume store lives inside it | **Chosen: option (a)**. R2 and fuzz entries launch `run-job t3-… -- bash run-r2-campaign.sh <name> <commit> <project_dir> <lane> [--base]` in the persistent `$RUNNER_HOME/checkouts/<commit>`, imported by `sync-from-devcontainer` from a bundle that carries the release tags. Why it is simpler: no new code path. The supervisor already relaunches while the lane exits 4 and `.assay/progress-*.jsonl` grows, stops after 2 stalls or `R2_MAX_ATTEMPTS`, and writes `final-exit`/`campaign-summary.json`/`verdict.json`. Both R2 command lanes (cli-extended, cmru) write the progress file it counts. Option (b) would add a keep/reuse branch to remote-gate's job script. `probe`/`variation` keep `remote-gate run`. `RESUME` now means re-supervision after a host or job restart. The acceptance test checks that progress continues from the last candidate. The CMRU `r2` row is enabled only after the campaign path works. WP-CMRU part 5's rehearsal includes one budget exit and resume, and checks that the tags reach the checkout. That tag transfer is UNVERIFIED. | D §3.4 (new "why" paragraph, loop steps 2–6), §3.5, WP-T3, part 5, D13; M "Long campaigns"; B header |
| **N8** count admission is cited as the safety net but is not enabled; the gates-slice admission never refuses and does not run on hosts (`RUN_GATE_PROFILE=off`) | The real guards are named: `remote-gate`'s start-time PSI refusal, the slice `MemoryMax`, and the per-host flock for queue launches. Count admission (`[admission]` + `run-gate admission set --max-concurrent N` + `admission_check`) is named as existing but not enabled. Known issue **LR-KI-10**: a manual run can overlap a queue run. Rule: manual T3 work is queued as an entry. Enabling count admission is the one-table follow-up if overlaps are seen. | M Distribution; D §1.5 row, §3.2 capacity row, §3.3, §3.4 claim; B header |
| **S11** enqueue-vs-claim base | The base is computed at the entry's **first claim**, stored in the entry, and reused by relaunches. The ledger's `base` is that value. | D §3.4, Q2 |
| **S12** window after KI-141 | The closing change makes the window open-ended from 7.3.0 (edits the `low <= v < below` test and its oracles). A later boundary needs its own declared precondition. | D part 0 |
| **S13** footprints empty (profiling off on hosts) | Placement uses committed manifests, else declared `resources.memory`. `peak_mib` stays empty: **LR-KI-11**. History copy-back is dropped from the first step. | D §3.4, D13, host-pipeline agreement |
| **S14** `run-gate --list` lacks budget/memory | Both are read from the lane declaration (lane → environment → central), resolved like `remote-gate.py` `lane_checks` (`:322-345`). | D §3.4 |
| **S15** strace not in the image; host strace sees only the docker client | The fsync count is dropped; write bytes + io PSI remain. `perf stat -G <lane cgroup>` as root is documented as the feasible method. The A/B runs where profiling is on. This departs from your instruction (f), which asked for an fsync count. | D §9 A/B, D14 |
| n1 (REVIEW2 n12) | The R4 lines are rewrapped (no prose line over 100 chars in the section). | M R4 |
| n2 | "WP-DOC (§5)" → "(§4)" | D:16 |
| n3 | `host-sat-queue.sh:30` → `POLL_S=60` at `:28` | D §1.5 |
| n4 | r1002 image label verified by image ID (was UNVERIFIED) | D §9, §10 |
| n5 | truenas redeclares `tester-unified`; relaxed does not reach it; harmless | D §9 |
| n6 | `--base` arrives as a SHA; CMRU accepts a commit a published tag points to and logs the tag | D §3.4 |
| n7 | `commit` (peeled tag) and `image` are added to the entry and the ledger (commit-addressed per M "Synchronous versus retroactive runs") | D §3.4; M Results |
| n8 | INFRA retry = delete the entry file after the attention item; no automatic retry | D §3.4 |
| n9 | The report's gate evidence cites the run record's `commit` field; no copy exists under `logs/` | this report |
| Coordinator: HP-D2..D4, D6..D8, D10 accepted | HP-D10 is recorded as accepted in the host-pipeline agreement; profiling stays off on host launches (LR-KI-11) | D §3.4 |

## Rev 3 summary (previous revision)

| | |
|---|---|
| Branch | `test-methodology-20261110` |
| Worktree | `<local-path>` |
| Base | origin/main `8f18a8669` |
| State | not pushed, merged, tagged or released; no other worktree touched |

## What changed in rev 3

1. `nyxloom/reference/TESTING-METHODOLOGY.md`, section `## Test tiers by event (T0–T3)`:
   - **Rule 1:** a version-boundary precondition is checked by the release gate itself (CMRU: the version-window check, also read by `cmru release --dry-run`), and is lifted only by a reviewed change that cites a T3 ledger PASS row.
   - **Rule 2:** for most projects the "R2 owed" record is the T3 queue entry and its ledger row; CMRU also writes a marker.
   - **Rule 6:** a pytest-cov whole-project floor asserts a zero-cost coverage inventory on its own report instead of a second canary run (Q5).
   - **T3 subsection:**
     - changed-line R2 base = the newest tag with a recorded R2 PASS;
     - one pull-based loop per host, with T3 first;
     - budget timeouts, resume pinned to the host;
     - FAIL attention clears when a backlog id is recorded;
     - the **entry format + ledger are the stable interface**;
     - an explicit, dated deviation from the 2026-09-02 Buildkite decision.
   - **Presets:** `r3` needs two Assay lanes for two canaries (AS-2 deferred).
   - **CMRU mapping:**
     - `gate` = the orchestrator without mutation and always writes the marker; `gate-provisional` is removed or kept as an alias;
     - `r2` = a container lane from local tags, rehearsed on a host;
     - KI-141 is enforced by the version window until a T3 PASS is recorded.
   - **Prep-only paths:** the small live lane must itself be named in the release step and in T1.
   - **Rust T3 base:** the last R2-covered tag.
   - **Environment levers:** relaxed I/O is set per eatmydata-labelled ephemeral environment. The page-cache saving is bounded by what the tests read (ZFS clones share the ARC). Measure first.
   - Nits: rule cross-reference, the draft-branch disposition, one over-long line.
2. `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, rev 3:
   - §2.2: cmru `r2` as a container lane;
   - AS-2 and RG-253-lite deferred;
   - §3.3: decision (a) with the D-110.4 deviation;
   - §3.4 rewritten:
     - entry files;
     - the base rule;
     - controller-side `ls-remote` publish check;
     - per-host claim against live slice headroom, with the lane's `resources.memory` as the default footprint;
     - main's `remote-gate.py`;
     - budget timeout;
     - `history.json` collection and `peak_mib` from the run record;
     - `RESUME` pinning;
     - ledger with `base` and `backlog_id`;
   - §4: WP-CMRU parts 0–9;
   - WP-ASSAY gets the wheel-contents check;
   - WP-ENV owns `run-gate.root.toml`;
   - sequenced sets and accepted deviations;
   - §6 adds draft corrections 16–17 and the branch disposition;
   - §7 adds the round-3 decisions D9–D17;
   - §8 keeps the window, adds the snapshot-guard row and the LR-KI-1/2 relabels, and defers RG-253-lite with its constraints;
   - §9: W0 at environment scope with an strace fsync count; W8 corrected;
   - §10 updated.
3. `run-gate-project/REMOTE-LANES-BUILDKITE.md`: a dated **"Superseded for T3, 2026-11-10"** header. T3 runs on the per-host queue and `remote-gate`, and Buildkite stays the scale-out path. Cross-trigger safety comes from on-host admission. The rest of the file is unchanged.

## Commits

All on branch `test-methodology-20261110`, each with trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`:

- `0a606b192` docs(testing): add T0-T3 test tiers and the lane rework design (rev 1).
- `1db3b353b` docs(testing): revise tier methodology and lane rework after review (rev 2).
- `866b6f376` docs(testing): add eatmydata and page-cache sharing to the cheap wins (operator follow-up).
- `99cfc29e2` docs(test-tiers): rev 3 after re-review 2 (N1-N6, S1-S10, nits) and operator round 3.
- `ae5c6691a` docs(test-tiers): rev 4 after re-review 3 (N7, N8, S11-S15, nits).

## Gate verdicts (lane, head, verdict)

`run-gate-project` `selftest` is the lane that holds the TESTING-METHODOLOGY docs-sync check (`tests/test_run_gate.py:7713-7718`). Every run was invoked as `flock .../gate.lock nice -n 10 run-gate --worktree <wt> selftest`.

| Head | Run | Result |
|---|---|---|
| `0a606b192` | 1 | **FAIL**: 1 failed, 3,079 passed in 338.81 s. Unrelated flake `TestRg109InterruptedClient::test_a_sigterm_during_the_wait_removes_the_container_and_clears_the_record` (60 s timeout; the run was memory-stall contaminated). It is now RG-255 (`1e3fbcba7`, unmerged). |
| `0a606b192` | 2 | **PASS**: 3,080 passed in 273.09 s. |
| `1db3b353b` (rev 2) | 1 | **PASS**: 3,080 passed, 1 skipped in 263.14 s. |
| `866b6f376` | 1 | **PASS**: 3,080 passed, 1 skipped in 253.86 s. The run record is **contamination-flagged** (2.3 s memory stall; disclosed per re-review n8). The PASS stands. |
| `99cfc29e2d69a7069ea544bcd3721707bcb72484` (rev 3; the run record `run-gate-project/.run-gate/history.json` → `lanes.selftest.latest.commit` matches, `dirty` false; the docker argv label `run-gate.commit=99cfc29e2…` appears in the run-gate stdout, not in the 4 KiB log tail; no copy under `logs/`) | 1 | **PASS**: 3,080 passed, 1 skipped in 268.62 s; diff-coverage SKIPPED (no executable line changed); exit 0. Log: `/tmp/run-gate/lanes/selftest/6e4422ee267ae63ed485a203a975594a.log`. The footprint measurement is **contamination-flagged** (4.3 s memory stall not explained by the lane's own limit), so it is excluded from sizing. The functional PASS stands. run-gate 25.2.1. |
| `ae5c6691ac636efdbfbff325a8577bc88cd98c51` (**final**, rev 4; run record `lanes.selftest.latest`: `commit` matches, outcome pass, `dirty` false; docker argv label `run-gate.commit=ae5c6691a…` in the run-gate stdout; no copy under `logs/`) | 1 | **PASS**: 3,080 passed, 1 skipped in 262.45 s; exit 0. Log: `/tmp/run-gate/lanes/selftest/f11316f621ffd7dc48ee998f86db4f2e.log`. The footprint is **contamination-flagged** (4.5 s memory stall), so it is excluded from sizing; the functional PASS stands. |

## Re-review 2 findings → what changed (rev 3)

| Finding / decision | Change | Where |
|---|---|---|
| **N1** KI-141 loses its enforcement | The version window stays `((7,2,0),(7,3,0))` until KI-141 closes (D11). WP-CMRU part 0 rewrites KI-141 "Fix" and "Close when": a T3 ledger PASS row of cmru `r2` with base ≤ `cmru-v6.1.0`, survivors closed or justified, and the reviewed window change citing it. No `gate` lane is named. Enforcement is named as the gate's version-window check, also read by `cmru release --dry-run`. The marker's `follow_up_lane` changes `gate` → `r2`. The text is in the design, not edited into the cmru backlog, because it names a lane that WP-CMRU creates; part 0 lands it first. | M rule 1 + CMRU mapping; D §4 part 0/6, §7 D11, §8.1, §8.2 |
| **N2** supersede + "previous tag" leaves deltas without R2 | Changed-line T3 R2 base = the newest tag with a PASS ledger row for that lane, else the row's `floor_tag` (D10). It is passed as `--base` or as CMRU's explicit base input. `whole_target` lanes are unaffected. D8 wording is amended. | M T3 subsection, Rust T3 row; D §3.4, §5.2–5.3, §7 |
| **N3** `--mutation-only` cannot run on a host | cmru `r2` = a container lane in `cmru-mutation` with facts from local tags (`local_baseline_facts.py` semantics), an explicit base and no `required_env`. "Published" is checked on the controller by `ls-remote`. One real host `r2` rehearsal runs before `gate` is retired (D12). | M CMRU mapping; D §2.2, §4 part 5 |
| **N4** queue cannot carry multi-hour resumable campaigns | Timeout = the lane budget + a margin. Exit 4 with progress → `RESUME`, pinned to the host. Without progress → INFRA. Footprint history is copied back (D13). | M T3 subsection; D §3.4 |
| **N5** W0 root default refuses non-ephemeral lanes | `io_sync = "relaxed"` goes in `[environments.tester-unified.resources]` (WP-ENV) and in `[environments.cmru-mutation.resources]` (WP-CMRU part 9); non-ephemeral environments stay strict. Precondition: the image label (UNVERIFIED for the r1002 override). The A/B counts fsyncs with `strace -f -c` (D14). | M environment levers; D §9 W0, WP-ENV/WP-IO |
| **N6** option (a) contradicts D-110.4 silently | Now stated as an explicit, dated deviation (D9). `REMOTE-LANES-BUILDKITE.md` header added. Cross-trigger safety comes from on-host admission. The stable interface is named. | D §3.3; Buildkite doc header; M T3 subsection |
| **S1** placement counts only the queue's own runs | Each host claims against live `memory.max − memory.current` of the gates slice. The default footprint is the lane's `resources.memory`. Peak includes page cache, so the estimate is conservative. On-host admission still decides. | D §3.4 |
| **S2** footprint feedback has no input | `remote-gate collect` adds `.run-gate/history.json`. `peak_mib` comes from `profile_summary.lane_cgroup.memory_peak_bytes`. | D §3.4 |
| **S3** moved CHANGES reader runs nowhere | WP-CMRU part 4 adds `release-preconditions` to `_run_locked_gate` (first) and to CMRU's T1 mapping. Methodology prep-only rule 2 requires the small lane to be named. | D §4 part 4, §8.1; M reuse rule 2 |
| **S4** LR-KI-2 misclassified | Reclassified (a). The guard is kept cheaply: the wheel file list is compared with `git ls-files assay/src`, and every module is imported from the wheel. | D §4 WP-ASSAY, §8.2 |
| **S5** W8 page cache ~10x too high | Measured at this head: tracked tree 132.3 MB; CMRU working set 5.6 MB (9.1 MB with `assay/src`; the reviewer quoted ≈ 12.7 MB). Upper bound ≈ 10–20 MB duplicate clean, reclaimable cache. W8 is not recommended until measured. ZFS ARC is noted. | D §9 W8; M environment levers |
| **S6** RG-253-lite underspecified | Deferred (D17), with constraints: Assay lanes only; the store sits outside per-attempt release worktrees, keyed by digest; vetoes as in methodology reuse rule 3; atomic write; LR-KI-6 is pre-existing. | D §2.3, §8.2 |
| **S7** parallel set not disjoint | WP-ENV is the sole owner of `run-gate.root.toml`. CMRU and Assay's W1–W3 are folded into WP-CMRU and WP-ASSAY. WP-DOC → WP-RG and WP-ASSAY → WP-B178 are sequenced. WP-DOC's test goes in a new file. Accepted deviations from "one pass per project" are stated. | D §4.1–4.2 |
| **S8** "`gate` means T2 everywhere" | CMRU `gate` = the orchestrator without mutation and always writes the marker; `gate-provisional` is removed or kept as an alias. `cmru-first-release`/`assay-first-release` and the first-release branch are deleted. | D §4 parts 6–7, §8.1; M CMRU mapping |
| **S9** B178 guard wording, verifier | "R0 PASS and, where declared, R1 PASS". `assay verify` is in WP-B178's scope. Correction: B180 (the reviewer's id) is a different backlog item, so the scope is named instead. | D §2.1 |
| **S10** queue interplay | T3 and the sat rotation share one per-host loop and lock, with T3 first. The loop uses main's `remote-gate.py` (today it copies the lane worktree's, `host-sat-queue.sh:145`). | D §3.4; M T3 subsection |
| n1 | "(rule 5)" → "(rule 6)" | M T2 row |
| n2 | Draft branch `test-policy-doc-20261109` (`b8c00b622`) is folded by WP-DOC, then closed **unmerged** and its worktree removed. Its `TEST-POLICY.md` pointers (verified at `CONSUMERS.md:11-14`) must not land. | M header; D §6 intro, WP-DOC |
| n3 | Draft corrections 16–17 (`:21`, `:57`, `:242/:256/:277/:283`, verified) | D §6 |
| n4 | Only CMRU has a marker; elsewhere the entry and its ledger row are the record. | M rule 2 |
| n5 | Two canaries need two lanes until AS-2 | M `r3` preset |
| n6 | The host-sat medians are queue wall times (60 s poll). | D §1.5 |
| n7 | A FAIL attention clears when a backlog id is recorded. | M, D §3.4 |
| n8 | The `866b6f376` contamination flag is disclosed. | this report |
| n9 | LR-KI-1 is labelled as the operator-D3 exception. | D §8.2 |
| n10 | The probe reads only `MemAvailable` today (`host-sat-queue.sh:63-71`); it is extended. | D §3.4 |
| n11 | CMRU gate-snapshot guards (~70 s, mostly (b)) are listed as a later candidate. | D §8.2 |
| n12 | The over-long line is rewrapped. | M R4 |
| AGENTS.md:197 CPU-PSI wording | Noted as a WP-DOC item | D WP-DOC |
| Operator (g) Q3 / Q5 / Q8 | D15 / D16 / D17 | D §7 |

Rev-2 changes against the first review (B1–B7, nits 1–15) are unchanged from the previous report revision. The re-review confirmed them closed (REVIEW2 §1).

## Work-package table (rev 3)

| WP | Scope | Saving (label) | Needs | When |
|---|---|---|---|---|
| WP-DOC | run-gate `CONSUMERS.md` + `LANE-AUTHORING.md` (fold the draft), `REMOTE-LANES-BUILDKITE.md` §1, docs-sync test in a new file, then close the draft branch unmerged | — | — | now; before WP-RG |
| WP-CMRU | parts 0–9: KI-141 rewrite (window kept); `whole_target`; explicit R2 base; retire coverage + canary; prep reader → `release-preconditions`, which joins the release gate; container `r2` + one host rehearsal, then retire `gate`; `gate` = T2 + marker; delete the first-release lanes; `r0-r1`; relaxed I/O on `cmru-mutation`, W1–W3. Step 2: verify after B178 | ≈ 1,670 s (estimated) → ≈ 1,320 s; after B178 ≈ 700–800 s | rehearsal; host `r2` run; B178 | now |
| WP-ASSAY | narrowed installed-wheel R0 + wheel-contents/import-all check; T1 lanes; `r2` T3; W1–W3; owns the CONSUMERS/CHANGES edits | up to ~1,000 s (UNVERIFIED) + ~300–500 s after B178 | B178 for step 4 | now; before WP-B178 |
| WP-CIU, WP-CLIX, WP-AUG, WP-IOBENCH, WP-TESTPOLICY, WP-IOGOVMON, WP-SEQ4, WP-TOPOS, WP-PWMCP, WP-NYXLOOM (two canary lanes), WP-MDT, WP-MAP-* | as design §4; pytest-cov floor lanes add the coverage-inventory assertion | as design | — | now |
| WP-RG | widen selftest sources, drop `assay-r1`, T2 [`selftest`, `assay-r3`], uncovered-line | ~250–300 s per `gate-full` | after WP-DOC | sequenced |
| WP-B178 | Assay code (automatic) + `assay verify` | one suite per R3 lane | after WP-ASSAY | sequenced |
| WP-T3 | per-host T3 mode; campaigns via `run-job … run-r2-campaign.sh` in the persistent checkout (rev 4); first lane cli-extended `r2`, then run-gate `assay-r2` for the base rule; the CMRU `r2` row only after the campaign path works | T3 becomes automatic | HP-D10 (accepted) hosts on 25.4.1; r1002 window | now (first step) |
| WP-IO | W0 A/B only (strace fsync count) | decides W0 | — | now |
| WP-ENV | `run-gate.root.toml`: relaxed on `tester-unified`, `state_root` on CoW | IO; root disk | the W0 A/B | after the A/B |
| WP-PC | policy-check, code only | — | WP-DOC + the per-project packages | last |
| WP-AS2, WP-RG253L | **deferred** (D15, D17) | — | — | — |
| Rust R-0, R-3a; R-1 → R-2 ‖ R-5 → R-4; R-3b, R-6 deferred | as design §5.4 | — | chain | R-0 running |

## Decisions for the operator

**Settled:** D1–D8 (D8 wording amended), plus round 3 D9–D17 (design §7).

**Still open, each with its default:**
1. **Q2** Let a running older-release T3 run finish; the newer entry's base is computed at its first claim and stored.
2. **Q4** Also run Assay `tester-unified` at T1 when `gate/`, `tools/` or packaging change.
3. **Q6** max(24 h, 2× median + 4 h); `RESUME` time counts as "slow".
4. **Q7** Accept LR-KI-1..4 and 7..11; relaxed I/O on the two environments after the A/B; manual-run overlap (LR-KI-10); declared-memory placement (LR-KI-11).

## Rust decisions NOT yet in the repo (for the persistence job)

These are unchanged from the previous revision. They are still only in `reports/assay-RUST-ADAPTER-DESIGN.md` and the 2026-11-08 blocks of `reports/hostobs-DECISIONS-20261107.md` (persistence job: `reports/assay-rust-docs-REPORT.md`):

- cargo-mutants as the ingested R2 engine;
- R1 = 100% changed lines + 90% line / 85% region per crate (branch coverage is a known issue);
- no vendoring: a `tester-unified-rust` crate cache plus `--offline --locked` with a precheck;
- the 14-day rule for toolchain, tools and crates together;
- R2 waived for now, with a per-release waiver record;
- diff-scoped 100% killed, `.cargo/mutants.toml`-only exclusions, no `#[mutants::skip]`, advisory whole-crate shards;
- the panicking `#[cfg(test)]` canary;
- the per-commit baseline list;
- `forbid(unsafe_code)` except the kernel/act crates;
- backlog: semver-checks, miri/sanitizers, benchmarks, fuzzing.

The tier amendments in design §5.3 also need to reach the persisted Rust design. Note that amendment 1 now uses the last R2-covered release tag as the base.
