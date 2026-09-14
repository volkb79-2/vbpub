---
schema: 1
project: cmru
kind: handoff
status: complete
---

# CMRU release cleanup after dirty-main failure

## Objective

Fix the release-transaction cleanup path exposed by the 2026-09-13 failure
recorded in `/workspaces/vbpub/cmru.release.log`. The parent was invoked with
`--allow-uncommitted`; the child correctly failed its run-gate, but the parent
then attempted `git rebase origin/main` from a dirty caller `main`, producing
`error: cannot rebase: You have unstaged changes` followed by the misleading
`fatal: no rebase in progress`. A future failed release must retain its useful
failure result without adding noisy secondary Git errors or pretending the
caller checkout was synchronized.

## Read first

1. `/workspaces/vbpub/AGENTS.md` and canonical doctrine already supplied to this
   worktree; obey all dirty-file, gate, commit, and no-dstdns rules.
2. `cmru.release.log` at the repository root (the incident evidence).
3. `cmru/src/cmru/transaction.py`, especially `sync_local_main`.
4. `cmru/src/cmru/cli.py`, release success, plan-refused, and child-failure
   cleanup branches around lines 2360–2425.
5. `cmru/tests/test_release_transaction.py` sync tests around 1500 and parent
   failure tests around 400.
6. `cmru/docs/SPEC.md` S-CLI.5/S-CLI.5a and failure behavior;
   `cmru/docs/RELEASE-TRANSACTIONS.md` corresponding operational guidance;
   `cmru/CHANGES.md`; and `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`.

## Required behavior

- Preserve the existing boolean API of `transaction.sync_local_main` unless a
  narrowly justified alternative is needed. When the caller currently has
  branch `main`, fetch `origin/main` as today, then inspect the caller
  worktree. If tracked or untracked changes exist, return failure without
  invoking rebase or rebase-abort, leave both the dirty files and local `main`
  ref untouched, and make the reason observable to the caller/operator.
- Do not change the safe non-current-branch behavior: a clean checkout may
  fast-forward its local `main`; a diverged local `main` is not force-moved.
- Distinguish a dirty-checkout refusal from a genuine rebase conflict in user
  diagnostics, or provide one accurate message that names both possible causes
  and the exact clean-checkout remedy. No message may claim a conflict when
  status proves the checkout is dirty. Cleanup branches that currently ignore
  a false sync result must handle/report it consistently, including child
  failure and plan refusal.
- Do not make `--allow-uncommitted` include dirty data in the immutable remote
  release snapshot. It only permits the preflight check already documented.
- Add behavioral regression tests that construct a dirty current `main` with a
  remote advance and prove: no rebase is attempted, local content/ref remains
  unchanged, and the diagnostic is accurate. Cover the parent cleanup false
  result (at least child-failure; plan-refusal if implementation touches it).
  Keep existing clean rebase and real-conflict tests green.
- Update both normative/user-facing CMRU documents and `CHANGES.md` in the same
  change. Add a new open/landed issue at the next appropriate ID in CMRU's
  canonical backlog, with the incident, root cause, and shipped behavior. Keep
  docs truthful and examples/config schemas unchanged unless needed.

## Verification and handoff

Run the focused CMRU transaction/CLI tests serially, plus any directly relevant
adversarial tests available without Docker. Report exact commands and results.
Do not touch `/workspaces/dstdns` or any operator-owned dirty files. Do not
commit from the detached judged RG-55 worktrees. Commit this branch's scoped
files with a clear message and the required `Co-Authored-By: Luna <noreply@anthropic.com>`
trailer. Before committing, verify the diff contains only CMRU source, tests,
spec/docs, backlog, and this report.

If context or call budget approaches the dispatch checkpoint (~120k context or
~60 calls), stop at a coherent boundary: update this BRIEF with completed work,
remaining work, exact tests, and a concise retention prompt, commit the partial
state, and return the commit plus next action. Otherwise complete the bounded
task and return the commit SHA, changed paths, tests, and any residual concern.

## Implementation report

The bounded repair is complete. `transaction.sync_local_main` retains its
`bool` return API and now refuses a dirty current `main` after fetching, before
either rebase command; the guard includes ignored files and directories. Caller
content and the local `main` ref remain untouched. A private per-call result
records the synchronization reason, and the parent reports that exact result on
success, plan refusal, and child-failure cleanup, without guessing that every
clean-rebase failure is a conflict. Clean rebase, clean non-current fast-forward,
and diverged non-current-main behavior remain covered.

Changed paths are limited to CMRU source, behavioral tests, user-facing and
normative documentation, the canonical CMRU backlog, and this report:

- `src/cmru/transaction.py`
- `src/cmru/cli.py`
- `tests/test_release_transaction.py`
- `tests/test_ki12_cli_wiring.py`
- `tests/test_cli_release_resume_adversarial.py`
- `tests/test_cli_release_behind_adversarial.py`
- `tests/test_cli_release_retention_dispatch_adversarial.py`
- `tests/test_cli_release_revert_outcomes_adversarial.py`
- `docs/SPEC.md`
- `docs/RELEASE-TRANSACTIONS.md`
- `README.md`
- `docs/CONSUMERS.md`
- `CHANGES.md`
- `KNOWN_ISSUES_TODO_BACKLOG.md` (KI-28)
- `nyxloom-trove/reports/cmru-release-dirty-sync-BRIEF.md`

Serial verification completed:

- `python -m pytest tests/test_release_transaction.py -k 'sync_local_main or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q` — 7 passed.
- `python -m pytest tests/test_release_transaction.py -k 'sync_local_main' -q` — 8 passed.
- `python -m pytest tests/test_ki12_cli_wiring.py -k 'parent_discards_worktree_on_a_plan_refusal_and_reports_sync_failure' -q` — 1 passed.
- `python -m pytest tests/test_cli_release_resume_adversarial.py -q` — 1 passed.
- `python -m pytest tests/test_release_transaction.py -q` — 91 passed.
- `python -m pytest tests/test_ki12_cli_wiring.py -q` — 6 passed.
- `python -m pytest tests/test_release_transaction_final_adversarial.py -q` — 10 passed.
- `python -m pytest tests/test_version_transaction_remaining.py -q` — 9 passed.
- `python -m pytest tests/test_noncli_final_branches.py -q` — 5 passed.
- `git diff --check` and `python -m compileall -q src/cmru` — passed.

Residual concern: the full Docker-backed `tester-unified` gate was not run;
the requested focused serial suites and directly relevant no-Docker adversarial
suites were run. No files outside this isolated CMRU worktree were touched.
