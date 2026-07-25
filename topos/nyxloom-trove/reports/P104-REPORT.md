# P104 report — exact snapshot coverage

## Result

`src/topos/snapshot/enrich.py` and `src/topos/snapshot/bundle.py` have exact
100% statement and branch coverage in two complete parallel gate runs. The
suite passed 2,040 cases in each run. No product source, gate, dependency, or
coverage configuration changed.

## Literal residual

Baseline lines:

```text
enrich.py: 26 29 31 46 47
bundle.py: 26 27 116 117 148 149 155 178 186 205 249
```

Baseline branch pairs:

```text
enrich.py: 28->29 30->31 61->60
bundle.py: 139->148 154->155 177->178 185->186
           204->205 207->203 245->249 247->245
```

Both run 1 and run 2 produced:

```text
enrich.py target missing lines: []
enrich.py target missing pairs: []
bundle.py target missing lines: []
bundle.py target missing pairs: []
```

The complete target-file records were:

| Run | File | Missing lines | Missing branches | Executed lines | Executed branches |
| --- | --- | --- | --- | ---: | ---: |
| 1 | `snapshot/enrich.py` | `[]` | `[]` | 44 | 16 |
| 1 | `snapshot/bundle.py` | `[]` | `[]` | 176 | 52 |
| 2 | `snapshot/enrich.py` | `[]` | `[]` | 44 | 16 |
| 2 | `snapshot/bundle.py` | `[]` | `[]` | 176 | 52 |

The SHA-256 of the normalized complete executed/missing records for both files
was identical in both runs:

```text
349c96d89d46c11e9b08fb4cee5bbd56b47f4a330705599f0f012dd603f52d69
```

## Gate evidence

The gate conjunct executed in each controller run was the declared
`topos-suite` command with `{worktree}` resolved to the P104 worktree:

```text
set -euo pipefail &&
docker run --rm
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub
  tester-unified:local
  bash -c 'set -euo pipefail &&
    cd /workspaces/vbpub/.worktrees/feat/topos-P104-snapshot-coverage &&
    export PYTHONPATH=topos/src:topos &&
    /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto
      --cov=topos/src/topos --cov-branch
      --cov-report=json:/tmp/topos-coverage.json &&
    /opt/tester-venv/bin/python topos/tools/coverage_gate.py
      --repo . --base main
      --coverage-json /tmp/topos-coverage.json
      --source topos/src/topos'
```

For the receipt runs, a final non-masking Python expression was appended to
the inner `&&` chain to print and hash the two target records before the
container was removed.

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,040 passed in 64.29s | 0/0, 100% ≥ 100% | 0 |
| 2 | 2,040 passed in 65.57s | 0/0, 100% ≥ 100% | 0 |

The 22 new test functions each collect as one case: 2,018 baseline cases plus
22 P104 cases equals 2,040.

## Behavioral coverage

The tests assert exact enrichment payloads for systemd and Docker failures,
stderr normalization and nonzero exit handling, reverse unit lookup, both
default-state-directory branches, swallowed cgroup ancestor write failure
with exact filesystem state, absolute archive-member rejection, malformed
manifest-item skips, exact notable-file selection, unique-name exhaustion at
10,000 existence checks without creating files, proper ancestor enumeration,
plain-tar membership, and the missing-zstandard error.
