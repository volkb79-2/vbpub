---
schema: 1
project: cmru
kind: review
status: complete
verdict: ACCEPT
---

# CMRU dirty-main cleanup — verification review, round 2

**Reviewed commit:** `6b476268f70c85862c582a4f22de6d05d467e295` (exact `HEAD`
verified in `/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`). The prior
round-1 review and the complete `main...HEAD` diff were read.

## Verdict: ACCEPT

Both round-1 blockers are repaired. Ignored files and ignored directories are
detected before cleanup can invoke either rebase command, and the per-call
result carries the actual outcome instead of classifying a later checkout.

## Evidence for the repaired blockers

- `cmru/src/cmru/transaction.py:1201-1209` runs status with
  `--untracked-files=all --ignored` and returns the dirty result before
  `:1217` can invoke `git rebase origin/main`.
- `:1221-1245` inspects `git ls-files -u` and rebase state before invoking
  `git rebase --abort`; the genuine-conflict message is emitted only when
  unmerged index entries were established by the failed rebase.
- `:1261-1274` reports hook/other failures as indeterminate, without claiming
  a conflict. `:1296-1303` preserves the historical boolean
  `sync_local_main(repo_root) -> bool` API; the CLI reports the private
  per-call result at `cmru/src/cmru/cli.py:111-117` on success, plan refusal,
  and child-failure cleanup (`:2373`, `:2385`, `:2421`).
- The committed tests at
  `cmru/tests/test_release_transaction.py:1632-1666` cover an ignored file
  and ignored directory, assert no `rebase`/`rebase --abort`, and preserve
  both content and the local `main` ref. The hook test at `:1669-1701`
  asserts indeterminate wording and no abort; the real conflict test at
  `:1704-1722` asserts genuine-conflict wording only after aborting a real
  unmerged conflict.

## Focused commands and results

All commands ran serially from the target worktree's `cmru/` directory and
completed with exit 0:

```text
python -m pytest tests/test_release_transaction.py -k 'sync_local_main or parent_reverts_workflow or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
  9 passed, 84 deselected
python -m pytest tests/test_ki12_cli_wiring.py -k 'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q
  1 passed, 5 deselected
python -m pytest tests/test_cli_release_resume_adversarial.py -q
  1 passed
python -m pytest tests/test_release_transaction.py -q
  93 passed
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

## PSI-gated live probes

Command:

```text
cat /proc/pressure/cpu
```

Result included `full avg10=0.00`; the live-probe threshold was satisfied.
The command below created temporary bare remotes and clones under Python
`TemporaryDirectory`, spied on actual subprocess argv, and removed all
temporary repositories on exit:

```text
PYTHONPATH=src python - <<'PY' ...temporary bare-repository probes... PY
```

The command completed with exit 0 and printed:

```text
ignored-file: result_ok=False rebase_calls=[] ref_preserved=True content_preserved=True reason=... caller checkout is dirty ... including ignored files and directories ...
ignored-directory: result_ok=False rebase_calls=[] ref_preserved=True content_preserved=True reason=... caller checkout is dirty ... including ignored files and directories ...
clean-success: result_ok=True main_equals_origin=True status=''
real-conflict: result_ok=False status='' genuine_conflict=True dirty_word=False reason=... genuine conflict ...
hook-failure: result_ok=False abort_calls=[] ref_preserved=True status='' reason=... without establishing a content conflict ... cause is undetermined ...
ALL LIVE PROBES: PASS
```

The ignored-file case used a remote tracked file at the former ignored path;
the ignored-directory case used a remote tracked entry inside the former
ignored directory. Neither invoked rebase or rebase-abort, and both preserved
the local `main` ref and files. The clean success fast-forwarded to
`origin/main`. The conflict probe observed Git's real content conflict and
ended clean after abort. The hook exited 42, left the clean local ref/status
unchanged, invoked no abort, and was reported indeterminate.

## Documentation and residual risk

The current diff keeps the README, SPEC, operational guidance, CONSUMERS,
CHANGES, and KI-28 backlog entry aligned with the repaired ignored-content and
per-call diagnostic behavior. The S-CLI.5a and caller-main-cleanup anchors
resolve by inspection; no config schema or closed vocabulary changed. The
full Docker-backed `tester-unified` gate was not run; all requested focused
no-Docker suites, source compilation, and PSI-approved live probes passed.
No product files, `dstdns`, or shared `main` were modified, and no commit was
made; this review record is the only new file in this round.
