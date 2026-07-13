# P78 — Action Kernel Gate-Chain Extraction — Implementation Report

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
  infrastructure in `src/groop/actions/execute.py`.
- Routed all four public executors through that one gate/audit/runner sequence.
- Kept the runner, clock, identity, root-check, protected-check, and
  current-memory-reader Python API seams unchanged.
- Added P78 tests for one-chain delegation, stale audit preservation,
  per-verb ordering, and the pre/post audit shape for all four success paths.
- Updated the architecture map and the P78 contract text to document the two
  gate categories.
- Updated P80's handoff: its install-specific checks must use P78's ordered
  pre-audit gate extension; the P49 post-audit stale exception does not apply
  to install actions. P80's audit record shape is unchanged.

## Observable-behavior evidence

The differential refusal taxonomy was rebuilt against the extracted code by
the unchanged P46/P49/P72 action suites plus the P78 ordering probes. The
focused run covers non-admin, confirmation, root, timeout, audit-path,
identity, pre/post-audit, target, signal, force, protected-target, systemd
target, below-current/unverifiable usage, runner OSError/timeout, and stale
paths. All 255 cases passed with the same exact assertions. The stale case
continues to return `outcome=stale`, `audit_outcome=None`, its original stderr
text, and `pre`, then `post` records; no deliberate exception is required.

`execute.py` changed from 1,438 to 1,237 lines. The reduction is the removed
copied common execution bodies; the remaining per-verb lines declare their
gates and immutable argv construction explicitly.

## Deviations from handoff

None after the reconciliation package's contract update. The former contract
2 wording incorrectly classified P49 stale detection as pre-audit. It is now
the documented post-audit exception that preserves contract 3 exactly.

## Validation

All commands were run from the repository root using the package virtual
environment at `groop/.venv` with `PYTHONPATH=groop/src`.

```text
PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_actions.py groop/tests/test_p72_kill_update.py -q -W error
251 passed in 0.83s

PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_p78_action_kernel.py groop/tests/test_actions.py \
  groop/tests/test_p72_kill_update.py -q -W error
255 passed in 1.35s

python3 -m py_compile groop/src/groop/actions/execute.py \
  groop/tests/test_p78_action_kernel.py
# exit 0

git diff --check
# exit 0
```

```text
timeout 900 env PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests -q -W error
1192 passed, 3 skipped in 146.99s (0:02:26)
```

## Known gaps / open items

None.
