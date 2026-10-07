# CLIX-04 adversarial review (cli-extended 0.4.0, LCR-1 + LCR-2)

Subject: `/workspaces/vbpub/.worktrees/clix-04` tip `6b00a80fc`, merge-base with `main` = `4670f53a6`.
Reviewer worktree: `rv-clix04` (control, at the tip, all plants reverted; `git status` clean).
Phase 1 was blind (diff + spec 2.1/2.3/2.4/7A only); the report was read afterwards (Phase 2).

## Verdict: ACCEPT-conditional

The library code is correct, I could not break it, and the tests are not hollow. Two conditions
must be met before/at release; neither needs a code change to `src/`.

### Blockers (conditions)

1. **RELEASE: `cmru release` as-is mints 1.0.0, not 0.4.0.** `cmru.toml` has `bump = "conventional"`
   (`libraries/cli-extended/cmru.toml`, `[project.version]`). `detect_changed_projects` feeds
   `_git_log(last_tag, <project paths>)` to `_bump_from_commits` (`cmru/src/cmru/version.py:278-286`);
   `_CC_BREAKING = ^[a-z]+(\([^)]+\))?!:|BREAKING[ -]CHANGE` (`version.py:274`). The range
   `cli-extended-v0.3.0..6b00a80fc -- libraries/cli-extended` contains `f6fef1d6b feat(cmru)!: retire
   cmru-agent/controller ...` (it touches `libraries/cli-extended/SPEC.md`). I ran (read-only, no
   transaction) `_git_log` + `_bump_from_commits` + `bump_version("0.3.0", ...)` on that range: 17
   messages, bump `major`, result `1.0.0`. This is the same class of mistake as the nyxloom v1.0.0 incident
   in memory. The spec (2.4 "cmru release cuts 0.4.0") and the report section 2 ("0.4.0 will come from
   the release tool") do not mention it.
   Prescription: the controller releases with an explicit override: `--minor` (`version.py:746`,
   override wins over conventional) or `--set-version 0.4.0`, and re-passes it on any `--resume`
   (see memory note on `--resume` forgetting `--set-version`). Verify the minted tag is
   `cli-extended-v0.4.0` before publish. Decision ask (not improvised): whether to also drop the `!`
   from future cmru commit subjects that touch other projects' docs is a product call for the carver.
2. **DOC: stale heading in the shipped CHANGES.md.** `CHANGES.md:50`
   `### Unreleased — library backlog fixes (CX-BACKLOG, 2026-10-06)` describes the already-tagged 0.3.0
   (`cli-extended-v0.3.0` exists; that heading is also present in the 0.3.0 tag's file at line 10). The 0.4.0
   wheel/doc would then read "0.4.0 ... Unreleased (really 0.3.0) ... 0.2.0". The report acknowledges it
   and leaves it. Prescription: rename it to `### 0.3.0 — library backlog fixes (CX-BACKLOG, 2026-10-06)`
   (one line, in this package or by the controller before release). Mechanically harmless either way
   (see section 6), so this is a correctness-of-docs condition, not a release-tool one.

### Non-blocking findings (no action required for ACCEPT)

- N1. `CheckResult.lines` validation rejects only `\n` and `\r` (`doctor.py:57-60`); `"a b"`,
  `"a\x0bb"` are accepted (probed). The spec says "single-line strings"; the human output uses
  `str.splitlines`-breakable characters. Low risk, no doctor consumer sets `lines` yet. Optional: refuse
  any char where `len(s.splitlines()) > 1`.
- N2. `run_cli(fallback_verb="nonexistent")` is not validated (only `build()` ever sets it; `RegisteredCli`
  construction by hand is test-only). Unreachable via `CliRegistry`.
- N3. Grammar facts for run-gate package E (spec-conformant, but worth knowing): `tool --fast -- LANE`
  is NOT rewritten (rule 2: `--` returns None) so it exits 2; a lane named `help`/`version` behind
  leading options (`--fast help`) is not rewritten; a fallback-verb option with `nargs` other than
  None/0/`?`/int (e.g. `*`, `+`) aborts the scan (no rewrite); with `allow_abbrev=True` an abbreviated
  leading option is not recognised (scan uses exact `_option_string_actions`). All documented-by-rule
  behaviours, all errors surface as usage errors, none swallowed.

## 1. LCR-1 argv probes (my own registry: root option `--profile N`; fallback verb `run LANE` with
`--worktree P`, `--fast` bool, `--opt [X]` nargs=?; plus verb `other`)

Probe script ran `cli.run(argv=...)` and `cli.parse_args(argv=...)` for 34 argvs. Results (all as the spec):

| argv | observed |
|---|---|
| `L`, `docter`, `""` | handler `lane=L/docter/""` (unknown lane reaches the handler, rule 5) |
| `--worktree X L`, `--worktree=X L`, `--fast L`, `--profile p L`, `--json L`, `--worktree= L` | rewritten, handler gets the option |
| `--opt --fast L` (`?` option followed by a dash token) | handler `fast=True lane=L` |
| `L --worktree X` | handler ok |
| `--worktree other`, `--worktree help` | `other`/`help` consumed as the VALUE; exit 2 (lane missing), argparse-consistent |
| `--` + `L`, `--bogus L`, `-x L`, `--fast -- L`, `--fast=1 L` | no rewrite, exit 2 |
| `--help`, `--version` | exit 0, help/version; `parse_args` SystemExit 0 |
| `other`, `--profile p other`, `help`, `version`, `help run`, `run L`, `run other` | verb/builtins win; `run other` = lane `other` |
| `[]` | exit 0 (help); `parse_args([])` UsageError, as on main |
| `--worktree`, `--fast`, `--worktree X` (no lane) | exit 2 from argparse |
| delegate group with its own fallback: `grp go L`, `grp L` | both run the child's handler; `--help` on parent and group list `[default verb]` and the extra usage line |

`parse_args` equalled the namespace the handler received in every case where a handler ran. Registry
without a fallback: ran 16 argvs (empty, bare token, root options, help/version forms, `doctor`,
`doctor --json` with a failing check, `--`, `--bogus`) plus the full surface JSON (1078 lines) against
the MAIN checkout source and the tip source: stdout, stderr, return codes and manifest are byte-identical
(identical md5). Rule 1 refusals (second fallback, delegate, no ArgumentSpec, non-bool -> TypeError)
observed.

## 2. LCR-2

`fail_exit_code`: refused `0, 256, True, False, "1", 1.0, None, -1`; accepted `1, 2, 255`. Handler
returns it only when a check fails (tests plus plant 4 below). `lines`: tuple of str with no `\n`/`\r`
(list and `("a\nb",)`, `(1,)`, `("a\r",)` refused; N1 above). Printed four-space indented before
remedy (plant 3). JSON `lines` only when non-empty (plant 9). `replace(result, remedy=...)`
(`doctor.py:156`) preserves `lines`; the crash/non-JSON paths build `CheckResult` without lines.
Existing doctor output and `tests/test_doctor.py::test_json_shape_exact` unchanged.

## 3. Beyond-brief changes

- `build()` refusal of a fallback on `single_command` (`parser.py:1696-1702`): sound (the rewrite would
  insert a verb name into a single-command parser that has none); tested by
  `test_a_single_command_registry_refuses_a_fallback`; plant 7 (guard disabled) fails exactly that test.
- SPEC.md edits: read all hunks against the code; accurate (rules, `fail_exit_code` 1..255 with
  `ValueError` at registration, `lines`, route key "MUST be absent from every other route", usage line).
  The spec text says run-gate's usage is `LANE`; the library prints the first ArgumentSpec's display name
  (`tool [options] lane ...` for a lowercase arg), which SPEC/CHANGES describe generically as `ARG`. Fine.
- `0b3d0c7c5` `tests/test_skills.py`: on main the path `cmru/.claude/skills/cmru-cli` does not exist
  (`ls` fails; the symlink was deleted in `e361069d1`), so `assert (path / "SKILL.md").is_file()` fails
  there; `cmru/src/cmru/skills/cmru-cli/SKILL.md` exists. The fix changes only the path; every assertion
  (validate, install, banner, check) is unchanged. Correct, not a weakened assertion.

## 4. Plants (10, all different from the implementer's 5; each applied by Edit in `rv-clix04`, test run,
reverted with `git checkout -- src`, tree clean after)

| # | plant | killed by |
|---|---|---|
| 1 | drop `"version"` from the reserved first-token set | `test_help_and_version_win_over_the_fallback`, `test_assert_cli_contract_passes_for_a_fallback_cli` |
| 2 | leading-option scan ignores the fallback verb's options (`extra`) | 7 tests (`test_leading_options_...`, `test_equals_form...`, `test_library_root_options...`, `test_explicit_run...`, 2x `parse_args_equals`, `parse_args_defaults_to_sys_argv`) |
| 3 | doctor prints remedy before lines | `test_human_output_prints_lines_indented_before_the_remedy`, `test_lines_survive_remedy_normalisation` |
| 4 | `fail_exit_code` upper bound 256 | `test_fail_exit_code_outside_one_to_255_is_refused[256]` |
| 5 | surface key always emitted (`False` too) | 3 tests incl. the byte-identical baseline |
| 6 | fallback dropped from the signed route context | `test_the_fallback_route_changes_the_review_signature` |
| 7 | `single_command` refusal disabled | `test_a_single_command_registry_refuses_a_fallback` |
| 8 | Markdown help uses `behavior_labels` (no `default verb`) | `test_catalog_marks_...`, `test_markdown_help_with_a_configure_callback_lists_the_label` |
| 9 | JSON always carries `lines` | 3 LCR-2 tests + pre-existing `test_doctor.py::test_json_shape_exact` |
| 10 | leading `--` ends the scan and is rewritten | `test_double_dash_first_is_not_rewritten` |

No survivors.

## 5. Consumer dimension

In-estate importers of `cli_extended` (grep of `*.py`, `.worktrees` and `.git` excluded; dstdns grep: none):

| consumer | uses fallback / lines / fail_exit_code | can behaviour or manifest change? | evidence |
|---|---|---|---|
| `cmru` (src + tests) | none | no | `cli-extended surface check` with tip source on PYTHONPATH from `cmru/` (worktree): "CLI surface check passed", rc 0 |
| `scripts/debian-install-v2` | none | no | surface check rc 0 |
| `scripts/netcup` (3 CLIs: install-host, monitor-task, scp-api) | none | no | surface check `--cli` each: rc 0 x3 |
| `nyxloom` (`cli_registry.py`, `backlog_wizard.py` use `CliFailure`, registry) | none | no manifest (no `cli-extended.toml`; surface check refuses to run: no config) | code grep, behaviour proven by the byte-identity probe |
| `libraries/cli-extended/tests` | the new tests only | n/a | gate r0-r1 |
| `/workspaces/dstdns` | no `cli_extended` import | no | grep |

Committed surface manifests: `cmru/docs/cli-surface.json`, `scripts/debian-install-v2/docs/cli-surface.json`,
`scripts/netcup/cli-surface-{install-host,monitor-task,scp-api}.json`: all four configured projects pass
`surface check` (cmru and debian-install-v2 printed pre-existing open minor/note findings, unrelated).
New optional parameters are keyword-only/defaulted (`VerbSpec.fallback`, `fail_exit_code`, `lines`
appended last, `RegisteredCli.fallback_verb` last, `run_cli(fallback_verb=...)` keyword-only), so no
positional caller shifts.

## 6. Release readiness (reasoned from cmru source, `cmru release` not run)

- Version: setuptools-scm from tag (`pyproject.toml` `dynamic=["version"]`, `tag_regex`); no hand bump
  needed. But see blocker 1: the tag cmru mints is decided by `_bump_from_commits` and defaults to 1.0.0.
- Changelog generation (`cmru/src/cmru/changelog.py`): the generated `## [0.4.0] - date` section is
  inserted after `<!-- cmru: release history -->` (`CHANGES.md:175`, `changelog.py` insert at the
  marker). `_HEADING_RE` (`^## \[...\] - date$`, line 31) and `_UNRELEASED_HEADING_RE` (line 38) match
  only level-2 bracket headings; the hand-written `### 0.4.0 — ...` and `### Unreleased — ...` are level 3
  and match neither, so no collision/duplicate/drop. KI-30 `_PLAIN_UNRELEASED_RE = ^## \[Unreleased\]$`
  (line 41) does not match any heading in the file, so `_unreleased_body` is empty and the check passes.
  The REL-02 regenerate branch only fires when a `## [0.4.0] - date` already exists with a cursor; it does
  not. Layout is therefore mechanically fine; the generated 0.4.0 section will also list the
  cmru-project commits that touched `libraries/cli-extended` paths (17 in range), which is the
  existing behaviour for this project.
- Misfiling risk: only the stale heading of blocker 2.

## 7. Gate (my control worktree at the tip, from `libraries/cli-extended`, flock/nice/ionice, foreground, PSI avg60 about 6)

- `./run-gate.py --worktree /workspaces/vbpub/.worktrees/rv-clix04 r0-r1`: `EXIT=0`;
  `Required test coverage of 100% reached. Total coverage: 100.00%`;
  `TOTAL 5411 0 2500 0 100%`;
  `run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r0-r1/3674cb3820b462c5a995d05375633f48.log`.
- `... r3`: `EXIT=0`; `canary rejected: JSON redaction test fails when its guard is disabled`;
  `run-gate: lane 'r3' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r3/976d577200109afb1122412b0344050a.log`.
- Both lanes printed the profiler warning `cleanup crashed unexpectedly: 'NoneType' object has no attribute 'get'`
  (as the report says; verdict unaffected). r2 not run (program runs it once on the integrated branch).

## 8. Phase 2: report reconciliation

Confirmed by me: test counts (46 + 32 collected); the file/line map matches the diff; the 5 plants are
plausible and consistent with my plants' coverage; the test_skills pre-existing failure and fix;
byte-identity (I re-proved it with my own registry); the gate numbers (same coverage, same verdicts,
different log ids); debian-install-v2 surface check; no `## [Unreleased]` added; the stale-heading
caveat (it is blocker 2). Not confirmed or incomplete: section 2 ("0.4.0 will come from the release tool")
omits that cmru would mint 1.0.0 (blocker 1); the report says it ran `audit` on debian-install-v2 (I did not
re-run `audit`, only `surface check` on it, cmru and 3 netcup manifests). Process deviations noted by the
implementer (two Edit calls per message, one `cp`) have no effect on content.
