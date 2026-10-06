# PROGRAM-2026-10 W1-CIU-ENROLL report

Branch `cmru-w1-enroll` (from `cmru-wave-2026-10`; integration branch merged before the final commit).

## Delivered
- **Mechanism (cmru).** `[project.installer] extensions = [...]` (config.py `_parse_installer_extensions`: list of
  non-empty, project-relative, no `..`, `.py`, unique paths; exit 2). `getpy.py`: `_read_extensions` (exists, resolves
  inside the project dir, symlink escape refused), `_inline_extensions`, `_check_extension` (checks a-e via `ast`),
  `ExtensionError` (CLI exit 2). Template: `_EXTENSIONS`, `EXTENSION_API`, `# @@EXTENSIONS@@` marker between the last
  `do_*` and `check_prerequisites`; `main()` merges extension commands, duplicate or parser-less command = `fatal`
  EXIT_CONFIG, dispatch `handler(args, token)`. Banner `# --- extension: <rel> sha256=<raw file bytes> ---`.
  Enroll fully removed from the template (also unused `grp`/`pwd`/`_glob` imports and the usage/epilog text).
- **Design points beyond the brief.** `[[VARNAME]]` placeholders are substituted over the whole result, so the verbatim
  fragment may keep `[[PROJECT_NAME]]` (checks run on the substituted text; the sha covers raw bytes). Fragment imports are
  exempt from fragment-to-fragment collision (each carries its own), but an import rebinding a template name collides.
  With no extensions a marker-less custom template is still accepted (existing test relies on it); with extensions the
  marker must occur exactly once. argparse >= 3.11 raises `ValueError` (not `ArgumentError`) for a duplicate subparser;
  both are caught.
- **Move (ciu).** `ciu/installer/enroll.py` (verbatim code + `_register_enroll`, same flags/help). `ciu/pyproject.toml`
  needs no change: package discovery is `where = ["src", ...]`, so `ciu/installer/` is not shipped. `ciu/cmru.toml`
  `extensions = ["installer/enroll.py"]`; tls-edge none. `ciu/get.py` and `tls-edge/get.py` re-rendered with the branch cmru
  (tls-edge: no `enroll`/`authorized_keys`; keeps `["python3","docker"]` and its `preserve`). Only intended difference in
  ciu/get.py outside the move: enroll text gone from the module docstring/epilog, extension block added, unused imports dropped.
- **Tests moved.** cmru `test_installer.py` enroll block -> `ciu/tests/tests/test_getpy_enroll.py` (runs against the committed
  `ciu/get.py`; env var `CMRU_ENROLL_REQUIRED` -> `CIU_ENROLL_REQUIRED`, fixture image/container names `ciu-enroll-*`;
  cmru's `tester_gate` imported optionally). Two tests changed shape: the tls-edge "renders enroll" test became the ciu
  committed-installer parser test, and the `cmru get-py` render test now renders ciu. Drift guard
  (`test_ciu_host_enroll.py::TestRenderedInstaller::test_render_is_byte_identical...`) already existed; updated for
  extensions and skips against a cmru without them.
- **Gate lane moved (consequence not named in the brief).** cmru's `[lanes.enroll]` ran `TestEnrollAgainstRealSystem` in
  `test_installer.py`; with the tests gone it would collect nothing (exit 5) and fail `gate`. Removed it from
  `cmru/run-gate.toml`, `tools/run_release_gate.py`, `test_release_gate.py`, `test_docs_config_examples.py`, SPEC S16.5/
  S16.x, README, CONSUMERS, DESIGN-GUIDE, RELEASE-TRANSACTIONS; added `[lanes.enroll]` to `ciu/run-gate.toml`
  (bare-host, `CIU_ENROLL_REQUIRED=1`). **Not run** (rules forbid the enroll lane; container oracles skipped here: no
  `CMRU_TESTER_CGROUP_PROBE_IMAGE`). The moved container tests are therefore unexercised on this branch; the code they test
  is byte-for-byte the former template code.
- **Backlogs.** cmru KI-49/KI-50 -> "moved" stubs; full text appended to ciu CIU-123 (KI-49) and CIU-122 (KI-50) with
  evidence; CIU-130 filed (`ciu host upgrade`, v8.2, O9, refs redesign R1/R3/R8).
- **CLI-D2.** `--project` callers switched to positional in ciu/README.md, ciu/docs/README.md, docs/VERSIONING.md,
  docs/ciu-vs-cmru.md, docs/release-plan.md, ciu CIU-HOST-ENROLLMENT-PROPOSAL.md, tls-edge/scripts/{release,build-artifact}.sh
  (the nonexistent `./cmru.release.sh --project ciu` became `cmru release ciu`). Left as historical records: dated
  plan-*/spec-* docs, nyxloom-trove reports/archives, the cmru KI-24 backlog text, CIU-V8 handoff/review docs.
- **Docs.** SPEC S6.14 (+ config sample), CONSUMERS "Installer extensions", README, ciu SPEC S14.7b.

## Final `EXTENSION_API`
`("EXIT_CONFIG", "EXIT_FAIL", "EXIT_PREREQ", "_EXTENSIONS", "_c", "_current_version", "_root_dir", "do_install", "fatal", "hr", "info", "ok", "warn")`
(verified with `ast`: exactly the template names ciu's fragment loads; `EXIT_FAIL` is also fatal's default).

## Findings / tests
New `cmru/tests/test_installer_extensions.py` (52 tests, fixtures in `tests/fixtures/ext_hello.py`, `ext_second.py`): 0/1/2
extensions, determinism, banner sha, each refusal a-e plus collisions/shadowing/decorators/lambdas, config path
validation, symlink escape, runtime duplicate refusal (3 variants), dispatch with token, `--help`, no-enroll/no-authorized_keys
without extensions, real tls-edge/ciu renders equal the committed files.

## Plant/revert (all reverted via `git checkout -- src`, clean tree confirmed)
| Plant | Failing tests (example) |
|---|---|
| (a) parse error reports line 0 | test_a_syntax_error_names_fragment_and_line |
| (b) template collision disabled | test_b_collision_with_a_template_name/assignment, import_rebinding |
| (c) API check disabled | test_c_template_name_outside_the_api_is_refused, ..._default_args_decorators... |
| (d) stdlib check disabled | test_d_non_stdlib_import_is_refused |
| (e) registration check disabled | test_e_missing_registration_is_refused, test_e_registration_must_be_top_level |
| marker kept with zero extensions | test_zero_extensions_marker_is_replaced_by_nothing |
| banner digest altered | test_one_extension_..._sha256_banner, test_banner_sha_tracks... |
| runtime duplicate-command check off | test_handler_name_duplicating_core_without_a_parser_clash_is_refused |
| token dropped on dispatch | test_extension_command_is_dispatched_with_args_and_token |
| config `..` check off | test_invalid_paths_are_refused_with_exit_2[../, a/../..] |
The added branch-coverage tests (lambda/decorator/class/varargs/`project_root=None`) were found missing by the first
coverage-lane run (99.89%).

## Results
- Full cmru suite via pt.py (no maxfail, test lock): 2871 passed, 2 skipped (before the last added tests; the extensions file alone: 52 passed).
- ciu: `test_getpy_enroll.py` + `test_ciu_host_enroll.py`: 172 passed, 8 skipped (container oracles).
- Gate lanes, read separately: `coverage` verdict PASS (after the added tests; first run FAIL 99.89%), `canary` verdict PASS.
- Not run: mutation, gate, enroll lanes (rules); full ciu suite (only the touched files).

## Deviations / notes
- Rendering `ciu/get.py` and `tls-edge/get.py` used `cmru get-py ... --output` (cmru's own CLI writing the file) through a
  scratchpad launcher that strips the editable finder; plant reverts used `git checkout -- src`, not Edit. No heredoc/sed
  edits to repo files.
- cmru `CHANGES.md` not edited (`[Unreleased]` is W0-REL's controller-folded body). Lines to fold: **Added** `[project.installer]
  extensions` (project-owned get.py fragments; `EXTENSION_API` stability contract); **Removed** `get.py enroll` and its
  `enroll` gate lane from cmru (now ciu's; KI-49/KI-50 moved to CIU-122/123); **Changed** tls-edge's committed `get.py`
  re-rendered (loses enroll).
- Open question for the controller: ciu's own release/assay flow does not run the new ciu `enroll` lane automatically.
