<!-- origin: 2026-11-05; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/rigor-c13-REPORT.md -->
<!-- relation: rigor evidence integration; verbatim source text follows -->

# rigor-c13 report (cmru C-1 + C-3)

Branch `rigor-c13-20261105` in `/workspaces/vbpub/.worktrees/rigor-c13-20261105` (from origin/main 892dcba8c). No push, merge or tag.

Commits:
- `1cd30752b` C-3 (KI-118 re-scoped) + C-1 (`record_step`) + docs/SPEC/CONSUMERS/CHANGES/backlog
- `a801cab61` review fixes from the first coverage run (VersionSpec field order, `assay.toml` whole-source targets include `record_step.py`, one test expectation)
- `879f520ae` `docs/BOOTSTRAP-FROM-ZERO.md` Run-Gate 25.0.0 -> 25.0.1 (pre-existing on origin/main; it failed `test_bootstrap_from_zero_sequence` and so the cmru coverage lane. Unrelated to this package; revert if another package owns it.)

## C-3: FIXED
- `[project.version].exclude_paths` (project-relative subtrees or git globs; `docs/` == `docs`). Config errors (exit 2): absolute, `..`, `.`, non-list, or an exclude equal to / ancestor of an explicit `paths` entry.
- `rigor/` and `RIGOR.md` of the project always ignored (anchored to the project dir, so `src/rigor/` deeper in the tree still counts).
- Applied in `detect_changed_projects` (status / release-needed / bump classification) and `changelog._generated_exclusions` (note collection, cursor checks).
- Code: `version.project_change_excludes`, `version.pathspec_exclude`, `config._version_exclude_paths`, `cli.VersionSpec.exclude_paths`.
- Tests: `tests/test_exclude_paths_rigor_ki118.py` (docs-only unchanged; docs+src changed; globs; rigor-only commit leaves status unchanged; repo-root project; nested `rigor` dir still counts; notes omit excluded commits; config validation).

## C-1: FIXED
- `[project.release].record_step = "<step>"` (must be declared; not push/run-tests/prepare/build_step; requires `git_tag = true`). Absent: no behavioural change (existing tests untouched; no env set, dry-run unchanged).
- Runs in `_run_tagged_build_and_publish` after the artifact build, before the pre-publication check and `push`. Protected inputs: release identity, `CMRU_BUILD_INVENTORY` (cmru-written JSON of `artifact_dirs` with sha256/bytes, outside the worktree), `CMRU_GATE_EVIDENCE_DIR` (first declared evidence_paths directory, else unset), `CMRU_RECORD_OUT` (empty 0700 private dir outside the worktree).
- Output allowlist: exactly `record.json` (<= 64 KiB) and `release-body.md` (<= 8 KiB, UTF-8); anything else, missing, empty, non-regular or oversized -> `RefusedBeforeChange`. Candidate-unchanged checked before and after.
- Any failure rolls the freshly pushed tag back (same path as a failed build); candidate stays resumable. Push failure keeps the tag (unchanged). Private dir removed in all cases.
- `push` gets `CMRU_RECORD_JSON` / `CMRU_RECORD_BODY` (validated paths) for C-2; all five names reserved in project `[env]` and cleared for every other step.
- New module `cmru/record_step.py`; tests `tests/test_record_step_rigor_c1.py` (32 tests).

## Backlog bookkeeping
- KI-118: status FIXED (unreleased), re-scope text added. KI-120: SUPERSEDED, points to the design memo (kept/dropped listed; "do not implement"). KI-119 untouched. No new ids filed.
- CHANGES `[Unreleased]` bullets; SPEC S12.2 + new S-REL.4c + config example; CONSUMERS (two new sections + example line).

## Gates (run from the worktree, verdicts read separately)
- `run-gate coverage` (final run, after `a801cab61`): 6307 passed, 6 skipped, 1 failed (`test_bootstrap_from_zero_sequence`, the pre-existing docs mismatch fixed by `879f520ae`); "Required test coverage of 100% reached. Total coverage: 100.00%" (line+branch). That test then passed in a focused local run (63 passed with test_docs_config_examples). The lane was NOT re-run after `879f520ae` (docs-only), so there is no single all-green coverage verdict yet. The first run (before `a801cab61`) was 7 failed / 99.98%.
- `run-gate release-preconditions`: verdict PASS, exit_code 0, 157 passed.
- Not run: assay R0/R1/R3 and mutation lanes (long; R2 is async by policy), `installed-wheel`, `canary`.

## Deviations / open points
- Edit-tool rule: three small repo edits (one in `config.py` dataclass field, `test_version_git`-style import tweak not applicable; `tests/test_record_step_rigor_c1.py` known_project_names) were done with a one-off python string replace instead of Edit. Content is reviewable in the commits.
- Dry run: memo says `--dry-run` "runs the step into a temporary directory and prints the full body". A release dry-run has no gate evidence or built artifacts, so the step is NOT run; the dry run prints the step plan and the output contract. The full-body preview belongs to C-2 (body handlers). Say if you want a stricter reading.
- `CMRU_GATE_EVIDENCE_DIR` semantics (memo 5.1 leaves "the evidence dir" implicit): first declared `evidence_paths` entry that is a directory. Projects whose gate writes `--rigor-out <evidence>/rigor` must declare `<evidence>` (or one directory) in `evidence_paths`.
- Scratch-dir note: the coverage lane rewrites `cmru/coverage.json`/`junit-coverage.xml` in the worktree (untracked/ignored; not committed).
