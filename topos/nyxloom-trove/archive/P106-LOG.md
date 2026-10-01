# P106 log — exact daemon deployment coverage

## Agent route correction

A fresh DeepSeek Flash Max session replaced the unhealthy persistent Topos
session. Its first turn preflighted the correct worktree but then read project
files from shared main; the controller interrupted before any write. On a
corrected resume it stayed in the worktree but ignored the declared runner,
created a 112 MiB disposable host venv, copied 33 MiB of the worktree to
`/tmp`, and probed several incorrect Docker mounts. The controller interrupted
again before implementation and removed exactly those two temporary trees.

Because both persistent and fresh Flash routes had now crossed mechanical
health tripwires, the controller implemented the bounded P106 tranche under
Sol/high. DeepSeek Pro remains the independent reviewer.

## Implementation

Six exact tests cover:

- unavailable runtime and socket paths plus a missing daemon group;
- regular files where runtime directory and Unix socket are required, with a
  deterministic non-member identity;
- a world-writable runtime directory, valid socket metadata, and caught
  connection `OSError`;
- deterministic numeric uid/gid label fallbacks;
- complete canonical preflight JSON; and
- complete install-plan text with a warning-only, command-free step.

All identity, stat, and connection seams are explicit and context-managed.
No source, status, CLI, gate, dependency, pragma, or omit change was made.

## Verification

Focused xdist diagnostic:

```text
6 passed in 7.71s
```

Two complete gate-plus-receipt runs:

```text
run 1: 2052 passed in 68.00s; diff-coverage OK; exit 0
run 2: 2052 passed in 65.55s; diff-coverage OK; exit 0
```

Both runs printed:

```text
missing_lines=[]
missing_branches=[]
executed_lines=195
executed_branches=20
target_record_sha256=c3b6feaf7a2f27219f5bf21302c5f309131a246674fa0c2cd64f32f5080bf965
```
