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
- Open question for the controller: ciu's own release/assay flow does not run the new ciu `enroll` lane automatically (now CIU-131).

## Review fixes (controller rulings, ACCEPT-conditional)
1. **Checker gaps (`getpy.py`).** (b) now checks every module-scope binding (`_bindings`: bindings inside top-level
   if/try/for/with/match, `del` targets, `except as`, match captures incl. `*rest`/`**rest`, imports); star imports are
   refused; `global`/`nonlocal` naming a template name is refused (a fragment's own global is fine); (c) walks argument
   (incl. `*args`/`**kwargs`/kw-only) and return annotations. SPEC S6.14 and CONSUMERS state these are a contract/lint
   guard over repo-owned fragments, not a security boundary. Tests: `test_b_module_scope_rebinding_in_blocks_is_refused`
   (9 cases), `test_b_star_import_is_refused`, `test_b_global_*`/`nonlocal_*`, `test_c_annotations_are_checked` (5 cases) + positive
   cases. Plant/revert (all reverted with Edit): bindings loop disabled (9+ failures), global/nonlocal loop disabled,
   star-import check disabled, annotation walk disabled, MatchAs/MatchStar and MatchMapping branches disabled (3 failures);
   every plant failed its tests.
2. **ciu gate / drift guard.** The only ciu lane that runs `test_getpy_enroll.py` and `test_ciu_host_enroll.py` is the assay lane
   `ciu` (`ciu/assay.toml`; R0-R3 including R2 mutation, budget 8h): **not run** (heavy mutation lane). Its `env.PYTHONPATH` is now
   `src:../cmru/src:../libraries/cli-extended/src:../libraries/worktree/src` (coverage is still `--cov=ciu`, `source_roots=["src"]`,
   so cmru is not measured). The drift test and the render test now `pytest.fail` (clear message) instead of `importorskip`/skip,
   including the template-unreachable / predates-extensions cases; verified red with `PYTHONPATH=src` only.
   `ciu/run-gate.toml [lanes.enroll]` is separate (container oracles, not the R2 lane).
3. **CIU-131 filed** (ciu backlog): O2/O3 real-system oracles are in no automated release gate; fix direction (tester-gate
   `--enable-docker` step or mandatory manual pre-release run), fixture cost (`ciu-enroll-fixture:local`, apt-get, non-privileged
   containers on the gates slice).
4. **Help text.** Loss accepted in `get.py`; the pre-move example (plus the `--version` pin) added to `ciu/docs/SPEC.md` S14.7b.
5. Trailing blank line removed from `cmru/tests/test_installer.py`.
6. `tls-edge/README.md:417` now `cmru release tls-edge --set-version 0.2.0`.

## Review fix 2 (release-gate environment blocker)
- **How ciu `src` reached the path before:** not via the runner. `ciu/run-ciu-tests.py` set no env; the test modules do
  `sys.path.insert(0, ".../src")` themselves (e.g. `test_ciu_documentation_contract.py`), and nothing put cmru on the path, so the
  image's older installed cmru was imported by the drift/render tests.
- **`ciu/run-ciu-tests.py`:** new `child_env()`; the pytest child's `PYTHONPATH` is ciu `src`, `../cmru/src`,
  `../libraries/cli-extended/src`, `../libraries/worktree/src` (resolved from `ROOT = script dir`, not cwd), ambient PYTHONPATH
  kept after them; same set as `assay.toml`. Pinned by `test_release_gate_child_env_uses_repo_sources_and_matches_the_assay_lane`
  (also compares with `assay.toml`'s env, run from a foreign cwd).
- **Mount (read from `tester_gate.py:778`):** `cmru tester-gate` bind-mounts the WHOLE worktree root at `/worktree` and sets
  the workdir to `/worktree/<--cwd>`, so `../cmru` and `../libraries` exist in the container.
- **R2 mutation snapshot:** `isolation.snapshot_selection = "repository-minus-unsafe-symlinks"` materialises the whole commit minus
  the three declared topos symlinks (`assay/config.py:228-234`, `isolation.py:1809`), so the siblings are present; no test exclusion
  from the mutation selection is needed. (Not run: the R2 lane itself.)
- **Probe (found a second environmental dependency):** first probe run failed `test_get_py_cli_render_carries_enroll`: the
  orchestration config interpolates `${CGROUP_PARENT_DEV_GATES}`, unset in my bare container. The test now pins it with
  `monkeypatch` (the render does not use the slice). Second probe, same method: one throwaway
  `docker run --rm --name cmru-w1-probe-<random> tester-unified:local`, worktree bind-mounted `readonly`, `--cgroup-parent
  dev-gates.slice --memory 2g --memory-swap 2g --cpus 1 --pids-limit 512`, PSI checked, gate lock held, command
  `python run-ciu-tests.py --no-cov --cov-fail-under=0 -p no:cacheprovider -n0 -k "byte_identical or carries_enroll or child_env"`:
  **12 passed, 4200 deselected, rc 0** (includes `test_render_is_byte_identical_to_the_committed_file`,
  `test_get_py_cli_render_carries_enroll`, and the env-pin test); container confirmed gone (`--rm`, exact name).
  Not exercised there: the container tests (skip without docker) and the coverage floor.
