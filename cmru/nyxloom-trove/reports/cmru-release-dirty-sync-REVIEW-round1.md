---
schema: 1
project: cmru
kind: review
status: complete
verdict: REJECT
---

# CMRU dirty-main cleanup — final adversarial review, round 1

**Reviewed commit:** `d775bb40378c8bcbaf6987e2be4858414cac12a9` (`HEAD` verified
at that SHA in `/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`).

## Verdict: REJECT

The ordinary tracked-plus-visible-untracked case is fixed, but the shipped
guard does not cover ignored untracked files. A live temporary-repository probe
showed that cleanup can run rebase and overwrite caller data. This violates the
primary required invariant and makes the new documentation false for a class
of untracked files.

## Blockers

1. **Ignored untracked caller files are not guarded and can be destroyed.**

   At `cmru/src/cmru/transaction.py:1178`, the guard uses
   `git status --porcelain --untracked-files=all`. That option enumerates
   untracked files that are not ignored; it does not make ignored files
   visible. Because the result is empty, `:1181` invokes `git rebase
   origin/main`.

   Live probe in a temporary bare remote and clones (PSI `full avg10=0.00`,
   therefore the requested live-probe threshold was met) created an ignored
   local `ignored-local.txt`, then advanced `origin/main` with a tracked file
   at that path. Result:

   ```text
   ignored untracked cleanup: result=True rebase_calls=[['git', 'rebase', 'origin/main']] file='remote tracked file\n'
   ```

   The probe exited 1 because its assertion correctly rejected this result.
   The local file changed from `must survive\n` to `remote tracked file\n`.
   The actionable repair is to detect all caller-local untracked content that
   can be overwritten (at minimum include ignored entries, or use a
   deliberately documented complete worktree scan), and add a regression test
   for an ignored untracked file whose path becomes tracked remotely. The
   guard must return false before either rebase command and preserve the file
   and `main` ref.

   This also contradicts the claims in `cmru/README.md:83-86`,
   `cmru/docs/SPEC.md:96-103`, `cmru/docs/RELEASE-TRANSACTIONS.md:116-121`,
   and `cmru/docs/CONSUMERS.md:165-169` that tracked or untracked dirt is
   refused before rebase and left untouched.

2. **The failure diagnostic collapses non-conflict clean-rebase failures into
   “genuine conflict.”**

   `cmru/src/cmru/transaction.py:1182-1184` returns false for every non-zero
   rebase result, while `:1229-1234` labels every false result observed later
   on a clean current `main` as a genuine conflict. A failed pre-rebase hook,
   an already-active rebase, an abort failure, or another Git/host failure can
   leave a clean checkout without being a content conflict. The separate
   post-hoc classifier has no recorded outcome from the preceding sync, so it
   cannot truthfully distinguish these cases; an indeterminate/other failure
   is reported as conflict. Preserve the boolean public API, but retain a
   per-call reason (for example through a private richer helper used by the
   CLI) and report “undetermined/other cleanup failure” unless Git actually
   established a conflict.

## What passed

All commands below were run serially from the target worktree's `cmru/`
directory; each listed command completed with exit 0.

```text
python -m pytest tests/test_release_transaction.py -k 'sync_local_main or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
  7 passed, 84 deselected
python -m pytest tests/test_ki12_cli_wiring.py -k 'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q
  1 passed, 5 deselected
python -m pytest tests/test_cli_release_resume_adversarial.py -q
  1 passed
python -m pytest tests/test_release_transaction.py -q
  91 passed
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

The committed regression test at
`cmru/tests/test_release_transaction.py:1574-1604` covers tracked and
ordinary visible-untracked dirt, no rebase/rebase-abort, ref/content
preservation, and dirty-vs-conflict wording. It does not cover ignored dirt,
which is the gap found above. The plan-refusal, child-failure, and resume
wiring tests passed and preserve the boolean `sync_local_main` API.

## Live probes

`cat /proc/pressure/cpu` reported `full avg10=0.00`, so live probes were safe
under the requested PSI rule. The first temporary-repository harness exited 1
before product execution because the bare remote had no symbolic `HEAD` and
the clone had no local `main`. The corrected harness reached product code:

- tracked plus visible-untracked dirt: **PASS** — false, no rebase or
  rebase-abort, files/ref preserved, dirty diagnostic;
- ignored untracked dirt: **FAIL** — false invariant; rebase ran, returned
  true, and overwrote the ignored file (the blocker above);
- clean successful sync and clean real conflict were run in a separate
  corrected harness, exit 0:

  ```text
  clean successful sync: result=True main_equals_origin=True status=''
  clean rebase conflict classification: result=False status='' genuine_conflict=True dirty_word=False
  ```

Temporary repositories were created under `TemporaryDirectory` and cleaned up.

## Documentation/backlog review and residual risk

The change updates README, SPEC, operational guidance, CONSUMERS, CHANGES, and
KI-28 in the canonical backlog. The cited S-CLI.5a and caller-main cleanup
anchors resolve by inspection; no config examples or schema vocabulary were
changed. However, the touched documents overclaim the behavior until the
ignored-file blocker is repaired. No full Docker-backed `tester-unified` gate
was run; the implementation brief explicitly recorded that omission. No
product files were modified and no commit was made; this review record is the
only worktree change.
