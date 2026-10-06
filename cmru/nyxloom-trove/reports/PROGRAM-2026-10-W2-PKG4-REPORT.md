# W2-PKG4 REPORT: packaging and distribution (cli-extended as a real wheel dependency)

Branch `cmru-w2-pkg4`, base `287813c9d` (PKG-0 merged). Implementer: fresh Sonnet. All repository edits were made with
the Edit/Write tools (plus `git mv`, `git checkout <file>` to revert plants, `rmdir` of the emptied `.claude` dirs); no
sed/heredoc/script wrote a repo file. No docker build, no network, no `.env` read.

## Findings closed (evidence = what I ran)

| Item | Change | Evidence |
| --- | --- | --- |
| Scope 1: `pyproject.toml` | `dependencies = ["cli-extended>=0.2.0"]` with the CX-D1/floor reason; `where` and `package-dir` no longer map `cli_extended` (`worktree` kept); skills in package data | real wheel built offline (`tools/installed_wheel_smoke.py`, `CMRU_SMOKE_NO_BUILD_ISOLATION=1 PIP_NO_INDEX=1`): passed; `check_wheel_contents` found no `cli_extended/` member, `Requires-Dist: cli-extended>=0.2.0`, and `cmru/skills/cmru-cli/SKILL.md` |
| Skill move | `git mv cmru/.claude/skills/cmru-cli -> cmru/src/cmru/skills/cmru-cli`; `.claude/` dir removed | `test_skills_ship_as_package_data_and_the_checkout_skill_dir_is_gone`; wheel check above |
| Scope 2 / D8 | `tester-unified/gen-requirements.py`: `admit()` SKIPS every `cli-extended` line (deps, extras, build-requires), still refuses all other internal names; new `tester-unified/fetch-cli-extended.py` (pointer or pinned asset, sha256 mandatory, https+prefix check, floor check, nothing written before the digest matches); Dockerfile fetches, installs the wheel `--no-index --no-deps` BEFORE building/installing cmru, no longer COPYs/builds `libraries/cli-extended` (COPY `libraries/worktree/` only), final assertion block updated | `tests/test_tester_unified_image.py` (50 -> 65 tests): generator skip, still-refuse, Dockerfile pip-command parse (the only pip command naming cli-extended is the offline `--no-deps` release-wheel install; every index-capable pip takes only the generator output), order (fetch < release install < cmru build < cmru install), floor equality with pyproject, fetcher behaviour, every COPY source admitted by `.dockerignore`. Image NOT built (static only). |
| `.dockerignore` (repo root; there is no `tester-unified/.dockerignore`) | dropped the cli-extended pyproject/README whitelist, added `fetch-cli-extended.py` | `test_dockerignore_*`, `test_every_dockerfile_copy_source_is_admitted_by_the_dockerignore` |
| Scope 3: installer order / D9 | `get.py.tmpl` re-checked, UNCHANGED: W1 already hoists cli-extended first (`_wheel_order`), `--isolated --no-index --find-links --require-hashes` lock, `_verify_wheel_sha256`/`_hex_digest` fail hard on a missing sha256 or missing entry. So no `ciu/get.py`/`tls-edge/get.py` re-render. | new `TestCliExtendedInstallOrder` in `test_installer.py` (real venv + offline pip): cmru-shaped project with the tool declared BEFORE cli-extended installs cli-extended first, hash-locked, importable; pip argv has `--no-index/--find-links/--require-hashes`; missing/empty/None/wrong sha256 for the cli-extended wheel exits 1 and leaves nothing installed |
| Scope 4: `build-initial-standalone.sh` | reads the floor from cmru's pyproject; takes `CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL` + `_SHA256` (offline) or the digest-verifying fetcher; unpacks the verified wheel with `python -m zipfile` into a private temp dir that goes on `PYTHONPATH` (nothing is installed into `$CMRU_BOOTSTRAP_PYTHON`), removed on exit; keeps the verified wheel in `dist/`; print-out installs cli-extended first, `--no-index --no-deps` | `tests/test_installed_wheel_subprocess.py` (11 tests): order of roots, unpacked package visible to the launched handlers, stage cleaned up, wrong/missing/malformed digest exits 2 before any launch, fetcher delegation with `--min-version 0.2.0`, fetcher failure exits 2 |
| Scope 5: gate config | `run-gate.toml` (coverage, mutation, canary), `assay.toml`, `cmru.toml [env]`, `tools/run_release_gate.py`, `tools/project_fixture.py`: no `libraries/cli-extended/src`; every PYTHONPATH is `src:../libraries/worktree/src`; fixtures never copy cli-extended | `test_every_gate_pythonpath_keeps_the_worktree_root_only`, `test_no_gate_or_bootstrap_config_puts_cli_extended_source_on_a_path`, `test_project_fixture` |
| Scope 6/7: CLI-20 + README | templates already emit `cmru handler` (earlier waves; asserted by `test_docs_config_examples`); README Install now documents the cli-extended dependency, the offline install forms, the bootstrap env vars, and that `python -m cmru.handlers` is bootstrap-only. Verbs referenced: only `cmru handler <verb>`, `cmru get-py`, `cmru tester-gate` (no PKG-1/2 renamed verb is used). | `test_docs_config_examples` green |
| Scope 8: subprocess tests | the two tests (`test_removed_module_console_dispatch_alias_refuses_version`, `test_source_module_invocation_works_from_the_cmru_project_directory`) moved to `tests/test_installed_wheel_subprocess.py`, now `invoke_module(..., home=tmp_path, pythonpath=[cmru src, worktree src])` (installed cli_extended; AC-23) | green; a third test asserts the child's `cli_extended` is not under `libraries/cli-extended` |
| `installed_wheel_smoke.py` | builds the cli-extended wheel from the checkout into the wheelhouse as the released-asset stand-in, runs `check_wheel_contents`, installs cmru with `--no-index --find-links` (resolves the dependency offline), asserts `cli_extended` imports from the venv | ran locally with no network; passed |

## Tests

- Full cmru suite via `pt.py` (serial, test.lock, nice/ionice, PSI full avg60 < 1): `3295 passed, 2 skipped, 1 xfailed` (run with `-x` at the then-HEAD tree equal to commit `fd2a60a79` content; no code changed after it except plants that were reverted).
- The xfail is `test_run_child_never_puts_a_candidate_cli_extended_source_root_on_pythonpath` (non-strict), the PKG-1 seam: `transaction.run_child` (~3436) still adds the candidate `libraries/cli-extended/src`; PKG-1 drops it (D10). Controller: remove the xfail marker at merge (it must then pass).
- Local equivalent of the coverage lane (home venv, which has the released cli-extended 0.2.0 wheel installed; full suite, no `--maxfail`, `--cov=src/cmru --cov-branch --cov-fail-under=100`): `3295 passed, 2 skipped, 1 xfailed`, "Required test coverage of 100% reached. Total coverage: 100.00%". (No `src/cmru` code changed in this package; the only `src` change is the skill file moving in.)

## Gate lanes: NOT GREEN on the current image, by construction (needs a tester-unified rebuild)

I ran, on the committed tree (clean), `flock gate.lock ./run-gate.py --worktree <wt> coverage` then `canary`. Verdicts read in a separate step from `scratchpad/pkg4-g-coverage.out` / `pkg4-g-canary.out`:

- `coverage`: `lane 'coverage' verdict FAIL; exit_code 2`: pytest collection stopped with 112 import errors `ModuleNotFoundError: No module named 'cli_extended'`.
- `canary`: `verdict FAIL; exit_code 1`: "known-good canary control failed with exit 2" (the same import error in the control run).

Cause (verified): the running `tester-unified:local` image was built 2026-09-16 and holds cmru `0` with no `cli_extended` anywhere in `/opt/tester-venv`. Before this package every lane got `cli_extended` only from the `PYTHONPATH=...:../libraries/cli-extended/src` source root, which scope 5 REMOVES by design (the gate must use the INSTALLED wheel). So these lanes can only pass on an image built from this package's Dockerfile (released cli-extended wheel installed by sha256). I did not build it (instruction) and did not add a source fallback to the lanes (that would silently reintroduce the vendoring this package removes). **Controller action: authorise one tester-unified rebuild (or install the released wheel into the image's venv), then rerun `coverage` and `canary` on this branch.** Until then the only gate evidence is the local run above and the full suite.

`tools/installed_wheel_smoke.py` (the `installed-wheel` lane's program) ran locally with no network (`CMRU_SMOKE_NO_BUILD_ISOLATION=1 PIP_NO_INDEX=1`): `installed wheel get-py acceptance: passed`; the same program raised "vendors cli_extended files" when the vendoring plant was applied. The lane itself was not run (it uses isolated builds, which need network).

## Plant mutations (each applied by hand, tests run, reverted with `git checkout <file>`; tree clean afterwards)

| Plant | Killed by |
| --- | --- |
| vendored copy re-added (`where` gains `../libraries/cli-extended/src`) | `test_pyproject_vendors_no_cli_extended_source_in_any_packaging_config`; the real wheel build in `installed_wheel_smoke.py` raised "vendors cli_extended files" |
| `Requires-Dist` missing (`dependencies = []`) | `test_pyproject_declares_cli_extended_with_the_documented_floor`, `test_real_estate_trees_...`, `test_dockerfile_fetch_floor_equals_cmru_declared_floor` (the synthetic-wheel oracle tests cover the built-wheel form) |
| cli-extended line reaching pip, generator form (`admit` returns every line) | 17 tests, incl. the 4 `..._skips_every_cli_extended_line_so_none_reaches_pip` cases |
| cli-extended line reaching pip, Dockerfile form (`pip install cli-extended>=0.2.0`) | `test_dockerfile_no_cli_extended_reaches_pip_except_the_sha256_verified_release_wheel`, `test_dockerfile_fetches_the_release_by_sha256_before_cmru_is_installed` |
| installer order reversed (`_wheel_order` puts cli-extended last) | `TestCliExtendedInstallOrder::test_declared_after_the_tool_it_is_still_installed_first_and_hash_locked` |
| bootstrap sha256 compare disabled | `test_bootstrap_refuses_a_wheel_whose_sha256_does_not_match_before_building` |
| fetcher digest compare disabled | `test_fetcher_installs_nothing_unless_the_pointer_digest_matches` |

Honest limit: pip's own order behaviour is not observable offline (it resolves the dependency from `--find-links` regardless
of order), so the "wrong order fails" oracle is the lock-file order plus the argv, as in W1's tests.

## Deviations and notes for the controller

1. **Scope extension, `cmru/cmru.toml`**: its `[env] PYTHONPATH` named `../libraries/cli-extended/src` (BG-04); not on my file list, but it is the same vendoring and the gate/transaction child inherits it, so I changed it to `src:../libraries/worktree/src` and updated `test_docs_config_examples.py::test_cmru_own_env_pythonpath_...` accordingly.
2. **Scope extension, `test_cli_dispatch.py`**: besides the two subprocess tests I also moved the two bootstrap-script tests (`test_fresh_checkout_bootstrap_is_the_only_source_build_launcher`, `test_bootstrap_script_runs_python_isolated...`): they asserted the old `libraries/cli-extended/src` root in the script I had to change. The change to that file is four whole-function deletions only (merge hazard for PKG-1: keep their edits to other functions).
3. **`.dockerignore`**: the brief said "include only what the Dockerfile COPYs", but `nyxloom/nyxloomd/Dockerfile` (root context) still needs `libraries/cli-extended/src/cli_extended`, so that nyxloom whitelist stays (commented as nyxloomd's). The tester image copies none of it (tested).
4. `tests/test_tester_unified_image.py` (W0-TESTER's file, unlisted) was updated: it pinned the old refuse/offline-build policy.
5. The tester-unified image in use today still holds the OLD local `cli-extended+tester.unified` build until rebuilt; the lanes work against it because PYTHONPATH now simply omits the source root. The rebuild (controller-approved only) will fetch the release; the `CLI_EXTENDED_POINTER_URL` default is `https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json` (not exercised: no live calls).
6. The Dockerfile build itself, the `fetch` against the real GitHub pointer, and `pip check` inside the image are UNVERIFIED (no docker, no network). I deliberately did not add a `pip check` step because I could not prove it passes on the image's existing closure.
7. `nyxloom/pyproject.toml` still vendors cli-extended via `package-dir` (nyxloom adoption is a separate wave); not touched.
8. CHANGES.md untouched (controller folds): "cmru now requires cli-extended>=0.2.0 (release asset, never PyPI): install it first / `--no-index --find-links`; `pip install -e .` without `--no-deps`/`--no-index` now queries PyPI for it; skill ships in the wheel; build bootstrap verifies the released wheel by sha256".
9. Checkpoint: I passed ~65 tool calls but cut only at this green boundary; no continuation file needed.
