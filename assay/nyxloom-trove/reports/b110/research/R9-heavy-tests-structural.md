# R9: Why assay's self-qualification suite is slow (structure before symptoms)

**Scope.** This was read-only research. The clone is `.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy`, branch `assay-b105-evidence-integrity`, HEAD `95622e3a`. Paths are relative to `assay/`. Nothing in the clone or in `/workspaces/vbpub` was edited; `git status` is still clean, and no bytecode was written into the clone.

**Evidence labels:** [M] measured, [C] calculated, [A] assumption, [I] inference. R8 per-test call times are [M] from the retained 30eec294 baseline.

**My own measurements** were taken in the devcontainer (Python 3.14.7, pytest 9.1.1), everything under `nice -n 19 ionice -c3`, with host load at 12.9–14.4 of 8 cores. The load inflates wall time, so CPU time is the steadier number. No gate, no docker, no test run. What I ran:
- process start-up timings: `python -c pass`, a toy pytest project, `import assay.cli`;
- one `git_repo`-equivalent setup;
- 7 `pytest --collect-only` runs under a scratch `PYTHONPYCACHEPREFIX`, to separate warm and cold bytecode.

---

## 0. Direct answers

### Q1. "113 slow tests take 72% of the time. Why, and can we change it?"

The slow tests are slow for five different reasons. Only a minority of the cost is the price of what the tests actually check. The top 25 tests account for 221.8 s [M]:

| Share of the top-25 time | Seconds | What it is |
|---:|---:|---|
| 54% | 118.9 [C] | Tests whose result **cannot depend on the mutated `src/assay`**. |
| 36% | 79.1 [C] | Tests that sit on a real clock, because a production constant cannot be shortened where the test runs. |
| 8% | 17.8 [C] | Genuine nested real-pytest runs. This cost is intrinsic, but the same behaviour is re-proven at several layers. |

The mutant-independent group is:
- the Topos/PATH-wheel tests;
- the committed 1.2.5 wheel tests;
- gate-script wheel builds;
- pyflakes;
- the `pyproject.toml` packaging negatives;
- the pip hash re-check.

**Two costs are outside call time altogether, and the plan does not see them:**
- **Every candidate's fresh snapshot re-runs pytest's assertion rewriting of ~245 unchanged test modules.** Measured: 5.5 s of CPU per collection [M]. That is most of the ~7.4 s "collection" constant C, and every candidate pays it, including cold kills.
- **Fixture setup.** This includes a 6-subprocess `git_repo` for ≥616 tests, a per-candidate wheel build, and two per-candidate full-history release builds. It is the likely bulk of the ~71 s of non-call time.

**Fixes, grouped by kind:**
1. **Product changes (i).** Short liveness windows (D5, already planned). A safe way to reuse the rewritten test bytecode across candidates.
2. **Test re-design (ii).** A template git repository. Decouple the cheap first user from the wheel fixture. One real-subprocess test per mechanism, with fakes for the other layers. Cache the static source sweeps.
3. **Lane moves (iii).** Take every mutant-independent test out of the B105 lanes, and keep them in the release lane. This gives up nothing for R1/R2, *provided* B105 also binds a same-commit release-gate pass.

### Q2. "87 s per run goes to installed-wheel tests. Is that needed? Isn't the wheel already OK?"

**Not per mutant.**
- The 73.6 s of PATH-wheel tests and the 13.8 s of 1.2.5-wheel tests all live in `tests/test_python_qualification.py`.
- That file tests the **gate harness** `gate/python/qualify_topos.py`, running an *unchanging* assay against a real Topos tree. The installed assay is the run-venv wheel built once from the qualified commit (`tools/self-qualification-gate.sh:92-126,153-154`).
- Every candidate therefore gets the same answer. The file can never kill a mutant or add coverage.
- The only way it can change a candidate's classification is a false "kill" from a failure caused by the host. The native judge maps any non-zero exit to `killed` (`src/assay/mutation.py:1683-1686`).

**Yes, the wheel is already established elsewhere.** The release lane (`assay.toml:44`) runs the *whole* suite, this file included, against a wheel of the same commit. The release gate also runs the real `qualify_topos.py` qualification (`tools/tester-unified-gate.sh:671,679`).

**The `standalone` fixture** builds a wheel from the snapshot for every candidate: its per-candidate call time is 37.7 s (test_standalone plus the zipapp tests; R8), plus about 5–6 s of setup.
- Mutation operators only splice comparison operators and boolean or falsy literals into `.py` bytes, and a wheel ships those bytes verbatim.
- So a wheel-path test can be the *only* killer in just two cases:
  - code that branches on the install layout. This is provenance's installed-wheel and zipapp detection, resource loading from a zip, and plugin-origin fingerprints: about 36–136 candidates [C];
  - a missing in-process assertion, which is a test gap to fix.
- Building the wheel "once per lane" would make these tests mutant-independent too, which amounts to moving them out of R2. Details in §3.

---

## 1. The top 25 tests: what each checks, and why it is slow

Categories (R8): a = real-clock wait, b = wheel/venv/pip, c = nested run, d = PATH wheel, e = git.

"Kill?" means whether the test's outcome can depend on the snapshot's `src/assay`.

| # | Test (file:line) | s [M] | Cat | Oracle (what it verifies) | Slow because | Intrinsic? | Kill? |
|---:|---|---:|---|---|---|---|---|
| 1 | `test_cli_run.py:586` busy loop | 35.90 | a | A real CPU-spinning mutant is `budget_exceeded`/`LANE_TIMEOUT`, not `hung`, end to end through `main()` in-process (`:127-130`) | The budget must exceed the fixed 30 s CPU window (`liveness.py:523`) | **No.** The decision logic is already covered with a fake clock (`test_liveness_runner_monitor.py:167-303`); only the wiring needs a real window | yes |
| 2 | `test_cli_run.py:463` thread-join hang | 30.83 | a | A real zero-CPU hang becomes `hung`/`CANDIDATE_HUNG` | 30 s window + 15 s idle floor (`liveness.py:523,641`) | **No.** A real child with a fake clock is already tested (`test_liveness_runner_monitor.py:662`) | yes |
| 3 | `test_python_qualification.py:601` | 22.01 | d | The harness's 6 integrity negatives produce the frozen terminals | 6 × (git export of 966 Topos entries + commit + a PATH `assay run` with its own snapshot, pytest and coverage) | Intrinsic to the *harness* oracle | **no** |
| 4 | `PQ:563` | 13.91 | d | The PATH assay reports `UNCOVERED_LINES` on the missing-line Topos scenario, and the Topos comparator agrees | same | same | **no** |
| 5 | `PQ:783` | 10.49 | b | The committed 1.2.5 wheel installs hash-bound and passes the release smoke | venv + `pip --require-hashes` + a Topos run | same | **no** |
| 6 | `test_distribution_gate.py:370` | 8.52 | b | The gate's bash closure build gives a real setuptools-scm dev version | Clone + closure venvs + `pip wheel` of a synthetic repo | Intrinsic to the gate-script oracle | **no.** `pip wheel` never executes src |
| 7–11 | `PQ:621`, `:636`, `:752`, `:583`, `:714` | 37.69 total | d | Harness guards: forged universal PASS, missing witness, decoy root, exclusion asymmetry, "measured nothing" | Topos materialization + a PATH `assay run` | harness | **no** |
| 12 | `test_progress_phase_stream.py:456` | 7.05 | a | `--progress-heartbeat` reaches a running lane | `sleep 7` against the CLI's 5 s floor (`runner.py:936`, checked at `cli.py:1083`) | **No.** The floor is read at call time, so the test can shorten it | yes |
| 13 | `test_standalone.py:1978` | 6.01 | b | Removing `package-data` from `pyproject.toml` gives a wheel whose schema cannot load | Own venv, wheel and install | Intrinsic to a **`pyproject.toml`** oracle | ~no [I]. Only `load_schema()` runs, and it is covered in-process |
| 14–15 | `test_standalone.py:2011`, `:2043` | 10.77 total | b | Dropping the console script leaves no binary; declaring a dependency breaks the offline install | Own venv, wheel and install each | same | **no** |
| 16 | `test_standalone.py:1064` | 5.27 | a | The R2 `budget_exceeded` bucket, through the wheel | Lane budget `"5s"` against `sleep 300` | Duplicates the in-process budget tests | yes (weakly) |
| 17 | `test_distribution_gate.py:395` | 5.26 | b | An ambient-only build is refused as a placeholder | venv + pip + `pip wheel` | gate-script | **no** |
| 18, 19, 21, 24 | `test_b106_reuse_and_witness.py:680`, `:820`, `:926`, `:1001` | 14.79 total | c | Witness-prefix replay, hook fallback, resume provenance, rejudge; each does 2–3 `run_lane` passes with real pytest | About 0.3–0.5 s per nested pytest child [M, toy project], plus git snapshots | Mostly intrinsic. About 40% of child start-up is plugin autoload [M] | yes |
| 20 | `test_distribution_release_wheel.py:239` | 3.48 | b | `pip --require-hashes` re-checks bytes mutated after verification | venv + pip | release tooling | **no** |
| 22 | `test_distribution_gate.py:920` | 3.42 | b | Locked pyflakes is clean over `src/assay` + `tests` | `lint_venv` + pyflakes | intrinsic | **no** [I]: no operator adds or removes a name (`python.py:454-463`, `:620-650`) |
| 23 | `PQ:771` | 3.33 | b | The 1.2.5 wheel installs into a pure hash-bound venv | venv + pip | release | **no** |
| 25 | `test_standalone.py:1657` | 3.04 | c | The R3 uncovered-line canary through the installed wheel | Wheel subprocess + 2+ pytest halves | Duplicates `test_runner_run_lane_r3.py:193` | yes (layout-insensitive) |

### Beyond the top 25

- **Real-clock (a).** `test_lane_timeout_writes_a_verdict.py` (5 × ~1.07 s, `BUDGET="1s"`), `test_environment_preflight.py:128/246`, `test_runner_result_report.py:450`. The duration grammar already accepts fractions (`config.py:348-350`), but the 1 s margins guard against load. Low value to change.
- **Nested runs (c), 69.9 s in total.** The same real pytest interaction is re-proven at four layers:
  - canary orchestration (`test_canary_python_pipeline.py`, 10.3 s);
  - `run_lane` (`test_runner_run_lane_r3.py`, 14.2 s);
  - the CLI (`test_cli_run.py:677,1720`);
  - the wheel (`test_standalone.py` R0–R3, about 18 s).
  - R3 real runs alone total about 43 s [C].
- **Git (e), 24.9 s.** Real P22 snapshots of toy repositories, the same machinery as the product's snapshot. P5's C1/C2 speed these up as a side effect [I].
- **Other (f).** `test_untrusted_json_parse_sweep.py` calls `_collect_sites()` (`:101`), which re-parses all of `src` in every one of its 14 tests: 5.96 s for the file [M]. Caching it is trivial.

---

## 2. Shared root causes

"Per run" means one full declared-suite run.
- **Today** that is every candidate: 3,760 runs, so 1 s per run ≈ 1.04 worker-hours per campaign.
- **After P3 cold kills plus tiering**, tail costs are paid only by survivors in screens, by ledger-audit entries, by the 4–5 baseline runs, and by kills whose first failing test is in the tail.
- Costs in **collection** and in the **fast tier** are still paid by every kill.

| # | Root cause | Tests affected | s/run | Structural fix | Kind | Saving per candidate | Claim effect | Effort |
|---|---|---|---:|---|---|---|---|---|
| RC1 | Mutant-independent tests run in the R2 suite: the PQ file | `test_python_qualification.py` (32 tests) | 93.7 [M] | `--ignore` (D4a, planned) **and** bind a same-commit release-gate pass into B105 (§4.2) | iii | 93.7 s per run | R1/R2: none (no coverage, no kill). B105's R0 gets narrower, and only the release-gate binding restores it | S |
| RC2 | The same, beyond PQ: gate-script, `pyproject.toml`, pyflakes and pip oracles | `dist_gate:370,395,920` + `lint_venv`; `release_wheel:239`; `standalone:1978,2011,2043`; cmru harness ×3; the second `built` build; static sweeps | ≈33 call [M] + ≈13–20 setup [A] | Declare a "distribution" test set (a directory or explicit `--ignore`s) that the B105 lanes skip and the release lane keeps | iii | ≈45–55 s per run | Same as RC1. Also closes a false-kill channel: 82 literal subprocess `timeout=` in 19 test files [M] can fail a test under load, and that becomes `killed` | S–M |
| RC3 | Production real-time constants reachable only by waiting at the end-to-end layer | `cli_run:463,586`; `progress:456` | 66.7 + 7.05 [M] | D5 lane keys (window 6 s / floor 5 s, P3c:85-86,220-265) → ≈22–24 s left for the two tests. Heartbeat: shrink `runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS` in the test, or spy on the interval passed to `_command_heartbeat` → <1 s | i (D5) / ii | ≈43–45 s (D5, not the ≈60 s in analysis §6) + ≈6.5 s | None. The decision logic is already tested with a fake clock (`liveness.py:1299-1330` has monotonic/sleep/cpu_reader/popen seams) | M (planned) / S |
| RC4 | **Every candidate recompiles and assertion-rewrites the unchanged test modules** in a fresh snapshot (`__pycache__` is gitignored, so it is never carried) | All ~245 test-side modules plus conftest | ≈5.5 CPU-s [M]: repo-cold collection 8.3 CPU-s vs 2.8 with `--assert=plain` vs 1.9 warm; src compile only ≈0.5 | (a) Harvest the baseline's rewritten `tests/**/__pycache__/*-pytest-*.pyc` and place them in each candidate; tests are never mutation targets, so the code is byte-identical. Never carry `src` bytecode: the stale-pyc hazard (analysis §7.2). (b) Or derive `--assert=plain` in P3's R2 command | i/ii | **≈4–5 s on every candidate, kills included** [I] → ≈5 worker-hours, ≈1.6 h wall on 3 workers | (a) None, but it breaks the "nothing carried into a snapshot" invariant for ignored files and needs a decision plus a guard in the style of P0 G3. (b) Changes the executed test bytecode and needs a D6-style equivalence ruling | M |
| RC5 | Every test builds its own git repo with 6 subprocesses (`conftest.py:531-545`) | ≥616 test instances use the `git_repo` closure [C, AST] | ≈18–49 setup [C from 0.079 s M × 616; A for the gate] | A session template repo plus `copytree` per test | ii | ≈15–40 s per run, largely in the fast tier, so it also shortens cold-kill prefixes | none | S–M |
| RC6 | Expensive session/module fixtures are triggered early by a cheap user, or build more than needed | `standalone` (`conftest.py:1432-1497`, first user `test_analysis.py:721`, a FIFO test that needs only a killable child); `built` = **two** full-history `clone --no-local` release builds of the snapshot repo (`test_distribution_build_release.py:363-389`, `build_release.py:159-176`); `lint_venv` | ≈5–6 (standalone, proxy: `:1978`-style builds 5.3–6.0 s M) + ≈15–30 (`built`) + ≈3–5 [A] | Run the FIFO test via `sys.executable -m assay` with `src` on PYTHONPATH (or a thread with a timeout). Move *all* `standalone` users together. Drop the second build (byte-reproducibility is mutant-independent). Build the zipapp from the standalone wheel instead of clone + closure | ii/iii | −5–6 s from the fast tier (every kill after `test_a…`); −12–15 s [A] per run for the second build | Second build: none. Standalone/zipapp: see RC7 | S–M |
| RC7 | The same product behaviour is re-proven through installed artifacts and across 3–4 layers with real nested pytest | `test_standalone.py` R0–R3 (≈18 s), zipapp execution tests (≈3 s), R2/R3 real runs in the canary, `run_lane` and CLI layers | ≈65–80 call [M/C] | Keep one real-subprocess test per mechanism, and 1–2 real-artifact provenance tests. Cover wiring at the other layers through `run_lane(process_runner=…)` fakes. In toy lanes set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` with explicit `-p` (toy pytest 0.50 → 0.29 s [M]) | ii/iii | ≈20–40 s [A] | Kill redundancy drops. Kill *capability* is lost only where a removed test was the sole killer. **Measure first** (§4.7) | M–L |
| RC8 | Static sweeps re-parse all of `src` in every test | `untrusted_json_parse_sweep` (14 tests), `dependency_purity`, the wiring sweep | ≈5 [M] | Session-cached parse | ii | ≈4–5 s | none | S |

**Scenario, not a forecast** [C/A]:
- The analysis puts a survivor's run after P1 and P3c at ≈365–385 s.
- The extra levers above remove roughly another 110–170 s from it, which gives ≈200–275 s per survivor, ledger audit entry or baseline.
- On the cold-kill path, RC4 alone lowers the post-P5 per-kill floor from 9.4–11.4 s to about 5–7 s. That puts the Pilot GO bound (≤ ~9.7 s per kill, analysis §1.7) within reach with 2–5 s left for the mean prefix P.

---

## 3. Q2 in detail: the installed-wheel tests

### 3.1 `tests/test_python_qualification.py`: what it does and why it exists

**What it tests.**
- Its subject is `gate/python/qualify_topos.py`, the P25 harness. The file's own docstring calls it "ordinary regression coverage for the P25 … harness and its registered-gate wiring".
- It never imports assay (`:105-106`).
- `installed_assay` is `shutil.which("assay")` (`:546-559`), which is real only inside tester-unified.

**Its groups:**
- **Static checks:** harness signatures, byte-exact fixture promotions, gate-script wiring.
- **Materialization:** a real `git archive` of the pinned 966-entry Topos tree out of `REPO_ROOT` history; the baseline OID reproduces the carver's witness.
- **Seven PATH-wheel pipeline tests:** the current wheel's R1 verdict on Topos scenarios (missing line → `UNCOVERED_LINES`, excluded lines → `EXCLUDED_LINES`) agrees with Topos's own evaluator, and the harness guards (integrity matrix, forged PASS, missing witness, vacuous 0/0, decoy root) fire.
- **Two tests of the committed `assay-1.2.5` wheel:** the release-manifest consumer path, i.e. P24 helper verification plus `pip --require-hashes`, then a smoke run.

**Why it is in the suite (history, `git log --follow`).**
- `2607cb7d` (2026-08-10, P25) promoted a slice of the carver-locked `carve-assets/P25/test_acceptance.py`, which is never collected, into `tests/`. The reason given: "so the registered gate's own `pytest tests -q` step exercises this code too".
- At that time the only lane was the R0 release lane, and it imports the wheel by design (A-130, `decisions.md:393`). So "the installed wheel" *was* the artifact under test.
- A-205 (`decisions.md:543`) gives the two wheel owners their roles:
  - the current wheel, "so later Assay changes remain externally qualified";
  - the 1.2.5 wheel, for the release-manifest path.
- P33 (`f13e78a2`) then locked the path: `carve-assets/P33/test_acceptance_v5.py:406-417` requires the v4→v5 consumer sweep to find this file.
- B105 (A-462, 2026-09-25) reused the same `tests/` tree for a per-mutant lane without re-scoping it. The file's presence in R2 is an accident of that inheritance, not a design choice.

**Is it needed per mutant? No** [I, strong]:
- Its inputs are the same for every candidate: a PATH wheel built once, the committed 1.2.5 bytes, and the pinned Topos history.
- Its outputs are therefore the same for every candidate.
- It adds zero coverage, because coverage is not configured for subprocesses (analysis §3).

**Where its assurance comes from.** The release lane runs it against the same-commit wheel. The release gate runs the full qualification phase (`tester-unified-gate.sh:671-679`) plus the P26/P33 locked suites.

**So "the wheel is OK" is established,** by the release gate, on that commit. §4.2 covers the missing binding.

### 3.2 The `standalone` fixture (per candidate, can kill, not coverage-measured)

**What it builds** (`conftest.py:1432-1497`):
- It copies `pyproject.toml` plus the snapshot's `src`, creates a venv, runs `pip wheel --no-build-isolation`, then `pip install --no-index`.
- It is session-scoped, so its cost lands in the *setup* of its first user, `test_analysis.py:721`, and never appears in call time.

**What the packaging-specific tests cover:**
- package data declared (the schema and the Go helper are in the wheel);
- the console script is declared and runs;
- zero runtime dependencies and an offline install;
- no leakage of the source tree;
- the real PEP 610 digest from pip, and zipimport reporting its archive.

All of these depend on **`pyproject.toml`, setuptools, pip and the build pipeline**, none of which is a mutation target.

**Can a wheel-path test kill a mutant that an in-process test could not?** Only in two cases.

1. **Layout-dependent branches** [I]:
   - `provenance.identify_judge`'s installed and zipapp paths (`provenance.py:241-320`; injectable `module`/`dist` stand-ins already exist, per its docstring near `:200`);
   - zip-backed resources in `go_stmtpos.py:137-160`;
   - plugin-origin fingerprinting in `mutation_witness.py:358-380`;
   - `__version__` metadata.

   Candidate counts from the 30eec294 plan [M]: `provenance.py` 24, `go_stmtpos.py` 12, `mutation_witness.py` 100 (only a subset is layout-dependent). That bounds the exposure to about 36–136 candidates.

2. **A missing in-process assertion.** That is a gap to fill, not a reason to keep a per-candidate wheel build.

Process-boundary effects do not add a third case: pip generates the console-script shim, and `main()`'s return value is already tested in-process.

**"Build once per lane"** means building from the unmutated baseline. That turns every standalone test into a constant, just like the PATH wheel. It is therefore equivalent to removing them from R2.

**Recommended split:**
- Packaging-property tests (`test_dependency_purity` wheel tests, `test_go_helper_is_packaged`, `test_verdict_schema_is_packaged`, the three `test_standalone:1978-2073` negatives): release lane only.
- Wheel-path R0–R3 runs: release lane. Their logic is covered in-process.
- Keep in the R2 tail only real-artifact tests that target layout branches (`test_the_installed_wheels_own_sha256…`, the zipapp `identify_judge` test), **or** replace them with stand-in tests plus a release-lane artifact test.

**Claim consequence:**
- R1: none (these tests are not coverage-measured).
- R2: any mutant for which these were the only killers becomes a screen survivor, and a new in-process test then fixes it. That improves the suite; it does not weaken the claim.
- B105 R0: narrower, and bound through §4.2.

**Saving per run:** ≈5–6 s setup + ≈21 s wheel-path calls + ≈17 s packaging negatives + ≈3–5 s of other users [C/A]. Moving `test_standalone.py` alone (as P1 does) does **not** remove the build from the fast tier, because `test_analysis.py:721` and three packaging files still trigger it. R5 already flagged this; P1's move list does not act on it.

**The zipapp tests (`built`).**
- They build twice from the snapshot's **HEAD**, which contains the mutant, because the mutant is committed via `commit-tree`. So they can kill.
- Each build does a full-history `git clone --no-local` of the snapshot repo, about 55k objects and 62 MiB of packs [A: this size is the snapshot's own pack size from the analysis §7.2 inventory].
- That roughly doubles the disk writes per run compared with the product's own ~130 MB snapshot [C/A].
- The second build exists only for byte-reproducibility, which is mutant-independent.

---

## 4. Q3: structural points the plan misses or treats as symptoms

1. **Collection is not a constant; it is mostly avoidable repeated work (RC4).**
   - The plan and analysis treat C ≈ 7.4 s + start-up as fixed. Cold collection is dominated by pytest's assertion rewriting of unchanged test modules [M].
   - No plan document mentions bytecode or rewrite caching; a grep found only the P0 G3 stale-bytecode guard. The plan's own G3 still applies to `src`.
   - This is the largest lever for the qualifying (cold-kill) path.
2. **The assurance transfer for ignored or moved tests is unbound.**
   - D4(a) says "the release lane keeps running it".
   - But B105's definition of done (plan §1 `:40-50`) and the qualifying runbook (§9.3 `:442-458`) never require a `tester-unified` release-gate pass **at X\***.
   - Add that to Qualifying GO, and every lane-move (iii) above becomes claim-neutral.
3. **Tiering postpones mutant-independent tests instead of removing them.**
   - After D4(a), ≈33 s of calls plus ≈13–20 s of setup (RC2) still sit in `zz_slow`.
   - They run for every survivor, audit entry, baseline and late kill.
   - They remain a false-kill channel: an inner `timeout=` or pip/git failure under load exits non-zero, which is `killed` (`mutation.py:1683-1686`). That is outside A-464's reach, because A-464 governs assay's own clocks, not the suite's.
4. **Tiering was decided on call time only.**
   - Setup costs invisible to it: `git_repo` ×616+, `standalone` via `test_analysis.py:721`, the `built` double clone.
   - P0 W3 will forward `setup_s`/`teardown_s` (`P0-measurement-hygiene.md:254-272`). The P1 move list should be re-derived from that data, with the `standalone` users moved together.
5. **Order inside the tier is alphabetical.** A kill whose first failing test is in `zz_slow` pays for every earlier tail test. Order the tier cheapest-first, or by kill yield once known, and add a drift test that fails when a new ≥0.5 s test (call **or** setup) lands in the fast tier.
6. **D5 is a product-surface change for a test-speed problem.**
   - It adds lane grammar, v14 disclosure and docs, while the product already has the seams (`liveness.py:1299-1330`).
   - Its saving is capped by the 5 s guardrail at ≈43–45 s (P3c's budgets of 25 s / 14 s), not the ≈60 s in analysis §6.
   - The operator has ruled; this is recorded only so the size of the saving is stated correctly.
7. **There is no kill-matrix evidence, so redundancy cannot be removed safely.**
   - State records keep only the bucket.
   - The candidate's liveness events file already records every test's outcome (`liveness.py:214-222`), but it is deleted after the run (`test_cli_run.py` asserts `.assay/liveness` is gone).
   - Retaining the failing node IDs per candidate, or running a no-stop stratified sample, would show which heavy or wheel tests are sole killers. RC7 and the §3.2 split should be gated on that evidence.
8. **The screen loop benefits most.** Under P3, heavy tests hurt survivors and audits, not the qualifying kills. So RC2, RC6 and RC7 are chiefly screen-cost fixes, while RC4 and RC5 help both screen and qualifying runs.

---

## 5. What remains unverified

- **RC4 in the gate.** The rewrite cost was measured in the devcontainer without coverage at load ≈13. How much of the gate's 7.4 s leading gap it accounts for (estimated 4–5 s [I]) needs one cold-vs-warm probe inside tester-unified, and the carry-in mechanism needs a design decision.
- **Setup costs** of `git_repo` (0.079 s measured here; the gate figure is unknown), `built` (2 builds, [A] 15–30 s), `lint_venv` and `standalone` (5–6 s from a proxy) all await P0 W3's `setup_s`. The upper ends of my ranges over-fill the 71 s residual, so the lower ends are likelier.
- **The sole-killer set** for wheel-path and nested-layer tests is unmeasured (§4.7). The 36–136 layout-dependent candidate bound is by file, not by site.
- **Plugin autoload savings** in the gate image. The plugin set differs from the devcontainer's (hypothesis, xdist, cov).
- **Temp-space pressure.** Whether per-candidate pytest `basetemp` retention (3 sessions × venvs and clones) lands on a tmpfs that counts against the 2 GiB cgroup. Not checked.
- **"Mutant-independent" for pyflakes and the static sweeps** rests on the four operators' token sets [I, strong]. It was not executed.
