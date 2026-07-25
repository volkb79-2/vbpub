# P112 log

## Scope and baseline

- Input revision: `a1c94c74`
- Branch: `feat/topos-P112-squeeze-coverage`
- Immutable implementation: `277209b58399bf0674aa7a77ff5fb1bae7368974`
- Baseline: 2,135 cases
- Product file: `actions/squeeze.py`

The handoff froze 41 missing lines and 11 missing pairs.

## Product repairs

The loop previously decremented its Python `high` variable without applying the
next value to cgroup `memory.high`. It now writes every next in-floor value
before that value is sampled. The exact two-step oracle observes initial `2`,
next `1`, then one restoration to `max`; its step records are exactly 2 and 1.

Four `BaseException` catches (audit start/end, generic measurement failure, and
root check) now catch `Exception`. Ordinary failures preserve their documented
nonfatal/fail-closed behavior. `KeyboardInterrupt` propagates at audit/root
boundaries, while the existing explicit in-loop handler still returns an
`interrupted` result.

The generic measurement handler also closed the log before its `finally` block
attempted to write the summary, which turned the intended error result into a
closed-file failure. The premature close was removed; `finally` writes the
error summary, closes once, and the restore guard restores `memory.high`.

## Test construction

`tests/test_p112_squeeze_coverage.py` adds 21 collected cases covering:

- suffix parse failure and exact default cgroup read/write boundaries;
- SIGTERM restoration plus injected exit and signal-handler restoration;
- absent original handlers and best-effort restore failure;
- exact multi-step applied-high order and zero-delta refault behavior;
- explicit-start/no-step floor behavior;
- log open, header, step, and summary failures;
- interruption before and after a recorded step;
- ordinary measurement failure with summary and restore;
- audit start/end ordinary and operator-interrupt behavior;
- complete no-step rendering plus `_mib(None)`; and
- root-check ordinary and operator-interrupt behavior.

The initial focused run had 20 passes and one test-fixture bookkeeping failure:
the fake signal registrar overwrote its installed-handler map during
restoration. The installed closures were snapshotted before restoration; the
next focused run passed all 21. A pre-gate scan then replaced count-only audit
assertions and a summary `any(...)` check with complete audit dictionaries and
complete parsed header/summary records. The final focused run again passed 21.

## Baseline-to-current mapping

Lines through baseline 423 remain unchanged. The two inserted step-write lines
shift the next region by two; removing the premature close reduces the later
shift to one:

```text
451->453
459->461 460->462 462->464 464->466 465->467 466->468
467->deleted 468->469
494->495 495->496 510->511 511->512 714->715 744->745 745->746
```

Corresponding shifted baseline pairs include `450->451` → `452->453`,
`462->464` → `464->466`, `462->476` → `464->477`, `664->676` →
`665->677`, and `713->714` → `714->715`. The new applied-step predicate is
also completely covered.

## Authoritative receipt command

Both receipts asserted exact clean HEAD before running in
`tester-unified:local`:

```text
cd /workspaces/vbpub/.worktrees/feat/topos-P112-squeeze-coverage
test "$(git rev-parse HEAD)" = 277209b58399bf0674aa7a77ff5fb1bae7368974
test -z "$(git status --porcelain)"
export PYTHONPATH=topos/src:topos
/opt/tester-venv/bin/python -m pytest topos/tests -q -n auto \
  --cov=topos/src/topos --cov-branch \
  --cov-report=json:/tmp/topos-p112-coverage.json
/opt/tester-venv/bin/python topos/tools/coverage_gate.py \
  --repo . --base main \
  --coverage-json /tmp/topos-p112-coverage.json \
  --source topos/src/topos
```

The normalized target record was asserted empty, printed, and hashed inside
the container.

## Receipts

| Run | Pytest | Changed-line floor | Target record hash | Exit |
| --- | --- | --- | --- | ---: |
| 1 | 2,156 passed in 63.19s | 6/6, 100% ≥ 100% | `c33dd2a3559cd9214ff5ebb13159eaaa8575ed811218c777af173195ff5df672` | 0 |
| 2 | 2,156 passed in 61.77s | 6/6, 100% ≥ 100% | `c33dd2a3559cd9214ff5ebb13159eaaa8575ed811218c777af173195ff5df672` | 0 |

Both runs reported `missing_lines=[]` and `missing_branches=[]` for
`squeeze.py`. Every mapped baseline line/pair is closed and the removed close
line has a deletion oracle. Collection arithmetic is exact:
2,135 + 21 = 2,156.
