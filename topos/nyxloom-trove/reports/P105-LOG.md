# P105 log — exact daemon status coverage

## Scope correction

P105 initially grouped daemon status and deployment. The persistent Flash
implementer repeatedly failed to construct the deployment filesystem fixtures
and accumulated stale edit-context failures. Its uncommitted draft contained
duplicated contradictory setup and partial assertions, so the controller
deleted only that draft and narrowed P105 to `daemon/status.py`; deployment is
deferred to P106.

The same cached session was tried once on the reduced six-line target. It
again weakened exact assertions to substring and selected-field checks while
debugging. The turn was interrupted immediately under PL4, and the uncommitted
draft was replaced by a controller-authored six-test exact tranche.

## Implementation

The retained tests cover:

- every combination boundary needed for optional protocol JSON fields;
- complete JSON and complete text when preflight is absent;
- the exact base-client error status and constructor call; and
- the exact status report plus dependency calls when preflight `OSError` is
  swallowed.

No source, deploy, CLI, gate, dependency, pragma, or omit change was made.

## Verification

Focused xdist diagnostic:

```text
6 passed in 4.95s
```

Two complete gate-plus-receipt runs:

```text
run 1: 2046 passed in 64.75s; diff-coverage OK; exit 0
run 2: 2046 passed in 69.84s; diff-coverage OK; exit 0
```

Both runs printed:

```text
missing_lines=[]
missing_branches=[]
executed_lines=78
executed_branches=18
target_record_sha256=d603b52192fa3adf6d9a038f3b819048cefcb3988994060ec8b294fdf00974df
```
