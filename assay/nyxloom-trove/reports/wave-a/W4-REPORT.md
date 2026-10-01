# W4 REPORT (B123): judge tests vs tooling tests, and the same-commit release-gate binding

Branch `wave-a-w4-test-split`, base `11ace522`, last code commit `730f2db7`. Oracles, deviations and the controller's gate list: `W4-LOG.md`. No `src/assay`, `analysis/**`, `run-gate.toml` or `gate/{python,distribution}/**` byte changed (`git diff --stat 11ace522 -- src analysis run-gate.toml gate/python gate/distribution` is empty). The registered gate was not run by the implementer (CD44).

## Final P1 table (paths after W4)

Tooling, moved whole with `git mv` (name kept):

| Now | Was |
|---|---|
| `gate/tests/test_b105_report_check.py` | `tests/test_b105_report_check.py` |
| `gate/tests/test_cgroup_parent.py` | `tests/test_cgroup_parent.py` |
| `gate/tests/test_gate_failure_diagnostics.py` | `tests/test_gate_failure_diagnostics.py` |
| `gate/tests/test_distribution_gate.py` | `tests/test_distribution_gate.py` |
| `gate/tests/test_distribution_build_release.py` | `tests/test_distribution_build_release.py` |
| `gate/tests/test_distribution_release_wheel.py` | `tests/test_distribution_release_wheel.py` |
| `gate/tests/test_dependency_purity.py` | `tests/test_dependency_purity.py` |
| `gate/tests/test_go_helper_is_packaged.py` | `tests/test_go_helper_is_packaged.py` |
| `gate/tests/test_self_hosting.py` | `tests/test_self_hosting.py` |
| `gate/tests/test_self_lane.py` | `tests/test_self_lane.py` (C4/CD12; W7 edits it here) |
| `gate/tests/test_standalone.py` | `tests/test_standalone.py` (not split) |
| `gate/tests/test_gate_qualify_dstdns_sql.py` | `tests/test_gate_qualify_dstdns_sql.py` (path rules only; W5 replaces it here) |
| `gate/tests/qualification/test_go_r1_real.py` | `tests/qualification/test_go_r1_real.py` |
| `gate/tests/qualification/test_javascript_real_vitest.py` | `tests/qualification/test_javascript_real_vitest.py` |
| `gate/tests/test_verdict_schema_wheel.py` (new: the 6 wheel tests) | split of `tests/test_verdict_schema_is_packaged.py` |

New support files: `gate/__init__.py`, `gate/tests/__init__.py`, `gate/tests/qualification/__init__.py` (empty), `gate/tests/support.py`, `gate/tests/conftest.py`.

Judge files that finished the layout (`git mv` into `tests/core/`, ROOT_PINNED gone): `test_runner_snapshot_selection`, `test_lane_schema_v2_locked_successors`, `test_verdict_v13_successors` (`parents[1]` -> `PROJECT_ROOT`), `test_b106_reuse_and_witness` (`Path(__file__).parent` -> `TESTS_ROOT`; generated-code string literals untouched), `test_verdict_conformance`, `test_errors`, and the kept split half `test_verdict_schema_is_packaged` (2 tests). `tests/` root now holds only `conftest.py` and `fixtures/` (plus subfolders `core/`, `adapters/`, `parsers/`).

Judge files added under `tests/` since `5bbd916e` (`git diff -M --diff-filter=A --name-only 5bbd916e -- tests/`), all judge, none imports `gate`, requests `standalone` or reads `tools/`, `gate/`, a wheel or a zipapp (checked by the new AST test and by grep): `tests/core/test_import_contracts.py` (W3, now with part 4), `tests/core/test_cli_analyze_seam.py` (W2), `tests/core/test_verdict_schema_is_packaged.py` (the kept split half). W6's per-component guard tests were not on this base.

Everything else under `tests/**/test_*.py` is judge (C2: the static source sweeps and the import-contract test stay in `tests/`).

## Heavy judge tests left (record for A-468(b), by name)
`test_cli_run` heavy cases, `test_b106_reuse_and_witness`, `test_runner_run_lane_r3`, `test_canary_python_pipeline`, `test_progress_phase_stream`, `test_lane_timeout_writes_a_verdict`, `test_environment_preflight`, `test_runner_result_report`, `git_repo` setup, the C2 sweep. R9 RC8 (caching the static sweeps) is a follow-up under B123/A-468(b), not done here.

## `src/assay` comments that name moved test paths (listed, not fixed; W10 updates them, CD43)
| Where | Names | Now |
|---|---|---|
| `src/assay/__init__.py:14` | `tests/test_dependency_purity.py` | `gate/tests/test_dependency_purity.py` |
| `src/assay/adapters/go.py:569` | `tests/test_standalone.py` | `gate/tests/test_standalone.py` |
| `src/assay/adapters/go_stmtpos.py:83` | `tests/test_go_helper_is_packaged.py` | `gate/tests/test_go_helper_is_packaged.py` |
| `src/assay/adjudication.py:50` | `tests/test_adjudication_registry.py` | `tests/core/test_adjudication_registry.py` |
| `src/assay/canary.py:190` | `tests/test_adapters_go_registration.py` | `tests/adapters/go/test_adapters_go_registration.py` |
| `src/assay/cli.py:572`, `:581` | `tests/qualification/`, `tests/test_cli_run.py` | `gate/tests/qualification/`, `tests/core/test_cli_run.py` |
| `src/assay/config.py:918` | `tests/test_config_snapshot_selection.py` | `tests/core/test_config_snapshot_selection.py` |
| `src/assay/provenance.py:206`, `:208` | `tests/test_standalone.py`, `tests/test_distribution_build_release.py` | `gate/tests/...` |
| `src/assay/result_reports/vitest_json.py:71` | `tests/test_untrusted_json_parse_sweep.py` | `tests/core/test_untrusted_json_parse_sweep.py` |
| `src/assay/runner.py:1337` | `tests/test_self_hosting.py` | `gate/tests/test_self_hosting.py` |
| `src/assay/verify.py:137`, `:2527` | `tests/test_errors.py` | `tests/core/test_errors.py` |
| `src/assay/verify.py:2498-2499` | `tests/test_runner_p23_cleanup_and_budget.py`, `tests/test_verify_layer_independence.py` | `tests/core/...` |
| `src/assay/vocabulary.py:25` | `tests/test_verdict_schema_is_packaged.py` | `tests/core/test_verdict_schema_is_packaged.py` (the vocabulary half) |
| `src/assay/vocabulary.py:294`, `:333` | `tests/test_config_statement_attribution_format.py`, `tests/test_adjudication_registry.py` | `tests/core/...` |

Outside `src/assay`, also forbidden to W4 and listed for their owners:

| Where | Names | Now | Owner |
|---|---|---|---|
| `gate/distribution/build_release.py:25` | `tests/test_distribution_build_release.py` | `gate/tests/test_distribution_build_release.py` | the next package allowed to touch `gate/distribution/` (W10 list) |
| `gate/python/qualify_dstdns_sql.py:47` | `tests/test_gate_qualify_dstdns_sql.py` | `gate/tests/test_gate_qualify_dstdns_sql.py` | W5 (replaces this harness) |

## Anything a later package must know
- W5 edits `gate/tests/test_gate_qualify_dstdns_sql.py` in place. W7 edits `gate/tests/test_self_lane.py` (the `snapshot_history == "full"` pin is kept) and `assay.toml`. W8 extends `tools/b105_report_check.py` (its CLI now has `--receipt-only` and `--tester-unified-receipt`, and the nine old flags are optional, checked by hand).
- New tooling tests import from `gate.tests.support` (never `from conftest import` for tooling helpers); the loader is by module name `assay_judge_conftest`.
- The docs-example test ids that carry a docs line number (`[CONSUMERS.md:1173]`) changed with the docs edit; nothing pins them.
- A gate run at a commit is bound to that exact commit: any later commit, docs included, makes the receipt not match the full lane's `HEAD`.
