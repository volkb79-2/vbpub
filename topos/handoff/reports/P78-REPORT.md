# P78 - Action Kernel Gate-Chain Extraction - Implementation Report

## Summary

Extracted the four action executors into one private `_execute_gated()` chain.
`execute_plan`, `execute_set_property`, `execute_kill`, and `execute_update`
retain their public signatures and now supply only their confirmation token,
ordered verb gates, and immutable argv/plan construction.

## Architecture decision: preserve P49's stale audit trail

The P49 stale-value re-read remains after the durable pre-audit write. It is
now an explicitly named **post-audit revalidation gate**, rather than an
ambiguous pre-audit verb gate. This is the more defensible audit posture:
when an operator attempts a stale mutation, the durable record shows both the
attempt (`pre`) and its refusal (`post`), even though no runner is invoked.

All other verb-specific gates remain ordered pre-audit gates. The existing
post-audit target revalidation also uses the same smaller category. This
resolves the former contract conflict without changing any stale result,
audit field, audit-record order, message, or exit-code behavior.

## What changed

- Added the private `_ExecutionSpec`, `_GateRefusal`, and `_execute_gated()`
  infrastructure in `src/topos/actions/execute.py`.
- Routed all four public executors through that one gate/audit/runner sequence.
- Kept the runner, clock, identity, root-check, protected-check, and
  current-memory-reader Python API seams unchanged.
- Added P78 tests for one-chain delegation, a differential refusal table,
  stale audit preservation, per-verb double-failure ordering, and the pre/post
  audit shape for all four success paths.
- Updated the architecture map and the P78 contract text to document the two
  gate categories.
- Updated P80's handoff: its install-specific checks must use P78's ordered
  pre-audit gate extension; the P49 post-audit stale exception does not apply
  to install actions. P80's audit record shape is unchanged.

## Observable-behavior evidence

The self-review rebuilt the differential refusal taxonomy as an explicit
golden table and ran the same tests against both the pre-extraction `HEAD^`
source tree and the extracted code. It covers non-admin, confirmation, root,
timeout, audit-path, identity, pre/post-audit, target, signal, force,
protected-target, systemd target, below-current/unverifiable usage, runner
OSError/timeout, and stale paths. It also makes two gates fail for each verb
and asserts the exact winner. The pre-extraction tree passed all 62 selected
cases; current code passed the same 62, plus three structural/audit-shape
checks. The stale case continues to return `outcome=stale`,
`audit_outcome=None`, its original stderr text, and `pre`, then `post` records.

`execute.py` changed from 1,438 to 1,237 lines. The reduction is the removed
copied common execution bodies; the remaining per-verb lines declare their
gates and immutable argv construction explicitly.

## Deviations from handoff

None after the reconciliation package's contract update. The former contract
2 wording incorrectly classified P49 stale detection as pre-audit. It is now
the documented post-audit exception that preserves contract 3 exactly.

## Validation

All commands were run from the repository root using the package virtual
environment at `topos/.venv` with `PYTHONPATH=topos/src`.

```text
PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests/test_actions.py topos/tests/test_p72_kill_update.py -q -W error
251 passed in 0.83s

PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests/test_p78_action_kernel.py topos/tests/test_actions.py \
  topos/tests/test_p72_kill_update.py -q -W error
255 passed in 1.35s

python3 -m py_compile topos/src/topos/actions/execute.py \
  topos/tests/test_p78_action_kernel.py
# exit 0

git diff --check
# exit 0
```

```text
timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests -q -W error
1192 passed, 3 skipped in 146.99s (0:02:26)
```

Self-review reruns after expanding the adversarial oracle:

```text
PYTHONPATH=/tmp/p78-baseline/topos/src topos/.venv/bin/python -m pytest \
  topos/tests/test_p78_action_kernel.py -q -W error \
  -k 'differential or gate_ordering_proof' --confcutdir=topos/tests
62 passed, 3 deselected in 0.20s

PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests/test_p78_action_kernel.py topos/tests/test_actions.py \
  topos/tests/test_p72_kill_update.py -q -W error
316 passed in 1.20s

timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests -q -W error
1253 passed, 3 skipped in 151.25s (0:02:31)
```

## Known gaps / open items

None.
