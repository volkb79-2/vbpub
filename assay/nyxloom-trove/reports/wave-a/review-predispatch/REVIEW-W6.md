# REVIEW-W6: Wave A W6 (B113, P2 loop guards)

- Reviewer: fresh adversarial reviewer (not a fork). Written 2026-09-29.
- Branch: `wave-a-w6-guards`. Diff reviewed: `20846716..wave-a-w6-guards` (commits `06e0a790`, `f13bf1d0`, `8036bd8d`, `4979f6d0`).
- Binding sources: `b110/P2-loop-guards.md`, the P2 section of `wave-a/REBASE-P0-P2.md`, and `CARVER-DECISIONS.md` (CD2, CD3, CD23 amended, CD27, CD29, CD44, CD45).
- Registered gate: **not** run (CD44).
- Everything ran under `nice -n 19 ionice -c3`. Scratch work is in `scratchpad/w6review/`.

## Verdict: MERGE-WITH-FIXES

| Severity | Count |
|---|---|
| BLOCKER | 0 |
| MAJOR | 1 |
| MINOR | 4 |

**What is correct:**
- The product change is correct. All 8 guards advance strictly on every legitimate input. A differential fuzz found 0 guard trips and byte-identical results against the base.
- The drain rewrite has the same meaning as the old loop.
- The exclusion map is exact.
- Coverage is clean apart from the known environmental line.

**What must change before merge:** the new behavioural test O4b. Under two `git.py` mutants it sits idle for up to its 120 s failsafe. In the R2 campaign the liveness monitor would call that `hung`, or else a timeout would decide the kill. Either way, the package adds a new non-killed candidate class, which is the opposite of B113's purpose. The fix text below is verified.

---

## Findings

### W6R-1 — MAJOR — O4b idles until its failsafe under the `git.py:359` wait-loop mutants (and never cleans up its child when an assertion fails)

**Where:** `assay/tests/core/test_isolation_scanner_progress_guards.py:65-102` (`test_the_git_drain_stops_after_an_output_overflow_even_without_a_deadline`). This interacts with `src/assay/git.py:359` and `src/assay/liveness.py:1665-1677`.

**Evidence.** Scratch script `w6review/o4b_mutants.py` runs O4b's exact call against every `_run_bounded` mutant the lane's four operators generate.

Two mutants **block**:
- `git.py:359` `Is->IsNot`, first occurrence: `while overflowed is not None and proc.poll() is None:`
- `git.py:359` `And->Or`

In both, the drain overflows and stops reading. `_run_bounded` then enters the wait loop, samples `remaining=None`, and calls `proc.wait()` (`git.py:361-363`). Meanwhile the child is blocked writing to the full pipe. Measured: child state `S`, **0 CPU ticks over 3 s**, and the test's main thread is parked in `thread.join(timeout=120.0)` (`:88`).

In an R2 candidate, the liveness monitor classifies that idle stall as `hung`:
- The hung condition is `idle_hang = idle_for >= bound and not cpu_growing` (`liveness.py:1670`).
- `bound = max(3 × worst baseline gap, 15 s)` (`liveness.py:1026`). The last measured value was `expect_next_event_within_s = 107.7` s (`assay-B110-RUNTIME-ANALYSIS-2026-09-28.md:124`), and that was before `35adca38` removed the slowest real-clock tests.
- The CPU window is 30 s (`liveness.py:542`).
- Both are below 120 s. `hung` maps to `BUDGET_EXCEEDED/CANDIDATE_HUNG` (`mutation.py:1946`, `:4002`), so the self-qualification R2 cannot PASS.
- If resource evidence is incomplete, the 120 s failsafe decides the kill instead. That is a timeout deciding a classification (A-464, AUTHORING §3b A: "If shrinking the timeout could flip the result, it is an oracle").
- **Before W6 no test exposed these mutants to an infinite real producer.** The only other real-child `_run_bounded` test writes 5 bytes and exits (`test_git_hostile_boundary.py:222-233`). `And->Or` is equivalent on every other path and was plausibly a survivor.

**Second defect: no cleanup on failure.** O4b kills its child only in the failsafe branch (`:89-96`).
- Under the `git.py:326` `IsNot->Is` mutants, the `assert` raises before the `try`, so `_run_bounded` never kills the child.
- O4b then fails on `:97` and leaves the infinite writer alive, blocked on the pipe, until the pytest process exits. The scratch run showed `GROUP-ALIVE`.
- The failsafe branch also calls `os.killpg(proc.pid, 9)` without the "leader still unreaped" check that the brief's own watchdog requires (P2 brief §Watchdog, round-2 P2R2-1).

**Verified fix** (scratch copy of the branch, `w6review/fix/`, runner `w6review/run_mutants.sh`):
- Correct code: 6/6 pass in 0.39 s.
- Both 359 mutants: fail in 0.7 s instead of idling.
- Both 326 mutants: fail in 0.7 s with no leftover writer.
- Wrong placement (`if overflowed: break` inside the `for`, failsafe shortened to 10 s in scratch only): still fails through the failsafe, with no leftovers.

**Fix text (exact):**

1. In `assay/tests/core/test_isolation_scanner_progress_guards.py`, replace the import block lines
   ```python
   import ast
   import os
   import subprocess
   ```
   with
   ```python
   import ast
   import contextlib
   import os
   import signal
   import subprocess
   ```
2. Replace the whole function `test_the_git_drain_stops_after_an_output_overflow_even_without_a_deadline` (currently `:65-102`) with:
   ```python
   def test_the_git_drain_stops_after_an_output_overflow_even_without_a_deadline(monkeypatch):
       started: list[subprocess.Popen] = []
       killed: list[int] = []
       real_popen = subprocess.Popen
       real_kill = git._kill_owned_group

       def recording_kill(proc):
           killed.append(proc.pid)
           real_kill(proc)

       def recording_popen(*args, **kwargs):
           proc = real_popen(*args, **kwargs)
           real_wait = proc.wait

           def wait_only_after_the_group_kill(*a, **k):
               # The overflowing child never exits by itself: waiting on it before
               # its group is killed deadlocks on the full pipe and would sit idle
               # until the failsafe below (git.py wait-loop mutants). Refuse at once.
               if proc.pid not in killed:
                   raise AssertionError("waited on the overflowing child before killing its group")
               return real_wait(*a, **k)

           proc.wait = wait_only_after_the_group_kill
           started.append(proc)
           return proc

       monkeypatch.setattr(git.subprocess, "Popen", recording_popen)
       monkeypatch.setattr(git, "_kill_owned_group", recording_kill)
       monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 4)  # overflow on the first chunk: minimal work before the verdict
       outcome: list[BaseException] = []

       def worker() -> None:
           try:
               git._run_bounded(
                   [sys.executable, "-c", "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"],
                   remaining=None,
               )
           except BaseException as exc:  # recorded and asserted below
               outcome.append(exc)

       thread = threading.Thread(target=worker, daemon=True)
       thread.start()
       try:
           thread.join(timeout=120.0)  # failsafe only; never decides pass/fail for a run that returns
           if thread.is_alive():
               pytest.fail("the drain did not stop after an output overflow; the child group was killed")
           assert len(outcome) == 1 and isinstance(outcome[0], AssayError), outcome
           assert outcome[0].reason_code is ReasonCode.GIT_FAILED
           assert "standard output" in str(outcome[0])
           (child,) = started
           with pytest.raises(ProcessLookupError):
               os.killpg(child.pid, 0)
       finally:
           for proc in started:
               if proc.poll() is None:  # only a leader that is still ours and unreaped
                   killed.append(proc.pid)
                   with contextlib.suppress(ProcessLookupError, PermissionError):
                       os.killpg(proc.pid, signal.SIGKILL)
           thread.join(timeout=60.0)
           if not thread.is_alive():
               for proc in started:
                   proc.stdout.close()
                   proc.stderr.close()
                   proc.wait(timeout=60.0)
   ```
3. Re-run `tests/core/test_isolation_scanner_progress_guards.py` serially under nice/ionice (expect 6 passed). Then run the O4b controlled break once more locally without committing it: move `if overflowed: break` inside the `for`, confirm the test fails through the failsafe, and revert.
4. Add one line to `W6-LOG.md` under "Oracles": `O4b also refuses a wait on the overflowing child before its group is killed, so the git.py:359 wait-loop mutants (Is->IsNot on 'overflowed is None', And->Or) fail at once instead of idling into liveness 'hung' (REVIEW-W6 W6R-1).`

*Why the 4-byte limit is safe:* existing tests inject only the ceiling's magnitude in the same way (`test_git_hostile_boundary.py:205`, `test_b105_git_process_boundaries.py:223`). The call still has no deadline and an infinite producer, so wrong placement is still caught, and the work before the verdict drops from 64 MiB to one chunk.

---

### W6R-2 — MINOR — The "ordinary input" tests never reach 4 of the 8 guarded assignments and assert no return value

**Where:**
- `assay/tests/scanner_progress_support.py:101-108` (`assert_ordinary_input_terminates`);
- its four callers, for example `tests/core/test_isolation_scanner_progress_guards.py:42-46`.

**Evidence.**
- A line trace of every case input run through the *real* functions (scratch, recorded in this session) reaches `go.py:326`, `go_modfile.py:393`, `javascript.py:259` and `sql_lex.py:198`.
- It **never reaches** `go.py:334` (closed `/* */`), `go.py:356` (closed raw string), `sql_lex.py:281` (closed dollar quote) or `isolation.py:1406`. Both isolation inputs are refused before the cursor moves, and the test even asserts `GIT_FAILED` (`:44-46`).
- The helper's body is `try: call; except refusal: pass`, with no assertion on the returned value (AUTHORING §3b C).
- O3's claim "keeps the guard from firing on correct code" therefore rests on the other suites plus 100% line coverage for half the sites.
- The product itself is fine: the differential fuzz is clean and those sites are covered elsewhere.

**Fix text (exact):** append these tests. Each expected value was verified against the branch's `src`.

- To `assay/tests/adapters/go/test_adapters_go_scanner_progress_guards.py`:
  ```python


  def test_the_block_comment_and_raw_string_guards_accept_ordinary_input():
      # Reaches go.py's closed `/* */` and closed raw-string guards with real code.
      assert go._strip_comments_and_literals("package x\n/* c */ var s = `r`\n") == "package x\n        var s =    \n"
  ```
- To `assay/tests/adapters/sql/test_adapters_sql_scanner_progress_guards.py`:
  ```python


  def test_the_dollar_quote_guard_accepts_ordinary_input():
      # Reaches sql_lex.py's closed dollar-quote guard with real code.
      mask, bodies = sql_lex._lex_once(b"SELECT $$ b $$;\n")
      assert bytes(mask) == b"SELECT        ;\n"
      assert bodies == [(9, 12)]
  ```
- To `assay/tests/core/test_isolation_scanner_progress_guards.py`, directly after `test_every_guarded_function_still_terminates_on_ordinary_input`:
  ```python


  def test_the_tree_record_guard_accepts_ordinary_records():
      # Reaches isolation.py's record-cursor guard twice (from 0 and from a later record) with real code.
      raw = b"100644 f\x00" + bytes(range(20)) + b"40000 d\x00" + bytes(range(20, 40))
      assert isolation._parse_tree(raw, "t") == (
          ("100644", b"f", bytes(range(20)).hex()),
          ("40000", b"d", bytes(range(20, 40)).hex()),
      )
  ```
- Update the W6-LOG "Collect-only" line: the after count grows by 3.

---

### W6R-3 — MINOR — O4 has no positive control, and the site generator silently returns `[]` for an unknown operator name

**Where:** `assay/tests/core/test_isolation_scanner_progress_guards.py:49-62`.

**Evidence.**
- `PythonAdapter().generate_mutation_sites(text, {line}, operators=("python:compare-swapx",), limit=1000)` returns `[]` with no error (scratch check). A future operator rename would therefore leave O4's `assert list(sites) == []` green forever.
- The log's controlled break (the old header restored) turns O4 red only through the missing anchor (`W6-LOG.md`, O4 row). So the emptiness half of O4 has never been seen red.
- The same call on the adjacent wait-loop line `git.py:359` returns 3 sites (`Is->IsNot` ×2, `And->Or`), which makes a natural positive control.

**Fix text (exact):** in `test_the_git_drain_loop_offers_no_mutation_site_in_its_exit_test`, replace
```python
    sites = PythonAdapter().generate_mutation_sites(
        text,
        {loop, overflow},
        operators=("python:compare-swap", "python:boolop-swap", "python:falsy-swap", "python:bool-const-flip"),
        limit=1000,
    )
    assert list(sites) == []
```
with
```python
    operators = ("python:compare-swap", "python:boolop-swap", "python:falsy-swap", "python:bool-const-flip")
    sites = PythonAdapter().generate_mutation_sites(text, {loop, overflow}, operators=operators, limit=1000)
    assert list(sites) == []
    # Positive control: the same call finds sites on the wait loop's mutable test, so the
    # empty result above is not a silent no-op (an unknown operator name also yields []).
    (wait,) = [n for n in span if lines[n - 1].strip() == "while overflowed is None and proc.poll() is None:"]
    assert PythonAdapter().generate_mutation_sites(text, {wait}, operators=operators, limit=1000)
```

---

### W6R-4 — MINOR — DESIGN-GUIDE and CHANGES say the drain "can no longer block forever"; the adjacent wait loop still can under a mutant

**Where:** `assay/docs/DESIGN-GUIDE.md:2074` and `assay/CHANGES.md:15-16`.

**Evidence.** See W6R-1: the `git.py:359` mutants make `_run_bounded` block forever on an overflowing child when there is no deadline. The rewrite removed the sites from the drain loop's exit test, and only there, which is all the brief asked for. The sentences read as a claim about `_run_bounded` as a whole.

**Fix text (exact):**
- In `docs/DESIGN-GUIDE.md`, replace `the \`for\`), so no mutant can make it block forever.` with:
  `the \`for\`), so no mutant of that exit test can make the drain block forever. The wait loop after it keeps its mutable test; the drain test refuses a wait on the overflowing child before its group is killed, so those mutants fail at once instead of idling into \`hung\`.`
- In `CHANGES.md`, replace `the \`git.py\` pipe drain can no longer block forever
  under a mutant (B113/A-466)` with `the \`git.py\` pipe-drain loop's exit test
  no longer offers a mutant that blocks forever (B113/A-466)`.

---

### W6R-5 — MINOR — The W6-LOG red record names the wrong missing anchor for O4

**Where:** `assay/nyxloom-trove/reports/wave-a/W6-LOG.md:16`.

**Evidence.**
- I re-ran the red commit `06e0a790` from a `git archive` extract: 16/16 `StepLimit`, and O4 red with `ValueError: not enough values to unpack (expected 1, got 0)` at **test line 54**.
- Line 54 is the `(loop,) = [... "while selector.get_map():" ...]` unpack. The old header `while selector.get_map() and overflowed is None:` does not contain that colon-terminated substring, so the `if overflowed:` anchor (`:55`) is never reached.
- The substance is right: O4 was red because an anchor was missing, not because sites were present.

**Fix text (exact):** in `W6-LOG.md:16`, replace `fails because the anchor \`if overflowed:\` is missing today (ValueError from the unpack)` with `fails because its first anchor \`while selector.get_map():\` is missing today (the old header continues \`and overflowed is None:\`; ValueError from the unpack at test line 54, before the \`if overflowed:\` anchor is read)`.

---

## Checks that found no defect

### 1. Guard placement (all 8 sites)

The table lists before → after for each site.

| Site | Before → after | Why the right-hand side is strictly greater on real code |
|---|---|---|
| `adapters/go.py:326` | `i = end` → `i = require_advance(i, end)` | `text[i:i+2]=="//"`, so `find("\n", i)` is at least `i+2`, or it is -1 and becomes `n`, which is also at least `i+2` |
| `adapters/go.py:334` | `i = end` → `i = require_advance(i, end)` | `close = find("*/", i+2)`, so `end = close+2` is at least `i+4` |
| `adapters/go.py:356` | `i = end` → `i = require_advance(i, end)` | the backtick search starts at `i+1`, so the result is at least `i+2` |
| `adapters/javascript.py:259` | `i = end` → `i = require_advance(i, end)` | the same two branches as go 326/334 |
| `adapters/sql_lex.py:198` | `i = end` → `i = require_advance(i, end)` | `source[i:i+2]==b"--"`, so the result is at least `i+2` |
| `adapters/sql_lex.py:281` | `i = close + len(delimiter)` → `i = require_advance(i, close + len(delimiter))` | `close` is at least `tag_end`, which is at least `i+2` |
| `adapters/go_modfile.py:393` | `index = length if newline == -1 else newline` → `index = require_advance(index, length if newline == -1 else newline)` | `startswith("//", index)`, so the result is at least `index+2` |
| `isolation.py:1406` | `index = oid_end` → `index = require_advance(index, oid_end)` | `oid_end = nul+21` is at least `index+22` (the name is non-empty) |

- Imports match the brief (`go.py:203`, `go_modfile.py:77`, `javascript.py:176`, `sql_lex.py:64`, `isolation.py:52`).
- Offsets never mix units: go, javascript and go_modfile use `str`/char offsets throughout, while `sql_lex` and `isolation` use `bytes` offsets.
- No guarded assignment sits on a lookahead or on a state change that consumes nothing.
- **Differential fuzz** (`w6review/fuzz.py`):
  - 6 targets × 20,000 random inputs, plus edge cases;
  - alphabet: delimiters, `\x00`, `é`, `𝄞`, ` `, and multi-byte UTF-8 in the SQL bytes;
  - covers the empty string, a final token at EOF, and unterminated forms;
  - result: **0 guard trips**, and a digest identical between W6 and base `20846716` (`1c96f0b9…`).

### 2. The 16 guard cases

- **Red** (my re-run of `06e0a790`): 16/16 `StepLimit`.
- **Green:** 16/16 raise the guard message.
- Per-case attribution by traceback frame shows each case trips its intended site, and together they cover all 8 sites:
  - go-292 → go.py:356;
  - go-321 and go-323 → :326;
  - go-330 → :334;
  - gomod → :393;
  - js ×3 → :259;
  - sql-193 ×3 and sql-195 → :198;
  - sql-270 and sql-273 → :281;
  - iso ×2 → :1406.
- The line budget counts lines, not seconds. `sys.settrace` and `sys.modules` are restored. Coverage runs on the `sysmon` core on 3.14, so tracing does not collide.

### 3. The git.py drain rewrite

- The new code has the same meaning as the old: the `if overflowed: break` sits at the `while` body's indentation (`git.py:357-358`), and `overflowed` is `None` or a non-empty tuple.
- Exit conditions:

| Situation | Result |
|---|---|
| Normal EOF | loop ends; `completed=True`; reaped |
| Exactly the limit | no overflow, because the check is strict (unchanged) |
| One byte over | overflow → break → group killed in the `finally` → pipes closed → reaped → raise |
| Child keeps writing after overflow | SIGKILL of the group in the `finally`; no pipe deadlock |
| Child closes stdout but keeps running | stderr is still drained; with no deadline, `proc.wait()` blocks until exit (pre-existing, unchanged) |
| Signal | the exception propagates through the `finally`, which kills and reaps |

- No new mutation site was added; the two header sites were removed (`generate_mutation_sites` over `_run_bounded`).

### 4. Exclusion map

- `grep` gives `378` and `1344` (base: `376`, `1342`). The pragma line text is byte-identical; only the numbers moved, by +2.
- The fixture `[378, 379, 1344, 1345]` equals coverage's raw `excluded_lines` for `git.py` in my focused run.
- No pragma was added anywhere (CD29).

### 5. Coverage

- Focused run with `--cov=assay.git --cov-branch`: lines 357 and 358 are executed, and both arcs `357->335` and `357->358` are covered.
- The only miss is `git.py:459` with arc `458->459`, which is `raise _git_failed("no .git marker found in any ancestor …")`. It was `:457` in the base, is pre-existing, and moved by the +2 shift.
- The miss comes from `tests/core/test_git_boundary.py:137` failing because `/tmp/.git` exists on this host.

### 6. Import contracts

- `tests/core/test_import_contracts.py` passes.
- `assay.errors` is in `ADAPTER_DEPS`, so the new `javascript → errors` and `sql_lex → errors` edges are allowed.
- `go.py` already imported `errors`.

### 7. Layout

- The four new files sit where `expected_dir` puts them.
- `tests/scanner_progress_support.py` is a root support file with no `__init__`, which is consistent with CD23 (amended) and W4's "support files" rule.
- Single-file and in-folder runs resolve the import.
- New files are tracked (CD45).

### 8. Process slips

- `src/assay/errors.py` and `tests/test_errors.py` both parse.
- Each has exactly one definition, with no stray EOF or heredoc markers and correct indentation.
- `require_advance` is in `__all__`.

### 9. README and CONSUMERS

Leaving both unchanged is right:
- The guard cannot fire on unmutated code (per-site proof plus the fuzz). It fires only inside assay's own mutated R2 snapshots, where it becomes a test failure and so `killed`: any non-zero exit maps to FAIL → `killed` (`mutation.py:1934-1948`).
- `assay.errors` is not a documented consumer surface.
- CD30 holds.

### 10. Combined-axis attacks

- **Scanner guard → runner classification:** no consumer SQL or JS input reaches the guard, and a mutated scanner is always `killed`. That holds even when the guard fires at collection time, because pytest exit 2 is non-zero and maps to FAIL. It is never `crashed`, which is reserved for exec failure.
- **Drain rewrite + liveness:** this attack produced W6R-1.

### 11. Tests: other points

- There is no elapsed-time assertion anywhere.
- O4b patches `git.subprocess.Popen`, which is the global `subprocess.Popen`, through monkeypatch. That has precedent at `test_b105_git_process_boundaries.py:198/234` and is restored.
- No `__getattr__` proxies are patched.

## Notes outside W6 scope (for the controller; not W6 findings)

- **Pre-existing test bug, suggested for the assay backlog.** `tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` depends on the host having no `.git` in any ancestor of the temp directory. That makes the filesystem an uncontrolled input (§3b E), and it is why `git.py:459` misses on this host. The `/tmp/.git` artefact dates from Sep 28 22:17.
- **Brief inaccuracy.** The P2 brief says the `git.py` wait loop "is timeout-bounded". It is not when `remaining is None` (`git.py:361-363`).
- **Footprint disclosure.** My focused `--cov` run wrote (or overwrote) the git-ignored `assay/.coverage` data file in the W6 worktree. There is no tracked change, and no edits or commits were made.
