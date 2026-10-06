# W2-PKG5 REPORT: surface lifecycle, skills, doctor, policy flip

Branch `cmru-w2-pkg5`, base `e1b8f4650`. Four implementer segments (the work crossed three checkpoints):
`e361069d1` (items 1-2), `7e99b63eb` (item 3 minus T5), `1ea4b49c1` (T5, item 4, findings, plants),
`e2387889b` (coverage fix). Edits were made with Edit/Write only; scratch generators wrote only to the scratchpad.
No image was built, no `.env` or secret was read. Claims below are what was run; items marked "predecessor"
come from the earlier segments' continuation notes and were not re-run in the last segment.

## Verdicts

| Check | Result |
| --- | --- |
| Full cmru suite (`pt.py`, serial, flock, nice/ionice, PSI full avg60 ~0) at `1ea4b49c1` | 3927 passed, 6 skipped |
| `coverage` lane at `e2387889b` (clean tree) | PASS, exit 0, 100.00%, 3928 passed, 6 skipped |
| `canary` lane at `e2387889b` | PASS, exit 0 |
| `cli-extended audit --cli cmru` | 13 pass, 0 warn, 0 manual, **2 fail** (AC-17, AC-18), both the CLI-EXT-26 gap |
| `cli-extended surface check` | **FAILS on exactly one message** (CLI-EXT-26, see Decision / library gaps); everything else current |

NOT green and NOT claimed: "surface check passes" and "audit has no fail". The controller accepted the CLI-EXT-26
library gap for cmru 6.0.0; the failure is pinned by an equality test (below) and backlog KI-61.

## Item 1: skills (W4, AC-19, CLI-D1) (predecessor, commit e361069d1)

- `register_skills_verbs(registry, package="cmru")` in `cli.py::_build_cli`: `cmru skills install|list|check|uninstall`.
- Bridge symlink `cmru/.claude/skills/cmru-cli` deleted; `test_packaging_cli_extended.py::test_no_checkout_skill_tree_duplicates_the_packaged_skill`
  replaces the symlink test; README documents `cmru skills install`.
- `src/cmru/skills/cmru-cli/SKILL.md` rewritten from the real `--help`; removed every listed error. Guard:
  `tests/test_w2_pkg5_skills_doctor.py` parses each `cmru ...` example against the registry (reuses
  `tests/test_w2_integ.py::_registry_problem`), plus a plant test and a "review-listed errors are gone" test.
- Every skills test uses a tmp HOME or `--dest`. One read-only `cmru skills check --json` was run once against the real
  HOME in segment 1 (it wrote nothing).

## Item 2: doctor (W5) (predecessor e361069d1; error-path tests added in e2387889b)

`src/cmru/doctor.py`: checks `git`, `docker`, `config`, `credentials` (reports WHICH variable, never the value),
`images`, `cli-extended` (reads the requirement from cmru's dist metadata); the library adds `skills`. Tests: each check's
ok/warn/fail/skip with fakes, the `--json` shape, exit 1 on any fail, a token-value-never-printed test (text and json),
a crashing-check test. The coverage lane initially showed 11 uncovered doctor lines (OSError, unreadable metadata
paths); `test_probe_failures_and_unreadable_metadata_degrade_to_a_result_never_a_crash` closes them.

## Item 3: surface lifecycle (W3b), S-CLI.9 rewrite, T3/T4/T5 (commit 7e99b63eb + T5 in 1ea4b49c1)

- `pyproject.toml [tool.cli-extended]` with ONE `[[clis]]` entry `id = "cmru"`, factory `cmru.cli:_build_cli`.
  **Justification:** `python -m cmru.handlers` builds the registry that the root mounts as `cmru handler`; both have
  identity "cmru" (the loader forces `identity.command_name == id`, a second "cmru" entry is refused as a duplicate).
  `test_one_cli_entry_covers_root_and_handlers_module_adapter` asserts the shared identity and the same command parsers.
- Paths: review `docs/cli-review.toml` (346 active cases, each linked to
  `tests/test_cli_review_cases.py::test_reviewed_case[...]`), manifest `docs/cli-surface.json`, spec `docs/SPEC.md`
  (generated region inside S-CLI.9), findings `docs/cli-review-findings.toml`.
- `tests/conftest.py`: `pytest_plugins = ["cli_extended.pytest_plugin"]`. `tests/test_cli_review_cases.py` is the
  data-driven sandboxed probe (empty non-repo cwd, tmp HOME, PATH = git + stub docker/gh/curl, no CMRU_/GITHUB_ env, HTTP
  and sockets blocked) plus the library `assert_cli_contract` black-box probe. In the last segment the black-box
  probe now passes `python=sys.executable`: without it the library puts its own install directory first on
  `PYTHONPATH`, and in the gate image that directory holds an older installed cmru, so the probe tested the wrong
  code (coverage lane failure `top-level help omits registered verb 'doctor'`, fixed).
- **T5** `tests/test_w2_pkg5_exploration_verbs.py`: for worktrees, status, resolve, versions check, doctor,
  skills list/check, handler wheel-validate/tarball-validate and dependencies/standards/tool-deps WITHOUT their write
  flag, a fixture git repo (valid orchestration config, committed) is snapshotted before and after: every file (path,
  mode, sha256, symlink target), `for-each-ref`, HEAD, `status --porcelain --ignored`, `worktree list`, `stash list`.
  `worktrees`, `resolve`, `skills list` and `dependencies` must exit 0, `standards` must reach its verdict (0 or 4).
  A self-test proves the snapshot sees file, ref, worktree changes.

### S-CLI.9 disposition table (predecessor, commit 7e99b63eb)

| Old row(s) | New home |
| --- | --- |
| Grammar tables: common flags, built-ins, per-leaf grammar incl. the 7 `python -m cmru.handlers` rows | Library-generated region (`<!-- cli-extended-surface:start/end -->`); the `python -m` rows are the same registry as `cmru handler *` |
| Semantic rows: common controls, run, worktrees, dependencies, build, publish, changelog, release, status, cleanup, abandon, init, versions x3, handler x7, tester-gate, resolve, get-py, standards, tool-deps | Option-level wording moved into the catalog `rationale`/`effects` fields (cases listed by route in `docs/cli-review.toml`); the verb-level cross-option contract is RETAINED, rows unchanged, in the subsection "Verb semantics not expressible in the catalog" |
| doctor and skills hand rows (predecessor scaffolding) | Removed; catalog cases `doctor/*`, `skills/*` take over |

The anchor `#s-cli9-canonical-cli-grammar-and-semantic-audit` still resolves (test kept).

### Deleted tests and what supersedes them (predecessor)

| Deleted in `tests/test_cli_spec_inventory.py` | Superseded by |
| --- | --- |
| `test_spec_cli_inventory_matches_registered_surfaces_and_options` (+ helpers `_marked_table`, `_argument_shape`, `_registered_surfaces`, `_registered_surface_groups`, `_inventory`) | `cli-extended surface check` (manifest, generated spec region, catalog), asserted by `test_surface_check_reports_nothing_beyond_the_known_library_gap` |
| `test_spec_builtin_help_and_version_grammar_matches_library_help` | the generated region plus `assert_cli_contract` (`test_real_executable_obeys_help_version_and_parse_contract`) |
| `_check_behavior_labels` and the mutating-label half of `test_registered_boolean_flags_default_off_and_help_marks_mutating_verbs` (circular) | T5 behavioural tree-unchanged tests; the boolean-default half is KEPT as `test_registered_boolean_flags_default_off` |
| `test_semantic_audit_covers_every_inventory_surface_and_option` | catalog cases linked by the pytest plugin (a row without a collected test fails collection; a candidate without a row fails `surface check`) |

Kept: dispatch-shell test, anchor test. Added: surface-check test, one-CLI-entry test.

## Item 4: audit, findings, policy flip (commit 1ea4b49c1)

- **Audit** (`cli-extended audit --cli cmru`): 13 pass, 0 warn, 2 fail, 0 manual. There were no `manual` items to
  record. The first run also failed AC-25 (no-path-hacks) on five guard tests that name the library checkout path to
  assert its absence (heuristic false positive: the file names the tree and `PYTHONPATH`/`sys.path`). Fixed by taking
  the needle from `tests/_cx_paths.py` (no behaviour change; the guards still assert absence); recorded as finding
  `adoption-audit-path-hack-heuristic`.
- **Policy flip:** `cli_support.UNEXPECTED_EXCEPTIONS_POLICY = "report"`. Audit AC-10 passes. New tests in
  `tests/test_cli_support_registry.py`: an unexpected exception in a real verb (`worktrees` with `_current_git_root`
  raising) gives the library one-line `unexpected ZeroDivisionError: ...` report, exit 1, no traceback, with the
  `--traceback` hint; with `--traceback` the exception propagates to the outer entrypoint, which prints the stack;
  under a patched "raise" policy it escapes. The flip changed eight pre-existing tests that pinned the old escaping
  behaviour (`pytest.raises(RuntimeError/AssertionError)` of `cli.main`); they now assert exit 1 and the message on
  stderr (test_cleanup_dry_run_yes, test_cli_dispatch_protocol_adversarial, test_cli_extended_semantics x4 sites,
  test_cli_final_deep_adversarial, test_cli_remaining_adversarial, test_release_candidate_changed_line_gaps).
- **Config-based `resolve`** now converts an unreachable registry (`OSError`/`URLError`) to a `CliFailure` exit 3 like
  `--repo/--prefix`; `test_config_based_resolve_with_an_unreachable_registry_exits_3_without_a_traceback`.
- **Real bugs found by the review probes and fixed** (each pinned by a catalog row or test): `handler bundle-manifest`
  without SOURCE_DATE_EPOCH (RuntimeError traceback, now `[ERROR]` exit 3); `resolve --repo/--prefix` unreachable
  registry (URLError traceback, now exit 3); config-based `resolve` (above).

## Findings summary (`docs/cli-review-findings.toml`, judged with the cli-extended-review rubric over the pack)

| id | status / severity | note |
| --- | --- | --- |
| adoption-skills-global-option | open minor, adoption | CLI-EXT-26 gap, accepted by controller; KI-61 |
| bug-bundle-manifest-traceback, bug-resolve-config-free-network, bug-resolve-config-based-network | fixed major | see Item 4 |
| adoption-audit-path-hack-heuristic | fixed minor | `tests/_cx_paths.py` |
| help-no-examples | open minor | no verb sets `VerbSpec.examples` |
| grammar-resolve-format-vs-json | open minor | `resolve --format` vs library `--json`; validate verbs lack `--json` |
| semantics-domain-runtimeerror-reported-as-unexpected | open minor | direct consequence of the flip: diagnosed refusals raised as bare `RuntimeError` render as `unexpected RuntimeError: ...` with exit 1 rather than exit 3/4 |
| semantics-publishing-verbs-dry-run-only | wontfix minor | automation verbs have `--dry-run`; human-facing cleanup/abandon/init have `--yes` |
| semantics-abandon-bare-selects-all | wontfix minor | inspect-first with confirmation and `--dry-run` |
| adoption-doctor-cli-extended-floor-warn | open note | stale editable dist metadata has no floor |
| help-description-style | open note | library verbs use different description style |

No open blocker or major remains. `surface sync` / `report` are current; `report` lists the five open findings and
nothing awaiting review or stale.

## Plant mutations

| Plant | Result |
| --- | --- |
| Read-only verb writes a file (`worktrees` writes `PLANT.txt`) | KILLED: `test_exploration_verb_leaves_the_tree_unchanged[worktrees]` failed; 13 others passed; reverted |
| Catalog row removed (case id renamed) | KILLED: `surface check` reports the stale case and stale spec block; the plugin aborts collection ("marked for CLI case ... but is not listed in test_ids"); reverted |
| Policy back to "raise" | KILLED: `test_registry_uses_the_single_policy_constant`, `test_module_entry_and_root_builders_agree_on_the_policy`, `test_an_unexpected_exception_in_a_real_verb_is_reported_not_raised` fail; audit AC-10 warns; reverted |
| SKILL.md example with a removed flag | KILLED in segment 1 (predecessor): the plant test in `test_w2_pkg5_skills_doctor.py` |
| Doctor check printing the token | KILLED in segment 1 (predecessor) by three tests, reverted |

## Library gaps

1. **CLI-EXT-26** (`libraries/cli-extended/BACKLOG.md`, filed in segment 2): `register_skills_verbs` builds its child
   registry without the parent's `global_options`, so `cmru skills` lacks `--log-prefix-time-short`. Accepted for 6.0.0.
   `test_surface_check_reports_nothing_beyond_the_known_library_gap` asserts the check findings EQUAL exactly that one
   message, so the library fix fails the test and forces removal of the tolerance and a floor bump. cmru backlog KI-61.
2. `cli_extended.testing._child_environment` prepends the library's own install directory to `PYTHONPATH` unless
   `python=` is passed, which lets an installed consumer shadow `src`. Worked around in the black-box probe; not filed
   in the library backlog (recommended follow-up).
3. Audit AC-25's heuristic cannot distinguish a negative guard test from a path hack (worked around, see above).
4. Stale editable dist metadata has no cli-extended floor, so doctor's `cli-extended` check warns in a development venv.

## What I did NOT run

- The mutation lane and Assay R1 (not requested for this package).
- `installed-wheel` lane / `tools/installed_wheel_smoke.py` (no wheel built; no image built).
- The segment-1 plants (SKILL flag, doctor token) were not re-run in the last segment.
- A fresh-clone / sparse-snapshot run of `cli-extended surface check` outside the canary lane (the canary lane passing
  shows the repo-walking tests are snapshot-safe; the surface check itself was run only in the worktree).
- Nothing was pushed; the controller merges.
