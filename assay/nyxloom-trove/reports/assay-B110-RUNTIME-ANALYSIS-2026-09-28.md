# B105 self-qualification runtime: analysis report (B110)

**Date:** 2026-09-28
**Author:** carver session (Claude Opus 5.5), working with the operator.

**Scope:** why Assay's full-source R2 self-qualification (B105) runs about 585 worker-hours when the ceiling is 6 h (target) and 8 h (hard stop). What can reduce it structurally without weakening the claim or raising the 2 GiB RAM cap.

**How this was done.** This was an analysis task. No repository code was edited and no gate or campaign was started. The inputs were:
- read-only inspection of the checkout at HEAD `db85f74784c354188650939e6ade1354ae29f3fa` (branch `assay-b105-evidence-integrity`);
- the retained progress and state evidence of two stopped campaigns;
- light probes: a version probe in `tester-unified:local`, niced git timings in a scratch directory, AST scans, a stale-bytecode reproduction and a hang simulation with a step cap;
- seven read-only research subagents;
- the earlier Luna/Sol design reviews.

**Companion documents:**
- [`assay-B110-PLAN-2026-09-28.md`](assay-B110-PLAN-2026-09-28.md): decisions, package order, gates, pilot, go/no-go, runbooks.
- [`b110/`](b110/): one implementation brief per package.
- [`b110/research/`](b110/research/): the verbatim research records R0–R8 behind this report, plus the probe scripts. Every table and line reference below is traceable to them.

**Evidence labels:**
- **[M]** measured from retained evidence;
- **[C]** calculated from measurements;
- **[A]** assumption or approximation;
- **[I]** inference;
- **[D]** documented by a primary external source (URL in §12).

---

## 1. Executive summary

1. **Every candidate paid for a whole baseline.**
   - Every killed candidate recorded `tests_completed = 5831`. Killed and surviving candidates cost the same, about one baseline each: 30eec294 killed 559.5 s vs. survived 561.1 s, baseline 519.9 s [M].
   - The declared command runs the whole coverage-instrumented suite in a fresh interpreter and a fresh full-history snapshot of the whole vbpub repository. Nothing stops at the first failure.
2. **The 62h40m plan was never evidence.** `assay plan` executes nothing and multiplies 3,760 candidates by a hard-coded 60 s (`cli.py:1786-1801`). The measured baseline (~520 s) already existed; reality was about 9× the plan [C]. `docs/CONSUMERS.md:2307-2312` wrongly calls that figure an upper bound.
3. **Spinning hangs cost three baselines.**
   - In the e79 attempt, 4 of 39 candidates were `budget_exceeded` at about 1,518 s each, which is 24.8% of that run's candidate time [M].
   - They are scanner loops (`adapters/go.py:292/321/323/330`) whose cursor stops or moves backwards. They keep the CPU busy, so liveness correctly does not call them `hung`.
   - 14 such at-risk mutants and 4 latent ones exist across the source (§7.4).
4. **The suite's cost is concentrated in a few tests, in the wrong place** [M]:
   - 113 of 5,831 tests take 317.7 s, 71.9% of the 441.7 s of call time. 4,875 tests take under 10 ms each, about 4 s in total.
   - Alphabetical order spends 99% of call time before the `test_v*` files. `verdict.py` and `verify.py` hold 950 of the 3,760 candidates.
   - 73.6 s per candidate goes to tests that exercise only the *unmutated* wheel on PATH. The whole of `tests/test_python_qualification.py` never executes snapshot source: it costs 97.8 worker-hours per campaign and can never kill a mutant [C].
   - Two liveness tests wait on real 30 s windows (66.7 s).
5. **CPU is idle.** The container has 3 CPUs; the campaign used 0.83–0.89 cores on average [M].
6. **The structural point, which matters more than runtime: a passing native R2 campaign contains only kills.**
   - `judge_mutation` passes a native lane only with zero survived, hung, crashed or budget-exceeded candidates (`mutation.py:3748-3769`).
   - Native lanes cannot lower `fail_under`, and `equivalent` requires a SQL-only artifact (`config.py:3082-3106`; `verdict.py:5041-5053`).
   - The ordered opening prefixes had 7/15 survivors (30eec294) and 9 survivors plus 4 hangs out of 39 (e79). The outcomes were identical on the 15 shared candidates [M].
   - 5 of the first 7 survivors are `@dataclass(frozen=…, kw_only=…)` flag flips; the plan holds 148 of them.
   - So B110's "273 survivor worker-hours" is the cost of a campaign that would **fail anyway**. Survivor cost belongs to a non-qualifying find-and-fix loop. A *qualifying* run costs, summed over 3,760 kills, snapshot + startup/collection + the declared-order prefix up to the first failing test.
7. **What a qualifying run costs on the claim-preserving path** (scenario, not forecast; §6):
   - per kill ≈ S + C + P ≈ 10–21 s, where S is the snapshot (2–4 s today, ~1–2 s after P5), C is interpreter start plus collection (≈8–9 s) and P is the prefix to the first failure (unmeasured);
   - that is 3.5–7.3 h on 3 workers plus ~40 min of fixed work, which fits the 6 h target only at the low end;
   - correction to the first draft: snapshot + collection alone use 2.9–4.0 h of the 5 h candidate budget on 3 workers, so the mean prefix must be ≤ ~3–6 s;
   - **the pilot decides.** The isolation-unit model (≈0.35–2.3 h) is the designed fallback.
8. **Operator decisions from the interview** (full list in the plan §3):
   - a reviewed **equivalence ledger** keyed by stable site anchors, with a **middle-path** audit in the screen at the same commit;
   - **loop guards**, keeping A-464 (time never classifies);
   - **preserve the claim first**, with the units model as a pilot-gated fallback;
   - suite changes: **ignore `test_python_qualification.py`**, **tiered layout**, **lane-level liveness windows with guardrails**, **drop `--override-ini`**;
   - **distributed/async foresight**.

---

## 2. Context and binding constraints

- **B105** (`4-backlog.md:10661`) requires retained, verifier-accepted, full-source R0–R3 self-qualification evidence before M7:
  - R0: the declared suite.
  - R1: `whole_target`, 100% line and branch.
  - R2: the complete native candidate inventory, unsharded in the final artifact.
  - R3: the import-break canary with controls.
- **A-462** (`decisions.md:941`) created the separately invoked `self-qualification` lane. It named "every native Python operator serially and unsharded". The ordinary `tester-unified` release lane stays R0-only.
- **A-463** (`:942`): exact coverage exclusions, pinned by `tests/fixtures/b105-coverage-exclusions.json`.
- **A-464** (`:943`):
  - a measured plan must project completion within 6 h, with an 8 h hard stop and no increase to the 2 GiB RAM cap;
  - reduce repeated work first; additional CPU is acceptable;
  - shards are scheduling, not proof; one persisted absolute deadline;
  - timeouts and host pressure never change a candidate's classification;
  - Buildkite capacity counts as zero until accepted.
- **B110** (`4-backlog.md:11028`): the runtime-reduction item. Its cold-witness, transform and v14 design came from GPT-6-Luna and Sol reviews (§11).
- **Gate envelope** (`run-gate.toml:33-60`):
  - `self-qualification`: `timeout --kill-after=30s 7h30m`, 3 CPUs, `memory = "2g"`, `memory_swap = "8g"`; `budget = "8h"` is advisory.
  - The preflight lane has no timeout wrapper and `budget = "60m"`.
- **Estate rules:**
  - The host is shared with a production game server: one gate container at a time, nice/ionice for light work.
  - Contention-agnostic testing: host load or scheduling never decides a functional verdict.
  - A check is only as strong as what it compares; defaults are hazards.
  - README / DESIGN-GUIDE / CONSUMERS stay in sync.

## 3. Evidence base

| Evidence | Location | Content |
|---|---|---|
| 30eec294 attempt | `.worktrees/assay-b105-gate-copy-30eec294/assay/.assay/progress-self-qualification.jsonl` (inside the target checkout) | 5,862 lines: the baseline's 5,831 per-test events, the `plan` event, and 15 candidate events. 15 state records in `mutation-state-self-qualification/`. |
| e79eb8f5 attempt | `/tmp/vbpub-b105-ciu-root-20260926/.worktrees/assay-b105-self-qualification/assay/.assay/` | 39 candidates, including 4 `budget_exceeded` |
| Plans | `/tmp/b105-selfqualification-plan-30eec294.json` (3,760 candidates; trailing `PLAN_EXIT=0`, parse with `raw_decode`); `/tmp/assay-b105-plan-20260926.json` (3,774, older) | candidate id / path / operator rows |
| Gate logs | `/tmp/run-gate/*b105*`; `reports/assay-B105-LOG.md:81,193,215,258,304,311` | exits, peaks, stalls |
| Prior design reviews | `/tmp/b105-r2-command-design-luna.txt`, `/tmp/b109-verdict-schema-luna.txt`, `/tmp/b105-runtime-rework-sol.txt`, `/tmp/assay-b110-sol-plan.txt` | conclusions in §11 |

The `/tmp` evidence is not durable. Section 4 retains every number that matters, and the per-test classification is in `b110/research/R8`.

- **Source applicability:** 30eec294 is not an ancestor of `db85f747`, because the branch was rebased. `git diff 30eec294 db85f747 -- assay/src` is empty; only `assay.toml`, `run-gate.toml`, `tests/test_self_lane.py` and `tools/self-qualification-gate.sh` differ. The evidence therefore applies to the analyzed source.
- **Tool environment** (probed in `tester-unified:local`) [M]:
  - Python 3.14.6 (the devcontainer host has 3.14.7);
  - pytest 9.1.1; pytest-cov 7.1.0; coverage 7.16.1 with the `sysmon` core (the default on 3.14);
  - pytest-xdist 3.8.0 and hypothesis 6.168.0 are installed; pytest-timeout and pytest-testmon are absent;
  - an `a1_coverage.pth` exists, but subprocess measurement is not configured: no `[run] patch = subprocess`, no `COVERAGE_PROCESS_START`.

## 4. Measured facts

### 4.1 Candidate cost

| Attempt | Candidates | Outcomes | Killed mean | Survived mean | Baseline | Notes |
|---|---:|---|---:|---:|---:|---|
| 30eec294 | 15 | 8 killed / 7 survived | 559.5 s | 561.1 s | 519.9 s (`plan.baseline_s` 519.891) | all-candidate mean 560.3 s; auto budget 1,559.67 s |
| e79eb8f5 | 39 | 26 killed / 9 survived / 4 budget_exceeded | 530.9 s | 515.8 s | 504.6 s | 4 × ≈1,518 s = 24.8% of candidate time |

- Every killed candidate in both attempts recorded `tests_completed = 5831`. No kill stopped early.
- The shared first 15 candidates had identical outcomes in both attempts. The outcome is deterministic, not load-driven.
- The `plan` event values were:
  - `budget_per_candidate_s = 1559.67`, i.e. `max(3b, b+60)`;
  - `expect_next_event_within_s = 107.7`;
  - `pre_first_event_within_s = 22.21`.

**B110's own projections** (`4-backlog.md:11032-11070`), from the opening prefix:
- about 585 worker-hours: 24.4 days on 1 worker, 8.1 days on 3 ideal workers;
- the required average is under 4.8 s per candidate with 1 worker, 9.6 s with 2, or 14.4 s with 3, to fit 5 h of candidate time in the 6 h target.

### 4.2 Planner

`assay plan` gives `3,760 × 60 s = 225,600 s = 62h40m`. The fallback applies to omitted, `auto` and `none` alike (`cli.py:1786-1803`).

Under `auto` the real per-candidate *bound* is `max(3b, b+60) ≥ 60`. So 60 s is a **lower** bound on the auto ceiling, not the upper bound CONSUMERS claims. The measured candidate mean was about 9× larger.

The data for a measured estimate already exists:
- `plan.baseline_s`;
- `command_finished` started/ended for the baseline or direct phase;
- per-candidate `elapsed_seconds`.

A precise `--baseline-from` design is in plan P0 and research R4 §C.

### 4.3 Suite profile (30eec294 baseline, 5,831 per-test call events, all passed) [M]

| Slice | Tests | Call s | Share |
|---|---:|---:|---:|
| all | 5,831 | 441.7 | 100% |
| 10 slowest | 10 | 152.2 | 34.5% |
| 25 slowest | 25 | 221.8 | 50.2% |
| 50 slowest | 50 | 271.6 | 61.5% |
| ≥ 0.5 s | 113 | 317.7 | 71.9% |
| ≥ 1.0 s | 63 | 286.5 | 64.9% |
| ≥ 2.0 s | 36 | 248.4 | 56.2% |
| ≥ 5.0 s | 17 | 193.7 | 43.9% |
| < 10 ms | 4,875 | ≈4 | ≈1% |

- **Fastest first:** the fastest 4,665 tests (80%) cost 3.0 s of call time; 90% of tests cost 17.9 s.
- **Alphabetical, as declared today:** 99% of call time is spent by test #4,360.
- **Wall time vs. call time:** the baseline wall was 519.9 s against 441.7 s of calls. About 78 s is not in call time: session and module fixture setup, collection, teardown. Only call records are forwarded to progress (`liveness.py:207-225`), so setup and teardown are unmeasured.

**Heavy-test cost by category** (R5 §1; per-test table in R8):

| Category | Seconds |
|---|---:|
| (a) waits on a real-time window | 91.0 |
| (d) runs the installed `assay` from PATH | 73.6 |
| (c) nested `assay run` / pytest in-process | 69.9 |
| (b) wheel / venv / pip | 54.2 |
| (e) git history or snapshot work | 24.9 |
| (f) other | 4.2 |

**What the heavy tests actually execute:**

| Executes | Seconds | Can kill a mutant? |
|---|---:|---|
| snapshot `src/assay` in-process | 162.0 | yes |
| a wheel built from the snapshot (`standalone`, zipapp) | 37.7 | yes (not coverage-measured) |
| the unmutated PATH wheel | 73.6 | **no** |
| the committed 1.2.5 release wheel | 13.8 | **no** |
| no `src/assay` at all (gate/distribution harness) | 27.2 | only indirectly |
| a static read of `src` (AST sweeps) | 2.4 | yes |
| in-process plus a pytest subprocess | 1.0 | yes |

### 4.4 Fixed per-candidate costs

| Cost | Value | Label |
|---|---|---|
| Snapshot materialization | ≈2–4 s. 18 P22 git processes plus 6 in the dirt check. ≈130 MB written per candidate: 62 MiB of packs + ~70 MB of worktree, ≈490 GB over a campaign. | [A], from git-equivalent timings at load ≈8/8 |
| Collection + first test setup ("leading gap") | 7.40 s = 22.21 / 3. This is the gap from `session_start` to the first record (`liveness.py:904-1010`). Interpreter start before `session_start` is **extra**, ≈0.5–1.5 s. | [C] / [A] |
| Non-call residual | ≈71 s (78 s minus the leading gap) | [C] |
| PATH-wheel tests | 73.6 s | [M] |
| Real-time liveness tests | 66.7 s (`test_cli_run.py:463` 30.83 s, `:586` 35.90 s) | [M] |

### 4.5 Resources [M]

- **CPU:** 0.83–0.89 cores on average in a 3-CPU container.
- **Peak RSS:**
  - 738 MiB in the preflight (612 MiB p90), with a 2.3 s memory-full stall;
  - 987 MiB in the e79 R2;
  - 588 MiB in attempt 3.
- **Coverage overhead:** an uncontrolled comparison only. The coverage-free R0 release lane took 573.17 s; the covered preflight baseline took 548.35 s, on different trees and at different load. The overhead is probably small; its real significance is that it **blocks witnesses** (§7.1).

### 4.6 Candidate inventory (3,760; plan at 30eec294) [M]

- **Operators:** compare-swap 2,169; boolop-swap 958; bool-const-flip 498; falsy-swap 135.
- **Files:** 47 candidate-bearing files (min 1, median 30, max 623 candidates per file). `verdict.py` and `verify.py` hold 950.
- **Import-time candidates:** 190 (5.1%), of which 189 are bool-const-flip in module or class-body scope. They cannot be switched after import (schemata or fork-server designs cannot handle them), and a coverage map attributes them to the empty context.
- **Dataclass flags:** 148 boolean flag candidates (80 `frozen=True`, 68 `kw_only=True`) in 80 decorators.
  - Among the first 15 candidates, 6 were flags: `base.py:77 frozen` killed; `base.py:77 kw_only`, `:129 frozen` + `kw_only` and `:164 frozen` + `kw_only` survived.
  - Only about 5 classes' frozen-ness is tested today.

## 5. Root-cause analysis

| # | Cause | Mechanism | Evidence | Remedy (package) |
|---|---|---|---|---|
| 1 | Full suite per candidate, even for kills | The declared command has no stop condition. The witness plugin cannot stop: B105's argv is ineligible because of `--override-ini` (`mutation_witness.py:60-63`); pytest-cov's `pytest_runtestloop wrapper=True` breaks `standard_loop`; and the conftest `pytest_sessionfinish` is untrusted. | every kill `tests_completed=5831` | cold witness + R2 command (P3) |
| 2 | Estimate without evidence | 60 s constant | `cli.py:1786-1801` | `--baseline-from` (P0) |
| 3 | Spinning hangs run to 3× budget | cursor non-advance, CPU busy, so not `hung` | e79: 4 × 1,518 s | guards (P2) |
| 4 | Heavy tests early in the declared order | alphabetical collection | §4.3 | tiered `zz_slow` (P1) |
| 5 | Tests that cannot kill | PATH wheel / locked 1.2.5 wheel | 97.8 worker-h per campaign | `--ignore` (P1) |
| 6 | Real-clock tests | 30 s CPU window, 15 s idle floor | 66.7 s | lane-level liveness keys; short windows in test lanes only (P3c) |
| 7 | Idle CPUs | `jobs = 1`; the executor uses joined waves | 0.83–0.89 cores | work queue (P4) |
| 8 | Heavy snapshot | whole repo, full history; status hashes every file (zeroed stat after `read-tree`); the full closure is re-walked | 18+6 git procs | C1/C2 (P5) |
| 9 | Duplicate R0/R1 | the preflight and the lane both run R0/R1 (R2 starts on an R0 PASS even when R1 refuses, `runner.py:4099`) | +~9 min | accepted as fixed cost; the preflight is a safety gate (A-462) |
| **S** | **Survivors present** | a native PASS needs all kills; no native equivalence path | 7/15 and 9/39 survivors | screen → fix → ledger (P1 contract test, P10, runbook §9) |

## 6. Cost model

- **Today:** `T ≈ T_fixed + Σ(S + F)`, where `F ≈ 505–520 s` per kill *and* per survivor, and `≈ 3F` per spinning hang. That gives ≈585 worker-hours [C].
- **A qualifying run with cold kills and only kills:**

  `T ≈ T_fixed + Σ_i (S + C + P_i + O) / W_eff`
  - `S`: snapshot. 2–4 s today [A]; ≈1–2 s after C1+C2 [A].
  - `C`: interpreter start + collection + first setup. ≈7.4 s [C] + ≈0.5–1.5 s [A], measured under sysmon coverage; the no-cov value is unmeasured.
  - `P_i`: the declared-order prefix up to the first failing test, including per-test setup and teardown. **Unmeasured**; the pilot measures it.
  - `O`: the dirt check, liveness monitor start, receipt and state writes. ≲1 s [A].
  - `W_eff`: effective workers. ≤3 locally (3 CPUs, 2 GiB aggregate).
  - `T_fixed`: ≈35–40 min [A]. Setup is 1–3 min, preflight 526–548 s, the lane's own coverage baseline 504–520 s, the no-cov baseline ≈1 suite, the R3 control ≈1 suite (`canary.py:300`), plus consolidation and verify (seconds).

| Scenario (scenario, not forecast) | Per kill | 3,760 kills on 3 workers | + fixed | Fits 6 h? |
|---|---:|---:|---:|---|
| S+C only, today's snapshot | 10.4–13.4 s | 3.6–4.7 h | 4.2–5.4 h | only if P ≈ 0 |
| S+C only, after P5 | 9.4–10.4 s | 3.3–3.6 h | 3.9–4.2 h | leaves 1–2 h for Σ P |
| Mean P = 3 s, after P5 | 12.4–13.4 s | 4.3–4.7 h | 4.9–5.3 h | borderline |
| Mean P = 10 s | 19.4–23.4 s | 6.8–8.2 h | >7 h | **no** |
| Isolation units (P11): S + unit start + K | 1–6.5 s | 0.35–2.3 h | 1.0–2.9 h | yes |

**Correction to the first (terminal) draft.** That draft said "snapshot + collection alone ≈ 3,760 × 6.5–8 s". With the leading gap correctly attributed, it is 3,760 × ≈9–13 s. The conclusion is unchanged but sharper: the claim-preserving path fits only if the tiered order makes most kills happen within a few seconds of collection. The pilot is decisive, and P11 is a real fallback, not a formality.

**Survivor cost in the non-qualifying screen after P1** ≈ 520 s − 73.6 s (PATH wheel) − ≈13.8 s (1.2.5 wheel) − 6.2 s (other PQ tests) − ≈60 s (short liveness windows in test lanes, P3c) ≈ 365–385 s [C].

## 7. Deep dives

### 7.1 Why no witness is captured today (and why B106 is inert for B105)

- `supports_sequential_pytest` rejects any `-o` or `--override-ini` (`mutation_witness.py:60-63`). B105's argv carries `--override-ini=pythonpath=src`, which duplicates `pyproject.toml:99` `pythonpath = ["src"]`.
- pytest-cov 7.1.0 registers:
  - `pytest_load_initial_conftests` (tryfirst);
  - `pytest_configure_node`;
  - `pytest_testnodedown`;
  - `pytest_runtestloop` (`wrapper=True`);
  - `pytest_runtest_call` (`hookwrapper=True`).

  The witness plugin accepts only built-in lifecycle hooks (`_only_builtin_hook_impls`, `mutation_witness.py:347-395`). Its `standard_loop` check (`:440-442`) is therefore false and the receipt says `unsupported`.
- `tests/conftest.py:59` `pytest_sessionfinish` (the B105 archive hook, active only when all four `ASSAY_B105_*` variables are set) fails the `session_finish` trust check (`:449`).
- **Consequence:** B106's `--reuse-from` is merged (A-461, `e5e9b95c`, assay 7.1.0) but inert for B105. Its state records hold only `{"mode":"full"}`. The backlog still lists B106 as OPEN; that is corrected in this commit.
- **Kill semantics today:** any non-zero exit is `killed`, including a collection error (`runner.py:1301-1339`, `mutation.py:1668-1692`). A cold kill (a verified *call-phase* failure with session exit 1 and process exit 1) is a **stricter** kill claim that arrives sooner.

### 7.2 Snapshot internals (R2)

**Repository scale** [M]:
- 5,298 tracked entries (26 symlinks, 3 omitted), 780 directories, maximum depth 13;
- 70.7 MB of blob bytes across 4,713 unique blobs;
- 6,338 commits; a full closure of 54,974 objects;
- `assay/` alone is 960 files and 20.2 MB.

**Per-candidate steps** (`isolation.py:703-863`):
1. Stale-site blob read.
2. `mkdtemp`.
3. `git init` with an empty template.
4. `_copy_objects`: `copyfile` of every seed pack, ≈62 MiB.
5. Remove `shallow` (full history).
6. `read-tree`.
7. `update-index --skip-worktree` for the 3 omissions.
8. `hash-object`, `update-index --cacheinfo`, `write-tree`, `commit-tree`: the deterministic child.
9. `_enforce_child_closure`: a **full** `rev-list --objects` (54,974 objects) plus batch-check plus a `--no-walk` walk.
10. `_write_worktree`: `cat-file --batch` of about 4.7k blobs (69.4 MB, 0.48 s), plus mkdir/chmod/`utime(_FIXED_MTIME)` per entry.
11. Write `HEAD`.
12. `_verify`: `rev-parse`, `rev-list --count` (0.06 s), **`status` on a zeroed-stat index, which hashes every file**, `write-tree`, `ls-files -v`, the manifest proof, and the alternates/hooks/config checks.

After the command, `_snapshot_left_dirt` runs `status` again, which hashes everything again. The directory is then removed.

**Timings** (niced, host at load ≈7.5–8 of 8 cores; indicative only):

| Operation | Time |
|---|---|
| Full `rev-list --objects` | 0.228 s |
| … plus batch-check | 0.282 s |
| `--no-walk` walk | 0.014 s |
| Delta walk `HEAD --not HEAD~1` | 0.023 s |
| `cat-file --batch` of all blobs | 0.484 s |
| `status` on a zeroed index | 0.887 s cold / 0.275 s warm |
| `update-index --refresh` | 0.291 s |
| `status` after refresh | 0.041 s |

**Probe of the zeroed index:** after `read-tree`, `ls-files --debug` shows `mtime: 0:0 size: 0`. `update-index --refresh` records stat data only for entries whose content matches; a mismatched entry stays unrefreshed and `status` still reports it.

**C1:** refresh once after writing, and both `status` calls become stat-only. Dirt detection is not weakened: the default `core.checkStat` includes inode and ctime, so a same-size edit whose mtime was restored is still caught. **Never** set `checkStat=minimal` or `trustctime=false`.

**C2:** closure(child) = base ∪ (`rev-list --objects <child> --not <base>` − base). This is exact, because the seed's reachable set was proven equal to the source inventory at prepare time (`isolation.py:2048-2077`). Subtracting the base handles a mutant blob equal to a historical blob. Cost: 0.023 s vs 0.5 s.

**Invariants and pins:**
- **O2 (disjoint inodes)** is pinned **only** by the uncollected `carve-assets/P22/test_acceptance.py:266-309`. A hardlink "optimization" would pass every collected test (`tests/test_b105_isolation_proof_boundaries.py:243` checks names and bytes only). Guard tests G1–G5 fix this (P0).
- **Design A** (reuse one tree and swap files) breaks A-120/A-161/A-184/A-195:
  - ignored residue survives (`__pycache__`, `.pytest_cache`, the coverage JSON);
  - `.git/info/exclude` would hide later dirt;
  - a `[filter]` clean driver planted in `.git/config` would run inside the next `_verify` status;
  - stale bytecode (below).
- **Design B** (copy a verified pristine template by an explicit manifest, not `copytree`; keep symlinks as symlinks; re-`utime` the swapped file; never copy a refreshed index; never run a command in the template) keeps every invariant. It is optional C4.

**Stale bytecode, reproduced on Python 3.14.7:**
- CPython validates a timestamp `.pyc` only against the source's `int(st_mtime)` and `st_size`.
- Every snapshot file gets `_FIXED_MTIME = 946684800` (`isolation.py:67, 1942`), and compare-swap `==`↔`!=` keeps the byte count.

| Setup | Result |
|---|---|
| Original tree | `True` |
| Mutant in the same tree | **`True` (stale)** |
| `--check-hash-based-pycs always` | `True` |
| `PYTHONDONTWRITEBYTECODE=1` with a stale pyc present | `True` |
| Fresh `PYTHONPYCACHEPREFIX` | `False` (correct) |
| `__pycache__` deleted | `False` (correct) |

Any tree reuse therefore risks **false kills**, i.e. a false PASS. The current fresh-tree-per-candidate design is safe and must stay (D8).

**Tests that need the repository:**
- About 68 test definitions need the parent repository. Only the 32 in `test_python_qualification.py` need history beyond HEAD, because they pin commit `9f522a72…`'s `topos` tree. That is the reason for `snapshot_history = "full"`.
- `test_distribution_build_release.py`'s fixture clones the full-history snapshot **twice per candidate**.
- No test reads another project's worktree files; this is static evidence.

**Once P1 ignores `test_python_qualification.py`,** full history may no longer be needed by the collected tests. That would allow a shallow snapshot (A-451) and cheaper clones. This is a follow-up question, not part of this plan's acceptance, because it changes the lane declaration and needs its own drift proof.

**The filesystem cannot reflink:** `/tmp` is ext4. A reflink-capable `TMPDIR` would turn the ≈130 MB of copies per candidate into metadata operations; A-184 already allows that. It is an environment item.

### 7.3 Test-suite anatomy (R5, R7, R8)

- **`tests/test_python_qualification.py` never executes snapshot `src/assay`.**
  - It has no `assay` import; the harness is asserted import-free at `:105-106`.
  - Its `installed_assay` fixture is `shutil.which("assay")` (`:546-555`), which resolves to `run-venv/bin/assay`: a wheel built once from the qualified commit (`self-qualification-gate.sh:92-126,153-154`), never per mutant.
  - `install_locked_release` installs the committed `gate/python/release/P25/assay-1.2.5-py3-none-any.whl`.
  - Per campaign: PATH wheel 76.9 h + 1.2.5 wheel 14.4 h + the rest 6.5 h = 97.8 worker-hours that cannot kill anything and add nothing to `src/assay` coverage.
  - The file path is locked by P33 acceptance (`carve-assets/P33/test_acceptance_v5.py:406-417`), so `--ignore` rather than moving the file.
  - The release lane keeps running it.
- **Tier directory:**
  - pytest 8+ collects directories and files together in name order (pytest 9.1.1: `_pytest/main.py:569`, `_pytest/pathlib.py:941-943`), so the tier directory must sort last: `tests/zz_slow/`.
  - It must have no `__init__.py` and no `conftest.py` (the `sys.modules['conftest']` collision, `conftest.py:342-347`), and every file basename must be unique.
- **Moves and splits:** listed in the P1 brief. The projection is a fast tier of 5,666 tests / 124.8 s of call time and `zz_slow` with 133 tests / 223.2 s [C].
- **Pinned paths:**
  - `test_python_qualification.py` and `test_distribution_gate.py` (P33);
  - `test_b106_reuse_and_witness.py` (gate `:653`);
  - `test_lane_schema_v2_locked_successors.py`, `test_verdict_v13_successors.py`, `test_self_hosting.py` (named by the gate script);
  - the `test_runner_snapshot_selection.py` node IDs (`assay.toml:85-86`, `test_self_lane.py:121-126`).
- **The liveness-leak blocker** (verified in this session):
  - `tests/test_liveness.py:581/601` call the materialized plugin's `pytest_sessionfinish(session=None, exitstatus=…)` without redirecting `ASSAY_LIVENESS_EVENTS`. That writes a real `session_finish` record, stamped with the candidate's pid, into the **outer** events file.
  - After it, the monitor classifies HUNG after 30 s of silence (`_HUNG_SESSION_FINISH_GRACE_S`, `liveness.py:525, 1050-1056, 1475-1476, 1516-1519`).
  - Today only the 30.8 s and 35.9 s tests exceed that, and they run *before* `test_liveness.py`. Tiering would move them after it, and every candidate would falsely become `hung`.
  - The reverse leak: `:656`/`:803` redirect the variable for the whole test body, so two outer call records are lost (`tests_completed` is 2 short, confirmed in the evidence).
  - **Fix:** narrow the redirection with `monkeypatch.context()`. Never use an autouse `delenv`.
- **Order dependence in `src/assay`:** none found. There are no caches, no mutated module-level containers and no signal handlers; registries are `MappingProxyType` or built per call. The other hazards are benign or already controlled (R7).

### 7.4 Hang-prone loops (R6)

- **Inventory:** 50 `while` loops, 2 `iter(callable, sentinel)` and 2 recursive functions. No `for` loop grows its own iterable, and there are no `itertools.count` loops.
- **Operators:**
  - compare-swap excludes `in`/`not in` (`python.py:447-463`);
  - falsy-swap touches only `return` values;
  - `while True → False` skips the body, which crashes rather than hangs.

**The four confirmed go.py mechanisms:**

| Site | Mutant | Effect |
|---|---|---|
| `:292` | `end == -1 → !=` | returns 0, so `:356` resets `i = 0` and the scan restarts forever |
| `:321` | `two == "//" → !=` | at a newline, `find` returns `i`, so the cursor stops |
| `:323` | trailing `//` without a newline | `i = -1` |
| `:330` | unterminated `/*` | `i = 1`, a step backwards |

**At risk (14 mutants):**
- go.py ×4;
- javascript.py 243/245/249 (the same bugs as go.py);
- sql_lex.py 193 ×3 and 195. The SQL corpus contains `\n-`, and `test_adapters_sql_lexer.py:57` has a trailing `--` with no newline;
- go_modfile.py 393 (unbounded `list(_tokens)`, memory growth);
- git.py 335 ×2. `and→or` means `select(None)` on an empty selector blocks forever; `is None→is not None` skips the drain and deadlocks above ~64 KiB;
- liveness.py 1530 (the timeout check itself; hangs the busy-loop *test*).

**Latent (4):** sql_lex 270/273 and isolation 1388/1392 (memory growth on input shapes not in the suite).

**Cost:**
- Spinning mutants run to the full budget (1,559.67 s) and land in `budget_exceeded`.
- Blocking ones become `hung` after 107.7 s.
- Memory-growing ones consume RSS for up to about 26 minutes on a host shared with production.

**Guard** (D2): add `errors.require_advance(old, new)`, which raises `AssertionError` if `new <= old`. Apply it at go.py 326/334/356, javascript.py 258, sql_lex.py 197/280, go_modfile.py 393 and isolation.py 1406. It adds no branches or mutation sites at the call sites, and one direct test kills its own `<=→<` mutant.

**Rejected alternatives:**
- An inline `assert` adds an unkillable mutant per site.
- A bounded `for … else: raise` leaves an uncovered `else` that breaks the 100% floor.

**`git.py:335`:** rewrite as `while selector.get_map(): … if overflowed: break`. This shifts line numbers, so `b105-coverage-exclusions.json` (which pins git.py 376-377 and 1342-1343) must be updated in the same commit.

**`liveness.py:1530`:** no source guard can protect a timeout check from a mutant of itself. Instead, give the real-child tests a thread + join watchdog that kills the child's process group. That also fixes a potential orphaned busy-loop child, since `start_new_session=True` (`liveness.py:1375`) puts it outside the kill.

### 7.5 Dataclass flag contract (R5 §4)

- **Inventory:** 80 decorators, all at module level; 80 `frozen=True`, 68 `kw_only=True`. The 12 frozen but not `kw_only` classes are:
  - `go_modfile.ModuleDeclaration`;
  - python and sql `_Worst`;
  - nine config classes.
- **Per module:** verdict 25; config 9; isolation, mutation and runner 5 each; others fewer.
- **Other sites:** one field-level boolean site (`config.py:1066`) and 12–13 fields with boolean defaults.
- **Test spec:** `tests/test_dataclass_contract.py` plus `tests/fixtures/dataclass-contract.json`. It:
  - imports every `src/assay` module;
  - collects the dataclasses owned by that module;
  - cross-checks with the AST that every `@dataclass` is covered;
  - records the ten `__dataclass_params__` attributes plus per-field `init/repr/compare/hash/kw_only` and boolean defaults;
  - asserts two-way set equality with the fixture.
- **Prototype:** 80 classes, 463 fields, 13 boolean defaults; the AST check passes.
- **Python 3.14 note:** `Field.doc` exists; don't read it. Use per-field `kw_only`, because 3.11's class params are unconfirmed.

### 7.6 Executor (R4 §A)

- **Today:** `jobs` fully joined waves (`mutation.py:2889-2994`); each wave waits for its slowest member.
- **Invariants a queue must keep:**
  - exact `jobs` and a single factory construction (A-082, A-122);
  - every candidate submitted exactly once;
  - position-aligned results (A-113);
  - expiry: stop submitting and mask every unsubmitted position `budget_exceeded`, while in-flight candidates finish and count (A-160, A-193);
  - fatal: stop submitting, drain, re-raise the first fatal (A-195);
  - progress and state written on the main thread in position order;
  - the peak-pack bound `(1 + jobs) × max_pack_bytes` (DESIGN-GUIDE `:1324-1334` needs its "waves" sentence rewritten).
- **External consumers:** the run-gate RG-36 watcher (`run-gate.py:6462-6565`) and `analysis.py:715-752` read `candidate_index` as the *completed count*. Completion-order events would break them, so use a reorder buffer.
- **The test that breaks:** `tests/test_b105_mutation_boundaries.py:432`'s fake executor never calls the function and returns duck-typed futures. Move it to real `Future`s.
- **A-462's "serially"** conflicts with `jobs > 1`; amended by D3/A-467.

### 7.7 Liveness windows (R4 §B)

- **Constants** (module-level, not configurable per lane):
  - `_HUNG_CPU_WINDOW_S = 30.0`, `_HUNG_CPU_GROWTH_FLOOR_S = 1.0`, `_HUNG_SESSION_FINISH_GRACE_S = 30.0`, `_LIVENESS_POLL_INTERVAL_S = 1.0` (`liveness.py:523-526`);
  - `LIVENESS_IDLE_FLOOR_S = 15` (`:641`), `LIVENESS_FALLBACK_FLOOR_S = 60` (`:646`).
- **A window key alone is not enough.** `hung` needs `idle ≥ bound` and `bound ≥ idle floor`. The hang test therefore needs both keys, and the busy-loop test needs a budget just above the window.
- **Growth floor:** it is absolute (1.0 s per window), so a 3 s window would demand 33% of a core. This conflicts with contention-agnosticism. Scaling it as `window/30` keeps the demanded fraction constant (D5).
- **Disclosure:** `judgment.r2.liveness` is closed at `{active, reason, plugin}` (`verdict.py:2934-2970`; schema `additionalProperties:false`). Disclosing the window is therefore part of v14.
- **Other real-clock tests:**
  - `test_progress_phase_stream.py:456` (7.05 s; `PROGRESS_HEARTBEAT_FLOOR_SECONDS=5.0`, `runner.py:936`);
  - `test_lane_timeout_writes_a_verdict.py` (about five 1 s waits);
  - `test_environment_preflight.py:128/246`.

  These are left as they are or moved to `zz_slow`.

### 7.8 Deadline, SIGTERM, pilot subset, resource evidence (R4 §D–F)

- **Deadline:**
  - `LaneDeadline` (`runner.py:210-308`) is monotonic-only; `tightened()` never widens.
  - The gate runs the preflight on a fresh 60 m clock, then the full lane on a fresh 5 h clock, each with `--resume`. Every re-invocation gets a fresh clock, which is exactly what B110 prohibits.
  - A persisted deadline must be UTC. Convert it to monotonic once per process (the A-193 concern).
- **SIGTERM:**
  - Assay installs no signal handler, and candidates run with `start_new_session=True`.
  - GNU `timeout` signals its own process group, so an in-flight pytest candidate would survive assay's death until container teardown. Needs verification; P6 fixes it either way.
- **Pilot subset:**
  - No existing mechanism selects explicit candidate IDs.
  - A sharded verdict **can be PASS and is accepted by `assay verify`** (`judge_mutation` ignores shard fields).
  - `tools/b105_report_check.py:14-107` does not refuse `shard_index` or a partial inventory. P0 adds that refusal.
- **Resources:**
  - `_pid_cpu_ticks` already reads `/proc/<pid>/stat`; RSS is field 24 of the same line (`fields[21]` after the split).
  - Sample the tree RSS peak at 1 Hz. It is a lower bound on the true peak; state that.
  - Add an optional `sampler` seam rather than changing `cpu_reader`: there are 17 test injections of it.

### 7.9 v14 surface (R3)

**Traps:**
1. Receipt keys must be added to both the plugin `_write` and `_INTERNAL_RECEIPT_KEYS`.
2. `inject_witness_plugin` recomputes `argv_effective` from `argv_declared`, so the transformed argv must live in the R2 plan's `argv_declared`.
3. `-p no:pytest_cov` goes in `argv_appended` with `cli_argv_appended` frozen; otherwise `allow_argv_append=false` gives `EXEC_FAILED`.
4. `JudgmentR2._NATIVE_ONLY_FIELDS[:-3]` slice, and the ingested branch rejects non-`None` native fields, so new fields default to `None`.
5. B105 cannot capture witnesses today (§7.1).
6. The 100% floor applies to new code: add new modules to both target lists and keep the exclusions fixture exact.
7. Don't bump `MUTATION_STATE_SCHEMA_VERSION`; it is shared with the shard manifests.
8. DESIGN-GUIDE §7 (`:1871`) says flags are "never derived by assay"; it needs an explicit exception.

**The v13 → v14 checklist** (from `e5e9b95c`):
- the constant and history paragraph;
- the schema `$id`/const;
- 50 golden fixtures (11 with native `judgment.r2`);
- freeze carve-assets W9 and add W10;
- `qualify_topos.py` literals;
- `test_gate_harness_version_pins.py`;
- `tester-unified-gate.sh:576-654` markers;
- literal-13 tests, including the `test_analysis.py:823` replace trap and `test_verdict_schema_is_packaged.py:267`;
- the `reuse.py` cold start;
- README, DESIGN-GUIDE, CONSUMERS, CHANGES.

**Ambiguities A1–A9** are resolved in the plan (D6).

### 7.10 Coverage mechanics relevant to test selection

- The `sysmon` core "does not yet support plugins, dynamic contexts" [D]. A per-test map (`--cov-context=test`) therefore needs the `ctrace` core.
- "Any code measured before a dynamic context is set will be recorded in this empty context" [D]. Import-time lines have no test attribution.
- Subprocess measurement is not configured, and many Assay tests run Assay in child processes (standalone wheels, the CLI, git).

So a per-test coverage map is **safe only for ordering** (or for isolation-unit order in P11), never for skipping tests. Sol's review concurs: coverage slices are research, and a slice failure must be re-proven in the full declared command.

## 8. How established tools avoid per-mutant full-suite work (R1)

| Tool | Test selection / order | Kill | Mutant insertion / process | Incremental | Transfer |
|---|---|---|---|---|---|
| PIT 1.30.0 | per-test coverage; covering tests ordered by increasing time, with the class's own unit tests weighted first (`TIME_WEIGHTING_FOR_DIRECT_UNIT_TESTS = 1000`) | early exit by default; `fullMutationMatrix` opt-in | class hot-swapped into a long-lived minion JVM; per-test timeout factor 1.25 + 4000 ms | "experimental"; all but "prioritise the previous killer" introduce "a degree of potential error" | pipeline template; no static-initializer mutation |
| StrykerJS 10.0.0 / .NET 4.16.0 | `perTest` by default (40–60% gain) | bail by default | mutant schemata (20–70%); hot reload; static mutants run all tests in a fresh worker, sorted last (6% of mutants ≈ 50% of runtime) | documented false positives and negatives | report schema `killedBy`/`coveredBy`/`testsCompleted`/`static` |
| mutmut 3.8.0 | stats run maps functions to tests; mutants sorted by estimated worst-case time | `pytest -x` | trampoline + fork/forkserver; in-function mutations only | function hashes; keeps the cache on dependency change by default | closest Python design; not audit-grade |
| Cosmic Ray 8.7.0 | none: full suite per mutant | exit code | on-disk rewrite; subprocess with `start_new_session`, `PYTHONDONTWRITEBYTECODE=1`; session DB | resumable | same cost structure as Assay today; timeout doc/source discrepancy |
| cargo-mutants 27.1.0 | package tests only | nextest early stop | build + test per mutant; `--shard k/n` ("all shards must be run with the same arguments") | `--iterate` "a heuristic" | shard identity discipline |
| mutatest 3.1.0 / MutPy 0.6.1 / Major 3.0.1 / mull 0.34.1 | coverage filtering; MutPy per-test AST-node coverage | varies | `__pycache__`-only mutation (mutatest); in-memory import (MutPy); conditional mutation (Major, mull) | — | — |
| Google (ICSE-SEIP'18) | line-coverage tests for changed lines | — | ≤1 mutant per line; arid nodes skipped | diff-based | "infeasibly expensive to compute the absolute mutation score": a different claim |

**Research:**
- Untch'93 schemata ("over 300%", seen only as a search snippet);
- split-stream execution / AccMut (2.56× over split-stream, 8.95× over schemata, C programs);
- Zhu et al. ICSTW'17 (83.93% time reduction, 0.257% precision loss; approximate);
- PMT (prediction, not evidence);
- Gopinath ISSRE'15 ("1,000-sampling approximates mutation score … 0.62% on average"; 9,604 samples → 99% accuracy);
- FaMT (per-mutant test prioritization; reduction is unsafe for survivors);
- ReMT (dangerous-edge reuse conditions);
- Chen & Zhang ICST'18 (file-level RTS is precise for mutation testing);
- Ekstazi (file-level dynamic dependencies; 32% average reduction, 54% for long suites).

**The four required quotes** (verbatim with URLs in R1 §7):
- (a) static/import-time mutants: Stryker, PIT and coverage.py;
- (b) pytest-testmon's tracked and untracked inputs;
- (c) PIT `fullMutationMatrix` and test selection;
- (d) mutmut 3 per-mutant test selection, which is in source only.

**Transfer to Assay:**
- No established tool reruns the full coverage-instrumented suite in a fresh interpreter per mutant. Cosmic Ray is closest and still does not run coverage per mutant.
- The one change every tool makes that Assay can adopt **without weakening its claim** is **stop at the first verified failure** (cold witness), and put fast, relevant tests early *in the declared order*.
- Per-test coverage *selection* (skipping tests) is what makes PIT, Stryker and mutmut fast. It changes the survivor claim to "survived its covering tests". Assay's B105 claim is "survived the declared suite", so selection is excluded (Sol).
- Schemata and fork servers lose per-candidate process isolation and cannot handle the 190 import-time candidates. At most they could serve a non-qualifying screen.

**Not verified:** PIT's change hash; mutmut's exit-code mapping, killer attribution and cache invalidation on test edits; StrykerJS per-mutant test order; Stryker release dates; whether MutPy stops at the first failure; Major's prioritization numbers; the Descartes comparison; FaMT's numbers; the Untch figure; the Offutt'96 abstract; the AccMut venue; Ekstazi's safety text; the Cosmic Ray timeout status. I spot-checked the PIT FAQ and the Stryker static-mutants quotes myself.

## 9. Ranked strategies

| # | Strategy | Qualifying / dev-loop saving | CPU/RAM | Proof | Claim | Confidence | Plan |
|---|---|---|---|---|---|---|---|
| 1 | Screen → fix → qualify, plus the equivalence and hang policy | avoids hundreds of worker-hours per doomed campaign | — | reviewed ledger + audit | preserved | high | §9 runbooks, P10 |
| 2 | Cold witness + no-cov R2 command + witness eligibility | ≈520 s → S+C+P per kill | ≤ baseline | manifests, hooks, receipts | preserved; stricter kills | high | P3 |
| 3 | Tiered declared order (heavy last) | prefix shrinks up to ~99% for fast kills | none | R0/R1 run the same order | preserved | medium (setup costs unknown) | P1 |
| 4 | Work queue; ≤3 workers under 2 GiB aggregate | ~2–3× wall | peaks 738–987 MiB per full-suite process | deadline/fatal semantics | neutral | high | P4 |
| 5 | Loop guards | 1,518 s → seconds per hang; required for a PASS | — | ordinary tests | neutral | high | P2 |
| 6 | Snapshot C1/C2 (+C3/C4) | ≈0.6–1.4 s × 3,760 ≈ 0.6–1.5 worker-hours | — | op-count + guard tests | neutral | medium | P5 |
| 7 | Faster suite for survivors and R0 (PQ ignore, short windows in test lanes) | ≈520 → ≈365–385 s per survivor | — | B105 amendment (A-468) | preserved | medium | P1, P3c |
| 8 | Isolation units + coverage-guided unit order | per kill ≈1–6.5 s → 0.35–2.3 h | small processes | per-unit manifests, union proof, R1 combine | **changed execution model** | medium | P11 (gated) |
| 9 | Remove the duplicate R0/R1 | ≈9 min | — | — | neutral | high | not planned (preflight is a safety gate) |
| 10 | B106 eligibility now; B109 later | reruns only | — | B109 closure proof | preserved | — | enabled by P1+P3 |
| 11 | Schemata / fork server | 10–100× in screening [I] | low | — | non-qualifying only | low | not planned |
| 12 | Remote / Buildkite | moves work, doesn't remove it | measured only | acceptance gate | neutral | zero until measured | P9 |
| 13 | Sampling, diff-only, rolling | large | — | — | weaker, separately named claims | — | rejected for M7 |

## 10. Operator decisions from the interview (2026-09-28), with their consequences

1. **Equivalents: reviewed ledger** (not "survivors fail", not a relaxed floor).
   - Why: A-462 and the native judge make any survivor a FAIL. Some mutants are genuinely equivalent (behavior-preserving), and a test cannot kill them.
   - The ledger is committed, reviewed and anchored to a *stable site*: path, enclosing scope, operator, original→replacement token, ordinal, plus a semantic fingerprint. Line numbers or candidate IDs would change on any edit. This overlaps B109's need for stable site identity.
2. **Ledger execution: middle path** (option 1).
   - Option 1 (chosen): the qualifying run skips ledger candidates, and a mandatory audit runs each ledger candidate on the full declared suite, in the screen, at the **same commit**, before the qualifying run.
   - Option 2 (not chosen): execute ledger candidates inside the qualifying run.
   - Neither option changes PASS correctness, because equivalents are excluded from the score either way. What differs is where the "still survives" check lives: a separate audit receipt bound by commit, tree, judge identity and ledger digest (option 1), or inside the verdict itself (option 2).
   - Option 1 loses in-verdict freshness but saves a full suite (≈380–520 s) per ledger entry from the 6 h budget.
   - Any audit entry that is killed means the entry is wrong (the mutant is killable), and it is refused.
3. **Hangs: guards; A-464 unchanged.**
   - A-464 is the rule that time or host load never decides a classification. Counting `hung` or `budget_exceeded` as `killed` would make the verdict depend on a timeout, so a slow host could manufacture kills.
   - The guards instead make the code itself fail fast and deterministically (an `AssertionError` when a cursor does not advance). The suite then kills those mutants honestly, in normal time.
4. **Execution model: preserve the claim; units as a pilot-gated fallback.**
   - The single-process full-declared-suite claim stays.
   - Cold kills, tiering, the queue and snapshots reduce cost without changing what a kill or a survivor means.
   - The units model would change the declared execution semantics (cross-file order dependence would stop counting), so it needs its own decision.
5. **Suite scope (all four approved):**
   - `--ignore` the whole of `test_python_qualification.py` in the self-qualification lanes;
   - tiered layout;
   - lane-level liveness keys with guardrails; the operator confirmed this is the cleaner and more reusable option over test-only monkeypatching;
   - drop `--override-ini`, relying on and pinning `pyproject.toml`.
6. **Distributed / async.**
   - The operator asked whether the expensive work could run across several hosts, asynchronously, while development continues with a provisionally accepted first step, and what foresight that needs. The answer is yes.
   - The foresight: identity-bound portable records (a runtime fingerprint in the judge identity), a conflict-refusing import, consolidation by ordinary `--resume`, and explicit provisional/accepted status.
   - The screen and the ledger audit are the first async workloads, and they also measure remote hosts. The design is host-agnostic.
   - One consequence needs operator confirmation (plan §11.2): may same-revision records produced before the campaign deadline was initialized count toward the qualifying verdict?

## 11. Prior design reviews (conclusions carried forward)

- **GPT-6-Luna xhigh (R2 command design):**
  - an explicit CLI opt-in that derives the R2-only command, removing exactly the three coverage tokens and disabling pytest-cov; no lane-schema change; verdict v14;
  - three runtime manifests (coverage baseline, no-cov baseline, each candidate) as ordered count + digest captured **during the actual run**, not by `--collect-only`;
  - `judge_sha256` over the R2 plan, the manifest digest and the transform version;
  - re-run the R2 baseline before resume lookup;
  - persist each candidate's collection proof with its state record.
- **Luna (B109/v14 schema):**
  - `witness-cold` has exactly `{mode, witness}` with the existing five-field receipt;
  - v13 inputs to `--reuse-from` become a bounded cold start;
  - the adversarial cases listed in the P3 briefs;
  - "allow only the exact `--override-ini=pythonpath=src`". This is superseded by D4(c), which removes the override from the lane.
- **Sol (runtime rework):**
  - `witness-prefix` means prior-verdict replay; a new mode is needed;
  - test selection is honest only as a kill screen;
  - shard consolidation: fan the records into a coordinator, reject conflicts and mismatches, then run the original lane **without `--shard`, with `--resume`**. `merge_mutation_shards` alone is insufficient as a certificate.
- **Sol (B110 plan):**
  - priority: no-cov command + cold stop, then speed up the complete suite, then local parallelism within the same aggregate 2 GiB, then remote capacity;
  - coverage slices stay research;
  - a 64-candidate hash-stratified pilot, 90 min, stopping at 2 h.

## 12. Missing evidence that could change the plan

1. **Survivor and equivalent rates across the stratified inventory.** Only ordered prefixes from two files exist. This decides whether B105 is achievable, and how big the ledger gets.
2. **Where the first failing test sits for kills (`P`).** Witness capture was off and no failing-test data was retained. This is the decisive unknown for the claim-preserving path.
3. **Per-test setup/teardown and interpreter start-up.** Only call durations were forwarded.
4. **Isolated coverage overhead** (the no-cov baseline time).
5. **Snapshot cost measured by Assay itself.** Ours is a git-equivalent approximation.
6. **Peak RSS of cold runs and at 2–3 concurrent workers.**
7. **Cross-file order dependence**, which would break tiering or units, and the per-unit start cost.
8. **Remote capacity:** zero until measured.
9. **Whether `timeout`'s SIGTERM orphans candidates** (P6 fixes it regardless).

## 13. Citations

**Repository** (relative to `assay/` at `db85f747`):
- `assay.toml:78-182` (the self-qualification lane; argv 82-90; `budget = "5h"` 96; `allow_argv_append = false` 97; isolation 99-106; mutation 172-177); `:185+` (preflight);
- `run-gate.toml:33-60`;
- `pyproject.toml:99`;
- `src/assay/mutation.py`:
  - 220, 225;
  - 1052 (`judge_sha256`);
  - 1273-1402, 1450-1497 (state and execution records);
  - 1500, 1529 (shards);
  - 1668-1692 (classification);
  - 1702-1735 (SQL equivalence);
  - 1892-1909 (auto budget);
  - 1912-2557 (`run_mutation`);
  - 2559-3035 (`_execute_mutation_jobs`; waves 2889-2994);
  - 3748-3769 (the PASS rule);
- `src/assay/isolation.py`: 67, 703-863, 970-1009, 1011-1085, 1154-1166, 1870-1958, 1961-2132;
- `src/assay/mutation_witness.py`: 18, 33-74, 146-204, 228-263, 308-321, 324-493;
- `src/assay/liveness.py`: 207-225, 523-526, 529-544, 586-625, 641-646, 830-947, 950-1013, 1299-1537;
- `src/assay/cli.py`: 255-367, 1584-1588, 1786-1803;
- `src/assay/runner.py`: 210-308, 687-741, 936, 1168-1182, 1301-1339, 3561-4583, 4099;
- `src/assay/verdict.py`: 350, 1473-1583, 2523-3111, 2934-2970, 5041-5053;
- `src/assay/verify.py`: 1286-1302, 1619-1814, 2011-2196, 3012-3091;
- `src/assay/config.py`: 226-238, 438-450, 3082-3106, 3144-3185;
- `src/assay/errors.py`: 57-66, 196-276;
- `src/assay/reuse.py:13-14`;
- `tests/conftest.py`: 52-56, 59, 165-212, 215, 326-334, 342-347, 1431-1499;
- `tests/test_python_qualification.py:546-555`;
- `tests/test_cli_run.py:463, 586`;
- `tests/test_liveness.py:581, 601, 656, 803`;
- `tests/test_self_lane.py`: 114-135, 176-200, 239-244;
- `tests/test_b105_mutation_boundaries.py:432`;
- `tools/b105_report_check.py:14-147`;
- `tools/self-qualification-gate.sh`;
- `tools/tester-unified-gate.sh`: 141-150, 491-523, 576-654;
- `nyxloom-trove/decisions.md`: 940-943;
- `nyxloom-trove/4-backlog.md`: 10661, 10738, 10795, 10858, 10949, 11028;
- `nyxloom-trove/reports/assay-B105-LOG.md`: 58-74, 182, 193, 212-215, 240-250, 258, 296-315;
- `nyxloom-trove/carve-assets/P22/test_acceptance.py:266-309`;
- `nyxloom-trove/carve-assets/P33/test_acceptance_v5.py:406-417`;
- `../run-gate-project/run-gate.py:6462-6565`.

**External** (retrieved 2026-09-28):
- **PIT:**
  - https://pitest.org/faq/
  - https://pitest.org/quickstart/maven/
  - https://pitest.org/quickstart/basic_concepts/
  - https://pitest.org/quickstart/incremental_analysis/
  - https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestWorker.java
  - https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/DefaultTestPrioritiser.java
  - https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/MutationTestUnit.java
  - https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/DefaultGrouper.java
  - https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestMinion.java
  - https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/TimeOutSystemExitSideEffect.java
  - https://docs.arcmutate.com/docs/git-integration
  - https://stamp-project.github.io/pitest-descartes/
- **Stryker:**
  - https://stryker-mutator.io/docs/stryker-js/configuration/
  - https://stryker-mutator.io/docs/stryker-net/configuration/
  - https://stryker-mutator.io/docs/mutation-testing-elements/static-mutants/
  - https://stryker-mutator.io/docs/stryker-js/incremental/
  - https://stryker-mutator.io/blog/stryker-js-v6-expeditious-superior-mutations/
  - https://stryker-mutator.io/blog/announcing-stryker-4-mutation-switching/
  - https://stryker-mutator.io/blog/mutation-switching/
  - https://stryker-mutator.io/docs/stryker-net/technical-reference/mutant-schemata/
  - https://github.com/stryker-mutator/mutation-testing-elements/blob/master/packages/report-schema/src/mutation-testing-report-schema.json
- **mutmut 3.8.0:**
  - https://github.com/boxed/mutmut/blob/3.8.0/README.rst
  - https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/__main__.py
  - https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/runners/harness.py
  - https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/mutation/trampoline.py
  - https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/stats.py
  - https://mutmut.readthedocs.io/en/latest/
- **Cosmic Ray:**
  - https://cosmic-ray.readthedocs.io/en/latest/concepts.html
  - https://cosmic-ray.readthedocs.io/en/latest/tutorials/intro/index.html
  - https://cosmic-ray.readthedocs.io/en/latest/tutorials/distributed/index.html
  - https://cosmic-ray.readthedocs.io/en/latest/how-tos/filters.html
  - https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/testing.py
  - https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/mutating.py
- **cargo-mutants:**
  - https://mutants.rs/how-it-works.html
  - https://mutants.rs/performance.html
  - https://mutants.rs/workspaces.html
  - https://mutants.rs/nextest.html
  - https://mutants.rs/timeouts.html
  - https://mutants.rs/shards.html
  - https://mutants.rs/in-place.html
  - https://mutants.rs/iterate.html
  - https://mutants.rs/in-diff.html
- **Other tools:**
  - https://github.com/EvanKepner/mutatest
  - https://github.com/mutpy/mutpy/blob/master/mutpy/utils.py
  - https://github.com/mutpy/mutpy/blob/master/mutpy/test_runners/base.py
  - https://mutation-testing.org/docs.html
  - https://mull.readthedocs.io/en/latest/HowMullWorks.html
  - https://mull.readthedocs.io/en/latest/MullConfig.html
- **Google:**
  - https://research.google.com/pubs/archive/46584.pdf
  - https://arxiv.org/abs/2102.11378
  - https://arxiv.org/abs/2103.07189
- **coverage.py / pytest-cov / testmon:**
  - https://coverage.readthedocs.io/en/latest/contexts.html
  - https://coverage.readthedocs.io/en/latest/config.html
  - https://pytest-cov.readthedocs.io/en/latest/contexts.html
  - https://testmon.org/
  - https://testmon.org/blog/determining-affected-tests/
  - https://testmon.org/blog/hidden-test-dependencies/
- **Papers:**
  - https://agroce.github.io/issre15.pdf (Gopinath ISSRE'15)
  - https://lingming.cs.illinois.edu/publications/issta2013a.pdf (FaMT)
  - https://mir.cs.illinois.edu/marinov/publications/ZhangETAL12RegressionMutationTesting.pdf (ReMT)
  - https://lingming.cs.illinois.edu/publications/icst2018.pdf (Chen & Zhang)
  - https://users.ece.utexas.edu/~gligoric/papers/GligoricETAL15Ekstazi.pdf (Ekstazi)
  - https://lingming.cs.illinois.edu/publications/issta2016.pdf (PMT)
  - https://arxiv.org/abs/1702.06689 (AccMut)
  - https://pure.tudelft.nl/ws/portalfiles/portal/45811968/main.pdf (Zhu et al.)
  - https://dl.acm.org/doi/10.1145/154183.154265 (Untch'93)
  - https://dl.acm.org/doi/10.1145/227607.227610 (Offutt'96)
  - https://ieeexplore.ieee.org/document/1702959/ (Howden'82)

## Appendix: research records

The records in [`b110/research/`](b110/research/) are verbatim outputs of read-only research agents, 2026-09-28, with line numbers at `db85f747`. Several mention the session's scratchpad, where the probe scripts lived; those scripts are copied into `b110/research/scripts/`.

| File | Content |
|---|---|
| `R0-initial-analysis.md` | the first terminal deliverable (superseded where §6 corrects it) |
| `R1-external-tools-research.md` | full primary-source research with quotes and the unverified list |
| `R2-snapshot-internals.md` | per-step snapshot inventory, timings, invariants, designs A/B, stale pyc, C1–C4, G/R tests |
| `R3-v14-cold-witness-map.md` | v14 traps, file:line insertion points, v13 cut checklist, ambiguities A1–A9 |
| `R4-executor-liveness-planner-deadline-pilot-resources.md` | executor invariants and tests; liveness keys; planner; deadline; pilot subset; resources |
| `R5-test-tiers-pathwheel-hangs-dataclasses-order.md` | heavy tier, PATH-wheel analysis, hang summary, dataclass spec, liveness leak |
| `R6-hang-loop-inventory.md` | the full 54-loop table with classes and simulated hangs |
| `R7-order-dependence-scan.md` | module-state and test-global-state scan |
| `R8-heavy-test-classification.md` | the per-test table (line, call s, category, what executes, fixtures, dominant cost) |
| `scripts/` | the probe scripts (hang simulator, dataclass contract prototype, table generator, dependency and state scans); research only, not product code |
