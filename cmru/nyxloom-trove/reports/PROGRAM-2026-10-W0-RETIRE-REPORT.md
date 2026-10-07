# PROGRAM-2026-10 W0-RETIRE report

Branch `cmru-w0-retire` (from `cmru-wave-2026-10` at `baf0ea295`). Implementation commit `f6fef1d6b`.

## 1. Retirement of cmru-agent / cmru-controller (O5, memo section 5)

Deleted: `cmru/src/cmru/agent/` (9 files), `cmru/src/cmru/controller/` (4 files), `cmru/packaging/cmru-agent.service`,
the two console scripts in `cmru/pyproject.toml`, and the 8 dedicated test files named in the memo, plus
`tests/test_abstract_contracts.py` (it tested only agent ABCs, so it was also fully agent). Net: about 5,500 lines of source and tests.

Shared helpers: none. `grep` for `cmru.agent` / `cmru.controller` in `src/` found no importer outside the two packages
(`manifest.py`, `release.py`, `hosts/`, `getpy.py` do not use them). Nothing was relocated.

Mixed test files pruned (agent/controller cases only; every other assertion kept): `test_cli_adversarial_contracts`,
`test_cli_config_deep_adversarial`, `test_cli_dispatch`, `test_cli_extended_semantics`, `test_cli_final_gaps_adversarial`,
`test_cli_spec_inventory` (now one family), `test_core_residuals`, `test_execution_boundaries_adversarial`,
`test_final_residual_contracts`, `test_last_operational_sweep`, `test_noncli_final_adversarial`,
`test_operational_coverage_adversarial`, `test_operational_last_contracts`, `test_operational_residual_contracts`,
`test_release_transaction_100_adversarial`, `test_runtime_100_adversarial`, `test_runtime_remaining_adversarial`,
`test_semantic_boundary_contracts`, `test_small_noncli_residuals`, `test_small_operational_final`,
`test_small_operational_residuals`, `test_smaller_modules_adversarial`, `test_version_transaction_remaining`.
Two non-agent assertions that lived inside agent-named tests were kept as their own tests
(`manifest._validate_images` shape check in `test_core_residuals` and `test_small_operational_final`).
`test_cli_dispatch::test_helper_nested_help_and_errors_start_with_the_headline` used agent/controller as its example
helper CLIs; it now uses `handlers.main` (`wheel-build --help`, `wheel-validate`), so the headline contract stays covered.

Docs: `cmru/docs/SPEC.md` (S-CLI.3, S-CLI.7, S-CLI.9 intro, common/builtins/grammar tables, audit rows, invocation roles,
closing paragraphs), `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `/docs/DEFAULTS-AUDIT.md` (whole
"Separate control-plane configuration" section), `libraries/cli-extended/SPEC.md:806`. RETIRED banner added at the top of
`docs/spec-cmru-agent-controller.md` (file kept). `ciu/docs/SPEC.md` S14: now says push over SSH is the only model, pull model
retired 2026-10-05. `ciu/docs/v8-dstdns-demo/ciu.toml`: removed the `cmru_node` and `cmru_controller` policies.
Backlog: KI-32 and KI-44 (the `controller status --plan` entry) marked obsolete/retired; KI-51's agent/controller
registries, `except Exception` sites and dry-run copies struck (KI-49/50 untouched).

Left alone on purpose (outside the stated scope; for the controller to decide):
- `ciu/docs/v8-dstdns-demo/ciu.toml`: `cmru_viewer` policy, `[registry.consul.deploy] kv_root = "cmru/landscapes"` and
  `auto_config`, and line 328 `consul_cmru_controller_token = "consul/cmru/controller/token"` (a hook mint that
  `ciu/docs/v8-dstdns-demo/README.md:121` already calls unconsumed). Also two comments mentioning `cmru_node` in the demo's consul-server
  `ciu.stack.toml` / `ciu.compose.yml.j2`.
- `docs/reviews/cli-extended-adoption-review.md` body still describes `cmru-agent`/`cmru-controller` (historical, banner added in CLI-D5).
- `get.py.tmpl` untouched (content out of scope). `ciu/tests/tests/test_ciu_host_enroll.py` reads the template from the
  package path first and still has a harmless fallback to the deleted `cmru/templates/get.py.tmpl`.

### Hazard found during the work (worth a backlog entry)
The venv has an editable install (`__editable___cmru_*_finder.py`) whose meta-path finder resolves `cmru.agent` from the MAIN
checkout even when the worktree's `src/cmru/agent` is deleted. A plain `pytest tests` run therefore stayed green with
every deleted import still resolving: 21 test files still imported `cmru.agent`/`cmru.controller`. I found this by running pytest through a
wrapper that drops the editable finder from `sys.meta_path` (`scratchpad/pt.py`) and used that wrapper for all my runs.
Reviewer: run the same way (or from a venv without the editable `cmru`), otherwise deleted-module imports are masked.
`tests/conftest.py` only prepends `src` to `sys.path`; it does not stop the finder.

### dstdns notice (draft; NOT sent, nothing written into dstdns)
> cmru retired `cmru-agent` and `cmru-controller` on 2026-10-05 (vbpub@<hash after merge>; operator decision O5,
> consistent with dstdns D-097). Leftovers on the dstdns side: (1) the `cmru/landscapes` Consul ACL policies
> (`cmru_node`, `cmru_controller`, and the viewer/kv_root parts) in the landscape/ACL bootstrap are now dead;
> (2) `scripts/bootstrap.py:6` docstring still names "the resident cmru-agent"; (3)
> `infra/consul-server/post_compose_consul.py:644` ("the pre-v3 cmru control plane (Seam 5), which spec section 1.4
> retires with cmru-agent") can drop that clause. `vbpub/docs/spec-cmru-agent-controller.md` is kept with a RETIRED
> banner so D-097 links still resolve. Pull-based convergence is gone; ciu push over SSH (S14, v8.2 S17) is the only model.

## 2. CLI findings

| Finding | Change | Test | Plant / revert result |
|---|---|---|---|
| CLI-04 | `default_projects` no longer required (`config.py`), accepted with `[WARN] orchestration.default_projects is ignored and will be removed ...` on stderr, no longer validated, field removed from `OrchestrationConfig`, removed from both shipped templates and the SPEC/CONSUMERS examples. Five target help texts now say "omitted: the current project, or every orchestrated project at the estate root". `estate_scope` param deleted from `select_target_names` and its 6 callers; `step_project_order` handling deleted from `_orchestrate` (both branches). | `tests/test_default_projects_deprecation.py` (9 tests) | Run against the pre-change source tree (`git archive baf0ea295` copied to the scratchpad, new test files copied in): 9 of 9 fail there. Revert evidence is therefore by running on the old tree, not by hand-planting each hunk. |
| CLI-05 | `cleanup TARGET --remove-assets AGE` exits 2 with "--remove-assets applies estate-wide [cleanup] policy; omit the target" before any API call; help text and SPEC row updated | `test_cleanup_dry_run_yes.py::test_remove_assets_refuses_a_project_target_before_touching_anything` | Replaced the guard condition by `False`: that test fails (the run listed GHCR deletions); restored |
| CLI-T1 | New `tests/test_cleanup_dry_run_yes.py`: for each of the 5 cleanup modes (policy, `--remove-assets`, `--delete-unmanaged-release-tag`, `--delete-build-output`, `--discard-build-worktree`) run the real `cmru cleanup ... --dry-run --yes` with a REAL `CleanupPlan` subclass that records itself and raises if `apply()` is called; only read-side GitHub/Git/retained-output boundaries are faked, any non-GET HTTP or non-dry transaction delete is a hard failure. Asserts exit 0 AND the preview captured actions. A control test proves the spy is live without `--dry-run`. | 7 tests | Replaced `if vargs.dry_run:` by `if False:`: 5 of the 5 mode tests fail; restored |
| CLI-14 | `--repack` removed from `oci-image-build`/`oci-image-push`; `_reject_experimental_repack`/`_OCI_REPACK_DISABLED` deleted; SPEC rows/text and CHANGES updated; KI-02 stays in the backlog (option returns with its fix) | `test_boundary_contracts.py::test_repack_is_not_part_of_the_handler_grammar`, `test_cli_extended_semantics` and `test_cli_review_decisions` assert `unrecognized arguments: --repack` and no Docker call | old code accepted the option and printed "path is disabled", so the new assertions fail there (the helper was also asserted absent) |
| CLI-18 | `templates/cmru.toml.tmpl` gets `[runtime] kind = "none"`; a second defect the new test exposed: `templates/cmru.orchestration.toml.tmpl` lacked central `[github]`/`[targets]` so the "ready-to-copy" pair could not load, fixed, and the project template now says to delete its own `[github]`/`[targets]` in a monorepo. Duplicate `templates/get.py.tmpl` deleted (readers: `getpy.py` uses the package resource; ciu test reads the package path first; no other reader). | `tests/test_shipped_templates_load.py` (7 tests, including an inventory check that fails when a template is added without coverage and `cmru init`-rendered project/orchestration files) | On the pre-change tree 5 of the 7 fail (standalone template, pair, inventory, single-copy, deprecated-key) |
| CLI-D3 | Removed all agent/controller rows. `resolve` row: dropped "distinguishable", states the shape varies (CLI-13 open). The `status` "read-only" row is NOT edited: it belongs with CLI-01, which W0-REL owns together with the release/status block | n/a (doc) | n/a |
| CLI-D4 | Second `S-CLI.4` renumbered `S-CLI.11 — Retired names` (no other reference to it) | `test_cli_spec_inventory` unchanged | n/a |
| CLI-D5 | Status table in `docs/reviews/cli-extended-adoption-review.md` annotated historical, column headed "as of 4de03ca5"; KI-07 corrected: the real option is `--discard-logs-on-release` (retained by default), `--retain-logs-on-release` never existed | n/a (doc) | n/a |

Decision notes:
- The `load_config` 10-tuple keeps its shape (55 test files and every caller unpack it). The `default_projects` slot is now a reserved
  always-empty list and the `step_project_order` slot is still `{}`; callers name them `_default_projects` / `_step_project_order`. Removing the slots is
  part of the planned `resolve_target` redesign (cli-surface section 13), not this package.
- `tests/test_cli_top_level_dispatch_adversarial.py` and `test_cli_extended_semantics.py` lost their `Unknown project in step_project_order`
  subtests (they exercised only the deleted branch).

## 3. Test and gate results

Commits: `f6fef1d6b` (main change), `059241d83` (draft report), `68884bb5c` (restored non-agent tests). Gates ran on `68884bb5c`; the commit
that carries this final report only edits this file.

Tests are run through `scratchpad/pt.py` (drops the editable-install finder, see the hazard note above), under the shared test lock,
memory pressure checked first.

| Run | Result |
|---|---|
| Baseline `cmru-wave-2026-10` (`baf0ea295`), full suite, with coverage | 2 failed, 2886 passed, 10 skipped; coverage 99.960%, 0 missing lines, 7 missing branches |
| This branch, full suite, no `--maxfail`, with coverage | **2 failed, 2696 passed, 10 skipped**; 99.956%, 0 missing lines, the same 7 missing branches (`cli.py` 5, `transaction.py` 2) |
| Failing tests (both pre-existing, KI-54/REL-01, W0-REL's) | `test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run`, `...::test_release_rejects_an_internal_snapshot_spanning_multiple_git_families`. Nothing else is red. |
| `coverage` lane (`run-gate.py --worktree <wt> coverage`) | **FAIL**, exit 1: `1 failed, 832 passed, 2 skipped` (the lane has `--maxfail=1`, so it stops at the first red; the failure is `test_release_rejects_internal_handoff_on_dry_run`, the REL-01 test). Because of `--maxfail=1` the 100% threshold was not evaluated by the lane; my own coverage run above is the evidence for coverage. |
| `canary` lane | **FAIL**, exit 1, and NOT caused by this package: the lane's known-good control run fails `tests/test_cli_build_output_semantics.py::test_tls_edge_retained_tarball_inventory_contains_the_publisher_version_file` because `tools/project_fixture.py` copies `topos/cmru.toml` and `nyxloom/cmru.toml` into the disposable fixture but not `tls-edge/cmru.toml` or `tls-edge/scripts/build-artifact.sh`, which that test reads. The same lane on the untouched integration branch (clean `cmru-wave-2026-10`, `baf0ea295`) fails identically (log kept at `/tmp/run-gate/lanes/canary/3b8e8e0a65551e1c8afc6037d8be2a2b.log`). I did not fix it (`tools/project_fixture.py` is not in this package's scope; the fix is adding the two tls-edge paths to `ESTATE_CONFIG_FIXTURES`). **The controller needs to route this to a package; until it is fixed no package can get a green canary.** |

Plant/revert summary: CLI-T1 (5 of 5 mode tests red with the early return removed), CLI-05 (guard test red with the guard disabled), the other new tests
red on the pre-change tree. Each was restored and re-run green.

Coverage note: deleting the 8 dedicated files wholesale first dropped coverage to 99.70% (21 uncovered lines in `release.py`,
`tester_gate.py`, `version.py`, `resolve.py`) because `test_release_agent_exhaustive_adversarial.py`,
`test_controller_cli_final_adversarial.py` and `test_noncli_release_rollout_branches.py` also held non-agent tests. They are restored (via `git checkout` +
`git mv`) as `test_release_exhaustive_adversarial.py`, `test_cleanup_dispatch_modes.py`, `test_release_publish_existing.py`,
minus the agent/controller functions. That brought coverage back to the baseline's exact residue.

## 4. Deviations and notes
- Editing rule: every file change was made with Edit/Write or `git rm`/`git checkout`/`git mv`. No sed, redirects or write-scripts touched the repo
  (the `sed -i ... /dev/null` in one command was a typo that failed harmlessly; a stray no-op Edit with a junk string also errored harmlessly).
- Version bump: removing two console scripts is a breaking change (memo R3 says major); CHANGES.md carries a `**Breaking:**` line under
  `## [Unreleased]`, the bump itself is the controller's.
- CLI-D3's `status` "read-only" row is deliberately left to W0-REL (CLI-01 owns the status block).
- The `load_config` tuple keeps all 10 slots (see CLI-04 notes).

## 5. Review fixes (commit `531e48659`)
- **Blocker, double warning:** `default_projects` removed from the estate `cmru.orchestration.toml` and `cmru.orchestration.sample.toml`;
  `config.py` now warns once per config path (module-level `_DEFAULT_PROJECTS_WARNED`). Test
  `test_the_deprecation_warning_is_printed_once_per_config_path` loads the same config twice and expects one warning; with the guard
  condition removed it fails (plant/revert done, restored).
- **`--repack` docs:** `cmru/README.md` and `cmru/docs/CONSUMERS.md` now say the option was removed while KI-02 is open and returns with the fix.
- **Stale template path:** `docs/spec-minisign-bundle-signing.md` (2), `docs/spec-cmru-installer-v2.md` (2) and
  `modern-debian-tools-python-debug/templates/README.md` now point at `cmru/src/cmru/templates/get.py.tmpl`.
- **Demo Consul leftovers:** removed the `cmru_node` comment in `consul-server/ciu.stack.toml`, the `consul_cmru_controller_token` secret
  in `ciu.toml` and the README bullet about its hook mint (nothing in the demo consumed it; the hook script is dstdns-side, so the dstdns
  notice should also mention it mints that token). Kept: `cmru_viewer` policy and the `consul_cmru_viewer_token` secret (consumed by
  `infra-global/reverse-proxy/ciu.stack.toml:107`), `consul_cmru_bootstrap_bearer_token`, `kv_root` and `auto_config` (the viewer policy and
  the consul-server compose still use them).
- **Help test:** `test_rendered_help_does_not_promise_an_estate_default` now renders `--help` for run, build, status, resolve, get-py,
  standards and tool-deps and asserts on the output (all 7 show the new wording).
- **Results:** full suite via `pt.py`, no `--maxfail`: 2 failed (the two KI-54 tests), 2699 passed, 10 skipped. `coverage` lane FAIL: only those
  two KI-54 tests (2905 passed). `canary` lane FAIL: only the tls-edge fixture test, as before (W0-GATE).
- Not done, per scope: KI-49/50 untouched; `get.py.tmpl` content untouched; the leftover demo Consul items listed in section 1.
