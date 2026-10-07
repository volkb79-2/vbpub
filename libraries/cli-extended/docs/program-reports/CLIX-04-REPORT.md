# CLIX-04 report: cli-extended 0.4.0 (LCR-1 fallback verb, LCR-2 doctor)

Worktree `/workspaces/vbpub/.worktrees/clix-04` (base `main` 4670f53a6). The library keeps its
reports in `docs/program-reports/`, so this file lives there, not in a new `docs/reports/`.
Normative source: `DESIGN-RG82-RUN-GATE-24.md` sections 2.1, 2.3, 2.4 and package A.
Commits (all paths under `libraries/cli-extended`): `0be95b3b6` LCR-1, `902fef22b` LCR-2,
`f3013ddb8` docs, `0b3d0c7c5` test-path fix (see "Pre-existing red gate"), then this report.

## 1. What changed (file:line at the commit tip)

LCR-1
- `src/cli_extended/parser.py:350` `VerbSpec.fallback: bool = False`; `:353` non-bool is `TypeError`.
- `:437-445` `VerbSpec.help_labels` (adds `default verb`, used by `summary` and `command_details`);
  `behavior_labels` is untouched so the manifest `behavior` list does not change.
- `:672-679` `HelpCatalog.fallback_usage`; `:722-724` text usage line, `:797-804` Markdown usage line;
  Markdown behavior lines use `help_labels`.
- `:1063-1112` `_leading_option_remainder(..., extra=)`: scan over root options UNION the fallback verb's.
- `:1116-1142` `_fallback_argv`: the ONE normalisation. `None`/empty argv, `--`, unknown option,
  leading `--help`/`--version`, a verb/delegate/`help`/`version` first token: unchanged. Otherwise
  `[fallback, *argv]` (verb FIRST).
- `:1470` `RegisteredCli.fallback_verb`; `:1478-1491` `RegisteredCli.parse_args` (same helper, no handler).
- `:1522` `run()` passes `fallback_verb`; `:2049` `run_cli(fallback_verb=...)`; `:2068` normalises `raw`
  before anything else reads it.
- `:1600-1620` `CliRegistry.register`: second fallback, fallback with `delegate=`, and fallback without
  an `ArgumentSpec` raise `ValueError`. `:1696-1702` `build()`: fallback in a `single_command` registry
  raises `ValueError` (not in the spec; added because the rewrite is meaningless there). `:1882` passes the name.
- `src/cli_extended/surface.py:900,1153-1156,1420` route record gets `"fallback": true` only for that verb;
  `:1749-1751` the key joins the route's signature context only when true.
- `src/cli_extended/review.py:641-644` generated spec text: "default verb: a first token that is not a
  verb selects this verb".
- `audit.py` needed no change (no check names verbs in a way the new field affects); tests cover it.

LCR-2
- `src/cli_extended/doctor.py:53,56-60` `CheckResult.lines: tuple[str, ...] = ()`, validated.
- `:160-171` `_check_record` (`"lines"` only when non-empty); `:212-213` human output prints each line
  indented 4 spaces before the remedy.
- `:231,241-248,285,220` `register_doctor(fail_exit_code=1)`: validated 1..255 (bool/non-int refused),
  threaded to the handler, which returns it when any check failed.

Docs: `CHANGES.md` (new "0.4.0" entry under "Contract and upgrade notes"; no `## [Unreleased]` exists
and none was added), `README.md`, `docs/CONSUMERS.md` (new default-verb section, doctor and surface
paragraphs), `docs/DESIGN-GUIDE.md` (new default-verb section, doctor paragraph), and `SPEC.md`
(section 2 fallback paragraph, section 13 route key, section 15 items 1/5/6/7; SPEC is the contract the
README links, so leaving it stale would contradict the code; not in the brief's list, easy to drop).

## 2. Version

Not bumped by hand. The version is `setuptools-scm` from the `cli-extended-v*` tag that
`cmru release` mints (`pyproject.toml` `dynamic = ["version"]`, `cmru.toml` `strategy = "scm"`); the
0.3.0 release commit only touched `CHANGES.md` ("prepare release inputs"). 0.4.0 will come from the
release tool. I did not tag.

## 3. Test-to-oracle mapping

Spec 2.3 rules (`tests/test_fallback_verb.py`, 46 collected tests; the doctor file adds 32, 78 new in total)

| Oracle | Test |
|---|---|
| fallback with leading options | `test_leading_options_before_the_lane_are_recognised`, `test_library_root_options_may_lead_the_lane` |
| `=` form | `test_equals_form_leading_option` |
| `--` after the lane | `test_double_dash_after_the_lane_reaches_lane_args`, `test_options_after_the_lane_still_parse` |
| unknown leading option: no rewrite, exit 2 | `test_unknown_leading_option_is_not_rewritten_and_exits_two`, `test_double_dash_first_is_not_rewritten`, `test_option_missing_its_value_is_not_rewritten` |
| only options (rule 2, `tokens == []`) | `test_only_leading_options_still_selects_the_fallback_which_refuses` |
| verb/delegate wins | `test_a_registered_verb_or_delegate_wins_over_the_fallback` (list, `--json list`, `admission show`, `run lane`), `test_a_verb_named_like_a_lane_needs_the_explicit_spelling` |
| `help`/`version` win | `test_help_and_version_win_over_the_fallback` |
| empty argv prints help, exit 0 | `test_empty_argv_prints_help_and_exits_zero` |
| `run LANE` explicit | `test_explicit_run_is_the_same_call_as_the_bare_form` |
| `help run`, help label, usage line | `test_catalog_marks_the_fallback_verb_and_help_run_works`, `test_markdown_help_with_a_configure_callback_lists_the_label`, `test_no_fallback_no_usage_line_and_no_label`, `test_a_lane_help_flag_shows_the_fallback_verbs_help` |
| `parse_args` equals what `run` executes | `test_parse_args_equals_what_run_executes` (6 argvs), `test_parse_args_runs_no_handler_and_reports_usage_errors`, `test_parse_args_defaults_to_sys_argv`, `test_parse_args_without_a_fallback_does_not_rewrite`, `test_registered_cli_exposes_the_fallback_name` |
| rule 5: errors not swallowed (`docter`) | `test_errors_inside_the_fallback_are_not_swallowed` |
| two fallbacks refused | `test_two_fallback_verbs_are_refused` |
| fallback + `delegate=` refused | `test_a_fallback_with_a_delegate_is_refused` |
| fallback without ArgumentSpec refused | `test_a_fallback_without_an_argument_spec_is_refused` |
| type / single_command | `test_fallback_must_be_a_bool`, `test_a_single_command_registry_refuses_a_fallback` |
| surface key only when true | `test_surface_key_is_present_only_for_the_fallback_route`, `test_surface_without_a_fallback_has_no_key_at_all`, `test_the_fallback_route_changes_the_review_signature` |
| byte-identical manifest vs main | `test_a_manifest_without_a_fallback_is_byte_identical_to_the_one_generated_at_main` |
| `surface check`/sync | `test_surface_sync_and_check_work_with_a_fallback_verb` (incl. stale-manifest detection), `test_the_cli_extended_audit_and_surface_commands_accept_a_fallback_project` |
| `audit` | `test_audit_runs_with_a_fallback_verb`, same CLI test (AC-16/17/18 rows) |
| `assert_cli_contract` | `test_assert_cli_contract_passes_for_a_fallback_cli` |

Spec 2.4 (`tests/test_doctor_lcr2.py`)

| Oracle | Test |
|---|---|
| fail exit code used (text, JSON, crash) | `test_fail_exit_code_is_used_when_a_check_fails`, `..._applies_to_json_mode_too`, `test_a_crashed_check_exits_with_fail_exit_code` |
| clean/warn still 0; default 1 | `test_clean_and_warn_only_runs_exit_zero_whatever_fail_exit_code_is`, `test_the_default_fail_exit_code_stays_one` (plus existing `test_doctor.py`) |
| 1..255 validation | `test_fail_exit_code_bounds_are_accepted` (1,2,128,255), `test_fail_exit_code_outside_one_to_255_is_refused` (0,-1,256,1000,True,False,2.0,"2",None) |
| lines human, indented, before remedy | `test_human_output_prints_lines_indented_before_the_remedy` |
| JSON `lines` only when non-empty; existing JSON unchanged | `test_json_has_a_lines_key_only_when_non_empty`, `test_doctor_json_of_a_tool_that_never_sets_lines_is_unchanged` (exact string), `test_a_crashed_check_carries_no_lines`, existing `test_doctor.py::test_json_shape_exact` |
| lines validation, survive remedy normalisation | `test_check_result_lines_must_be_a_tuple_of_single_line_strings`, `test_lines_survive_remedy_normalisation`, `test_check_result_lines_default_is_empty` |

## 4. Byte-identical manifest proof

`tests/baseline_app.py` builds a fallback-free registry (library `run` verb with constraint and dry-run,
a delegate group, `doctor`, a global option). `tests/data/surface-baseline-main.json` (36465 bytes,
sha256 `cfc66690d269d15fad6ff73116006bafba477bd1c4c1738151496251d636bc88`) was generated from that module
with the library source of the MAIN checkout (`PYTHONPATH=/workspaces/vbpub/libraries/cli-extended/src`,
`cli_extended.__file__` printed to confirm) BEFORE any change, then `cmp`-ed against the export of this
worktree's code: IDENTICAL. The committed test compares bytes of
`render_cli_surface_json(export_cli_surface(...))` against that file.
(The fixture was copied into `tests/data/` with `cp`, a shell write of generated data, not hand edits.)
Real-consumer evidence: `cli-extended surface check` and `audit` run from `scripts/debian-install-v2`
with `PYTHONPATH=<worktree>/libraries/cli-extended/src` (its committed `docs/cli-surface.json`, a tool
with no fallback verb): `CLI surface check passed.` exit 0; `audit: 12 pass, 0 warn, 0 fail, 3 manual` exit 0.

## 5. Plant transcripts (each planted via Edit, test run, reverted via Edit; `git diff` empty afterwards)

1. Controlled wrong implementation "rewrite puts the verb after the options":
   `return [fallback, *argv]` replaced by `return [*argv[: len(argv) - len(tokens)], fallback, *tokens]`.
   ```
   FAILED test_leading_options_before_the_lane_are_recognised
   >       assert (code, err) == (0, "")
   E       AssertionError: assert (2, '[ERROR] ...th --json)\n') == (0, '')
   FAILED test_equals_form_leading_option; test_library_root_options_may_lead_the_lane;
   test_explicit_run_is_the_same_call_as_the_bare_form;
   test_parse_args_equals_what_run_executes[argv1], [argv2]; test_parse_args_defaults_to_sys_argv
   ```
   (the root parser rejects `--worktree` before a verb: exit 2.)
2. JSON `lines` always emitted (`if result.lines:` -> `if True:` in `_check_record`):
   `FAILED test_json_has_a_lines_key_only_when_non_empty`, `test_doctor_json_of_a_tool_that_never_sets_lines_is_unchanged`,
   `test_a_crashed_check_carries_no_lines`, and the PRE-EXISTING `test_doctor.py::test_json_shape_exact`.
3. `fail_exit_code` ignored (`return fail_exit_code if ...` -> `return 1 if ...`):
   `FAILED test_fail_exit_code_is_used_when_a_check_fails`, `..._applies_to_json_mode_too`,
   `test_a_crashed_check_exits_with_fail_exit_code`, `test_fail_exit_code_bounds_are_accepted[2]/[128]/[255]`.
4. Second fallback accepted (`if existing_fallback is not None:` -> `if False and ...:`):
   `FAILED test_two_fallback_verbs_are_refused`.
5. Extra: surface key always emitted (`record["fallback"] = fallback` unconditionally):
   `FAILED test_surface_key_is_present_only_for_the_fallback_route`, `test_surface_without_a_fallback_has_no_key_at_all`,
   `test_a_manifest_without_a_fallback_is_byte_identical_to_the_one_generated_at_main`.

## 6. Gate

Run from `libraries/cli-extended`, foreground, `flock .../gate.lock nice -n 19 ionice -c 3`, PSI avg60 4-8 before each.
- `./run-gate.py --worktree /workspaces/vbpub/.worktrees/clix-04 r0-r1`, FIRST run: exit 1, FAIL, a single
  failure `tests/test_skills.py::test_o7_real_repo_skills_validate_and_install[cmru-cli-path0]`
  (coverage was 100%). Pre-existing: the same test fails on `main` (reproduced in the main checkout), because
  cmru's skill moved to `cmru/src/cmru/skills/cmru-cli` in `e361069d1`. Fixed in its own commit `0b3d0c7c5`
  (one path in the test); drop it if the controller prefers to fix it elsewhere, but r0-r1 is red on main without it.
- r0-r1 after the fix: `EXIT=0`; `Required test coverage of 100% reached. Total coverage: 100.00%`
  (`TOTAL 5411 stmts, 0 miss, 2500 branches, 0 partial`);
  `run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r0-r1/700764a0fa79cffe8b23951ef1d60b35.log`.
- r3: `EXIT=0`; `canary rejected: JSON redaction test fails when its guard is disabled`;
  `run-gate: lane 'r3' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r3/ff8f92fad15e474d4a1b1160ba77291b.log`.
- Not run: r2 (mutation lane; the program runs it once on the integrated branch).
- Library's own audit/surface check: the library has no `cli-extended.toml`/manifest of its own, so there
  is nothing to run for "its own CLI"; the substitute is section 4's real-consumer run and the in-suite tests.
- Both lanes printed `run-gate: WARNING profiling: cleanup crashed unexpectedly: 'NoneType' ...` (profiler
  noise unrelated to the verdict; not investigated).

## 7. 0.4.0 release-note text

See `CHANGES.md`, "### 0.4.0 — fallback verb and doctor exit code/detail lines (LCR-1, LCR-2)" (hand-written, not
`## [Unreleased]`). Summary: `VerbSpec(fallback=True)` default verb with one shared normalisation for `run` and
`RegisteredCli.parse_args`; help label `default verb`; route key `"fallback": true` only for that verb; contract
version 1 and manifest schema 7 unchanged and fallback-free manifests byte-identical; `register_doctor(fail_exit_code=1)`
(1..255); `CheckResult(lines=())` printed 4-space-indented before the remedy and in JSON only when non-empty.

## 8. Deviations and caveats (for the reviewer)

- I sent a few messages with two Edit calls and one `Intent:` line (rule: one Edit per message); content is unaffected.
- `cp` was used once to place the generated baseline JSON (see section 4).
- The existing CHANGES.md heading "### Unreleased — library backlog fixes (CX-BACKLOG, 2026-10-06)" actually describes the
  already-released 0.3.0; left as is (outside this package), my entry sits above it.
- A `fallback` verb adds `default verb` to `VerbSpec.summary`, hence to the fallback route's exported `summary`
  (only that route); its `behavior` list is unchanged.
- Nothing merged, pushed, tagged or released.
