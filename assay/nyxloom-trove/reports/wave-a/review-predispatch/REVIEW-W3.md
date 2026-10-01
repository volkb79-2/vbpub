# REVIEW-W3: Wave A W3 (B130), component boundaries

- **Reviewer:** fresh adversarial session (Opus), 2026-09-29.
- **Diff reviewed:** `git diff -M 3daf62a7..wave-a-w3-boundaries` (`be0357de`, `f2c249fe`, `ea990a53`). Worktree `/workspaces/vbpub/.worktrees/wave-a-w3-boundaries`.
- **Binding inputs:** brief `W3-component-boundaries.md`, `CARVER-DECISIONS.md` (CD10, CD23 + amendment, CD27).
- **Method:** read-only on the repo. Scratch work is under `.../scratchpad/w3review/`. I ran collect-only at base and at branch head, the latter both in the implementer's worktree and in a clean detached checkout of `ea990a53`. The scratch worktree was removed afterwards. I also ran a mechanical purity check over all 221 renames, drove the contract functions with synthetic sources, and planted misplaced files and forbidden imports in a plain scratch copy of `src/` and `tests/`, never a git worktree. Part 1's scripts were rerun on the preserved baseline database `/tmp/w3-0OY6`. No gate and no containers were run; everything was niced.

**Headline:** the package's central deliverable, `tests/core/test_import_contracts.py`, **is not in the branch.** The vbpub root `.gitignore` ignores every path named `core`, so the new file was never added. Commit `f2c249fe` says it adds the file, but it does not. Every oracle in the log that involves the contract test (O1, O2, O3 and O5's +7) was measured in a working tree that differs from the commit. The registered gate builds from an exact-OID clone of HEAD, so it would run green without the test. Everything else (the 221 moves, the path rewrites, the allowance maps, the layout rule and Part 1) checks out.

---

## Findings

### W3R-1: BLOCKER. The import-contract test is not committed: the root `.gitignore` pattern `core` hides `tests/core/`

**Where:**
- `/workspaces/vbpub/.gitignore:352` (`core`, under "cleanup hardening (2026-06-18)").
- Commit `f2c249fe`.
- `assay/tests/core/test_import_contracts.py`, which is untracked and ignored in the W3 worktree.

**Evidence:**
- `git check-ignore -v assay/tests/core/test_import_contracts.py` prints `.gitignore:352:core	assay/tests/core/test_import_contracts.py`.
- `git status --short --ignored assay/tests` lists `!! assay/tests/core/test_import_contracts.py`. Plain `git status` is clean, so nothing warned the implementer.
- `git show --name-status --format= f2c249fe`: the only `A` line is `assay/nyxloom-trove/reports/wave-a/W3-test-moves.tsv`. The commit message (line 3) says "Adds tests/core/test_import_contracts.py". `git ls-tree ea990a53 assay/tests/core/` has no `test_import_contracts.py`.
- Collect-only on a clean detached checkout of `ea990a53` gives **5965 tests**, with 0 `test_import_contracts` ids. That is the base count; the only id difference is the DESIGN-GUIDE line shift. The log's O3 figure "after 5972 = +7" (`W3-LOG.md:21`) holds only in the implementer's dirty worktree.
- The 156 files moved into `tests/core/` are tracked because `git mv` bypasses ignore rules. Only new files there are hit. `git check-ignore --no-index` on a new `test_new_probe.py` in each of the 8 component folders: only `tests/core/` is ignored.
- **Hollow-green mechanism (combined axis: ignore rule × exact-OID materialization × clean-tree check):**
  - The registered gate "builds from an exact-OID clone of HEAD" (`assay/run-gate.toml:26-29`).
  - assay's own snapshots materialize `HEAD^{tree}` (`src/assay/isolation.py:1050-1055`).
  - Clean-tree checks use `git status --porcelain`, which does not show ignored files.
  - So the gate, the self-qualification snapshots and every clean clone run 5965 tests with no contract test, while the developer worktree runs 5972. Nothing reports the difference.
- `README.md:1031` and `docs/DESIGN-GUIDE.md:2970` point at a file that is not in the tree.
- **Forward hazard:** W2 T6 extends this file, and W4 and W10 add tests under `tests/core/`. Each new file there is silently dropped by `git add -A`/`git add .`, with no `git status` signal.

**Fix (exact; apply in `/workspaces/vbpub/.worktrees/wave-a-w3-boundaries`):**

1. Append these lines to the end of `assay/.gitignore`. A lower-level `.gitignore` overrides the root pattern. I verified in a throwaway repo that this re-includes `tests/core/test_import_contracts.py` while `tests/core/__pycache__/`, `*.pyc` and a core-dump file named `core` stay ignored.
   ```
   # B130/W3: the vbpub root .gitignore ignores every path named `core` (core
   # dumps, root .gitignore:352), which hides the judge-test folder tests/core/
   # and every new file in it. Re-include that one directory.
   !/tests/core/
   ```
2. Run `git check-ignore -v --no-index assay/tests/core/test_import_contracts.py; echo "exit=$?"`. It must print only `exit=1` (not ignored).
3. Run `sha256sum assay/tests/core/test_import_contracts.py`. It must print `20891aae2c456794037543010ddc4ea85920427545ba773c24aed5daabf81815`, the content this review read. If it differs, stop and report.
4. `git add assay/.gitignore assay/tests/core/test_import_contracts.py`, then `git commit --only -m "<msg>" -- assay/.gitignore assay/tests/core/test_import_contracts.py`. Use this message:
   ```
   test(assay): commit W3's import-contract test that the root .gitignore hid (B130, W3R-1)

   f2c249fe's message says it adds tests/core/test_import_contracts.py, but the
   vbpub root .gitignore pattern `core` (:352) ignores the tests/core/ folder, so
   the file was never added. assay/.gitignore now re-includes tests/core/.

   Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
   ```
5. Run `git status --porcelain --ignored assay/tests | grep -v __pycache__`. It must print nothing.
6. Run the fresh-checkout oracle, which replaces the worktree-based O3. `SCR` is any scratch dir.
   ```
   git worktree add --detach "$SCR/w3fix" HEAD
   cd "$SCR/w3fix/assay"
   nice -n 19 ionice -c3 python -m pytest --collect-only -q -p no:cacheprovider | tail -1
   nice -n 19 ionice -c3 python -m pytest tests/core/test_import_contracts.py -q -p no:cacheprovider | tail -1
   git worktree remove --force "$SCR/w3fix"
   ```
   The first `tail` must be `5972 tests collected ...` and the second `7 passed ...`.
7. Append this to `assay/nyxloom-trove/reports/wave-a/W3-LOG.md` and commit it with `--only` in the same way:
   ```
   ## Fix after review (W3R-1)
   - The vbpub root `.gitignore:352` pattern `core` ignored `tests/core/`, so `tests/core/test_import_contracts.py` was never added: `f2c249fe` did not contain it and a clean checkout collected 5965, not 5972. O1/O2/O3/O6 above were measured in the working tree, not the commit.
   - Fix: `assay/.gitignore` re-includes `/tests/core/`; the file is committed unchanged (sha256 20891aae…1815).
   - O3 re-run on a fresh detached worktree of the fixed HEAD: <paste the two tail lines>.
   - Correction to the 2c line above: 15 `pathlib` imports were removed (14 `Path`, 1 function-local `Path as _Path`), not 14.
   ```
8. The registered gate (O7) must run on the fixed HEAD, not on `ea990a53`.

---

### W3R-2: MINOR. The component map fails open: a new module under `adapters/` or a parser package silently becomes `core`

**Where:** `assay/tests/core/test_import_contracts.py:47` (`component()` falls through to `return "core"`) and `:133`.

**Evidence:**
- In a scratch copy I planted `src/assay/adapters/rust.py` containing `from assay.runner import run_lane`, and a function-level `from .adapters import rust` in `src/assay/runner.py`. Result: **7 passed**. A fifth language adapter is classified `core`, may import all of core, and core may import it. That contradicts DESIGN-GUIDE's "Core never imports an adapter" (`docs/DESIGN-GUIDE.md:2989`).
- The assertion `modules == expected` (`:133`) compares `rglob` with the same `rglob` (`_module_paths` at `:95`), so it cannot catch this.
- A deleted mapped module is not detected either. Imports are resolved syntactically, so a stale edge keeps its allowance. The runtime ImportError catches that case elsewhere, so it is lower risk.
- `importlib.import_module("assay.adapters.go")` and `__import__(...)` are also not seen. `src/assay` has no dynamic import of an assay module today, so that gap is theoretical.

**Brief:** 2a does not require any of this. `component()` is normative and returns `"core"` by default, so this is a brief gap, not an implementer error.

**Fix (exact; carver may defer to W4):** insert after `test_edge_walk_sees_type_checking_and_function_level_relative_imports` in `tests/core/test_import_contracts.py`:
```python
PARSER_PACKAGES = ("coverage_parsers", "mutation_parsers", "result_reports")


def unmapped_modules(paths: dict[str, Path]) -> list[str]:
    """Modules under adapters/ or a parser package that component() files under core by default."""
    bad = []
    for name, path in paths.items():
        rel = path.relative_to(PACKAGE_DIR)
        if rel.parts[0] == "adapters" and rel.name not in ("__init__.py", "base.py"):
            if not component(name).startswith("adapter."):
                bad.append(name)
        elif rel.parts[0] in PARSER_PACKAGES and not component(name).startswith("parsers."):
            bad.append(name)
    return sorted(bad)


def test_no_adapter_or_parser_module_falls_into_core_by_default():
    assert unmapped_modules(_module_paths()) == []
    synthetic = {"assay.adapters.rust": PACKAGE_DIR / "adapters" / "rust.py"}
    assert unmapped_modules(synthetic) == ["assay.adapters.rust"]
```
The real tree gives `[]`: all 7 adapter modules map to `adapter.*` and all 13 parser modules to `parsers.*`, per my run of `component()` over the 51 modules. Collect-only then becomes 5973, and the W3R-1 step 6 expectation moves to 5973/8 passed if this is applied in the same fix round.

---

### W3R-3: MINOR. `*_test.py` files escape the layout rule, but pytest collects them

**Where:** `assay/tests/core/test_import_contracts.py:250` (`if rel.name.startswith("test_") and rel.suffix == ".py"`).

**Evidence:**
- `pyproject.toml:98-100` sets no `python_files`, so pytest's default `test_*.py *_test.py` applies.
- In the scratch copy, `tests/escape_test.py` at the root was collected (`1 passed`), and `test_import_contracts.py` stayed green (`7 passed`).
- The tree has no `*_test.py` today (`git ls-files tests | grep _test.py$` outside fixtures is empty).

**Brief:** 2b says "Every `test_*.py`", so this was not required.

**Fix (exact):** append to the layout section:
```python
def suffix_style_test_files(rel_paths: list[Path]) -> list[str]:
    """pytest also collects `*_test.py`; the layout rule is written for `test_*.py` only."""
    return [p.as_posix() for p in rel_paths if p.suffix == ".py" and p.name.endswith("_test.py")]


def test_no_suffix_style_test_files():
    assert suffix_style_test_files(_tests_files()) == []
    assert suffix_style_test_files([Path("escape_test.py")]) == ["escape_test.py"]
```

---

### W3R-4: MINOR. Cross-folder test-module imports are not guarded. Concrete combined-axis failure: layout × pytest prepend import × per-partition lane

**Where:**
- `assay/tests/core/test_import_contracts.py:213`: `EXCEPTIONS` exists only because `test_evaluate_javascript_end_to_end.py` imports a sibling test module.
- Nothing checks that future cross-test imports stay in one folder.

**Evidence:**
- Under the default `prepend` import mode, `from test_x import …` resolves only once `test_x`'s folder is on `sys.path`, which pytest inserts when it collects that folder.
- In the scratch copy I planted `tests/adapters/javascript/test_javascript_probe.py` with a function-level `from test_coverage_istanbul_default_arg_signature import specimen`:
  - `pytest tests/adapters/javascript tests/parsers` gives `647 passed`;
  - `pytest tests/adapters/javascript` gives `1 failed, 115 passed` (`ModuleNotFoundError`);
  - `test_import_contracts.py` gives `7 passed`.
- A full-suite gate stays green, but the "one lane per partition" that Part 1 §7 lists as the R2 prerequisite breaks.
- The tree is correct today: the four test-module imports are same-folder (`core/test_verify_ingested_r2.py:41`, `parsers/coverage/test_evaluate_javascript_end_to_end.py:352,480`, root `test_verdict_conformance.py:296`).

**Brief:** not required.

**Fix (exact):** append to the layout section:
```python
def cross_folder_test_imports(sources: dict[Path, str]) -> list[str]:
    """`from test_x import ...` resolves only when test_x's folder is on sys.path (pytest's prepend
    import mode inserts each collected file's folder), so it passes in a full run and fails in a
    one-folder run unless both files share a folder."""
    folder_of = {rel.stem: rel.parent for rel in sources}
    bad = []
    for rel, source in sorted(sources.items()):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.startswith("test_") and folder_of.get(name) != rel.parent:
                    bad.append(f"{rel.as_posix()} imports {name}")
    return bad


def test_test_modules_import_only_test_modules_in_their_own_folder():
    files = [p for p in _tests_files() if p.name.startswith("test_") and p.suffix == ".py"]
    assert cross_folder_test_imports({p: (TESTS_ROOT / p).read_text(encoding="utf-8") for p in files}) == []
    synthetic = {
        Path("adapters/javascript/test_javascript_x.py"): "def test_a():\n    from test_coverage_y import z\n",
        Path("parsers/coverage/test_coverage_y.py"): "z = 1\n",
    }
    assert cross_folder_test_imports(synthetic) == ["adapters/javascript/test_javascript_x.py imports test_coverage_y"]
```
If W3R-2, W3R-3 and W3R-4 are all applied, the contract file has 10 tests and collect-only gives 5975. Adjust the W3R-1 step 6 expectation to match whatever set is applied. I dry-ran all three fix blocks in memory against the real tree: each new test passes, and each synthetic negative fires; `unmapped_modules` also flags a planted `assay.adapters.java`.

---

### W3R-5: MINOR. `w3-scripts/durations.py` misattributes every moved test to `core` on post-move junit

**Where:** `assay/nyxloom-trove/reports/wave-a/w3-scripts/durations.py:13`.

**Evidence:**
- `classname.split(".")[1]` takes the second dotted part. For `tests.core.test_x` (after the move) that part is `core`, which yields `core.py`.
- Run on `/tmp/w3-0OY6/after.xml`, the script reports `core 5102 / root 533` and no adapter or parser rows.
- The module-level skip record (`classname=""`, `name="tests.test_mutation_judge_identity_properties"`) lands in `core` only by accident: `expected_dir("")` falls through to `core`.
- The §7 figures came from `before.xml` and are correct. The script is a committed deliverable that will be reused for the R2 decision data.

**Fix (exact):** replace line 13 with:
```python
    dotted = tc.get("classname") or tc.get("name", "")
    f = next((part + ".py" for part in dotted.split(".") if part.startswith("test_")), "")
```
and replace line 14 (`d = expected_dir(f) or "root"`) with:
```python
    d = (expected_dir(f) if f else "") or "root"
```
Verified:
- on `before.xml` this reproduces the report exactly: go 191/0.5, js 115/1.9, python 123/15.0, sql 120/0.3, core 4015/203.6, parsers 462/5.2, 43/0.1 and 26/2.0, root 533/48.0;
- on `after.xml` it gives the same partitions, with core 4022 (= 4015 + 7).

---

### W3R-6: MINOR. Stale prose mentions of moved test paths, outside 2c's edit scope (hand to W4)

**Where and evidence:** these are comments, docstrings and prose that name `tests/<old>.py` for files now under `tests/core|adapters|parsers`. None is functional.
- `src/assay`: `adjudication.py:50`, `canary.py:190`, `cli.py:575`, `config.py:918`, `result_reports/vitest_json.py:71`, `verify.py:2499`, `vocabulary.py:294,333`.
- `pyproject.toml:36`.
- Byte-pinned fixtures: `tests/fixtures/canary/go/greet/greet.go:22`, `fixtures/go/hello/hello.go:18`, `fixtures/mutation/go/sample.go:3`, `fixtures/mutation/python/{sample,broken}.py:3`, `fixtures/isolation/root_project_manifest.json:3`, `fixtures/coverage/PROVENANCE.md:284,363`.
- About 25 docstrings and comments in moved tests, e.g. `tests/core/test_cli_lanes.py:223` and `tests/parsers/result_reports/test_result_reports.py:4,134`.
- One is a runnable command that now errors: `tests/core/test_config_snapshot_selection.py:21` (`pytest -q -p no:randomly tests/test_config_snapshot_selection.py::test_snapshot_selection_closed_matrix`).

The brief forbids `src/**`, `pyproject.toml` and `tests/fixtures/**`, and 2c says "Nothing else changes", so W3 was right not to edit these.

**Fix (exact; carver's call, suggested text):**
- Append to `W3-LOG.md`: `- Stale prose paths (out of 2c scope, not edited): src/assay/{adjudication.py:50,canary.py:190,cli.py:575,config.py:918,result_reports/vitest_json.py:71,verify.py:2499,vocabulary.py:294,333}, pyproject.toml:36, the fixture comments listed in REVIEW-W3 W3R-6, and the moved tests' docstrings (git grep -nF -f <old paths> -- tests). W4 finishes the layout and updates them.`
- Add one Work line to `W4-test-split.md`: `Update prose path mentions of moved test files (W3-LOG "Stale prose paths"); byte-pinned fixtures stay as they are.`

---

## Checks that passed (no finding)

1. **Pure moves: verified mechanically.**
   - 221 renames: 194 `R100` (byte-identical) and 27 modified.
   - Normalization: I substituted the four 2c forms in the old text, dropped `from pathlib import Path( as _Path)`, `from conftest import …` and blank lines, then compared line by line with indentation kept. Zero residue in all 27. A whitespace-collapsed variant also gives zero.
   - The changed `from conftest import` lines only add `TESTS_ROOT`/`PROJECT_ROOT` and remove nothing.
   - There are 30 `Path(__file__)` occurrences in 27 files, and none remain.
   - pyflakes 3.4.0 is clean over `src/assay` and `tests` minus fixtures (O6).
   - `TESTS_ROOT = PROJECT_ROOT / "tests"` (`tests/conftest.py:59`) and `PROJECT_ROOT = Path(conftest).resolve().parent.parent`. Both equal the old root-level `.resolve().parent` and `.resolve().parents[1]` and do not depend on depth. Checked for `tests/core/…` and `tests/adapters/python/…`.
2. **Contract strength** (against the test's own `edges_of`/`allowed`):

   | Case | Caught? |
   |---|---|
   | function-level import | caught |
   | `from . import x` / `from .. import x` | caught |
   | `import assay.adapters.python as p` | caught |
   | `TYPE_CHECKING` import | caught |
   | `try/except ImportError` import | caught |
   | class-body import, star import | caught |
   | `importlib.import_module` / `__import__` literal | not caught (not required by the brief; no real use) |
   | new unmapped module | fails open (W3R-2; not required) |
   | deleted mapped module | not detected (not required; ImportError elsewhere) |

   The O1 plant (function-level `from .adapters import python` in `runner.py`) goes red and names the edge.
3. **Allowance maps:**
   - `ADAPTER_DEPS`, `PARSER_DEPS`, `CORE_PARSER_SURFACE`, `ANALYSIS_DEPS` and `allowed()` match the brief verbatim.
   - Against the measured 176-edge graph, every allowance is used by a real edge, except CD27's `assay.records` and `assay.guards`. Neither module exists yet, and CD27 mandates them.
   - There is no over-broad rule. Core may reach parsers only through the four-name surface, with `assay.coverage` as the dispatcher. Core cannot reach adapters, `cli` or `analysis`. `adapters.base` and the `adapters` package cannot reach concrete adapters.
4. **Layout:**
   - `ROOT_PINNED` equals the brief's 24 names and the 24 root test files in `git ls-files`.
   - `expected_dir` recomputed over `git ls-files tests` gives 0 misplaced, with counts core 156, parsers/coverage 24, go 15, python 12, js 5, sql 5, parsers/mutation 2, result_reports 2, root 24 (the carver's counts).
   - `fixtures/` and `qualification/` are excluded.
   - Planted files: a new root `test_*.py`, a nested `tests/core/sub/test_x.py` and a `tests/adapters/python/__init__.py` each go red.
5. **Collection:**
   - Base: 5965.
   - Implementer worktree: 5972 (+7 contract ids; one id shift `DESIGN-GUIDE.md:2974` to `:3011`).
   - Committed tree: 5965 (W3R-1).
   - No moved test was lost or renamed; compared by basename::id.
6. **Old-path literals:** every functional reference targets a `ROOT_PINNED` file:
   - `assay.toml:44,84-86,192-194`;
   - `tools/tester-unified-gate.sh:367,534,644,653`;
   - `nyxloom-trove/nyxloom.toml`;
   - `tests/test_self_lane.py:122-124`;
   - locked `P33/test_acceptance_v5.py:414,416,699`;
   - `P23/test_acceptance.py:50`.

   The carve-asset path lists (`P33/migration-manifest.json`, `W1/migrate_v5_to_v6.py`) are historical (CD8) and are not checked against the tree. The three doc edits the brief named are done, and no other doc names an old path. Outside assay, only historical reports under `run-gate-project/`/`nyxloom/` mention old paths. dstdns has none.
7. **Part 1:**
   - `coupling.py` rerun on `/tmp/w3-0OY6/before.coverage` is byte-identical to `w3-coupling.json`.
   - `graph.py` output is byte-identical to the scratch `graph.md` (176 edges, 3 TYPE_CHECKING-only, 118 module cycles, 2 component cycles).
   - `reach.py` is byte-identical to the scratch `reach.json`: only `assay.cli` loads adapters, and there are at least 23 modules per import.
   - `durations.py` on `before.xml` reproduces §7.
   - Lines total 47935 = `wc -l`. The union check is equal for all 10 components.
   - The report's tables match the JSON. It keeps to [M]/[C]/[I]/[A] measurement and leaves the component-scoped R2 claim as "Decision needed (operator)".
8. **Combined axes tried:**
   - B106 witness replay × moved node ids: safe. A stale `tests/test_x.py::…` target gives `target_count != 1`, `replay_witness_from_receipt` returns `None`, and the run falls back to full (`src/assay/mutation_witness.py:266-285`).
   - `conftest` path hooks: none are keyed on nodeid or path.
   - Snapshot lanes: they materialize `HEAD^{tree}` (this feeds W3R-1).
   - Ignore rule × exact-OID clone: W3R-1.
   - Cross-folder import × partition lane: W3R-4.

## Note for the carver (W1/W4, not a W3 finding)

- The gate's locked P33 suite (`tools/tester-unified-gate.sh:491`) asserts that the sweep finds `tests/test_python_qualification.py`, `tests/test_distribution_gate.py` and `tests/test_self_hosting.py` at those root paths (`P33/test_acceptance_v5.py:414,416,699`).
- P23's suite reads `tests/test_verdict_conformance.py` (`P23/test_acceptance.py:50`).
- `tests/test_self_lane.py:116-126` pins the literal `--deselect=tests/test_runner_snapshot_selection.py::…` strings but never checks that they exist. A stale `--deselect` silently stops deselecting.
- When W1 deletes or W4 moves these ROOT_PINNED files, these locked or pinned consumers must be handled in the same commit, or the gate's P33 phase goes red.

## Verdict

**MERGE-WITH-FIXES.**
- W3R-1 (BLOCKER) is mandatory before merge: apply steps 1–8, including the fresh-checkout collect oracle and the registered gate on the fixed HEAD. Its fix commits the exact file content reviewed here (sha256 `20891aae…1815`), so no re-review of the test body is needed; only the fix's verification output needs checking.
- W3R-2 through W3R-5 are small, self-contained hardening items with exact text, applicable now or deferred by the carver.
- W3R-6 is a handoff to W4.

Counts: **BLOCKER 1, MAJOR 0, MINOR 5.**
