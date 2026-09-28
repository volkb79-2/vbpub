# B110-P2 — Loop-progress guards: a spinning mutant fails fast instead of burning three baselines

| Field | Value |
|---|---|
| Backlog | **B113** (split from B110) |
| Branch | `assay-b110-p2-guards`, from the integration line `assay-b105-evidence-integrity` (after the plan §11.1 reconciliation) |
| Depends on | nothing. It can start on day one, in parallel with P0, P6 and P10a. |
| Contract class | **2d**: exact helper, exact call-site map, carver-witnessed red and green oracle |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-466** (plan D2, operator): add loop-progress guards and **keep A-464 unchanged**. Time never determines a candidate's classification, and there is no "hung counts as killed" rule. A guarded mutant becomes an ordinary **kill**, because a test raises; the monitor's rules are untouched. |
| Size | S: 1 helper, 8 one-line call-site rewrites plus imports, 1 loop rewrite, 1 exclusion-map update, 2 test files, 1 test-harness watchdog |

**Why.** In the e79 attempt, 4 of 39 candidates were `budget_exceeded` at 1,518 s each, 24.8% of that run's candidate time. They were `go.py` scanner mutants whose cursor stopped advancing. A CPU-spinning candidate keeps `cpu_growing` true, so liveness correctly never calls it `hung`; it burns the whole auto budget of `max(3×baseline, baseline+60)`. The analysis found 14 at-risk and 4 latent single-operator mutants. After this package, each of them raises `AssertionError` in whichever test drives that scanner path. The suite fails in normal time, so the candidate is `killed`. Nothing about liveness, budgets or classification changes.

---

## Context to read first

Paths are relative to `assay/`, verified at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0 (host-load rule), §3 D2, §10 (gates).
2. `src/assay/errors.py`:
   - `:1-40`: module docstring (stdlib-only; every module may depend on it) and `__all__`.
   - The precedent for raising `AssertionError` on "can't happen": `src/assay/git.py:261`, `src/assay/cli.py:488`.
3. The scanner sites, read each function whole:
   - `src/assay/adapters/go.py:203` (imports), `:283-292` (`_scan_raw_string`), `:305-359` (`_strip_comments_and_literals`; cursor assignments `:326`, `:334`, `:356`);
   - `src/assay/adapters/javascript.py:169-178` (imports), `:216-260` (`_strip_comments`; cursor assignment `:258`);
   - `src/assay/adapters/sql_lex.py:60-66` (imports), `:174-300` (`_lex_once`; cursor assignments `:197` and `:280`);
   - `src/assay/adapters/go_modfile.py:76-77` (imports), `:372-395` (`_tokens`; `:393`);
   - `src/assay/isolation.py:50-52` (imports), `:1374-1407` (`_parse_tree`; `:1406`).
4. `src/assay/git.py:283-365`: `_run_bounded`. The drain loop is `:335-355`; the wait loop `:357-365` is timeout-bounded and not touched. The selector-cleanup `pragma: no cover` lines are `:376-377` and `:1342-1343`.
5. `tests/fixtures/b105-coverage-exclusions.json`: git.py is pinned at `[376, 377, 1342, 1343]`. It is enforced by `tests/conftest.py:107-113` and `:165-214` in the B105 preflight, and by `tests/test_b105_source_coverage_controls.py:136-162`.
6. The mutation operators, so you know which mutants exist: `src/assay/adapters/python.py:445-470` (the compare-swap and boolop-swap catalogues), `:537-611` (compare-swap, boolop-swap and bool-const-flip site builders), `:618-672` (falsy-swap, direct falsy returns only), and `generate_mutation_sites` at `:881`.
7. The real-child liveness tests: `tests/test_cli_run.py:463` `test_run_liveness_classifies_a_thread_join_hang_as_hung` (budget `"50s"` at `:529`) and `:586` `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung` (budget `"35s"` at `:645`). Both call `run(argv)` (`:127`) in-process.
   - **If P1 merged first**, both live in `tests/zz_slow/test_cli_run_real_campaigns.py`. Find them by name.
   - Supporting context: `src/assay/runner.py:4420` (`liveness.LivenessRunner(...)` is looked up on the module at call time, so it is patchable), `src/assay/liveness.py:1299-1316` (the `popen` seam), `:1375` (`start_new_session=True`) and `:1530-1537` (the timeout check that a mutant can disable).
8. The analysis: `nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`, hang-loop section. It has the full 50-loop inventory and explains why an inline `assert` or a bounded `for … else` was rejected.

---

## Implementation packet (normative)

### Interfaces

**The helper** goes in `src/assay/errors.py`, appended after the existing definitions, and is added to `__all__`:

```python
def require_advance(old: int, new: int) -> int:
    """Return *new*; a scanner cursor that did not move strictly forward is an
    internal defect (never an input property), so refuse instead of spinning.

    (B113/A-466) Scanner loops advance a cursor by assignment. A single
    mutant can make that assignment stall or step backwards, and a spinning
    candidate never looks idle to the liveness monitor, so it would burn its
    whole per-candidate budget. Raising here turns it into an ordinary test
    failure. One shared comparison means one mutation site, killed by
    tests/test_errors.py, instead of one unkillable site per scanner.
    """
    if new <= old:
        raise AssertionError(f"scanner cursor did not advance ({old} -> {new})")
    return new
```

- Use an explicit `raise`, never an `assert` statement, because `python -O` would strip it.
- It adds no branch at any call site.
- It creates no import cycle: `errors.py` imports only stdlib.

**Call-site map.** Change exactly these eight assignments, with no other edit to those lines:

| File:line | Before | After | Import change |
|---|---|---|---|
| `adapters/go.py:326` | `i = end` | `i = require_advance(i, end)` | `:203` → `from ..errors import AssayError, Outcome, ReasonCode, require_advance` |
| `adapters/go.py:334` | `i = end` | `i = require_advance(i, end)` | (same) |
| `adapters/go.py:356` | `i = end` | `i = require_advance(i, end)` | (same) |
| `adapters/javascript.py:258` | `i = end` | `i = require_advance(i, end)` | add `from ..errors import require_advance` immediately **above** `from ..mutation import MutationSite` (`:176`) |
| `adapters/sql_lex.py:197` | `i = end` | `i = require_advance(i, end)` | add `from ..errors import require_advance` immediately **above** `from ..mutation import MutationDiscoveryError` (`:64`) |
| `adapters/sql_lex.py:280` | `i = close + len(delimiter)` | `i = require_advance(i, close + len(delimiter))` | (same) |
| `adapters/go_modfile.py:393` | `index = length if newline == -1 else newline` | `index = require_advance(index, length if newline == -1 else newline)` | `:77` → add `require_advance` to the existing `from ..errors import ...` |
| `isolation.py:1406` | `index = oid_end` | `index = require_advance(index, oid_end)` | `:52` → add `require_advance` to the existing `from .errors import ...` |

In unmutated code every one of these right-hand sides is strictly greater than the cursor. The analysis proved this per site: `find` results start at least 2 characters past a two-character opener, `close + 2` and `close + len(delimiter)` are past the opener, and `oid_end ≥ index + 22`. So a correct run never raises.

**Explicitly out of scope:**
- the "optional uniformity" sites `go.py:342/349`, `sql_lex.py:220/240/252/264`, `go_modfile.py:406` (safe today; leaving them keeps the B105 inventory change minimal);
- every other loop in the inventory.

**The `git.py` drain rewrite.** Replace the loop header at `git.py:335` and add a post-loop check, so that the loop's exit test contains **no** compare-swap or boolop-swap site:

```python
        while selector.get_map():
            timeout = _sample_remaining(remaining)
            for key, _ in selector.select(timeout):
                ...                         # the existing body, byte-identical, including both `break`s
            if overflowed:
                break
```

Why each form fails or works:
- `and → or` on the old header ran `selector.select(None)` on an empty selector, which **blocks forever** when `remaining is None`.
- `is None → is not None` skipped draining, which deadlocks `proc.wait()` on more than ~64 KiB of output.
- `overflowed` is `None` or a non-empty tuple, so its truthiness is exactly `is not None`. The existing overflow tests take the true side; every ordinary call takes the false side.

The rewrite shifts later `git.py` lines. Update `tests/fixtures/b105-coverage-exclusions.json` `"src/assay/git.py".lines` to the **new** line numbers of the same two `except … # pragma: no cover` lines and their `pass` lines. Take the numbers from `grep -n "pragma: no cover" src/assay/git.py` after the edit; do not predict them. Keep the `reason` text unchanged.

**`liveness.py:1530`** (the `timeout is not None` check) cannot be protected against a mutant of itself. Its hazard is test-level: a mutant that disables the timeout makes the busy-loop test's child spin forever. The fix is the **test watchdog** below, not a source change.

### Watchdog for the two real-child liveness tests

This goes in the test module that currently holds them. The helper is private to that module:

```python
@contextlib.contextmanager
def _recorded_liveness_children(monkeypatch) -> Iterator[list[subprocess.Popen]]:
    started: list[subprocess.Popen] = []
    real = liveness.LivenessRunner

    def recording_popen(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        started.append(proc)
        return proc

    class _RecordingLivenessRunner(real):
        def __init__(self, **kwargs):
            kwargs.setdefault("popen", recording_popen)
            super().__init__(**kwargs)

    monkeypatch.setattr(liveness, "LivenessRunner", _RecordingLivenessRunner)
    yield started


def _run_with_child_watchdog(monkeypatch, argv, *, failsafe_s: float = 300.0):
    """Run `run(argv)` in a thread; a FAILSAFE only (A-466, AUTHORING §3b A).
    On expiry, kill every candidate process group the run started (each is its
    own session, `start_new_session=True`, so the outer kill cannot reach it)
    and fail. Never decides pass/fail for a run that returns."""
    with _recorded_liveness_children(monkeypatch) as started:
        box: dict[str, object] = {}
        thread = threading.Thread(target=lambda: box.setdefault("result", run(argv)), daemon=True)
        thread.start()
        thread.join(timeout=failsafe_s)
        if thread.is_alive():
            for proc in started:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
            thread.join(timeout=60.0)
            pytest.fail("watchdog: the real-child liveness run did not return; its candidate process groups were killed")
        assert all(proc.poll() is not None for proc in started), "a candidate process outlived its run"
        return box["result"]
```

In both tests, replace `code, out, err = run([...])` with `code, out, err = _run_with_child_watchdog(monkeypatch, [...])` and add the `monkeypatch` parameter. Their assertions stay byte-identical.

- The failsafe of 300 s is generous: both runs return in about 31–36 s today. It exists only so that a mutant which disables the elapsed-budget check becomes a *test failure*, and a kill, within 300 s, instead of consuming the outer auto budget of about 1,560 s and leaving an orphaned spinning child.
- The final `poll()` assertion is a deterministic post-condition. It is not timing.

### The deterministic mutant oracle

This is `tests/test_scanner_progress_guards.py`, prepared and witnessed by the carver. It builds each at-risk mutant **with assay's own Python adapter** from the current source, `exec`s it as a throw-away module, and drives it with an input that makes the mutant stall. A `sys.settrace` **line-event budget** of 200,000 turns an unguarded spin into a deterministic `_StepLimit` exception, independent of machine speed:
- **red (today):** every case raises `_StepLimit`;
- **green (after the guards):** every case raises `AssertionError("scanner cursor did not advance ...")`.

The carver ran exactly this table against an unmodified `src` (16/16 hit the step limit) and against a scratch copy with the packet's edits applied (16/16 raise the guard's `AssertionError`). The scratch prototype is `/tmp/claude-1003/-workspaces-vbpub/4d188675-268f-4f79-8aec-e7e00b2aac8a/scratchpad/p2_oracle_proto.py`; the test below is its pytest form.

```python
import ast
import sys
import types
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

from assay.adapters.python import PythonAdapter

SRC = PROJECT_ROOT / "src" / "assay"
_LINE_BUDGET = 200_000   # deterministic: counts executed lines, never seconds


class _StepLimit(Exception):
    """The mutant executed more lines than any terminating scan of these inputs needs."""


# (id, file under src/assay, function holding the site, unique substring of the site's line
#  within that function, operator, site description, occurrence on that line in byte order,
#  entry call)
CASES = [
    ("go-292", "adapters/go.py", "_scan_raw_string", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\nvar s = `open")),
    ("go-321", "adapters/go.py", "_strip_comments_and_literals", 'two == "//"', "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\nfunc f() {}\n")),
    ("go-323", "adapters/go.py", "_strip_comments_and_literals", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\n// c")),
    ("go-330", "adapters/go.py", "_strip_comments_and_literals", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\n/* open")),
    ("js-243", "adapters/javascript.py", "_strip_comments", 'two == "//"', "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("a\nb\n")),
    ("js-245", "adapters/javascript.py", "_strip_comments", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("a // c")),
    ("js-249", "adapters/javascript.py", "_strip_comments", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("ab /* open")),
    ("sql-193-eq", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-193-or1", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:boolop-swap", "And->Or", 0,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-193-or2", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:boolop-swap", "And->Or", 1,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-195", "adapters/sql_lex.py", "_lex_once", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1; -- trailing, no newline")),
    ("sql-270", "adapters/sql_lex.py", "_lex_once", "tag_end is not None", "python:compare-swap", "IsNot->Is", 0,
     lambda m: m._lex_once(b"-- $1\nSELECT $1")),
    ("sql-273", "adapters/sql_lex.py", "_lex_once", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1; $$ open")),
    ("gomod-393", "adapters/go_modfile.py", "_tokens", "newline == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: list(m._tokens("module x\n// c", source="go.mod"))),
    ("iso-1388", "isolation.py", "_parse_tree", "space == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._parse_tree(b"a\x00" + b"\x01" * 20 + b"b\x00" + b"\x01" * 20, "t")),
    ("iso-1392", "isolation.py", "_parse_tree", "nul == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._parse_tree(b"100644 aaaaaaaaaaaaaaaaaaaa bbbb", "t")),
]


def _locate(rel, function, anchor):
    text = (SRC / rel).read_text(encoding="utf-8")
    matches = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == function]
    assert len(matches) == 1, f"{rel}: expected one module-level {function}()"
    lines = text.splitlines()
    hits = [n for n in range(matches[0].lineno, matches[0].end_lineno + 1) if anchor in lines[n - 1]]
    assert len(hits) == 1, f"{rel}:{function}: anchor {anchor!r} must name exactly one line, got {hits}"
    return text, hits[0]


def _mutant_module(case, monkeypatch):
    cid, rel, function, anchor, operator, description, occurrence, _ = case
    text, lineno = _locate(rel, function, anchor)
    sites = sorted(
        (s for s in PythonAdapter().generate_mutation_sites(text, {lineno}, operators=(operator,), limit=1000)
         if s.lineno == lineno and s.description == description),
        key=lambda s: s.start_byte,
    )
    assert len(sites) > occurrence, f"{cid}: the mutant this guard exists for is no longer generated"
    package = "assay" + ("." + rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else "")
    name = f"{package}._b113_mutant_{cid.replace('-', '_')}"
    module = types.ModuleType(name)
    module.__package__ = package
    monkeypatch.setitem(sys.modules, name, module)   # dataclasses resolve annotations via sys.modules
    # A synthetic filename: coverage never attributes these lines to the real source file.
    exec(compile(sites[occurrence].apply(text.encode("utf-8")).decode("utf-8"),
                 f"<b113 mutant {cid}>", "exec"), module.__dict__)
    return module


def _line_budgeted(call):
    executed = 0

    def tracer(frame, event, arg):
        nonlocal executed
        if event == "line":
            executed += 1
            if executed > _LINE_BUDGET:
                raise _StepLimit()
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        return call()
    finally:
        sys.settrace(previous)


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_stalled_scanner_mutant_is_refused_not_spun(case, monkeypatch):
    module = _mutant_module(case, monkeypatch)
    with pytest.raises(AssertionError, match="scanner cursor did not advance"):
        _line_budgeted(lambda: case[7](module))
```

It also needs these sibling tests in the same file:
- `test_every_guarded_function_still_terminates_on_ordinary_input`, which runs each unmutated entry function on its case input and asserts it returns a value or raises that module's **own** declared refusal (`MutationDiscoveryError`, or `AssayError` with `GIT_FAILED`), **not** `AssertionError`. This keeps the guard from firing on correct code.
- `test_the_git_drain_loop_offers_no_mutation_site_in_its_exit_test`:
  1. Parse `src/assay/git.py` and locate `_run_bounded`.
  2. Find the single line in it containing `while selector.get_map():` and the single line `if overflowed:`.
  3. Assert that `PythonAdapter().generate_mutation_sites(text, {those two line numbers}, operators=("python:compare-swap", "python:boolop-swap", "python:falsy-swap", "python:bool-const-flip"), limit=1000)` is empty.

  Red today: the old header line carries `Is` and `And` sites.

### Decision table

| Situation | Before | After | Classification effect |
|---|---|---|---|
| Unmutated scan of any input | terminates | terminates identically (guard never fires) | none |
| Mutant stalls or rewinds a guarded cursor | spins until the per-candidate budget → `budget_exceeded` | `AssertionError` in the driving test → suite fails → `killed` | this changes **which code runs**, not a classification rule |
| Mutant grows memory in a latent loop (`go_modfile:393`, `sql_lex:273`, `isolation:1388/1392`) | unbounded RSS for up to ~26 min | immediate `AssertionError` | same |
| `git.py` drain `and→or` / `is None→is not None` | blocking forever or deadlock | site no longer exists | fewer candidates |
| `liveness.py:1530` mutant hangs the busy-loop test | child spins; the outer candidate burns its budget; orphan child | watchdog kills the child group at 300 s; the test fails → `killed`; no orphan | same |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| helper | `errors.py` | O1 | `tests/test_errors.py` new test | change `<=` to `<` → `require_advance(3, 3)` does not raise → O1 fails |
| 8 call sites | adapters, `isolation.py` | O2 (16 parametrized cases), O3 | `CASES` | remove any one call-site guard → its case hits `_StepLimit`, not `AssertionError` → O2 fails |
| drain rewrite | `git.py` | O4 | `_run_bounded` source | restore the old header → sites found → O4 fails |
| exclusion map | `tests/fixtures/b105-coverage-exclusions.json` | O5 | existing `test_b105_source_coverage_controls.py` and the B105 preflight | leave the old line numbers → the preflight's raw-exclusion validation fails |
| watchdog | liveness real-child tests | O6 | the two tests | (review only) remove the `killpg` loop → a disabled-timeout mutant leaves an orphan (argued, not executed) |

### Degrees of freedom

You choose the watchdog helper's private names and the exact assertion-message text of the sibling tests. Nothing else: the helper's signature and message prefix (`"scanner cursor did not advance"`), the call-site set, the loop rewrite form, the line budget and the case table are fixed.

---

## Work

1. **Red first.**
   - Add `tests/test_scanner_progress_guards.py` exactly as specified, and add a `require_advance` test to `tests/test_errors.py`:
     ```python
     def test_require_advance_returns_a_strictly_advanced_cursor_and_refuses_the_rest():
         assert require_advance(3, 4) == 4
         for stalled in (3, 2, -1):
             with pytest.raises(AssertionError, match="scanner cursor did not advance"):
                 require_advance(3, stalled)
     ```
   - Run both files and confirm the failures. The 16 guard cases fail with `_StepLimit`. The drain-site test fails with sites present. The errors test fails on import.
   - Record the counts, then commit as the red commit.
2. **Implement** the helper, the 8 call sites with their imports and the drain rewrite. Update the exclusion map from `grep -n "pragma: no cover" src/assay/git.py`. Re-run the focused tests: all green. Commit.
3. **Watchdog.** Add `_recorded_liveness_children` and `_run_with_child_watchdog` to the module holding the two real-child liveness tests, then switch both tests to them. Add imports as needed (`contextlib`, `os`, `signal`, `subprocess`, `threading`, `from assay import liveness`, `Iterator`); keep pyflakes clean. Run those two tests once, serially. Commit.
4. **Docs** (below). Commit.

## Oracles

For each oracle: what shows it holds (Observable), the plausible wrong implementation it catches (Negative), and where it runs (Gate).

- **O1. The helper contract.**
  - Observable: `(3, 4) → 4`; `(3, 3)`, `(3, 2)` and `(3, -1)` raise with the prefix.
  - Negative: `<` instead of `<=` lets a stalled cursor through.
  - Gate: `tests/test_errors.py`, then tester-unified.
- **O2. Every at-risk mutant is refused, not spun.**
  - Observable: all 16 parametrized cases raise `AssertionError` under the deterministic line budget.
  - Negative: guarding the wrong assignment (for example the `continue` path) leaves a case at `_StepLimit`. A guard written as `assert new > old` passes under a normal run but is stripped under `-O`, and it adds a surviving mutant per site. The code review rejects that form.
  - Gate: focused, then tester-unified.
- **O3. Correct code is untouched.**
  - Observable: the "ordinary input" sibling test passes, and the whole existing adapter and isolation suites stay green unchanged (for example `tests/test_adapters_sql_lexer.py` and `tests/test_isolation.py`).
  - Negative: a guard on a path where the cursor legitimately equals `end`. None exists; the test proves it.
- **O4. The drain loop's exit test has no mutation site.** Observable: the site-generation assertion. Negative: the old header.
- **O5. The coverage exclusion inventory still matches raw coverage.**
  - Observable: `self-qualification-preflight` passes. Its session hook validates raw `excluded_lines` against the updated map.
  - Negative: stale line numbers, or excluding a line that is not a `pragma` line.
- **O6. The watchdog is a failsafe and never an oracle.**
  - Observable: both liveness tests pass with byte-identical assertions, and the post-run `poll()` check holds.
  - Negative: using the 300 s join as the pass condition, for example asserting the elapsed time. That is forbidden.

### What an oracle must NOT contain (AUTHORING.md §3b, verbatim)

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

---

## Docs sync

- **`docs/DESIGN-GUIDE.md`**, §"Full-source self-qualification (B105)" (`:1934`). Add one paragraph with the *why*:
  - time never classifies a candidate (A-464), so a mutant that spins must fail by itself;
  - scanner cursors advance through `errors.require_advance`, one shared comparison that one test kills;
  - the `git.py` drain loop's exit test deliberately has no mutable comparison;
  - the two real-child liveness tests carry a failsafe watchdog.

  Cite A-466.
- **`CHANGES.md` `## [Unreleased]`:**
  - `### Fixed`: single-operator mutants of the Go, JavaScript, SQL and go.mod scanners, and of the git tree parser, now fail fast instead of spinning until the per-candidate budget; the `git.py` pipe drain can no longer block forever under a mutant.
  - `### Testing`: the deterministic mutant-guard tests and the watchdog.
- **README / CONSUMERS:** no consumer-visible behavior changes. Leave both files unchanged and say so in the report.

## Scope / forbid

**You may touch only:**
- `src/assay/errors.py`;
- `src/assay/adapters/{go,javascript,sql_lex,go_modfile}.py`, the named lines and imports only;
- `src/assay/isolation.py`, `:52` and `:1406` only;
- `src/assay/git.py`, the drain loop only;
- `tests/test_errors.py`, `tests/test_scanner_progress_guards.py` (new);
- the module that holds the two real-child liveness tests, for the watchdog and those two call sites only;
- `tests/fixtures/b105-coverage-exclusions.json`, the git.py `lines` only;
- `docs/DESIGN-GUIDE.md`, `CHANGES.md`.

**Forbidden:**
- `liveness.py`, `mutation.py`, `runner.py`;
- any classification, budget or liveness constant;
- `pragma: no cover` additions;
- any new `src/assay` module, which would also break `tests/test_self_lane.py:128-135`;
- the optional uniformity sites;
- `assay.toml`, `run-gate.toml`, the gate scripts;
- `nyxloom-trove/4-backlog.md`, `decisions.md`.

Anything else is a BLOCKED trigger.

## Gate

**Host-load rule (plan §0, verbatim):**

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. **Focused tests, serially:**
   `cd <worktree>/assay && nice -n 19 ionice -c3 python -m pytest tests/test_errors.py tests/test_scanner_progress_guards.py tests/test_b105_source_coverage_controls.py tests/test_adapters_sql_lexer.py tests/test_isolation.py tests/test_git_hostile_boundary.py tests/test_b105_git_process_boundaries.py -q -p no:cacheprovider`.
   Then run the two watchdogged liveness tests by node ID, once.
2. **Registered gate:**
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p2-tester-unified.log 2>&1; echo "exit=$?"`.
   Then, as a separate step: `grep -E 'ASSAY_GATE_CONTAINER_EXIT=|ASSAY_REGISTERED_GATE_COMPLETE=' /tmp/b110-p2-tester-unified.log`.
3. **B105 preflight** (required: `src/assay` lines and the exclusion map changed, and R1 must stay 100% with the exact exclusion inventory):
   `cd <worktree>/assay && python ./run-gate.py self-qualification-preflight > /tmp/b110-p2-preflight.log 2>&1; echo "exit=$?"`.
   Read `B105_VERIFIED_LANE=self-qualification-preflight` in a separate step. Run it only after step 2 has finished.

**Never run the `self-qualification` lane.**

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P2-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Examples of a BLOCKED trigger:
- a guard case still reaches `_StepLimit` after its call-site edit;
- an unmutated input trips the guard;
- R1 loses a line or branch that needs a pragma.

A product gap is not a BLOCKED. For example, you believe another loop needs a guard. Record it as `D-<NNN>` in the report and do not act on it.

## Report

Write `nyxloom-trove/reports/assay-B110-P2-REPORT.md` and return:
- the red commit, the green commit and the head commit, each with trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`;
- `git diff --stat`;
- the traceability table with actual node IDs. Give the red run's failure count per case and the green run's pass count. Report every controlled break you ran, with its result;
- the new git.py exclusion line numbers and the grep that produced them;
- the gate log paths, with the markers read separately;
- residuals.

**Checkpointing:** ARM at ~120k context or ~60 tool calls. CUT at a green, committed boundary, with a continuation brief in the report.
