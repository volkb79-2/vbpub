---
schema: 1
project: cmru
kind: review
status: complete
verdict: ACCEPT
---

# CMRU release dirty-sync — final adversarial review

**Reviewed tip:** `700ef5f86ae09cbed1214687cb75e1797c346c30` (exact `HEAD`
verified in `/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`).

## Verdict: ACCEPT

No blocker remains. The exact `443e4d75..700ef5f8` diff changes only the
interrupted-rebase test and adds the gate-repair evidence report; shipped
product code is unchanged from the previously reviewed repair.

## Findings and contract checks

- **Both non-conflict abort outcomes are real and covered in-process.** The
  test at `cmru/tests/test_release_transaction.py:1932-2028` calls
  `_sync_local_main_result` in pytest's process. A real repository-local
  executable hook runs after Git creates actual rebase state, records
  `active`, and terminates Git's real rebase. The no-lock case reaches real
  successful `git rebase --abort`; the real `.git/index.lock` case makes the
  abort fail. The result is captured before fixture teardown, and the lock is
  removed only afterward so teardown cannot manufacture the observed result.
  The inspection-indeterminate parameter is separately explicit and does not
  replace either required real outcome.
- **Configuration isolation is closed.** The fixture uses only local
  `core.hooksPath` under the temporary repository, supports both
  `rebase-merge` and `rebase-apply`, and does not mutate global config. The
  prior auxiliary Python child and its timeout are gone; there is no unbounded
  fixture child to wait on. The only subprocess boundary is Git itself, and
  the finite local hook terminates it deterministically. This is the supported
  Unix context already required by `transaction.py`'s `fcntl` import.
- **API and classifications remain compatible.** `_SyncLocalMainResult` is an
  immutable two-field `NamedTuple` (`ok`, `reason`, with the empty-string
  default); `sync_local_main()` still returns an exact `bool`, and the CLI
  consumes `.ok` plus the captured reason. Ordinary tuple semantics are
  intentional (`bool(_SyncLocalMainResult(False, "sample"))` is `True`, and
  `reversed(...)` yields `(reason, ok)`); no public caller uses the carrier as
  a boolean. The complete relevant transaction/CLI suites and full coverage
  run show no failure-classification regression.

## Live probe evidence

The independent probe used temporary bare remotes and clones, real Git
subprocesses, a temporary global config containing an unrelated global
`core.hooksPath`, and a repository-local absolute hooks path. Git was
`2.55.0`. In all four cases `git config --show-origin --get core.hooksPath`
resolved to `file:.git/config`, the hook recorded `active`, and `ls-files -u`
was empty:

| backend | abort condition | result | state after | main preserved |
|---|---|---|---|---|
| merge | normal | false, “aborted successfully” | none | yes |
| merge | real `index.lock` | false, “could not be aborted” | `rebase-merge` | no (manual inspection required) |
| apply | normal | false, “aborted successfully” | none | yes |
| apply | real `index.lock` | false, “could not be aborted” | `rebase-apply` | yes |

The probe also reported:

```text
PUBLIC_AND_TYPING clean_type=bool clean_value=True dirty_type=bool dirty_value=False namedtuple_is_tuple=True namedtuple_fields=('ok', 'reason') bool_false_payload=True reversed_payload=['sample', False]
```

The focused fixture passed under all four global-backend/global-hooks
combinations, each with `3 passed, 103 deselected` and `CASE_EXIT=0`.
Fixture cleanup measurement reported:

```text
ACTUAL_PYTEST_EXIT=0
NEW_TEMP_REPOSITORIES=[]
FIXTURE_CLEANUP_DELTA=PASS
```

## Test and gate evidence

Commands were run serially with `nice -n 19 ionice -c 3` where applicable;
statuses below are the actual job statuses, not wrapper or pipe statuses.

```text
pytest changed fixture: 3 passed, 103 deselected in 1.78s; exit 0
relevant transaction/CLI/adversarial suites: 149 passed in 16.52s; exit 0
full CMRU pytest + branch coverage: 1779 passed, 3 skipped in 66.92s; exit 0
Required test coverage of 100% reached. Total coverage: 100.00%
TOTAL: 6837 statements, 0 missed; 2498 branches, 0 partial; 100%
compileall: exit 0
git diff --check 443e4d75..700ef5f8: exit 0, no output
live rebase processes after probes: none
```

The full run was admitted by a fresh `full avg10=0.00` CPU PSI reading. No
registered Docker gate or mutation campaign was run: the requested P1/P6
RG-55 mutation slots were occupied. That is the only deferred operational
check; it is not a product or oracle blocker for this test-only coverage
repair.

## Residual risk

The real-Git hook interruption is Unix-specific, but CMRU's shipped transaction
module is already Unix-specific and the live probe establishes the behavior in
that supported environment. No product file, prior report, shared `main`,
`/workspaces/dstdns`, or other worktree was modified. The only intended write
in this review is this report.
