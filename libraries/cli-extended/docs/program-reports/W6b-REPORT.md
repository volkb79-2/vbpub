# W6b report: multi-CLI plugin fix and Netcup-adoption follow-ups

## 1. Plugin with several reviewed CLIs
- `assert_cli_case_tests(..., foreign_case_ids=())` skips markers of other
  catalogs. The plugin loads all catalogs, refuses a case id in two catalogs
  (naming both CLI ids), checks each catalog with the union of the others' ids,
  and raises one deduplicated `UsageError`.
- Tests (`tests/test_w6_testing.py`): `test_two_reviewed_clis_with_their_own_markers_pass`,
  `test_marker_unknown_to_every_catalog_is_reported_once`,
  `test_case_id_in_two_catalogs_is_refused_naming_both_clis`,
  `test_errors_from_every_catalog_appear_in_one_failure`,
  `test_foreign_case_ids_skip_only_those_markers` (unit).
- SPEC section 13 rule 12 and the CONSUMERS plugin section updated.

## 2. Audit `synopsis-overrides`
Redundant overrides alone are the evidence; the summary appends
", N other override(s) need a justification" when others exist; all-justified
still yields `manual`. Tests in `tests/test_audit.py` updated and extended.

## 3. Docs
- `### A project with several CLIs` (real key is `[[clis]]`, not `[[cli]]`).
- Link at CONSUMERS.md fixed to `cli-review-monitor-task.toml`; `tests/test_docs.py`
  allows only that path when missing via `PENDING_NETCUP_RENAME` (controller
  removes at the Netcup merge).
- `### Running the cli-extended CLI from a source checkout`. I ran the recipe in
  a scratch venv: `python3 -m venv`, `pip install --no-deps -e libraries/cli-extended`,
  then `cli-extended --version` printed a dev version; with
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=0.2.0` it printed
  `cli-extended 0.2.0`.

## 4. BACKLOG
CLI-EXT-17 and CLI-EXT-18 added (planned, source W9a Netcup adoption); heading
range updated. No detail sections exist for existing rows, so none added.

## Gate
See the verdict line in the hand-back.
