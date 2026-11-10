# Design: estate lane rework by test tier (2026-11)

**Status: DESIGN, rev 2** (2026-11-10). Rev 2 folds in the independent Opus
review (`reports/test-methodology-REVIEW.md`, FIX-FIRST, B1–B7 plus nits) and
the operator's answers after that review (`reports/test-policy-REQUIREMENTS.md`,
last section). It also applies the operator's guidance to relax adversarial
guards, think from the end, and look for CoW/tmpfs wins (§8, §9). No
executable change.

How this document relates to the others:

- **Doctrine** (why): `nyxloom/reference/TESTING-METHODOLOGY.md`,
  `## Test tiers by event (T0–T3)`.
- **Normative adoption contract**: `run-gate-project/CONSUMERS.md` +
  `LANE-AUTHORING.md`, written by WP-DOC (§5).
- **This document**: the current state, the measurements, the features, the
  T3 mechanism and the per-project work packages.

Labels:

| Label | Meaning |
|---|---|
| **measured** | a number printed by a release log or lane |
| **inferred** | derived from timestamps |
| **estimated** | arithmetic on the above |
| **UNVERIFIED** | not established by any file read for this design |

`logs/...` and `reports/...` are under `/home/vscode/vbpub-release-program-20261027/`.

Operator rules this design keeps:

- presets plus documented per-project deviations, no new config layer;
- "known issue / by design" is a legitimate close;
- tests stay load-independent;
- R2 and fuzzing never block an ordinary release;
- R4 is the reviewer's call;
- audit and fix in one pass per project.

## 1. Findings

### 1.1 Measured release-gate times (this release wave)

| Project | Release test steps (measured) | Source |
|---|---|---|
| cmru 7.2.5 | `gate-provisional` **2,989.8 s** (pytest 741.17 s inside it); 7.2.4: 3,269.4 s | `logs/release-cmru-7.2.5.log:30`, `release-cmru-7.2.4.log:30` |
| assay 12.1.1 | `tester-unified` **1,370.3 s** + provisional qualification **1,300.2 s** | `logs/release-assay-12.1.1.log:30,33` |
| run-gate 25.4.1 | build+receipt 123.1 s + `selftest` **299.2 s** (25.4.0-c: 116.9 s + 246.1 s) | `logs/release-run-gate-25.4.1.log:30,33` |
| pwmcp r6 / r7 | Assay R1 **397.5 s** / **391.6 s** | `logs/release-pwmcp-r6.log:39`, `-r7.log:39` |
| ciu 7.24.1 | coverage gate **230.1 s** + enrollment **60.3 s** | `logs/release-ciu-7.24.1.log:30,33` |
| iobench 0.2.0 | `r0-r1` **206.8 s** | `logs/release-iobench-0.2.0.log:30` |
| cgprofile 3.0.0 | `r0-r1` **193.5 s** + `r3` **11.0 s** | `logs/release-cgprofile-3.0.0.log:30,33` |
| topos 0.8.0 | parallel suite **110.9 s** (no coverage) | `logs/release-topos-0.8.0.log:30`, `topos/cmru.toml:29` |
| cli-extended 0.6.0 | `r0-r1` **82.4 s** + `r3` **4.1 s** | `logs/release-cli-extended-0.6.0.log:29,32` |
| augeaswrap 0.2.0 | `r0-r1` **65.6 s** + `r3` **3.1 s** | `logs/release-augeaswrap-0.2.0.log:30,33` |
| tester-base 0.2.0 | contract lane **6.1 s** / **5.4 s** | `logs/release-tester-base-0.2.0.log:36,48` |

No release log line carries the "ciu unit 242 s" quoted in the brief.

### 1.2 Where the time goes, by project class

All 36 project `run-gate.toml` files (fixture excluded) were read with `tomllib`.

| Class | Projects | Cost driver / gap | Lever |
|---|---|---|---|
| **A. Large suite, several judged runs** | cmru, assay | cmru runs one suite 6 times per release: four unmodified controls + two transforms (`reports/gate-reuse-REVIEW.md:21`). assay's installed-wheel R0 (`tester-unified`, `tests gate/tests`) and its source R0/R1/R3 qualification both run `tests`. | Remove duplicate runs (WP-CMRU, B178, WP-ASSAY) |
| **B. Mid suite, one run** | ciu, run-gate, pwmcp, iobench, cgprofile, topos, nyxloom | Tier gaps, not time. Details below the table. | Re-point T1/T2, add canaries and an R2 lane |
| **C. Small library** | cli-extended, augeaswrap, test-policy, iogovmon, damon-analysis, debian-install-v2, gstammtisch-guide | `gate` is a `bash -c` conjunction: with `r2` in augeaswrap, cli-extended, test-policy, damon-analysis, gstammtisch-guide; **without** `r2` in debian-install-v2 (`r0-r1 && r3`). iogovmon holds an **88%** floor and has no R3. test-policy releases without R3. | Sequence without R2; R1/R3 per project |
| **D. Single lane, no Assay** | scripts/mounttrace, netcup, telegram, truenas, voice-screen-share/*, plesk-mailbox-create, tls-edge, tester-base, wings-patchstack, shared-ramdisk-depot-manager, libraries/patchseries, rigor-record, vmscreen | Seconds to minutes. | Mapping + known issues |
| **E. Libraries judged inside consumers** | cowtree (R0/R1, base `main`), worktree (R0–R3 incl. R2 in one lane) | Not released on their own; judged again by assay and cmru/ciu. | Mapping; worktree's R2 is its T3 lane |
| **F. Live / host lanes** | truenas `live-*`, truenas/tnt `live-*`, mdt-cockpit `vm-system`/`image-runtime*`, debian-install-v2 `r1-vm-real-commit` | Real hosts, VMs, images. | Never reusable; T2 only where the release names them |

Class B gaps in detail:

- **ciu:** the pre-merge pointer runs the R2 lane `ciu` (`ciu/nyxloom-trove/nyxloom.toml:58` → `ciu`: R0–R3, 8 h). The release runs `run-ciu-tests.py` through `cmru tester-gate` with no R3 (`ciu/cmru.toml:77-79`).
- **run-gate:**
  - the release runs `selftest` only, whose `coverage_gate.py` judges just three files (`run_gate.py`, `run_gate_admission.py`, `run_gate_profile.py`);
  - `gate-full` adds `assay-r1`, which judges `source_roots = ["."]` including `tools/` (`run-gate-project/assay.toml:5-16`). That is a wider claim, not a duplicate.
- **pwmcp and iobench:** no R3 at release.
- **topos:** the release step runs pytest **without coverage**, so there is no release R1 at all. `topos-suite`'s `coverage_gate.py --base main` is vacuous on a release tree.
- **nyxloom:** the release R1 is `tester-unified` with `base = "main"` (`nyxloom/assay.toml:57-68`), vacuous on a tagged main HEAD (UNVERIFIED by run). No R3 at release.

### 1.3 The CMRU gate, phase by phase

`gate-provisional` runs serially (`cmru/tools/run_release_gate.py:1514-1555`). All per-lane values are **inferred** (`reports/gate-reuse-INVESTIGATION.md:15-22`).

| Phase | Time |
|---|---:|
| `ref-probe` | ~2 s |
| `installed-wheel-live` | ~10.5 s |
| `assay` | ~1,249 s |
| `coverage` | ~751 s |
| `canary` | ~919 s |
| residual | ~58 s |

**`assay`** is Assay lane `cmru`: R0/R1/R3, changed lines against `base = "cmru-v6.1.0"`, import-break canary.

**`coverage`** re-runs `pytest tests` with `--cov-fail-under=100`.
- Assay `whole_target` R1 is at least as strict: the same total formula at exactly 100, and it refuses exclusions, unmeasured targets and remapped lines.
- cmru has no `[tool.coverage]`, no `omit` and no pragma in `src/`.
- The 40 targets equal the tracked `src/cmru/**/*.py` files (except a docstring-only `__init__.py`), and `tests/test_assay_lane_inventory.py` guards this. Verified by the review.
- What `coverage` adds beyond that: the junit artifact used for failure naming (`run_release_gate.py:1023`), and a `check_assay_source.py` preflight that stays in `mutation`.

**`canary`** (`tools/coverage_canary.py`) proves that **pytest-cov's** `--cov-fail-under` floor rejects an uncovered line. It probes the instrument `coverage` provides, so it is retired with it (operator D1 revised).

The replacement needs **no new Run-Gate/Assay feature**, but it is more than configuration:
- **R2 base coupling.** `whole_target` refuses `judge.base` (`assay/src/assay/config.py:2645-2660`). `tools/check_assay_baseline.py:248-251` hard-reads `lanes.cmru.judge.base` and requires it to equal the latest published ancestor tag (`:492-497`). The R2 base must therefore move to the mutation lane or come from published tags only. The stale `cmru-v6.1.0` pin exists because R2 has not run since 6.1.0.
- **`cmru-first-release` only declares** a whole-target lane. `_plan_gate` (`run_release_gate.py:1451-1490`) selects it only when no tag exists, so no release has ever run it. WP-CMRU's own gate and a rehearsal patch release are its first real runs.

**R2 today** runs only inside the aggregate `gate`, which prepares `CMRU_ASSAY_BASELINE_FACTS` (`run_release_gate.py:1530-1550`). `mutation` cannot run alone. KI-141 (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:3165-3180`, OPEN, major) makes a recorded full R2 of the 7.2 line a hard precondition for 7.3.0. More than 160 survivors are already recorded (KI-129/133/135/138).

### 1.4 What verdict reuse (RG-253) would save

RG-253 v1 saves **0 s per normal release** on today's CMRU layout (`DESIGN-verdict-reuse.md:527-541`). Its use case is a repeat on an identical snapshot, and this wave had many:

| Retry | Snapshot | Green tests re-run (measured) | Log |
|---|---|---:|---|
| pwmcp r7 attempt 2 → final | `41389b6a86ef` | 396.9 s | `logs/release-pwmcp-r7.log.attempt2-timeout:5,39` |
| pwmcp r7 attempt 3 → final | `41389b6a86ef` | 393.7 s | `logs/release-pwmcp-r7.log.attempt3-timeout:5,39` |
| assay 12.1.0-d → -e | `2f7f386cccc3` | 1,358.1 s | `logs/release-assay-12.1.0-d.log:5,30` |
| ciu 7.24.1 attempt 2 → final | `15d7d50a87e5` | 255.6 s | `logs/release-ciu-7.24.1-attempt2-dind-missing.log:30` |
| augeaswrap 0.1.0 attempt 1 → final | `f454c38d8dd6` | 32.4 s | `logs/release-augeaswrap-0.1.0-attempt1.log:30,33` |
| **Total** | | **≥ 2,436.7 s** | (review B1) |

There was also one non-test repeat: run-gate 25.4.0-b → -c rebuilt and re-receipted (131.3 s).

The main causes were the checksum-sidecar timeout and environment errors, now fixed at the root (cmru KI-144, `cmru/KNOWN_ISSUES_TODO_BACKLOG.md:3200-3206`). pwmcp attempt 1 failed on the external-version read (KI-142), before its tests.

Conclusion (D6): defer RG-253.
- It saves nothing per normal release.
- Its prerequisites are heavy: an immutable snapshot view and a protected evidence root.
- It needs a ciu v8 home.
- The free part, moving the `CHANGES.md` reader into `release-preconditions`, is done in WP-CMRU.

### 1.5 Resource facts for T3 distribution

| Fact | Where it lives today | Value / state |
|---|---|---|
| Per-lane duration per host | `logs/host-sat-results.tsv` (896 rows: host, lane, verdict, seconds) | **measured** medians, r1002 / v1001: `pwmcp:r1` 437 / 478 s; `run-gate-project:selftest` 254 / 348 s; `libraries/iobench:r0-r1` – / 283 s; `cli-extended:r0-r1` 130 / 157 s; `topos-suite` 130 / 158 s; `cgprofile:focused` 67 / 94 s. v1001 is ~1.2–1.4x slower. |
| Per-lane peak memory, cores, duration | Run-Gate footprint manifest `run-gate.footprint.json` (SPEC R-44, `run-gate footprint` verb), distilled from `.run-gate/history.json` | Committed only for run-gate-project (example: `assay-r1` median peak 743 MB, 0.79 cores, 185 s). Every other project has none (gap). |
| R2 campaign size | Assay progress/verdict artifacts in `remote-gates/<run>/`, cmru KI entries | Per campaign; no manifest. cmru R2 budget 7 h; tnt 24 h. |
| Host profile | `reports/R1002-REINSTALL-2026-11-04.md:8`; `HANDOFF-STATE-20261107.md:483`; host-pipeline plan §8.4 proposes `hosts/<host>/profile-<date>.json` | r1002: 4 vCPU, 7.75 GiB RAM, 16 GiB swap (measured). v1001: 4 vCPU; `dev-gates.slice` `MemoryMax=6G` (set by hand); RAM UNVERIFIED. |
| Host admission (on-host) | Run-Gate admission (gates slice budget) | Prints "gates slice X of Y MB used; lane needs Z — admitted". |

## 2. Features needed

### 2.1 Required

| ID | Tool | What | Smallest implementation | Effort | Blocks |
|---|---|---|---|---|---|
| **B178** (existing Assay backlog, `assay/nyxloom-trove/4-backlog.md:163,13457`; designed in `assay/nyxloom-trove/reports/ASSAY-DESIGN-OPUS-2026-10-30.md` §§6.3, 7) | Assay | R3 canary control taken from the lane's own passing baseline. **Automatic when sound** (operator); no config key. | Pass the baseline unit and judgement from `runner.py` (baseline at ~:4583) into `canary.py` `run_isolated_canary` (:636; control block :766-810) and `run_isolated_canaries` (:957), see below. | M (3–5 days) | WP-CMRU step 2, WP-ASSAY step 2; every Assay R3 lane |
| **RG-F1** (file with the next free RG id; RG-254 and RG-255 are taken on unmerged branches) | Run-Gate | Read-only `run-gate policy-check`. **Required** (operator answer 3) and built **last**. | An extension of `cmd_validate_pointers` (`run_gate.py:11174`). Rules and derivation below. | M (2–3 days) | the closing estate audit |
| **WP-T3** | release tooling (`scripts/netcup/runner/`) | T3 queue: release-triggered, supersede, capacity-aware, result ledger, attention hook | Extend the host queue; see §3. | M | everything moved to T3 |

**B178 details.**
- **Skip condition.** The control is skipped only when all of these hold:
  - the baseline PASSed;
  - it left no `post_reason`;
  - its contention observation is complete;
  - it ran the **identical `CommandPlan`**. R2 lanes build a different `baseline_plan` with `ASSAY_LIVENESS_EVENTS_ENV`, so the plans are not equal by construction.
- **Otherwise** the canary runs a separate control and records the reason.
- **Recording.** `control_source` goes into the attempt evidence (verdict schema bump).
- **Docs.** Fix `assay/docs/CONSUMERS.md:2826-2832` ("runs three commands") and the `canary.py:650-690` docstring ("two independent P22 snapshots").
- **Consumers.** Notify dstdns of the schema bump; its JS R3 lanes gain the most, since each control repeats a cold `npm ci`.

**B178 trust.** A baseline-as-control cannot produce a false R3 PASS. R3 PASS needs the *transformed* run to fail for the mechanism's expected cause (import receipt, floor rejection). What is given up is detecting a nondeterministic materialization; that is by design.

**RG-F1 rules.**
- **T2** = the lanes the release step invokes, expanded through sequences. Recognize `run-gate <lane>` plus the two known wrapper scripts: `assay tools/release-gate.sh <lane>` and run-gate-project `tools/release_candidate.py run-selftest`. Anything else reports **UNDERIVABLE**, a status of its own.
- **T1** = trove `phase = "implementation"` gates, or the project's `r0-r1`.
- **T3** = Assay rigor containing R2, or a lane named `r2`/`mutation`/`fuzz*`.
- Checks:
  - P1: T2 contains no R2 or fuzz lane;
  - P2: T2 carries the rule-6 canaries;
  - P3: T1 contains no R2 (a **warning**, because a reviewer may ask for synchronous R2);
  - P4: duplicate test commands inside one T2 composite (warning);
  - P5: command lanes that call `assay.cli run` pass `--resume --progress`;
  - P6: T2 composites are `kind = "sequence"`;
  - P7: a project that claims R2 has a T3 R2 lane that runs alone (no `required_env` that only an aggregate supplies).
- Exit codes: 0 pass, 1 violation, 2 unreadable input.
- Accepted deviations: one table `[policy.accepted]` of rule-id → reason string with a decision reference. Pre-seed cmru's P6 entry, because `run_release_gate.py` must share one reset snapshot.
- The "latest release has no T3 R2 result" staleness signal is **not** in policy-check (it would have to read state outside the repo). It is a ctlstat attention rule (§3).

### 2.2 Recommended

| ID | Tool | What | Why | Effort |
|---|---|---|---|---|
| **AS-2** (new Assay backlog item; no existing entry found) | Assay | Several canary mechanisms in one lane (for example `judge.canary.mechanisms = ["import-break", "uncovered-line"]`), sharing the B178 baseline | Methodology rule 6 needs both canaries on every changed-lines Assay lane at T2: Rust, ciu-gate, run-gate `assay-r1`, nyxloom `tester-unified`. Assay accepts one `mechanism` per lane, so without AS-2 each extra canary costs a second Assay lane with its own full baseline. | M (2–3 days, after B178) |
| **cmru T3 R2 entry point** | cmru | `run_release_gate.py --mutation-only` (prepares the baseline facts, runs `mutation`), exposed as lane `r2` | Operator D4 revised: every project needs a runnable-alone T3 R2 lane. Today `mutation` needs facts that only the aggregate `gate` prepares. | S–M (inside WP-CMRU) |

### 2.3 Evaluated and not recommended now

| Idea | Verdict | Reason |
|---|---|---|
| A shared suite execution feeding Assay R0/R1 and the coverage floor | **No feature** | Assay `whole_target` already makes the one run the floor (§1.3). |
| Canary only on judge-input change | **Policy** | Methodology rule 6: applicable canaries at T2. B178 makes each canary cost one transform. |
| A tier key on lanes | **No** | Tiers are derived (RG-F1). |
| RG-253 verdict reuse | **Defer** | 0 s per normal release; ≥ 2,436.7 s of retry waste whose causes are fixed; heavy prerequisites (§1.4). |
| Diff-aware test selection | **T0 only** | R0 at T1/T2 is the project suite. |
| Parallel lanes inside one gate | **Defer** | CMRU lanes share one reset snapshot; T3 parallelism comes from distributing over hosts instead. |
| Prebuilt Assay image | **Rejected** | Same-repo consumers install Assay editable at lane time. |

## 3. T3 trigger and distribution

### 3.1 Requirements (operator, 2026-11-10)

1. **R1 Trigger.** A release queues its T3 entries at its tag, so every project's T3 R2 lane is always available.
2. **R2 Supersede.** Only the latest release of each tool gets the run; older pending entries are superseded.
3. **R3 Distribution.** Lanes are assigned to hosts by capacity, using known approximate runtime and RAM.
4. **R4 Results.** Results land in one place, with verdict, tag, host and run directory.
5. **R5 Attention.** A missed or failed T3 run is an attention item (ctlstat detector).
6. **R6 Fit.** It works with the existing hosts, their guards and the load rules (one heavy gate per host unless capacity allows more; memory and IO PSI as refusal signals; CPU overcommit by design).

### 3.2 Options

The three options compared:

- **(a)** extend the host queue + `remote-gate`;
- **(b)** a Run-Gate / ciu v8 server component;
- **(c)** Buildkite agents on our hosts.

| Requirement | (a) | (b) | (c) |
|---|---|---|---|
| Exists today | `remote-gate run <host\|auto> <worktree> <lane> --detach` ships a committed worktree, including release tags (`release_tags()`), to r1002/v1001. It runs the lane, `collect`s logs, artifacts and `result.json` into `remote-gates/<run>/`, and has picked the less-busy host by PSI (`choose_host`). It already ran every T3 R2 of this wave by hand (for example `clix-r2-060-r1002`). The host queue adds probing, rotation, auto-pause and a results TSV. | Nothing. v8 specifies per-host admission (tickets, `max_concurrent`, v8.1 byte budget from the footprint manifest), evidence and history. It has no cross-host queue: the v8 spec keeps cross-host work to push/activate/probing and never drives a host by SSH (`REMOTE-LANES-BUILDKITE.md` §1). v8 itself is unbuilt. | Pipeline generator and trigger/collector written and stub-tested (`tools/buildkite/pipeline.sh`, `bk-lane.sh`). **No build has ever run against the real API.** Seam 1 (artifacts contract) and seam 3 (image provenance) have not started (`REMOTE-LANES-BUILDKITE.md` §6). |
| Trigger | The queue watches release tags (`git tag -l '<prefix>v*'`, the same source the host-pipeline plan §8.2 uses for "released") | new daemon | Pipeline trigger per tag (API call from the controller or a webhook) |
| Supersede | A rule in the queue: for each (tool, lane), keep only the newest tag pending | to build | Buildkite "skip intermediate builds" per pipeline/branch [K, UNVERIFIED for tag builds] |
| Capacity | Place by lane footprint (peak memory) against host free memory (gates slice budget − Σ running peaks). Order by expected duration. Keep PSI refusal. | to build (v8.1 byte budget is per host only) | Agents take one job each; capacity is agent count and tags, not RAM-aware. One agent per host = one T3 lane at a time. |
| Results | `remote-gates/<run>/result.json` + a T3 ledger row | to build | Build record + artifacts; `bk-lane.sh collect` |
| Attention | ctlstat reads the ledger | to build | Notifications + a collector |
| New dependencies | none | a service to run, secure and port | outbound HTTPS from hosts, an account and plan limits, agent token custody, host access to the git remote (hosts receive bundles today) |
| Effort | **M** (one queue mode + ledger + ctlstat rule) | **L+**, against D-110 ("no CI system of our own") | **M–L** + external setup; live seams still unproven |

### 3.3 Recommendation

**(a): extend the host queue and `remote-gate`.** It meets every requirement with the code already in use and needs no new service, account or host network access.
- **(b)** is rejected: it would write the CI system that D-110 refused, inside a tool that is being ported to v8.
- **(c)** stays the scale-out path, for example if a third or fourth host is added, or if a browsable build history is wanted. It plugs in later behind the same ledger, because both call the same `run-gate <lane>`.

Keep the gate tool single-host: v8 `ciu gate` admission and the footprint manifest are the per-host inputs the queue reads.

Two homes, agreed with the host-pipeline plan:
- **The queue code** moves from the release-program directory into the repo next to `remote-gate.py` (`scripts/netcup/runner/t3-queue`, tested like `remote-gate.py`). The host-pipeline plan's B28 lists the queue as an external operator tool.
- **Its state** (ledger, pause flags, per-host logs) stays in the release-program directory.

### 3.4 Mechanism

**T3 lane table.** One committed file, `scripts/netcup/runner/t3-lanes.toml`. Each row has:
- `tool`, `tag_glob`, `project_dir`, `lane`, `kind` (`r2` | `fuzz` | `probe` | `variation`);
- optional `budget_hint`.

Footprint values are not copied into this file; they are read from the project's `run-gate.footprint.json`.

**Loop (per controller tick):**
1. **Enqueue.** For each row, find the newest tag. If the ledger has no row for (tool, tag, lane) and nothing is pending or running for it, enqueue it. Mark older pending entries for the same (tool, lane) as `SUPERSEDED` in the ledger. A running older entry continues.
2. **Assign** each pending entry, longest expected duration first, to the host where `free_mem = slice_budget − Σ peak(running)` ≥ the lane's footprint peak (default 2 GiB when no footprint exists), and memory PSI some ≤ 25 and IO PSI full ≤ 25 (the existing `remote-gate`/queue limits). Ties go to lower queued work. CPU is not a refusal signal (operator: overcommit by design). A lane whose peak exceeds every host's budget is marked `UNPLACEABLE` (attention).
3. **Launch** with `remote-gate run <host> <worktree at the tag> <lane> --detach`, run name `t3-<tool>-<tag>-<lane>-<host>`.
4. **Collect** with `remote-gate collect` when the completion marker appears, then append the ledger row. Copy footprint data back by running `run-gate footprint` over the collected history, so the next placement uses measured values.

**Ledger.** `logs/t3-results.tsv` with columns `utc, tool, tag, lane, host, run_name, verdict, seconds, peak_mib, run_dir`. Verdicts:
- PASS, FAIL;
- INFRA:<remote-gate verdict>;
- SUPERSEDED, UNPLACEABLE, CANCELLED.

**Attention (ctlstat rule).** For each T3 row, the latest tag must have a PASS row within N hours of its release (default: 24 h, or 2× the lane's median duration plus 4 h, whichever is larger). Otherwise raise an attention item:
- "missing";
- "FAIL → backlog item";
- or "INFRA → retry/fix".

This also gives the per-release "R2 owed" record its closer: the cmru marker is closed by the matching PASS row.

**Host profile.** The plan's `hosts/<host>/profile-<date>.json` (§8.4) gets three fields: `cores`, `ram_bytes` and `gates_slice_memory_max`. The queue reads them. Until the profiles exist, it reads the host's `memory.max` of the gates slice and `nproc` through the read-only probe it already runs.

**Agreement with the host-pipeline plan:**
- Its §8.2 "green" status source gains the T3 ledger: last verdict per (tool, tag, lane).
- Its D10 (both hosts default to run-gate 25.4.1) is a precondition for observer lanes (R2 with `resource_observer = "cgprofile"`).
- Hosts paused by the queue's flags also pause T3.

### 3.5 Smallest first step

Add `--release <tool>` mode to the queue for **one** lane: `libraries/cli-extended` `r2` at the newest `cli-extended-v*` tag. That is the campaign run by hand today (`clix-r2-060-r1002`).

The step includes the supersede rule, the ledger row, footprint-based placement over both hosts, and one ctlstat attention rule.

Acceptance:
- an older pending tag is superseded;
- a lane is placed on the host with room;
- a missing result raises attention;
- a red result names the backlog to file into.

Then add rows project by project as each per-project package lands its `r2` lane.

## 4. Per-project work packages

Every package is **one pass for one project** (operator answer 4). It covers lane config, release pointer, trove pointer, a runnable-alone `r2` lane (or a known issue), canaries per rule 6, adoption docs and docs-sync tests, in one reviewed change. Each is gated through the project's registered lane in `tester-unified`.

Host rules: one gate container at a time per host, no cockpit pytest, no big local processes; heavy runs go to the hosts.

### 4.1 Table

| WP | Project | Change (one pass) | Expected saving (label) | Risk | Needs |
|---|---|---|---|---|---|
| **WP-DOC** | run-gate docs only | Normative tier contract in `CONSUMERS.md` + `LANE-AUTHORING.md`: tiers, presets, prep-only paths, `[policy.accepted]`, the canary rule, the T3 trigger. Fold the draft `TEST-POLICY.md` (§6). `LANE-AUTHORING.md` §3: `gate` excludes R2; `mutation` → `r2` at T3. Docs-sync test: the methodology's preset names are documented in `LANE-AUTHORING.md`. | — | docs-sync tests | — |
| **WP-CMRU** | cmru | Step 1, a rehearsal patch release, has eight parts listed below the table. Step 2: after B178 ships, nothing to configure (automatic). | Step 1 ≈ 1,670 s (**estimated** from inferred 751 + 919) → T2 ≈ 1,320 s. Step 2: one control suite, 550–600 s more likely than 400 (review bound) → ≈ 700–800 s. | KI-79/KI-141 tests and docs; junit failure naming; `tests/test_assay_lane_inventory.py` must cover lane `cmru`; R2 facts on a host (UNVERIFIED that `local_baseline_facts.py` works from bundled tags) | Rehearsal release; B178 for step 2 |
| **WP-ASSAY** | assay | (1) **Narrow the installed-wheel R0 now** (operator override of D5), details below the table. (2) T1: `self-qualification-preflight` for source changes, plus `tester-unified` when `gate/`, `tools/` or packaging change; otherwise `gate/tests`, the analysis lane and the independent/SQL witnesses would surface only at release. (3) T3 lane = `self-qualification` (R2). (4) B178 then halves the provisional lane automatically. | (1) up to ~1,000 s (UNVERIFIED split); (4) ~300–500 s (**estimated**) | Packaging regressions seen only by tests outside the subset | B178 for (4) |
| **WP-CIU** | ciu | New command lane `r0-r1` (`run-ciu-tests.py`, xdist, 100% whole-project); trove T1 pointer → `r0-r1`; `r3` targeted canary; `gate` sequence [`r0-r1`, `r3`, `enroll`] named by `[steps.run-tests]` (or `enroll` kept as its own step if the nested daemon needs it); `ciu` (R2) = T3 lane; `ciu-gate` kept on demand. | T1: hours → ~4 min (**estimated**; ciu R2 duration UNVERIFIED) | `enroll` needs `--enable-docker` | — |
| **WP-RG** | run-gate-project | Widen `selftest`'s `coverage_gate.py --source` list to every non-test `.py` that `assay-r1` judges (`tools/coverage_gate.py`, `tools/release_candidate.py`, every root module), **then** drop `assay-r1` from `gate-full`; `gate-full` → sequence [`selftest`, `assay-r3`]; release step adds `assay-r3`; `selftest`'s diff floor gets an uncovered-line canary (rule 6, `--base latest-release-tag` is already set); `assay-r2` = T3 lane. | One suite (~250–300 s **estimated**) per `gate-full` | `tools/` lines newly judged may need tests | — |
| **WP-CLIX** | libraries/cli-extended | `gate` → sequence [`r0-r1`, `r3`] (R2 out); **keep** the `--capture=sys` second run (operator D2 revised); `r2` = T3 lane | — (by design: the second run is a kept guard) | — | — |
| **WP-AUG** | augeaswrap | as WP-CLIX | — | — | — |
| **WP-IOBENCH** | libraries/iobench | `gate` → sequence (R2 out); add targeted `r3`; release names `r0-r1` + `r3`; `r2` = T3 | +seconds | canary target drift must refuse | — |
| **WP-TESTPOLICY** | libraries/test-policy | as WP-IOBENCH | +seconds | — | — |
| **WP-IOGOVMON** | iogovmon | 88% floor ⇒ add changed-lines R1 against the previous release tag (rule 4) with an uncovered-line canary; targeted `r3`; `gate` → sequence; R2: add an `r2` lane or a known issue | +seconds | first release still gated on WP-IGM-ACC | — |
| **WP-SEQ4** | cgprofile; scripts/damon-analysis; scripts/gstammtisch-guide; scripts/debian-install-v2 | One project per pass, same change: `gate` → sequence; R2 out (debian-install-v2 has none in `gate`); existing `r2` = T3 lane | — | — | — |
| **WP-TOPOS** | topos | Release step → `run-gate topos-suite` (coverage on); `coverage_gate.py --base` → the latest release tag; uncovered-line canary on that diff floor + targeted `r3`; `r2` lane (Assay, Python) or known issue | release gains R1 | the Rust sampler stays on PoC branches (§5) | — |
| **WP-PWMCP** | pwmcp | Targeted `r3`; `r1` stays (whole-target R0/R1); `r2` = T3 lane | +seconds | — | — |
| **WP-NYXLOOM** | nyxloom | Release R1 base → previous release tag (`base_source = "request"`); import-break + uncovered-line canaries (changed-lines R1; with AS-2 in one lane); a full-suite `r2` lane (today only `session-extract` claims R2, over a subset) or known issue | — | Hypothesis `gate` profile before R2 (methodology §Hypothesis) | AS-2 optional |
| **WP-MDT** | mdt-cockpit | Map: smoke/release/oci-push/image-runtime-built = T2; `assay-full` (R0–R3) = T3 R2 lane; `vm-system` = T3/manual | — | — | — |
| **WP-MAP-*** | class D and E projects, tnt, scripts/truenas, wings-patchstack, shared-ramdisk-depot-manager, cowtree, worktree | One pass per project: tier mapping + known issues (no R3 / no R2 adopted), live lanes = T2 only where the release names them | — | many small edits | — |
| **WP-B178** | assay (code) | Implement B178 (§2.1) | enables WP-CMRU step 2, WP-ASSAY (4) | schema bump; self-hosting | — |
| **WP-AS2** | assay (code) | Implement AS-2 (§2.2) | removes extra lanes for two-canary lanes | — | B178; decision Q3 |
| **WP-T3** | scripts/netcup/runner + ctlstat | §3.5 first step, then rows per project | moves T3 from manual to automatic | host capacity | hosts on run-gate 25.4.1 |
| **WP-PC** | run-gate (code only) | RG-F1 (§2.1); its docs come from WP-DOC | — | — | after WP-DOC and the per-project packages |
| **WP-TMP** | central config + per project | W1 (§9): central `state_root` on the CoW area per environment (in-container path of `/srv/cow` / `/srv/mdt-cockpit-cow`, UNVERIFIED whether one value fits all hosts); `TMPDIR = "required-env:RUN_GATE_TMPDIR"` on each Assay lane, folded into each per-project package (CMRU first, then the CMRU shard branch); command lanes use `TMPDIR="$MDT_COW_ROOT/..."` in their argv | root-disk IO and space (**measured** ~2.5 GB/shard) | RG-98 rule (state root outside the worktree) | — |
| **WP-PYC** | per project, CMRU first | W2: one A/B run, then `PYTHONPYCACHEPREFIX` in place of `PYTHONDONTWRITEBYTECODE` where it pays | UNVERIFIED until measured | none | — |
| **WP-DUR** | cmru, assay | W3 duration survey; follow-up fixture packages only where measured | UNVERIFIED | — | — |
| **WP-RG253L** | run-gate (code) | RG-253-lite (§8.2), only if Q8 = yes | retries (≥ 2,436.7 s measured this wave) | LR-KI-5/6 | Q8; after WP-CMRU |

**WP-CMRU step 1 parts:**
1. Switch `[lanes.cmru]` to `whole_target` (targets exactly as `cmru-first-release`; the inventory test also covers `cmru`).
2. Re-home the R2 base. `check_assay_baseline.py` resolves it from published tags only; its assay.toml consistency check is deleted or pointed at the mutation lane. Update `tests/test_assay_baseline_gate.py`, `tests/test_docs_config_examples.py`, `tools/prepare_assay_baseline.py` and `tools/local_baseline_facts.py`.
3. Retire `coverage`, `canary` and `tools/coverage_canary.py`. Failure naming reads the Assay verdict tail instead of `junit-coverage.xml`.
4. Move `test_w3_prep_changelog_preflight.py` to `release-preconditions` and `--ignore` it in the Assay argv. Check that the 100% floor still holds (UNVERIFIED).
5. Retire the R2-including `gate` and add `r2` (= `run_release_gate.py --mutation-only`) as the always-available T3 lane.
6. Keep the per-release postponement marker as the "R2 owed" record (drop the version window, keep the marker).
7. Keep KI-141 as a hard 7.3.0 precondition in `release-preconditions`.
8. Add an `r0-r1` Assay lane (R0, R1) for T1.

**WP-ASSAY step 1 details.** Narrow `tester-unified` to `gate/tests` plus the tests that exercise the installed CLI/wheel. Record one phase-split measurement as evidence. That measurement does not gate the change.

### 4.2 Order and parallelism

**Now, in parallel** (disjoint files):
- WP-DOC;
- WP-CMRU step 1;
- WP-ASSAY steps 1–3;
- WP-CIU, WP-RG, WP-CLIX, WP-AUG, WP-IOBENCH, WP-TESTPOLICY, WP-IOGOVMON;
- each WP-SEQ4 project, WP-TOPOS, WP-PWMCP, WP-MDT, the WP-MAP-* projects;
- WP-B178, WP-T3 (first step);
- WP-TMP and WP-PYC (each folded into the project's own package; CMRU first), WP-DUR;
- Rust R-0 and R-3a (§5).

WP-NYXLOOM can start now; it is simpler after AS-2.

**After B178 ships:** WP-CMRU step 2 (verify only) and WP-ASSAY (4); WP-AS2.

**Last:** WP-PC, then the estate run of policy-check, then the WP-T3 rows for every project.

**Conflict avoidance:**
- WP-DOC owns `CONSUMERS.md`/`LANE-AUTHORING.md`; WP-PC touches only code and tests.
- Each per-project package touches only its project directory. Exception: WP-T3 adds that project's row to `t3-lanes.toml`, sequentially after the package merges.

### 4.3 Projected T2 for the two large projects

| Project | Today (measured) | After step 1 (estimated) | After B178 (estimated) |
|---|---:|---:|---:|
| cmru | 2,989.8 s | ≈ 1,320 s | ≈ 700–800 s |
| assay | 2,670.5 s | up to ≈ 1,670 s (UNVERIFIED split) | ≈ 1,200–1,400 s (UNVERIFIED) |

## 5. Rust

### 5.1 Current state (verified)

- **Assay has no Rust adapter.** The matrix covers Python, JS/TS, Go and SQL (`assay/README.md:544-550`). The adapters are base, go, javascript, python and sql. The lcov parser exists, and DESIGN-GUIDE §11 names Rust as an lcov producer (`assay/docs/DESIGN-GUIDE.md:3275`). R0 works today for any command.
- **No Rust on main** except a legacy tauri experiment. The topos sampler and the ratatui PoC exist only on unmerged branches (`topos-sampler-poc-20261105`, `topos-ratatui-poc-20261105`, `topos-singleview-20261105`). Their `topos-sampler` lane runs `cargo test` inside `docker build`, and its own comment marks it UNVERIFIED. There is no `hostobs/` on main.
- **Binding design:** `reports/assay-RUST-ADAPTER-DESIGN.md` plus the two 2026-11-08 Rust blocks of `reports/hostobs-DECISIONS-20261107.md`. A persistence job is copying them into the repo (`reports/assay-rust-docs-REPORT.md`).

### 5.2 Rust by tier

| Tier | Lanes | Content |
|---|---|---|
| T0 | `lint`, `focused` | fmt, clippy `-D warnings`, cargo-deny, filtered nextest; `--offline --locked` |
| T1 | sequence [`lint`, `assay-r1`] | nextest under llvm-cov + doctests; Assay 100% changed lines; per-crate floors 90% line / 85% region; branch coverage on stable is a known issue |
| T2 | sequence `gate` [`lint`, `assay-r3`, `conformance`] | `assay-r3` is the one R0/R1/R3 lane. It **carries the per-crate floor step** (Rust design §B line 59-60, §C line 86) and both canaries: test-break (a panicking `#[cfg(test)]` module), and uncovered-line because R1 is changed-lines (operator D1 revised; one lane needs AS-2, otherwise a second lane). |
| T3 | `assay-r2`, `mutants-<crate>-<k>of<n>`, later `fuzz` | Diff-scoped cargo-mutants against the previous release tag (D8) once adopted; advisory whole-crate shards on the hosts via WP-T3; cargo-fuzz later |

### 5.3 Amendments to the Rust design for the tiers

1. **§E `assay-r2`** "Pre-merge, not in `gate-full`" → T3, against the previous release tag. Never a merge or release gate. Survivors become backlog items. The end-state claim stays "diff-scoped 100% killed" (D8).
2. **§E `gate-full = lint && assay-r1 && assay-r3 && conformance`** → `gate`, `kind = "sequence"` [`lint`, `assay-r3`, `conformance`]. `assay-r1` is T1 only. `assay-r3` gains the crate-floor step.
3. **§E `assay-r3` "both canaries on one declared target"** is kept, as the operator's revised D1 requires. It needs AS-2 (one `mechanism` per lane today, `assay/docs/CONSUMERS.md:2829-2834`); until then, the uncovered-line canary is a second Assay lane.
4. **Vendoring is superseded** by the second 2026-11-08 block. This covers:
   - §E "Vendoring", "Verification against Cargo.lock" and the offline source replacement;
   - §F items 3 and 5;
   - R-1's "filtered `vendor/`", R-0's spike S1, §I question 1, and the preamble's danger (1).

   What replaces it: `tester-unified-rust` carries a crate cache built by CMRU with `cargo fetch --locked`, and lanes run `--offline --locked` with a precheck naming any missing crate. S1 becomes "crate-cache size + a negative test where an image/lock mismatch fails the precheck by crate name". R-5 becomes "`.crates` source + lock writer + image rebuild trigger". Keep `[net] offline = true`; drop the `[source.*]` lines.
5. **§I question 2 floors** → decided: 90% line / 85% region per crate, plus Assay's 100% changed-line rule.
6. **§E row `mutants-<crate>-<k>of<n>`** (Rust design line 180), "release-time" → T3, advisory, sharded by WP-T3.
7. **The per-release R2 waiver stays** (operator's Rust block 2, and D4 revised keeps per-release R2-owed records). Until Rust R2 is adopted, each release records the waived R2. The T3 ledger has no row to close it. That is the documented known gap.

### 5.4 Rust work packages

| WP | Content (Rust design, amended) | Needs | Can start |
|---|---|---|---|
| R-0 | Spikes S2, S5, S1' (crate cache) in a disposable container **on a host** | — | **now** (running: `reports/rust-r0-REPORT.md`) |
| R-3a | Spikes S3/S4/S7 (cargo-mutants) in a disposable `rust:*-slim` container | — | now, low priority (R2 waived) |
| R-1 | `tester-unified-rust` + crate cache; hostobs skeleton; `lint` + `selftest` (milestone 1) | R-0 | after R-0 |
| R-2 | `RustAdapter` at R1, recognizers, fixtures, `assay-r1` (milestone 2) | R-1 | parallel with R-5 |
| R-5 | CMRU `.crates` source, lock writer, image rebuild, advisories | R-1 | parallel with R-2 |
| R-4 | Canary injectors; `assay-r3` with floors; T2 `gate` sequence | R-2 (+ AS-2 for one lane) | after R-2 |
| R-3b | cargo-mutants ingestion (milestone 3, T3) | R-2, R-3a | deferred (waived) |
| R-6 | Whole-crate shards through WP-T3 | R-3b, WP-T3 | deferred |

## 6. Corrections for the draft `run-gate-project/TEST-POLICY.md`

Branch `test-policy-doc-20261109`, head `b8c00b622`. WP-DOC folds the draft into `CONSUMERS.md`/`LANE-AUTHORING.md` and applies these corrections:

1. **`:87-121`** — tiers derived (RG-F1) and one `[policy.accepted]` map, replacing `[policy.tiers]` + the six-field deviations.
2. **`:48-78`** — drop the `gate-provisional` preset; `gate` is a sequence without R2.
3. **`:137-146`** — retire CMRU's coverage lane via `whole_target` (cmru-first-release already *declares* it; it has never run) instead of "keep both".
4. **`:147-151`** — retire the uncovered-line canary **with** the coverage lane (not "retain", not "move to T3"). Keep canaries per the methodology's rule 6.
5. **`:152-166`** — RG-253 is merged as a design and deferred; v1 saves 0 s per normal release.
6. **`:74-78` and `:342`** — the CIU error, in both places: its release runs `run-ciu-tests.py` (no R3), and its T1 pointer runs R2.
7. **`:391-404`** — CIU, augeaswrap, cli-extended and iobench times are measured.
8. **`:339-350`** — the audit covers 10 of 36 projects; add the rest, plus run-gate's `assay-r1` (wider scope, not a duplicate).
9. **`:177-192`** — shorten the fuzz declaration to target, corpus and budget; add the T3 queue.
10. **`:266-294`** — policy-check output: one line per rule (`rule`, `status`, `lanes`, `detail`), plus `--format json`. Add the UNDERIVABLE status.
11. **`:411-432`** — the open questions are answered by the methodology and the operator decisions.
12. **`:384`** — assay's 1,370.3 s and 1,300.2 s are installed-wheel R0 + witnesses and source R0/R1/R3, not "R2 self-qualification samples".
13. **`:383` and `:395`** — replace the "~990 s conditional on RG-253" projection with ≈ 1,320 s (step 1) and ≈ 700–800 s (after B178).
14. **`:343`** — the assay row lists `selftest`/`gate-full`, which are run-gate-project lanes.
15. **`:345`** — the run-gate row: T2 is [`selftest` with widened `--source`, `assay-r3`], not `gate-full`.

## 7. Operator decisions: settled and open

Settled (operator, 2026-11-10; `reports/test-policy-REQUIREMENTS.md`):

| # | Decision |
|---|---|
| D1 | `whole_target` replaces CMRU's coverage lane. The uncovered-line canary is retired with it, the baseline check is fixed in WP-CMRU, proven by a rehearsal patch release. Applicable canaries are kept or added everywhere (rule 6). |
| D2 | The capture-mode second runs stay at T2. |
| D3 | B178 is automatic when sound. |
| D4 | The R2-including `gate` is retired. Every project gets an always-available T3 R2 lane and a trigger. KI-141 stays a hard 7.3.0 precondition. |
| D5 | Narrow Assay's installed-wheel R0 now. |
| D6 | RG-253 is deferred; the prep-reader split is done now. |
| D7 | Minimal policy-check, built last. |
| D8 | Rust R2 runs at T3 against the previous release tag. |

Open:

| # | Question | Recommended default |
|---|---|---|
| Q1 | T3 mechanism: (a) host queue + `remote-gate`, with the queue code moved into the repo (`scripts/netcup/runner/t3-queue`, state in the release-program directory); (b) a gate-tool server; or (c) Buildkite? | **(a)**; (c) is the scale-out path; (b) is rejected (§3.3) |
| Q2 | A running older-release T3 run when a newer release arrives: let it finish, or cancel it? | **Let it finish**; drop only pending ones |
| Q3 | Build AS-2 (several canary mechanisms per Assay lane), or accept a second Assay lane per two-canary lane? | **Build AS-2** after B178 |
| Q4 | Assay T1: run `tester-unified` too when `gate/`, `tools/` or packaging change? | **Yes** |
| Q5 | Rule 6 for whole-project pytest-cov `--cov-fail-under=100` command lanes (small libraries, ciu): uncovered-line at T2? And class D projects: an R2 lane each? | **No** uncovered-line at T2 (targeted `r3`; uncovered-line optional T3). Class D: known issue "no R2" unless the project has Python logic worth mutating. |
| Q6 | Attention threshold for a missing T3 result | **max(24 h, 2× median + 4 h)** after the release |
| Q7 | Accept the known issues LR-KI-1..4 and LR-KI-7/8 (§8.2, §9) as written? | **Yes** |
| Q8 | Under the relaxed trust model, build RG-253-lite (S–M, LR-KI-5/6) after WP-CMRU, i.e. revisit D6? | **Yes, after WP-CMRU and W1**: retries cost ≥ 2,436.7 s this wave; RG-253 full stays deferred |

## 8. Assay and gate relaxations: think from the end

Operator guidance (2026-11-10): we control the hosts, worktrees and lanes, so
guards against an *adversarial* actor may be relaxed where they cost
complexity or runtime. Guards against *accidental* error stay. Prefer
deleting a restriction to adding a feature, and record every weaker guarantee
as an explicit known issue.

### 8.1 End state, worked backwards

**CMRU release (T2):**
- `release-preconditions`, which includes the `CHANGES.md` reader and KI-141;
- `ref-probe` and `installed-wheel-live`;
- **one** Assay lane run: one baseline feeding R0, `whole_target` R1, and the
  import-break canary with the baseline as its control.

R2 is the T3 lane `r2`.

Smallest change set: WP-CMRU step 1 (CMRU config, tools and tests) plus Assay
B178. Restrictions **deleted** rather than features added:
- the CMRU `assay.toml` base consistency check;
- the `coverage` lane and its pytest-cov canary;
- the KI-79 version window (the marker stays).

No Assay restriction has to be relaxed for this end state. `whole_target`
refusing `judge.base` stays, because the coupling it breaks is removed on the
CMRU side.

### 8.2 Classification

| Guard | Where | Guards against | Proposal | Saving | Given up (known issue) |
|---|---|---|---|---|---|
| `whole_target` refuses `judge.base` as inert config | `assay/src/assay/config.py:2645-2660` | (a) accidental: a base that silently does nothing | **Keep.** Delete CMRU's `assay.toml` base consistency check instead (`cmru/tools/check_assay_baseline.py:248-251`); the R1/R2 base drift it guards no longer exists once R1 has no base. | unblocks WP-CMRU | none |
| Separate canary control materialization | `assay/src/assay/canary.py:766-810` | (a) accidental: a nondeterministic materialization | **Relax** via B178 (automatic) | one full suite per R3 lane run | **LR-KI-1**: "A canary whose control is the lane's baseline does not detect a nondeterministic snapshot materialization." |
| Post-run dirty-tree / HEAD-changed check | `assay/src/assay/runner.py:3802-3806`, `git.py:862` | (a) accidental: tests writing into the tree | **Keep** (cheap, catches real bugs) | — | — |
| `allow_excluded = false`, unmeasured-target and remapped-line refusals; empty-delta and `--require-candidates` refusals | Assay judge | (a) accidental vacuity | **Keep**: they are what make `whole_target` trustworthy | — | — |
| Run-Gate `clean_tree` refusal at lane start | Run-Gate | (a) accidental | **Keep** (`--allow-dirty` exists) | — | — |
| Assay self-hosting: exact-OID private clone, hash-pinned offline build closure, two venvs, `--require-judge-provenance`, then the whole `tests` suite against the wheel | `assay/tools/tester-unified-gate.sh` | the clone and offline closure: (a) worktree residue entering the wheel; provenance and the full re-run: largely (b) attestation | **Keep** the clean-clone build and the cheap independent witness (one test file, `:457-463`). **Narrow** the installed-wheel suite to packaging tests (operator D5 override, WP-ASSAY) | up to ~1,000 s (UNVERIFIED split) | **LR-KI-2**: "The installed Assay wheel is proven by a packaging subset and the self-hosting witness, not by re-running the whole source suite against it." |
| `safeio` descriptor-relative, `O_NOFOLLOW` reservations | `assay/src/assay/safeio.py` | (b) symlink and parent-swap races | **Keep where it exists** (no runtime cost). **Do not extend it** to new artifact kinds when that costs a feature (for example the Rust `mutants.out/` directory, spike S3); read them plainly. | Rust R-3b effort | **LR-KI-3**: "Directory-shaped ingested R2 reports are read without race-proof reservation." |
| Ingested R2: "never accepts a report it did not watch being produced"; per-mutant diff content check | Assay ingested R2 | the freshness part: (a) a stale report; the content tier: mostly (b) | **Keep freshness.** Drop the per-mutant diff content tier for cargo-mutants if spike S3 shows it costs more than an S change | Rust R-3b effort | **LR-KI-4**: "A cargo-mutants report is bound to its run, not checked mutant-by-mutant against the committed blob." |
| RG-253: protected evidence root, immutable snapshot view, staged candidate, promotion validator, record self-hash, receipt refusal | `DESIGN-verdict-reuse.md` | almost entirely (b): forged or raced PASS records | **Relax to "RG-253-lite"** (open question Q8; detail below the table) | retries: **≥ 2,436.7 s measured** in this wave; same-tree repeats | **LR-KI-5** and **LR-KI-6** (below the table) |
| KI-79 version window | CMRU | operator policy | Replaced by the per-release marker (D4 revised) | — | — |

**RG-253-lite.**
- Run-Gate writes a record after a clean PASS into its ordinary evidence directory.
- The digest is computed over:
  - the selected tree, minus declared prep-only paths;
  - project and central config, lane argv/env, image ID, Run-Gate and Assay version and source digest;
  - the resolved R1 base.
- SCM-derived versions are excluded only for lanes that declare they read no version string.
- No promotion protocol and no immutable-view requirement. The same live-lane vetoes as RG-253 apply (R2, probes, network/host lanes).
- Effort: **S–M**, against RG-253's L.

Known issues RG-253-lite would record:
- **LR-KI-5**: "A PASS record in the evidence directory is trusted as written; anything able to write there can fake one (by design: the operator controls hosts and lanes)."
- **LR-KI-6**: "A tracked file changed and restored while a lane ran is not detected."

## 9. Cheap wins beyond tiering

The candidates below cut runtime or IO for non-R2 work. Host prerequisites:
- btrfs `/srv/cow` (`MDT_COW`) exists on r1002 and v1001 (host-pipeline plan §1.1);
- the controller has `/srv/mdt-cockpit-cow`, already bind-mounted into lane containers;
- Run-Gate forwards `MDT_COW_ROOT` and `COWTREE_MODE` into lanes (seen in this branch's selftest docker argv).

| # | Candidate | Exists today | Missing | Expected saving | Risk | Effort | Needs a feature? |
|---|---|---|---|---|---|---|---|
| **W1** (first) | **Test temp trees on the CoW area** (`RUN_GATE_TMPDIR`) | Run-Gate exports the state root to every Assay lane's judge as `RUN_GATE_TMPDIR` (`run_gate.py:13905-13917`, SPEC RG-98); a lane maps it onto pytest's `TMPDIR` with `[lanes.<n>.infrastructure] TMPDIR = "required-env:RUN_GATE_TMPDIR"` (run-gate-project does, `run-gate-project/assay.toml:56,101`); `[environments.<n>].state_root` is configurable (SPEC). | The CMRU shard/temp fix is a handoff TODO only (`HANDOFF-STATE-20261107.md:60`; `reports/session-audit-3.md` row 2). No `state_root` in `run-gate.root.toml`; CMRU's `assay.toml` has no infrastructure fact; command lanes (shards, `coverage`) receive no `RUN_GATE_TMPDIR`. | Moves pytest temp trees (**measured** ~2.5 GB per R2 shard, handoff :60) off the container's overlay root disk (r1002 root is 40 GiB). Runtime effect UNVERIFIED (btrfs vs ext4/overlay2 were similar for copies, methodology "Environment levers"). | Low. The state root must stay outside the judged worktree (RG-98 rule). | S | No |
| **W2** | **Bytecode cache outside the tree**: replace `PYTHONDONTWRITEBYTECODE=1` with `PYTHONPYCACHEPREFIX=<RUN_GATE_TMPDIR or lane temp>/pycache` | Lanes set `PYTHONDONTWRITEBYTECODE=1` so the snapshot stays clean (`cmru/assay.toml` `[lanes.cmru]` env). | Nothing in the tools; one env line per lane plus an A/B measurement. | Every CLI subprocess in a test stops recompiling imports. Saving **UNVERIFIED**; the methodology estimates ≈ 4–5 s per mutant for unchanged test bytecode. Measure once on cmru `r0-r1`. | Low. The cache is outside the tree, so the dirty check is unaffected. R2 clones have unique paths, so there is no stale aliasing. | S | No |
| **W3** | **Slowest tests and git-repo fixtures**: one `pytest --durations=50` survey per large suite; turn per-test `git init` + commits into a session template copied per test (methodology guideline 10) | Guideline exists | The survey; per-project fixture changes | Possibly large for cmru (6,486 tests) and assay; **UNVERIFIED** | Low | S survey, M per fix | No |
| **W4** | Per-lane snapshot creation | Assay builds one sealed template per run and gives the baseline and control cheap cowtree clones when the CoW area is set (`assay/src/assay/isolation.py:819-823`); the 59 s figure in `cmru/assay.toml` dates from 2026-10-21 | After WP-CMRU only one Assay lane per gate builds a template | ~1–2 template builds per gate saved by WP-CMRU itself (**estimated**) | — | none | No (by design) |
| **W5** | Keyed read-only dependency venv | `tester-unified` already bakes all test dependencies in `/opt/tester-venv`; lanes only `pip install -e ../assay --no-deps` (seconds), as AGENTS.md requires | — | negligible | — | — | **By design** (LR-KI-7: "Same-repo Assay is installed editable per lane; a keyed venv would contradict AGENTS.md and saves seconds.") |
| **W6** | Plain tmpfs (`/dev/shm`) for test temp | Assay R2 has `tmpfs-worker` (B223) | — | small (small suites take 30–60 s) | tmpfs is charged to the 2 GiB lane memory cap | — | **Not recommended** (LR-KI-8: "Test temp is on disk-backed CoW, not tmpfs, because tmpfs counts against the lane memory cap.") |
| **W7** | Fuzz corpora as keyed trees | — | — | none (corpora are small) | — | — | By design: tracked seeds + ignored output |

Recommended order: W1 (inside WP-CMRU and the shard branch), then W2 (A/B
measured in WP-CMRU), then W3 (survey before deciding on fixture work). W4–W7
are left as by-design notes.

## 10. Verification boundary

**Verified** by reading files and logs:
- every release time in §1.1 and every retry row in §1.4 (re-verified by the review);
- the CMRU lane order, the `whole_target` equivalence, the R2 base coupling and the canary's instrument;
- KI-79/KI-141;
- run-gate's `selftest` source list vs `assay-r1`'s source roots;
- the class C/D facts (debian-install-v2, iogovmon, test-policy, topos);
- B178's existence;
- the `remote-gate` capabilities, the queue's behaviour and its disabled flags;
- the Buildkite seam status;
- the host-sat duration medians;
- the r1002 host profile.

**UNVERIFIED:**
- the CMRU and Assay phase splits (so step-2 savings are ranges);
- the CIU R2 duration;
- v1001 RAM;
- whether `local_baseline_facts.py` can prepare CMRU's R2 facts from bundled tags on a host;
- whether nyxloom's `base = "main"` release R1 is vacuous in a real run;
- Buildkite's skip-intermediate behaviour for tag builds;
- whether moving the CMRU prep reader out of the Assay argv keeps the 100% floor;
- whether one central `state_root` value fits every host's CoW mount path (W1);
- the runtime effect of W1 and W2;
- the size of the suites' git-fixture cost (W3).
