# W2-PKG2 continuation (checkpoint cut at a green boundary)

Worktree `.worktrees/cmru-w2-pkg2`, branch `cmru-w2-pkg2`, base 287813c9d. The cut was taken because
the ~60-tool-call checkpoint was passed. State at the cut: full cmru suite via pt.py = 3322 passed,
2 skipped (read from `scratchpad/pkg2-full2.log`). NOT yet done: gates, plant mutations, the REPORT.

## Done (code)
- All nine delegate builders use `cmru_registry()` (scaffold, versions, runner, handlers, tester_gate,
  resolve, getpy, standards, tool_deps). New `cmru/src/cmru/delegate_targets.py`
  (`resolve_target`, `current_project`) replaces the five copied context blocks (B13).
- Library controls: `dry_run=True` on init, versions init/resolve, run-step, handler mutating verbs,
  tester-gate, get-py, standards, tool-deps; D4 `Requires`/`Conflicts` on get-py, standards, tool-deps;
  `standards --json`; tool-deps `--json` through `runtime.output.primary`; init uses library `--yes`.
- versions ladder: exit 3 (`VersionsPrerequisiteError`, `RegistryError`) and 2 (`VersionsError`,
  `ValueError`) are `CliFailure`; exit 1 is `expected_exceptions=(VersionsOperationError, OSError)`.
- B8-B12: get-py multi-project stdout refused (CLI-12); resolve shape rule (CLI-13) and config-free
  `--repo/--prefix` (CLI-D2); handler `--bake-target`; tester-gate `--forward-background-slice` /
  `--forward-gates-slice`, every env fallback at run time + "(default: $VAR)" (CLI-17); init
  non-interactive when facts complete, prompt driver, `--yes`, decline exits 0 (CLI-10).
- Exit codes: `exit_codes.POLICY_REFUSED = 4` (new constant in `exit_codes.py`); standards issues and
  tool-deps stale -> 4; tester-gate missing config -> 3.
- C: narrowed `versions.py` jinja render catch and `resolve.resolve_via_latest_json`.
- `python -m cmru.handlers` -> `handlers.main` catches `VersionLookupError` -> exit 3, one line.
- Docs: SPEC S-CLI.9 rows for my verbs, CHANGES.md `[Unreleased]` lines.
- Tests: `tests/test_w2_pkg2_delegates.py` (new), `tests/prompt_fakes.py` (new), rewritten
  `tests/test_init_scaffolding.py`, and edits in 14 existing test files.

## Next steps
1. Plant one mutation per scope item, show each test fails, restore (table in the REPORT). Candidates:
   factory (revert one builder to `CliRegistry`), `--bake-target` rename, tester-gate build-time env read,
   get-py multi-stdout check, resolve `single` rule, resolve narrowing, standards exit 4, tool-deps exit 4,
   versions exit-3 mapping, init `_missing_facts`, decline exit 0, handler VersionLookupError.
2. Gates from `cmru/`: commit first, then
   `flock scratchpad/gate.lock ./run-gate.py --worktree <wt> coverage` and `... canary`
   (one at a time, verdict read in a SEPARATE step). Expect new coverage gaps in `scaffold.py`
   (`_NoPrompts` branches, `_missing_facts`), `resolve.py`, `standards.py`; add tests until 100%.
3. Write `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W2-PKG2-REPORT.md`: findings closed with test names
   (CLI-10, 12, 13, 14, 17; CLI-20 scaffold side), decisions D1/D4/D5/D6, the plant table, deviations below.

## Deviations to record in the REPORT
- `runner.runner_cli` kept as a converted compatibility export (root `cli.py` still mounts `run-step`);
  controller deletes it and `_run_step_cli` after PKG-1 merges.
- `init`: project-level options are `--folder/--id/--kind/...` (NOT `--project-*`: the old test pins that
  `--project` is rejected and argparse prefix-matching would make it ambiguous); a monorepo always prompts.
- A non-integer `SystemExit` in `versions` is now rendered by the library as exit 1 (was 2); no code raises one.
- `versions check --json` keeps its sorted indented `print` (stable-order contract); other prints stay (D5).
- `--dry-run` help is the generic library sentence (accepted, D4/CLI-EXT-21).
- Edited root-side tests only where my delegates changed their observable output
  (`test_cli_delegated_dispatch_adversarial`, `test_cli_adversarial_contracts`, `test_cli_review_decisions`);
  `exit_codes.py` gained `POLICY_REFUSED` (PKG-1 may add the same line: trivial merge).
- Edit/Write tools only; two read-only `python3 -` heredocs (a no-op and a builder smoke import) ran,
  neither wrote a repository file.
