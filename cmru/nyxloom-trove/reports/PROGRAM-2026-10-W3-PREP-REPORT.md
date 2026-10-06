# W3-PREP REPORT: release preparation for cmru 6.0.0

Branch `cmru-w3-prep`, base `90193c4d3`. All repository edits by Edit/Write. Nothing pushed, no image built, nothing released,
no remote ref touched. The only mutation outside the worktree was the tool's own lock file during `cmru abandon --dry-run`.
(Temporary edits to `cmru.orchestration.toml` and `ciu/cmru.toml` for the compat audit were reverted with `git checkout --`; the
tree shows neither file modified.)

## Evidence summary (what I ran)
- Full cmru suite, forward, via pt.py (serial, `flock test.lock`, nice/ionice, PSI `full avg60` 0.00 before): **3993 passed, 6 skipped**
  (log `scratchpad/w3prep-full2.log`). A first full run had 1 failure (item 2 below), fixed.
- Full suite in REVERSE file order (`-p no:randomly` + the 180 `tests/test_*.py` files sorted descending): **3993 passed, 6 skipped**
  (`scratchpad/w3prep-rev.log`). `pytest-reverse` is not installed.
- `cli-extended surface check` on this tree's cmru: the only finding is exactly
  `[REVIEW] incomplete parser syntax: cmru skills: delegated parser does not register inherited global option(s): --log-prefix-time-short`
  (CLI-EXT-26 / KI-61), then "CLI surface check failed." (exit 1, as KI-61 documents; every other line is a `[NOTE]` open finding).
- Gate lanes: see "Gate" at the end (filled after the run).

## 1. KI-35 / REL-09 sweep (PREPARED, not executed)
Plan: `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W3-SWEEP-PLAN.md` (16 refs, class, evidence, exact command, safe-to-delete).
Headlines:
- Only **16** origin `cmru-release-*` branches exist (not ~25); **all are origin-only** (no worktree, no local branch; `cmru worktrees --json` = `[]`).
- **12 are safe** (published or abandoned pre-tag, every commit already on `origin/main`); this tree's `cmru abandon <b> --dry-run` exits 0 for all 12.
- **4 are withheld by the tool** (exit 4): three genuinely ambiguous MDT release-inputs candidates (#11, #13, #16: hold, check GHCR first)
  and one (#15, `...9rjl8c`) whose commits are patch-equivalent to commits on main (`git cherry` `-`), deletable only by the raw lease command.
- `xm4okg` no longer exists anywhere (no ref, branch, worktree or sidecar): nothing to abandon.
- 17 orphan sidecar tokens in `.git/cmru-release-scopes/` have no cmru removal command (listed in the plan; manual `rm` or a follow-up).

**Code needed and added.** `cmru abandon BRANCH` could not act on an origin-only branch. Added `transaction.inspect_remote_candidate`,
`transaction.retire_remote_candidate`, `transaction.RemoteCandidate` and a fall-through in `cli._abandon_locked`
(`_abandon_remote_candidate`): when no local worktree matches a `cmru-release-*` name and the branch exists on origin it is retired only if
every commit is on `origin/main` (`rev-list --count <tip> ^<main>` is 0), with `--force-with-lease=<ref>:<oid>`, read-back, sidecar removal.
Otherwise exit 4 with the `git log` and manual delete command; unreachable origin/unavailable objects exit 2 (naming the `git fetch` to run);
an absent branch keeps the original "no exact managed ..." refusal (exit 2). No grammar change (the `abandon` description is unchanged, so
`docs/cli-surface.json`, SPEC S-CLI.9 and the 346-case catalog needed no regeneration). Tests: `tests/test_w3_prep_remote_retire.py`
(10 tests on a real bare origin: dry-run read-only, `--yes` retires and removes only that token's sidecars while main and tags stay,
declined confirmation, unique commits withheld with the manual command, absent branch refusal, unreachable origin, missing local objects,
lease blocks a moved branch, unique-commit and local-branch refusals in the transaction function, non-release name). `tests/test_cli_abandon.py`
had one parametrised test adapted (the unmatched release name now also looks on origin; it stubs that lookup to "absent"). REL-08 docs:
`docs/RELEASE-TRANSACTIONS.md` (the existing "never merge a candidate by hand" note stays; new section "Retiring an origin-only candidate (KI-35)").
KI-35 in the backlog is annotated (partly fixed; items 2 and 3 and orphan sidecars remain open).

**Not done, KI-35 items 2 and 3** (a per-fact "what is missing" report for ambiguous LOCAL worktrees; deciding whether a successful release
removes its own worktree). They concern retained local worktrees, of which there are none to act on now.

## 2. Test environment leak
- `tests/conftest.py`: `preserved_environ()` context manager, session-start baseline `SESSION_START_ENVIRON`, and an autouse function-scoped
  fixture `_restore_process_environment` that snapshots and restores `os.environ` around every test.
- Removed the workarounds that existed only because of the leak: in `tests/test_w2_integ.py` the module-scoped `_env_before_module` baseline
  fixture, the last-in-file `test_zz_...leaks_out_of_this_file` probe, and the set-then-delete monkeypatch trick (`_clean_env` is now a plain
  `delenv(raising=False)` loop). The per-test `delenv(raising=False)` calls in other files were left (they ensure a clean START state and are harmless).
- Proof: `tests/test_zz_environment_probe.py` (4 tests). The end-of-run probe asserts no `CMRU_INTERNAL_*`/`PYTHONUNBUFFERED` differs from the
  session-start environment; a paired pair of tests writes the variables directly in one test and checks the next; two unit tests pin the
  restore of additions, changes, removals and the restore on an exception. **Plant:** with the autouse fixture's body replaced by a bare `yield`
  the probe FAILED (`{'CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT': '1', 'PYTHONUNBUFFERED': '1'}`); reverted.
- Latent order dependency exposed and fixed: `tests/test_cli_dispatch.py::test_cleanup_delete_unmanaged_release_previews_then_refuses_without_confirmation`
  passed only because an earlier test leaked a GitHub credential into `os.environ` (it also fails alone at the base commit). It now writes its own
  `cmru.secret.toml` and clears `GITHUB_PUSH_PAT`/`GITHUB_TOKEN`, like its sibling test.
- Reverse order: green (see Evidence). In reverse the probe runs first, so it is baseline-based, not position-based.

## 3. `cmru standards` template revision 5
- What revision 5 requires (from `standards.py`, CHANGES): the `template_revision = 5` marker; `CMRU_TESTER_PIDS_LIMIT` (and the DinD limits with
  `--enable-docker`) in `[env]` for projects that call `tester-gate`; the `[runtime]` table; `cmru handler` (not `python -m cmru.handlers`) steps.
  cmru's own `cmru.toml` already met everything except the marker.
- Applied with this tree's CLI: `standards cmru --update` changed only `template_revision = 4` -> `5` in `cmru/cmru.toml`;
  `cmru standards cmru` then reports "1 project(s) conform".
- Estate (`cmru standards all --update --dry-run` on this tree, nothing written; `git status` clean for other projects). All ten projects below are
  marker-only changes, and no project reports any other standards problem:

| project | today | `--update` would change |
|---|---|---|
| cli-extended | r4 | `template_revision = 5` (marker only) |
| ciu | r4 | marker only |
| cmru | r5 (done here) | none |
| assay | r4 | marker only |
| topos | r4 | marker only |
| nyxloom | r4 | marker only |
| modern-debian-tools-python-debug | r4 | marker only |
| pwmcp | r4 | marker only |
| tls-edge | r4 | marker only |
| run-gate-project | r4 | marker only |
| cgroup-profiler | r4 | marker only |

  Caution: once other projects are at r5, an installed cmru 5.5 `standards` reports them as "expected 4" (item 5).

## 4. CHANGES for 6.0.0
`cmru/CHANGES.md`: `## [Unreleased]` is now an empty comment-only section (KI-30 accepts that) and the whole body moved under
`## [6.0.0] - UNRELEASED`, headed by operator-facing sections: BREAKING grammar table (old to new), exit codes and `CmruError`, dependencies/packaging/new
verbs (`cli-extended` dependency, `cmru[interactive]`, `doctor`, `skills`, retired agent/controller, `get.py` split with `[project.installer] extensions`,
`CMRU_INTERNAL_*`, `run_step` kept, KI-35 abandon), and "How to upgrade from 5.5" (8 steps naming every command an operator script must change). The pre-wave
`[Unreleased]` text at `68a03b4fe^` was already fully present (a line-set comparison found 0 missing lines), so it is kept as the Added/Changed/Fixed/Testing body.
**Heading deviation from the brief:** I used `## [6.0.0] - UNRELEASED` (bracketed) because that is the form `changelog._UNRELEASED_HEADING_RE` (KI-23) recognises.
**Release-time note:** KI-23 makes `cmru release cmru --set-version 6.0.0` REFUSE while that hand-authored heading exists ("fold it into the generated section by hand
first, then rename its heading to match (or remove it)"). The controller must fold or remove the draft heading at release time (this is the existing documented
behaviour, not a regression). Table entries were checked against `--help` of this tree for `release`, `publish`, `cleanup`, `run`, `status`.

## 5. Landing compatibility audit (read-only; NO shim added)
Method: the installed `cmru` (`5.5.1.dev1197+gfbe05280d`, the pre-wave main source) run read-only with `--config` pointing at this worktree's root
and project contracts, adding one candidate shim at a time until `standards all`, `status`, `dependencies`, `build --dry-run` ran, then reverting.
Failures, in the order met:
1. `[ERROR] orchestration.default_projects is required` (root `cmru.orchestration.toml`). **Shim A:** re-add `default_projects = [...]` under
   `[orchestration]` of the root config (5.5 accepts any list; 6.0 accepts it with a one-line "ignored, will be removed" warning). Tried `["cmru"]`.
2. `[ERROR] project.ciu.installer: unknown keys ['extensions'] (V09)` (`ciu/cmru.toml` line 52). **Shim B:** remove (or comment) the
   `extensions = ["installer/enroll.py"]` line of `ciu/cmru.toml` while main must be readable by 5.5. Consequence: a 5.5 `get-py` render of ciu would
   omit `enroll`; 5.5 never rendered it from an extension, so only re-rendering with 5.5 loses it; restore the line when 6.0 is installed.
With Shims A and B, 5.5 ran `cmru standards all`, `cmru status`, `cmru dependencies` (PREFLIGHT PASS) and `cmru build --dry-run` (all `cmru handler ...` argv
resolve; 5.5 has `cmru handler`). `cmru resolve all` failed only on "No releases found for project 'modern-debian-tools-python-debug'" (unrelated: that project
publishes no git tag). `cmru release --dry-run` stopped on "Local main is 10 commit(s) ahead of origin/main" (this worktree, unrelated).
Other observations (not parse failures, no shim required):
- 5.5 `cmru standards` reports **cmru's own `template_revision = 5`** as "expected 4" (and would do so for every project after `standards --update`). Not
  release-blocking; stop using 5.5 `standards` after the marker bump or accept the warning.
- The root `cmru.toml`-level changes in the 9 project contracts since `68a03b4fe^` (handler argv via `cmru handler`, DinD digest/limits env) parse under 5.5.
  `cmru/cmru.toml` `[env] PYTHONPATH` no longer names `libraries/cli-extended/src`; a release child therefore needs `cli_extended` INSTALLED in the interpreter
  that runs the candidate's cmru source (the venv has cli-extended 0.2.0).
- `cmru/run-gate.toml` still carries the Part C `tester-unified:cmru6-integ` environment (`cmru-tester`) and the `TODO(cmru-6.0 landing)` markers; the landing
  revert list is in `W2-INTEG-REPORT.md` (review fix round 1, C6). Not touched here.
- Not exercised: any 5.5 write path (no release/publish run), because the audit is read-only.

## Plants (mutation-style checks I ran)
| Plant | Result |
|---|---|
| autouse environment restore replaced by a bare `yield` | KILLED by `test_zz_no_internal_name_or_pythonunbuffered_leaked_from_any_earlier_test`; reverted |
No other plants were run for the abandon path; the 10 real-git tests are the oracle (not mutation-checked). A fresh reviewer should plant at least:
`unique_commits` check removed in `retire_remote_candidate`, the `--force-with-lease` oid replaced by a plain `--delete`, sidecars not removed, and the
`_is_release_branch` guard dropped in `inspect_remote_candidate`.

## Files changed
`cmru/src/cmru/cli.py`, `cmru/src/cmru/transaction.py`, `cmru/tests/conftest.py`, `cmru/tests/test_w2_integ.py`, `cmru/tests/test_cli_abandon.py`,
`cmru/tests/test_cli_dispatch.py`, new `cmru/tests/test_w3_prep_remote_retire.py`, `cmru/tests/test_zz_environment_probe.py`, `cmru/cmru.toml` (marker),
`cmru/CHANGES.md`, `cmru/docs/RELEASE-TRANSACTIONS.md`, `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`, the two reports.

## Deviations
- 16 branches, not ~25; all origin-only; `xm4okg` already gone (see item 1).
- CHANGES heading bracketed (item 4).
- KI-35 wanted items 2/3 and orphan-sidecar removal not implemented (item 1).
- No coverage of the new abandon code by the `coverage` lane was assumed; see Gate.
