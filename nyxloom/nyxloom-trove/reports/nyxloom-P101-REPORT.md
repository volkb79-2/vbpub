# nyxloom-P101-retire-tier-band -- REPORT

**Status: implementer pass complete, awaiting a fresh adversarial reviewer.**
All 10 Work items done. Direct evidence below for O1-O5 (local test runs) and
O6 (real containerized `tester-unified` gate, verdict filled in once the run
completes -- see "Gate run" at the end).

## What changed

- `src/nyxloom/adapters.py`: replaced the comment block above `_LOW_BAND`
  (Work item 2, pinned verbatim); dropped the `tier` parameter from
  `compute_review_depth_directive` and collapsed the band computation to the
  unconditional scope-size ternary (Work item 3). `_LOW_BAND`, `_HIGH_BAND`,
  `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`, `_RIGOROUS_ASSERTS` all kept.
- `src/nyxloom/effects_review.py`: dropped the stale
  `tier=first_fm.tier if first_fm is not None else None,` kwarg from the sole
  production caller (Work item 5). `first_fm` and both remaining kwargs
  unchanged.
- `tests/test_adapters.py`: dropped `tier=` from all 12 pre-existing call
  sites; renamed `test_compute_review_depth_directive_scope_size_fallback` to
  `..._scope_size_band` and deleted its now-meaningless third assertion block;
  updated the four non-call docstring/name hits; added three new tests for
  O1/O3/O4 (Work item 4). Added `import inspect`, `import re`.
- `tests/test_effects_review.py`: added
  `TestReviewDepthReachesTheRealCaller` (O5), mirroring `TestWaveLeaseUnion`'s
  full-path harness (Work item 6).
- `docs/plan-next-batches.md`: appended the pinned one-line supersession note
  after BATCH C's final line (Work item 9). Historical `✅ DONE` record left
  untouched otherwise.
- `nyxloom-trove/backlog/NL-7-*.md`: replaced the "Observed mechanism and
  reproduction" section with the pinned corrected text; status `open` ->
  `fixed` via the CLI (Work item 8a/8b).
- `nyxloom-trove/backlog/INDEX.md`: regenerated via `nyxloom backlog index`
  (never hand-edited); NL-7 moved from the `open` group to the `fixed` group.
- `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`,
  `tests/test_core_characterization.py`: VERIFY-ONLY, not edited. Measured
  real `wc -l`: `adapters.py` 1204 (projected ~1205), `effects_review.py` 638
  (projected 638, exact), `tests/test_adapters.py` 2826 (projected ~2775, well
  inside the ~277-line tolerance band). `tests/test_effects_review.py` is
  recorded `new` and skipped by the tolerance loop entirely.

## Oracle evidence

### O1 -- suppressor gone, exact signature, threshold boundary

`tests/test_adapters.py::test_compute_review_depth_directive_signature_and_threshold_boundary`
(new). Ran in isolation:

```
$ python3 -m pytest tests/test_adapters.py -k signature_and_threshold_boundary -v
collected 1 item
tests/test_adapters.py .                                                 [100%]
1 passed
```

Asserts: `compute_review_depth_directive(scope_touch=[6 paths],
gate_asserts=RIGOROUS)` contains `high-complexity`; the same call with exactly
5 paths returns `""`; `tuple(inspect.signature(adapters.
compute_review_depth_directive).parameters) == ("scope_touch",
"gate_asserts")` -- exact tuple equality, not a bare absence check.

**Mutation evidence** (Work item 7, run by hand, reverted -- see the LOG for
the full transcript table):
- **B1** (restore `_TIER_BAND` + required `tier` param + `.get` lookup) ->
  `TypeError: compute_review_depth_directive() missing 1 required positional
  argument: 'tier'` -- fails immediately, before the assertions even run.
- **B1'** (keep the suppressor, rename the param to `handoff_tier` WITH a
  default so the call still succeeds) -> the 6-path/5-path calls both pass
  (suppressor fully intact for real dispatches), but:
  ```
  E       assert ('scope_touch...handoff_tier') == ('scope_touch...gate_asserts')
  E         Left contains one more item: 'handoff_tier'
  ```
  Proves the check is exact-tuple equality, not mere absence of a parameter
  literally named `tier`.
- **B2** (`>` -> `>=` in the threshold comparison) ->
  ```
  E       assert 'This is a hi...ated oracles.' == ''
  ```
  (the 5-path case wrongly fires). Catches the off-by-one independently of
  the signature half.

### O2 -- neutral case byte-identical (LOAD-BEARING SAFETY ORACLE)

`tests/test_adapters.py::test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc`
(pre-existing, updated to drop `tier=`). Ran in isolation:

```
$ python3 -m pytest tests/test_adapters.py -k byte_identical_to_pre_batchc -v
1 passed
```

Asserts a small `scope_touch` + a rigorous gate returns `""`, and
`build_dispatch`'s prompt for both the default-omitted and explicit-empty
forms equals the literal `_PRE_D1_SIMPLE_REVIEW_PROMPT` snapshot
(`tests/test_adapters.py:1890`), with `"Review depth:"` absent from both.

**Mutation evidence -- B7'** (flip the new ternary's `else _LOW_BAND` to
`else _HIGH_BAND`):
```
$ python3 -m pytest tests/test_adapters.py::test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc -q
E       assert 'This is a hi...ated oracles.' == ''
FAILED
```
The neutral case wrongly returns a non-empty directive -- the oracle catches
it at its very first assertion (before even reaching the snapshot-equality
lines), which still isolates O2: the oracle IS this test, and the test fails
because of B7'.

Per Work item 3, the `if band < _HIGH_BAND and not shallow: return ""` early
return is a **provable pure fast path** after this edit -- enumerated at carve
time over 0/1/4/5/6/8/30 paths x rigorous/shallow/empty/None asserts, zero
differing cases from removing it (`reasons` is empty and `" ".join([])` is
`""` in exactly the case it covers). It is kept deliberately, as instructed,
because it names the neutral case legibly at the top of the function -- **not
because it is load-bearing behavior**. Stating this explicitly per the
handoff's own instruction, so a downstream reviewer does not flag it as
unreachable-in-effect dead code and "clean it up".

### O3 -- no tier-shaped string literal anywhere in adapters.py's AST

`tests/test_adapters.py::test_adapters_module_carries_no_tier_shaped_string_literal`
(new). Ran in isolation:

```
$ python3 -m pytest tests/test_adapters.py -k module_carries_no_tier_shaped -v
1 passed
```

Walks every `ast.Constant` whose value is a `str` in `adapters.py` (parsed
fresh from disk, path resolved via
`Path(__file__).resolve().parents[1] / "src" / "nyxloom" / "adapters.py"`) and
asserts none matches `(implement|review|carve)-\d+`; separately asserts
`not hasattr(adapters, "_TIER_BAND")`.

**Mutation evidence:**
- **B3** (`_BANDS = {"implement-1": 1, "implement-2": 2, "implement-3": 3}`)
  -> `AssertionError: tier-shaped string literal(s) survive in adapters.py:
  ['implement-1', 'implement-2', 'implement-3']`.
- **B3'** (`_BANDS = dict(zip(("implement-1","implement-2","implement-3"),
  (1,2,3)))` -- measured to EVADE an `ast.Dict`-keys-only check) -> same
  failure, same three literals detected. Proves the widened string-constant
  scan catches what a narrower `ast.Dict`-keys check would miss.
- **B3''** (revert ONLY the line-209-era docstring parenthetical, back to
  `"...BELOW high (i.e. implement-1/implement-2, or the fallback's
  small-scope case) AND..."`) -> the whole function docstring (one
  `ast.Constant` node) is flagged as containing `implement-1`/`implement-2`.
  Proves the widened scan covers Work item 3's docstring edit too, not just
  the dict.

### O4 -- pinned comment present verbatim, anchored, old text gone

`tests/test_adapters.py::test_adapters_carries_the_pinned_nyxloom_p101_band_comment_exactly`
(new). Ran in isolation:

```
$ python3 -m pytest tests/test_adapters.py -k pinned_nyxloom_p101_band_comment -v
1 passed
```

Asserts `src.count(PINNED) == 1` AND `src.index(PINNED) <
src.index("def compute_review_depth_directive")`, PLUS
`src.count('"mechanical/cheap vs. hard" banding routes.host.toml already uses
for') == 0` AND `"per _TIER_BAND" not in src`. Independently verified
byte-exactness of the pinned literal against the handoff's own pinned block
via a Python string-containment probe before committing (`pinned in
adapters.py source` -> `True`, count 1, index 9067 < index 10607 for
`def compute_review_depth_directive`).

**Mutation evidence:**
- **B4** (reword "It was a one-way suppressor, never a trigger." to "It only
  ever suppressed, never triggered anything.") -> `assert src.count(pinned)
  == 1` -> `0 == 1`. The verbatim check alone catches a same-meaning
  paraphrase.
- **B4'** (remove the pinned block from its original position -- replacing it
  with just the bare `_LOW_BAND`/`_HIGH_BAND`/`_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`
  lines -- and paste the FULL block verbatim at the END of the file) ->
  `assert src.index(pinned) < src.index("def compute_review_depth_directive")`
  -> `65188 < 9466` is False. The containment check alone would have passed;
  only the anchor catches this.
- **B5** (leave the OLD false `"mechanical/cheap vs. hard" banding
  routes.host.toml already uses for..."` sentence in an extra comment line
  directly above the pinned block, WITHOUT touching the pinned block's own
  bytes) -> the pinned-containment and anchor checks BOTH still passed;
  ```
  E       assert 1 == 0
  E        +  where 1 = ...count('"mechanical/cheap vs. hard" banding routes.host.toml already uses for')
  ```
  Isolates the absence half specifically -- proving the two absence
  assertions are not redundant with the verbatim check.

### O5 -- scope.touch reaches the directive through the REAL caller

`tests/test_effects_review.py::TestReviewDepthReachesTheRealCaller::test_scope_touch_size_drives_review_depth_through_the_real_caller`
(new). Ran in isolation:

```
$ python3 -m pytest tests/test_effects_review.py::TestReviewDepthReachesTheRealCaller -v
1 passed
```

Drives the REAL `ReviewEffector.launch_review` (monkeypatching only
`effects_dispatch.frontmatter_for`, `wrapper.launch_detached`,
`effects_review.adapters.build_dispatch` -- a kwarg-capturing fake, not a
fixed return -- and `effects_review.config.Routes.load`), asserting BOTH
directions: a 6-path `scope.touch` frontmatter stub captures a `review_depth`
containing `high-complexity`; a 1-path stub captures one that does NOT.

**Mutation evidence -- B6'** (in `effects_review.py`, pass a hardcoded
`["a","b","c","d","e","f"]` instead of `first_fm.scope.touch if first_fm is
not None else []`):

```
$ python3 -m pytest tests/test_effects_review.py::TestReviewDepthReachesTheRealCaller tests/test_effects_review.py::TestWaveLeaseUnion -q
FAILED tests/test_effects_review.py::TestReviewDepthReachesTheRealCaller::test_scope_touch_size_drives_review_depth_through_the_real_caller
1 failed, 1 passed
```

The small-scope direction wrongly fires `high-complexity` (the hardcoded list
ignores the real 1-path frontmatter). Crucially, `TestWaveLeaseUnion` -- the
PRE-EXISTING test that would catch a stale `tier=` kwarg via `TypeError` --
stays GREEN under B6', confirming B6' isolates O5 alone rather than riding on
an unrelated pre-existing test (the rejected old-B6 trap the handoff names
explicitly).

### O6 -- whole gate green + BLG-findings-only lint read

See "Gate run" below for the verdict, read in a separate step from the run.

**BLG findings (Work item 8(c)):**

```
$ python3 exec-nyxloom.py lint > lint-output.txt 2>&1; echo "EXIT=$?" >> lint-output.txt
```

Read in a SEPARATE step: `EXIT=1`, 794 output lines total. `grep -c "BLG"` on
the captured output -> **0 BLG2/BLG3 findings**. Per-rule histogram (`grep -oE
'L[0-9]+ (error|warning)' | sort | uniq -c`): 164 L7 error, 92 L14 error, 56 L1
error, 30 L10 warning, 23 L11 error, 10 L4 warning, 6 L7 warning, 5 L12 error,
2 L10 error -- 342 errors / 444 warnings total, matching the handoff's own
documented baseline (`handoff_globs` sweeping in `ciu-P04..P06`,
`topos-P17x`, and `CORE-REDESIGN-SESSION-HANDOFF-*` files authored for other
tools) exactly, rule for rule. **This is the documented pre-existing baseline,
not a finding this package caused** -- not pasting the 794-line output per the
handoff's own instruction.

Also ran `tests/test_core_characterization.py -q` -> 26 passed, confirming the
ownership-inventory tolerance rows all hold with the real post-edit line
counts (no `escalate_if` re-measurement trigger fired).

## Deviations / ambiguities

None from the handoff's Work items themselves -- all 10 were followed as
specified, all pinned text reproduced byte-for-byte (verified programmatically
before committing in every case, catching one line-wrap mistake on the NL-7
prose before it was committed -- see the LOG).

One minor procedural note: an opportunistic local `pytest tests -q` (serial,
no `-n auto`, as a belt-and-braces pre-gate sanity check beyond what the
handoff requires) was started but hit its own 300s timeout before finishing --
not a failure signal, just an incomplete extra check; the individually-run
oracle tests and `test_core_characterization.py` above already cover what
mattered, and the real containerized gate below is the actual O6 evidence.

## Gate run

<!-- filled in once the real containerized gate has run and its verdict has
     been read in a separate step -->
