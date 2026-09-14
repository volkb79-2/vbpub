---
schema: 1
project: cmru
kind: review
status: complete
verdict: ACCEPT
---

# CMRU release dirty-sync — final-2 reviewer report

**Reviewed tip:** `2eff6bdd22f0b7a603a6a07798c62f67f8bdc709` (exact `HEAD`
verified on `cmru-release-dirty-sync` in
`/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`).

## Verdict: ACCEPT

The previous final-review blocker is closed. The exact tip is a test-only
change to the real interrupted-rebase oracle. The fixture pins a repository-
local absolute `core.hooksPath`, observes either `rebase-merge` or
`rebase-apply`, and passes under both supported rebase backends and an
unrelated global hooks path. It uses real temporary Git repositories and real
subprocesses, with no transaction-state monkeypatch, fake subprocess result,
wall-clock success oracle, or new cleanup leakage.

## Ranked findings

None. No P0, P1, or P2 finding remains at this tip.

## Closure of the previous blocker

The tip diff changes only `cmru/tests/test_release_transaction.py`:

- Lines 1934–1942 create `cmru-test-hooks` inside the temporary repository's
  `.git` directory and set it with `git config --local core.hooksPath`, so an
  ambient global hooks path cannot suppress the fixture hook.
- Lines 1947–1949 record the active state when either `rebase-merge` or
  `rebase-apply` exists.
- Lines 2008–2017 resolve the repository's actual Git directory and accept
  either layout. Lines 2019–2024 preserve the backend-specific ref assertion
  when a deliberately failed abort leaves state behind.
- The 60-second `communicate()` limit is only a process-group termination
  failsafe. No expected result depends on elapsed time; timeout is an explicit
  test failure.
- The fixture's process is a real Python child calling
  `_sync_local_main_result`; the hook is a real executable that interrupts the
  real Git rebase. The test function contains no `monkeypatch`, fabricated
  `CalledProcessError`, or fake `subprocess.run` result.

The configuration matrix ran both parameter cases in all four combinations:

| global `rebase.backend` | unrelated global `core.hooksPath` | result |
|---|---|---|
| `merge` | absent | 2 passed |
| `merge` | `/tmp/cmru-unrelated-hooks-that-do-not-exist` | 2 passed |
| `apply` | absent | 2 passed |
| `apply` | `/tmp/cmru-unrelated-hooks-that-do-not-exist` | 2 passed |

The `apply` plus unrelated-hooks case was also run with the two parameter
nodes in reverse order: 2 passed. Each parameter owns a fresh
`_OriginAndClone`, so the cases do not depend on ambient ordering.

## Compatibility and inherited cleanup contracts

- `_SyncLocalMainResult` is now a private `NamedTuple` with the same
  positional field order and default (`ok`, `reason=""`). Production code
  consumes only `.ok` and `.reason`; no production or test path requires
  dataclass identity, `asdict`, or dataclass replacement semantics.
- `transaction.sync_local_main(repo_root)` remains the public
  `-> bool` wrapper and returned exact `bool` values in an independent live
  clean-success and dirty-refusal probe.
- The full `sync_local_main` contract remains green: clean current-main
  synchronization, local-commit rebase, tracked/visible-untracked and
  ignored-file/directory refusal before rebase, genuine-conflict abort,
  indeterminate hook/unknown-state handling, active-rebase conservatism,
  failed-abort reporting, clean non-current fast-forward, and diverged
  non-current-main preservation.
- Parent cleanup reporting remains covered for success, plan refusal, child
  failure, and resume paths. Existing promotion/revert, retention, and
  release-transaction cleanup tests remain green.
- The tip does not change `transaction.py`, `cli.py`, documentation,
  backlog, schemas, public configuration, or release behavior. The earlier
  dirty-main, ignored-content, diagnostic, and immutable-snapshot contracts
  therefore remain the ones reviewed in the prior rounds.

## Commands and results

All commands were run serially from the pinned worktree. The complete local
CMRU suite was run with `-n0`:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q -n0
  1778 passed, 3 skipped in 77.01s
```

Focused and directly related suites:

```text
python -m pytest tests/test_release_transaction.py -k \
  'sync_local_main or parent_reverts_workflow or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
  16 passed, 89 deselected
python -m pytest tests/test_ki12_cli_wiring.py -k \
  'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q
  1 passed, 5 deselected
python -m pytest tests/test_cli_release_resume_adversarial.py -q
  1 passed
python -m pytest tests/test_release_transaction.py -q
  105 passed
python -m pytest tests/test_ki12_cli_wiring.py -q
  6 passed
python -m pytest tests/test_release_transaction_final_adversarial.py -q
  10 passed
python -m pytest tests/test_cli_release_behind_adversarial.py \
  tests/test_cli_release_retention_dispatch_adversarial.py \
  tests/test_cli_release_revert_outcomes_adversarial.py \
  tests/test_cli_release_resume_adversarial.py -q
  9 passed
python -m pytest tests/test_version_transaction_remaining.py \
  tests/test_noncli_final_branches.py -q
  14 passed
python -m pytest tests/test_transaction_boundaries_adversarial.py \
  tests/test_transaction_dispatch_adversarial.py -q
  10 passed
```

The real-Git configuration matrix used temporary `GIT_CONFIG_GLOBAL` files
with `GIT_CONFIG_SYSTEM=/dev/null`; all four cases passed. A corrected
independent live probe passed the reversed parameter order, the public bool
checks, and the NamedTuple field/immutability checks:

```text
REVERSED_PARAMETER_ORDER: PASS
PUBLIC_BOOL_AND_NAMEDTUPLE: PASS
```

The focused fixture cleanup delta was measured against a pre-run `/tmp` set:

```text
2 passed in 1.62s
new cmru_txn_test temporary repositories after focused fixture: 0
FOCUSED_FIXTURE_CLEANUP_DELTA: PASS
no rebase process remained
```

An inventory found 14 older `/tmp/cmru_txn_test_*` directories, all timestamped
2026-07-29 through 2026-08-16, before this review's test runs. They were left
untouched under the requested read-only review scope; no directory was created
by the reviewed fixture run. No `git-rebase` process remained.

Diff and source checks:

```text
git diff --check 2eff6bdd^ 2eff6bdd
  passed; no output
git diff 2eff6bdd^ 2eff6bdd -- cmru/src cmru/README.md cmru/docs \
  cmru/CHANGES.md cmru/KNOWN_ISSUES_TODO_BACKLOG.md
  passed with no output; the exact tip has no source/docs/backlog change
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=cmru/src python -m compileall -q cmru/src/cmru
  passed
git diff-tree --no-commit-id --name-status -r 2eff6bdd
  M cmru/tests/test_release_transaction.py
```

One preliminary independent-probe invocation had an import-path mistake
(adding `cmru` instead of `cmru/src`), failed before creating a temporary
repository, and was discarded. The corrected invocation above is the evidence
used for this verdict.

No Docker-backed registered `run-gate.py gate` or release was launched by this
read-only review. No product file, prior report, shared `main`, or
`/workspaces/dstdns` was modified, and no commit was made. The only intended
write is this report.
