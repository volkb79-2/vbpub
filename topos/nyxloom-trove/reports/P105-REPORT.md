# P105 report — exact daemon status coverage

## Result

`src/topos/daemon/status.py` has exact 100% statement and branch coverage in
two complete parallel gate runs. Each run passed 2,046 cases. No product
source, gate, dependency, or coverage configuration changed.

## Literal and whole-file evidence

Baseline:

```text
missing lines: 90 91 131 132 166 168
missing pairs: 44->46 46->48 48->50 74->76 85->90
```

Both final runs produced:

```text
target missing lines: []
target missing pairs: []
whole-file missing_lines=[]
whole-file missing_branches=[]
executed_lines=78
executed_branches=18
target_record_sha256=d603b52192fa3adf6d9a038f3b819048cefcb3988994060ec8b294fdf00974df
```

## Gate evidence

The declared `topos-suite` test and changed-line gate conjuncts ran under
`set -euo pipefail` with the P105 worktree substituted. A final non-masking
Python expression printed and hashed the complete status target record inside
the container.

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,046 passed in 64.75s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,046 passed in 69.84s | 0/0, 100% ≥ 100% | 0 |

The six new test functions each collect as one case: 2,040 P104 cases plus six
P105 cases equals 2,046.

## Behavioral coverage

The suite asserts complete dictionaries for protocol states with all optional
fields present and absent, complete no-preflight report JSON and text, the
exact `ProtocolStatus` returned for a base `DaemonClientError`, and the exact
degraded `DaemonStatusReport` after preflight raises `OSError`. Dependency-call
receipts prove the inducing inputs for both caught-error paths.
