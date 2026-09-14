---
schema: 1
project: cmru
kind: evidence
status: complete
---

# CMRU registered-gate coverage repair

## Diagnosis

The registered gate at the pre-repair tip `443e4d757affe270f7183bbc431d843d20e709ae`
ran the CMRU assay successfully but its coverage lane failed. The gate evidence
reports 1,770 passed and 11 skipped, with total coverage `99.90%`; the lane
exited 1 and the enclosing gate exited 1. The coverage artifact identified
only these missing transaction statements:

```text
src/cmru/transaction.py:1257, 1258, 1265, 1266
branches: 1256->1257, 1257->1258, 1257->1265,
          1265->1266, 1265->1273
```

The existing real-Git interrupted-rebase test delegated
`_sync_local_main_result` to a Python child process. Git's real rebase and hook
ran, but the Python classification ran outside the pytest coverage collector.

## Repair oracle

The test in `tests/test_release_transaction.py` now keeps the product call in
pytest's process. The only subprocess boundary is Git itself: a real temporary
bare remote and clones are created, `origin/main` is advanced without a content
overlap, and a repository-local absolute `core.hooksPath` contains real hooks.
The merge backend uses `post-rewrite`; the apply backend uses
`pre-applypatch`/`post-applypatch`. Each hook observes either Git's real
`rebase-merge` or `rebase-apply` state, records that observation, and terminates
the real rebase child after state creation.

The parameterized contract witnesses both requested outcomes:

- with no lock, the real `git rebase --abort` succeeds; the result is false,
  says the in-progress state was aborted successfully, leaves no rebase state,
  and does not claim a content conflict;
- with a real `.git/index.lock` created by the hook, abort fails; the result is
  false, says the in-progress state could not be aborted, leaves the observed
  rebase state, and does not claim a content conflict.

The third parameter makes the existing defensive indeterminacy explicit: the
same real interrupted state and real successful abort are used while only the
`ls-files -u` inspection is made unreadable. It asserts the accurate
"conflict state could not be determined" result. No rebase state,
`_rebase_in_progress`, or abort result is fabricated.

The fixture also runs successfully when a temporary global Git config selects
either supported backend and an unrelated global hooks path; the repository-
local absolute hooks path remains effective.

## Commands and exact results

All focused and expensive commands were run serially after a fresh
`/proc/pressure/cpu` check with `full avg10=0.00`; no container was launched.

```text
nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/test_release_transaction.py \
  -k 'sync_local_main or parent_reverts_workflow or parent_reverts_promotion_and_reports_sync_failure_on_child_failure' -q
17 passed, 89 deselected in 5.65s

nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/test_release_transaction.py -q
105 passed in 23.08s

nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/test_ki12_cli_wiring.py -q
6 passed in 0.33s

temporary GIT_CONFIG_GLOBAL with rebase.backend=merge and unrelated global
core.hooksPath, then the interrupted-rebase selection:
3 passed, 103 deselected in 1.85s

temporary GIT_CONFIG_GLOBAL with rebase.backend=apply and unrelated global
core.hooksPath, then the interrupted-rebase selection:
3 passed, 103 deselected in 1.42s

nice -n 19 ionice -c 3 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests -q --cov=src/cmru --cov-branch --cov-fail-under=100 \
  --cov-report=json:coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
1,779 passed, 3 skipped in 70.84s
```

The final coverage artifact reports `src/cmru/transaction.py` at 613/613
statements and 214/214 branches, with no missing lines or branches. The
focused interrupted-rebase selection itself is 3 passed, including both
requested outcomes and the inspection-indeterminate defensive branch.

```text
git diff --check
passed; no output

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src/cmru
passed; no output
```

The registered full gate was not rerun because its P1/P6 mutation slots were
occupied, as directed. No mutation campaign or CMRU release was run.

## Changed scope

The repair changes only:

- `tests/test_release_transaction.py`
- this new evidence report

Production code, prior immutable review reports, operator files, shared
`main`, `/workspaces/dstdns`, and other worktrees were not modified.

## Remaining coverage gap

None in the local full pytest coverage run: total and transaction statement and
branch coverage are all 100.00%. The only remaining external verification is a
future registered gate rerun when its occupied mutation slots are available.
