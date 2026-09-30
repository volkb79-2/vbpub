# B105 R2 runtime: independent analysis

**What I analyzed.** The target checkout is `/workspaces/vbpub/.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy`, on branch `assay-b105-evidence-integrity`. HEAD is `db85f74784c354188650939e6ade1354ae29f3fa`, as expected, and the working tree is clean.

The runtime evidence comes from two stopped attempts:
- **30eec294:** 15 candidates plus the baseline's 5,831 per-test events. The stream is at `.worktrees/assay-b105-gate-copy-30eec294/assay/.assay/progress-self-qualification.jsonl`, inside the target checkout.
- **e79eb8f5:** 39 candidates, at `/tmp/vbpub-b105-ciu-root-20260926/.worktrees/assay-b105-self-qualification/assay/.assay/`.

30eec294 is not an ancestor of HEAD because the branch was rebased. However, `git diff 30eec294 db85f747 -- assay/src` is empty. Only `assay.toml`, `run-gate.toml`, `tests/test_self_lane.py` and the gate script differ, so the evidence applies to the analyzed source.

I changed nothing and ran no gate or campaign. I ran three light, read-only probes:
- a version probe inside `tester-unified:local`;
- niced git timings that approximate the snapshot steps, in the scratchpad;
- AST counts over the retained plan.

**Labels:** [M] measured from retained evidence · [C] calculated · [A] assumption or approximation · [I] inference · [D] documented by an external source.

---

## 1. Recommendation and root cause

**Why the runtime is so far off:**
1. **Every candidate runs the whole declared command to the end, including killed ones.** Every killed candidate recorded `tests_completed: 5831`. Killed and surviving candidates cost the same [M]:

   | Run | Killed mean | Survived mean | Baseline |
   |---|---|---|---|
   | 30eec294 | 559.5 s | 561.1 s | 519.9 s |
   | e79eb8f5 | 530.9 s | 515.8 s | 504.6 s |

   So one candidate costs about one baseline.
2. **The planner's 62h40m had no evidence behind it.** `assay plan` executes nothing, and every "auto" budget falls back to a hard-coded 60 s per candidate (`assay/src/assay/cli.py:1786-1801`). The measured preflight baseline (~520 s) already existed. It is about 9× larger than 60 s [C].
3. **Spinning hangs cost three baselines each.** The auto bound is `max(3×baseline, baseline+60)` (`mutation.py:1892-1909`). In e79, 4 of 39 candidates were `budget_exceeded` at 1,518 s each, which is 24.8% of that run's candidate time [M]. These are scanner loops, for example `adapters/go.py:292`. They spin the CPU, so liveness correctly does not call them `hung`.
4. **Cost is concentrated in a few tests, and they run in the wrong place** [M]:
   - 113 tests of 0.5 s or more account for 317.7 s, 72% of the 441.7 s of call time.
   - 4,875 tests take under 10 ms each and about 4 s in total.
   - Files run alphabetically, so 99% of call time has been spent before the `test_v*` files run. Those files test `verdict.py` and `verify.py`, which together hold 950 of the 3,760 candidates.
5. **Work that cannot detect a mutant.** 73.6 s per candidate goes to tests that run only the *unmutated* run-venv wheel on PATH (`tests/test_python_qualification.py:546-555`). Another 66.7 s goes to two tests that wait on real clocks (`tests/test_cli_run.py:463,586`) [M].
6. **CPU goes unused.** The container has 3 CPUs, but the campaign uses 0.83–0.89 cores on average [M]. With `jobs>1`, the executor submits fully joined *waves* (`mutation.py:2893-2901`). Every worker would then wait for the slowest candidate in its wave [I].

**The structural issue, which matters more than runtime: a passing B105 campaign contains only kills.**
- For native lanes, `judge_mutation` returns PASS only when there are no survivors, `budget_exceeded`, `hung` or `crashed` candidates (`mutation.py:3748-3769`).
- Native lanes cannot set `fail_under`.
- Marking a mutant `equivalent` is supported only for SQL (`config.py:3082-3106`).
- A-462 already states that a survivor cannot pass.

The opening prefix had 7/15 survivors (30eec294) and 9 survivors plus 4 hangs in 39 (e79). The outcomes were identical on the 15 candidates both runs share [M]. Five of the seven 30eec294 survivors are `@dataclass(frozen=…, kw_only=…)` flag flips. The plan contains 148 such candidates.

It follows that B110's 273 survivor worker-hours is **the cost of a campaign that would FAIL anyway**. Survivor cost belongs to a find-and-fix loop. A *qualifying* run costs the sum, over 3,760 kills, of the time to the first verified failure. That is dominated by fixed per-candidate cost:
- the snapshot, about 2–4 s [A];
- interpreter start plus collection, about 7.4 s [C];
- the position of the first failing test in the declared order.

**What I recommend:**
1. **Do not start another full campaign yet.** First, a cheap *non-qualifying* survivor screen plus fixes must reach zero survivors and zero hangs, or each remaining one must have an accepted disposition.
2. **Decide the Python equivalent-mutant and hang policy now.** It, not runtime, is the real blocker.
3. **Build B110's cold witness and mutation-only command**, plus four things B110 lacks:
   - a re-declared, tiered test order (fast tests first) that R0, R1 and R2 all use;
   - a work-queue executor, with 3 workers for cold runs;
   - the cold prefix recorded as a digest and length, not a list of node IDs;
   - progress guards in scanner loops.
4. **Run the pilot.** If the qualifying run projects above about 4.5 h under today's single-process claim, adopt the named *isolation-unit* model (§5, row 8). It is the only design here in which ordering tests per candidate is sound.
5. **B108 and B109 do not shorten the first campaign.** B106 is merged but inert for this lane (§3).

## 2. Cost model

**Formulas:**
- Today: `T ≈ T_fixed + Σ(S + F)`, where F ≈ 505–520 s per kill or survivor and ≈ 3F per spinning hang. This gives about 585 worker-hours, which matches B110 [C].
- A qualifying run with cold kills, all kills: `T ≈ T_fixed + Σ(S + C + P_i)/W_eff`.
- `T_fixed` [A], about 35–40 min:
  - setup, about 1–3 min;
  - preflight, 526–548 s [M];
  - the lane's own baseline, 504–520 s [M];
  - B110's uninstrumented baseline, about one suite run;
  - the R3 control, about one suite run (`canary.py:300`);
  - consolidation and verify, a few seconds.

| Stage | Scales with | Evidence | Accidental? | Possible structural saving |
|---|---|---|---|---|
| Clone exact source, build wheel, create venvs | gate | not timestamped | no | — |
| Preflight R0/R1 | gate | 526–548 s [M] | **duplicated**: R2 starts on an R0 PASS even when R1 refuses (`runner.py:4099`; attempt 3 in LOG) | reuse the preflight, or gate R2 on R1: ~9 min |
| Lane baseline R0/R1 | gate | 519.9 / 504.6 s [M] | see above | same |
| R3 control and transformed canary | gate | ≈1 suite [I] | no | cold run for the transformed half |
| Snapshot per candidate: whole vbpub repo, full history (5,296 files / 67 MiB written, 62 MiB of packs copied, ~55k-object closure walk, status, manifest proof; `isolation.py:762-863, 1154-1166, 1870-1958`) | candidates | ≈2–4 s from git-equivalent timings at load ≈ 8/8 cores [A] | yes, ×3,760 | copy-on-write or a scoped snapshot → under 1 s; **the largest fixed cost once tests are cheap** |
| Interpreter start and collection of 5,831 tests | candidates | ≈7.4 s: leading gap = 22.21/3 (`liveness.py:1008-1010`) [C] | inherent to the single-process claim (the manifest must be proven) | per-unit collection (units model) |
| Coverage: `sysmon` core on Python 3.14.6 / coverage 7.16.1 / pytest-cov 7.1.0, plus the JSON report | tests executed | not isolated; an uncontrolled comparison (R0 without coverage 573 s vs preflight with coverage 548 s) [M] suggests it is small | mainly because it **blocks witnesses** (§3) | B110's command without coverage |
| Test calls | tests executed | 441.7 s in total; the heavy 113 take 317.7 s [M] | yes for kills | cold kill plus test order |
| Setup, teardown and session fixtures (e.g. `standalone` builds a wheel and venv, `tests/conftest.py:1431-1499`) | candidates | ≈71 s left over [C] | partly | tiers, fixture scope |
| Tests that only exercise the PATH wheel | candidates | 73.6 s [M] | yes for R2 | move to the release lane |
| Two liveness tests that wait on real clocks | candidates | 66.7 s [M] | test design | windows configurable per lane |
| Spinning hang | hangs | 1,518 s each [M] | yes | loop progress guards |
| Dirt check, liveness monitor, state writes | candidates | ≲1 s [A] | minor | — |
| Wave-synchronous executor | waves × slowest | not exercised (`jobs = 1`) | yes | work queue |
| Consolidation and verify | gate | seconds [A] | **risk**: storing B110's started prefix as node lists would take up to ~5,831 × ~80 B per candidate. That exceeds the 16 KiB receipt (`mutation_witness.py:18`) and the 16 MiB reuse cap (`reuse.py:13`) | manifest digest + length + failed index |
| Resume and retry | reruns | resume skips judged candidates; any change to the tree invalidates everything (the judge identity digests the tree) | — | B106/B109, reruns only |
| Queue, remote, Buildkite | shards | capacity 0 | — | — |

**Minimum cost, and what is accidental:**
- Under today's claim, a kill must pay at least the minimum snapshot cost, full collection and the declared-order prefix up to the first failure. A survivor must pay the full suite.
- For a qualifying run, the snapshot and collection alone come to 3,760 × about 6.5–8 s ≈ 6.8–8.4 worker-hours. On 3 workers that is 2.3–2.8 h [C]. That leaves about 2.2–2.7 h for the summed prefixes, so the mean prefix must be about 6–8 s or less, **including setup and teardown**.
- That is plausible but unproven. In a fast-first order, the 4,665 fastest tests cost only 3.0 s of call time [M], but setup and teardown per test were never retained.
- Everything else in the table is accidental.

## 3. Challenging the plan (condensed)

- **Command without coverage.** Keep it, but for the right reason. pytest-cov registers a `pytest_runtestloop` wrapper and a `pytest_runtest_call` hookwrapper (probed in the image). The witness plugin rejects any lifecycle hook that is not built in (`mutation_witness.py:347-395`), so under coverage no receipt is ever produced.
  - A separate blocker: the B105 argv contains `--override-ini=pythonpath=src`, and any `--override-ini` disables witness capture (`mutation_witness.py:60-63`). That override repeats `pyproject.toml:99` (`pythonpath = ["src"]`).
  - Dropping it, with a drift test on `pyproject.toml` instead, makes the lane eligible without B110's special allow-list exception.
  - As things stand, **B106's `--reuse-from` is merged (A-461, e5e9b95c) but inert for B105**: its state records hold only `{"mode":"full"}`. The backlog still lists B106 as OPEN, which is stale.
- **Cold witnesses.** Today "killed" already means any non-zero exit, including a collection error (`runner.py:1301-1339`, `mutation.py:1668-1692`). The eight full-suite kills in 30eec294 each bought a single retained bit. A cold kill is a *stricter* kill claim that arrives sooner.
- **Test order.** Reordering tests per candidate inside one process is exactly what A-464 rejects, and it is right to. But the *declared* order is free to choose:
  - Put the heavy tests in a last tier.
  - List the tiers as directories in the argv.
  - R0/R1 then prove that order.

  This preserves the claim. It helps kills only, not survivors.
- **Schemata, fork server, persistent worker** (mutmut 3.8 trampoline and fork [D]).
  - They would bring snapshot plus collection cost down to milliseconds.
  - But they execute instrumented code rather than the exact mutant snapshot.
  - 190 of 3,760 candidates (5.1%; 189 are `bool-const-flip`, including all 148 dataclass flags) are import-time code and cannot be switched after import [M].
  - They also lose per-candidate isolation.
  - Use them only in the non-qualifying screen, if at all.
- **Coverage-based test selection.**
  - No subprocess measurement is configured: no coverage `patch`, and `COVERAGE_PROCESS_START` is not set. Import-time lines land in the empty context [D].
  - Assay's tests run a lot of Assay in child processes (standalone wheels, the CLI).
  - So a per-test coverage map is safe **only for ordering**.
  - Skipping tests for survivors would need file-level dependency tracing à la Ekstazi, covering non-Python children too (git reads the whole tree). It would also need isolation from shared state. Few Assay tests would qualify, and a qualifying run has no survivors anyway.
- **Reorganizing the tests.** This is worthwhile:
  - tiers, with the 113 heavy tests isolated;
  - liveness windows configurable per lane, so the end-to-end path stays real;
  - move the 74 s of PATH-wheel tests to the release lane, following the `test_self_hosting.py` precedent;
  - one reflective test of the dataclass contract, which should kill up to 148 flag flips in milliseconds.
- **Sharding.** Shards schedule work; they do not reduce it. With costs this heavy-tailed, hash shards are skewed.
  - Locally, use a queue. Across hosts, assign by longest predicted cost first.
  - The shard merge exists (`mutation.py:1529`) but is deferred (B023).
  - `--reuse-from` refuses `--shard` (`cli.py:1584-1588`).
- **Reuse (B106/B109).** Once cold kills exist, a B106 replay costs about as much as a fresh cold kill. B109 saves one whole candidate cost per carried kill (~10–15 s), but its dependency proof is only tractable with isolation units. Neither shortens the first campaign.
- **Sampling, diff-only runs, lower frequency.** These establish a different claim: an estimate or a change-local result (Gopinath [D]; Google [D]). They suit regular cadence, not M7.

## 4. How other mutation tools do it (primary sources, retrieved 2026-09-28)

| Tool | Test selection / order | Kill | How the mutant is inserted / process | Incremental | Transfer to Assay [I] |
|---|---|---|---|---|---|
| **PIT 1.30.0** | Per-test coverage; tests that don't reach the line are discarded; the rest run fastest first, with the class's own unit tests weighted first (FAQ, spot-checked) | Stops at first failure by default; `fullMutationMatrix` is opt-in | Class hot-swapped into a long-lived minion JVM; timeout per test (factor 1.25 + 4000 ms) | "Experimental"; everything except "prioritise the previous killer" introduces "a degree of potential error" | The template for the pipeline; static initializers are not mutated |
| **StrykerJS 10.0.0 / .NET 4.16.0** | `perTest` coverage by default | Bails by default | Mutant schemata with an active-mutant switch; hot reload; static mutants run **all tests**, in a fresh worker, sorted last, or can be ignored (static-mutants page, spot-checked) | Documented false positives and negatives | Report schema: `killedBy` / `coveredBy` / `testsCompleted` / `static` |
| **mutmut 3.8.0** | A stats run maps functions to tests | `pytest -x` | Trampoline plus fork, or forkserver; mutates only inside functions | Function hashes; keeps the cache on dependency change by default | Closest Python design; not audit-grade |
| **Cosmic Ray 8.7.0** | None: the full suite for every mutant | Exit code | Rewrites the file on disk, runs a subprocess; session database | Resumable only | Same cost structure as Assay today |
| **cargo-mutants 27.1.0** | Tests of the mutated package only | Stops early with nextest | Build and test per mutant; `--shard k/n` | `--iterate` is described as "a heuristic" | Shards must share identical arguments |
| **Google (ICSE-SEIP'18)** | Covering tests for changed lines only | — | At most one mutant per line; arid nodes skipped | Diff-based | States a full score is "infeasibly expensive"; a different claim |
| **Research** | FaMT orders tests per mutant (reduction is unsafe for survivors); ReMT and Chen & Zhang (ICST'18) cover reuse and test selection for mutation | — | Untch'93 schemata; split-stream execution / AccMut | Ekstazi file-level dependencies | PMT predicts, so it is not evidence; Gopinath: 1,000 samples ≈ 0.62% score error |

No established tool re-runs the full, coverage-instrumented suite in a fresh interpreter for each mutant. Cosmic Ray, the closest match, still does not run coverage per mutant.

## 5. Ranked strategies

Savings figures are [C] from §2 unless marked otherwise.

| # | Strategy | Qualifying-run / dev-loop saving | CPU / RAM | Change | Proof needed | Effect on the claim | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | Screen → fix → qualify, plus a decision on equivalents and hangs | Avoids 100s of worker-hours per doomed campaign | — | process, new backlog item | a reviewed disposition policy (A-463-style exact inventory), or refactoring | preserved | high |
| 2 | B110 cold witness + no-coverage command + the witness fixes above | ~520 s → S+C+P per kill | ≤ baseline | v14 schema (prefix as digest) | manifest, hook and receipt proofs (B110) | preserved; kill evidence gets stricter | high |
| 3 | Re-declared tiered order (heavy tests last) | Prefix shrinks by up to ~99% for fast kills | none | move test files, argv | R0/R1 run the same order | preserved | medium (setup costs unknown) |
| 4 | Work queue instead of waves; 3 workers cold, ≤2 full | ~2–3× wall on CPUs already allocated | ≤2 GiB (full-suite peak 738–987 MiB [M]) | executor | deadline and fatal semantics preserved | neutral | high |
| 5 | Loop progress guards | 1,518 s → seconds per hang; also required for a PASS | — | source | ordinary tests | neutral | high |
| 6 | Scoped or copy-on-write snapshot | ~2–3 s × 3,760 ≈ 2–3 worker-hours | — | isolation | manifest proof kept. **Hazard**: fixed mtimes (`isolation.py:67,1942`) plus a same-size mutant (`==`→`!=`) would pass pyc validation if `__pycache__` is ever reused [I] | neutral | medium |
| 7 | Faster suite for survivors and R0 (PATH-wheel tests out, clock windows, fixtures) | ~520 → ~385 s per survivor | — | tests, lane config | B105 acceptance amendment | preserved (release lane keeps those tests) | medium |
| 8 | **Isolation-unit suite** (fresh process per test file) with a coverage-guided unit order per candidate | Per kill ≈ S + unit start (~0.5–1.5 s [A]) + K ≈ 1–6.5 s → 0.35–2.3 h on 3 workers | small processes | runner and R1 coverage-combine design | union of per-unit manifests = full manifest; per-unit dirt check | **changed declared execution model**, not a narrower inventory; order-dependent cross-file behaviour stops counting | medium |
| 9 | Remove the duplicate R0/R1 run | ~9 min | — | runner/lane | — | neutral | high |
| 10 | B106 eligibility now; B109 later | Reruns only | — | — | B109 closure proof | preserved | — |
| 11 | Schemata / fork server | 10–100× faster screening [I] | low | large | — | **non-qualifying screen only** | low |
| 12 | Remote / Buildkite | Moves work, does not reduce it | measured capacity only | infrastructure | acceptance gate | neutral | zero until measured |
| 13 | Sampling, diff-only, rolling qualification | Large | — | — | — | **weaker, separately named claims** | — |

Rough totals for the qualifying run [C]:
- Row 2 alone on today's alphabetical order: if the mean prefix is about 3–10 s, 3.5–7.3 h on 3 workers plus ~40 min fixed. That only fits the 6 h target at the low end. Rows 3 and 6 are what make the low end plausible.
- Row 8 (units): about 0.35–2.3 h plus fixed.

## 6. Architecture and package order

**Pipeline:** exact revision → one R0/R1 run (preflight reused) → survivor screen (non-qualifying; cold, tiered, work queue) → fix/triage loop → qualifying run with a persisted absolute deadline, cold kills, and one ordinary verdict.

**Measuring real savings.** Every campaign should report three ledgers:
- per-candidate CPU- and wall-seconds, plus a histogram of executions per candidate;
- fixed work;
- idle time: queue, waves, and retries or revalidation.

A change is a genuine reduction only if per-candidate work drops for the same inventory and outcomes. Shards and remote workers should leave that number unchanged.

**Package order:**
1. **B110a**, small, no schema change:
   - the planner reads the measured baseline;
   - retain setup/call/teardown events;
   - make the lane witness-eligible;
   - record per-candidate peak RSS and CPU.
2. **B110b:** cold witness v14, the command without coverage, manifests, and the prefix stored as a digest.
3. **B110c:** work-queue executor and persisted deadline. Shards follow later.
4. **B110d:** tiered test layout, clock windows, relocating the PATH-wheel tests, the dataclass contract test, loop guards.
5. **New B111, blocking B105:** a policy for equivalent mutants and hangs in native Python.
6. **B108a**, the deterministic campaign-analysis core. The pilot and screen need it. B108b, the run-gate auto-closeout, comes later.
7. **Pilot** → go/no-go → survivor screen and fixes → **B105** qualifying run.
8. **B109**, afterwards. Its design depends on whether row 8 is adopted.

Also: correct B106's stale status line; the "B110 must pilot 64 candidates in 90 minutes" rule is impossible on today's path (about 9.6 worker-hours [C]), so the pilot must come after B110b.

## 7. Pilot (≤90 min, absolute stop at 2 h)

**Where it runs:** local `tester-unified`, 3 CPUs, 2 GiB RAM / 8 GiB RAM+swap, one gate container. It happens after B110b and B110c exist.

**Phases:**

| Phase | Time | Work |
|---|---|---|
| A | 0:05–0:20 | Run the baseline without coverage, in the tiered order, with per-phase timings. Time `--collect-only` and 5 snapshot creations. Record peak RSS. |
| B | same window, concurrently if RAM stays under 1.6 GiB | One run with per-test coverage (`--cov-context=test`, `ctrace` core) to build a per-test coverage map, then an **offline projection** of each of the 3,760 candidates' prefix. |
| C | 0:25–1:15 | 64 hash-selected candidates (one per each of the 47 candidate-bearing files, all 4 operators, the rest filled by hash rank), plus a separately reported 6-candidate "known-hard" set (the 4 e79 hang sites and 2 import-time sites). Cold runs, work queue with 3 workers. Pilot-only cap of 150 s; a capped candidate is recorded as *unresolved*, never classified. At most 3 full-suite survivor runs, one at a time. |
| D | 1:15–1:25 | Three hash shards → merge → `assay verify`. Replay the measured durations offline to compare shard skew under hash shards versus the queue. |

Budget: about 75–110 of the 150 available worker-minutes.

**Outputs:**
- the cold-kill rate, with a Wilson 95% interval of about ±12 points at n = 64;
- snapshot, collection and prefix distributions;
- survivor duration;
- per-worker and aggregate peak RSS and memory-full stall time;
- shard skew;
- consolidation time;
- the error of the coverage-map projection.

**Limitations:**
- 1.7% of the inventory, stratified by file and operator, not by how hard a mutant is to kill.
- The survivor and hang tails are censored by the cap.
- The host is shared with a production server, so timings are noisy.
- The results are not qualification evidence.

It improves on the first-15 prefix because it measures the cost *components* and then projects over the whole inventory, instead of multiplying out a mean.

**GO for a full run only if all of these hold:**
1. The screen shows 0 survivors, hangs and crashes, or each has an accepted disposition.
2. Projected fixed overhead plus the summed per-candidate cost over the effective worker count is ≤ 5 h using p90 stratum costs, and ≤ 4 h at the median.
3. Aggregate peak RSS is ≤ 1.6 GiB, and memory-full stall is ≤ 5% of wall time.
4. Measured fixed overhead is ≤ 60 min, and the pilot verdict is verifier-accepted.
5. B110b/c and B111 have been reviewed.

Otherwise, change the execution model (row 8) or add measured remote capacity. Never lengthen the timeout.

## 8. Citations

**Repository** (paths relative to the checkout):
- `assay/assay.toml:78-182` — the lane: argv 82-90, `budget = "5h"` 96, full-history isolation 99-106, `jobs = 1` 172-177.
- `assay/run-gate.toml:33-45` — 3 CPUs / 2g / 8g, `7h30m`.
- `assay/src/assay/mutation.py`:
  - 1668-1692 — result classification;
  - 1892-1909 — the auto bound;
  - 2039 — a fresh snapshot per mutant;
  - 2605, 2678-2758 — the per-candidate path;
  - 2893-2901 — waves;
  - 3748-3769 — the PASS rule.
- `assay/src/assay/isolation.py:67, 762-863, 1011-1085, 1154-1166, 1870-1958`.
- `assay/src/assay/mutation_witness.py:18, 60-63, 347-395, 464-484`.
- `assay/src/assay/liveness.py:950-1013`.
- `assay/src/assay/cli.py:1786-1801, 1584-1588`.
- `assay/src/assay/runner.py:1301-1339, 4099`.
- `assay/src/assay/config.py:3082-3106`.
- `assay/src/assay/reuse.py:13`.
- `assay/pyproject.toml:99`.
- `assay/tests/conftest.py:1431-1499`.
- `assay/tests/test_python_qualification.py:546-555`.
- `assay/tests/test_cli_run.py:463, 586`.
- `assay/nyxloom-trove/decisions.md:941-943` (A-462 to A-464).
- `assay/nyxloom-trove/4-backlog.md:10661, 10738, 10858, 10949, 11028` (B105, B106, B108–B110).
- `assay/nyxloom-trove/reports/assay-B105-LOG.md:81, 193, 215, 258, 304, 311`.

**External:**
- PIT 1.30.0: [FAQ](https://pitest.org/faq/), [maven](https://pitest.org/quickstart/maven/), [incremental](https://pitest.org/quickstart/incremental_analysis/), [MutationTestWorker.java](https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestWorker.java).
- Stryker: [config](https://stryker-mutator.io/docs/stryker-js/configuration/), [static mutants](https://stryker-mutator.io/docs/mutation-testing-elements/static-mutants/), [v6](https://stryker-mutator.io/blog/stryker-js-v6-expeditious-superior-mutations/), [incremental](https://stryker-mutator.io/docs/stryker-js/incremental/), [report schema](https://github.com/stryker-mutator/mutation-testing-elements/blob/master/packages/report-schema/src/mutation-testing-report-schema.json).
- mutmut 3.8.0: [README](https://github.com/boxed/mutmut/blob/3.8.0/README.rst), [harness.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/runners/harness.py).
- Cosmic Ray: [concepts](https://cosmic-ray.readthedocs.io/en/latest/concepts.html), [testing.py](https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/testing.py).
- cargo-mutants: [shards](https://mutants.rs/shards.html), [iterate](https://mutants.rs/iterate.html).
- Google: [ICSE-SEIP'18](https://research.google.com/pubs/archive/46584.pdf).
- coverage.py: [contexts](https://coverage.readthedocs.io/en/latest/contexts.html), [config](https://coverage.readthedocs.io/en/latest/config.html).
- testmon: [site](https://testmon.org/), [hidden dependencies](https://testmon.org/blog/hidden-test-dependencies/).
- Papers: [Gopinath ISSRE'15](https://agroce.github.io/issre15.pdf), [FaMT](https://lingming.cs.illinois.edu/publications/issta2013a.pdf), [ReMT](https://mir.cs.illinois.edu/marinov/publications/ZhangETAL12RegressionMutationTesting.pdf), [Chen & Zhang ICST'18](https://lingming.cs.illinois.edu/publications/icst2018.pdf), [Ekstazi](https://users.ece.utexas.edu/~gligoric/papers/GligoricETAL15Ekstazi.pdf), [PMT](https://lingming.cs.illinois.edu/publications/issta2016.pdf), [AccMut](https://arxiv.org/abs/1702.06689).
- I checked the PIT FAQ and the Stryker static-mutants quotes myself. The Untch'93 "300%" figure and Major's prioritization numbers could not be verified.

## 9. Missing evidence that could change this

- **Survivor and equivalent-mutant rates across the stratified inventory.** Only an ordered prefix from two files exists. This decides whether B105 is achievable at all.
- **Where the first failing test sits for kills.** Witness capture was off, and no failing-test data was retained.
- **Per-test setup/teardown and collection time.** Only call durations were forwarded; collection time is inferred from calibration.
- **Coverage overhead, isolated.** The only comparison is uncontrolled.
- **Snapshot cost measured by Assay itself.** Mine is a git-equivalent approximation.
- **Peak RSS for cold runs and at 2–3 concurrent workers.**
- **Cross-file order dependencies**, which would break tiering or units, and the per-unit startup cost.
- **Host contention.** Load was about 8 on 8 cores during this analysis; it is shared with production. Remote capacity is unmeasured.

I can publish this as a private artifact page or write it into `assay/nyxloom-trove/reports/` if you want it kept.