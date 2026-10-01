# P113-LOG — Execute primitive coverage closure

## Scope and baseline

- Implementation commit: `8f74d77d5de37a87bea0baff6c2ec65adcda5d3e`.
- P112 verified baseline: 2,156 collected cases.
- P113 addition: 9 test functions and 13 collected cases.
- Literal target: 32 `actions/execute.py` executable lines and 24 branch pairs;
  P114/P115 retain the later-file residual. No whole-file claim is made.
- Product source change: none. The primitive audit/validation behavior matched
  its contract; this package added behavioral tests only.

## Routing observation

The fresh Flash implementer was stopped before editing. Its preflight was
correct, but it used host `python3` for exploratory imports despite the
handoff's declared-container-only rule. The worktree remained clean. This is a
session-health `runner/worktree contract violation`, not implementation
evidence; the controller took over under the L12 immediate-rotation rule.

## Diagnostic and gate evidence

The first focused diagnostic deliberately did not count: it passed a file path
to pytest-cov's `--cov` selector, which emitted `module-not-imported` and
`no-data-collected` warnings. The test failure in that run also exposed a test
literal error (`"bad\\nuser"` instead of a newline control character). Both were
corrected; the no-data receipt was discarded. The accepted focused command used
the declared source root (`--cov=topos/src/topos`) and passed all 13 P113 cases
with empty literal intersections.

Both authoritative runs used the exact clean implementation commit and the
declared `topos-suite` command shape in `tester-unified:local`:

```text
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub \
  tester-unified:local bash -c 'set -euo pipefail && cd <P113-worktree> &&
  export PYTHONPATH=topos/src:topos &&
  /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto \
    --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json &&
  /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main \
    --coverage-json /tmp/topos-coverage.json --source topos/src/topos'
```

| Run | Result | Changed-line floor | P113 line/pair intersection | execute record SHA-256 |
| --- | --- | --- | --- | --- |
| 1 | 2,169 passed in 78.56s; exit 0 | `0/0`, exit 0 | `[]` / `[]` | `7446e44f3192c076403a44dc812ac54e68430319f25dde50ee0612a4c34a4588` |
| 2 | 2,169 passed in 51.83s; exit 0 | `0/0`, exit 0 | `[]` / `[]` | `7446e44f3192c076403a44dc812ac54e68430319f25dde50ee0612a4c34a4588` |

The independent Pro run also passed the declared gate: 2,169 cases, exit 0,
literal lines `[]`, literal pairs `[]`.

`0/0` is expected: the implementation commit changes a test file only, while
the permanent floor evaluates changed executable product lines. The temporary
literal residual checker supplies the historic-coverage acceptance oracle.
