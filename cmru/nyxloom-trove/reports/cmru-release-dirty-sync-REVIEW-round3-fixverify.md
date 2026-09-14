---
schema: 1
project: cmru
kind: review
status: complete
verdict: REJECT
---

# CMRU dirty-main cleanup — round-3 mutation-survivor fix verification

**Reviewed tip:** `08692bf22b657d6c0f8bb7f40c6492d39bf26245` on
`cmru-release-dirty-sync`, in
`/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`. The worktree was
clean before this report was created. The repair range `f6972c98..08692bf2`
changes only `cmru/src/cmru/transaction.py` and
`cmru/tests/test_release_transaction.py`.

## Verdict: REJECT

The cleanup implementation passes the focused suites and real temporary-Git
probes, including a genuinely reachable interrupted-rebase state. However,
two of the three survivor-closing oracles are not acceptable as gate-grade
behavioral evidence:

1. The frozen-result oracle kills a private dataclass representation mutant,
   but no shipped product contract makes this private object immutable and no
   production path can currently mutate it.
2. The boolop oracle reaches its post-failure active-rebase branch by combining
   a pre-rebase-hook failure with a monkeypatched active state. Git does not
   leave rebase state after that hook failure; the fixture is causally
   manufactured. A separate live probe proves the state is reachable after a
   real process interruption, but the committed oracle does not exercise that
   path.

These are evidence blockers, so this review does not authorize the controller
to rerun the registered gate.

## Ranked findings

### 1. P1 — the `frozen=True` survivor is killed only by an implementation-detail test

`_SyncLocalMainResult` is a private two-scalar carrier at
`cmru/src/cmru/transaction.py:65-70`. The helper constructs it and the CLI
reads `.ok`/`.reason` immediately (`transaction.py:1191-1316` and
`cli.py:111-117`); there is no production mutation, persistence, or
cross-component handoff whose correctness depends on object immutability.

The new test at `cmru/tests/test_release_transaction.py:1901-1910` directly
instantiates the private class and asserts that assignments raise
`dataclasses.FrozenInstanceError`. A controlled in-memory replacement of just
that decorator produced:

```text
immutability mutant: assignment permitted; new oracle would fail as intended
fixed result: assignment rejected
```

Thus it does fail under `True->False`, but it proves only the generated
dataclass setter behavior. It does not prove a release-cleanup observable, and
the mutant is behaviorally equivalent for the current production call graph
unless immutability is made an explicit contract. This is the hollow
implementation-detail pattern proscribed by `AUTHORING.md` §3b(C), not a
meaningful fix verification.

**Disposition:** survivor syntactically killed, but not legitimately closed.

### 2. P1 — the `And->Or` test manufactures the state it claims to classify

At `cmru/tests/test_release_transaction.py:1934-1949`, the test installs a
`pre-rebase` hook that exits 42, then forces `_rebase_in_progress()` to return
`False` for the preflight and `True` after the failed rebase. It also fabricates
the abort result rather than running `git rebase --abort`.

The real Git control measurement for the same hook failure was:

```text
pre-rebase: exit=1 rebase_merge=False rebase_apply=False unmerged=False status=''
```

So that hook cannot produce the active state used by the test. The state itself
is reachable, but through a different defensive transition: a `post-rewrite`
hook terminating the rebase process after rebase state is created. The live
probe below exercised that real transition and the repaired code classified
both abort outcomes correctly. That independent evidence validates the
implementation, but it does not make the committed hook-plus-monkeypatch
oracle non-manufactured.

**Disposition:** current source behavior verified live; the committed
survivor-closing oracle is not accepted as sufficient regression evidence.

### 3. P2 — the `check=False` survivor is properly closed

The parameterized test at `cmru/tests/test_release_transaction.py:1913-1961`
raises `CalledProcessError` whenever the abort call is made with anything other
than `check=False`. The fixed implementation returns the intended classified
result rather than leaking that exception. The isolated tests passed, and the
real interrupted-rebase probe also passed the failed-abort case with
`no_exception=True`.

**Disposition:** survivor closed.

## Survivor matrix

| registered survivor at `f6972c98` | evidence | disposition |
|---|---|---|
| `transaction.py:65` `True->False` | In-memory mutant permits field assignment; fixed result rejects it. The test is private-representation-only. | **Not accepted** |
| `transaction.py:1234` `False->True` | Isolated abort test fails on a check-true boundary; live failed-abort state returns a classified result with no exception. | **Closed** |
| `transaction.py:1253` `And->Or` | Current nested logic passes a real interrupted-rebase state; committed test uses a fabricated hook/state pairing. | **Not accepted as gate-grade oracle** |

The controlled state truth table for the registered first-`And` mutant was:

```text
registered And->Or survivor: original condition=False; first-And mutant=True for active/successful-abort/no-conflict state
```

## Re-audit of prior accepted blockers

- **Ignored files/directories:** PASS. `transaction.py:1201-1209` uses
  `--untracked-files=all --ignored` before either rebase command. The live
  ignored-file/directory probe preserved content and `main`, with no rebase or
  abort.
- **Genuine conflicts versus hooks/other failures:** PASS. The real conflict
  probe produced `genuine conflict` only with unmerged index entries and
  successfully aborted. The pre-rebase hook probe produced an indeterminate
  non-conflict result with no abort.
- **Active rebase conservatism:** PASS for observable safety. A real active
  conflict left in progress was refused without a new rebase or abort; state
  and status were preserved. Its diagnostic was the conservative
  non-current-main refusal because Git reports no current branch while that
  rebase is active.
- **Public API compatibility:** PASS. `sync_local_main(repo_root)` still
  returns a boolean through `return _sync_local_main_result(repo_root).ok`;
  the focused and full transaction suites cover both results.

## Commands and outputs

All test and probe commands were run serially from `cmru/` under
`nice -n 19 ionice -c 3`, after a fresh `/proc/pressure/cpu` read. The fresh
PSI included:

```text
some avg10=22.17 avg60=20.85 avg300=14.56 total=40723209464
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

The focused command sequence was:

```text
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_release_transaction.py -k 'sync_local_main or parent_reverts_workflow or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
  17 passed, 89 deselected in 5.30s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_ki12_cli_wiring.py -k 'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q
  1 passed, 5 deselected in 0.37s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_cli_release_resume_adversarial.py -q
  1 passed in 0.46s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_release_transaction.py -q
  106 passed in 13.51s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_ki12_cli_wiring.py -q
  6 passed in 1.15s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_release_transaction_final_adversarial.py -q
  10 passed in 1.06s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_version_transaction_remaining.py -q
  9 passed in 0.69s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_noncli_final_branches.py -q
  5 passed in 0.28s
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_release_transaction.py -k 'sync_local_main_result_keeps_its_captured_outcome_immutable or sync_local_main_classifies_active_non_conflict_after_abort_attempt' -q
  3 passed, 103 deselected in 1.48s
```

Repair-range check:

```text
nice -n 19 ionice -c 3 git diff --check f6972c98..08692bf2
  exit 0, no output
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src/cmru
  exit 0
```

The consolidated live invocation was:

```text
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python - <<'PY'
  # temporary bare remotes/clones; visible and ignored dirt, clean success,
  # real conflict, pre-rebase hook failure, and an already-active rebase;
  # subprocess argv was observed and every temporary repository was removed.
PY
```

It completed with exit 0 and printed:

```text
visible-dirty: ok=False rebase_calls=[] abort_calls=[] ref_preserved=True content_preserved=True
ignored-file-directory: ok=False rebase_calls=[] abort_calls=[] ref_preserved=True content_preserved=True
clean-success: ok=True main_equals_origin=True status='' rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[]
real-conflict: ok=False rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[['git', 'rebase', '--abort']] status='' genuine_conflict=True
hook-failure: ok=False rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[] ref_preserved=True status='' indeterminate=True
active-rebase: initial_exit=1 state_preserved=True rebase_calls=[] abort_calls=[] status_preserved=True conservative_refusal=True reason_class=non-current-main
ALL CONSOLIDATED LIVE REPOSITORY/WORKTREE PROBES: PASS
CONSOLIDATED_LIVE_REPOSITORY_WORKTREE_PROBES_EXIT=0
```

The separate reachable defensive probe used a real `post-rewrite` hook that
terminated `git rebase` after state creation, then tested successful abort and
an injected failed abort. It printed:

```text
interrupt-abort-success: ok=False rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[['git', 'rebase', '--abort']] state_after=False status='' ref_preserved=True classified_successful_abort=True
interrupt-abort-failure: ok=False rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[['git', 'rebase', '--abort']] state_after=True status='' no_exception=True classified_failed_abort=True
REACHABLE_DEFENSIVE_REBASE_PROBES: PASS
REACHABLE_DEFENSIVE_REBASE_PROBES_FINAL_EXIT=0
```

No mutation campaign, registered gate, merge, release, or Docker gate was run.
No product file, prior review report, `/workspaces/dstdns`, shared `main`, or
other worktree was modified. The only intended write in this review is this
new report.
