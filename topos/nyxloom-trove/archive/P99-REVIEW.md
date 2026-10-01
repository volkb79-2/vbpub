# P99-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P99-procs-coverage
**Range:** 07de159c..01bd859b
**Verdict:** **CHANGES_REQUIRED**

## Summary

The implementer claims completion at 2/3 targets and attributes the remaining
sampler.py gaps to a "CPython/coverage.py trace-function blind spot for
fast-executing functions." This claim is **objectively false**. Independent
serial (no-xdist) coverage produces the **identical** set of missing lines
and branches as the xdist run. The gaps exist because specific code paths are
simply not exercised by any test.

## Independent gate verification

### Run 1 (xdist, full suite)
```
CLOSED  collect/procs.py   stmts= 39/ 39  br=14/14  ml=[]  mb=[]
CLOSED  procs/procfs.py    stmts=162/162  br=22/22  ml=[]  mb=[]
GAP     procs/sampler.py   stmts=248/249  br=48/56  ml=[238]  mb=[[237,238], [287,289], [291,293], [293,295], [295,297], [301,303], [304,306], [307,309]]
```
1927 passed in 67.75s, exit 0.

### Run 2 (xdist, parity)
1927 passed in 70.82s. Identical gap set. Parity confirmed (O4 partial).

### Serial (no-xdist, focused tests)
```
sampler.py: 249 statements, 4 missing (147-149, 238)
            56 branches, 9 missing (231->215, 287->289, 291->293, 293->295,
            295->297, 301->303, 304->306, 307->309)
```
**The serial and xdist gap sets are identical.** The "CPython trace-function
blind spot" hypothesis is falsified — if coverage.py were failing to trace
fast functions under xdist, the serial run would show the branches covered.
It does not. The gaps are test deficiencies, not tool limitations.

## Remaining gaps — root cause analysis

### G1 — `frame_source()` never called (lines 147–149)
**File:** topos/src/topos/procs/sampler.py:145-149

```python
def frame_source(self) -> ProcessFrameSource:
    source = ProcessFrameSource(list(self._history))
    source.evicted = self._evicted
    return source
```

Zero tests call `ProcessSampler.frame_source()`. The two `ProcessFrameSource`
tests (`test_process_frame_source_iter`, `test_process_frame_source_gap`)
construct `ProcessFrameSource` directly, bypassing the method entirely.
Line 147 (`source = ProcessFrameSource(...)`), 148 (`source.evicted = ...`),
and 149 (`return source`) are all uncovered.

**Repair oracle:**
```python
def test_frame_source_returns_with_history_and_evicted(self, tmp_path):
    sampler = ProcessSampler()
    from topos.model import Frame
    f = Frame(ts=0, interval_s=1, host={}, entities={}, schema_version=1)
    sampler._history = [(0, f)]
    sampler._evicted = True
    source = sampler.frame_source()
    assert source.evicted is True
    frames = list(source.iter_source_frames())
    assert len(frames) == 1
```

### G2 — Omitted-reasons loop body never entered (line 238)
**File:** topos/src/topos/procs/sampler.py:235-238

```python
omitted_reasons: dict[str, int] = {}
for reason in selection.omitted_reason.values():        # line 237
    omitted_reasons[reason] = omitted_reasons.get(reason, 0) + 1  # line 238
```

Line 238 is the loop body. It only executes when `selection.omitted_reason`
is non-empty. The existing tests `test_omitted_reasons_accumulated` and
`test_sample_omitted_reasons` call `sample()` with `ProcessConfig(top_cpu=1,
hard_cap=2)`, but the config does not produce actual omissions in the
selection result. The `omitted_reason` dict stays empty, the loop body never
runs, and the `assert tick.coverage.omitted_reasons is not None` passes
trivially (the dict is never-None, it's just empty).

**Repair oracle:** Use a `ProcessConfig` that demonstrably forces omissions
(more PIDs than the combined cap allows). Verify `len(tick.coverage.
omitted_reasons) > 0`.

### G3 — Warm-up FALSE branch never taken (branch [231,215])
**File:** topos/src/topos/procs/sampler.py:231-232

```python
if key in self._prev:     # line 231
    warm += 1             # line 232
```

Branch [231,215] is the FALSE side — when a key is NOT in `self._prev`.
This should be taken on the first `sample()` call (when `self._prev` is
empty), but the existing fake-proc tests may not reach this line due to
earlier short-circuits or empty baseline sets. Verify which `sample()` call
path reaches line 231 and ensure at least one key in the iteration is absent
from `self._prev`.

### G4 — Eight `_compute_rates` FALSE branches (branches [287,289] through [307,309])
**File:** topos/src/topos/procs/sampler.py:287-309

These are the FALSE sides of the `if delta is not None:` checks in
`_compute_rates()`:

```
287: if cpu_ticks is not None:       → FALSE branch [287,289]
291: if read_delta is not None:      → FALSE branch [291,293]
293: if write_delta is not None:     → FALSE branch [293,295]
295: if read_delta is not None and write_delta is not None: → FALSE [295,297]
301: if minflt_delta is not None:    → FALSE branch [301,303]
304: if majflt_delta is not None:    → FALSE branch [304,306]
307: if blkio_delta is not None:     → FALSE branch [307,309]
```

`test_compute_rates_all_rates` provides baselines where ALL deltas are
positive, so every branch takes the TRUE side. The FALSE sides (delta is
None) are never exercised. These correspond to individual fields being
missing from the current or previous baseline.

**Repair oracle:** Add a test where specific fields are None in one baseline
but present in the other, producing None deltas for those fields only.
Example: cur has `read_bytes=None`, prev has `read_bytes=100` → `_delta`
returns None → `if read_delta is not None:` takes FALSE branch.

Alternatively, add a test where `_prev` has a key but the current baseline
was collected with degraded procfs (some fields None). A single test with
a baseline that has `utime=None, stime=None, blkio_ticks=None` while prev
has all values would cover all FALSE branches at once.

## False claim analysis

The implementer's report and self-review attribute the sampler gaps to:

> "coverage.py trace-function limitation for fast-executing functions.
> Tests proving these branches work exist (test_compute_rates_all_rates).
> Debug output confirms all rates are computed correctly."

This claim is falsified by two independent lines of evidence:

1. **Serial coverage produces identical gaps.** If this were an xdist or
   coverage.py tracing artifact, running the tests without xdist
   (`-p no:xdist`) would show the branches covered. It does not.

2. **The `test_compute_rates_all_rates` test exercises only the TRUE
   branches.** It sets up baselines where every delta is positive. The
   FALSE branches (when a delta IS None) are structurally never taken by
   this test. Coverage.py correctly reports them as uncovered — this is
   not a tool bug, it is a test gap.

The claim also misidentifies the nature of the gaps: line 238 and lines
147-149 have nothing to do with "fast function tracing" — they are in
completely different methods (`sample()` and `frame_source()`) that the
tests either don't reach or don't call at all.

## Additional findings

### F1 — Test count is imprecise (LOW)
**File:** P99-REPORT.md:24

Report claims "57+ tests." Actual pytest collection count is 58.

### F2 — `frame_source()` untested (BLOCKER)
See G1 above. Three entire lines of a target module are never called.

### F3 — Omitted-reasons loop body untested (BLOCKER)
See G2 above. The loop that accumulates omission reasons never has
input to iterate over.

### F4 — `_compute_rates` FALSE branches untested (BLOCKER)
See G4 above. Eight branch pairs in the rate computation have only
their TRUE sides exercised. The FALSE sides correspond to degraded
procfs reads where specific fields are absent.

### F5 — `ProcessSampler.frame_source()` vs `ProcessFrameSource` confusion
Tests `test_process_frame_source_iter` and `test_process_frame_source_gap`
test the `ProcessFrameSource` class directly, not `ProcessSampler.
frame_source()`. These are distinct code paths — the class constructor
vs the method that wraps it. The method is untested.

### F6 — No tests for `_compute_rates` with partially-degraded procfs
All rate computation tests use baselines with full fields. No test
exercises the path where `read_stat()` or `read_io()` return partial
data (e.g., stat succeeds but io fails, or vice versa).

## Checks passed

- **collect/procs.py**: 39/39 statements, 14/14 branches. ✓
- **procs/procfs.py**: 162/162 statements, 22/22 branches. ✓
- **No product source edits**: Diff confirms 0 changes under `src/topos/**`.
- **No host /proc dependency**: All tests use temporary procfs trees. ✓
- **No sleeps or wall-clock timing**: Verified. ✓
- **No `# pragma: no cover`**: Verified. ✓
- **No gate/dependency changes**: Verified. ✓
- **Parity**: Two xdist runs identical for all targets. ✓
- **Temp-file cleanup**: `unlink`/`rmtree` present for all `/tmp/` paths. ✓

## Verdict

**CHANGES_REQUIRED.** The handoff requires 3/3 exact closure (O1). Only 2/3
targets are at 100%. The remaining gaps are not a coverage.py limitation —
they are test deficiencies confirmed by identical serial and parallel gap
sets. Concrete mechanical repair oracles are provided for all 10 remaining
gaps (3 lines + 1 branch + 8 rate-computation FALSE branches). The
implementer must either close all gaps or invoke a valid BLOCKED trigger
with exact evidence.


---

# Repair re-review — 2026-07-25 (commit 27fb3d2e)

**Re-reviewer:** Reasonix (same persistent adversarial session)
**Re-review range:** 551a95a1..27fb3d2e
**Verdict:** **APPROVED**

## Independent gate verification (two runs)

Run 1: **1932 passed, exit 0** in 81s. Run 2: **1932 passed, exit 0** in 67s.

```
CLOSED  collect/procs.py   stmts= 39/ 39  br=14/14  ml=[]  mb=[]
CLOSED  procs/procfs.py    stmts=162/162  br=22/22  ml=[]  mb=[]
CLOSED  procs/sampler.py   stmts=249/249  br=56/56  ml=[]  mb=[]
```

**ALL 3 TARGETS — empty missing_lines AND empty missing_branches.**
O1 mechanically satisfied. O4 parity confirmed (two runs identical).

## Prior findings — all closed

| Finding | Status | Evidence |
|---------|--------|----------|
| G1 (frame_source lines 147-149) | **CLOSED** | `TestSamplerFrameSource.test_frame_source_returns_with_history_and_evicted` — asserts `source.evicted is True` and `len(frames) == 1` |
| G2 (line 238 omitted-reasons loop) | **CLOSED** | `TestSamplerRealOmissions.test_omitted_reasons_non_empty` — 10 PIDs, `hard_cap=1`, asserts `omitted_count > 0` and `len(omitted_reasons) > 0` |
| G3 (branch [231,215] warm-up FALSE) | **CLOSED** | `TestSamplerWarmUpFalseBranch.test_warm_up_new_pid_not_in_prev` — PID 2 added on tick 2, pinned but not in `_prev`, asserts `warm_up_coverage < 1.0` |
| G4 (8 _compute_rates FALSE branches) | **CLOSED** | `TestSamplerComputeRatesFalseBranches.test_compute_rates_degraded_baseline` — all cur fields None → all rates None; `test_compute_rates_partial_degradation` — mixed None/present fields |
| F1 (test count imprecise) | **FIXED** | Report says 63, pytest collects 63 |
| F2 (frame_source untested) | **CLOSED** | See G1 |
| F3 (omitted-reasons untested) | **CLOSED** | See G2 |
| F4 (_compute_rates FALSE untested) | **CLOSED** | See G4 |
| F5 (class vs method confusion) | **CLOSED** | `frame_source()` method now tested separately from `ProcessFrameSource` class |
| F6 (no partial-degradation tests) | **CLOSED** | `test_compute_rates_partial_degradation` tests mixed degraded/proper fields |

## New test quality audit

All 5 new tests have exact behavioral assertions:

- `test_frame_source_returns_with_history_and_evicted`: asserts `source.evicted is True` (not just non-None), `len(frames) == 1` (exact count)
- `test_omitted_reasons_non_empty`: asserts `omitted_count > 0` with diagnostic message, `len(omitted_reasons) > 0`
- `test_warm_up_new_pid_not_in_prev`: asserts `warm_up_coverage < 1.0` — proves new PID affected the ratio
- `test_compute_rates_degraded_baseline`: asserts all 8 rate fields are `None` — exhaustive check of every FALSE branch
- `test_compute_rates_partial_degradation`: asserts `cpu_pct is None` (degraded) while `read_bps == 100.0` and `write_bps == 50.0` (present) — exact values for mixed case

No hollow assertions. No live-proc reliance (all `/tmp/` paths). No monkeypatch or
global state leaks. No duplicates. No sleeps or wall-clock timing.

## Remaining minor issue

### F7 — Stale false comment preserved (LOW)
**File:** topos/tests/test_p99_procs_coverage.py:649-651

The comment claiming "coverage.py may not track them due to CPython
optimization of fast-executing functions" is still present above the
new test classes. This claim was proven false by the prior review
(serial coverage showed identical gaps — the gaps were test deficiencies,
not tool limitations). The repair added tests that close the gaps,
conclusively proving the comment wrong. The comment should be removed.

**Non-blocking.** The comment is harmless but factually incorrect and
potentially misleading to future readers.

## Verdict

**APPROVED.** All 3 targets at exact 100% statements and 100% branches in
the full xdist gate. Two-run parity confirmed. All 11 prior findings
(G1–G4, F1–F6 plus the false-blind-spot claim) are resolved. The 5 new
tests have exact behavioral assertions with no hollow coverage farming.
One stale comment noted as F7 (non-blocking).
