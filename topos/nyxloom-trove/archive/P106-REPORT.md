# P106 report — exact daemon deployment coverage

## Result

`src/topos/daemon/deploy.py` has exact 100% statement and branch coverage in
two complete parallel gate runs. Each run passed 2,052 cases. No product
source, gate, dependency, or coverage configuration changed.

## Literal and whole-file evidence

Baseline:

```text
missing lines: 67 68 82 102 103 141 180 210 211 212 328 329 335 336 344
missing pairs: 81->82 101->102 134->141 179->180 558->560
```

Both final runs produced:

```text
target missing lines: []
target missing pairs: []
whole-file missing_lines=[]
whole-file missing_branches=[]
executed_lines=195
executed_branches=20
target_record_sha256=c3b6feaf7a2f27219f5bf21302c5f309131a246674fa0c2cd64f32f5080bf965
```

## Gate evidence

The declared `topos-suite` test and changed-line gate conjuncts ran under
`set -euo pipefail` with the P106 worktree substituted. A final non-masking
Python expression printed and hashed the complete deploy.py target record
inside the container.

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,052 passed in 68.00s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,052 passed in 65.55s | 0/0, 100% ≥ 100% | 0 |

The six new test functions each collect as one case: 2,046 P105 cases plus six
P106 cases equals 2,052.

## Behavioral coverage

Three complete preflight-report assertions cover unavailable paths, regular
files where directories/sockets are required, a world-writable directory,
non-membership, and connection failure. The remaining tests prove numeric
uid/gid label fallbacks, canonical complete preflight JSON, and exact install
plan text for a warning-only step. Every caught-error scenario includes the
inducing dependency and complete resulting report.
