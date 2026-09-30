Scope: item 3 only, the hang-prone loop inventory for src/assay. It is read-only; I only ran AST scans and a mutant simulation with a step cap.

## 3. Hang-prone loops

**Summary.** src/assay has 50 `while` loops, 2 `iter(callable, sentinel)` loops and 2 self-recursive functions. 14 single-operator mutants can make a loop never finish, and 2 more can only do so on unusual input. A simulation confirmed all four hang sites you already knew about and found the same bug shape in the JavaScript, SQL and go.mod scanners. One shared helper that refuses a cursor that did not move forward, used at 8 assignment sites, turns every scanner hang into a fast `AssertionError`, which the suite reports as a kill. It adds one branch, and a direct unit test covers both sides of it.

### 3.0 What the mutation operators can do (src/assay/adapters/python.py)

| Operator | What it swaps | Can it reach a loop? |
|---|---|---|
| compare-swap | `<`↔`<=`, `>`↔`>=`, `==`↔`!=`, `is`↔`is not` (python.py:454-463). `in`/`not in` are deliberately excluded (python.py:447-453). | Yes, in loop conditions and in any comparison inside a loop body. |
| boolop-swap | `and`↔`or`, one site per operator token (python.py:468, 570-590) | Yes |
| bool-const-flip | every `True`/`False` literal (python.py:714-715), including `while True` | `while True`→`while False` only skips the body, so it causes a crash or wrong result, never a hang. |
| falsy-swap | only the value of a `return` statement: `None`→`[]`, `0`/`""`/`b""`/`[]`/`()`/`set()`/`{}`→`None` (python.py:620-678) | Only through a helper's return value. For example `_scan_quoted_literal`'s `return None` (go.py:276/280) becoming `[]` gives `_blank(chars, i, [])`, a TypeError. That is a crash, not a hang. None of the risky sites below is fed by a falsy-swap. |

Mutants are generated from a full `ast.walk` of every line eligible in `whole_target` mode (python.py:756-773).

### 3.1 The four known go.py sites, mechanism confirmed

I simulated each mutant against representative inputs, with a cap of 200k traced lines (scratch script `sim_hang.py`).

| Site | Mutant | What happens | Input that hangs |
|---|---|---|---|
| go.py:292 (`_scan_raw_string`) | `end == -1` → `end != -1` | When no closing backtick exists, it returns `end + 1` = **0** instead of `None`. The caller at go.py:353 does not see `None`, so go.py:356 sets `i = 0`. The scan restarts and reaches the same backtick forever. | `"package x\nvar s = \`open"`: HANG. A closed raw string returns `None` instead, so the caller returns `None` and nothing hangs. |
| go.py:321 | `two == "//"` → `!=` | Every position that does not start `//` enters the line-comment branch. At a `\n` character, `text.find("\n", i)` returns `i`, so go.py:326 sets `i = i`. The cursor stops moving. | Any input containing a newline. All 5 sample inputs hung. |
| go.py:323 | `end == -1` → `!=` | For a trailing `//` comment with no newline, `end` stays -1 and go.py:326 sets `i = -1`. The next pass reads `text[-1]`, then `i += 1` brings it back to 0, and the scan loops forever. | `"package x\n// c"`: HANG |
| go.py:330 | `close == -1` → `!=` | For an unterminated `/*`, `end = close + 2` = **1**, so go.py:334 sets `i = 1`. That is a step backwards from any opener at offset 2 or later, and a stall at offset 1. | `"package x\n/* open"`: HANG |

These are pure CPU spins: the `chars` list does not grow.

### 3.2 Full inventory

- **Classes:** SAFE, AT-RISK (a single mutant hangs on input the current suite plausibly uses), LATENT (it hangs only on an input shape not found in the suite), TIMEOUT-BOUNDED (it waits on a clock or deadline).
- **Cursor movement:** "+k" means the cursor moves forward by at least k on every path that continues the loop.

| File:line | Function | Condition | How the cursor moves | Single-mutant risk | Class |
|---|---|---|---|---|---|
| adapters/go.py:270 | `_scan_quoted_literal` | `i < n` | +1/+2, or returns i+1 | `<=` gives IndexError; any other swap only returns earlier. Always returns a value greater than the start. | SAFE |
| **adapters/go.py:319** | `_strip_comments_and_literals` | `i < n` | `i = end` at :326/:334/:342/:349/:356; `+1` at :358 | **:321, :323, :330, and :292 via :356** (3.1). :328/:337/:351 `!=` still move forward (simulated OK). :339/:353 `is not` gives TypeError. | **AT-RISK** |
| adapters/go.py:388 | `_scan_signature_for_body` | `i < n` | +1, or returns | Returns a position of at least `start` = caller's i+4 | SAFE |
| adapters/go.py:426 | `_scan_for_top_level_func_body` | `i < n` | +1, or `i = new_index` (at least i+4) | :440 `!=` still calls `(i+4)` and moves forward | SAFE |
| adapters/go_modfile.py:196 | `_self_and_ancestors` (`while True`) | – | `.parent` until `PurePosixPath(".")` | :198 `!=` returns early. The input is always relative (go_modfile.py:153). | SAFE |
| adapters/go_modfile.py:246, :250 | `_module_argument` | `index < len and tok == "newline"` | +1 | `<=` or `or` gives IndexError | SAFE |
| adapters/go_modfile.py:347 | `_unquote` | `index < len(body)` | +1/+2, or raises | `>=`→`>` gives IndexError | SAFE |
| **adapters/go_modfile.py:382** | `_tokens` (generator) | `index < length` | `index = length if newline == -1 else newline` (:393) | **:393 `newline == -1` → `!=`**: for a trailing `//` comment with no newline, index becomes -1 and the scan re-lexes from 0 forever. It yields tokens without end into `list(...)` at go_modfile.py:211, so **memory grows without limit**. Simulated HANG on `"module x\n// c"`. | **AT-RISK** (trigger input not found in tests/test_adapters_go_modfile.py by grep) |
| adapters/go_modfile.py:410 | `_tokens` inner ident scan | `index < length` | +1, or break | The first character always moves forward, because the outer checks exclude space, punctuation and `//` | SAFE |
| adapters/go_modfile.py:431 | `_scan_string` (`while True`) | – | +1/+2, or raise/return | :432 `>` gives IndexError; :435/:440/:442 swaps return or raise early | SAFE |
| **adapters/javascript.py:241** | `_strip_comments` | `i < n` | `i = end` (:258), `+1` (:253) | **:243 `two == "//"`→`!=`** (hangs on any multi-line input, simulated). **:245 `end == -1`→`!=`** (trailing `//`). **:249 `close == -1`→`!=`** (unterminated `/*` at offset 1 or later). Same bugs as go.py. | **AT-RISK** |
| adapters/javascript.py:389 | `_top_level_statements` | `index < length` | +1, or `index = end` (greater than index) | :393 `is not` gives TypeError | SAFE |
| adapters/javascript.py:424 | `_skip_literal` | `index < length` | +1/+2, or return | Returns at least start+2 | SAFE |
| adapters/sql.py:130 | `_find_matching_close_paren` | `i < n` | +1 | `<=` gives IndexError | SAFE |
| adapters/sql.py:151, :154 | `_preceding_word` | `j > 0 and …` | −1 | `>=`/`or` stop at an empty slice or IndexError | SAFE |
| adapters/sql.py:166, :179, :371, :459, :467 | small whitespace/word scans | `j < n and …` | +1 | `<=`/`or` read an empty slice (b"") or raise IndexError | SAFE |
| adapters/sql.py:226 | `_unused_string_literal` (`while True`) | – | `n += 1` | The exit test uses `not in`, which is not in the operator list; `existing` is finite | SAFE |
| adapters/sql.py:318 | `_extend_references_clause` (`while True`) | – | `end = action.end()` | `_ON_ACTION_RE` (sql.py:290-293) always consumes at least 4 bytes. :320 `is not` gives AttributeError. | SAFE |
| adapters/sql_lex.py:136 | `_scan_quoted` | `i < n` | +1/+2, or return i+1 | Always returns a value greater than start | SAFE |
| adapters/sql_lex.py:167 | `_find_dollar_tag_end` | `j < n and ident` | +1 | IndexError | SAFE |
| **adapters/sql_lex.py:189** | `_lex_once` | `i < n` | `i = end` (:197), `i = j` (:220), `i = end` (:240/:252/:264), `i = close + len(delim)` (:280), `+1` | **:193 `b == _DASH`→`!=`, and both `and`→`or` sites on :193**: at a `\n` followed by `-`, `find("\n", i) == i`, so the cursor stalls. The committed corpus tests/fixtures/mutation/sql/dstdns-21-create-workflow-corpus.sql contains `b"\n-"`. **:195 `end == -1`→`!=`**: a trailing `--` with no newline sets i = -1 and the scan loops (tests/test_adapters_sql_lexer.py:57 uses exactly `b"SELECT 1; -- trailing, no newline"`). **:273 `close == -1`→`!=`**: sets i = len(delim) − 1 and appends to `dollar_bodies` on every pass (:278), so memory grows. Simulated HANG on `b"SELECT 1; $$ open"`; the suite's `b"A $foo$ never closed"` does not hang. **:270 `is not None`→`is None`**: `source.find(source[i:], None)` can find an earlier masked copy of the rest of the file and step backwards (simulated HANG on `b"-- $1\nSELECT $1"`). | **AT-RISK** (:193×3, :195). **LATENT** (:270, :273). |
| adapters/sql_lex.py:204 | block-comment nesting | `j < n and depth > 0` | +1/+2 | `or` gives IndexError; `>=` still moves forward | SAFE |
| analysis.py:656 | `_report_blocks` | `while remaining:` | `remaining -= len(block)`, and it raises on an empty read | No mutation sites | SAFE |
| analysis.py:52, provenance.py:88 | `iter(read, b"")` | EOF sentinel | – | Regular files only (analysis.py:47) | SAFE |
| canary.py:750 | `run_isolated_canaries` | `index < len(declared)` | +1, or break/raise | `<=` gives IndexError | SAFE |
| diff.py:160 | `_unquote_git_path` | `index < length` | +1/+2/+4, or raise | `>=`→`>` gives IndexError | SAFE |
| **git.py:335** | `_run_bounded` pipe drain | `selector.get_map() and overflowed is None` | Stops when the selector's fds are unregistered | **`and`→`or`**: once both pipes hit EOF, `selector.select(None)` runs on an *empty* selector. CPython 3.14 selectors.py:435-452 turns `None` into -1 and calls `poll(-1, max(0, 1))`, which **blocks forever** whenever `remaining is None`, the default for `git.run/_run_bytes/...` (git.py:633, 707, 753…). **`is None`→`is not None`**: the drain is skipped, and git.py:360 `proc.wait()` then deadlocks on any git output over about 64 KiB. | **AT-RISK** (blocking; ends only by a deadline when `remaining` is given) |
| git.py:357 | `_run_bounded` wait | `overflowed is None and proc.poll() is None` | – | `and`→`or` spins until `_sample_remaining` raises when there is a deadline. With `None` it takes the `proc.wait(); break` path (:359-361). | TIMEOUT-BOUNDED |
| git.py:443 | `_nearest_git_marker` (`while True`) | – | `candidate = parent`, raises at the filesystem root | :456 `!=` raises early | SAFE |
| git.py:886 | `dirty_paths` | `index < len(tokens)` | +1 or +2 | IndexError | SAFE |
| git.py:1307 | `_p22_pump` | `while selector.get_map():` | – | No sites in the condition; an empty `select` raises `_p22_timeout` (:1309-1310) | TIMEOUT-BOUNDED |
| git.py:1501 | `relay` | `while view:` | `view[written:]` | No sites | SAFE |
| isolation.py:515 | `_BatchReader.feed` (`while True`) | – | Each pass consumes at least 1 byte, returns or raises | :533 `!=` calls `_start` on a partial header, but the next pass always consumes a byte (via remaining or trailer) or raises. :556 is harmless. | SAFE |
| **isolation.py:1386** | `_parse_tree` | `index < length` | `index = oid_end` (at least index+22) | **:1388 `space == -1`→`!=`** and **:1392 `nul == -1`→`!=`** can set `oid_end` to at most the current index (`find(b"\0", 0)`, or `oid_end = 20`), and `records.append` then grows without limit. Simulated against every case in tests/fixtures/isolation/tree_grammar.json: **no hang**, and the `accepted` case kills both. | **LATENT** (memory-growing if triggered) |
| isolation.py:1572 | `_resolve_declared_omission_modes` | `while frontier:` | `parts[1:]` shrinks by one each pass | Mutants only raise | SAFE |
| isolation.py:1679 | `_build_manifest` BFS | `while pending:` | The tree DAG is finite; capped by `max_entries` (:1703) | :1753 `!=` sends blobs to the `kind != "tree"` raise | SAFE |
| liveness.py:608 | `tree_cpu_seconds` DFS | `while stack:` | `visited` set; the process tree is finite | Only `in`/`not in` sites, which are not mutated | SAFE |
| liveness.py:1194 | `_CpuSampleHistory.add` | `len > 1 and …` | `popleft` | `>=`/`or` gives IndexError | SAFE |
| **liveness.py:1459** | `LivenessRunner._monitor` (`while True`) | – | Exits on child exit (:1461), `hung` (:1516) or `timeout` (:1530) | **:1530 `timeout is not None`→`is None`**: the timeout never fires. In tests/test_cli_run.py:586 (busy-loop child `while True:` at :613, 35 s budget) the child's CPU keeps growing, so `hung` never fires either, and the *test* hangs. :1472/:1480/:1497 swaps only disable `hung`, so the timeout still ends those runs. | TIMEOUT-BOUNDED, except :1530, which is AT-RISK at test level |
| mutation.py:426 | `byte_offset` | `remaining > 0` | −1 | `>=` makes one extra `index`, then stops | SAFE |
| mutation.py:2895 | `_execute_mutation_jobs` | `index < total and fatal is None` | `index = wave[-1] + 1` | `<=`/`or` gives IndexError on an empty wave | SAFE |
| runner.py:677 | `_bounded_tail` | `cutoff < len and cont-byte` | +1 | IndexError, or runs to the end | SAFE |
| runner.py:977 | heartbeat `_tick` (daemon thread) | `not stop.wait(interval)` | stop Event | No sites | SAFE |
| safeio.py:395 | `_safe_bounded_read` | `remaining > 0` | `-= len(chunk)`, break on empty | `>=` reads 0 bytes and breaks | SAFE |
| verdict.py:3547 | `refusal_detail` | `cutoff > 0 and cont-byte` | −1 | Stops at an empty slice or IndexError | SAFE |
| analysis.py:684 (recursion) | `references` | JSON depth | – | Mutants do not affect depth | SAFE |
| runner.py:5631→5652 (recursion) | `run_lane` re-entry | – | The re-entry passes `progress_artifact=None, progress_stream=stream` (runner.py:5675-5676) | Every single mutant leaves recursion at most one level deep, or raises TypeError | SAFE |

No `for` loop grows its own iterable (AST check), and there are no `itertools.count` loops. Every `.count(` hit is `str.count`/`list.count`.

**Tally.**
- AT-RISK, 14 mutants:
  - go.py:292, 321, 323, 330
  - javascript.py:243, 245, 249
  - sql_lex.py:193 ×3, 195
  - go_modfile.py:393
  - git.py:335 ×2
  - liveness.py:1530 at test level (counted with the AT-RISK set because it hangs a test)
- LATENT, 4 mutants: sql_lex.py:270, 273; isolation.py:1388, 1392.

### 3.3 What a hang costs today

- **CPU-spinning stalls** (all scanner sites) keep `cpu_growing` true: at least 1.0 s of CPU per 30 s window (liveness.py:523-524, 1494-1498). That means `hung` (liveness.py:1516) never fires, and the candidate is stopped only at `budget_per_candidate_s` = **1559.67 s**. That value is `max(3×baseline, baseline+60)` (mutation.py:1892-1909) with the plan event's baseline_s of 519.891. The candidate lands in bucket `budget_exceeded`, not `killed` (verdict.py:709-716). That is about 3× a normal candidate, which the evidence shows at 522-618 s each.
  - This is the same behaviour that tests/test_cli_run.py:586 pins down.
- **Blocking hangs** (git.py:335 mutants) show no CPU growth. They become `hung` after `expect_next_event_within_s` = 107.7 s (plan event), which is cheaper but still not `killed`.
- **Memory-growing stalls:** go_modfile.py:393 (unbounded `list(_tokens)`), sql_lex.py:273 (`dollar_bodies.append`), isolation.py:1388/1392 (`records.append`). Their RSS grows for up to about 26 minutes, and this host is shared with a production game server.
- **Possible orphaned child (verify):** `LivenessRunner` starts children with `start_new_session=True` (liveness.py:1375). If the liveness.py:1530 mutant hangs the busy-loop test, that test's nested `while True:` child sits in its own process group. The outer kill (liveness.py:1425-1430, `killpg` of the candidate's pgid) does not reach it, so it may survive as an orphan busy loop.

### 3.4 Proposed guard

**One shared helper in `src/assay/errors.py`.**
- errors.py imports only `enum`, `types` and `typing` (errors.py:24-28), so it creates no import cycle.
- It is not in the B105 exclusion map.
- It needs no new entry in `judge.targets`. A new module would, because tests/test_self_lane.py:128-135 requires the targets to equal the discovered `src/assay/**/*.py`, in both lanes of assay.toml.

```python
def require_advance(old: int, new: int) -> int:
    """Return *new*; a scanner cursor that did not move strictly forward is an
    internal defect (never an input property), so refuse instead of spinning."""
    if new <= old:
        raise AssertionError(f"scanner cursor did not advance ({old} -> {new})")
    return new
```

The codebase already raises `AssertionError` for "can't happen" conditions: git.py:261 and cli.py:488. The helper uses an explicit `raise` rather than an `assert` statement, so `python -O` cannot strip it.

**Direct test, added to tests/test_errors.py.** Call `require_advance(3, 4) == 4`, and check that `pytest.raises(AssertionError, match="did not advance")` fires for `new` in `(3, 2, -1)`. This covers both branches. It also kills the helper's only mutant (`<=`→`<`), because `require_advance(3, 3)` must raise.

**Call-site rewrites** (`x = end` becomes `x = require_advance(x, end)`). This adds no branches and no mutation sites. In unmutated code, every one of these values is strictly greater than the cursor.

| Site | Mutants it neutralises | Import to add |
|---|---|---|
| go.py:326 | 321, 323 | go.py:203 add `require_advance` to `from ..errors import …` |
| go.py:334 | 330 | same |
| go.py:356 | 292 | same |
| javascript.py:258 | 243, 245, 249 | `from ..errors import require_advance` |
| sql_lex.py:197 | 193×3, 195 | `from ..errors import require_advance` |
| sql_lex.py:280 (`require_advance(i, close + len(delimiter))`) | 270, 273 | same |
| go_modfile.py:393 (`require_advance(index, length if newline == -1 else newline)`) | 393 | go_modfile.py:77 |
| isolation.py:1406 | 1388, 1392 (latent) | isolation.py:52 |

- **Optional, for uniformity:** go.py:342/349, sql_lex.py:220/240/252/264 and go_modfile.py:406. These are safe today.
- **Effect:** each hang becomes an immediate `AssertionError` in whichever test exercises that path. The candidate finishes in normal suite time and is classified `killed`, instead of burning 1559.67 s as `budget_exceeded`.
- **Exceptions are not swallowed:** none of these paths catches broad exceptions in the adapters. isolation.py:717/2111 `except BaseException` only runs cleanup.

**Why not the other two patterns:**
- **An inline `assert end > i` at each site.** coverage.py adds no branch arc for an `assert`, so coverage is fine. But each site adds its own `>`→`>=` mutant, and correct code can never trip it. That is one unkillable, equivalent mutant per site, which would sit as a survivor in R2. The helper puts the comparison in one place, and one test kills it.
- **A bounded `for _ in range(n + 1): … else: raise`.** In coverage.py 7.16.1 (parser.py:1212-1232 for `while`, and the equivalent `for`/`else` handling), the `else` body can only run when the loop is exhausted, which correct code never does. So each converted loop adds an uncovered line and a partial branch.
  - git.py:250-261 does use this pattern, but its trailing raise is covered by monkeypatching a module constant (tests/test_b105_git_process_boundaries.py:84-85). A scanner's bound is `len(text)`, which cannot be monkeypatched.
  - The only other route is adding `pragma: no cover` lines to tests/fixtures/b105-coverage-exclusions.json. That map is strict and reviewed (enforced by conftest.py:165-212) and currently lists only TYPE_CHECKING lines and git.py:376-377/1342-1343.
- **Coverage background:** coverage.py adds no exit arc for a constant `while True:` (parser.py:1230 `if not constant_test: exits.add(...)`). A `while cond:` loop has an exit arc that must be taken. The helper approach changes no loop structure, so every existing arc stays as it is.

**git.py:335 (blocking, not a cursor problem).** Rewrite the drain so its exit test has no mutation sites:

```python
while selector.get_map():
    timeout = _sample_remaining(remaining)
    for key, _ in selector.select(timeout):
        ...                    # unchanged, including the overflow `break`
    if overflowed:             # truthiness: no compare-swap / boolop site
        break
```

- `overflowed` is either `None` or a non-empty tuple, so its truthiness is exactly `is not None`.
- Existing overflow tests take the `if` branch's True side, and ordinary runs take the False side.
- **This shifts line numbers in git.py.** tests/fixtures/b105-coverage-exclusions.json pins git.py `[376, 377, 1342, 1343]`, so the map must be updated in the same commit, or the preflight's `_validate_b105_exclusion_inventory` (conftest.py:165-212) fails. No other edit proposed here touches a file in that map.

**liveness.py:1530 (the timeout check itself).** No single site can make a timeout check immune to swapping its own comparison. The fix belongs in the tests. The real-child liveness tests in tests/test_cli_run.py (:586 busy-loop, and the thread-join test around :470) should get an independent watchdog, for example:
- Run `main([...])` in a thread.
- `join(timeout=4 × declared budget)`.
- On expiry, kill the child's process group (it has its own session) and fail.

That turns the 1559.67 s budget burn into a failure of about 140 s, classified `killed`, and clears up the possible orphaned busy-loop child.