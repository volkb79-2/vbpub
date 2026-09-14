---
schema: 1
project: cmru
kind: review
status: complete
verdict: ACCEPT
---

# CMRU dirty-main cleanup — final verification review, round 3

**Reviewed commit:** `164b114c67d0c11d12c4b1c4f0ec038718ccb53e` (short tip
`164b114c`, parent `48e99c7f`, product repair `6b476268`). Exact `HEAD` was
verified in `/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`.

## Verdict: ACCEPT

Both round-1 blockers remain fixed. The post-repair product code is unchanged:
`git diff 6b476268..HEAD --name-status` shows only the two prior review records
and the modified `cmru/tests/test_release_transaction.py`; there is no source,
CLI, documentation, or backlog code change after `6b476268`. The round-3 test
delta is additive defensive-branch coverage and does not remove or weaken an
existing assertion.

## Blocker verification

- `cmru/src/cmru/transaction.py:1201-1209` uses
  `--untracked-files=all --ignored`, so ignored files and ignored directories
  produce the dirty result before `:1217` can run `git rebase`; neither rebase
  nor rebase-abort is invoked.
- `:1221-1245` requires unmerged index entries (`git ls-files -u`) before the
  genuine-conflict outcome. `:1261-1274` reports hook/other failures as
  indeterminate. `:1296-1303` retains the boolean `sync_local_main` API,
  while the CLI consumes the per-call result at
  `cmru/src/cmru/cli.py:111-117` on success, plan refusal, and child failure.
- The new tests at `cmru/tests/test_release_transaction.py:1632-1666`
  cover ignored-file and ignored-directory preservation; `:1669-1701`,
  `:1766-1863`, and `:1865-2025` cover hook/unknown-state/abort defensive
  outcomes. Existing real-conflict coverage remains at `:1704-1722`.

## Focused commands and results

All commands ran serially from the target worktree's `cmru/` directory and
completed with exit 0:

```text
python -m pytest tests/test_release_transaction.py -k 'sync_local_main or parent_reverts_workflow or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
  15 passed, 89 deselected
python -m pytest tests/test_ki12_cli_wiring.py -k 'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q
  1 passed, 5 deselected
python -m pytest tests/test_cli_release_resume_adversarial.py -q
  1 passed
python -m pytest tests/test_release_transaction.py -q
  104 passed
python -m pytest tests/test_ki12_cli_wiring.py -q
  6 passed
python -m pytest tests/test_release_transaction_final_adversarial.py -q
  10 passed
python -m pytest tests/test_version_transaction_remaining.py -q
  9 passed
python -m pytest tests/test_noncli_final_branches.py -q
  5 passed
git diff --check
  passed
python -m compileall -q src/cmru
  passed
```

## PSI-gated live temporary-git probes

`cat /proc/pressure/cpu` reported `full avg10=0.00`, satisfying the requested
live-probe threshold. The exact probe invocation was:

```text
PYTHONPATH=src python - <<'PY' ...temporary bare-repository probes... PY
```

It used `TemporaryDirectory`, actual local bare remotes/clones, and a
subprocess argv spy; it completed with exit 0 and cleaned its temporary repos.
Observed results:

```text
ignored-file: result_ok=False rebase_calls=[] ref_preserved=True content_preserved=True dirty_reason=True
ignored-directory: result_ok=False rebase_calls=[] ref_preserved=True content_preserved=True dirty_reason=True
clean-success: result_ok=True main_equals_origin=True status='' rebase_calls=[['git', 'rebase', 'origin/main']]
real-conflict: result_ok=False rebase_calls=[['git', 'rebase', 'origin/main'], ['git', 'rebase', '--abort']] abort_calls=[['git', 'rebase', '--abort']] status='' genuine_conflict=True dirty_word=False
hook-failure: result_ok=False rebase_calls=[['git', 'rebase', 'origin/main']] abort_calls=[] ref_preserved=True status='' indeterminate=True genuine_conflict=False
ALL LIVE PROBES: PASS
```

The ignored-file probe made the former ignored path remotely tracked; the
ignored-directory probe added a remotely tracked entry inside the former
ignored directory. Both preserved local content and `main` and ran no rebase
or abort. The clean successful sync reached `origin/main`. The real conflict
left a clean checkout after abort and was called genuine only with unmerged
conflict evidence. The pre-rebase hook exited 42; no abort ran, the ref/status
remained unchanged, and the result was indeterminate.

## Documentation and residual risk

No documentation, backlog, schema, or cross-document anchor changed in this
test-only round; round 2's documentation review therefore remains applicable.
The full Docker-backed `tester-unified` gate was not run. No product files,
`dstdns`, shared `main`, or existing review record were modified, and no
commit was made; this round adds only this review record.
