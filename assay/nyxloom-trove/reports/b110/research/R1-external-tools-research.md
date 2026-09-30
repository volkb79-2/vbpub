I've covered all 5 tool groups, the research techniques, and the four required quotes (a) to (d). Most claims come from current official docs or from source at a pinned release tag, and every claim has a URL. Where a paper PDF wouldn't render, I pulled its text out locally with a small zlib script. Mark legend: **[D]** means documented or read in source/paper, **[I]** means my inference, **[UNVERIFIED]** means I could not confirm it from a primary source.

# How established mutation tools avoid per-mutant full-suite work: research for Assay self-qualification

## Headline: the mechanisms that cut per-mutant work the most

For scale **[I]**: 3,760 candidates × ~560 s ≈ 2.1 M s, about 585 h or **about 24 days** on one worker. None of the established tools re-runs the whole suite in a fresh, coverage-instrumented interpreter for each mutant. In rough order of impact:

1. **Per-test coverage map built once, then only covering tests run per mutant.** PIT, Stryker (`perTest`, the default), mutmut 3 (function-level), MutPy (`--coverage`) and Google (line level to test targets) all do this. Mutants that no test covers get a verdict with no execution at all (PIT, Stryker `NoCoverage`, mutmut exit code 33).
2. **Bail at the first failing test, and order the covering tests so a killer runs early.** PIT runs tests in increasing-runtime order. Stryker bails by default (`disableBail=false`). mutmut runs pytest with `-x`. FaMT (the research form of this idea) states the cost floor: *"For each mutant that is not killed, we must run every test (that reaches the mutated statement)."*
3. **No fresh process per mutant.** PIT rewrites the loaded class inside a long-lived "minion" JVM. Stryker loads the code once and switches the active mutant ("hot reload"). mutmut forks a pre-warmed process that has already imported pytest and the code. Interpreter startup, imports and test collection are paid once, not 3,760 times.
4. **Mutant schemata / mutation switching.** All mutants are compiled or instrumented in once and one is activated at run time, via an environment variable or a global (Stryker, Stryker.NET, Stryker4s, mutmut's trampoline, Major, mull).
5. **Coverage is collected in a separate first phase, not during mutant runs** (PIT, Stryker dry run, mutmut stats run).
6. **Import-time ("static") code is handled on a separate, explicit path.** PIT doesn't mutate static initializers. mutmut 3 only mutates inside functions. Stryker runs all tests in a fresh worker for static mutants, sorts them last, or can ignore them.
7. **Incremental reuse, sampling, or diff-only runs.** These are the next multipliers. Every tool documents them as heuristic or unsafe, and they don't produce a complete inventory.

---

## 1. PIT / Pitest (Java)

- **Version:** 1.30.0, the latest release, Maven metadata `lastUpdated` 2026-08-27 ([maven-metadata](https://repo1.maven.org/maven2/org/pitest/pitest/maven-metadata.xml)). Source below was read at `master` on 2026-09-28, not pinned to the tag.
- **Test selection [D]:**
  - *"Before running the tests PIT performs a traditional line coverage analysis for the tests, then uses this data along with the timings of the tests to pick a set of test cases targeted at the mutated code"* ([basic concepts](https://pitest.org/quickstart/basic_concepts/)).
  - The coverage map is per test. In source it is keyed by **block** (`coverage.getTestsForBlockLocation(loc)`) ([DefaultTestPrioritiser.java](https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/DefaultTestPrioritiser.java)).
  - *"there is little or no execution time cost for mutations on lines that have no test coverage"* ([FAQ](https://pitest.org/faq/)).
- **Test ordering [D]:**
  - *"The remaining tests are then ordered by increasing execution time - test cases that belong to a class that is identified as a unit test for the mutated class are however weighted above other tests"*. A "unit test" means FooTest/TestFoo naming ([FAQ](https://pitest.org/faq/)).
  - In source: `TIME_WEIGHTING_FOR_DIRECT_UNIT_TESTS = 1000` ([DefaultTestPrioritiser.java](https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/DefaultTestPrioritiser.java)).
- **Kill semantics [D]:**
  - Source: `if (this.fullMutationMatrix) { pit.run(c, tests); } else { pit.run(c, createEarlyExitTestGroup(tests)); }`, so the default stops at the first failure ([MutationTestWorker.java](https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestWorker.java)).
  - The worker records failing, succeeding and covering tests in `MutationStatusTestPair` (same file).
- **Mutant insertion [D]:**
  - The mutated class bytes are hot-swapped: `this.hotswap.insertClass(...)`; if that fails the mutant is marked `NON_VIABLE` ([MutationTestWorker.java](https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestWorker.java)).
  - *"Pitest inserts mutants into a jvm by re-writing the class after it has loaded. This is orders of magnitude faster than starting a new jvm or creating a new classloader"* ([FAQ](https://pitest.org/faq/)).
- **Process reuse:**
  - [D] Mutations are grouped per class, then split by `mutationUnitSize` (0 = unlimited, meaning one unit per class) ([DefaultGrouper.java](https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/DefaultGrouper.java); [maven docs](https://pitest.org/quickstart/maven/)).
  - [D] One minion receives the whole mutation list for its unit ([MutationTestMinion.java](https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/MutationTestMinion.java)).
  - [D] The parent loops `while (mutations.hasUnrunMutations())` and starts a new minion for whatever is left after a minion dies ([MutationTestUnit.java](https://github.com/hcoles/pitest/blob/master/pitest-entry/src/main/java/org/pitest/mutationtest/build/MutationTestUnit.java)).
  - [D] On timeout the minion's side effect is `r.done(ExitCode.TIMEOUT)` ([TimeOutSystemExitSideEffect.java](https://github.com/hcoles/pitest/blob/master/pitest/src/main/java/org/pitest/mutationtest/execute/TimeOutSystemExitSideEffect.java)).
  - **[I]** So the default is one JVM per class, reused across all of that class's mutants and replaced only after a crash or timeout.
- **Timeouts [D]:** `timeoutFactor` defaults to 1.25 and `timeoutConstant` to 4000 ms. Both apply to *"the normal runtime of a test"*, i.e. per test ([maven](https://pitest.org/quickstart/maven/)).
- **Parallelism [D]:** *"By default a single thread will be used"* (`threads`) ([maven](https://pitest.org/quickstart/maven/)).
- **Incremental [D]:** ([incremental analysis](https://pitest.org/quickstart/incremental_analysis/))
  - It is labelled *"experimental"*. The reuse rules:
    - killed is kept *"if … neither the class under test or the killing test has changed"*
    - survived is kept *"if … no new tests cover it, and none of the covering tests have changed"*
    - a timeout is kept if the class is unchanged
    - the previous killer is *"prioritised"*
  - Dependencies considered: only *"changes to super classes and outer classes"*.
  - Safety: *"With the exception of 4), all these optimisations introduce a degree of potential error into the analysis."* The underlying assumption is *"currently unproven"*.
  - `withHistory` *"Sets the history input and output files to point a project specific file within the temp directory"* ([maven](https://pitest.org/quickstart/maven/)).
  - **[UNVERIFIED]** The exact hash PIT uses to detect a change (bytecode or source) isn't stated in the docs I read.
- **Result evidence [D]:** `fullMutationMatrix` *"causes pitest to continue processing after a test fails and record addition failing tests when XML output is enabled"* ([maven](https://pitest.org/quickstart/maven/)). `testStrengthThreshold` *"exclud[es] mutants where no coverage information is available"* (same page).
- **Arcmutate / Descartes:**
  - [D] Arcmutate's change-based mode: *"only code modified between the specified git refs will be analysed"*. The range only *selects* classes: *"analysis is always performed with the current code"* ([Arcmutate git integration](https://docs.arcmutate.com/docs/git-integration)).
  - Descartes implements "extreme mutation operators" from Niedermayr et al. ([Descartes](https://stamp-project.github.io/pitest-descartes/)). **[UNVERIFIED]** I couldn't retrieve its mutant-count or speed comparison page (404).
- **Transfer [I]:** PIT's pipeline is the most direct template: a per-test coverage phase, then covering tests only, fastest and most relevant first, stop at first kill, many mutants per warm process, per-test timeouts. Its static-initializer caveat maps one-to-one onto Python module-level code (see (a)).

## 2. Stryker (StrykerJS 10.0.0, Stryker.NET 4.16.0, Stryker4s)

Versions are the latest on [npm](https://registry.npmjs.org/@stryker-mutator/core/latest) and [NuGet](https://api.nuget.org/v3-flatcontainer/dotnet-stryker/index.json) as of 2026-09-28. I didn't find release dates for them.

- **Schemata [D]:**
  - StrykerJS 4.0 (2020-10-07) puts all mutants in the code at once and selects the active one with `global.activeMutant`, reporting *"20% to 70%"* speed increases ([blog](https://stryker-mutator.io/blog/announcing-stryker-4-mutation-switching/)).
  - Stryker.NET uses `Environment.GetEnvironmentVariable("ActiveMutation")`. Mutants that fail to compile are removed and the code recompiled (`builderror`). It *"skip[s] constant values from mutating"* ([.NET schemata](https://stryker-mutator.io/docs/stryker-net/technical-reference/mutant-schemata/)).
  - Stryker4s (2018-10-06) uses `sys.env.get("ACTIVE_MUTATION")`: *"the code base only needs to be compiled once"* ([blog](https://stryker-mutator.io/blog/mutation-switching/)).
- **Coverage analysis [D]:** ([JS config](https://stryker-mutator.io/docs/stryker-js/configuration/))
  - `off`: *"All tests are executed for each mutant."*
  - `all`: uncovered mutants are `NoCoverage`.
  - `perTest` (the default): *"Only the tests that cover a specific mutant are executed for each mutant."* It assumes tests are independent. Static mutants trigger all tests.
  - The 4.0 blog attributes *"40-60%"* gains to `perTest`.
  - .NET adds `perTestInIsolation`: *"running each test in an isolated run … more accurate coverage … at the expense of a longer startup time"* ([.NET config](https://stryker-mutator.io/docs/stryker-net/configuration/)).
- **Process reuse / hot reload [D]:**
  - v6 (2022-05-04): load the code once *"using `import`"*, then activate each mutant and rerun, with no reloading ([v6 blog](https://stryker-mutator.io/blog/stryker-js-v6-expeditious-superior-mutations/)).
  - `maxTestRunnerReuse` defaults to 0 (never restart). The docs say it is *"Not recommended unless you are experiencing memory leaks"* ([JS config](https://stryker-mutator.io/docs/stryker-js/configuration/)).
- **Kill semantics [D]:**
  - `disableBail` defaults to false, i.e. Stryker bails *"after the first failing test"* ([JS config](https://stryker-mutator.io/docs/stryker-js/configuration/)).
  - .NET: *"aborts … as soon as one test fails because this is enough to confirm the mutant is killed"* ([.NET config](https://stryker-mutator.io/docs/stryker-net/configuration/)).
- **Ordering:** StrykerJS sorts *mutants* so static ones run last (v6 blog). **[UNVERIFIED]** I found no documented ordering of tests within one mutant.
- **Timeouts [D]:**
  - JS: `netTimeMs * timeoutFactor + timeoutMS + overheadMs`, defaults factor 1.5 and 5000 ms. `dryRunTimeoutMinutes` defaults to 5.
  - .NET: `(initialTestRunTime + coveringTestsTime) * timeout-ratio + additional-timeout`, defaults 1.5 and 3000 ms. Both from the config pages above.
- **Concurrency [D]:** JS defaults to about n−1 cores; .NET to logical processors / 2 (config pages).
- **Incremental [D]:** ([JS incremental](https://stryker-mutator.io/docs/stryker-js/incremental/))
  - Reuse rules: killed if *"the culprit test still exists, and it didn't change"*; not-killed if *"no new test covers it, and no tests changed"*.
  - Detecting test changes depends on whether the runner reports test locations.
  - Limits: *"will not detect any changes you've made in files other than mutated files and test files"* and *"updated (dev) dependencies, changes to environment variables"*.
  - .NET baseline: *"Results can contain false positives and false negatives."* .NET `since`: *"For changes on test project files all mutants covered by tests in that file will be seen as changed"* ([.NET config](https://stryker-mutator.io/docs/stryker-net/configuration/)).
- **Result evidence [D]:** ([report schema](https://github.com/stryker-mutator/mutation-testing-elements/blob/master/packages/report-schema/src/mutation-testing-report-schema.json))
  - `killedBy`: *"It is a best practice to "bail" on first failing test, in which case you can fill this array with that one test."*
  - `coveredBy`: *"The test ids that covered this mutant."*
  - `testsCompleted`: *"Can differ from "coveredBy" because of bailing."*
  - `static`: *"loaded once … during initialization, this makes it slow or even impossible to test."*
  - Status values include `NoCoverage`, `Timeout`, `Ignored`, `CompileError`.
- **Transfer [I]:** The report schema is an off-the-shelf, auditable evidence format (`killedBy` / `coveredBy` / `testsCompleted` / `static`). It shows that "complete inventory" and "bail at first kill" can coexist when each verdict records what it actually rests on.

## 3. mutmut (Python), 3.8.0

3.8.0 was uploaded 2026-09-12 ([PyPI](https://pypi.org/pypi/mutmut/json)). Source below was read at tag `3.8.0`.

- **Execution model [D]:**
  - *"Mutmut's main process imports pytest and runs your test suite to collect stats, then forks one child per mutant"* (the default `fork` mode).
  - `forkserver` mode: *"that child does the importing once and forks a worker per mutant"*.
  - Requires fork support, so no native Windows ([README 3.8.0](https://github.com/boxed/mutmut/blob/3.8.0/README.rst); [docs](https://mutmut.readthedocs.io/en/latest/)).
- **Schemata (the trampoline) [D]:**
  - The trampoline reads `MUTANT_UNDER_TEST`. If another module's mutant is active it calls the original function; otherwise it looks the mutant up in `mutants_dict`.
  - The special value `"stats"` records hits through `record_trampoline_hit(orig_qual_name, caller=…)` ([trampoline.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/mutation/trampoline.py)).
  - Only code inside functions is mutated: *"If you want to mutate code outside of functions, you can try using mutmut 2"* ([README](https://github.com/boxed/mutmut/blob/3.8.0/README.rst)).
- **Test selection [D, source]:**
  - `tests_for_mutant_names()` returns `state().tests_by_mangled_function_name[...]` for the mutant's function ([`__main__.py`](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/__main__.py)).
  - The stats plugin records test node ids and per-test durations ([harness.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/runners/harness.py); [stats.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/stats.py)).
  - `max_stack_depth`: count a test only if the function is reached within N stack frames; *"Only stack frames from code inside `source_paths` is counted"* ([README](https://github.com/boxed/mutmut/blob/3.8.0/README.rst)).
  - Optional `mutate_only_covered_lines` (coverage.py).
  - No covering tests: `exit_code_by_key[mutant_name] = 33` ("no tests"), never executed (`__main__.py`).
- **Ordering [D]:**
  - *Mutants* are sorted by `estimated_worst_case_time` = sum of their covering tests' baseline durations (`__main__.py`).
  - Tests are passed as `list(tests)` from a set. **[UNVERIFIED]** I found no per-test duration ordering within a mutant in 3.8.0.
- **Kill semantics [D]:** The mutant run uses `["-x", "-q", "-p", "no:randomly", "-p", "no:random-order"]` plus the selected test ids ([harness.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/runners/harness.py)), so it stops at the first failure. **[UNVERIFIED]** I couldn't locate `status_by_exit_code` or any per-test "killed by" attribution.
- **Timeouts [D]:**
  - Docs: `(duration_of_original_tests + timeout_constant) * timeout_multiplier`, defaults 1.0 and 15.0, marked unstable.
  - Source: `cpu_time_limit_s = ceil((estimated_time_of_tests + cfg.timeout_constant) * cfg.timeout_multiplier * 2)`, enforced as SIGXCPU and then SIGKILL (`__main__.py`). The timeout is based on the covering tests only, not the whole suite.
- **Incremental [D]:**
  - *"Between runs, mutmut only re-tests mutants in functions whose source changed"*, via function hashes (source: `if func not in hash_by_function_name or func in changed: merged[key] = None`).
  - Non-Python files are tracked through git or a fixed list of lockfiles. `on_dependency_change` defaults to *"warn … and keep the cache"*.
  - Config changes (`pytest_add_cli_args`, `type_check_command`) are *"always detected"* ([README](https://github.com/boxed/mutmut/blob/3.8.0/README.rst)).
  - **[UNVERIFIED]** The README doesn't say whether *changing a test* invalidates cached survivors. The source has `function_dependencies` / `track_dependencies`, which aren't documented.
- **Parallelism:** Runner capacity is gated (`runner.has_capacity()`) (`__main__.py`). **[UNVERIFIED]** I didn't confirm the exact default worker count.
- **Transfer [I]:** This is the closest existing design for Python/pytest. The stats pass gives a function-to-tests map, a fork per mutant avoids interpreter start, import and collection, `-x` gives bail, and the time limit follows the covering tests. Gaps for Assay's goals: no module-level mutants, and the default "keep cache" on dependency change is not audit-grade.

## 4. Cosmic Ray (Python), 8.7.0

8.7.0 was uploaded 2026-08-09 ([PyPI](https://pypi.org/pypi/cosmic-ray/json)).

- **Model [D]:**
  - *"A session is a database which records the work that needs to be done"* ([concepts](https://cosmic-ray.readthedocs.io/en/latest/concepts.html)).
  - `exec` runs only items *"that don't yet have results"*, so it can resume.
  - `baseline` checks that the suite passes unmutated ([intro tutorial](https://cosmic-ray.readthedocs.io/en/latest/tutorials/intro/index.html)).
- **Test selection [D]:** None. *"it will run this test suite for every mutation that it creates"* (intro tutorial).
- **Insertion and process model [D]:**
  - *"Cosmic Ray mutation works by actually modifying the code on disk"*, so each HTTP worker needs its own clone and handles one request at a time. Celery is only suggested as a custom plugin ([distributed](https://cosmic-ray.readthedocs.io/en/latest/tutorials/distributed/index.html)).
  - Source: the mutated file is written and restored ([mutating.py](https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/mutating.py)).
  - Each mutant runs the test command as a subprocess: `shlex.split`, `start_new_session=True`, `PYTHONDONTWRITEBYTECODE=1`. Exit 0 means SURVIVED, non-zero means KILLED ([testing.py](https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/testing.py)).
- **Timeouts [D, conflict]:** The docs say a timeout marks the mutant *"incompetent"* (concepts), but current `testing.py` returns `(TestOutcome.KILLED, 'timeout')`. **This is an unresolved doc/source discrepancy.**
- **Filters [D]:** `cr-filter-operators`, `cr-filter-pragma`, and `cr-filter-git` (skip lines not changed on a branch) mark items "skipped" ([filters](https://cosmic-ray.readthedocs.io/en/latest/how-tos/filters.html)).
- **Result evidence [I]:** Only the exit code and output of the whole test command. There is no per-test kill attribution, so a collection error also counts as "killed".
- **Transfer [I]:** Cosmic Ray has the same cost structure as Assay today: full suite, fresh process, on-disk mutation. It is the counter-example. Worth borrowing: the resumable session DB and the baseline gate.

## 5. Other tools

**mutatest 3.1.0** (2022-02-20, [PyPI](https://pypi.org/pypi/mutatest/json); [repo](https://github.com/EvanKepner/mutatest)) [D]:
- *"No source code modification, only the `__pycache__` is changed"*. Each trial runs the configured test command.
- `.coverage`-based filtering (`--nocov` turns it off), sampling `-n`, `--parallel` on Python 3.8+, and break-on-survival/detection modes.
- **[I]** It still runs the full test command per mutant.

**MutPy 0.6.1** (2019-11-17, [PyPI](https://pypi.org/pypi/MutPy/json); last push 2024-04-23 per the [GitHub API](https://api.github.com/repos/mutpy/mutpy)) [D, source]:
- The mutant is served from memory by an `InjectImporter` (`sys.modules[fullname] = self.module`) ([utils.py](https://github.com/mutpy/mutpy/blob/master/mutpy/utils.py)).
- Each mutant runs in `MutationTestRunnerProcess(…, Process)` with a result `Queue`.
- With coverage on, tests are skipped when `mutated_nodes.isdisjoint(coverage_result.test_covered_nodes[test_id])` (per-test, AST-node coverage).
- Timeout is `timeout_factor * total_duration` ([base.py](https://github.com/mutpy/mutpy/blob/master/mutpy/test_runners/base.py)).
- **[UNVERIFIED]** Whether it stops at the first failure.

**Major 3.0.1** ([docs](https://mutation-testing.org/docs.html)) [D]:
- *"Individual mutants can be enabled at runtime without recompilation"* (conditional mutation via `_M_NO`; `COVERED` monitors mutation coverage).
- `timeoutFactor` defaults to 8. `serializedMapsFile` caches preprocessing.
- `exportKillMap`: *"executes every test on every covered mutant!"* **[I]** That implies the default analysis runs only covering tests and stops early.
- **[UNVERIFIED]** The "runtime + mutation-coverage prioritization, up to 65% cost reduction" claim (Just & Schweiggert, STVR 2015) is known only from a search snippet. I couldn't read the abstract.

**mull 0.34.1** [D]:
- *"Each injected mutation is hidden under a conditional flag"*, all in one binary. Mutants run *"in child subprocesses"*. LLVM JIT was removed in January 2021 ([How Mull works](https://mull.readthedocs.io/en/latest/HowMullWorks.html)).
- Config has a per-test-run timeout, an include-not-covered switch, worker threads, and a git-diff ref for incremental runs ([config](https://mull.readthedocs.io/en/latest/MullConfig.html)).

**cargo-mutants 27.1.0** (2026-06-02, [crates.io](https://crates.io/api/v1/crates/cargo-mutants)) [D]:
- Copies the tree, runs a baseline, then for each mutant patches, builds and runs `cargo test`, then reverts ([how it works](https://mutants.rs/how-it-works.html)). *"incremental builds … are done once per viable mutant"* ([performance](https://mutants.rs/performance.html)).
- Test selection is coarse: *"each mutant runs only the tests from the package that's being mutated"* ([workspaces](https://mutants.rs/workspaces.html)).
- Bail: nextest *"can stop faster if a single test fails"*, but *"allows straggling tests to run to completion"* ([nextest](https://mutants.rs/nextest.html)).
- Timeout: *"5 times the baseline test time, with a minimum of 20 seconds"*; with `--baseline=skip` it defaults to 300 s ([timeouts](https://mutants.rs/timeouts.html)).
- `--shard k/n` (slice or round-robin): *"All shards must be run with the same arguments … or the results will be meaningless"* ([shards](https://mutants.rs/shards.html)).
- `--in-place` is incompatible with `--jobs` ([in-place](https://mutants.rs/in-place.html)).
- `--iterate` *"is a heuristic, and makes the assumption that any new changes you make won't reduce coverage"* ([iterate](https://mutants.rs/iterate.html)).
- `--in-diff`: *"a diff that only deletes or changes test code won't cause any mutants to run"* ([in-diff](https://mutants.rs/in-diff.html)).
- **[UNVERIFIED]** Whether it reports which test caught a mutant.

**Google** [D, text extracted from the [ICSE-SEIP 2018 PDF](https://research.google.com/pubs/archive/46584.pdf)]:
- *"Only lines affected by the diff under review that are covered and are not arid are mutated."*
- *"For each line, at most one mutant is generated."*
- *"Line level coverage is used for test execution phase, where the minimal set of tests is run in the attempt to kill the mutant."*
- *"At present it is infeasably expensive to compute the absolute mutation score for the codebase at any given fixed point."*
- *"Over 87% of all test runs over mutants fail … This is not the mutation score … because of the probabilistic nature of mutagenesis."*
- In Python, `if __name__ == "__main__":` is treated as an arid node.
- The follow-up "Practical Mutation Testing at Scale" ([arXiv 2102.11378](https://arxiv.org/abs/2102.11378); IEEE TSE 2022 vol. 48(10) per a [search listing](https://dl.acm.org/doi/10.1109/TSE.2021.3107634)) adds operator selection from historical performance. The ICSE'21 paper ([arXiv 2103.07189](https://arxiv.org/abs/2103.07189)) measures the effect on developers, not adequacy.
- **[I]** This is explicitly *not* a whole-source inventory. It is the opposite design point from Assay's goal.

## 6. Research techniques

- **Mutant schemata.** Untch, Offutt & Harrold, ISSTA'93 ([ACM](https://dl.acm.org/doi/10.1145/154183.154265)): all mutants are encoded into one "metaprogram", compiled once, *"over 300%"* improvement. The 300% figure comes from the ACM abstract as shown in a search snippet; I didn't read the full text.
- **Split-stream execution and AccMut.** Wang et al. ([arXiv 1702.06689](https://arxiv.org/abs/1702.06689)) describe split-stream (attributed to King & Offutt 1991 and Tokumoto et al.'s MuVM 2016) as: *"start with one process representing the original program, and split a new process when the first mutated statement of a mutant is encountered."* AccMut groups mutants that are "equivalent modulo state" and reports 2.56× over split-stream and 8.95× over schemata on C programs. **[UNVERIFIED]** The ISSTA'17 venue; the arXiv page doesn't state it.
- **Data compression + state infection.** Zhu, Panichella & Zaidman, ICSTW'17 ([PDF](https://pure.tudelft.nl/ws/portalfiles/portal/45811968/main.pdf)): filter out uninfected test executions, cluster mutants (FCA), *"reduce the execution time by 83.93% with only 0.257% loss in precision"*. It is approximate.
- **Predictive mutation testing (PMT).** Zhang et al., ISSTA'16 ([PDF](https://lingming.cs.illinois.edu/publications/issta2016.pdf)): *"predicts mutation testing results without mutant execution"*, with speedups of 15.2× to 151.4×. It is **prediction, not evidence**.
- **Sampling.** Gopinath et al., ISSRE'15 ([PDF](https://agroce.github.io/issre15.pdf)):
  - *"Observation 2: 1,000-sampling approximates mutation score with high accuracy, 0.62% on average."*
  - A sample of 9,604 gives 99% accuracy for 95% of samples, and *"our results hold even if the mutants are not independent."*
  - Sufficient operators (Offutt et al., TOSEM 1996, [ACM](https://dl.acm.org/doi/10.1145/227607.227610)): **[UNVERIFIED]** abstract not retrievable.
- **FaMT.** Zhang, Marinov & Khurshid, ISSTA'13 ([PDF](https://lingming.cs.illinois.edu/publications/issta2013a.pdf)): prioritize tests per mutant so a killer runs earlier. Reduction *"does not preserve coverage of test requirements"*, so it is unsafe for survivors. **[UNVERIFIED]** The quantitative results; only the first pages extracted.
- **ReMT.** Zhang et al., ISSTA'12 ([PDF](https://mir.cs.illinois.edu/marinov/publications/ZhangETAL12RegressionMutationTesting.pdf)): *"a mutant-test result can be reused if (1) no dangerous edge is executed from the beginning of the test to the mutated statement and (2) no dangerous edge can be executed from the mutated statement to the end of the test."* It uses dynamic coverage plus static CFL-reachability, and *"only works for traditional mutation operators that change statements in methods."*
- **RTS for mutation testing.** Chen & Zhang, ICST'18 ([PDF](https://lingming.cs.illinois.edu/publications/icst2018.pdf)): *"both file-level static and dynamic RTS can achieve precise and efficient mutation testing."*
- **Ekstazi.** Gligoric et al., ISSTA'15 ([PDF](https://users.ece.utexas.edu/~gligoric/papers/GligoricETAL15Ekstazi.pdf)): *"tracks dynamic dependencies of tests on files"*, with 32% average end-to-end reduction (54% for long suites). Definition: *"RTS is safe if it guarantees that the subset of selected tests includes all tests whose behavior may be affected by the changes."* **[UNVERIFIED]** Ekstazi's own safety argument text.
- **Weak vs. strong mutation.** Howden, TSE 1982 ([IEEE](https://ieeexplore.ieee.org/document/1702959/)). Second-order mutant batching: Polo et al., STVR (seen only as a search listing). Both **[UNVERIFIED]** beyond their bibliographic data.

## 7. The four required quotes

**(a) Static and import-time mutants under schemata or coverage-based selection:**
- Stryker: *"A static mutant is a mutant that is executed once on startup instead of when the tests are running."* ([static mutants](https://stryker-mutator.io/docs/mutation-testing-elements/static-mutants/))
- Stryker v6: *"Testing static mutants can be very expensive … they need a fresh worker process for each run (for Node-based test runners)." "On top of that, Stryker cannot determine test coverage for them. So the only thing it can do is run all tests." "Stryker will also sort mutants, testing static mutants after non-static mutants." "…a whopping 50% performance improvement by ignoring 6% of our mutants (the static mutants)."* ([v6 blog](https://stryker-mutator.io/blog/stryker-js-v6-expeditious-superior-mutations/))
- Stryker.NET: *"mutants that are executed as part of some static constructor/initializer are run against all tests as Stryker cannot reliably capture coverage for those."* ([.NET config](https://stryker-mutator.io/docs/stryker-net/configuration/))
- Stryker incremental: *"Static mutants don't have test coverage; thus, Stryker won't detect test changes for them."* ([incremental](https://stryker-mutator.io/docs/stryker-js/incremental/))
- Framework support table: StrykerJS and .NET run static mutants against all tests by default; Stryker4s ignores them ([static mutants](https://stryker-mutator.io/docs/mutation-testing-elements/static-mutants/)).
- PIT: *"the only test to execute a static initializer will be the first test to run that causes that class to load" … "code in static initializer blocks is not re-run so the mutants have no effect" … "It will not create mutants in static initializers, private methods called only from static initializers. You will however encounter other scenarios that this simple filtering will miss."* ([FAQ](https://pitest.org/faq/))
- coverage.py, the Python equivalent: *"Any code measured before a dynamic context is set will be recorded in this empty context."* ([contexts](https://coverage.readthedocs.io/en/latest/contexts.html))
- Also: the `sysmon` core *"does not yet support plugins, dynamic contexts"*, so a per-test map needs the `ctrace` core ([config](https://coverage.readthedocs.io/en/latest/config.html)).
- pytest-cov `--cov-context=test` records per-test `setup` / `run` / `teardown` phases ([pytest-cov](https://pytest-cov.readthedocs.io/en/latest/contexts.html)).

**(b) pytest-testmon 2.2.0** (2025-12-01, [PyPI](https://pypi.org/pypi/pytest-testmon/json)):
- It tracks project code, *"environment variables (e.g. DJANGO_SETTINGS_MODULE), python version"* and package versions.
- It does not track *"static files (txt, xml, other project assets)"* or *"external services (reachable through network)"* ([testmon.org](https://testmon.org/)).
- *"The limits and reliability of this method are pretty much the same as limits of coverage.py"* ([determining affected tests](https://testmon.org/blog/determining-affected-tests/)).
- It can't handle *"tests that depend on a global state which they don't control for sufficiently"* ([hidden dependencies](https://testmon.org/blog/hidden-test-dependencies/)).
- `--testmon-nocollect` is forced *"when running under a debugger or coverage tool"* ([testmon.org](https://testmon.org/)). **[I]** That conflicts with Assay's coverage-on runs.

**(c) PIT `fullMutationMatrix` and test selection:**
- *"When set to true causes pitest to continue processing after a test fails and record addition failing tests when XML output is enabled"* ([maven](https://pitest.org/quickstart/maven/)).
- *"Per test case line coverage information is first gathered and all tests that do not exercise the mutated line of code are discarded. The remaining tests are then ordered by increasing execution time…"* ([FAQ](https://pitest.org/faq/)).

**(d) mutmut 3 per-mutant test selection:**
- The docs only say it *"Knows which tests to execute"* and describe *"runs your test suite to collect stats, then forks one child per mutant"* ([README](https://github.com/boxed/mutmut/blob/3.8.0/README.rst)).
- The mechanism is in source, not in the docs:
  - `tests_for_mutant_names()` maps the mutant's mangled function name to the recorded test set.
  - Exit 33 if the set is empty.
  - The run is `pytest -x` over those node ids ([`__main__.py`](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/__main__.py), [harness.py](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/runners/harness.py)).

## 8. Transfer to Assay: whole-source, auditable, complete inventory [all I]

1. **Where the ~560 s goes.** Per candidate, Assay pays interpreter start, full collection of 5,831 tests, coverage tracing, and *all* tests even after the first failure. 560 / 5,831 ≈ 0.1 s per test if the tests dominate. Every tool above drops at least three of those four costs. None of them reruns coverage during mutant runs.
2. **Phase 1: one baseline run.** Build a per-test coverage map at line or function level (`dynamic_context=test_function` / `--cov-context=test`, `ctrace` core) plus per-test durations. With that you can:
   - estimate the campaign cost up front, as mutmut does with `estimated_worst_case_time`;
   - issue `NoCoverage` verdicts with no execution;
   - set per-mutant timeouts from the covering tests' times, not the suite time.
3. **Phase 2: per mutant.** Run only the covering tests, fastest first, previous killer first (PIT rule 4 is the only reuse PIT calls error-free, because it only changes order), with `-x`, coverage off, in a worker forked from a pre-imported process (mutmut fork/forkserver). Activate the mutant by schemata/trampoline or in-memory module injection (MutPy), not by rewriting the source and cold-starting.
   - Survivors still cost the full *covering* set. That is the irreducible floor for a strong-mutation verdict (FaMT).
4. **Static / import-time mutants need their own path.** Module-level code, class bodies, decorators, default arguments and constants land in coverage.py's empty context. They behave like PIT's static initializers. Options:
   - Flag them `static`, run them against the full suite in a fresh process, and sort them last (Stryker's default). This keeps the inventory complete.
   - Or mark them `Ignored` explicitly with a reason (Stryker `ignoreStatic`, Stryker4s default).
   - Silently omitting them (mutmut 3, PIT) would break "complete inventory".
   - Measure how many of the 3,760 fall in this class first. Stryker saw 6% of its mutants cost about 50% of the runtime.
5. **Soundness hazards in the coverage map:**
   - Session- or module-scoped fixtures get attributed to the first test that triggers them (pytest-cov's `setup` phase; PIT's "first test to load the class").
   - Code run in subprocesses spawned by tests is invisible unless subprocess coverage is configured. **[UNVERIFIED]** The current coverage.py mechanism for that.
   - Global-state test coupling (testmon).
   - Each of these can wrongly produce `NoCoverage` or drop the only killing test.
   - Mitigation: treat empty-context and fixture-setup lines as static, and audit a random sample of `NoCoverage` and survivor verdicts against the full suite.
6. **Auditable evidence without the full matrix.** Record per mutant: `killedBy` (the first failing test), `coveredBy`, `testsCompleted`, `static`, and the baseline-map hash (Stryker schema). A survivor's claim is "survived all tests in coveredBy", which is exactly what a PIT or Stryker survivor proves. Offer an opt-in full-matrix audit mode (PIT `fullMutationMatrix`, Major `exportKillMap`).
7. **Incremental reuse.** Every tool calls reuse heuristic: PIT *"unproven"*, Stryker lists changes it can't detect, mutmut *"warn … keep the cache"*, cargo-mutants *"heuristic"*. For audit-grade reuse:
   - Key each verdict on the hash of the mutated function, the hashes of every covering test (and fixtures / conftest), the dependency lockfile, the environment and the tool config.
   - Label reused verdicts as carried forward, with the run they came from.
   - Keep periodic full runs. ReMT's dangerous-edge conditions are the formal version of this.
8. **What does not fit a complete-inventory goal:** diff-only runs (Google, Arcmutate, `--in-diff`, `cr-filter-git`, Stryker.NET `since`), sampling (Gopinath: about 1,000 mutants gives an estimate within about 0.6%, but that isn't an inventory) and PMT (prediction). They suit a fast PR gate, reported as a separate, clearly labelled metric.
9. **Parallelism.** Shard-style partitioning is available (Cosmic Ray workers, cargo-mutants `--shard`). Given the shared host, the algorithmic changes above are the main lever, not more workers.

## Could not verify

- PIT's exact change hash for incremental analysis.
- mutmut's `status_by_exit_code` mapping, its killer attribution, and whether test edits invalidate its cache.
- Whether StrykerJS orders tests within a mutant.
- Stryker release dates.
- Whether MutPy stops at the first failure.
- Major's prioritization numbers.
- The Descartes comparison.
- FaMT's quantitative results.
- The Untch '93 figure, which I saw only as a search snippet.
- The Offutt '96 abstract.
- The AccMut venue.
- Ekstazi's safety argument text.
- The Cosmic Ray timeout status (docs and source disagree).

I modified no files. The only local activity was extracting text from downloaded paper PDFs in the scratchpad.