# REVIEW-W2: Wave A W2 (B127), `assay analyze` as its own package

- Reviewer: fresh adversarial session (not a fork).
- Worktree: `/workspaces/vbpub/.worktrees/wave-a-w2-analysis`, branch `wave-a-w2-analysis` at `e81dce9c`.
- Diff reviewed: `4c53f28c..wave-a-w2-analysis`, commits `1cd16baf` and `4f2fd892` (code), `5e763cb2` and `d816dbf4` (docs), `e81dce9c` (log).
- Paths below are relative to `assay/` unless stated otherwise.
- Nothing in the repository was edited or committed. The registered gate was not run (CD44).
- Scratch work: `scratchpad/w2review/`. The temporary worktrees at `4c53f28c` and `e81dce9c` were created there and removed afterwards (`git worktree list` shows neither).

## What I ran (all under `nice -n 19 ionice -c3`)

| Check | Result |
|---|---|
| Collect-only, default run at HEAD | 6004 |
| Collect-only, `tests analysis/tests` | 6004 |
| Collect-only, `analysis/tests tests` | 6004 |
| Collect-only, `tests` alone | 5783 |
| Collect-only, `analysis/tests` alone | 221 |
| Collect-only, `-p no:randomly` | 6004 (pytest-randomly is not installed, so the flag does nothing) |
| Collect-only, one judge file alone and mixed with analysis files | OK |
| Collect-only at base `4c53f28c` (temporary worktree) | 5975 |
| Id diff, base vs HEAD, after mapping `analysis/tests/` to `tests/` | Exactly the 214 moved ids. One docs-param id renamed (`README.md:849` to `:855`, a line shift). 29 new tests (22 judge, 7 analysis). Nothing lost. |
| `pytest --import-mode=append --co` (both trees) | base: 5975. HEAD: `ImportPathMismatchError` on `conftest` (see W2R-1). |
| Wheel and sdist, `python -m build --no-isolation -x` on a scratch worktree | See "Packaging" below |
| Zipapp, built with `build_release.build_zipapp` from that wheel | Contains `assay/`, `assay_analysis/`, dist-info and `__main__.py`. Under `python3 -S`: `analyze --help` exits 0 with the headline; `report … /nonexistent` exits 2 with an `evidence_error` JSON; `analyze record … -- sh -c '…' sh --flag -x` keeps the argv verbatim and exits 3. |
| Editable install (PEP 660) of HEAD in a fresh venv | `assay analyze --help` works. `assay_analysis` resolves through the editable finder. |
| `assay run analysis` from source on a scratch worktree at `e81dce9c` (a lane run, not the registered gate; no provenance) | PASS. R0 PASS, R1 930/930 lines, 16 s. |
| `tools/b105_report_check.py` on that analysis verdict | `REJECTED=judgment.resolved.source_roots ['analysis/src/assay_analysis'] != ['src/assay']`, exit 2. A checker copy outside `--repo-root` is refused too. |
| Focused files: `analysis/tests` plus the T4–T10 files, serial | 367 passed |
| CLI output compared byte for byte, base vs HEAD from source, 14 invocations | 13 identical. One differs (W2R-4). |
| `pyflakes` over `analysis/`, `src/assay/cli.py`, `tools/b105_report_check.py` and the changed tests | clean |
| `git diff --check 4c53f28c..wave-a-w2-analysis` | clean |

## Conformance: verified OK

- **CD15.**
  - `pyproject.toml:105-106`: `pythonpath = ["src", "analysis/src"]`, `testpaths = ["tests", "analysis/tests"]`.
  - `:47-51`: `package-dir = {"" = "src"}`, `where = ["src", "analysis/src"]`. setuptools maps `assay_analysis` from `where`, and the built wheel confirms it.
  - The egg-info lands in `src/assay.egg-info`, which is ignored.
- **CD13.**
  - The FIFO test (`analysis/tests/test_analysis.py:723-746`) uses exactly `[sys.executable, "-c", "import sys; from assay.cli import main; raise SystemExit(main(sys.argv[1:]))", "analyze", *arguments]`.
  - `PYTHONPATH` is set to the absolute `src` and `analysis/src`.
  - There is no `standalone` fixture and no `assay/__main__.py`.
- **CD16.** The schemas stay in `src/assay/schemas/`. The wheel ships `assay/schemas/analysis-{archive,receipt,report}.schema.json`.
- **CD17.** No R2 lane.
- **CD18.** `ALLOWED_PRIVATE_JUDGE_NAMES: dict[str, str] = {}` exists and is empty (`analysis/tests/test_analysis_package_boundary.py:20`).
- **CD19.**
  - `src/assay/analysis.py` is gone, and nothing under `src/`, `tools/`, `assay.toml` or the B105 target lists names it.
  - The only remaining mentions are the removal notes in CONSUMERS, DESIGN-GUIDE and CHANGES, the T8 negative, and historical `nyxloom-trove/` reports.
- **CD31.** The loader module name is `assay_judge_conftest`.
- **CD45.** There are no `!!` entries under `analysis/`.
- **Packaging.**
  - The wheel's top level is `assay` (56 files) + `assay_analysis` (`__init__`, `cli`, `evidence`) + dist-info.
  - `top_level.txt` is `assay`, `assay_analysis`, and `entry_points.txt` is `assay = assay.cli:main`.
  - The wheel has no `assay/analysis.py` and nothing from `tests/` or `analysis/tests`.
  - The sdist includes `analysis/tests/`. It already included `tests/` before W2, because the setuptools-scm file finder ships every tracked file, and the sdist is not a release artifact (`cmru.toml` `artifacts = ["wheel"]`). So this is not a W2 finding.
- **Seam.**
  - A fresh interpreter's `import assay.cli` loads no `assay_analysis` module (checked with `-X importtime` and `sys.modules`).
  - `_run_analyze` is the only importer. T6 (`tests/core/test_import_contracts.py:348-382`) refuses a module-level import or an import in another function, and its tainted negative works.
  - `raw[:1] == ["analyze"]` bypasses `_split_appended_argv`, so `record -- cmd --flag` is preserved (checked end to end through the zipapp).
- **B105 checker.**
  - Refusals (a)–(e) match the brief and D3.
  - A verdict whose `source_roots` is the analysis root can never pass as a judge report.
  - Both B105 lanes' `targets` equal the 50 tracked `src/assay/**/*.py` at HEAD.
  - The ACCEPTED suffix is exact.
- **Gate script.** `run_analysis_lane` is verbatim from the brief and is called directly after `run_self_hosted_lane`. The lint scope adds `analysis/**/*.py` and dies when it finds none.
- **Moved tests.** Both files differ from base only in the edits step 10 names: imports, `JUDGE_VERDICT_FIXTURES` and `PROJECT_ROOT`, the FIFO argv, `build_parser` on `[2:]`, and `choices`. No assertion changed.
- **New tests T2–T10.** No clock, no sleep, no elapsed-time assertion. Globals change only through `monkeypatch` (`setitem(sys.modules, …)` and `chdir`, both restored).
- **D2** is sound: `standalone` must copy `analysis/src`, or the wheel it builds lacks `assay_analysis`.
  - Three other recipes still copy `src/` only: `tests/test_standalone.py:1943`, `tests/test_self_hosting.py:288` and `tests/test_distribution_gate.py:107`.
  - setuptools silently skips a missing `where` directory, so they build judge-only wheels. None of them runs `analyze`, so this is harmless.
- **D5.** Both heredoc-appended files (`tests/core/test_import_contracts.py`, `tests/test_b105_report_check.py`) end with a single `\n`. They have no stray `EOF` or `cat >>` text, no tabs, no CRLF and no duplicated top-level definitions.
- **Mutation surface.** The new judge lines have no surviving-mutant shape under the Python operators (compare-swap, boolop-swap, bool-const-flip, falsy-swap):
  - `raw[:1] == ["analyze"]` is killed by T5.
  - The `return` statements of `_run_analyze` and `main` are not falsy constants.

## Findings

### W2R-1: MAJOR. The analysis conftest's `__getattr__` proxy makes judge tests' `from conftest import …` depend on collection order

**Where:** `analysis/tests/conftest.py:23-46` (D1). The trigger is `pyproject.toml:106` (the CD15 `testpaths` order puts `analysis/tests` last).

**Evidence.** I used a probe plugin (`scratchpad/w2review/plug/w2probe.py`) that inspects state at `pytest_collection_finish`.

- With `tests/core/test_cli_run.py analysis/tests`, which is also the default `testpaths` order:
  - `sys.modules["conftest"]` is `analysis/tests/conftest.py`.
  - `test_cli_run.GitRepo.__module__ == "assay_judge_conftest"`, and `test_cli_run.GitRepo is <registered judge conftest>.GitRepo` is **False**.
- With `analysis/tests tests/core/test_cli_run.py`: the module is `conftest` and the identity check is **True**.
- Consequences of the default order:
  - Every judge test module collected with `analysis/tests` last binds its conftest names to a **second execution** of `tests/conftest.py`. That copy is not the plugin pytest registered and whose fixtures it serves.
  - That copy is also loaded with `spec_from_file_location`, so pytest does not rewrite its asserts.
- **Concrete failure, demonstrated in a scratch copy of the tree.** The judge test is `from conftest import GitRepo` + `def test_x(git_repo): assert isinstance(git_repo, GitRepo)`.

  | Invocation | Result |
  |---|---|
  | alone | passes |
  | `analysis/tests/… tests/core/…` | passes |
  | `tests/core/… analysis/tests/…` | **fails** (`AssertionError: assert False`) |
  | default `pytest -k zz_identity` | **fails** |

  This is exactly the §3b check the brief asks for: "could it flip on … another order?"
- **Import mode.** `pytest --co --import-mode=append` (both trees) now raises `ImportPathMismatchError('conftest', …/tests/conftest.py, …/analysis/tests/conftest.py)`. The same command collects 5975 at base.
- **The docstring's claim is wrong.** The docstring says pytest sees none of the judge names. But pytest's conftest-variable lookup uses `getattr`, not `dir()`, so the proxy leaks them:
  - `config._getconftest_pathlist("collect_ignore_glob", path=analysis/tests)` returns the judge's two globs, re-rooted under `analysis/tests/`.
  - `hasattr(<analysis conftest>, "pytest_sessionfinish")` is True. pluggy still registers the hook only once, which I checked.
- The proxy is also eager: `_judge = _load_judge_conftest()` at `:40` runs the whole judge conftest (jsonschema, `assay.config`, a `git rev-parse`) in every analysis-lane run. It does this although no analysis test uses a judge fixture (D1 says so itself).
- If `exec_module` raises, `_load_judge_conftest` leaves a half-initialised module in `sys.modules["assay_judge_conftest"]`, and W4's reuse would then get that broken module.
- **W4 (CD14/CD31) is not broken, but it inherits the hazard.** A `gate/tests` module that does `from conftest import …` would resolve through this proxy in one order and not in another.

**Simpler correct design, verified in a scratch copy.**

- Make `analysis/` and `analysis/tests/` packages. pytest then imports the conftest as `analysis.tests.conftest` and never rebinds `sys.modules["conftest"]`, so the proxy is unnecessary.
- The loader keeps the CD31 name `assay_judge_conftest` and loads on demand.
- Results with the fix:
  - The identity demo passes in all three orders and under default `-k`.
  - `--import-mode=append --co` collects.
  - `analysis/tests` passes (222 with the guard test), with 100% line and branch coverage over `analysis/src/assay_analysis`.
  - The default run collects 6005 (6004 + the guard test).
  - `pyflakes analysis` is clean.
  - The wheel contents are unchanged: `analysis/__init__.py` is outside `where`.

**Exact fix:**

1. Create `analysis/__init__.py` with exactly this content:
   ```python
   """Test-collection package root for analysis/tests (A-478); it holds no shipped code."""
   ```
2. Create `analysis/tests/__init__.py` with exactly this content:
   ```python
   """The analysis tests, imported as ``analysis.tests.*`` so their conftest never rebinds ``sys.modules["conftest"]`` (W2R-1)."""
   ```
3. Replace the whole content of `analysis/tests/conftest.py` with:
   ```python
   """Shared-fixture loader for the analysis tests (CD13, CD31).

   ``analysis/`` and ``analysis/tests/`` are packages, so pytest imports this file
   as ``analysis.tests.conftest`` and never rebinds ``sys.modules["conftest"]``:
   that name stays the judge's ``tests/conftest.py`` in every collection order, so
   a judge test's ``from conftest import ...`` always reaches the conftest pytest
   registered. The judge conftest is loaded only on demand, under the unique module
   name ``assay_judge_conftest`` (never ``conftest``); no analysis test needs a
   judge fixture today. ``gate/tests/support.py`` (W4) reuses that module name.
   """

   from __future__ import annotations

   import importlib.util
   import sys

   from analysis.tests.analysis_support import PROJECT_ROOT

   _NAME = "assay_judge_conftest"


   def load_judge_conftest():
       """The judge's ``tests/conftest.py`` as ``assay_judge_conftest``, loaded once."""
       loaded = sys.modules.get(_NAME)
       if loaded is not None:
           return loaded
       path = PROJECT_ROOT / "tests" / "conftest.py"
       spec = importlib.util.spec_from_file_location(_NAME, path)
       module = importlib.util.module_from_spec(spec)
       sys.modules[_NAME] = module
       try:
           spec.loader.exec_module(module)
       except BaseException:
           del sys.modules[_NAME]
           raise
       return module
   ```
4. In `analysis/tests/test_analysis.py:21` and `analysis/tests/test_analysis_package_boundary.py:16`, replace `from analysis_support import` with `from analysis.tests.analysis_support import`. Leave the rest of each line unchanged.
5. Add a regression guard to `analysis/tests/test_analysis_package_boundary.py`. It fails at `e81dce9c` in both the analysis-only and the combined run, and passes after steps 1–4.
   - Add `import sys` directly after `import ast` (line 11).
   - Append:
     ```python


     def test_the_analysis_conftest_never_rebinds_the_judge_conftest_name():
         """(W2R-1) pytest imports this tree's conftest as a package module, so a judge
         test's ``from conftest import ...`` always reaches the judge's conftest."""
         assert "analysis.tests.conftest" in sys.modules
         bound = sys.modules.get("conftest")
         assert bound is None or Path(bound.__file__).resolve() == PROJECT_ROOT / "tests" / "conftest.py"
     ```
6. In `nyxloom-trove/reports/wave-a/W2-LOG.md`, replace the whole D1 bullet with:
   ```
   - D1 `analysis/tests/conftest.py` (revised by review W2R-1): `analysis/` and `analysis/tests/` are packages, so pytest imports the analysis conftest as `analysis.tests.conftest` and `sys.modules["conftest"]` stays the judge's `tests/conftest.py` in every collection order. The judge conftest is loaded only on demand (`load_judge_conftest()`) under `assay_judge_conftest`; no analysis test needs a judge fixture. The first version's module `__getattr__` proxy bound judge tests' `from conftest import ...` to a second, unregistered copy of `tests/conftest.py` whenever `analysis/tests` was collected last (the default `testpaths` order), and broke `--import-mode=append`.
   ```
7. Check that `git status --short --ignored analysis` shows no `!!` for the two new files. Also run `pytest --co -q` for both orders: each must print `6005 tests collected`.

Carver note, no fixer action: `gate/tests/support.py` (W4) cannot import `load_judge_conftest` unless `assay/` is on `sys.path`. W4 should reuse the `assay_judge_conftest` name and the same `sys.modules` guard rather than import the function.

### W2R-2: MINOR. T8's zipapp `analyze` test passes for a zipapp that lacks `assay_analysis` when the interpreter has it installed (the gate's case)

**Where:** `tests/test_distribution_build_release.py:534-551`.

**Evidence.**
- The test runs `[sys.executable, zipapp, "analyze", …]`. In the tester-unified gate, `sys.executable` is the run-venv Python, which has the just-built wheel, and so `assay_analysis`, in site-packages.
- I made `stripped.pyz`: the real zipapp with every `assay_analysis/` member removed.
  - Under a venv that has `assay_analysis` installed: `analyze --help` exits 0 with the headline, and `report … /nonexistent` exits 2. Both assertions pass.
  - Under `python3 -S`: it fails with `ModuleNotFoundError`.
- The neighbouring schema test (`:461-481`) already asserts that the module's origin is inside the archive. This test does not.

**Exact fix.** Replace the body of `test_the_zipapp_runs_analyze_from_the_one_archive`, from the line after its docstring to the end of the function, with:
```python
    artifacts = built["first"]

    # `-S`: no site-packages, so an `assay_analysis` installed in the running
    # interpreter (the gate's run venv) cannot stand in for a missing one.
    helped = subprocess.run(
        [sys.executable, "-S", str(artifacts.zipapp), "analyze", "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert helped.returncode == 0, helped.stderr
    assert helped.stdout.splitlines()[0] == f"ASSAY {artifacts.version} — declared-lane judge"

    refused = subprocess.run(
        [sys.executable, "-S", str(artifacts.zipapp), "analyze", "report",
         "--expected-commit", "a" * 40, "--verdict", "lane", "/nonexistent"],
        capture_output=True, text=True, timeout=120,
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert json.loads(refused.stdout)["lanes"][0]["status"] == "evidence_error"

    origin = subprocess.run(
        [sys.executable, "-S", "-c", "import assay_analysis; print(assay_analysis.__file__)"],
        env={"PYTHONPATH": str(artifacts.zipapp), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=120,
    )
    assert origin.returncode == 0, origin.stderr
    assert str(artifacts.zipapp) in origin.stdout, origin.stdout
```
I verified `python -S` on both archives: the real zipapp passes all three checks, and `stripped.pyz` fails.

### W2R-3: MINOR. Nothing classifies packages under `analysis/src/`, and T3 keys sources by basename, so files can shadow each other

**Where:**
- `analysis/tests/test_analysis_package_boundary.py:110-115`, `:124` and `:165-172`.
- `tests/test_dependency_purity.py:151-155`.
- `tools/b105_report_check.py:15-16`.

**Evidence.**
- `where = ["src", "analysis/src"]` ships every package found under `analysis/src/`. The checker's (b) classifies `src/` only, and T7 scans `analysis/src/assay_analysis` only. The checker comment at `:16` still claims "A tracked package that is neither B105's root nor named here is refused", which is false for `analysis/src/`.
- `_analysis_sources` keys files by `path.name`. A later file with the same basename silently replaces an earlier one in the scan, and the `set(sources)` guard still sees `{"__init__.py", "cli.py", "evidence.py"}`.
- Demonstrated in a scratch copy:
  - A planted `analysis/src/assay_analysis/campaign/cli.py` containing `from assay.mutation import _x` passes `test_analysis_reaches_no_private_judge_name`, because the real `cli.py` sorts after it and overwrites it.
  - A planted `analysis/src/other/__init__.py` containing `import requests`, which violates A-005 and would ship in the wheel, passes T3 (all 5 tests), T7 and the lane test.

**Exact fix** (the changes to `analysis/tests/test_analysis_package_boundary.py` were verified together with W2R-8: 7 passed at HEAD, and both plants turn red):

1. Replace `_analysis_sources` (`:110-115`) with:
   ```python
   def _analysis_sources() -> dict[str, str]:
       """Every analysis source, keyed by its path below ``analysis/src`` (never by
       basename: two files named ``cli.py`` must both be scanned)."""
       return {
           path.relative_to(ANALYSIS_SRC).as_posix(): path.read_text(encoding="utf-8")
           for path in sorted(ANALYSIS_SRC.rglob("*.py"))
           if "__pycache__" not in path.parts
       }
   ```
2. Replace line `:124` (`assert set(sources) == {"__init__.py", "cli.py", "evidence.py"}  # guard the guard`) with:
   ```python
       assert set(sources) == {  # guard the guard
           "assay_analysis/__init__.py",
           "assay_analysis/cli.py",
           "assay_analysis/evidence.py",
       }
   ```
3. In `test_the_analysis_lane_is_a_whole_target_r0_r1_lane_over_every_analysis_source`, replace the `discovered = sorted(...)` statement (`:167-170`) with:
   ```python
       packages = {
           path.name
           for path in ANALYSIS_SRC.iterdir()
           if path.is_dir() and not path.name.endswith(".egg-info") and path.name != "__pycache__"
       }
       assert packages == {"assay_analysis"}  # the wheel ships every package under analysis/src
       discovered = sorted(
           path.relative_to(PROJECT_ROOT).as_posix()
           for path in ANALYSIS_SRC.rglob("*.py")
           if "__pycache__" not in path.parts
       )
   ```
4. In `tools/b105_report_check.py`, replace the two comment lines `:15-16` (keep `:14` `B105_SOURCE_ROOT = ...`) with:
   ```python
   #: Source trees that are deliberately outside B105, each with the decision that put
   #: it there. A tracked top-level entry under ``src/`` other than B105's root is
   #: refused; the packages under ``analysis/src/`` are pinned by the analysis tests.
   ```

### W2R-4: MINOR. O1's "byte-identical" claim is overstated: usage errors for unrecognized arguments changed

**Where:**
- `src/assay/cli.py:476-477` (the cause, and it is intended).
- `nyxloom-trove/reports/wave-a/W2-LOG.md:13` and `:56`.
- `CHANGES.md:8-13`.

**Evidence.** Running from source, base vs HEAD: `assay analyze collect --output x -- y` exits 2 in both. The stderr differs:

- Base prints `usage: assay [-h] [--version] {analyze,lanes,run,plan,verify} ...` and `assay: error: unrecognized arguments: -- y`.
- HEAD prints `usage: assay analyze [-h]\n {collect,…} ...` and `assay analyze: error: unrecognized arguments: -- y`.

The other 13 invocations are byte-identical, including every `--help`, bare `analyze`, `analyze bogus`, `analyze --version` and the argument-type errors. The change is not a documented surface (CD30), but the LOG and CHANGES claim that nothing changed.

**Exact fix:**

1. In `W2-LOG.md`, add under "## Deviations (successor)":
   ```
   - D8 (review W2R-4) O1 covered help, bare and exit-code paths. One stderr path is not byte-identical: a usage error for unrecognized arguments (e.g. `assay analyze collect --output x -- y`) now prints the `assay analyze` usage line and `assay analyze: error:` prefix instead of the top-level `assay` ones, because the analysis parser is now the top-level parser for `analyze`. Exit code 2 is unchanged; no documented surface changes (CD30).
   ```
2. In `W2-LOG.md:56`, replace `` `assay analyze` help/exit codes byte-identical (O1)`` with `` `assay analyze` help output and exit codes byte-identical (O1); unrecognized-argument usage errors now name `assay analyze` (D8)``.
3. Replace the `CHANGES.md` bullet at `:8-13` with:
   ```
   - refactor(assay): move `assay analyze` into a second top-level package,
     `assay_analysis`, shipped in the same wheel and zipapp; `assay analyze`
     behaviour, exit codes and schema paths are unchanged (a usage error for
     unrecognized arguments now prints the `assay analyze` usage line and prefix),
     the undocumented `import assay.analysis` is removed, an editable install
     made before this change must be re-run so `assay analyze` finds the new
     package, the package has its own R0+R1 lane, and the B105 report checker
     now names its scope (`src/assay` only; analysis out of scope by A-478)
     (B127)
   ```

### W2R-5: MINOR. A pre-W2 editable install breaks `assay analyze` with a traceback and exit 1, which `report` uses for a failing lane

**Where:** `docs/INTERNAL-CONSUMERS.md:66-67` and the seam at `src/assay/cli.py:455-459`.

**Evidence.**
- This devcontainer's own `/home/vscode/.venv` holds `__editable__.assay-7.1.1.dev188+gba88d673.pth`. It is a static path to `…/nyxloom-session-cli-acceptance/assay/src` only.
- I reproduced that shape: a `.pth` naming only HEAD's `src`. With it, `assay analyze report --expected-commit a…a --verdict l /nonexistent` fails with `ModuleNotFoundError: No module named 'assay_analysis'` and **exit 1**.
- `_REPORT_EXITS` maps `fail` to 1 (`analysis/src/assay_analysis/evidence.py:360`), so a script that reads only the exit code sees "a lane failed". `assay lanes` still works.
- A fresh editable install of W2 works: setuptools emits a finder that maps both packages. So only installs made before this change and not re-run are affected, and B100 tells operators to use exactly such an install ("The same worktree-installed CLI includes `assay analyze report`").
- T5(ii) deliberately pins the `ModuleNotFoundError`, so the fix belongs in the docs, not the seam.

**Exact fix:**

1. In `docs/INTERNAL-CONSUMERS.md`, replace
   ```
   The same worktree-installed CLI includes `assay analyze report` (its code is
   the separate `assay_analysis` package in the same wheel). Use it when a
   ```
   with
   ```
   The same worktree-installed CLI includes `assay analyze report` (its code is
   the separate `assay_analysis` package in the same wheel). An editable install
   made before B127 maps only `src/`: re-run `pip install --no-deps
   --no-build-isolation --editable assay` once, or `assay analyze` fails with
   `ModuleNotFoundError: No module named 'assay_analysis'` and exit 1, the code
   `report` otherwise uses for a failing lane. Use it when a
   ```
2. The CHANGES text for this is in W2R-4 step 3.

### W2R-6: MINOR. Doc and comment drift

**Where and exact fix:**

1. `docs/DESIGN-GUIDE.md:3602-3604` says `verify_scope` refuses a report "whose R1/R2/R3 targets are not the tracked `src/assay` sources". The code checks R3 targets only for being under `src/assay/` (`tools/b105_report_check.py:72-73`). Replace
   ```
     refuses a report whose source roots are not exactly `src/assay`, whose
     R1/R2/R3 targets are not the tracked `src/assay` sources, or that names an
     analysis target; an accepted report says
   ```
   with
   ```
     refuses a report whose source roots are not exactly `src/assay`, whose
     R1 (and R2) targets are not exactly the tracked `src/assay` sources, whose
     R3 targets lie outside `src/assay/`, or that names an analysis target; an
     accepted report says
   ```
2. `assay.toml:31` still documents the old pin. Replace `` # `[tool.pytest.ini_options] pythonpath = ["src"]` (a developer convenience,`` with `` # `[tool.pytest.ini_options] pythonpath = ["src", "analysis/src"]` (a developer convenience,``.
   - `nyxloom-trove/nyxloom.toml:107` has the same stale value, but the brief forbids touching that file. Leave it and mention it in the LOG.
3. `nyxloom-trove/4-backlog.md:11650` marks B127 "DONE" before the gate, the review and the merge. Every other DONE line in the file cites a gate PASS or release. Replace that line with:
   ```
   **Status: IMPLEMENTED on branch `wave-a-w2-analysis` (2026-09-29, A-478; report `reports/wave-a/W2-LOG.md`); DONE after the controller's registered tester-unified PASS, the fresh review and the merge. Analysis R2 is B131.**
   ```

### W2R-7: MINOR. T10's "no analysis tree" test would pass for any failure

**Where:** `tests/test_distribution_gate.py:954-967`.

**Evidence.** `test_a_clone_with_no_analysis_tree_refuses_rather_than_linting_nothing` asserts only `returncode != 0` and the absence of the marker. It would also pass if pyflakes crashed on a missing path, or if any earlier line of `run_lint_phase` failed, so it does not prove the new guard (`tools/tester-unified-gate.sh:151-152`).

**Exact fix.** After line `:966` (`assert proc.returncode != 0`), insert:
```python
    assert "lint phase found no analysis sources to lint" in proc.stdout + proc.stderr
```

### W2R-8: MINOR. The analysis→judge dependency surface is no longer pinned

**Where:**
- The deleted W3 `ANALYSIS_DEPS` (`tests/core/test_import_contracts.py`, removed in `1cd16baf`).
- `analysis/src/assay_analysis/cli.py:11`.
- `analysis/tests/test_analysis_package_boundary.py`.

**Evidence.**
- W3 restricted the analysis component to `{"assay", "assay.errors", "assay.git", "assay.mutation", "assay.verify"}`. T6.1 deleted that restriction, as the brief required, and nothing replaced it. T3 checks only underscore names.
- Analysis now also imports the composition root, `assay.cli`, for `AssayArgumentParser`. The brief mandates that edge, but no test would notice analysis importing `assay.runner` or any other judge module.
- Two names that analysis uses are unprefixed but missing from their module's `__all__`: `assay.git.ignore_rule_source` and `assay.cli.AssayArgumentParser`. The DESIGN-GUIDE says analysis "imports `assay` public names".

**Exact fix:**

1. In `analysis/tests/test_analysis_package_boundary.py`, directly after `_analysis_sources` (as rewritten by W2R-3), insert:
   ```python


   #: (A-478) The judge modules the analysis package may import. W3's
   #: ``ANALYSIS_DEPS`` pinned this set while analysis lived in ``src/assay``;
   #: adding a module here is a reviewed change.
   ALLOWED_JUDGE_MODULES = frozenset(
       {"assay", "assay.cli", "assay.errors", "assay.git", "assay.mutation", "assay.verify"}
   )


   def judge_modules_imported(sources: dict[str, str]) -> set[str]:
       """Every judge module (``assay`` or ``assay.<module>``) the sources import."""
       found: set[str] = set()
       for text in sources.values():
           for node in ast.walk(ast.parse(text)):
               if isinstance(node, ast.Import):
                   found.update(
                       alias.name
                       for alias in node.names
                       if alias.name == "assay" or alias.name.startswith("assay.")
                   )
               elif isinstance(node, ast.ImportFrom) and node.level == 0:
                   module = node.module or ""
                   if module == "assay" or module.startswith("assay."):
                       found.add(module)
                       found.update(
                           f"{module}.{alias.name}"
                           for alias in node.names
                           if _is_judge_module(f"{module}.{alias.name}")
                       )
       return found
   ```
2. Append to the same file:
   ```python


   def test_analysis_imports_only_the_allowed_judge_modules():
       assert judge_modules_imported(_analysis_sources()) == ALLOWED_JUDGE_MODULES


   def test_the_judge_module_checker_sees_a_new_judge_dependency():
       assert judge_modules_imported({"x.py": "from assay.runner import run\n"}) == {"assay.runner"}
       assert judge_modules_imported({"x.py": "import assay.config as c\n"}) == {"assay.config"}
       assert judge_modules_imported({"x.py": "from assay import cli\n"}) == {"assay", "assay.cli"}
       assert judge_modules_imported({"x.py": "import json\nfrom assay_analysis import evidence\n"}) == set()
   ```
3. In `W2-LOG.md` under "## Deviations (successor)", add:
   ```
   - D9 (review W2R-8) The analysis->judge module set is pinned in T3 (`ALLOWED_JUDGE_MODULES`), replacing W3's deleted `ANALYSIS_DEPS`; it now includes the composition root `assay.cli` (brief step 3). Carver question: analysis uses two unprefixed judge names absent from their module's `__all__` (`assay.git.ignore_rule_source`, `assay.cli.AssayArgumentParser`). T3 treats "public" as "no leading underscore"; if CD18's "public judge name" means `__all__` membership, adding them touches `git.py`, a judge module W2 may not edit.
   ```

## Answers to the specific D1 questions

- **Order dependence: yes.**
  - Every order collects the same 6004 ids.
  - Which conftest object judge tests bind to depends on which tree comes last. The default `testpaths` order, `tests analysis/tests`, is the bad one. The demonstration is in W2R-1.
  - A judge file run by itself, or `analysis/tests tests`, binds to the real conftest.
  - `-p no:randomly` does nothing here, because pytest-randomly is not installed.
- **Double registration or hidden fixture: no.** pluggy registers `pytest_sessionfinish` once, from `tests/conftest.py`. The analysis conftest defines no fixtures, so none is hidden. Conftest variables such as `collect_ignore_glob` do leak through the proxy.
- **W4 (CD14/CD31): not broken.** The `sys.modules` guard makes a shared `assay_judge_conftest` possible. But a `gate/tests` module doing `from conftest import …` would inherit the same order dependence. W2R-1's fix removes that coupling.
- **Simpler correct design: yes.** It is W2R-1's fix: package-qualified analysis tests plus an on-demand loader under the CD31 name. I verified it end to end in a scratch copy.

## Combined-axis attacks

1. **D1 proxy × CD15 `testpaths` order.** A legitimate judge test fails only in the default run. This is W2R-1, demonstrated.
2. **Zipapp × gate run venv.** A zipapp missing `assay_analysis` passes T8 because site-packages supplies the package. This is W2R-2, demonstrated.
3. **Lazy seam × pre-W2 editable install (B100's documented way to read gate snapshots).** `assay analyze report` crashes with exit 1, the same code as a failing lane. This is W2R-5, demonstrated.
4. **`pythonpath` pin × in-container installed wheel: no failure.**
   - tester-unified clears `pythonpath` (`--override-ini=pythonpath=`), so both packages come from the wheel.
   - The B105 lanes use `pythonpath=src`, so `assay_analysis` could come only from the installed wheel, but no judge test imports it for real. T5 uses a fake module and `None`.
   - The analysis lane uses `src` and `analysis/src` from the snapshot, which is what its path-based `--cov` needs. It passed locally at 930/930.

## Counts

| Severity | Count | Findings |
|---|---|---|
| BLOCKER | 0 | |
| MAJOR | 1 | W2R-1 |
| MINOR | 7 | W2R-2 to W2R-8 |

## Verdict: MERGE-WITH-FIXES

The code seam, the packaging, the B105 scope check, the moved tests and the analysis lane are correct:

- The one wheel and zipapp are verified.
- The seam is lazy and one-way.
- The checker refusals are exact.
- The 214 moved tests have no assertion changes.
- The analysis lane PASSes R0+R1 at 930/930.

W2R-1 must be fixed before merge: the conftest proxy makes judge-test semantics depend on collection order in the default run. The MINOR fixes are mechanical, and the text above is ready to apply. After the fixes:

1. Re-run collect-only in both orders and expect 6005.
2. Run the focused files.
3. Then the controller runs the registered gate (CD44).
