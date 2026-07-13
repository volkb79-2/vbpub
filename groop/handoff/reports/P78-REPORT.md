# P78 — Action Kernel Gate-Chain Extraction — Blocked Report

## Status

**BLOCKED before implementation.** No production source was changed.

The P78 handoff contains incompatible mandatory requirements for P49's stale
set-property guard:

- Required contracts 1 and 2 classify the stale re-read as a pre-argv verb
  gate and require it to run before the pre-audit write.
- The `main`-based implementation writes the durable pre-audit record before
  that stale re-read. On a stale result it then writes a post-audit refusal
  record.
- Required contract 3 and the `Escalate-if` header forbid changing observable
  audit behavior during this extraction.

A package-venv probe established the current stale result exactly:

```text
outcome=stale
audit_outcome=None
stderr=current memory.high value changed (1024 -> 2048); preview again with the fresh value
audit records=2; stages=[pre, post]
```

Moving stale detection before pre-audit would remove both records; retaining
them leaves a named verb gate after pre-audit. The handoff says this is a
BLOCKED condition, so the extraction has not been attempted.

## What was built

- An ignored package virtual environment at `groop/.venv`, with editable groop
  and pytest installed for validation.
- P78 resumability evidence in `handoff/reports/P78-LOG.md`.

## Deviations from handoff

The requested executor extraction, action tests, and architecture-map update
were not made because the handoff's own escalation rule prohibits a choice
between its conflicting audit-preservation and gate-placement contracts.

## Proposed contract changes

Maintainer direction is required for one of these mutually exclusive choices:

1. Preserve current behavior: exempt P49's stale re-read from P78's
   pre-audit verb-gate rule, keeping its existing `pre`/`post` audit pair.
2. Make stale detection a true pre-audit gate: explicitly authorize removal of
   those stale-path audit records and update the byte-identical/audit-equality
   acceptance oracle accordingly.

No public API or `actions/__init__.py.__all__` change is proposed.

## Test evidence

All results below were run from the repository root in the package virtual
environment (`groop/.venv`) with `PYTHONPATH=groop/src`.

```text
PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests/test_actions.py groop/tests/test_p72_kill_update.py -q -W error
251 passed in 1.32s
```

```text
timeout 900 env PYTHONPATH=groop/src groop/.venv/bin/python -m pytest \
  groop/tests -q -W error
1188 passed, 3 skipped in 154.15s (0:02:34)
```

The documented P79 failure did not reproduce in this venv (the installed
dependency path exercised a passing case); no other failure occurred. No Python
source changed, so a changed-file `py_compile` gate is not applicable.

## Known gaps / open items

- The P78 gate-chain extraction remains unimplemented pending the required
  contract decision above.
