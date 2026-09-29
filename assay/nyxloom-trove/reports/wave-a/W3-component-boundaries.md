# W3 — Component boundaries: measure (Part 1), then import contracts and test layout (Part 2) (B130)

| Field | Value |
|---|---|
| Backlog | **B130** (operator: "when we test python, do the react/go tests not run?") |
| Stage | **0**, before W1/W2 (operator). Later packages re-find `tests/` anchors by file name |
| Branch | `wave-a-w3-boundaries` from `assay-b110-landing` (`5bbd916e`). Own worktree; merge `--no-ff`; then delete worktree and branch |
| Contract class | Part 1: research. Part 2: **2e** (locked rules, edit rule, oracles) |
| Implementer | **Sonnet**, fresh session (operator rule); Part 1 before Part 2 |
| Decisions | A-005, A-476, A-478. A component-scoped R2 claim is a later operator decision; nothing here depends on it |

## Carver questions (the brief assumes the recommendation)
- **C1.** Stdlib AST test, not `import-linter` (a test-only dependency needs a pinned wheel in the gate closure and a decision). **Rec.: AST.**
- **C2.** Language-specific integration tests (e.g. `test_canary_go_pipeline.py`) go to `tests/adapters/<lang>/`. **Rec.: yes.**
- **C3.** 24 files stay at root (2b): path-referenced outside `tests/`, or next moved/deleted by W1/W2/W4/W5. **Rec.: yes.**
- **C4.** Part 1 findings never re-map Part 2 (a later package).

## Context to read first (verified at `5bbd916e`, relative to `assay/`)
1. Plan §2, §4; `4-backlog.md:11670` (B130); A-478 (`decisions.md:1023`).
2. `src/assay/registry.py:1-60,94,145-195`; `cli.py:72-89` (concrete adapter imports), `:134`, `:516-671` (`_built_in_registry`); `__init__.py:19-31` (imports `config`, `errors`, `verdict`); `coverage.py:58-66`; `mutation.py:149-155` (TYPE_CHECKING).
3. `reports/b110/research/R11-dry-libraries.md` §4–§5, `scripts/r11/candidates.py`; R9 §1; `assay-B110-RUNTIME-ANALYSIS-2026-09-28.md` §7.10 (sysmon: no dynamic contexts; import-time lines get the empty context).
4. `tests/conftest.py:52`; `tests/test_dependency_purity.py:22-40` (AST precedent); `tests/qualification/test_javascript_real_vitest.py:37` (subdirectory test importing `conftest`).

## Part 1 — research (read-only: no gate, no mutation campaign, no container)
1. **Map.** Each of the 51 `src/assay/**/*.py` modules in exactly one component (`component()`, 2a). Per component: modules, lines, candidates from `scripts/r11/candidates.py` (whole-target).
2. **Static graph.** AST module edges incl. function-level and `TYPE_CHECKING` imports (marked); component aggregation; cycles; the implicit `assay/__init__.py` edge.
3. **Dynamic reach.** Per module M, fresh interpreter: `nice -n 19 python3 -I -c "import sys; sys.path.insert(0,'src'); import M; print(sorted(k for k in sys.modules if k.startswith('assay')))"`. Which modules load which adapters (expected only via `assay.cli`; verify).
4. **One serial coverage-context run**, before any move. `SCR=$(mktemp -d /tmp/w3-XXXX)`; `IG` = `--ignore=tests/X` for X in `test_self_hosting.py qualification test_gate_qualify_dstdns_sql.py test_gate_qualify_cmru_b006a.py test_python_qualification.py test_standalone.py test_distribution_build_release.py test_distribution_gate.py test_distribution_release_wheel.py test_dependency_purity.py test_verdict_schema_is_packaged.py test_go_helper_is_packaged.py` (recursion, containers, cross-project, wheel builds; record each as a gap):
```bash
cd <wt>/assay && COVERAGE_CORE=ctrace COVERAGE_FILE=$SCR/before.coverage nice -n 19 ionice -c3 python3 -m pytest tests -q \
 -p no:cacheprovider -p no:xdist -p no:randomly --override-ini=pythonpath=src --cov=src/assay --cov-branch \
 --cov-context=test --cov-report= --durations=0 --junitxml=$SCR/before.xml $IG > $SCR/before.log 2>&1; echo "exit=$?"
```
   Failures are data (A-040). Record exit, wall time, core, context count.
5. **Coupling.** From the database (`coverage.CoverageData`): a context's part before `::` is the test file; its component is `expected_dir()` (2b; root = `root`). Matrix test-component × source-component = distinct executed lines. Per candidate site (step 1): own-only / both / foreign-only / none / import-time-only counts per source component.
6. **Subprocess gaps.** Static list of tests running assay in a child (`subprocess`, `Popen`, `sys.executable`, `runpy`); their rows are lower bounds.
7. **Recommendation** on component-scoped R2: the claim ("each component's mutants are killed by its own partition"); saves ([C] own-partition vs full-suite `--durations`, candidate-weighted); loses ([M] foreign-only and import-time-only candidates, [I] subprocess gaps); prerequisites (2a, a lane per partition, an R11 §5.2-style checker binding); the decision needed.

**Deliverables:** `reports/wave-a/W3-REPORT-component-boundaries.md` (§1 map, §2 graph, §3 reach, §4 coupling, §5 gaps, §6 contracts = 2a plus any failing contract with its edges, §7 recommendation); `w3-scripts/`; `w3-coupling.json` (both in `reports/wave-a/`). The database stays in `$SCR`. Tag claims [M]/[C]/[I]/[A].

**Deliverable checks:** 51 modules, one component each, lines reconcile with `wc -l`; per source component the union over contexts equals coverage's measured lines; contexts within ±1% of the junit test count (else wrong core: stop); rerunning step 5 reproduces `w3-coupling.json` byte for byte; no predicted number.

## Part 2 — implementation (same branch)

### Implementation packet (normative)
**2a. New `tests/core/test_import_contracts.py`** (name must match `import.contract`; W2 T6 extends it). `import_edges()` walks `src/assay/**/*.py` with `ast.walk` (module, nested, `TYPE_CHECKING`), resolves relative levels against the importer's package (`__init__.py` is its own package), maps `from X import n` to `X.n` when that is a module else `X`, drops self-edges. Carver-verified: 176 edges, 0 violations at `5bbd916e`.
```python
def component(m):
    if m in ("assay.adapters", "assay.adapters.base"): return "core"
    for lang in ("python", "javascript", "go", "sql"):
        if m.startswith(f"assay.adapters.{lang}"): return f"adapter.{lang}"
    for pkg, name in (("coverage_parsers", "parsers.coverage"), ("mutation_parsers", "parsers.mutation"),
                      ("result_reports", "parsers.result_reports")):
        if m == f"assay.{pkg}" or m.startswith(f"assay.{pkg}."): return name
    if m == "assay.analysis": return "analysis"   # W2 removes this row
    if m == "assay.cli": return "cli"             # composition root
    return "core"
ADAPTER_DEPS = {"assay.adapters.base", "assay.errors", "assay.mutation", "assay.safeio", "assay.statement_attribution"}
PARSER_DEPS = {"assay.errors", "assay.vocabulary"}
CORE_PARSER_SURFACE = {"assay.coverage_parsers.model", "assay.mutation_parsers", "assay.mutation_parsers.model", "assay.result_reports"}
ANALYSIS_DEPS = {"assay", "assay.errors", "assay.git", "assay.mutation", "assay.verify"}
def allowed(importer, imported):
    ci, ct = component(importer), component(imported)
    if ci == "cli": return True
    if ci == ct and ci != "analysis": return True
    if ci.startswith("adapter."): return imported in ADAPTER_DEPS
    if ci.startswith("parsers."): return imported in PARSER_DEPS
    if ci == "analysis": return imported in ANALYSIS_DEPS
    if ct == "parsers.coverage" and importer == "assay.coverage": return True  # format dispatcher
    return imported in CORE_PARSER_SURFACE
```
Tests: (i) real tree: no violation; the message lists every violating `importer -> imported`. (ii) `allowed` refuses `assay.runner->assay.adapters.python`, `assay.adapters.go->assay.adapters.python`, `assay.coverage_parsers.lcov->assay.runner`, `assay.evaluate->assay.coverage_parsers.lcov`, `assay.verdict->assay.analysis`, `assay.adapters.sql->assay.config`; accepts `assay.coverage->assay.coverage_parsers.lcov`, `assay.adapters.go->assay.adapters.go_modfile`. (iii) on a synthetic source string the extractor finds a function-level `from ..adapters import python` and a `TYPE_CHECKING` import, correctly resolved.

**2b. Layout rule** (second section of the same file):
```python
ROOT_PINNED = frozenset({  # path-referenced from gate script, assay.toml, build_release.py, carve-assets/P23
    "test_self_hosting.py", "test_runner_snapshot_selection.py", "test_lane_schema_v2_locked_successors.py",
    "test_verdict_v13_successors.py", "test_b106_reuse_and_witness.py", "test_distribution_build_release.py",
    "test_verdict_conformance.py", "test_errors.py",  # imported by test_verdict_conformance.py:296
    # deleted by W1, rewritten by W5, moved by W2 or W4
    "test_python_qualification.py", "test_gate_qualify_cmru_b006a.py", "test_gate_harness_version_pins.py",
    "test_gate_qualify_dstdns_sql.py", "test_analysis.py", "test_analysis_json_framer.py",
    "test_distribution_gate.py", "test_distribution_release_wheel.py", "test_standalone.py", "test_cgroup_parent.py",
    "test_self_lane.py", "test_go_helper_is_packaged.py", "test_verdict_schema_is_packaged.py",
    "test_dependency_purity.py", "test_b105_report_check.py", "test_gate_failure_diagnostics.py"})
EXCEPTIONS = {"test_evaluate_javascript_end_to_end.py": "parsers/coverage"}  # imports test_coverage_istanbul_default_arg_signature
LANGS = ("python", "javascript", "go", "sql")
def expected_dir(name):
    if name in ROOT_PINNED: return ""
    if name in EXCEPTIONS: return EXCEPTIONS[name]
    words = name[len("test_"):-len(".py")].split("_")
    if words[0] == "adapters" and words[1] in LANGS: return f"adapters/{words[1]}"
    if words[0] == "coverage": return "parsers/coverage"
    if name in ("test_mutation_format_registry.py", "test_mutation_report_json_parser.py"): return "parsers/mutation"
    if words[:2] in (["result", "report"], ["result", "reports"]): return "parsers/result_reports"
    for lang in LANGS:
        if lang in words: return f"adapters/{lang}"
    return "core"
```
Carver-run at `5bbd916e`: core 156, parsers/coverage 24, adapters/go 15, python 12, javascript 5, sql 5, parsers/mutation 2, parsers/result_reports 2, root 24; `qualification/` and `fixtures/` stay. Tests: every `tests/**/test_*.py` outside `qualification/` is in `tests/<expected_dir(name)>` (pinned names may be absent); basenames unique under `tests/`; no `__init__.py`, and no `conftest.py` except `tests/conftest.py`; a synthetic-name table maps per the rules; a synthetic misplaced path is reported.

**2c. Path expressions.** Add `TESTS_ROOT = PROJECT_ROOT / "tests"` after `tests/conftest.py:52`. In every moved file, replace `Path(__file__).resolve().parent`, `Path(__file__).parent` and `_Path(__file__).resolve().parent` with `TESTS_ROOT`, and `Path(__file__).resolve().parents[1]` with `PROJECT_ROOT`; import the name from `conftest` (extend an existing `from conftest import`). At `5bbd916e`: 27 moved files, 30 occurrences, only these four forms (`git grep -lE "Path\(__file__\)" -- 'tests/test_*.py'` minus root-pinned). Add `from conftest import …` where absent. Remove a `Path`/`_Path` import only if pyflakes says it is unused. Nothing else changes; `Path(module.__file__)` and `SimpleNamespace(__file__=…)` uses do not match and stay.

### Work
1. Commit Part 1 first (its run is the baseline).
2. `nice -n 19 python3 -m pytest tests --collect-only -q -p no:cacheprovider > $SCR/collect-before.txt`.
3. Add `TESTS_ROOT` and write `tests/core/test_import_contracts.py` (2a, 2b).
4. `git mv` each `tests/test_*.py` with non-empty `expected_dir` to `tests/<dir>/<same name>` (a loop is fine). Write `reports/wave-a/W3-test-moves.tsv` (`old<TAB>new`).
5. Apply 2c. Update prose paths at `tests/conftest.py:1220-1223`, `docs/DESIGN-GUIDE.md:712`, `docs/CONSUMERS.md:1746,2111`.
6. Docs: DESIGN-GUIDE §11 gets "Component boundaries and test layout" (2a in prose, the 2b rule; the layout groups tests by subject and claims nothing about kill ownership). README "## Testing" (`:1024`): two sentences pointing there. Backlog B130: Part 2 done, report linked.
7. Oracles, then the gate.

### Oracles
| # | Observable | Negative (apply locally, see red, revert, log it) |
|---|---|---|
| O1 | `test_import_contracts.py` passes | a function-level `from .adapters import python` in `src/assay/runner.py` → red naming the edge |
| O2 | layout tests pass | `git mv` one core test back to root → red naming it |
| O3 | collect-only after the moves has the same `(basename, test id)` set as step 2 | an unrewritten fixture path → error |
| O4 | `git grep -nE "Path\(__file__\)" -- tests/core tests/adapters tests/parsers` empty; `git diff -M --stat`: renames, non-100% only for 2c files | — |
| O5 | Part 1 step 4 rerun unchanged into `$SCR/after.*`: same `(basename, testcase)` keys and outcomes as `before.xml` | a differing test is run alone at base and at HEAD once; still differing → BLOCKED |
| O6 | `nice -n 19 python3 -m pyflakes src/assay tests` clean | — |
| O7 | registered gate green except the known red test | — |

**Forbidden in tests you write (AUTHORING §3b):** A. no time-based verdicts (`monotonic()+N` then assert, `sleep` to wait, elapsed-time asserts); timeouts only as generous hang failsafes; a slow-host red is a true red. B. no unrestored global state, no `monkeypatch` of `__getattr__` proxies, no destructive teardown. C. no hollow tests (`pass`, "nothing raised", call counts, private attributes, log strings); never weaken an assertion. D. no no-cover pragma, even in comments. E. no real network or clock values. F. no predicted coverage/mutation numbers.

## Scope
- **Touch:** `tests/**` (moves, 2c, new test, `conftest.py`), `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, `README.md`, `4-backlog.md`, `reports/wave-a/W3-*`, `w3-*`.
- **Forbid:** `src/**`, `gate/**`, `tools/**`, `assay.toml`, `pyproject.toml`, `run-gate.toml`, `tests/fixtures/**`, `tests/qualification/**`, any assertion, pytest config.

## Gate
```bash
docker ps   # no other session's gate container
cd <wt>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W3-gate.log 2>&1; echo "exit=$?"
```
Read markers and `FAILED` lines separately. **Known red (plan §4):** `test_run_liveness_classifies_a_thread_join_hang_as_hung` (now `tests/core/test_cli_run.py`) fails on main (B107); acceptable only as the sole failure, logged in `W3-LOG.md`.

**Host-load rule.** Shared with a production game server: nice/ionice everything; serial pytest; one gate container at most, none while another session's gate runs; never the full `self-qualification` lane; remove containers by exact name only. Editor tools for edits. Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
Write `BLOCKED: <reason>` to `W3-LOG.md`, commit, stop if: a module fits no component; the tree violates 2a (report edges, change nothing); O3 or O5 differs; no contexts recorded; the gate is red beyond the known test. Never improvise.

## Review
Fresh-session adversarial review before merge: recompute `expected_dir` over `git ls-files tests`; plant an unlisted forbidden import and a misplaced file; reconcile the Part 1 matrix with the database.
