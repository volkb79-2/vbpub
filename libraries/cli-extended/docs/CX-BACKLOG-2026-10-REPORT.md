# CX-BACKLOG report, 2026-10-06

Branch `cx-backlog-2026-10` (from `75a638539`), library `libraries/cli-extended`. Nothing released or pushed.

Commits: `f6ad8ce0c` (23, 26, 27, 28, 29: code and tests), `7cfe227f3` (19, 21, 22, 24: code and tests),
`919bafcbe` (docs, backlog, CHANGES), `318d6735b` (one coverage test), plus the commit that adds this report.

## Triage (CLI-EXT-01..29)

| ID | Status | Size | Evidence |
|---|---|---|---|
| 01, 03, 04, 05 | done (closed / implemented earlier) | - | BACKLOG entries |
| 02 | done (W2) | - | BACKLOG entry |
| 06-16 | done (released in 0.2.0) | - | CHANGES.md `[0.2.0]` |
| 17 | open, decision ask | L | design note in BACKLOG (shared with 25) |
| 18 | open, decision ask | L | design note in BACKLOG |
| 19 | done `7cfe227f3` | S | `python_args`, `isolated` |
| 20 | open, decision ask | M (policy) | not implemented: it reverses a documented, tested fail-loud refusal |
| 21 | done `7cfe227f3` | S | `VerbSpec(dry_run_help=)` |
| 22 | done `7cfe227f3` | S | AC-25 ignores comments, AC-01 ignores test files |
| 23 | done `f6ad8ce0c` | M | `identity_banner` / `error_help` opt-ins, defaults unchanged |
| 24 | done `7cfe227f3` | S | `version_probe` |
| 25 | open, decision ask | L | needs positional-subject constraint kind, surface schema bump, review changes |
| 26-29 | done `f6ad8ce0c` | S | |

## The "8 failures" (TODO 1)

They do not reproduce. This worktree has no `failure_notify.py` or `test_failure_notify.py`
(`find` over the tree: none). A hermetic run from `libraries/cli-extended` with `PYTHONPATH=src` and
`-p no:cacheprovider` collects 1280 tests, all under `tests/`, and exits 0 (`rt.sh`, output
`scratchpad/cx-full-a.txt`; `-q` from `addopts` plus the helper's `-q` suppresses the summary line, so the pass
count is not printed, only the exit code and the collect-only count). The earlier run was therefore collected
from outside this tree (the stale 803-test count also differs from 1280); I did not identify which cwd or helper
caused it. No library test walks outside its own tree: the only repo-walking tests use `tmp_path` or the
package's own `src`.

## Verdicts

| Check | Result | How |
|---|---|---|
| cli-extended full suite, hermetic | exit 0, 1280 collected | `rt.sh tests` after all fixes except the last test-only commit |
| Gate `r0-r1` (tester-unified, `./run-gate.py --worktree ... r0-r1`) | first run FAIL: 99.95% coverage, `output.py` 181/186 (the `CliOutput` policy validation from fix 23). Test added in `318d6735b`; second run PASS, 100.00% (5341 statements, 2456 branches, 0 missing) | logs `scratchpad/cx-gate-r0r1.log`, `cx-gate-r0r1-b.log` |
| Gate `r3` canary | PASS ("canary rejected: JSON redaction test fails when its guard is disabled") | `cx-gate-r3.log` |
| Gate `r2` mutation (720m budget) | NOT RUN | a 12 h, 3-worker run on the host shared with the production game server; needs the controller's go. The new code is covered only by the plants below, not by an assay verdict |
| netcup `scripts/netcup/tests`, `PYTHONPATH` = this tree's `libraries/cli-extended/src` | 522 passed | `cx-netcup.txt` |
| cmru `cmru/tests`, `PYTHONPATH=src:<this src>:<worktree src>` | 2886 passed, 10 skipped, 2 failed: `test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run` and `::test_release_rejects_an_internal_snapshot_spanning_multiple_git_families` | `cx-cmru.txt` |
| same 2 cmru tests with the library from `75a638539` (`git archive` into scratch) | the same 2 fail, 6 pass | `cx-cmru-base.txt`; so not caused by this branch (a Git-family check in this worktree layout) |

Deviation: `docker update --cpus=3` was not applied to the r0-r1 and r3 gate containers. My poll ran in a
parallel call and finished before the container started (the runs take about a minute); the containers ran with
the lane's own `--memory 512m` and `dev-gates.slice` placement, serially under `gate.lock`, peak 245 MiB.

## Per-fix evidence and plants

Each plant: the source change was made, the named tests were run and failed, then the file was restored with
`git checkout`. Rows marked (carried) were done by the previous implementer and are not re-run by me.

| Fix | New tests | Plant | Result |
|---|---|---|---|
| 26 skills delegate global options | `test_skills.py` cx26 (5) | revert `skills.py` (carried) | 5 fail |
| 27 testing PYTHONPATH | `test_w6_testing.py` cx27 (2) | revert `testing.py` (carried) | 2 fail |
| 28 AC-25 marker | `test_audit.py` cx28 (2) | revert `audit.py` (carried) | 1 fails; the second guards "marker exempts the whole file" |
| 29 exit code / hint | `test_w1_runtime.py` cx29 (3) | revert `parser.py` (carried) | fails |
| 23 banner / usage help | `test_w1_runtime.py` cx23 (6) + `CliOutput` validation | ignore the policy, disable usage branch (carried) | 3 fail |
| 24 version probe | `test_w1_runtime.py` cx24 (7 cases) | probe excluded from the disagreement check | `probe_disagreeing_with_metadata`, `..._with_version_file` fail |
| 19 python_args / isolated | `test_w6_testing.py` cx19 (2, one a real subprocess) | `isolated` no longer drops `PYTHONPATH` | `isolated_drops_inherited_pythonpath_in_a_real_child` fails |
| 21 dry_run_help | `test_w1_runtime.py` cx21 (2) | argparse help ignores `dry_run_help` | both fail |
| 22 audit precision | `test_audit.py` cx22 (3) | no comment stripping; no test-file exclusion | `ac25_ignores_comments...` and `ac01_ignores_test_files...` fail |

Behaviour changes are listed in CHANGES.md "Unreleased". The one that can break a consumer: `invoke_*` no longer
prepends the library directory (27). netcup (522 passed with `PYTHONPATH` from its gate lane) and cmru were
unaffected in the runs above.

## Decision asks

1. **17 + 25 together (L).** One new constraint kind with a positional subject (`When(subject, equals=|count=, then=...)`),
   which needs a surface manifest schema bump (7 to 8), review-candidate and trigger-check changes, and a one-time
   `surface sync` for adopters using it. Approve the schema bump and the spelling; proposed timing: after cmru 6.0.
2. **18 (L).** Shared review decision across routes changes the review catalog format and signatures (a migration
   for every adopter's catalog). Do it now, or after the remaining CLIs adopt so the migration happens once?
3. **20 (policy).** Keep refusing defaulted options (the documented rationale), allow them with the blind spot that
   an explicitly typed default looks absent, or allow them only as an explicit opt-in on the constraint
   (proposed). Not implemented.
4. **r2 mutation lane:** authorize the 720m run, or accept the plants plus r0-r1 100% as the evidence for this release.
5. **Release note:** 27 is a behaviour change for the testing helpers; confirm it ships in the next 0.x without a
   contract-version bump (the library contract concerns the CLI controls, not the test helpers).

## Review round 1 fixes

- C3 (audit bypass): `audit.py` now splits on `"\n"` only (`_without_comments`, `_without_allowed_assertions`), like the
  tokenizer; `splitlines()` also breaks on `\x0c`, `\x1c`, `\x85`, ` `, which shifted token rows and blanked a real
  `sys.path.insert(...)`. Tests: `test_cx22_ac25_line_separators_do_not_misalign_comment_stripping` (4 separators, a
  separator line and one inside a string, each followed by a real hack with a trailing comment).
- C1: an expected exception's `exit_code` and `CliFailure.exit_code` outside 1..255 (0, negative, above 255, bool) become 1
  (`_valid_exit_code`); test `test_cx29_exit_codes_outside_1_to_255_fall_back_to_one` covers 0, -2, 300, True, False for both.
- Skills child forwarding of `identity_banner`/`error_help`: `test_skills_child_forwards_identity_banner_and_error_help`.
- Pytest strict-mode error now names `--cli-case-partial` (the opt-out already existed); BACKLOG CLI-EXT-31.
- Docs: CHANGES release-policy ruling, marker-in-non-test-files, `version_probe` has no timeout; BACKLOG CLI-EXT-30/31 filed,
  17/18/20/25 statuses updated with the 2026-10-06 rulings.
- Plants (each killed and reverted): splitlines restored in `_without_comments` (4 audit tests fail); `1 <= code <= 255`
  clamp removed (3 cx29 cases fail: 0, -2, 300); skills forwarding of `identity_banner`/`error_help` removed (the new
  forwarding test fails).

## Not done / open

- CLI-EXT-24's UNVERIFIED route-metadata question (delegate wrapper with `mutating=True` and no `dry_run`) was not
  investigated.
- Alternative forms in the entries (`assert_no_path_hack` helper for 28, `{type: exit_code}` mapping for 29,
  `on-usage-error` banner for 23) were deliberately not built.
