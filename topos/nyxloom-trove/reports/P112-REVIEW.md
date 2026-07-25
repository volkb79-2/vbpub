# P112-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P112-squeeze-coverage
**HEAD:** f1fdb123e58eeea030c06157bb5226888bbd45b7 (confirmed)
**Verdict:** **APPROVED**

## Preflight

- `pwd`: `/workspaces/vbpub/.worktrees/feat/topos-P112-squeeze-coverage`
- Branch: `feat/topos-P112-squeeze-coverage` ✓
- HEAD: full sha matches required ✓
- `git status`: clean ✓

## Gate evidence (LOG/REPORT, mechanically consistent)

Two complete xdist runs from exact clean commit: **2,156 passed, exit 0**
(63s / 62s).

```
squeeze.py: missing_lines=[], missing_branches=[]
target_record_sha256=c33dd2a3559cd9214ff5ebb13159eaaa8575ed811218c777af173195ff5df672
Changed-line floor: 6/6, 100% >= 100%
```

Both runs: literal intersections empty, whole-file record empty, record hash
identical. O1/O5 satisfied. 21 collected cases, 2,135 + 21 = 2,156. ✓

## Source edit audit (6 changed executable lines)

### Step-write fix (current lines 445–446 — 2 new lines)
Previously, `high -= step_bytes` updated the Python variable but did not
write the new value to cgroup `memory.high`. Now writes before sampling:
```python
high -= step_bytes
if high >= floor_bytes:
    writer(target, "memory.high", str(high))
```
Proved by `test_multistep_run_applies_every_measured_high_before_sampling`:
writes `["2", "1", "max"]` with steps recorded at high=2 and high=1.
Restoration writes `max` once. ✓

### Premature-close removal (baseline line 467 deleted)
The generic error handler called `log_fh.close()` before `finally` wrote
the summary, turning the intended error result into a closed-file failure.
The close was removed; `finally` handles close + summary once.
`test_ordinary_measurement_failure_returns_error_after_summary_and_restore`
proves: header + summary records exist, `memory.high` restored. ✓

### Four BaseException→Exception repairs (lines 317, 467, 511, 745)
| Boundary | Line | Ordinary test | Interrupt test |
|----------|------|---------------|----------------|
| Audit start | 317 | RuntimeError → nonfatal (audit start only) | KeyboardInterrupt propagates |
| Measurement handler | 467 | RuntimeError → error result + summary + restore | In-loop KeyboardInterrupt → `interrupted` result |
| Audit end | 511 | RuntimeError → nonfatal (full result still returned) | KeyboardInterrupt propagates + restore |
| Root check | 745 | RuntimeError → fail-closed error result | KeyboardInterrupt propagates |

All four boundaries have both ordinary-failure and operator-interrupt
counterparts. The in-loop `KeyboardInterrupt` contract (which returns
`stop_reason="interrupted"` rather than propagating) is preserved and
tested both before-first-step and after-step-exists. ✓

The exact six-line changed-line denominator is current lines 317, 445, 446,
467, 511, and 745. The deleted baseline line 467 is covered by the separate
deletion oracle above and is not part of that denominator.

## Literal line/arc coverage

All 41 baseline lines and 11 baseline pairs closed. The LOG documents a
complete baseline→current line-shift map accounting for two inserted
step-write lines and 1 removed premature-close line. All shifted pairs
verified against current source. Retained lines verified at HEAD:
317, 445, 446, 467, 511, and 745 as shown above.

## Test quality audit (21 functions, 21 cases)

| Test | Assertion type |
|------|---------------|
| Parse size failure | Exact ValueError text |
| Default cgroup boundaries | Exact int/dict/pressure returns + exact path/call/write records |
| SIGTERM restore + exit | Exact write sequence + `exits == [128 + SIGTERM]` + exact handler call sequence |
| Restore guard no-enter | Exact write + zero handler calls |
| Restore write failure | Exact one write call + idempotent (called twice, writes once) |
| Multi-step write-before-sample | Complete `SqueezeResult` with 2 exact steps + writes `["2","1","max"]` + `audit.calls` dict equality |
| Start below floor | Complete `SqueezeResult` + writes `["0","max"]` + audit dict equality |
| Log open failure | Complete `SqueezeResult` with exact error path |
| Header write failure | Complete error result + `file.closed is True` + zero writes |
| Step write failure | Complete result with preserved step + writes + `file.closed` + exact parsed JSONL header/summary records |
| Summary write failure | Complete result + writes + `file.closed` |
| KB interrupt before step | Complete `interrupted` result + exact call sequence + restore writes |
| KB interrupt after step | Complete result with preserved step + writes + `file.closed` |
| Ordinary measurement failure | Complete error result + exact call sequence + restore writes + parsed JSONL (header+summary with stop_reason=error) |
| Audit start ordinary failure | Nonfatal (error result still returned) + `audit.calls` dict equality (start only) |
| Audit start KB interrupt | `pytest.raises(KeyboardInterrupt)` + `audit.calls` (start only) |
| Audit end ordinary failure | Nonfatal (full result returned) + complete `audit.calls` |
| Audit end KB interrupt | `pytest.raises(KeyboardInterrupt)` + complete `audit.calls` + restore writes |
| Render without steps | Complete multiline text + `_mib(None) == "N/A"` |
| Root check ordinary failure | Complete error result + `calls == ["root"]` |
| Root check KB interrupt | `pytest.raises(KeyboardInterrupt)` + `calls == ["root"]` |

All 21 cases use complete dataclasses, dicts, call structures, parsed JSONL
records, rendered output, or exact exception text/type. Zero substring,
membership, non-None, range, len-only, or assertion-free bodies. Zero
duplicates and zero `pass` statements in the P112 test file. Source line 466 is the
intentional `if steps: pass` branch, exercised by
`test_keyboard_interrupt_after_step_preserves_complete_step`; the no-step
counterpart is exercised separately.

Only cgroup, filesystem, signal, exit, audit, clock, and root seams are
injected; no target function (`run_squeeze`, `run_squeeze_gated`,
`parse_size`, `render_squeeze_result`) is mocked.

### Scope
No non-P112 source, gate, dependency, pragma, or omit changes. ✓
