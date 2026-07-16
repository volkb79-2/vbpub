# P78 Work Log

## Context

- Branch: `feat/topos-p78-action-kernel-gate-extraction`
- Worktree: `/workspaces/vbpub/.worktrees/topos-p78-action-kernel-gate-extraction`
- Base commit: `61b6aa1` (`main`)
- Package: P78 - Action kernel gate-chain extraction
- Current objective: Extract the four action executors only if every existing
  observable result and audit behavior can be preserved.

## Timeline

```text
2026-07-13 UTC
- Action: Read the P78 handoff, standing contracts/log template, all four
  execute.py executors, and their per-verb helpers.
- Commands: sed/rg inspection of the bounded action-area files; git log/status.
- Files changed: none.
- Result: execute.py is 1,438 lines and has four copied gate/audit/runner
  chains. The set-property stale check is located after the pre-audit write.
- Follow-up: establish the existing stale-path audit behavior before editing.

2026-07-13 UTC
- Action: Created the ignored package virtual environment and installed topos
  editable plus pytest.
- Commands: python3 -m venv topos/.venv; topos/.venv/bin/python -m pip install
  -e topos; topos/.venv/bin/python -m pip install pytest.
- Files changed: none (topos/.venv is ignored).
- Result: package-venv baseline is available.
- Follow-up: run the focused action baseline and prove the stale/audit ordering.

2026-07-13 UTC
- Action: Ran the focused package-venv baseline and a direct stale-plan probe.
- Commands: PYTHONPATH=topos/src topos/.venv/bin/python -m pytest
  topos/tests/test_actions.py topos/tests/test_p72_kill_update.py -q -W error;
  direct execute_set_property() probe with planned_current_value=1024 and a
  reader returning 2048.
- Files changed: none.
- Result: 251 passed. The stale result is outcome=stale, audit_outcome=None,
  stderr="current memory.high value changed (1024 -> 2048); preview again with
  the fresh value", and the audit file contains two records in order:
  pre, post.
- Follow-up: blocked; record the conflict and do not refactor.

2026-07-13 UTC
- Action: Starting the read-only full package-venv test gate requested by the
  handoff after documenting the blocker.
- Commands: timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python -m
  pytest topos/tests -q -W error.
- Files changed: P78 log/report only.
- Result: 1188 passed, 3 skipped in 154.15s. The documented P79 failure did
  not reproduce because this package venv has the installed dependency path;
  no other failure occurred.
- Follow-up: record the exact baseline tail, run diff validation, then commit
  the blocked handover artifacts.

2026-07-13 UTC
- Action: Ran the final whitespace validation for the handover artifacts.
- Commands: git diff --check.
- Files changed: P78 log/report only.
- Result: clean (exit 0). No Python source changed, so no changed-file compile
  command applies.
- Follow-up: commit the blocked handover artifacts on the requested branch.
```

## Baseline refusal/audit evidence

The P78 handoff names P49's stale re-read as a pre-argv verb gate and requires
every verb gate to run before the pre-audit write. The existing `main` behavior
does not meet that premise:

| Verb / failure | Outcome | Audit outcome | Stderr | Audit records |
|---|---|---|---|---|
| `set-property` / stale `memory.high` | `stale` | `None` | `current memory.high value changed (1024 -> 2048); preview again with the fresh value` | `pre`, then `post` |

Moving that named gate before the pre-audit write as P78 contract 2 requires
would remove both existing audit records. Leaving it after the pre-audit write
preserves behavior, but violates required contracts 1 and 2. There is no shared
chain implementation that can satisfy both requirements.

## Decisions

- Decision: Stop without changing `execute.py`.
  Reason: The handoff's explicit `Escalate-if` condition blocks work when an
  extraction cannot preserve existing audit behavior byte-for-byte. The stale
  check's current post-pre-audit placement makes the required pre-audit placement
  observably different.
  Impact: No action behavior, public API, or tests have been changed.

## Blockers

- Blocker: P78 contracts conflict with current `execute_set_property` behavior.
  Tried: Verified the source order and ran a package-venv probe against the
  `main`-based branch.
  Needed: Maintainer direction to either (a) preserve the existing stale audit
  pair and exempt stale detection from the pre-audit verb-gate rule, or (b)
  authorize the behavior/audit change and update the acceptance oracle.

## Validation

```bash
PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests/test_actions.py topos/tests/test_p72_kill_update.py -q -W error
# 251 passed in 1.32s

timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python -m pytest \
  topos/tests -q -W error
# 1188 passed, 3 skipped in 154.15s (0:02:34)

git diff --check
# clean (exit 0)
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Focused package-venv baseline recorded.
- [x] Known blocker documented.
- [x] Feature branch committed (blocked handover artifacts).

## Resumption and implementation

```text
2026-07-13 UTC
- Action: Read the reconciliation package, P78/P80 handoffs, blocker evidence,
  action executors/helpers, standing contracts, and handoff guidance.
- Decision: Preserve the P49 stale refusal's durable pre/post audit pair. Model
  stale detection as a named post-audit revalidation gate, alongside the
  existing target revalidation, while all other verb gates remain pre-audit.
- Reason: This preserves the more complete audit trail and every existing
  observable result; moving stale before pre-audit would erase both records.
- Files changed: execute.py, a new P78 regression test, P78/P80 handoffs,
  architecture map, and P78 report/log.

2026-07-13 UTC
- Action: Extracted `_execute_gated()` and routed execute_plan,
  execute_set_property, execute_kill, and execute_update through it.
- Result: execute.py reduced from 1,438 to 1,237 lines. Public signatures and
  test seams are unchanged. The stale path still writes `pre`, then `post` and
  returns outcome=stale with its original stderr.
- Validation: `PYTHONPATH=topos/src topos/.venv/bin/python -m pytest
  topos/tests/test_p78_action_kernel.py topos/tests/test_actions.py
  topos/tests/test_p72_kill_update.py -q -W error` => 255 passed in 1.35s.
- Validation: `python3 -m py_compile topos/src/topos/actions/execute.py
  topos/tests/test_p78_action_kernel.py` => exit 0.
- Validation: `git diff --check` => exit 0.
- Follow-up: run the required full suite, record its result, perform final
  diff review, and commit on this branch without merging.

2026-07-13 UTC
- Action: Ran the required full package test gate from the repository root.
- Command: `timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python -m
  pytest topos/tests -q -W error`.
- Result: 1192 passed, 3 skipped in 146.99s (0:02:26). No failures; the
  package virtual environment reproduced no P79 zstandard failure.
- Follow-up: final source/doc review, whitespace check, self-review record,
  then commit without merge.

2026-07-13 UTC
- Action: Re-ran the handoff's exact focused action gate after final edits.
- Command: `PYTHONPATH=topos/src topos/.venv/bin/python -m pytest
  topos/tests/test_actions.py topos/tests/test_p72_kill_update.py -q -W error`.
- Result: 251 passed in 0.83s. `py_compile` for execute.py and the new P78
  test, plus `git diff --check`, completed with exit 0.

2026-07-13 UTC - self-review pass #1
- Action: Read the committed diff mechanically against the standing
  self-review template and P78 differential-taxonomy/gate-ordering oracles.
- Finding: The first P78 regression file sampled the taxonomy and proved
  double-failure ordering for only kill/update. It did not mechanically prove
  every common failure for every verb or ordering for plan/set-property.
- Fix: Expanded `test_p78_action_kernel.py` to pin exact `(outcome,
  audit_outcome, stderr)` triples for every common failure across all four
  verbs, every verb-specific gate, and a two-failing-gates ordering case for
  each verb.
- Differential command: extracted `HEAD^` action sources to
  `/tmp/p78-baseline`, then ran the same `differential or
  gate_ordering_proof` tests with that source tree first on `PYTHONPATH`.
- Result: pre-extraction tree 62 passed, 3 deselected; extracted tree 65
  passed. The golden refusal table and ordering winners are identical.
- Production finding: none. Source inspection plus the differential run found
  no dropped/reordered verb gate and required no execute.py change.
- Follow-up: rerun focused and full package gates with the expanded tests,
  update REPORT/SELFREVIEW with real tails, and commit the pass separately.

2026-07-13 UTC - self-review validation complete
- Focused command: `PYTHONPATH=topos/src topos/.venv/bin/python -m pytest
  topos/tests/test_p78_action_kernel.py topos/tests/test_actions.py
  topos/tests/test_p72_kill_update.py -q -W error`.
- Focused result: 316 passed in 1.20s.
- Full command: `timeout 900 env PYTHONPATH=topos/src topos/.venv/bin/python
  -m pytest topos/tests -q -W error`.
- Full result: 1253 passed, 3 skipped in 151.25s (0:02:31).
- Compile/diff result: changed Python files compile; `git diff --check` exits
  0.
- Follow-up: write the final self-review finding and commit this pass
  separately on the P78 branch without merging.
```
