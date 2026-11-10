# Design: estate lane rework by test tier (2026-11)

**Status: DESIGN, rev 4** (2026-11-10). Rev 4 folds in re-review 3
(`reports/test-methodology-REVIEW3.md`: N7 campaign resume, N8 host guards,
S11–S15, nits). Rev 2 folded in the independent Opus
review (`reports/test-methodology-REVIEW.md`, FIX-FIRST, B1–B7 plus nits) and
the operator's answers after that review (`reports/test-policy-REQUIREMENTS.md`,
last section), plus the operator's guidance to relax adversarial guards, think
from the end, and look for CoW/tmpfs wins (§8, §9). Rev 3 folds in the
re-review (`reports/test-methodology-REVIEW2.md`, N1–N6, S1–S10, nits) and the
operator's decisions on it (§7, round 3). No executable change.

How this document relates to the others:

- **Doctrine** (why): `nyxloom/reference/TESTING-METHODOLOGY.md`,
  `## Test tiers by event (T0–T3)`.
- **Normative adoption contract**: `run-gate-project/CONSUMERS.md` +
  `LANE-AUTHORING.md`, written by WP-DOC (§4).
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
- **R2 base coupling.** `whole_target` refuses `judge.base` (`assay/src/assay/config.py:2645-2660`). `tools/check_assay_baseline.py:248-251` hard-reads `lanes.cmru.judge.base` and requires it to equal the latest published ancestor tag (`:492-497`). The R2 base must therefore move to the R2 lane. At T3 it is the newest cmru tag with a recorded R2 PASS (§3.4, operator round 3), which today is `cmru-v6.1.0`: the pin is not stale but correct, because R2 has not passed since 6.1.0.
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
| Per-lane duration per host | `logs/host-sat-results.tsv` (896 rows: host, lane, verdict, seconds) | **measured** medians of the queue's wall time per run (60 s poll granularity, `POLL_S=60` at `host-sat-queue.sh:28`; not pure lane durations), r1002 / v1001: `pwmcp:r1` 437 / 478 s; `run-gate-project:selftest` 254 / 348 s; `libraries/iobench:r0-r1` – / 283 s; `cli-extended:r0-r1` 130 / 157 s; `topos-suite` 130 / 158 s; `cgprofile:focused` 67 / 94 s. v1001 is ~1.2–1.4x slower. |
| Per-lane peak memory, cores, duration | Run-Gate footprint manifest `run-gate.footprint.json` (SPEC R-44, `run-gate footprint` verb), distilled from `.run-gate/history.json` | Committed only for run-gate-project (example: `assay-r1` median peak 743 MB, 0.79 cores, 185 s). Every other project has none (gap). |
| R2 campaign size | Assay progress/verdict artifacts in `remote-gates/<run>/`, cmru KI entries | Per campaign; no manifest. cmru R2 budget 7 h; tnt 24 h. |
| Host profile | `reports/R1002-REINSTALL-2026-11-04.md:8`; `HANDOFF-STATE-20261107.md:483`; host-pipeline plan §8.4 proposes `hosts/<host>/profile-<date>.json` | r1002: 4 vCPU, 7.75 GiB RAM, 16 GiB swap (measured). v1001: 4 vCPU; `dev-gates.slice` `MemoryMax=6G` (set by hand); RAM UNVERIFIED. |
| Host guards against overlapping triggers | `remote-gate`'s start-time PSI wait and refusal (memory some / IO full, exit 75 after `--wait-psi`, `remote-gate.py:518-522, 612`); the gates slice's kernel `MemoryMax` (OOM inside the slice, not a refusal); for queue launches, the per-host flock (`host-sat-queue.sh:189`) | **Not active on hosts:** Run-Gate count admission (`[admission]`, `max_concurrent`; `enabled` defaults false, `SPEC.md:683`; no vbpub `run-gate*.toml` declares it; remote-gate's `admission_check` is on only for dstdns, `remote-gate.py:103`), and the gates-slice "admitted" message, which never refuses (`run_gate.py:6069-6080`) and does not run at all because remote-gate and `run-lane.sh` set `RUN_GATE_PROFILE=off`. |

## 2. Features needed

### 2.1 Required

| ID | Tool | What | Smallest implementation | Effort | Blocks |
|---|---|---|---|---|---|
| **B178** (existing Assay backlog, `assay/nyxloom-trove/4-backlog.md:163,13457`; designed in `assay/nyxloom-trove/reports/ASSAY-DESIGN-OPUS-2026-10-30.md` §§6.3, 7) | Assay | R3 canary control taken from the lane's own passing baseline. **Automatic when sound** (operator); no config key. | Pass the baseline unit and judgement from `runner.py` (baseline at ~:4583) into `canary.py` `run_isolated_canary` (:636; control block :766-810) and `run_isolated_canaries` (:957), see below. | M (3–5 days) | WP-CMRU step 2, WP-ASSAY step 2; every Assay R3 lane |
| **RG-F1** (file with the next free RG id; RG-254 and RG-255 are taken on unmerged branches) | Run-Gate | Read-only `run-gate policy-check`. **Required** (operator answer 3) and built **last**. | An extension of `cmd_validate_pointers` (`run_gate.py:11174`). Rules and derivation below. | M (2–3 days) | the closing estate audit |
| **WP-T3** | release tooling (`scripts/netcup/runner/`) | T3 queue: release-triggered, supersede, capacity-aware, result ledger, attention hook | Extend the host queue; see §3. | M | everything moved to T3 |

**B178 details.**
- **Skip condition.** The control is skipped only when all of these hold:
  - the baseline has an R0 PASS and, where the lane declares R1, an R1 PASS (backlog B178: "already passed R0 and R1"; `runner.py` already refuses an uncovered-line control without an R1 PASS);
  - it left no `post_reason`;
  - its contention observation is complete;
  - it ran the **identical `CommandPlan`**. R2 lanes build a different `baseline_plan` with `ASSAY_LIVENESS_EVENTS_ENV`, so the plans are not equal by construction.
- **Otherwise** the canary runs a separate control and records the reason.
- **Recording.** `control_source` goes into the attempt evidence (verdict schema bump).
- **Docs.** Fix `assay/docs/CONSUMERS.md:2826-2832` ("runs three commands") and the `canary.py:650-690` docstring ("two independent P22 snapshots").
- **Consumers.** Notify dstdns of the schema bump; its JS R3 lanes gain the most, since each control repeats a cold `npm ci`.
- **Verifier.** `assay verify` (`assay/src/assay/verify.py`, the independent artifact validator that checks canary evidence) is in WP-B178's scope, so that a verdict with `control_source = baseline` still verifies. (The re-review cited B180 for this; B180 is a different backlog item, so the scope is named here, not by id.)

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
| **cmru T3 R2 entry point** | cmru | A **container command lane `r2`** in the `cmru-mutation` environment. It builds the baseline facts in-lane from the checkout's own `cmru-v*` tags (the semantics of `tools/local_baseline_facts.py`, which exists for exactly this: "a mutation campaign started OUTSIDE a release … on a host with no publisher credentials"), takes the §3.4 base as an explicit input, and runs the campaign. No `required_env = ["CMRU_ASSAY_BASELINE_FACTS"]`. | Operator D4 revised: every project needs a runnable-alone T3 R2 lane. Today `mutation` needs facts that only the aggregate `gate` prepares, and `run_release_gate.py --mutation-only` cannot run on a host: `_plan_gate` queries origin with publisher auth (`run_release_gate.py:1454-1457`), host checkouts are bundles without origin or credentials, and the orchestrator is a bare-host lane. "Published" is checked once on the controller (`git ls-remote --tags origin`) before a tag is enqueued; with local tags there is no credential to keep out of the container. | S–M (inside WP-CMRU) |

**Deferred (operator round 3):** **AS-2**, several canary mechanisms in one Assay lane. After WP-RG and WP-CIU the only changed-lines Assay lane at T2 that needs both canaries is nyxloom `tester-unified`; it gets a second Assay lane as a known cost. Revisit at Rust R-4.

### 2.3 Evaluated and not recommended now

| Idea | Verdict | Reason |
|---|---|---|
| A shared suite execution feeding Assay R0/R1 and the coverage floor | **No feature** | Assay `whole_target` already makes the one run the floor (§1.3). |
| Canary only on judge-input change | **Policy** | Methodology rule 6: applicable canaries at T2. B178 makes each canary cost one transform. |
| A tier key on lanes | **No** | Tiers are derived (RG-F1). |
| RG-253 verdict reuse | **Defer** | 0 s per normal release; ≥ 2,436.7 s of retry waste whose causes are fixed; heavy prerequisites (§1.4). |
| RG-253-lite | **Defer** (operator round 3, Q8) | The retry causes are fixed (KI-144); re-measure same-tree retries over the next releases first. Constraints if it is ever built: §8.2. |
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
| Capacity | Each host's loop pulls the next entry whose footprint (peak memory) fits the host's live gates-slice headroom. Keep the start-time PSI refusal and the slice `MemoryMax` (count admission is not enabled, §1.5). | to build (v8.1 byte budget is per host only) | Agents take one job each; capacity is agent count and tags, not RAM-aware. One agent per host = one T3 lane at a time. |
| Results | `remote-gates/<run>/result.json` + a T3 ledger row | to build | Build record + artifacts; `bk-lane.sh collect` |
| Attention | ctlstat reads the ledger | to build | Notifications + a collector |
| New dependencies | none | a service to run, secure and port | outbound HTTPS from hosts, an account and plan limits, agent token custody, host access to the git remote (hosts receive bundles today) |
| Effort | **M** (T3 mode in the per-host loop + ledger + ctlstat rule) | **L+**: a service inside a tool that is being ported to v8 | **M–L** + external setup; live seams still unproven |

### 3.3 Decision (operator round 3: option (a))

**(a): extend the host queue and `remote-gate`, one loop per host.** It meets every requirement with the code already in use and needs no new service, account or host network access.

- **Explicit deviation from D-110.4.** `REMOTE-LANES-BUILDKITE.md` records D-110.4 ("Buildkite is the queue, the trigger, the log store and the artifact store") and, on 2026-09-02, rejected a host-side queue as "the CI system D-110 refused to write". Option (a) is such a queue. It is chosen anyway because `remote-gate` works and ran every T3 campaign of this wave, while Buildkite has never created a build and seams 1 and 3 have not started. That file now carries a dated "superseded for T3" header; Buildkite stays the scale-out path.
- **The stable interface is the entry format and the ledger** (§3.4). Lanes, release steps, the CMRU marker, KI-141's closing evidence and ctlstat depend only on them. A service or Buildkite can later replace the per-host loops without changing any of these.
- **Cross-trigger safety** (what `concurrency: 1` gave in the Buildkite design) is weaker here, and that is accepted. The guards that really exist are `remote-gate`'s start-time PSI refusal, the gates slice's kernel `MemoryMax`, and the per-host queue lock for queue launches (§1.5). Run-Gate count admission (`[admission]` + `run-gate admission set --max-concurrent N` on the host + `admission_check` in the remote-gate repo profile) exists but is not enabled for vbpub. Known issue **LR-KI-10**: "A manual `remote-gate` run can overlap a queue run on one host; only PSI at start and the slice cap bound it." Rule: manual T3 work is queued as an entry file, not launched by hand. Enabling count admission is the one-table follow-up if overlaps are seen; nothing new is built.
- **(b)** is not chosen: a service inside a tool that is being ported to v8, with no cross-host scheduler in the v8 spec.
- **(c)** is the scale-out path, for example if a third or fourth host is added, or if a browsable build history is wanted. It calls the same `run-gate <lane>` and writes the same ledger rows.

Keep the gate tool single-host. In v8, `ciu gate` admission and the footprint
manifest will be the per-host inputs the loops read.

Two homes, agreed with the host-pipeline plan:
- **The queue code** moves from the release-program directory into the repo next to `remote-gate.py` (`scripts/netcup/runner/t3-queue`, tested like `remote-gate.py`). The host-pipeline plan's B28 lists the queue as an external operator tool.
- **Its state** (entries, ledger, pause flags, per-host logs, collected campaign directories) stays on the controller in the release-program directory.

### 3.4 Mechanism

**T3 lane table.** One committed file, `scripts/netcup/runner/t3-lanes.toml`. Each row has:
- `tool`, `tag_glob`, `project_dir`, `lane`, `kind` (`r2` | `fuzz` | `probe` | `variation`);
- `scope` (`changed_lines` | `whole_target`) and, for `changed_lines`, `floor_tag`: the base used when no R2 PASS is recorded yet (cmru: `cmru-v6.1.0`).

Footprint values and budgets are not copied into this file. The footprint comes from the project's committed `run-gate.footprint.json` where one exists, else from the lane's declared `resources.memory`. The budget is the lane's declared `budget`. Both are read from the lane declaration in the project `run-gate.toml` and `run-gate.root.toml` (lane, then environment, then central `resources`), resolved the way `remote-gate.py` `lane_checks` already resolves a lane (`:322-345`). `run-gate --list` prints neither (`run_gate.py:17568-17690`).

**Entry (stable interface).** One file per entry, `t3/entries/<tool>-<tag>-<lane>.json`, with `tool, tag, commit` (the peeled tag), `lane, project_dir, base, budget_s, footprint_mib, state, host, run_name, image, attempts`. `state` is one of `pending`, `running`, `resume`, `done`, `superseded`, `infra`. Only the controller-side enqueue step creates entries; only a host loop claims and advances them. Retrying an `infra` entry means deleting its file (after the attention item); enqueue then recreates it. There is no automatic retry.

**Base of a changed-line R2 (operator round 3).** At the entry's **first claim**, `base` = the newest tag of that tool with a **PASS ledger row for that lane**, or the row's `floor_tag` when none exists. It is stored in the entry; relaunches and re-supervision reuse it, and the ledger's `base` is that value. Computing it at first claim, not at enqueue, lets a newer entry follow an older run that finished in between. The loop passes it as `--base <tag>` for delegating lanes (`judge.base_source = "request"`; `remote-gate run … --base` exists, `remote-gate.py:5`) and as the explicit base input of CMRU `r2` (§2.2). `remote-gate` resolves `--base` to a SHA (`remote-gate.py:303-308, 586`), so CMRU's check accepts a commit that a published `cmru-v*` tag points to and logs the tag name. A superseded or skipped release is therefore still covered: its delta lies inside the next judged range. This matches AGENTS.md ("older releases … are never retro-run"): the newest release runs, over a wider range. `whole_target` R2 lanes (cli-extended, augeaswrap, iobench, pwmcp, assay `self-qualification`) have no base. This amends D8's wording: "previous release tag" → "last R2-covered release tag".

**Enqueue (controller, each tick).**
1. For each row, find the newest tag. Check that it is published (`git ls-remote --tags origin <tag>`, once, on the controller; hosts have no origin).
2. If the ledger has no PASS/FAIL row for (tool, tag, lane) and no entry exists for it, create a `pending` entry.
3. Mark older `pending` entries for the same (tool, lane) `superseded` (ledger row `SUPERSEDED`). A `running` or `resume` older entry continues, because its result is still that release's evidence; the newer entry's base is computed at its first claim, so it follows the older run's result if that finished first.

**Why campaigns do not go through `remote-gate run` (re-review N7).** `remote-gate run` builds a throwaway checkout per run (`CK=rg-<name>-<sha12>`, `remote-gate.py:447-451`) and its EXIT trap deletes it (`rm -rf -- "$CKDIR"`, `:484-499`). Every resume store lives inside that checkout: Assay-kind lanes keep state under `repo/.run-gate` because no environment declares `state_root` (`run_gate.py:13205-13230`); cli-extended `r2` uses `--state-dir .assay/mutation-state-r2` (`libraries/cli-extended/run-gate.toml:25`); CMRU resumes from `--evidence .assay/mutation-cmru.json --resume` (`cmru/run-gate.toml:264-270`). A relaunch would start from the first candidate again. Two fixes reuse existing code: (a) run campaigns through the existing persistent-checkout supervisor, or (b) make `remote-gate` keep the checkout when the lane exits 4. **(a) is chosen because it is simpler: it is no new code path.** `run-r2-campaign.sh` already runs in the persistent `$RUNNER_HOME/checkouts/<commit>`, already relaunches the lane while it exits 4 and its `.assay/progress-*.jsonl` grows, stops after 2 stalls or `R2_MAX_ATTEMPTS` (default 12), and writes `final-exit`, `campaign-summary.json` and the lane's own `verdict.json` (`scripts/netcup/runner/run-r2-campaign.sh:1-6, 39-70`). Both R2 command lanes write the progress file it counts (cli-extended `--progress .assay/progress-r2.jsonl`; CMRU `--progress .assay/progress-mutation-cmru.jsonl`). Option (b) would add a keep-or-delete branch and a reuse path to `remote-gate`'s job script.

**Per-host loop (one per host; T3 first, then the main-branch sat rotation, in the same loop and under the same lock).**
1. **Claim.** First any `resume` entry pinned to this host. Otherwise the oldest-release `pending` entry whose footprint fits: `memory.max − memory.current` of the host's gates slice (read live by the probe; the probe today reads only `MemAvailable`, `host-sat-queue.sh:63-71`, so it is extended), and memory PSI some ≤ 25 and IO PSI full ≤ 25 (the existing limits). CPU is not a refusal signal (operator: overcommit by design). The footprint is the manifest's recorded `memory.peak` where one is committed, else the lane's declared `resources.memory` (every container lane declares one, RG-20). `memory.peak` includes page cache, so the fit errs conservative. The claim check is the only placement guard; nothing on the host refuses a second lane by count (§1.5, LR-KI-10). An entry whose footprint exceeds every host's `memory.max` gets ledger row `UNPLACEABLE` (attention). The claim writes `host`, `base` and `commit` into the entry; from then on the entry is pinned to that host.
2. **Launch, campaign kinds (`r2`, `fuzz`).** Ship the tag's commit to the host as a bundle that includes the tool's release tags and a branch head at the tag's commit (for example `refs/heads/t3/<tool>-<tag>`; `sync-from-devcontainer` refuses a bundle without `refs/heads/<branch>`). Import it with `sync-from-devcontainer` into `$RUNNER_HOME/checkouts/<commit>`, then start `run-job t3-<tool>-<tag>-<lane> -- bash run-r2-campaign.sh t3-<tool>-<tag>-<lane> <commit> <project_dir> <lane> [--base <base>]`. Whether `sync-from-devcontainer`'s `git clone --single-branch` from such a bundle carries the tags into the checkout (CMRU `r2` needs them) is **UNVERIFIED**; WP-CMRU part 5's rehearsal checks it.
3. **Launch, other kinds (`probe`, `variation`).** `remote-gate run <host> <worktree at the tag> <lane> [--base <base>] --detach`, run name `t3-<tool>-<tag>-<lane>-<host>`; these need no resume. Both launches use **main's** runner scripts and `remote-gate.py` (today the queue copies `remote-gate.py` from the lane worktree, `host-sat-queue.sh:145`, which at a release tag is an old copy).
4. **Watchdog.** Each attempt is bounded by Run-Gate's own hard `budget`. The loop's per-entry watchdog is `R2_MAX_ATTEMPTS × (budget + margin)` for campaigns and `budget + margin` otherwise, not the sat rotation's `LANE_TIMEOUT_S=3600` (`host-sat-queue.sh:27`).
5. **Collect.** For campaigns, when `campaigns/<name>/final-exit` appears, copy `campaigns/<name>/` back to the controller (`verdict.json`, `campaign-summary.json`, `attempts.log`, attempt logs) and the lane's declared `artifacts`. For other kinds, `remote-gate collect`. Then append the ledger row: final exit 0 → PASS, 1 → FAIL, and 4 → `INFRA:stalled` or `INFRA:max-attempts` according to the `stopping: stalled=… attempt=…` line in `attempts.log`; anything else → `INFRA:<exit>`.
6. **Re-supervision.** If the host or the job died (no `final-exit` and no live job), the entry goes to `resume`, stays pinned, and the next claim on that host starts the same `run-r2-campaign.sh` again. The campaign continues from the checkout's state; its per-campaign lock refuses a second supervisor.

**Footprint feedback (re-review S13).** Hosts run lanes with `RUN_GATE_PROFILE=off` (`remote-gate.py:589`, `run-lane.sh:15`), so run records carry no resources and no `memory_peak_bytes`. Placement therefore uses committed manifests and declared `resources.memory` only, and the ledger's `peak_mib` stays empty. Known issue **LR-KI-11**: "T3 placement uses declared memory, not measured peaks, until host runs are profiled." No history is copied back in the first step. Known issue **LR-KI-12**: "Persistent per-commit checkouts and their `.assay` resume state are retained until a later cleanup feature deletes them when their last entry is done."

**Ledger (stable interface).** `logs/t3-results.tsv` with columns `utc, tool, tag, commit, lane, base, host, image, run_name, verdict, seconds, peak_mib, run_dir, backlog_id`. `image` is the tester image ID the run used (remote-gate's job header already prints it; for campaigns, the probe records `docker image inspect` of the image `run-lane.sh` uses). With `commit` and `image` the entry is commit-addressed as methodology "Synchronous versus retroactive runs" requires. Verdicts:
- PASS, FAIL;
- RESUME (a re-supervision after a host or job restart);
- INFRA:<reason>;
- SUPERSEDED, UNPLACEABLE, CANCELLED.

**Attention (ctlstat rule).** For each T3 row, the latest tag must have a PASS row within N hours of its release (default: 24 h, or 2× the lane's median duration plus 4 h, whichever is larger; a campaign that is still running and whose `attempts.log` still shows progress raises "slow", not "missing"). Otherwise raise an attention item:
- "missing";
- "FAIL → backlog item", which clears when a `backlog_id` is recorded against the row;
- or "INFRA → retry/fix".

This also gives the per-release "R2 owed" record its closer: the cmru marker is closed by the matching PASS row, and KI-141's closing change cites one (WP-CMRU part 0).

**Host profile.** The plan's `hosts/<host>/profile-<date>.json` (§8.4) gets three fields: `cores`, `ram_bytes` and `gates_slice_memory_max`. Until the profiles exist, the extended probe reads the gates slice's `memory.max`/`memory.current` and `nproc` live.

**Agreement with the host-pipeline plan:**
- Its §8.2 "green" status source gains the T3 ledger: last verdict per (tool, tag, lane).
- Its D10 (both hosts default to run-gate 25.4.1; accepted by the operator with HP-D2..D4 and D6..D8) is a precondition for observer lanes (R2 with `resource_observer = "cgprofile"`). Observer lanes still record no footprint while host launches set `RUN_GATE_PROFILE=off` (LR-KI-11).
- Hosts paused by the queue's flags also pause T3.

### 3.5 Smallest first step

Add the T3 mode to the per-host loop for **one** lane: `libraries/cli-extended` `r2` at the newest `cli-extended-v*` tag. That is the campaign run by hand today (`clix-r2-060-r1002`). It is `whole_target`, so the base rule is exercised by the second row (run-gate-project `assay-r2`, `base_source = "request"`).

The step includes the entry files, the supersede rule, the ledger row, footprint-based claiming on both hosts, the campaign launch through `run-job … run-r2-campaign.sh`, the watchdog, re-supervision and one ctlstat attention rule.

Acceptance:
- an older pending tag is superseded;
- an entry is claimed by the host with room, and refused entries wait;
- a budget exit with progress resumes on the same host, **and the next attempt's progress continues from the previous attempt's last candidate** (checked on the progress file, not on the relaunch);
- a missing result raises attention;
- a red result names the backlog to file into, and recording the id clears it.

The CMRU `r2` row is enabled only after this campaign path exists: KI-141's owed campaign (6.1.0..7.2.x at a 7 h budget) will not fit in one budget window.

Then add rows project by project as each per-project package lands its `r2` lane.

## 4. Per-project work packages

Every package is **one pass for one project** (operator answer 4). It covers lane config, release pointer, trove pointer, a runnable-alone `r2` lane (or a known issue), canaries per rule 6, adoption docs and docs-sync tests, in one reviewed change. A package whose R1 is a pytest-cov whole-project floor (ciu, the small libraries) adds the coverage-inventory assertion on that run's `coverage.json` (D16). Each is gated through the project's registered lane in `tester-unified`.

Host rules: one gate container at a time per host, no cockpit pytest, no big local processes; heavy runs go to the hosts.

### 4.1 Table

| WP | Project | Change (one pass) | Expected saving (label) | Risk | Needs |
|---|---|---|---|---|---|
| **WP-DOC** | run-gate docs only | Normative tier contract in `CONSUMERS.md` + `LANE-AUTHORING.md`: tiers, presets, prep-only paths, `[policy.accepted]`, the canary rule, the T3 trigger. Fold the draft `TEST-POLICY.md` (§6). `LANE-AUTHORING.md` §3: `gate` excludes R2; `mutation` → `r2` at T3. `REMOTE-LANES-BUILDKITE.md` §1 points at the T3 queue (its superseded-for-T3 header lands with this design). Note the `AGENTS.md:197` CPU-PSI wording that conflicts with "CPU is not a refusal signal". Docs-sync test, in a **new** test file (not `tests/test_run_gate.py`): the methodology's preset names are documented in `LANE-AUTHORING.md`. Then close draft branch `test-policy-doc-20261109` (`b8c00b622`) **unmerged** and remove its worktree. | — | docs-sync tests | — |
| **WP-CMRU** | cmru | Step 1, a rehearsal patch release, has the parts listed below the table, including CMRU's share of W1–W3. Step 2: after B178 ships, nothing to configure (automatic). | Step 1 ≈ 1,670 s (**estimated** from inferred 751 + 919) → T2 ≈ 1,320 s. Step 2: one control suite, 550–600 s more likely than 400 (review bound) → ≈ 700–800 s. | KI-79/KI-141 tests and docs; junit failure naming; `tests/test_assay_lane_inventory.py` must cover lane `cmru`; R2 facts from bundled tags on a host (proven by the host rehearsal in part 5) | Rehearsal release; one host `r2` run; B178 for step 2 |
| **WP-ASSAY** | assay | (1) **Narrow the installed-wheel R0 now** (operator override of D5), keeping a cheap wheel-contents check; details below the table. (2) T1: `self-qualification-preflight` for source changes, plus `tester-unified` when `gate/`, `tools/` or packaging change; otherwise `gate/tests`, the analysis lane and the independent/SQL witnesses would surface only at release. (3) T3 lane = `self-qualification` (R2). (4) B178 then halves the provisional lane automatically. (5) Assay's share of W1–W3. WP-ASSAY owns the `assay/docs/CONSUMERS.md` and `assay/CHANGES.md` edits that WP-B178 needs, or the two are sequenced (WP-B178 after WP-ASSAY). | (1) up to ~1,000 s (UNVERIFIED split); (4) ~300–500 s (**estimated**) | Packaging regressions outside the subset that the wheel-contents check does not see (LR-KI-2) | B178 for (4) |
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
| **WP-NYXLOOM** | nyxloom | Release R1 base → previous release tag (`base_source = "request"`); import-break + uncovered-line canaries (changed-lines R1), as two Assay lanes because AS-2 is deferred (a known cost); a full-suite `r2` lane (today only `session-extract` claims R2, over a subset) or known issue | — | Hypothesis `gate` profile before R2 (methodology §Hypothesis) | — |
| **WP-MDT** | mdt-cockpit | Map: smoke/release/oci-push/image-runtime-built = T2; `assay-full` (R0–R3) = T3 R2 lane; `vm-system` = T3/manual | — | — | — |
| **WP-MAP-*** | class D and E projects, tnt, scripts/truenas, wings-patchstack, shared-ramdisk-depot-manager, cowtree, worktree | One pass per project: tier mapping + known issues (no R3 / no R2 adopted), live lanes = T2 only where the release names them | — | many small edits | — |
| **WP-B178** | assay (code) | Implement B178 (§2.1) | enables WP-CMRU step 2, WP-ASSAY (4) | schema bump; self-hosting | — |
| **WP-AS2** | assay (code) | **Deferred** (operator round 3, §2.2); revisit at Rust R-4 | — | — | — |
| **WP-T3** | scripts/netcup/runner + ctlstat | §3.5 first step (entries, ledger, base rule at first claim, campaigns via `run-job … run-r2-campaign.sh` in the persistent checkout, watchdog, re-supervision, live-slice probe, main's runner scripts), then rows per project; the CMRU `r2` row only after the campaign path works | moves T3 from manual to automatic | host capacity | hosts on run-gate 25.4.1 (D10); the r1002 window (host-pipeline plan D1) |
| **WP-PC** | run-gate (code only) | RG-F1 (§2.1); its docs come from WP-DOC | — | — | after WP-DOC and the per-project packages |
| **WP-ENV** | `run-gate.root.toml` only | One package for both central edits, so WP-IO and WP-TMP do not touch the file in parallel: (1) W0, after its A/B: `io_sync = "relaxed"` in `[environments.tester-unified.resources]` (`run-gate.root.toml:29`; environment scope, not a root default); (2) W1: `state_root` on the CoW area per environment (in-container path of `/srv/cow` / `/srv/mdt-cockpit-cow`, UNVERIFIED whether one value fits all hosts). Not in parallel with packages that are mid-gate, because history splits by mode. | IO and write volume on shared hosts; root-disk IO and space (**measured** ~2.5 GB/shard); runtimes UNVERIFIED until measured | LR-KI-9; RG-98 rule (state root outside the worktree) | the W0 A/B |
| **WP-IO** | measurement only | W0 A/B (§9) on one host. The CMRU environment edit (`[environments.cmru-mutation.resources] io_sync = "relaxed"`) is in WP-CMRU; passing the preload into the Assay self-qualification and CMRU orchestrator child containers is a follow-up in WP-ASSAY / WP-CMRU | decides W0 | — | — |
| **W1–W3 per project** | each project's own package | `TMPDIR = "required-env:RUN_GATE_TMPDIR"` on each Assay lane (command lanes use `TMPDIR="$MDT_COW_ROOT/..."` in their argv); W2 `PYTHONPYCACHEPREFIX` A/B; W3 durations survey. Folded into WP-CMRU and WP-ASSAY for those two projects, and into the other per-project packages where they pay; no separate packages | UNVERIFIED until measured | — | WP-ENV (2) for `state_root` |
| **WP-RG253L** | run-gate (code) | **Deferred** (operator round 3, Q8); constraints in §8.2 | — | — | re-measured retry cost |

**WP-CMRU step 1 parts** (in this order; part 0 comes before part 5 retires anything):
0. **Rewrite KI-141** (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`), keeping its enforcement. The version window `POSTPONEMENT_RELEASE_VERSIONS` (`run_release_gate.py:1060-1062`, checked by `_validate_postponement`, read also by `cmru release --dry-run` through `release_preconditions.py`) stays `((7,2,0),(7,3,0))` until KI-141 closes; it is the only runtime enforcement of "no 7.3.0 without R2". New text:
   - **Fix:** "finish the owed R2 as the T3 lane `r2` on a host, base `cmru-v6.1.0` (the last R2-covered tag); close or justify KI-129/133/134/135/138."
   - **Close when:** "a T3 ledger PASS row for cmru `r2` at a 7.2.x tag whose base is ≤ the last R2-covered tag (`cmru-v6.1.0`), every survivor killed or justified, and the reviewed change that opens the window cites that row; or the operator extends the window and this entry records that decision."
   - **After KI-141 closes (re-review S12).** After part 6, `gate` writes the marker on every release, so every release passes `_validate_postponement` (`run_release_gate.py:1143-1173`; dry run via `release_preconditions.py:86`). Widening the window only to `(7,3,0)` would refuse 7.4.0 again by surprise. The closing change therefore replaces the bounded window with one **open-ended from 7.3.0** (R2 is T3 by design from then on), a small change to the `low <= v < below` test (`:1165-1167`) and its oracles in `tests/test_release_gate_provisional.py`. Any later version boundary needs its own declared precondition.
1. Switch `[lanes.cmru]` to `whole_target` (targets exactly as `cmru-first-release`; the inventory test also covers `cmru`).
2. **Re-home the R2 base.** The R2 lane takes the base as an explicit input (§3.4: the newest cmru tag with a recorded R2 PASS, else `cmru-v6.1.0`). `check_assay_baseline.py` checks that the given base is a published release tag in HEAD's ancestry instead of requiring the highest one, and its `assay.toml` base consistency check (`:248-251`, `:492-497`) is deleted; `source_roots` stays. Update `tests/test_assay_baseline_gate.py`, `tests/test_docs_config_examples.py`, `tools/prepare_assay_baseline.py` and `tools/local_baseline_facts.py`.
3. Retire `coverage`, `canary` and `tools/coverage_canary.py`. Failure naming reads the Assay verdict tail instead of `junit-coverage.xml`.
4. Move `test_w3_prep_changelog_preflight.py` (the KI-23/KI-30 release-start guards over the real `CHANGES.md`) to `release-preconditions` and `--ignore` it in the Assay argv. **Add `release-preconditions` to `_run_locked_gate`'s lane list, first** (it takes seconds; today the release gate never runs it, `run_release_gate.py:1517-1556`), and to CMRU's T1 mapping; otherwise the moved test runs nowhere. Check that the 100% floor still holds (UNVERIFIED).
5. **Add `r2`** as a container command lane in `cmru-mutation` (§2.2): facts from local tags, explicit base, no `required_env` facts. **Rehearse it once on a host** through the T3 campaign path (`run-job … run-r2-campaign.sh` in the persistent per-commit checkout; bundled tags, no origin) and record the result, **then** retire the R2-including `gate`. The rehearsal checks that the release tags reach the checkout and includes at least one budget exit followed by a resume that continues from the last candidate; if the campaign happens to finish in one window, the record says so. Until that run exists the only R2 entry point would be "declared, never run" again.
6. **T2 naming (D4 "`gate` means T2 everywhere").** CMRU's `gate` becomes the release orchestrator without mutation and always writes the R2-owed marker; `cmru.toml` names it. `gate-provisional` is removed, or kept one release as an alias. The marker's `follow_up_lane = "gate"` (`run_release_gate.py:1196`) becomes `"r2"`. The version window stays (part 0).
7. **First-release lanes.** Once `[lanes.cmru]` is `whole_target`, `cmru-first-release`/`assay-first-release` and `_plan_gate`'s first-release branch (`run_release_gate.py:1458-1482`) select an identical lane. Delete them and keep only the first-release R2 follow-up distinction; this halves the target-inventory maintenance.
8. Add an `r0-r1` Assay lane (R0, R1) for T1.
9. CMRU's share of the cheap wins: `[environments.cmru-mutation.resources] io_sync = "relaxed"` after the W0 A/B (`cmru-mutation` does not inherit the `tester-unified` environment setting); W1 `TMPDIR = "required-env:RUN_GATE_TMPDIR"` on the Assay lane and the shard argv; W2 one A/B; W3 one durations survey.

**WP-ASSAY step 1 details.** Narrow `tester-unified` to `gate/tests` plus the tests that exercise the installed CLI/wheel. Keep the accidental-packaging guard (LR-KI-2) at a cost of seconds: compare the wheel's file list with `git ls-files assay/src`, and import every module from the installed wheel. Record one phase-split measurement as evidence. That measurement does not gate the change.

### 4.2 Order and parallelism

**Now, in parallel** (each set touches disjoint files):
- WP-DOC;
- WP-CMRU step 1 (parts 0–9 in order; part 5's host rehearsal before `gate` is retired);
- WP-ASSAY steps 1–3 and 5;
- WP-CIU, WP-CLIX, WP-AUG, WP-IOBENCH, WP-TESTPOLICY, WP-IOGOVMON;
- each WP-SEQ4 project, WP-TOPOS, WP-PWMCP, WP-MDT, WP-NYXLOOM, the WP-MAP-* projects;
- WP-T3 (first step), once its D10 and r1002-window preconditions hold;
- WP-IO (the A/B only);
- Rust R-0 and R-3a (§5).

**Sequenced, because they share files:**
- **run-gate-project:** WP-DOC, then WP-RG (both touch `run-gate-project/`; WP-DOC's docs-sync test goes into a new test file so it does not conflict with `tests/test_run_gate.py`). WP-PC is last anyway.
- **assay:** WP-ASSAY, then WP-B178 (both touch `assay/docs/CONSUMERS.md` and `assay/CHANGES.md`; WP-ASSAY owns those edits).
- **`run-gate.root.toml`:** only WP-ENV edits it, after the W0 A/B.

**After B178 ships:** WP-CMRU step 2 (verify only) and WP-ASSAY (4).

**Last:** WP-PC, then the estate run of policy-check, then the WP-T3 rows for every project.

**Accepted deviations from "one pass per project"** (operator answer 4): run-gate-project has WP-DOC, WP-RG and WP-PC, and assay has WP-ASSAY and WP-B178, because code, configuration and docs are reviewed separately there. They run in sequence, never in parallel.

**Conflict avoidance:**
- WP-DOC owns `CONSUMERS.md`/`LANE-AUTHORING.md`/`REMOTE-LANES-BUILDKITE.md`; WP-PC touches only code and tests.
- Each per-project package touches only its project directory. Exceptions: WP-ENV owns `run-gate.root.toml`; WP-T3 adds each project's row to `t3-lanes.toml` sequentially after that project's package merges.

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
| T2 | sequence `gate` [`lint`, `assay-r3`, `conformance`] | `assay-r3` is the one R0/R1/R3 lane. It **carries the per-crate floor step** (Rust design §B line 59-60, §C line 86) and both canaries: test-break (a panicking `#[cfg(test)]` module), and uncovered-line because R1 is changed-lines (operator D1 revised; a second Assay lane while AS-2 is deferred). |
| T3 | `assay-r2`, `mutants-<crate>-<k>of<n>`, later `fuzz` | Diff-scoped cargo-mutants against the last R2-covered release tag (D8 as amended, §3.4) once adopted; advisory whole-crate shards on the hosts via WP-T3; cargo-fuzz later |

### 5.3 Amendments to the Rust design for the tiers

1. **§E `assay-r2`** "Pre-merge, not in `gate-full`" → T3, against the last R2-covered release tag (§3.4). Never a merge or release gate. Survivors become backlog items. The end-state claim stays "diff-scoped 100% killed" (D8).
2. **§E `gate-full = lint && assay-r1 && assay-r3 && conformance`** → `gate`, `kind = "sequence"` [`lint`, `assay-r3`, `conformance`]. `assay-r1` is T1 only. `assay-r3` gains the crate-floor step.
3. **§E `assay-r3` "both canaries on one declared target"** is kept, as the operator's revised D1 requires. Assay accepts one `mechanism` per lane (`assay/docs/CONSUMERS.md:2829-2834`) and AS-2 is deferred (revisit at R-4), so the uncovered-line canary is a second Assay lane.
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
| R-4 | Canary injectors; `assay-r3` with floors plus a second canary lane; T2 `gate` sequence; revisit AS-2 | R-2 | after R-2 |
| R-3b | cargo-mutants ingestion (milestone 3, T3) | R-2, R-3a | deferred (waived) |
| R-6 | Whole-crate shards through WP-T3 | R-3b, WP-T3 | deferred |

## 6. Corrections for the draft `run-gate-project/TEST-POLICY.md`

Branch `test-policy-doc-20261109`, head `b8c00b622`. WP-DOC folds the draft's content into `CONSUMERS.md`/`LANE-AUTHORING.md` and applies these corrections. The branch is then **closed unmerged** and its worktree (`.worktrees/test-policy-doc-20261109`) removed: its pointer edits to `TEST-POLICY.md` (`CONSUMERS.md:11-14`, `LANE-AUTHORING.md:10-12` on that branch) must not land, because they would create a third source of truth.

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
16. **`:21`, `:57`** — drop "reuse only eligible … PASS records" at T2 and "`r0-r1` reused as eligible at T2": reuse is deferred (D6).
17. **`:242`, `:256`, `:277`, `:283`** — drop the policy-check `reuse` field and its PROPOSED status, for the same reason.

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
| D8 | Rust R2 runs at T3 against the last R2-covered release tag (wording amended by D10; it said "previous release tag"). |

Settled in round 3 (operator, 2026-11-10, after `reports/test-methodology-REVIEW2.md`):

| # | Decision |
|---|---|
| D9 (Q1) | T3 runs on the extended host queue + `remote-gate`, one loop per host, with a placement (claim) function and a results ledger: an explicit, dated deviation from the 2026-09-02 note in `REMOTE-LANES-BUILDKITE.md`, which now carries a superseded-for-T3 header. Buildkite stays the scale-out path. The entry format and ledger are the stable interface (§3.3, §3.4). |
| D10 | A changed-line T3 R2 judges against the last tag with a recorded R2 PASS (else the lane's floor tag), so a superseded or skipped release is still covered (§3.4). |
| D11 | KI-141 keeps its enforcement: the cmru version window stays until a T3 R2 PASS is recorded; the closing change cites the ledger row (WP-CMRU part 0). |
| D12 | cmru `r2` is a container lane that builds its facts from local tags; it is run once on a host before `gate` is retired (WP-CMRU part 5). |
| D13 | Queue timeouts follow the lane budget; budget exits resume on the same host. Rev 4 implements the resume through the existing `run-r2-campaign.sh` supervisor in a persistent checkout (re-review N7). Footprint history is not copied back because hosts run with profiling off and their records hold no footprint; placement uses committed manifests and declared memory (LR-KI-11). |
| D14 | `io_sync = "relaxed"` goes on the `tester-unified` and `cmru-mutation` environments, not a root default (§9). The operator asked for an fsync count with strace/perf/blktrace-style counters. Rev 4 drops the count instead: `strace` is not in the image and cannot see into the container from the host. Write bytes and io PSI remain the measure; `perf stat -G` by lane cgroup is the documented method if a count is wanted. |
| D15 (Q3) | AS-2 is deferred; a two-canary changed-lines lane uses a second Assay lane. |
| D16 (Q5) | A pytest-cov whole-project floor lane asserts a coverage inventory on its own run's report instead of a second canary run (methodology rule 6). Class D: known issue "no R2" unless the project has Python logic worth mutating. |
| D17 (Q8) | RG-253-lite is not built now; deferred with the §8.2 constraints. |

Still open (defaults apply unless the operator says otherwise):

| # | Question | Recommended default |
|---|---|---|
| Q2 | A running older-release T3 run when a newer release arrives: let it finish, or cancel it? | **Let it finish**; drop only pending ones; the newer entry's base is computed at its first claim and stored in the entry |
| Q4 | Assay T1: run `tester-unified` too when `gate/`, `tools/` or packaging change? | **Yes** |
| Q6 | Attention threshold for a missing T3 result | **max(24 h, 2× median + 4 h)** after the release; a campaign whose `attempts.log` still shows progress raises "slow", not "missing"; a FAIL clears when a backlog id is recorded |
| Q7 | Accept the known issues LR-KI-1..4 and LR-KI-7..12 (§3.3, §3.4, §8.2, §9), including relaxed I/O on the two environments after the A/B, manual-run overlap (LR-KI-10), declared-memory placement (LR-KI-11) and persistent-checkout retention (LR-KI-12)? | **Yes** (LR-KI-2 now with the wheel-contents check) |

## 8. Assay and gate relaxations: think from the end

Operator guidance (2026-11-10): we control the hosts, worktrees and lanes, so
guards against an *adversarial* actor may be relaxed where they cost
complexity or runtime. Guards against *accidental* error stay. Prefer
deleting a restriction to adding a feature, and record every weaker guarantee
as an explicit known issue.

### 8.1 End state, worked backwards

**CMRU release (T2), lane `gate`:**
- `release-preconditions` first (seconds), which includes the `CHANGES.md` reader;
- the version-window check (KI-141 enforcement, until KI-141 closes);
- `ref-probe` and `installed-wheel-live`;
- **one** Assay lane run: one baseline feeding R0, `whole_target` R1, and the
  import-break canary with the baseline as its control;
- the R2-owed marker, closed later by the T3 ledger.

R2 is the T3 container lane `r2`.

Smallest change set: WP-CMRU step 1 (CMRU config, tools and tests) plus Assay
B178. Restrictions **deleted** rather than features added:
- the CMRU `assay.toml` base consistency check;
- the `coverage` lane and its pytest-cov canary;
- the separate first-release lanes and `_plan_gate`'s first-release branch;
- `gate-provisional` (or an alias for one release).

The KI-79 version window is **not** deleted: it stays the version-boundary
mechanism until KI-141 closes (D11).

No Assay restriction has to be relaxed for this end state. `whole_target`
refusing `judge.base` stays, because the coupling it breaks is removed on the
CMRU side.

### 8.2 Classification

| Guard | Where | Guards against | Proposal | Saving | Given up (known issue) |
|---|---|---|---|---|---|
| `whole_target` refuses `judge.base` as inert config | `assay/src/assay/config.py:2645-2660` | (a) accidental: a base that silently does nothing | **Keep.** Delete CMRU's `assay.toml` base consistency check instead (`cmru/tools/check_assay_baseline.py:248-251`); the R1/R2 base drift it guards no longer exists once R1 has no base. | unblocks WP-CMRU | none |
| Separate canary control materialization | `assay/src/assay/canary.py:766-810` | (a) accidental: a nondeterministic materialization | **Relax** via B178 (automatic). This is an (a) guard relaxed by **operator decision D3**, the one exception to "accidental guards stay"; sound because a false R3 PASS still needs the transformed run to fail for the expected cause. | one full suite per R3 lane run | **LR-KI-1**: "A canary whose control is the lane's baseline does not detect a nondeterministic snapshot materialization." |
| Post-run dirty-tree / HEAD-changed check | `assay/src/assay/runner.py:3802-3806`, `git.py:862` | (a) accidental: tests writing into the tree | **Keep** (cheap, catches real bugs) | — | — |
| `allow_excluded = false`, unmeasured-target and remapped-line refusals; empty-delta and `--require-candidates` refusals | Assay judge | (a) accidental vacuity | **Keep**: they are what make `whole_target` trustworthy | — | — |
| Run-Gate `clean_tree` refusal at lane start | Run-Gate | (a) accidental | **Keep** (`--allow-dirty` exists) | — | — |
| Assay self-hosting: exact-OID private clone, hash-pinned offline build closure, two venvs, `--require-judge-provenance`, then the whole `tests` suite against the wheel | `assay/tools/tester-unified-gate.sh` | the clone and offline closure: (a) worktree residue entering the wheel; provenance: (b) attestation; the full re-run against the wheel: mostly **(a)**, an accidental packaging omission (a module or data file missing from the wheel, an `importlib.resources` path) | **Keep** the clean-clone build and the cheap independent witness (one test file, `:457-463`). **Narrow** the installed-wheel suite to packaging tests (operator D5 override, WP-ASSAY) and **keep the accidental guard cheaply**: wheel file list vs `git ls-files assay/src`, plus importing every module from the installed wheel (seconds) | up to ~1,000 s (UNVERIFIED split) | **LR-KI-2**: "The installed Assay wheel is proven by a packaging subset, a wheel-contents and import-all check, and the self-hosting witness, not by re-running the whole source suite against it. A packaging error that only a deeper source test would hit is not caught at T2." |
| `safeio` descriptor-relative, `O_NOFOLLOW` reservations | `assay/src/assay/safeio.py` | (b) symlink and parent-swap races | **Keep where it exists** (no runtime cost). **Do not extend it** to new artifact kinds when that costs a feature (for example the Rust `mutants.out/` directory, spike S3); read them plainly. | Rust R-3b effort | **LR-KI-3**: "Directory-shaped ingested R2 reports are read without race-proof reservation." |
| Ingested R2: "never accepts a report it did not watch being produced"; per-mutant diff content check | Assay ingested R2 | the freshness part: (a) a stale report; the content tier: mostly (b) | **Keep freshness.** Drop the per-mutant diff content tier for cargo-mutants if spike S3 shows it costs more than an S change | Rust R-3b effort | **LR-KI-4**: "A cargo-mutants report is bound to its run, not checked mutant-by-mutant against the committed blob." |
| RG-253: protected evidence root, immutable snapshot view, staged candidate, promotion validator, record self-hash, receipt refusal | `DESIGN-verdict-reuse.md` | almost entirely (b): forged or raced PASS records | **Defer** RG-253 and RG-253-lite (D6, D17; constraints below the table) | retries: ≥ 2,436.7 s measured in this wave, causes since fixed | — |
| KI-79 version window | CMRU | operator policy: the only runtime enforcement of KI-141 | **Keep** until KI-141 closes (D11); the per-release marker records the owed R2 beside it | — | — |
| CMRU gate-snapshot guards: `ref-probe`, the live-snapshot wheel proof, the source canary, Run-Gate and tester-image identity rechecks | `run_release_gate.py` | mostly (b), ~70 s per release (**estimated**) | **Keep for now**; a later simplification candidate, not needed for the end state | ~70 s | — |

**RG-253-lite: deferred (D17).** Not built now. The retry causes are fixed
(KI-144); re-measure same-tree retries over the next releases first. If it is
ever built, these constraints from the re-review apply:
- **Assay lanes only** (`kind = "assay"`, which judge a committed snapshot).
  Command lanes run on the live worktree, where ignored files are test inputs
  that a tracked-tree digest misses (the repo-root `.env`, root-owned `vol-*`
  directories, `.assay` state).
- **The record store lives outside per-attempt release worktrees**, keyed by
  digest. CMRU release attempts use candidate worktrees under
  `.worktrees/cmru-release-*` that `cmru abandon` removes (KI-143), so a record
  inside one is not found by the next attempt.
- The digest covers the selected tree minus declared prep-only paths, project
  and central config, lane argv/env, image ID, Run-Gate and Assay version and
  source digest, and the resolved R1 base.
- Vetoes as methodology rule 3 of "PASS-record reuse": R2, live probes,
  network/time/host-dependent lanes, composites, `exec`/`host`/`bare-host`.
- Records are written atomically (an accidental partial write is an (a) risk).
- Known issues it would record: **LR-KI-5** "a PASS record is trusted as
  written (by design: the operator controls hosts and lanes)"; **LR-KI-6** "a
  tracked file changed and restored while a lane ran is not detected", which
  is pre-existing for every run today, not introduced by reuse.

## 9. Cheap wins beyond tiering

The candidates below cut runtime or IO for non-R2 work. Host prerequisites:
- btrfs `/srv/cow` (`MDT_COW`) exists on r1002 and v1001 (host-pipeline plan §1.1);
- the controller has `/srv/mdt-cockpit-cow`, already bind-mounted into lane containers;
- Run-Gate forwards `MDT_COW_ROOT` and `COWTREE_MODE` into lanes (seen in this branch's selftest docker argv).

| # | Candidate | Exists today | Missing | Expected saving | Risk | Effort | Needs a feature? |
|---|---|---|---|---|---|---|---|
| **W0** (first) | **eatmydata on the `tester-unified` and `cmru-mutation` environments** (`io_sync = "relaxed"`) | Since run-gate 24.2.0, `io_sync = strict\|relaxed\|durable` exists (SPEC "io_sync", `LANE-AUTHORING.md` §4). `relaxed` bind-mounts a generated read-only `/etc/ld.so.preload` naming the image's `org.vbpub.tester.eatmydata-lib`; `tester-unified` installs `libeatmydata1` and publishes that label (`tester-unified/Dockerfile:64-83`). Lanes that measure IO are `durable` (iobench, iogovmon, rigor-record, test-policy). | **No lane uses `relaxed`**; the default is `strict`. | All fsync/fdatasync/sync calls become no-ops in every dynamic process of the lane: pytest's own writers, git, sqlite. This includes the real fsyncs that production code under test makes (cmru transactions, ciu worktree, augeaswrap atomic writes). Expected to be the largest IO win per unit of work on shared-IO hosts; **UNVERIFIED** until the A/B below (the assay B110 snapshot path measured no fsync saving, because that path did not fsync). | Low. Durability claims move to `durable` lanes (detail below the table). | S | No (config only) |
| **W1** | **Test temp trees on the CoW area** (`RUN_GATE_TMPDIR`) | Run-Gate exports the state root to every Assay lane's judge as `RUN_GATE_TMPDIR` (`run_gate.py:13905-13917`, SPEC RG-98); a lane maps it onto pytest's `TMPDIR` with `[lanes.<n>.infrastructure] TMPDIR = "required-env:RUN_GATE_TMPDIR"` (run-gate-project does, `run-gate-project/assay.toml:56,101`); `[environments.<n>].state_root` is configurable (SPEC). | The CMRU shard/temp fix is a handoff TODO only (`HANDOFF-STATE-20261107.md:60`; `reports/session-audit-3.md` row 2). No `state_root` in `run-gate.root.toml`; CMRU's `assay.toml` has no infrastructure fact; command lanes (shards, `coverage`) receive no `RUN_GATE_TMPDIR`. | Moves pytest temp trees (**measured** ~2.5 GB per R2 shard, handoff :60) off the container's overlay root disk (r1002 root is 40 GiB). Runtime effect UNVERIFIED (btrfs vs ext4/overlay2 were similar for copies, methodology "Environment levers"). | Low. The state root must stay outside the judged worktree (RG-98 rule). | S | No |
| **W2** | **Bytecode cache outside the tree**: replace `PYTHONDONTWRITEBYTECODE=1` with `PYTHONPYCACHEPREFIX=<RUN_GATE_TMPDIR or lane temp>/pycache` | Lanes set `PYTHONDONTWRITEBYTECODE=1` so the snapshot stays clean (`cmru/assay.toml` `[lanes.cmru]` env). | Nothing in the tools; one env line per lane plus an A/B measurement. | Every CLI subprocess in a test stops recompiling imports. Saving **UNVERIFIED**; the methodology estimates ≈ 4–5 s per mutant for unchanged test bytecode. Measure once on cmru `r0-r1`. | Low. The cache is outside the tree, so the dirty check is unaffected. R2 clones have unique paths, so there is no stale aliasing. | S | No |
| **W3** | **Slowest tests and git-repo fixtures**: one `pytest --durations=50` survey per large suite; turn per-test `git init` + commits into a session template copied per test (methodology guideline 10) | Guideline exists | The survey; per-project fixture changes | Possibly large for cmru (6,486 tests) and assay; **UNVERIFIED** | Low | S survey, M per fix | No |
| **W4** | Per-lane snapshot creation | Assay builds one sealed template per run and gives the baseline and control cheap cowtree clones when the CoW area is set (`assay/src/assay/isolation.py:819-823`); the 59 s figure in `cmru/assay.toml` dates from 2026-10-21 | After WP-CMRU only one Assay lane per gate builds a template | ~1–2 template builds per gate saved by WP-CMRU itself (**estimated**) | — | none | No (by design) |
| **W5** | Keyed read-only dependency venv | `tester-unified` already bakes all test dependencies in `/opt/tester-venv`; lanes only `pip install -e ../assay --no-deps` (seconds), as AGENTS.md requires | — | negligible | — | — | **By design** (LR-KI-7: "Same-repo Assay is installed editable per lane; a keyed venv would contradict AGENTS.md and saves seconds.") |
| **W6** | Plain tmpfs (`/dev/shm`) for single-consumer test temp | Assay R2 has `tmpfs-worker` (B223) | — | small (small suites take 30–60 s) | tmpfs is charged to the 2 GiB lane memory cap | — | **Not recommended** (LR-KI-8: "Test temp is on disk-backed CoW, not tmpfs, because tmpfs counts against the lane memory cap.") |
| **W7** | Fuzz corpora as keyed trees | — | — | none (corpora are small) | — | — | By design: tracked seeds + ignored output |
| **W8** | **Shared page cache for parallel R2 candidates** (cowtree level A) | cowtree `Provisioner` level A (one shared read-only tree + a per-worker overlay) is implemented, but no tool uses `Provisioner` yet: Assay uses cowtree `Run` clones and `tmpfs-worker` | Assay adoption of level A for parallel R2 workers on the hosts (overlay via docker) | Page cache holds one copy of what the tests read instead of one per worker: **tens of MB at most** (upper bound, detail below the table) | Overlay setup needs the docker socket or `CAP_SYS_ADMIN` | M | Yes: Assay. **Not recommended until measured** |

**W0 detail.**
- **Where it plugs in** (operator round 3, D14).
  - Set `io_sync = "relaxed"` at **environment scope**: in `[environments.tester-unified.resources]` (`run-gate.root.toml:29`, WP-ENV) and in `[environments.cmru-mutation.resources]` (WP-CMRU part 9; `cmru-mutation` is its own environment, `cmru/run-gate.toml:216-224`, and does not inherit the first). Lanes that must keep real fsync declare `durable`. Non-ephemeral environments keep `strict`.
  - **Not a central root default:** Run-Gate refuses relaxed outside ephemeral environments (`run_gate.py:1975-1977`), so a root default would refuse every bare-host, host and exec lane (for example cmru `gate`, debian-install-v2 `gate`); and an ephemeral lane whose image lacks the eatmydata label refuses too (`run_gate.py:2141-2142`), which covers `[environments.host]` on `tester-base:local`.
  - **Precondition:** the host tester images carry the label. Verified for `tester-unified:local` (`/usr/lib/x86_64-linux-gnu/libeatmydata.so.1`), and verified by image ID for the r1002 override: the tag `tester-unified:r25-d38b3324fba5` names image `sha256:d38b3324fba5…`, label `/usr/lib/x86_64-linux-gnu/libeatmydata.so.1` (re-review 3 §5).
  - **Not reached:** `scripts/truenas/run-gate.toml:3` redeclares `[environments.tester-unified]`, and a project environment replaces the central one whole (`run_gate.py:2567-2569`), so WP-ENV's relaxed setting does not reach truenas lanes. That is harmless; they stay `strict`.
  - git, sqlite and Python are covered because the loader applies `/etc/ld.so.preload` to every dynamic process. No `core.fsync=none` or `PRAGMA synchronous=OFF` is needed; those are only the fallback for lanes that cannot be `relaxed`.
  - Not covered: statically linked binaries (Go; musl Rust release binaries) and bare-host lanes. Notably Assay's self-qualification wrapper and CMRU's `gate` orchestrator start their own containers, so they need the same preload passed to their child containers (follow-up, S).
  - Assay's own verdict writes use the raw `syscall(SYS_fsync)` (`assay/src/assay/_durable.py:3,26-28`), which eatmydata does not intercept, so they stay durable.
- **Which tests assert durability.** By grep:
  - most fsync tests **patch** the call and assert order or arguments: `augeaswrap/tests/test_endpoints.py:803`, `cmru/tests/test_cli_abandon.py:2249`, `ciu/tests/tests/test_ciu_worktree.py:623`, `assay/tests/core/test_mutation_executor_bound.py:1155`, `assay/tests/core/test_durable.py:19` (asserts the raw syscall, not `os.fsync`). These are unaffected by eatmydata;
  - `libraries/cowtree/tests/live/test_live.py:53,71` calls `os.fsync` for setup and asserts no durability;
  - real IO measurement is only in iobench/iogovmon, already `durable`.

  No estate test proves crash durability (none can, inside a container). The opt-out is therefore **per lane** (`io_sync = "durable"`, which refuses every override), not a per-test marker. A future test that needs a real fsync goes into a durable lane.
- **A/B plan.** The same commit, cmru `r0-r1` (or today's `coverage`), on a gate path where profiling is on, so the cgprofile session records write bytes: the controller's own gated run (one container at a time under the gate lock), or a host run with `RUN_GATE_PROFILE` left on. Hosts launched through `remote-gate`/`run-lane.sh` run with profiling off.
  - run `run-gate --io-sync relaxed` and `--io-sync strict` alternately, 3 runs each. History keeps the modes separate; CLI overrides are excluded from history, so read the durations from the lane log;
  - record wall time, cgroup write bytes (cgprofile session) and io PSI full avg10;
  - **no fsync count** (re-review S15, as REVIEW2 allowed): `tester-unified` has no `strace`, and `strace -f run-gate …` on the host traces only the docker client, not the lane container. Write bytes and io PSI are the measure. If a count is ever wanted, the feasible method is on a host as root, by lane cgroup: `perf stat -e 'syscalls:sys_enter_fsync,syscalls:sys_enter_fdatasync' -G <lane cgroup>`;
  - the functional verdict must be identical in both modes;
  - set the two environments only if relaxed is not slower.
- **Known issue LR-KI-9:** "Lanes run with `io_sync = relaxed`: fsync/fdatasync/sync are no-ops in the lane's dynamic processes, so lane runs give no crash-durability evidence. fsync ordering is proven by unit tests that patch the call, and real IO behaviour only in `io_sync = durable` lanes."

**W1/W8: what each CoW mechanism actually shares.** The property that saves memory is **inode reuse**. The page cache is per (device, inode), so one cached copy serves every reader only when every reader opens the same inode.

| Mechanism | Shares page cache (inode reuse) | Saves |
|---|---|---|
| cowtree **level A**: one shared read-only tree (on tmpfs) + a per-worker overlay | **Yes**: the lower layer's inodes are shared, so one cached copy is used by all workers | RAM and page cache, plus copy time |
| level **B**: a per-worker copy inside tmpfs | No: each worker owns a copy in RAM | time (RAM-backed) |
| level **C**: a reflink clone | No: new inodes; shared extents on disk only | space and copy time |
| btrfs snapshot (cowtree `Run`) | No: same inode numbers but a different device (subvolume; cowtree DESIGN-GUIDE line 110), so separate cache entries | space and copy time |
| level **D**: a plain copy, and Assay `tmpfs-worker` | No: a copy per worker | nothing or time |

What this means:
- **Single-consumer lanes** (one baseline, one canary transform, T1/T2): there is only one reader, so page-cache sharing does not matter. CoW snapshot/reflink saves copy time and disk, and W1 is about putting temp IO on the CoW area. No level-A work is needed for T1/T2.
- **R2 shards on the hosts** (2–3 parallel candidates reading the same tree): with today's clones each worker caches its own copy of **what it reads**, not of the whole tree. The rev-2 figure (164 MB tree, ~330 MB saving per campaign) was about 10x too high (re-review S5):
  - the tracked tree is **132.3 MB** of blobs (148.6 MB `du` excluding `.git`); the 164 MB did not reproduce;
  - CMRU's test working set is **5.6 MB** of tracked blobs (`cmru/src` + `cmru/tests` + `libraries/worktree/src`), **9.1 MB** with `assay/src` (**measured** with `git ls-tree -r -l HEAD` at this branch head; the re-review quoted ≈ 12.7 MB for the second figure);
  - btrfs-snapshot and reflink materialization do not read file contents;
  - clean cached pages are reclaimed under `memory.max` before any OOM.

  So 3 workers hold at most about 10–20 MB of duplicate clean cache (**estimated upper bound**: two extra copies of the working set), and it is reclaimable.
- Level A stays the right mechanism **if** parallel R2 on the hosts ever becomes memory-bound, but W8 is not recommended until a measurement shows that.

Recommended order:
1. **W0**: measure it (A/B with an fsync count), then set relaxed on the two environments.
2. **W1**, inside WP-CMRU and the shard branch.
3. **W2**, with an A/B measurement in WP-CMRU.
4. **W3**: run the survey before deciding on fixture work.
5. **W8**, only if a measurement shows parallel R2 on the hosts is memory-bound by duplicate page cache.

W4–W7 are left as by-design notes.

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
- the host-sat duration medians (queue wall times);
- the r1002 host profile;
- rev 3: the version window and its readers, `release-preconditions` absent from `_run_locked_gate`, `_plan_gate`'s origin query, `local_baseline_facts.py`'s purpose, the queue's `LANE_TIMEOUT_S` and `remote-gate.py` copy, `remote-gate collect`'s file patterns, `run-r2-campaign.sh`, the `io_sync` refusals, the tracked-tree and working-set sizes;
- rev 4: `remote-gate` deleting its throwaway checkout, `run-r2-campaign.sh`'s persistent checkout and exit-4 loop, the progress files of both R2 command lanes, `RUN_GATE_PROFILE=off` in `remote-gate.py` and `run-lane.sh`, no `[admission]` in any vbpub `run-gate*.toml`, the PSI start check and per-host flock, `_validate_postponement`'s bounded test.

**UNVERIFIED:**
- the CMRU and Assay phase splits (so step-2 savings are ranges);
- the CIU R2 duration;
- v1001 RAM;
- whether CMRU's `r2` builds correct facts from bundled tags on a host (WP-CMRU part 5's rehearsal proves it);
- whether `sync-from-devcontainer` carries release tags into the persistent per-commit checkout (WP-CMRU part 5's rehearsal checks it);
- whether nyxloom's `base = "main"` release R1 is vacuous in a real run;
- Buildkite's skip-intermediate behaviour for tag builds;
- whether moving the CMRU prep reader out of the Assay argv keeps the 100% floor;
- whether one central `state_root` value fits every host's CoW mount path (W1);
- the runtime effect of W1 and W2;
- the size of the suites' git-fixture cost (W3).
