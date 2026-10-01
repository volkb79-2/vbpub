# W3 Part 1 — component boundaries: measured report (B130)

Base `3daf62a7` (`assay-b110-landing`), branch `wave-a-w3-boundaries`. Scripts: `w3-scripts/` (`common.py`, `graph.py`, `reach.py`, `coupling.py`, `durations.py`; candidates from `b110/research/scripts/r11/candidates.py`). Data: `w3-coupling.json`. The coverage database (86.9 MB) stays in the scratch dir. Tags: [M] measured, [C] computed from measurements, [I] inference, [A] assumption.

## 0. The run (step 4)

`COVERAGE_CORE=ctrace`, `--cov-branch --cov-context=test`, serial, `-p no:xdist -p no:randomly`, niced. 5965 tests collected; 12 test paths ignored (below). [M] exit 1, **5626 passed, 1 failed, 1 skipped**, wall 288 s (pytest 285.46 s), 5628 junit testcases.

- The one failure is environmental and is data (A-040): `tests/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (DID NOT RAISE). A `/tmp/.git` exists on this host, and the test builds its tree under `/tmp`. It fails the same way at base and after the moves. It is not part of this package's scope.
- Contexts: 5036 raw contexts, **4902 distinct nodeids** after stripping `|setup|run|teardown`, against 5628 junit testcases [M]. A context only exists when the test executed a measured line, so ~726 testcases left no context (parametrized ids that execute nothing new, pure-data tests) [I]. The empty context `""` holds 3593 (file, line) entries, the import-time lines [M]. Non-empty contexts exist, so the sysmon symptom is absent.
- Caveats: `COVERAGE_FILE` leaks into child interpreters that run coverage [M, static]; `test_analysis.py`'s FIFO case still builds the `standalone` wheel inside the run (a gap: that child's coverage is not collected).
- Ignored (each a gap): `test_self_hosting.py`, `qualification/` (containers), `test_gate_qualify_dstdns_sql.py`, `test_gate_qualify_cmru_b006a.py` (cross-project), `test_python_qualification.py`, `test_standalone.py`, `test_distribution_build_release.py`, `test_distribution_gate.py`, `test_distribution_release_wheel.py` (wheel builds), `test_dependency_purity.py`, `test_verdict_schema_is_packaged.py`, `test_go_helper_is_packaged.py` (wheel/recursion).

## 1. Map (steps 1)

51 modules, each in exactly one component (`component()`, brief 2a). Lines sum to the `wc -l` total, 47935 [M]. Candidates (whole target, four python operators): 4007 [M] (541 bool-const-flip, 1054 boolop-swap, 2277 compare-swap, 135 falsy-swap).

| component | modules | lines | candidates |
|---|---|---|---|
| core | 29 | 37028 | 2993 |
| analysis | 1 | 1130 | 317 |
| adapter.sql | 2 | 1099 | 156 |
| parsers.coverage | 7 | 2539 | 153 |
| adapter.go | 3 | 1606 | 132 |
| cli | 1 | 2062 | 117 |
| adapter.python | 1 | 893 | 51 |
| adapter.javascript | 1 | 577 | 49 |
| parsers.mutation | 3 | 697 | 30 |
| parsers.result_reports | 3 | 304 | 9 |
| total | 51 | 47935 | 4007 |

Members: adapter.go = `adapters.go, go_modfile, go_stmtpos`; adapter.sql = `adapters.sql, sql_lex`; parsers.coverage = `coverage_parsers` + `cobertura, coverage_istanbul_json, coverage_py_json, go_cover, lcov, model`; parsers.mutation = `mutation_parsers` + `model, mutation_report_json`; parsers.result_reports = `result_reports` + `model, vitest_json`; core includes `assay/__init__`, `adapters/__init__`, `adapters.base` and 26 flat modules (`runner` 6561 lines, `verdict` 5455, `mutation` 4074, `config` 3773, `verify` 3180 dominate). Core holds 74% of candidates: a core-only partition is where any per-component R2 saving would be small [C].

## 2. Static graph (step 2)

[M] 176 assay-internal module edges (function-level and `TYPE_CHECKING` included). Three are `TYPE_CHECKING`-only: `liveness -> runner`, `mutation -> adapters.base`, `mutation_witness -> runner`. Component edges: `cli` imports every other component; adapters import only core (`adapters.base, errors, mutation, safeio, statement_attribution`), parsers import only core (`errors, vocabulary`), `analysis` imports `assay, errors, git, mutation (nested), verify`; core imports parsers (`coverage` -> six coverage parser modules, `config`/`runner` -> mutation/result-report parser packages, six modules -> `coverage_parsers.model`) and never an adapter or `cli`. The full edge list is reproducible with `graph.py`.

- Component-level cycles: `core <-> parsers.coverage` and `core <-> parsers.mutation` [M]. They are not violations under the 2a contract: the parser package imports leaf core modules (`errors`, `vocabulary`) and core imports the parser surface. They are the reason 2a needs the `PARSER_DEPS`/`CORE_PARSER_SURFACE` lists.
- Module-level cycles inside core: 118 simple cycles, all through the strongly connected group `config, verdict, canary, runner, evaluate, mutation, isolation, verify, reuse, adapters.base` [M]. Core is one tightly coupled unit; no boundary can be cut inside it without a design change (out of scope).
- Implicit edge: every `import assay.X` first runs `assay/__init__.py`, which imports `config, errors, verdict` [M]. It is core-to-core and adds no boundary edge.

## 3. Dynamic reach (step 3)

[M] Fresh `python3 -I` per module. Importing any single module (even `assay.errors`) loads at least 23 assay modules, including all 13 parser modules, because `assay/__init__` pulls `config` and `verdict` and those pull the parser packages. Only `assay.cli` loads adapters other than the imported one: all 7 adapter modules load only through `assay.cli` (and each adapter module loads itself and, for go/sql, its own helpers). No core module loads an adapter. This confirms the expected shape. Consequence [C]: import-time lines of parsers and core execute in the empty context for every test, so they can never be attributed to a partition.

## 4. Coupling (step 5)

Matrix: distinct executed (file, line) pairs, test component (rows, from the basename via `expected_dir`, `root` for the 24 pinned names) by source component. Reproduced byte for byte on a rerun of `coupling.py` (`w3-coupling.json`) [M].

| test \ source | core | cli | analysis | adapter.go | adapter.js | adapter.python | adapter.sql | parsers.cov | parsers.mut | parsers.rr |
|---|---|---|---|---|---|---|---|---|---|---|
| core | 12953 | 861 | 38 | 3 | 1 | 189 | 121 | 168 | 140 | 46 |
| root | 6988 | 367 | 927 | | | 114 | | 56 | | |
| adapters/go | 513 | 7 | | 475 | | 4 | | 108 | | |
| adapters/javascript | 2741 | 258 | 38 | | 102 | | | 119 | | |
| adapters/python | 2802 | | | | | 225 | | 57 | | |
| adapters/sql | 93 | | | | | | 440 | | | |
| parsers/coverage | 2851 | 240 | 38 | | 49 | | | 841 | | |
| parsers/mutation | 10 | | | | | | | | 194 | |
| parsers/result_reports | | | | | | | | | | 52 |

Reading [C]: each adapter's partition executes far more core than adapter lines (e.g. adapters/javascript: 2741 core vs 102 own; adapters/python: 2802 vs 225), because their tests drive the pipeline. The reverse, foreign partitions executing adapter lines, is large for python (core 189, root 114) and sql (core 121).

Consistency checks [M]: per source component, the union over contexts equals coverage's measured lines for every component (core 15855, cli 919, analysis 995, go 593, js 152, python 327, sql 559, parsers.coverage 984, parsers.mutation 266, parsers.result_reports 88).

Candidate sites (4007) by own-partition classification (own partition: core->core, cli->core, analysis->root, adapter.X->adapters/X, parsers.coverage->parsers/coverage, and so on). 816 sites (20%) were attributed to their enclosing statement start because the operator's line is a continuation line of a multi-line statement [M]; that is a limitation of this join [A].

| source | own_only | both | foreign_only | import_time_only | none |
|---|---|---|---|---|---|
| core | 599 | 1969 | 36 | 150 | 239 |
| cli | 42 | 63 | 7 | 0 | 5 |
| analysis | 304 | 12 | 0 | 1 | 0 |
| adapter.go | 127 | 0 | 0 | 5 | 0 |
| adapter.javascript | 28 | 17 | 0 | 4 | 0 |
| adapter.python | 8 | 34 | 0 | 5 | 4 |
| adapter.sql | 117 | 25 | 0 | 7 | 7 |
| parsers.coverage | 81 | 59 | 0 | 9 | 4 |
| parsers.mutation | 1 | 23 | 0 | 6 | 0 |
| parsers.result_reports | 0 | 7 | 0 | 2 | 0 |

`none` = no test in this run executed the line, and includes the ignored tests (gap, section 5). `analysis` is measured against the root partition (its tests are pinned at root today).

## 5. Gaps and lower bounds (step 6)

- 12 ignored paths (section 0). Any candidate whose only killer is there is `none` here [I].
- Subprocess: 75 test files mention `subprocess`, `Popen`, `sys.executable` or `runpy` (most run `git`). Those that launch assay itself (`sys.executable` with `-m assay`, `assay.cli` or a wheel): `conftest.py`, `test_analysis.py`, `test_b105_report_check.py`, `test_b106_reuse_and_witness.py`, `test_cli_run.py`, `test_distribution_*.py`, `test_gate_qualify_*.py`, `test_python_qualification.py`, `test_r3_canary_sees_infrastructure.py`, `test_self_hosting.py`, `test_standalone.py`. Coverage of the child is not collected here (`--cov` subprocess support is not configured), so the rows of those files are lower bounds [M, static].
- Sites attributed by statement start (816) and tests that leave no context (~726) are measurement limits [M].
- ctrace core here; under sysmon (R9) dynamic contexts do not exist, so the same join cannot be repeated with the production core [A, per RUNTIME-ANALYSIS section 7.10].

## 6. Contracts (input to Part 2)

The 2a contract holds on the tree at `3daf62a7`: 176 edges, the walk in `common.py` finds no edge outside the allowed sets (Part 2's test asserts it). Adapters import only the leaf modules `adapters.base, errors, mutation, safeio, statement_attribution` today; `records` and `guards` (CD27) do not exist yet.

## 7. Recommendation on a component-scoped R2

- **The claim to test:** every mutant of component X is killed by X's own partition.
- **Saves [C]:** own-partition run time from junit: adapters/go 0.5 s, adapters/sql 0.3 s, parsers 7.3 s together, adapters/javascript 1.9 s, adapters/python 15.0 s, against a 276.6 s full run (core 203.6 s, root 48.0 s). The seven non-core partitions together take 25.0 s (9%); only they get a cheaper lane; core (74% of candidates, 74% of time) does not shrink.
- **Loses [M]:** kills by foreign partitions. Of adapter/parser candidate sites, `both` is large (javascript 17, python 34, sql 25, parsers.coverage 59, parsers.mutation 23 of 30, result_reports 7 of 9), so most sites have a foreign killer as well as an own one; `foreign_only` is 0 for adapters and parsers, 36 for core and 7 for cli. `import_time_only` (adapters 21, parsers 17, core 150) can never be killed by a partition run, and neither can `none`.
- **[I] lower bounds:** the subprocess rows and ignored tests undercount both own and foreign killers.
- **Prerequisites:** the 2a map; one lane per partition; an R11 section 5.2-style checker binding a lane to its partition; the R2 claim moved from the whole suite to each partition.
- **Decision needed (operator):** whether to accept a per-component R2 claim at all. The data shows it would save real time for adapters (python is the only sizable one) and parsers, save nothing for core, and lose the sites above. Part 2 (layout, contracts) does not depend on it.
