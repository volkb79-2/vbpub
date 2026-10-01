# P111 log

## Scope and baseline

- Input revision: `0a26749b`
- Branch: `feat/topos-P111-update-ops-coverage`
- Immutable implementation: `ca011af4ebcd0894157d603321b6f785246edb49`
- Baseline: 2,110 cases
- Product file: `actions/update_ops.py`

The handoff froze 28 missing lines and 14 missing pairs.

## Product repairs

Both the default container-resolution reader and preview current-usage reader
caught `BaseException`. They now catch `Exception`: ordinary failures remain
fail-closed, while `KeyboardInterrupt`/`SystemExit` propagate.

The direct argv builder also accepted `True` as memory or CPU because Python
booleans are numeric subclasses. Memory now requires exact `int`, and CPU
explicitly rejects `bool` before accepting `int`/`float`. Positive ordinary
numeric inputs remain unchanged.

## Test construction

`tests/test_p111_update_ops_coverage.py` adds 25 collected cases covering:

- non-finite and over-limit CPUs;
- empty and over-limit memory;
- one collector sweep, one container resolver, and the exact resolved cgroup
  read path;
- direct cgroup-key read/parse failures;
- ordinary versus operator-interrupt resolution failure;
- empty target, absent resource options, and invalid typed memory/CPU values;
- ordinary versus operator-interrupt preview reader failure; and
- complete CPU-only and memory-without-current-usage rendering.

All collector, resolver, configuration, and `Path.read_text` effects are
injected. No host cgroupfs or Docker state is consulted.

## Authoritative receipt command

Both receipts asserted the exact clean implementation HEAD before running in
`tester-unified:local`:

```text
cd /workspaces/vbpub/.worktrees/feat/topos-P111-update-ops-coverage
test "$(git rev-parse HEAD)" = ca011af4ebcd0894157d603321b6f785246edb49
test -z "$(git status --porcelain)"
export PYTHONPATH=topos/src:topos
/opt/tester-venv/bin/python -m pytest topos/tests -q -n auto \
  --cov=topos/src/topos --cov-branch \
  --cov-report=json:/tmp/topos-p111-coverage.json
/opt/tester-venv/bin/python topos/tools/coverage_gate.py \
  --repo . --base main \
  --coverage-json /tmp/topos-p111-coverage.json \
  --source topos/src/topos
```

The normalized target record was asserted empty, printed, and hashed inside
the container.

## Receipts

| Run | Pytest | Changed-line floor | Target record hash | Exit |
| --- | --- | --- | --- | ---: |
| 1 | 2,135 passed in 69.75s | 4/4, 100% ≥ 100% | `a48772803e64446ac7b90be20102b056f5feb29ee19dcba90e885c72dcfb0dc7` | 0 |
| 2 | 2,135 passed in 69.34s | 4/4, 100% ≥ 100% | `a48772803e64446ac7b90be20102b056f5feb29ee19dcba90e885c72dcfb0dc7` | 0 |

Both runs reported `missing_lines=[]` and `missing_branches=[]` for
`update_ops.py`; every handoff line/pair intersection is empty. Collection
arithmetic is exact: 2,110 + 25 = 2,135.
