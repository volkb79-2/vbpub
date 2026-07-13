# P78 Self-Review - 2026-07-13

## Outcome

One test-evidence gap was found and fixed. No production defect was found:
the extracted `_execute_gated()` chain preserves every enumerated refusal and
gate-ordering winner from the pre-extraction implementation, and no verb lost
a gate.

## Finding SR1: the implementation oracle sampled instead of enumerated

The implementation commit's P78 test covered the stale path, all four success
audit shapes, and double-failure ordering for kill/update. It did not meet the
handoff's adversarial standard for two reasons:

- plan and set-property had no two-failing-gates ordering proof;
- common failures were not asserted as exact `(outcome, audit_outcome,
  stderr)` triples for every verb.

The structural one-chain test would detect a public executor bypassing
`_execute_gated()`, but it would still pass if a gate were deleted inside the
shared chain. The success-shape test has the same blind spot. Those tests are
useful but hollow as differential gate-preservation evidence on their own.

Fix: expanded `test_p78_action_kernel.py` to 65 tests. The differential set
pins every named common failure across all four verbs, each verb-specific
gate, and a distinct double-failure winner for plan, set-property, kill, and
update. Assertions are on returned observable fields, not mock bookkeeping.

## Mechanical gate map

| Verb | Pre-audit gates, in order | Post-audit gates |
|---|---|---|
| plan | action-kind/allowlist; target + immutable plan validation | target + plan revalidation |
| set-property | property; unit; value; persistence | stale-value revalidation |
| kill | signal allowlist; KILL force; protected target; action kind | target revalidation |
| update | memory; CPUs; at-least-one limit; systemd target; current usage | target revalidation |

Shared gates remain: admin, typed confirmation, root, timeout, absolute audit
path, identity, pre-audit write, bounded runner normalization, duration clamp,
and post-audit write/failure conversion.

## Differential and ordering evidence

The pre-extraction source was extracted from `HEAD^` into
`/tmp/p78-baseline`. The same observable taxonomy/order tests were then run
with that tree first on `PYTHONPATH`, followed by current code:

```text
PYTHONPATH=/tmp/p78-baseline/groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_p78_action_kernel.py -q -W error \
  -k 'differential or gate_ordering_proof' --confcutdir=groop/tests
62 passed, 3 deselected in 0.20s

PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_p78_action_kernel.py -q -W error
65 passed in 0.26s
```

The exact refusal triples and all four ordering winners match. In particular:

- plan: unknown kind wins over an invalid target;
- set-property: invalid property wins over invalid unit/value;
- kill: invalid signal wins over protected-target refusal;
- update: systemd-target redirection wins over unverifiable current usage.

## Standing-template checklist

- [x] Every handoff gate was run in the package venv from the repository root;
  REPORT contains real output, including the post-self-review reruns.
- [x] Diff scope is limited to P78 action tests and handoff artifacts for this
  pass; the implementation commit remained within its declared scope.
- [x] Numbered differential and ordering oracles now assert observables and
  fail if their corresponding gate or ordering is removed.
- [x] Date, counts, branch paths, LOG, and REPORT were checked against current
  files and command output.
- [x] Changed artifacts are ASCII; no dead code or scaffolding was introduced.

## Final validation

```text
PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_p78_action_kernel.py groop/tests/test_actions.py \
  groop/tests/test_p72_kill_update.py -q -W error
316 passed in 1.20s

timeout 900 env PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests -q -W error
1253 passed, 3 skipped in 151.25s (0:02:31)

python3 -m py_compile groop/src/groop/actions/execute.py \
  groop/tests/test_p78_action_kernel.py
# exit 0

git diff --check
# exit 0
```
