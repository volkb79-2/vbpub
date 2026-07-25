# P104-LOG — Complete snapshot coverage

## Baseline

2018 total. enrich.py: 5 lines, 3 branches. bundle.py: 11 lines, 8 branches.

## Implementation

22 tests covering all 16 lines and 11 branch pairs:
- enrich.py: systemctl OSError, stderr collect, nonzero return, docker
  OSError/ValueError/TypeError, leaf_unit reverse search
- bundle.py: xdg dir both branches, cgroup OSError, tar fallback, no-zstd
  archive, unsafe member rejection, malformed manifest, notable-file
  selection/loop, unique-path exhaustion, ancestor_keys

## Gate

totop_ 2040 passed, exit 0, parity PASS.

## Literal residual

both run1 and run2: all target lines=[] and pairs=[].
both files whole: missing_lines=[] missing_branches=[].
