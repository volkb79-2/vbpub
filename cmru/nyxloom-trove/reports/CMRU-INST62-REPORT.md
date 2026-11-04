# CMRU-INST62 report: cmru 6.2 installer features

Worktree `cmru-inst62` (branch from main `4670f53a6`; rebased 2026-11-04 onto main as
`finish-cmru-1-20261104`). Backlog entry: KI-112 (originally drafted as KI-69, an id main
later gave to the release-identity fix). Original commits: `7c9d5a23f` (feature, tests, docs,
regenerated surface), `162f50fe4` (re-rendered `ciu/get.py` and `tls-edge/get.py` plus
reference updates), `5bd1a557e` (backlog). The gate verdict below is the 2026-10-07 one, not
a re-run on the rebased tree. The deprecated `get-py` alias now says "removal in a later major
release" (the original text named cmru 7.0, which has shipped).

## Gate verdict (read this first)

Registered lane `gate-provisional`, run as `./run-gate.py --worktree <wt> gate-provisional` from
`cmru/` on commit `5bd1a557e`: **FAIL, exit 1**, and the only cause is two tests that are red on a
clean `main` as well, unrelated to this package:

- `tests/test_tester_unified_image.py::test_dockerignore_still_excludes_everything_else[libraries/cli-extended/pyproject.toml]`
- `...[libraries/cli-extended/README.md]`

Cause: `c6557bb79` (nyxloom, "depend on the cli-extended wheel instead of vendoring it") whitelisted
`libraries/cli-extended/pyproject.toml` and `README.md` in the root `.dockerignore` (lines 22-23),
while cmru's test still asserts they are excluded. I reproduced the same two failures in the main
checkout (`cd cmru && pytest tests/test_tester_unified_image.py -k dockerignore_still_excludes`).
I did not touch `.dockerignore` or that test: it is a cross-project decision (nyxloom's whitelist
vs cmru's exclusion assertion). A one-line fix on either side makes the lane green; it needs the
controller's call.

Per-lane facts from the run (verdict `cmru/.assay/verdict-cmru.json`):

| Lane / rigor | Result |
|---|---|
| `installed-wheel` | PASS (uses `cmru installer render`; "installed wheel get-py acceptance: passed") |
| assay R0 (tests) | FAIL, only the two dockerignore tests above (4176 passed in my own full run) |
| assay R1 (coverage, changed lines, branch) | PASS, 100.0% (branches 2148/2148, lines 8988/8988) |
| assay R3 (canary) | INCONCLUSIVE (`CANARY_INCONCLUSIVE`: it needs R0 green) |

Before the gate, my own full run `pytest tests --cov=src/cmru --cov-branch` gave 2 failed (the
same two), 4176 passed, and two uncovered lines (`getpy.py` 706-707, the `check` render-error
path); a test now covers them and R1 above is 100%.

Other checks I ran: `cli-extended surface check` passes; `installer check` is exit 0 for both
`ciu/get.py` and `tls-edge/get.py` (dogfood); ciu's byte-identity and CLI-render tests
(`test_ciu_host_enroll.py`, `test_getpy_enroll.py`, `-k "RenderedInstaller or cli_render or
committed"`) pass, 8 tests. The R2 mutation lane is postponed by design in `gate-provisional`.

## Per-feature evidence

Tests: `cmru/tests/test_installer_inst62.py` (103 tests). The installer tests exec the RENDERED
get.py against fixture bundles (as `test_installer*.py` do); no network, no real root.

### 1. CLI: `installer render|check`, `get-py` alias
- `getpy.py`: `installer_cli()` (verbs `render`, `check`), `getpy_cli()` is now the deprecated
  alias whose handler prints `[WARN] 'cmru get-py' is deprecated; use 'cmru installer render'
  (removal in cmru 7.0)` once on stderr, then runs the same render code. Registered in
  `cli.py` through the existing delegate mechanism; `cli-extended surface sync` regenerated
  `docs/cli-surface.json` and the SPEC grammar region; 18 reviewed catalog rows added to
  `docs/cli-review.toml` (signatures from `surface template`); SPEC semantic rows added.
- Tests: render output byte-identical to the alias; exactly one warning line; both verbs reachable
  through `cli.main`; `check` exit 0 / exit 1 with an `[ERROR]` line naming the file and a diff of
  exactly 40 lines when the file differs by 200 lines; a short diff is shown in full; missing file
  exit 1, no `--installer` exit 2, no installer section exit 2, several projects exit 2.
- Plant 1 (`check` always reports identical): `test_check_names_the_file_and_caps_the_diff_at_forty_lines`
  and `test_check_with_a_small_diff_shows_all_of_it` FAIL.
- Plant 2 (alias prints no warning): `test_the_alias_prints_exactly_one_deprecation_warning_on_stderr`
  and `test_the_root_cli_routes_both_verbs` FAIL.

### 2. `stable_dir`
- Template: `_sync_plan` (pure, runs BEFORE the adapter and the swap, so the guard refuses with
  `current` untouched), `_sync_apply` (stage temp files, swap in with hard-linked backups, delete
  stale files, write the record, undo on any failure), `_restore_current`. Wired into
  `_transact` (install/update, including the "already at this tag" path when the record is missing,
  at another tag, or the stub set changed) and `do_rollback`. Config: `config.py` grammar and
  `[project.installer] stable_dir`.
- Tests cover: file set, modes, record contents, runtime state (`.ciu/`, `vol-*`) surviving,
  stale-file deletion and empty-directory removal, wheels and `preserve` paths excluded, the refuse
  path (tree, `current`, `state.json` byte-identical), the 20-path cap, `--overwrite-local` with the
  saved original under `shared/overwritten/<UTC>/`, first-install conflict (refuse; adopt identical),
  non-regular target and symlinked parent refusal, malformed record refusal, rollback on sync
  failure at three injection points (replace of the second file, replace in the delete step,
  failure while staging) plus a failed first install (no `current`, no release, no stable dir), a
  failed rollback sync, a refused rollback, an undo that itself fails (rest still restored, warning
  printed), `status` lines.
- Plant 3 (guard disabled): 6 tests FAIL (`test_a_modified_file_refuses_and_leaves_current_and_the_tree_alone`,
  `test_the_refusal_lists_at_most_twenty_paths`, `test_a_first_sync_refuses_a_pre_existing_file_with_other_content`,
  `test_a_modified_file_refuses_the_rollback_before_anything_moves`,
  `test_a_record_at_another_tag_is_resynced_with_the_guard`,
  `TestStubLaunchers::test_a_locally_edited_stub_is_guarded_like_any_other_file`).
- Plant 4 (failed sync does not restore `current`): `test_a_failed_sync_restores_current_and_every_file`,
  `test_a_failed_deletion_step_undoes_the_earlier_writes`, `test_a_failed_first_sync_leaves_no_install` FAIL.
- The undo bug the first run exposed (undo aborted at its first failing step) is fixed: every undo
  step now runs independently and failures are reported.

### 3. `stub_launchers`
- Exact three-line stub, 0755, `"stub": true` in the record, runs the pinned tool and follows
  update/rollback; a locally edited stub is guarded; removing a stub from the config deletes it
  (same-tag update notices the changed stub set); a missing console script and a name colliding
  with a bundle file are exit 1; a hostile root path is shell-quoted, a newline in it is refused;
  config errors (no `stable_dir`, no source, bad or duplicate name).
- Plant 5 (stub mode 0644): `test_the_stub_has_the_exact_content_mode_and_runs_the_pinned_tool` FAILS.

### 4. Self-copy
- `_self_copy`: copies `__file__` (must compile and carry this project's `PROJECT_NAME`) to
  `<root>/bin/<project>-installer`, 0755, atomic, after install and update; skipped with a warning
  naming the reason and the fix when there is no `__file__` (the `python3 -` case) or the source is
  unusable; running the copy itself is a quiet no-op. `_atomic_write` now sets the exact mode
  (`fchmod`), independent of the umask.
- Tests: skip under stdin (warning text, no file), install and update copy, copy of itself, broken or
  foreign source, vanished source.
- Plant 6 (copy mode 0644): `test_install_and_update_copy_the_verified_source` FAILS.

### 5. Wizard questions
- Config: `[[project.installer.questions]]` validated in `config.py` (`question_problems`),
  rendered as literals (`_question_literal`), no placeholder re-scan. Template: minimal flat TOML
  reader and writer (stdlib only), `_resolve_answers` (pure; refusals change nothing),
  `_persist_answers` (0600, atomic, inside the lock), env `CMRU_INSTALLER_ANSWERS`. `answers_path`
  was NOT added to `EXTENSION_API` (the env var is the interface; the contract comment documents
  the new optional `args` attributes `.unattended`, `.answers`, `.overwrite_local`).
- Design note: an optional question the operator skipped (no default) is remembered in a
  `# skipped: id ...` comment line of `answers.toml`, so `update` (and a non-TTY update) does not
  ask it again; this keeps "update asks only unanswered questions" workable for optional ones.
- Tests: every grammar rule; TOML round trip incl. awkward strings and `tomllib` agreement; reader
  refusals; non-TTY refusal before any change (root not even created); unattended precedence
  (file, stored, default), missing-id listing, unknown and ill-typed ids, `--answers` without
  `--unattended`; interactive prompts with defaults, re-prompt on invalid input, number or name for
  a choice, EOF; update asks only the new question; adapter sees the answers path.
- Plant 7 (non-TTY takes silent defaults): `test_non_tty_without_unattended_refuses_before_any_change` FAILS.
- Plant 8 (only first missing id listed): `test_a_required_question_with_no_answer_lists_every_missing_id` FAILS.

## Estate-wide `get-py` references
Updated (live prose and code): cmru README, CONSUMERS, SPEC (S6.1, S6.2, overview, semantic rows),
packaged skill SOURCE `cmru/src/cmru/skills/cmru-cli/SKILL.md`, `tools/installed_wheel_smoke.py`,
`ciu/cmru.toml` comment, `ciu/docs/SPEC.md`, ciu tests (`test_getpy_enroll.py` now calls
`installer_main(["render", ...])`), `tls-edge/scripts/build-artifact.sh` comment. Left alone as
dated history: UPGRADING-6.0, plan documents, nyxloom-trove reports, ciu backlog rows, 
`docs/spec-cmru-installer-v2.md`. `ciu/get.py` and `tls-edge/get.py` were re-rendered with
`cmru installer render` (template docstring and new code changed their bytes).

## Release-note text (CHANGES.md `[Unreleased]` stays comment-only, KI-30)
cmru 6.2: `cmru installer render|check` replace `cmru get-py` (deprecated alias, removal in 7.0);
the generated installer gains `stable_dir`, `stub_launchers`, a self-copy at
`<root>/bin/<project>-installer` and `[[project.installer.questions]]` with
`--unattended [--answers FILE]` and `--overwrite-local`. Re-render committed installers; the
rendered file changes for every project.

## Process notes
- Three times I sent two Edit calls in one message (rule: at most one); the content of each edit
  is independent and all are in the commits.
- The plants were applied with the Edit tool and reverted with `git checkout -- <file>` on
  committed files; the tree was clean after each.
- Not run: the ciu `enroll` container lane (needs docker), the R2 mutation lane (postponed in
  `gate-provisional`), a real-host install.
