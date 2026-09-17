# P109 log

## Scope and baseline

- Input revision: `ef0b1d79`
- Branch: `feat/topos-P109-action-safety-coverage`
- Baseline: 2,070 cases
- Product files inspected: `actions/kill_ops.py`,
  `actions/owner_safety.py`
- Product source changes: none

The handoff froze six missing lines/six missing pairs in `kill_ops.py` and nine
missing lines/nine missing pairs in `owner_safety.py`.

## Test construction

Added `tests/test_p109_action_safety_coverage.py`. Its 18 collected cases cover:

- non-string/empty signals and targets;
- unknown SIG-prefixed input and both invalid kill-kind paths;
- the exact KILL preview including its warning;
- non-string owner detail sanitization;
- absent, malformed, and mixed `Config.Labels` shapes;
- Compose metadata whose display detail sanitizes to empty;
- the defensive unknown-owner message;
- nameless canonical identity protection; and
- one-call delegation to the injected Docker-inspect seam.

The initial focused diagnostic passed all 18 cases. Its two file-path
`--cov` selectors were not valid pytest-cov import targets, so coverage.py
reported no data; this diagnostic is not gate evidence. Both authoritative
receipts used the declared source-root selector in the full xdist suite.

## Full receipt command

Both receipts ran in `tester-unified:local` with:

```text
cd /workspaces/vbpub/.worktrees/feat/topos-P109-action-safety-coverage
export PYTHONPATH=topos/src:topos
/opt/tester-venv/bin/python -m pytest topos/tests -q -n auto \
  --cov=topos/src/topos --cov-branch \
  --cov-report=json:/tmp/topos-p109-coverage.json
/opt/tester-venv/bin/python topos/tools/coverage_gate.py \
  --repo . --base main \
  --coverage-json /tmp/topos-p109-coverage.json \
  --source topos/src/topos
```

The normalized records for both target files were printed and hashed inside
the container after each run.

## Receipts

| Run | Pytest | Changed-line floor | Target record hash | Exit |
| --- | --- | --- | --- | ---: |
| 1 | 2,088 passed in 57.56s | 0/0, 100% ≥ 100% | `1e8d018816b6b29ff1677dbb0f6882396c48a49af71033111539318174937bee` | 0 |
| 2 | 2,088 passed in 63.76s | 0/0, 100% ≥ 100% | `1e8d018816b6b29ff1677dbb0f6882396c48a49af71033111539318174937bee` | 0 |

Both runs reported:

```text
kill_ops.py:
  executed_lines=63
  missing_lines=[]
  executed_branches=24
  missing_branches=[]

owner_safety.py:
  executed_lines=150
  missing_lines=[]
  executed_branches=64
  missing_branches=[]
```

The intersections with every literal handoff line and pair are empty. Collection
arithmetic is exact: 2,070 + 18 = 2,088.
