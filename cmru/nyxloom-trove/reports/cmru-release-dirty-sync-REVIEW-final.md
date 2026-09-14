---
schema: 1
project: cmru
kind: review
status: complete
verdict: REJECT
---

# CMRU release dirty-sync — final adversarial review

**Reviewed tip:** `e224579c096834f9d86906afbf2a73a4389775b7` (`HEAD` verified
on branch `cmru-release-dirty-sync` in
`/workspaces/vbpub/.worktrees/cmru-release-dirty-sync`).

## Verdict: REJECT

The `NamedTuple` replacement is behaviorally safe, and the interrupted-rebase
fixture does use real Git state with no monkeypatched transaction state.
However, the new survivor-closing test is still dependent on ambient Git
configuration. A valid `rebase.backend=apply` configuration makes the product
reach and classify the real active state, but the test fails because it checks
only `.git/rebase-merge`. A valid `core.hooksPath` override prevents the hook
from running at all. This leaves the required gate-grade real-Git oracle
environment-dependent and blocks a clean ACCEPT.

## Ranked findings

### P1 — the real interrupted-rebase oracle is not isolated from Git configuration

`cmru/tests/test_release_transaction.py:1934-1945` installs the hook at
`.git/hooks/post-rewrite` without pinning the effective `core.hooksPath`. The
hook itself correctly recognizes both `rebase-merge` and `rebase-apply`, but
`cmru/tests/test_release_transaction.py:1999` asserts only
`.git/rebase-merge`.

Both are observable failures under valid Git configuration:

- With a temporary global config containing `[rebase] backend = apply`, the
  real hook runs, records `active`, and the implementation returns the
  expected successful/failed-abort classifications. The committed test still
  exits 1: its failed-abort parameter reaches the real active state, then
  asserts `rebase-merge.exists() is True` while Git has created
  `rebase-apply`.
- With a temporary global config containing
  `[core] hooksPath = /tmp/cmru-no-such-hooks`, Git does not run the installed
  hook. Both cases return `ok=True` from the child because the rebase completes,
  and the test exits 1 instead of proving that an interrupted rebase was
  reached.

The product helper deliberately supports both layouts at
`cmru/src/cmru/transaction.py:1181-1187`, so the test should assert the
presence/absence of either Git-resolved state path and should pin the hook
path locally (or otherwise make the fixture's hook discovery explicit). Until
then, the second previously rejected survivor is closed only in the review
environment's default configuration, not as a portable behavioral oracle.

## Survivor review

### NamedTuple replacement — PASS

The exact source delta is limited to importing `NamedTuple` and changing the
private `_SyncLocalMainResult` carrier. `ReleaseWorkspace` remains a frozen
dataclass. A repository-wide production-use audit found only the CLI helper
reading `.ok` and `.reason`; `sync_local_main(repo_root)` still returns
`_sync_local_main_result(repo_root).ok`, and all constructors preserve the
existing positional/default shape. No public export, config key, release
format, or documented contract exposes the carrier's dataclass identity,
`asdict` behavior, or exception type. The removed frozen-dataclass test was
therefore correctly not replaced with another private-representation oracle.

The independent public probe verified that the shipped function returned
`type(result) is bool` for both a clean current-main sync and a dirty
current-main refusal, while preserving the expected ref/content behavior.

### Interrupted rebase — implementation PASS, committed oracle REJECT

The new test launches a separate Python process, installs a real
`post-rewrite` hook, observes a real rebase directory before terminating the
Git parent, and uses a real `.git/index.lock` only in the failed-abort case.
It does not monkeypatch `_rebase_in_progress` or `subprocess.run`. In the
default review environment, both parameter cases passed repeatedly. The
independent temporary-bare-repository probe also observed:

```text
block_abort=false: ok=False, rebase_state_after=False,
  in-progress state was aborted successfully
block_abort=true:  ok=False, rebase_state_after=True,
  in-progress state could not be aborted
```

That proves the implementation's two abort classifications and the real
state transition. It does not cure the test's backend/hooks-path dependence
described above.

## Scope, compatibility, and release implications

- `git diff --name-status e224579c^ e224579c` contains only
  `cmru/src/cmru/transaction.py` and
  `cmru/tests/test_release_transaction.py`; no unrelated source, docs, config,
  or release metadata changed at this tip.
- The inherited release repair's README, SPEC, operational guidance,
  CONSUMERS, CHANGES, and KI-28 backlog entry remain aligned with the shipped
  dirty-check and per-call diagnostic behavior reviewed in round 2. This tip
  changes no user-facing capability, public config key, vocabulary, or release
  behavior, so no additional documentation change is required for the
  NamedTuple/test-only adjustment.
- `cmru/run-gate.toml` parses as schema 1 and still declares the assay,
  coverage, mutation, canary, enroll, and conjunction `gate` lanes. The gate
  remains the configured tester-unified/bare-host orchestration; no gate
  configuration was changed by this tip.
- No public API compatibility issue was found: the public synchronization
  function remains boolean, and the private carrier is consumed only through
  its two existing fields.

## Commands and results

All commands ran serially from the target worktree. Product/test files were
not modified. The worktree already contained the earlier untracked
`cmru-release-dirty-sync-REVIEW-round3-fixverify.md`; it was read and left
untouched. This report is the only file written by this review.

```text
git rev-parse HEAD
  e224579c096834f9d86906afbf2a73a4389775b7

python -m pytest tests/test_release_transaction.py \
  -k 'sync_local_main_classifies_real_interrupted_rebase_abort' -q
  2 passed, 103 deselected
```

The focused interrupted-rebase test was run three additional consecutive
times; each run produced `2 passed, 103 deselected`. This checks parameter
order and temporary-repository cleanup under the default environment.

```text
python -m pytest tests/test_release_transaction.py -q
  105 passed
python -m pytest tests/test_ki12_cli_wiring.py -q
  6 passed
python -m pytest tests/test_release_transaction_final_adversarial.py -q
  10 passed
python -m pytest tests/test_cli_release_resume_adversarial.py \
  tests/test_cli_release_behind_adversarial.py \
  tests/test_cli_release_retention_dispatch_adversarial.py \
  tests/test_cli_release_revert_outcomes_adversarial.py -q
  9 passed
python -m pytest tests/test_version_transaction_remaining.py \
  tests/test_noncli_final_branches.py -q
  14 passed
git diff --check e224579c^ e224579c
  passed; no output
python -m compileall -q src/cmru
  passed; no output
```

Independent live probes used temporary bare remotes/clones and removed them
with `TemporaryDirectory`:

```text
PYTHONPATH=src python - <<'PY' ... real post-rewrite interruption,
real abort, and real index-lock failure ... PY
  git version 2.55.0
  REAL_INTERRUPTED_REBASE_PROBE: PASS

PYTHONPATH=src python - <<'PY' ... public sync_local_main probe with a
temporary git argv wrapper ... PY
  PUBLIC_SYNC_LOCAL_MAIN_PROBE: PASS
  public return type: bool; clean current-main sync and dirty current-main
  refusal verified
```

The configuration-isolation reproductions were also run serially:

```text
python - <<'PY' ... set temporary GIT_CONFIG_GLOBAL with
[rebase] backend = apply; run the focused test ... PY
  exit 1: 1 passed, 1 failed; failure at line 1999 because only
  rebase-merge was asserted

python - <<'PY' ... set temporary GIT_CONFIG_GLOBAL with
[core] hooksPath = /tmp/cmru-no-such-hooks; run the focused test ... PY
  exit 1: both parameters failed because the hook was not discovered and
  the child returned ok=True
```

The review environment reported Git 2.55.0, no global `core.hooksPath` value,
and `full avg10=0.00` in the observed CPU-pressure read. The real-Git fixture
uses `start_new_session=True`, a 30-second child timeout, process-group kill
on timeout, and per-case temporary repositories; no active `git-rebase` child
remained after the passing runs. Those timeout/order safeguards passed review,
but they do not compensate for the unpinned Git configuration above.

No Docker-backed `./run-gate.py gate` run, mutation campaign, merge, or release
was performed. The focused no-Docker evidence is sufficient to identify this
remaining test blocker, but not to certify a clean gate.
