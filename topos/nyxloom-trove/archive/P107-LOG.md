# P107 log — exact network-provider coverage

## Implementation

The controller mapped all 28 lines and 12 arcs against exact source. Two netns
aggregation branches were invariant-impossible and were removed with their
redundancy proof recorded in the report. Ten deterministic tests close the
remaining provider rejection, parser, status, and helper paths with complete
`NetSample`, result, candidate, or status assertions.

No host namespaces, BPF maps, shared temporary paths, copied worktrees, host
virtualenvs, or rebuilt images were used. Time, stat, read, and provider helper
seams are bounded and context-managed.

## Verification

Focused xdist diagnostic:

```text
10 passed in 5.42s
```

Two complete gate-plus-receipt runs:

```text
run 1: 2062 passed in 66.77s; diff-coverage OK; exit 0
run 2: 2062 passed in 65.48s; diff-coverage OK; exit 0
```

Both runs printed:

```text
net_netns.py missing_lines=[] missing_branches=[] executed=116/34
net_bpf.py missing_lines=[] missing_branches=[] executed=123/42
target_record_sha256=d160957e5ed21f415337b338442b73c4a04a4338df063aa7936314ebbc5003ec
```
