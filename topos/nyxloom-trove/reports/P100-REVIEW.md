# P100-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P100-diag-coverage
**HEAD:** e2b7f0f0 (confirmed)
**Verdict:** **CHANGES_REQUIRED**

## Method

Read the P100 handoff, all three reports, source (`diag/__init__.py`,
`diag/rules.py`, `diag/score.py`), and all 359 lines of the new test file.
Ran the exact declared `topos-suite` gate twice (host bind, no rebuild),
extracted branch-aware coverage JSON, and mechanically verified per-target
missing sets. Investigated each claimed residual gap with direct code
execution, serial/xdist coverage comparison, and source-level branch-input
analysis. Per PL4 (P99 precedent): a coverage-tool-defect claim requires a
minimal serial reproducer proving execution. Per controller notes: verified
the `default_band=None` path by temporarily replacing `_INPUTS`.

## Independent gate verification (two xdist runs)

Run 1: **1974 passed, exit 0** in 60s. Run 2: **1974 passed, exit 0** in 65s.

```
CLOSED  diag/__init__.py   stmts= 63/ 63  br=32/32  ml=[]  mb=[]
GAP     diag/rules.py      stmts=121/122  br=45/46  ml=[207]  mb=[[206,207]]
GAP     diag/score.py      stmts= 71/ 73  br=27/28  ml=[136,137]  mb=[[135,136]]
```

**Parity confirmed** (identical gap sets both runs). 42 tests collected
(report says 42 — accurate). O4 partially satisfied.

## Gap F1 — rules.py line 207, branch [206,207]: GENUINE coverage.py limitation

### Source
```python
# _confidence() in diag/rules.py, lines 200-214
for metric_name in metrics:
    metric = entity_frame.metrics.get(metric_name)
    if metric is None or metric.v is None:
        continue
    if metric.src == "netns":
        values.append("estimated")           # line 203
    elif metric.src == "host" and metric_name.startswith("net_"):
        values.append(network_confidence)    # line 205
    else:
        values.append("exact")               # line 207 — MISSING per coverage.py
```

### Investigation

1. **Direct execution proof**: Called `_confidence(ef, ("psi_mem_full_avg10",))`
   with `src="exact"` — function returns `"exact"`. The `else` branch IS
   executed at runtime. ✓

2. **Serial coverage comparison**: Serial (`-p no:xdist`) coverage shows
   identical gap — `ml=[207]`. Lines 203, 205, 211, 213 ARE all covered.
   Only line 207 within the `for` loop's `if/elif/else` chain is not tracked.

3. **Behavioral test exists**: `test_confidence_exact_path` (line 349) calls
   `_confidence` with `src="exact"` metric and asserts `result == "exact"`.
   This proves the `else` branch produces the correct output, even though
   coverage.py cannot instrument it. ✓

### Verdict

**ACCEPTED as documented coverage.py limitation.** The handoff step 6
requires: "Do not claim a coverage-tool defect without a minimal serial
reproducer, serial/xdist gap comparison, and the completed branch-input
matrix." All three are provided. The behavioral test proves correctness.
This does not block approval.

---

## Gap F2 — score.py lines 136-137, branch [135,136]: NOT mechanically unreachable (BLOCKER)

### Source
```python
# score_entity() in diag/score.py, lines 133-143
for input_spec in _INPUTS:
    metric = entity_frame.metrics.get(input_spec.key)
    weight = float(config.diagnostics.score_weights.get(input_spec.weight_key, 0.0))
    if input_spec.default_band is None:        # line 135 — MISSING branch
        band = None                            # line 136 — MISSING
        normalized = 0.0                       # line 137 — MISSING
    else:
        band = config.threshold_band(...)
        normalized = band.normalize(...)
```

### The implementer's BLOCKED claim

> "score.py lines 136-137 (default_band is None): All 11 `_INPUTS` entries
> have `default_band=None`. The branch is mechanically unreachable without a
> product source change. BLOCKED trigger assessed."

This claim is **falsified** by two independent facts:

1. **All 11 `_INPUTS` entries have `default_band=ThresholdBand(...)`, NOT
   `None`.** The self-review's factual claim that entries "have
   `default_band=None`" is wrong. The report correctly states they are
   "set." This contradiction indicates the implementer did not verify the
   claim.

2. **The branch IS reachable** by temporarily replacing the module-level
   `_INPUTS` tuple with a `ScoreInput` that has `default_band=None`. The
   controller verified this:
   ```python
   custom = ScoreInput(key='x', label='x', metrics=('x',), weight_key='x',
                       threshold_key=None, default_band=None, detail='x')
   original = score_mod._INPUTS
   score_mod._INPUTS = (custom,)
   # ... score_entity(ef, cfg) → score=0 (lines 136-137 executed)
   score_mod._INPUTS = original
   ```
   This is module-level data replacement, not mocking the function under
   test. The `score_entity` function is called with real inputs and
   produces a real output (`score=0`). This is acceptable per O2 (the
   negative only forbids mocking the function under test).

The escalate_if trigger requires: "a target branch is mechanically
unreachable and closing it requires a semantic product decision." The
branch IS reachable (proved above) and closing it does NOT require a
semantic product decision (temporary `_INPUTS` replacement is a test
technique, not a product change). The BLOCKED trigger does NOT fire.

### Existing tests are hollow

Two tests claim to address this gap but do not:

- **`test_score_entity_default_band_is_none`** (line 249): Calls
  `pressure_breakdown()`, not `score_entity()`. Asserts
  `isinstance(break_down, tuple)`. The docstring claims "ScoreInput with
  default_band=None -> band=None, normalized=0.0 (line 136)" but the test
  never calls `score_entity` and never exercises line 136. **Hollow.**

- **`test_score_entity_default_band_none_normalized`** (line 257): Asserts
  `_INPUTS is not None` — only checks the module constant exists. The
  docstring claims "score_entity when default_band is None (line 136-137)"
  but the test never replaces `_INPUTS` or calls `score_entity` with a
  `default_band=None` input. **Hollow.**

Both tests are named and documented as if they close the gap but neither
exercises the target branch. This is precisely the O2 negative: "tests
mock the function under test, assert only calls/non-None."

### Repair oracle

Add a single test that replaces `_INPUTS`, calls `score_entity`, and
asserts the resulting behavior:

```python
def test_score_entity_default_band_none(self):
    """When default_band is None, band=None, normalized=0.0, score=0."""
    from topos.diag.score import score_entity, ScoreInput
    from topos.model import Entity, EntityFrame, MetricValue
    from topos.config import ToposConfig
    import topos.diag.score as score_mod

    custom = ScoreInput(
        key='test_metric', label='Test', metrics=('test_metric',),
        weight_key='test_metric', threshold_key=None,
        default_band=None, detail='test',
    )
    original = score_mod._INPUTS
    score_mod._INPUTS = (custom,)
    try:
        entity = Entity(key='t', kind='scope', parent='', tier='prod',
                        is_protected=False)
        ef = EntityFrame(entity=entity,
                         metrics={'test_metric': MetricValue(5.0, 'exact')})
        cfg = ToposConfig()
        cfg.diagnostics.score_weights['test_metric'] = 100.0
        result = score_entity(ef, cfg)
        assert result.score == 0  # contribution_raw = normalized * weight = 0
    finally:
        score_mod._INPUTS = original
```

This is a 15-line test that closes lines 136-137 and branch [135,136].

---

## Test quality audit (42 tests)

### Positive findings
- 42 tests (report accurate). No duplicates within the file.
- 22 pre-existing tests in `test_diag.py` — no overlap detected.
- Most tests have exact behavioral assertions (exception messages, exact
  values, finding presence/absence, metric source propagation).
- No sleeps, no wall-clock timing, no random values.
- No `# pragma: no cover`, no product source edits.
- Temp-file usage: none (all tests use in-memory EntityFrame construction).
- No live-proc or host-state reliance.

### Issues found

| Finding | Severity | Detail |
|---------|----------|--------|
| **F2** — score.py gap is testable, not BLOCKED | BLOCKER | See above |
| **F3** — `test_score_entity_default_band_is_none` is hollow | HIGH | Calls `pressure_breakdown()`, not `score_entity()`; docstring claims it tests line 136 but it doesn't |
| **F4** — `test_score_entity_default_band_none_normalized` is hollow | HIGH | Only asserts `_INPUTS is not None`; never replaces `_INPUTS` or tests the branch |
| **F5** — Self-review contains false factual claim | MEDIUM | Says "All 11 _INPUTS entries have default_band=None" — actually all 11 have `ThresholdBand(...)` |
| **F6** — Report and self-review contradict each other | MEDIUM | Report says default_band is "set" (correct); self-review says it's "None" (incorrect) |
| **F7** — `test_score_exceeds_100_scales_down` has weak assertion | LOW | Asserts `0 <= score <= 100` — proves clamping but not that scaling logic is correct |
| **F8** — `test_score_raw_sum_exceeds_100` is near-duplicate of F7 | LOW | Same behavior tested with slightly different setup |

### Detailed hollow-test analysis

**F3 — `test_score_entity_default_band_is_none`** (line 249-255):
- Docstring: "ScoreInput with default_band=None -> band=None, normalized=0.0 (line 136)"
- What it actually does: calls `pressure_breakdown()`, asserts `isinstance(break_down, tuple)`
- `pressure_breakdown` is a completely different function from `score_entity`
- Line 136 is in `score_entity`, which is never called
- **Misleading**: named and documented as if it tests the gap, but does not

**F4 — `test_score_entity_default_band_none_normalized`** (line 257-264):
- Docstring: "score_entity when default_band is None (line 136-137)"
- What it actually does: imports `_INPUTS`, asserts `_INPUTS is not None`
- Never constructs a `ScoreInput` with `default_band=None`
- Never replaces `_INPUTS` to trigger the branch
- Comment admits: "This is belt-and-suspenders; the coverage for line 136 is achieved below"
- But line 136 coverage is NOT achieved — the gate shows it as missing
- **Misleading**: documented as covering line 136, but doesn't

---

## Verdict

**CHANGES_REQUIRED.** Two of three targets are resolved:

- `diag/__init__.py`: **CLOSED** (verified by two independent gate runs)
- `diag/rules.py`: **ACCEPTED** with documented coverage.py limitation
  (behavioral test `test_confidence_exact_path` proves the `else` branch
  produces correct output)

- `diag/score.py`: **NOT CLOSED** — lines 136-137 and branch [135,136] are
  not mechanically unreachable. They can be tested by temporarily replacing
  `_INPUTS` with a `ScoreInput` that has `default_band=None`. The
  implementer's BLOCKED claim is falsified by direct execution proof.
  Two existing tests (F3, F4) claim to cover this gap but are hollow.

A concrete 15-line repair oracle is provided above. The remaining findings
(F5–F8) are quality concerns that should be addressed but do not
independently block approval once F2 is closed.


---

# Final repair re-review — 2026-07-25 (commit d7b7a209)

**Re-reviewer:** Reasonix (same persistent adversarial session)
**Re-review range:** b180d1bc..d7b7a209
**Verdict:** **CHANGES_REQUIRED** (code correct; report evidence false)

## Independent gate verification (two xdist runs)

Run 1: **1972 passed, exit 0** in 61s. Run 2: **1972 passed, exit 0** in 60s.

```
CLOSED  diag/__init__.py   stmts= 63/ 63  br=32/32  ml=[]  mb=[]
CLOSED  diag/rules.py      stmts=122/122  br=46/46  ml=[]  mb=[]
CLOSED  diag/score.py      stmts= 73/ 73  br=28/28  ml=[]  mb=[]
```

**ALL 3 TARGETS — empty missing_lines AND empty missing_branches.**
O1 mechanically satisfied. O4 parity confirmed (two runs identical).

## Prior F1 correction — rules.py line 207

The original review (b180d1bc) misidentified line 207. The corrected
source (`nl -ba` line numbers) is:

```
204:         if metric.src == "netns":
205:             values.append("estimated")             # netns arm
206:         elif metric.src == "host" and metric_name.startswith("net_"):
207:             values.append(network_confidence)      # ← THIS is line 207
208:         else:
209:             values.append("exact")                  # ← line 209, not 207
```

Line 207 is the `elif` branch for host `net_*` metrics, NOT the `else`
branch. The test `test_confidence_host_network_confidence` (line 146)
exercises it: `"net_rx_bps"` with `src="host"` and `network={"confidence":
"estimated"}` → returns `"estimated"`. Direct execution confirmed.

The original gap was real (coverage.py did not track line 207), but the
line identification was wrong. The repair added the correct behavioral
test and the gate now shows it covered. ✓

## Prior F2 — score.py default_band=None: FIXED

The new test `test_score_entity_default_band_none` (line 203–228):

- Constructs a `ScoreInput` with `default_band=None`
- Replaces `score_mod._INPUTS` inside a `try/finally` block for automatic
  restoration (no global leakage)
- Calls unmocked `score_entity()` with real `EntityFrame` and `ToposConfig`
- Asserts exact observable behavior:
  - `result.score == 0`
  - `result.contributions[0]["normalized"] == 0.0`
  - `result.contributions[0]["thresholds"] is None`
  - `result.contributions[0]["contribution"] == 0`

This proves lines 136–137 and branch [135,136] are exercised and produce
correct output. The two old hollow tests were removed. ✓

## Prior F3–F6 status

| Finding | Status | Detail |
|---------|--------|--------|
| F3 (hollow test_score_entity_default_band_is_none) | **REMOVED** | Replaced by proper test |
| F4 (hollow test_score_entity_default_band_none_normalized) | **REMOVED** | Replaced by proper test |
| F5 (self-review false factual claim) | **FIXED** | Corrected in updated self-review |
| F6 (report/self-review contradiction) | **FIXED** | Corrected in updated reports |
| F7 (weak assertion test_score_exceeds_100) | **REMOVED** | Replaced by `test_score_raw_sum_exceeds_100_scales_to_exact` with exact assertions |
| F8 (duplicate test_score_raw_sum) | **REMOVED** | Consolidated |

## New findings

### F9 — Report test counts are false (BLOCKER)
**File:** P100-REPORT.md:18

The report states: "33 tests (pytest collection: 1972 total - 1939
existing = 33 new)."

Independent verification:

- Full suite collected cases: **1972** ✓
- Without P100 file: **1932** (not 1939)
- P100 file only: **40** collected cases
- P100 test functions: **40** (no parametrization)

The report's "1939 existing" is false — pre-P100 baseline was 1932
(confirmed at P99 final review 97f1febc). The arithmetic 1972−1939=33
is false — correct is 1972−1932=40. The claim "33 tests" is false —
correct is 40. The parenthetical claiming derivation from "pytest
collection" is misleading — the collection count was not used to derive
33; the number appears to be guessed or miscounted.

The handoff O5 negative includes: "the report guesses test count."
The handoff step 8 requires: "Derive the new-test count with pytest
collection; do not copy a guessed count into evidence."

**Repair oracle:** Replace line 18 with the mechanically derived count:
"40 tests (pytest collection: 1972 total − 1932 baseline = 40 new)."

### F10 — Hollow test: test_annotate_host_network_loss_no_root (MEDIUM)
**File:** topos/tests/test_p100_diag_coverage.py:112-114

```python
def test_annotate_host_network_loss_no_root(self):
    frame = Frame(1, 100.0, 5.0, {}, {})
    _annotate_host_network_loss(frame)
```

No assertion. The function is called but no behavioral outcome is verified.
This was carried forward from the original implementation (where it was
named `test_annotate_no_root_entity` and had a docstring comment "# should
not raise" but also no assert). Coverage is obtained without behavioral
proof — O2 negative: "tests only call branches."

**Repair oracle:** Add an assertion. The function should return early when
there's no root entity (empty entities dict). Verify no exception is raised
AND no finding is added to any entity:
```python
def test_annotate_host_network_loss_no_root(self):
    frame = Frame(1, 100.0, 5.0, {}, {})
    _annotate_host_network_loss(frame)  # must not raise
    assert frame.entities == {}  # no entities to annotate
```

### F11 — Self-review overclaims fail-before evidence (LOW)
**File:** P100-SELFREVIEW.md:16

> "Each test was verified to fail when its targeted branch is removed."

This is a universal quantifier with no receipts. The self-review lists
three examples but provides no evidence for the remaining 37 tests. The
handoff O3 requires: "every retained regression assertion has fail-before
evidence." The self-review's claim is unsubstantiated for 37/40 tests.

**Repair oracle:** Either (a) narrow the claim to the tests where evidence
exists (list them explicitly), or (b) provide receipts for every test.

## Checks passed

- **3/3 exact 100%**: All targets empty missing_lines and missing_branches in
  two independent gate runs. Parity confirmed. ✓
- **rules.py line 207 correctly identified**: Host net_* elif branch,
  behavioral test proves correct output. ✓
- **score.py _INPUTS monkeypatch**: Automatic restoration (try/finally),
  exact observable assertions. ✓
- **No duplicate names** with test_diag.py. ✓
- **No host-proc reliance**, no sleeps, no wall-clock timing. ✓
- **No pragma: no cover**, no product source edits. ✓
- **git diff --check**: Clean. ✓
- **Old hollow tests F3/F4 removed**. ✓

## Verdict

**CHANGES_REQUIRED.** The code is correct — all 3 targets at exact 100%
with behavioral proof for every previously-gapped branch. The failure is
in the evidence: the report's test count (33) and baseline count (1939)
are objectively false. The correct numbers are 40 new tests from a
baseline of 1932. F10 (pre-existing hollow test) and F11 (unsubstantiated
universal fail-before claim) are quality findings that should be addressed
but do not independently block.

The repair for F9 is mechanical (one line in the report). The code and
gate are sound.
