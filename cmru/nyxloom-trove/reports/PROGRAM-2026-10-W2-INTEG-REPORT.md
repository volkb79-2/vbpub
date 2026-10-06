# W2-INTEG REPORT (parts A and B; part C pending the controller's image)

Branch `cmru-w2-integ`, base `d8ac6b66d`. All edits by Edit/Write. Nothing pushed, no image built.

## Evidence (what I ran)
- Full cmru suite via `pt.py` (serial, flock, nice/ionice, PSI full avg60 0.00 before): **3512 passed, 6 skipped, 0 failed, 0 xfail**, with
  `--cov=cmru --cov-branch`: TOTAL 11875 stmts / 5130 branches, 0 missed, **100%** (log `scratchpad/integ-run4.log`).
- The controller baseline's one failure (the strict XPASS `test_run_child_never_puts_a_candidate_cli_extended_source_root_on_pythonpath`)
  had its marker removed; it passes.
- NOT run: the `coverage` and `canary` run-gate lanes, the installed-wheel/gate-image paths, any image build (part C).

## Per item
**A1 env renames.** `CMRU_BIN/RUN_LOG/SHOW_RUN_DETAILS/LOG_APPEND/LOG_PREFIX_TIME_SHORT` -> `CMRU_INTERNAL_*` (writers and readers, one change:
`cli.py`, `runner.py`, `output.py`, `transaction.py`). `CMRU_LOG_PREFIX_TIME_SHORT` is internal (set only by `--log-prefix-time-short`; no
doc names it as an operator input). New `transaction.internal_launcher(repo_root)` + `INTERNAL_BIN_ENV`: `CMRU_INTERNAL_BIN` is honoured only
inside a verified transaction child (invalid child context fails closed -> ignored); used by `run_child` and `_dispatch_independent_git_families`.
**Deviation:** the four log/presentation variables are NOT child-gated: they are set and read by the same process from its own
`--show-run-details/--log-append/--log-prefix-time-short` flags (gating would break `cmru run --show-run-details` outside a transaction). Only the
executable-selecting variable is gated. SPEC S8.1 documents this, plus `CMRU_RELEASE_LOG` (operator input) and `CMRU_NATIVE_RELEASE_LOGGING=0` (test-only).
Tests moved: `test_workspace_adversarial_review`, `test_transaction_dispatch_adversarial`, `test_core_orchestration_adversarial`,
`test_cli_build_output_semantics`, `test_version_runner_contracts`, `test_git_auth`, `test_high_residual_boundaries`, `test_cli_config_final_adversarial`,
`test_config_runtime_exhaustive`, `test_runtime_contracts_final`, `test_runner_quiet_output`, `test_release_native_logging`.
`test_release_end_to_end_real_git` now uses a `bin/cmru` wrapper on PATH (no env seam).

**A2 run-step.** `runner_cli` and `_run_step_cli` deleted (root mount was already gone on the merged tree), unused imports removed. Removed/rewrote the tests
that exercised them (`test_w2_pkg2_delegates` test + factory entry, `test_release_coverage_gaps`, `test_runtime_highrisk_residuals`,
`test_version_runner_contracts`, `test_cli_adversarial_contracts`, `test_semantic_boundary_contracts`, `test_cli_extended_semantics`; the render-plan
validity assertion was kept as `test_runner_step_plan_rejects_an_invalid_command`). Refusal text is now `Use the installed 'cmru run --step' command`
(`test_cli_extended_semantics`, `test_module_entrypoints`). The `run --step` behaviours are covered by PKG-1's tests.

**A3.** `test_init_scaffolding` already imported nothing from `cmru.cli` on the merged tree (headline test uses `importlib.metadata`): no change.
`test_git_auth` post-D10 test already asserted the post-D10 PYTHONPATH; the strict-xfail marker was on the sibling test and is removed.
`test_project_fixture.py:34,48` already asserts cli-extended is NOT copied (CX-D1-correct): no change.

**A4.** `POLICY_REFUSED` alias deleted; `standards.py`, `tool_deps.py`, `tests/test_standards.py` use `REFUSED`.

**A5 SPEC.** S8 rewritten: five-value table, verbs using 4 (release plan/uncommitted, abandon blockers/recheck/lock, standards, tool-deps), S12.2d
sentence (plan refusal is 4, not 1), S8.1 environment. CIU S10.3 stops at 0-3 (verified in `ciu/docs/SPEC.md`), so the claim "identical" was dropped:
cmru is a superset (adds 4).

**A6 sweep** (table below). **A7** `interactive = ["cli-extended[interactive]>=0.2.0"]` in `pyproject.toml`; `Provides-Extra: interactive` /
`Requires-Dist: questionary>=2.1.1; extra == "interactive"` verified in `libraries/cli-extended/artifacts/cli-extended-v0.2.0/dist/*.whl` METADATA.
Hint now `pip install 'cmru[interactive]'` via `cli_support.INTERACTIVE_EXTRA`, passed as `interactive_extra=` at both `init` run sites (root `main`,
`scaffold.init_main`); tested through both entries.
**A8** not done: the gate image does not yet bake cmru, so both branches of the module-entry test in `test_installed_wheel_subprocess.py` stay.

**B.** N8 literal digest pinned (`test_tester_unified_image`); N16 probe asserts `metadata['Name'] == 'cmru'`; nit 7: the `cmru.cli` alias test matches the
PKG-1 stderr (`Use the installed 'cmru' command; python -m cmru.cli is not supported.`), verified by the passing suite; `forward_from` is now a required
keyword in `_child_release_args` and `_dispatch_independent_git_families` (all 25 test call sites pass `forward_from=None`); PKG-2 handoff 5 = A6.

## Sweep table (outside `.worktrees/`, CHANGES history, archived reports, KNOWN_ISSUES history)
| Hit | Disposition |
|---|---|
| `cmru/README.md` publish/cleanup/discard/run-step (x7) | fixed (`--from-checkout`, `abandon PATH`, `release --discard X`, `run --step`) |
| `README.md:126` bare `publish` | fixed (`--build-output ID`) |
| `docs/RELEASE-TOOLING.md:36`, `ciu/docs/README.md:59`, `game_stuff/empyrion/README.md` (x2, also stale `--project`) | fixed |
| `cmru/docs/{CONSUMERS,DESIGN-GUIDE,RELEASE-TRANSACTIONS,SPEC,plan-contextual-config}.md` | fixed |
| `src/cmru/skills/cmru-cli/SKILL.md` (cleanup modes, `--abandon`, `--ref`, bare publish, `get`) | fixed |
| `delegate_targets.py` docstring | fixed |
| all estate `cmru.toml`/orchestration/`run-gate*.toml`/templates (handler, tester-gate argv) | TOML only: no stale callers (that was ALL the first sweep covered; see the round-1 rows below) |
| **round 1:** `game_stuff/empyrion/run-full-workflow.sh:248,251` (`build --project`, `publish --project`) | fixed: `cmru build empyrion-translation`, `cmru publish empyrion-translation --from-checkout` |
| **round 1:** `game_stuff/empyrion/TOOLS-README.md:306,309`, `game_stuff/empyrion/README.md:136,601` | fixed (same spellings) |
| **round 1:** `pwmcp/docs/DEPLOYMENT.md:167,198` (bare `publish pwmcp`) | fixed: `--from-checkout` |
| **round 1:** `cmru/docs/CONSUMERS.md:427` (`run example-wheel --build`) | fixed: `--step build` |
| **round 1:** `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md:46,272` (`release --project cli-extended`) | fixed: `cmru release cli-extended` |
| **round 1:** `docs/spec-cmru-installer-v2.md:58`, `ciu/docs/CIU-V8-HANDOFF-2026-09-03.md:24` (`get-py --project`) | fixed: `get-py <name>` |
| **round 1:** `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md:34`, `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md:855,1096`, `cmru/docs/reviews/KI-24-REPORT.md`, `ciu/nyxloom-trove/archive/*`, backlogs (`get-py --project ciu`) | NOT changed: dated design proposals / review records that quote the then-current spelling |
| **round 1:** `docs/plan-post-cmru-estate-release.md:30` (`--discard-*-on-release`), `assay/nyxloom-trove/{W1-RESUME,STATE}.md` (`release --project assay`) | NOT changed: dated plans and resume notes |
| `run-gate-project/REMOTE-LANES-BUILDKITE.md:408` | names the handlers only, no flags: no change |
| `docs/plan-cmru-*.md`, `assay/nyxloom-trove/*`, `cmru/KNOWN_ISSUES_*`, `ciu/nyxloom-trove/archive/*` | historical records, intentionally untouched |
| `--forward-cgroup-parent*` | no callers in tracked non-history files |
| `get-py` multi-project stdout | `plan-contextual-config.md` already corrected by PKG-2; nothing else describes it |

## Plants (each by Edit, test run, reverted; `git diff` of `cmru.toml`/`exit_codes.py` clean afterwards)
| Plant | Killed by |
|---|---|
| (a) `internal_launcher` returns the variable without the child check | `test_the_launcher_variable_is_ignored_outside_a_transaction_child`, `test_an_inconsistent_child_context_fails_closed`, `test_run_child_ignores_the_variable_and_the_old_name_outside_a_child`, `test_the_family_dispatcher_ignores_the_variable_outside_a_child` |
| (b) `_apply_output_options` writes old `CMRU_SHOW_RUN_DETAILS` | `test_writers_set_only_the_internal_names`, `test_output_options_export_only_explicit_flags` |
| (c) `--target` in `cmru/cmru.toml`'s wheel-build argv | `test_every_estate_cmru_argv_parses_against_the_registry` |
| (d) `POLICY_REFUSED = REFUSED` re-added | `test_refused_is_the_only_exit_code_name_for_four` |

## Part C (image `tester-unified:cmru6-integ`)
**Image switch.** The only `image =` line in `cmru/run-gate.toml` belonged to the `cmru-mutation` environment; `coverage` and `canary` run in the
root `tester-unified` environment (`run-gate.root.toml`, still `:local`). So I added a project environment `cmru-tester` (mirrors the root one:
ephemeral, `CGROUP_PARENT_DEV_GATES`, forwards `CGROUP_PARENT_DEV_BACKGROUND`) on the new image, pointed `coverage` and `canary` at it, and also
switched `cmru-mutation`'s image; each carries `# TODO(cmru-6.0 landing): back to tester-unified:local after the retag`. The `assay` and
`installed-wheel` lanes still use the root environment (not run). At landing: restore the two images, delete `cmru-tester`, and point the
two lanes back at `tester-unified`.

**A8.** The module-entry test in `test_installed_wheel_subprocess.py` is now exact: the child inherits the test process's environment, so the test
asks `importlib.metadata.version("cmru")` and asserts exit 0 plus help when installed, exit 3 plus the one-line message when not (no either-or).
It passes on the host (no distribution) and inside the image (distribution baked): the lane ran it with 3512 passed, 6 skipped.

**Version `0.0.0+tester.unified`.** Consumers of the cmru version: `cli_support.cmru_version()` (headlines, `--version`) and `cli.py:404`, which
compares the bound launcher's reported version with the active runtime (both read the same metadata, so they agree). The manifest derives the
version from the wheel name (D3), not metadata, and no floor check reads cmru's own version. So `0.0.0` does not matter in the lane. I did not
test other consumers of the image.

**Lanes (each read in a separate step):**
- `coverage` PASS, exit 0: 3512 passed, 6 skipped, total coverage 100.00%; log `/tmp/run-gate/lanes/coverage/0854a03c81a5d3eda24e9ff18257f0f4.log`
  (at commit `5150de172`).
- `canary` first run FAIL: a lane-only failure. The canary control runs a sparse snapshot with no sibling projects, so
  `test_the_estate_scan_actually_finds_the_project_contracts` (which demanded `ciu/cmru.toml`) failed. Fixed by requiring only cmru's own contract and the
  `init` template (>= 4 argvs, a handler and a tester-gate argv). The full-estate guard test is unaffected and passed in the sparse run.
- `canary` after the fix PASS, exit 0; log `/tmp/run-gate/lanes/canary/d4fddfb5e38902cce59607b4690928d3.log`. The coverage lane was NOT re-run after the
  test-only fix (test_w2_integ.py passes under pt.py, 19 passed).
- `mutation`, `assay`, `installed-wheel`, `gate` lanes not run.

## Review fix round 1 (successor implementer; review `REVIEW-ROUND1.md`, ACCEPT-conditional C1-C7)
Edit/Write only; no image built; nothing pushed.

**C1 (estate guard shrinks to cmru alone).** `test_the_estate_scan_actually_finds_the_project_contracts` now discovers
project contracts on disk independently of `_SKIP_PARTS` (`*/cmru.toml`, `*/*/cmru.toml`, minus `.worktrees`/`.git`) and requires every one
to be among the scanned files; for each of `ciu nyxloom assay topos pwmcp tls-edge run-gate-project` that has a `cmru.toml` on disk (the
first canary attempt showed the sparse snapshot has a bare `run-gate-project/` dir, the target of the `cmru/run-gate.py` symlink, with no
contract, so a mere directory is not enough to require one), that file must be scanned; when any exists (a full checkout) at least 8 distinct project contracts must be on disk. The sparse canary snapshot
still needs only cmru's own contract and the `init` template.
**C3 (string / shell blind spot).** The guard now tokenises every TOML string value with `shlex` (descending into `bash -c`/`sh -c`
payloads, splitting on `&&`, `||`, `;`, `|`, newlines; a segment counts only when `cmru` is its command word after `VAR=x`/`exec`/`sudo`...)
and runs each `cmru ...` through `_registry_problem`. Every `*.sh` in the estate (comments dropped, `\` continuations joined) is scanned the
same way, and its text also goes through the removed-spelling regex. Unit tests cover string, `bash -c`, prose-is-not-a-call and the
`.sh` reader; a coverage test requires `tls-edge/scripts/release.sh` and `game_stuff/empyrion/run-full-workflow.sh` to be scanned and to
contain cmru calls. First full-estate run: zero false positives.
**C4 (env leak).** `_clean_env` now sets then deletes every name the code under test may write (the five `CMRU_INTERNAL_*`, the five
old names, `PYTHONUNBUFFERED`, the child marker), so monkeypatch records "absent" and teardown removes what was written. A module-scoped
baseline fixture plus a last-in-file probe `test_zz_no_internal_name_or_pythonunbuffered_leaks_out_of_this_file` fails on any leak.
Observation (not fixed, outside this file): other test files already leave `CMRU_INTERNAL_RUN_LOG`/`SHOW_RUN_DETAILS`/`LOG_PREFIX_TIME_SHORT`
in `os.environ` (found when my new tests failed in the full suite until they cleared them); the probe compares to the baseline so it is
order-proof. A repo-wide leak sweep belongs to a later package.
**C5 (reserve the prefix).** `config.is_reserved_internal_env(name)`: the old exact set (`CMRU_INTERNAL_RELEASE_PREFLIGHT_FD`,
`CMRU_RELEASE_PREFLIGHT_SNAPSHOT`) OR any name starting `CMRU_INTERNAL_`; used by `_scalar_env` (project/orchestration `[env]`) and
`apply_release_env`. Test: each of the five names, the two preflight names and an arbitrary `CMRU_INTERNAL_X` are refused on both paths; an
ordinary `CMRU_*` name (and `CMRU_INTERNALS`) is still accepted.
**C6 (landing hazards).** `# TODO(cmru-6.0 landing)` markers on the `coverage` and `canary` `environment =` lines; the mutation-lane
paragraph moved back above `[environments.cmru-mutation]`. **Complete landing-revert list** (all go back after the `tester-unified` retag;
`grep -rn "cmru6-integ\|TODO(cmru-6.0 landing)" cmru/run-gate.toml` finds the first four):
  1. lane `coverage`: `environment = "cmru-tester"` -> `"tester-unified"`;
  2. lane `canary`: same;
  3. delete `[environments.cmru-tester]` (image `tester-unified:cmru6-integ`);
  4. `[environments.cmru-mutation]` `image` -> `tester-unified:local`;
  5. `CMRU_TESTER_UNIFIED_IMAGE = "tester-unified:local"` in `cmru.orchestration.toml:59` and `cmru.project.sample.toml:23` STAY `:local` (the
     retag makes them right: until then the real `cmru release` gate uses the old image);
  6. lanes `assay` and `installed-wheel` stay on the root `tester-unified` environment (`run-gate.root.toml`, `:local`): never switched, not run here.
**C7 + decision ask 1 (refusals exit 4 everywhere).** New `transaction.RefusedBeforeChange(RuntimeError)` and
`transaction.ReleaseLockHeld(RefusedBeforeChange)`; `release_lock` raises the latter. `build` (uncommitted project paths: raises
`RefusedBeforeChange`; held lock) and `release` (held lock; uncommitted paths already exited 4) and `abandon` (held lock) map them to
`exit_codes.REFUSED`; the string match in `abandon` is gone. A build failing after it started (e.g. `fetch_origin_main` raising) stays exit 1.
SPEC S8 now lists `build` and the lock for `release`, states the rule and the typed exception, and lists every decline-exits-0 site
(`init` write; `abandon` and its retained-build-worktree discard path; `cleanup`). Tests: build uncommitted = 4 (also the existing
`test_build_refuses_uncommitted_snapshot_before_fetch_or_workspace`, changed 1 -> 4), build lock = 4, release lock = 4, abandon lock = 4
(raises the typed exception now), real second lock holder raises `ReleaseLockHeld`, post-start build failure = 1.
**Decision ask 2: `runner.run_step` deleted** (and its docs: SPEC, DESIGN-GUIDE, CONSUMERS; no caller existed, MDT never used it).
Behaviours its direct tests pinned and where each is pinned now (`tests/test_run_step_replacements.py`, through the real `cmru run --step`):
| Direct test deleted | Behaviour | Replacement |
|---|---|---|
| `test_runner_step_uses_nearest_central_config_for_project_path` | step runs in the project root, project `[env]` as extra env, protected `CMRU_RUNTIME_KIND`/`CMRU_INTERNAL_BIN` (bound `cmru`) | `test_run_step_executes_in_the_project_root_with_env_and_protected_runtime` |
| `test_raw_runner_uses_project_local_log_root` | detail goes to `<project>/logs/cmru/build.log` | `test_run_step_quiet_detail_goes_to_the_project_local_log_root` (also: quiet output not streamed) |
| `test_runner_run_step_requires_one_project_and_declared_step` (declared-step half) | undeclared step refused | `test_run_step_refuses_an_undeclared_step` (exit 2, nothing runs) |
| `test_runner_step_refuses_central_config_without_exact_project_match`, same test's cardinality half | refuse a project the config does not name | `test_run_step_refuses_a_project_the_config_does_not_register` (exit 2). The path-to-project inference itself (nearest orchestration, project-root match) existed only in `run_step`; `run` selects by name, so it has no counterpart and is gone by design |
**Nits.** `flat =" ".join(` typo fixed; README sentence added (`cmru[interactive]` needs `--find-links` to the release assets).
**Out of scope, noted only.** `cmru standards cmru` fails "template revision is 4; expected 5" on the base tree (predates this package; Wave 3 runs `cmru standards --update`).

**Plants** (each by Edit, targeted tests run, reverted; `git status` shows only intended files afterwards). All killed:
| Plant | Killed by |
|---|---|
| C1: every sibling project added to `_SKIP_PARTS` | `test_the_estate_scan_actually_finds_the_project_contracts`, `..._covers_the_shell_scripts_that_call_cmru` |
| C3 string: `CMRU_PLANT = "cmru publish ciu"` in `cmru.orchestration.toml` `[env]` | `test_every_estate_cmru_argv_parses_against_the_registry` |
| C3 `.sh`: `cmru publish tls-edge` line in `tls-edge/scripts/release.sh` | same test |
| C4: `_clean_env` back to `delenv(raising=False)` | `test_zz_no_internal_name_or_pythonunbuffered_leaks_out_of_this_file` |
| C5: reservation back to the exact set (`CMRU_INTERNAL_BIN` accepted) | `test_project_env_cannot_declare_internal_names` x6 (the five names and `CMRU_INTERNAL_X`) |
| C7 build: uncommitted raised as plain `RuntimeError` (exit 1) | `test_build_with_uncommitted_project_paths_is_a_refusal_exit_4`, `test_build_refuses_uncommitted_snapshot_before_fetch_or_workspace` |
| C7 lock: `release_lock` raises plain `RuntimeError` and `release` loses its mapping (exit 1) | `test_release_while_the_release_lock_is_held_is_a_refusal_exit_4`, `test_transaction_release_lock_refuses_second_holder` |
| run_step replacement: `extra_env={}` in `run_project_step` | `test_run_step_executes_in_the_project_root_with_env_and_protected_runtime` |
| run_step replacement: log root `logs` instead of `logs/cmru` | the same test and `test_run_step_quiet_detail_goes_to_the_project_local_log_root` |
| run_step replacement: undeclared-step check disabled | `test_run_step_refuses_an_undeclared_step` |
| run_step replacement: unknown-project check disabled | `test_run_step_refuses_a_project_the_config_does_not_register` |

**Full suite (round 1, via `pt.py`, serial, flock/nice/ionice, PSI full avg60 0.00 before):** 3530 passed, 6 skipped, 0 failed;
`--cov=cmru --cov-branch` TOTAL 11862 stmts / 5120 branches, 0 missed, 100%.
