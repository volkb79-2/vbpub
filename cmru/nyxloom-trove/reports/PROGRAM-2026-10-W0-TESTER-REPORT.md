# PROGRAM-2026-10 W0-TESTER report

Branch `cmru-w0-tester` (from `cmru-wave-2026-10` `baf0ea295`). Implementer: fresh Sonnet session.
No docker build and no real container was started; `docker` was a recording fake on PATH.
All repository files were changed with Edit/Write only (the plant/revert experiments ran on
scratch copies under the session scratchpad with a throwaway python replace, not on repository files;
one `cat >>` heredoc appended two tests to `cmru/tests/test_standards.py` and the scratchpad helper
scripts were heredocs: the heredoc append to a repository test file is a rule deviation, stated here).

## Findings closed

| Finding | Evidence (file) | Test(s) |
|---|---|---|
| KI-52(a) / BG-01(a): `--init` on the gate workload and the DinD sidecar | `tester_gate.py` `build_docker_command`, `_dind_start_argv` | `test_w0_tester_hardening.py::test_every_container_cmru_starts_has_the_declared_argv_policy` (drives real `main()` against the fake docker, asserts the argv of EVERY container started: gate, DinD, probe), `::test_run_command_surface_has_no_unnamed_or_uninitialised_gate_launch` |
| BG-01(a'): required `CMRU_TESTER_PIDS_LIMIT` -> `--pids-limit` | `REQUIRED_TESTER_ENV`, `resolve_pids_limit`; estate value `4096` in `cmru.orchestration.toml`, `templates/cmru.toml.tmpl`, `src/cmru/templates/orchestration.toml`, both samples | `::test_pids_limit_is_required_and_has_no_hidden_default`, `::test_pids_limit_refuses_unlimited_or_malformed_values`, `::test_estate_configs_declare_the_pids_limit_and_pinned_helper_images`, `test_standards.py::test_standards_requires_the_pids_limit_for_a_tester_gate`, `test_ki17_*` (set shared with standards) |
| BG-01(c'): in-container wrapper copies `pids.events`/`memory.events` to `.cmru/tester-gate-events-<uuid>.txt`; cmru exits 3 on missing file / `max>0` / `oom_kill>0`, else preserves the command's code | `_EVENTS_WRAPPER`, `read_events_problems`, `gate_exit_code`, `_run_tester_gate_body` | `::test_gate_exit_code_reads_the_events_file` (9 cases incl. fake events files: max=0 pass-through, max>0, missing, partial, garbage), `::test_main_exits_infrastructure_failure_on_counter_or_missing_file` (real `main()`, fake docker writes the file; also asserts the file and the empty `.cmru` dir are removed), `::test_gate_wrapper_copies_both_counter_files_and_preserves_the_exit_status` (runs the real shell wrapper against fake cgroup files) |
| KI-52(b): `git config --system maintenance.autoDetach false` / `gc.autoDetach false` | `tester-unified/Dockerfile` (+ build-time `test` assertions), `tester-unified/README.md` | `test_tester_unified_image.py::test_dockerfile_disables_detached_git_maintenance_system_wide_and_asserts_it` |
| BG-05: requirements generator refuses estate-internal names; internal packages only built offline from COPYed sources; build-time import assertion | new `tester-unified/gen-requirements.py`, `Dockerfile`, `.dockerignore` | `test_tester_unified_image.py` (30 tests: generator on synthetic trees and on the real trees, refusal of every internal name/extras/build-requires/direct URL, `--build-requires` mode, Dockerfile text, a Docker-semantics `.dockerignore` emulator proving the needed files are admitted and the rest excluded) |
| BG-05 note: `bundle.py` `pip wheel .` default index | `bundle.py` `build_wheel` | `test_build_release_boundaries_final.py::test_bundle_build_wheel_command_uses_find_links_and_project_cwd` (see decision below) |
| BG-02: exact unique names, SIGTERM/SIGHUP -> `SystemExit`, `docker stop` then `docker rm -f` by exact name in `finally`, probe timeout removes the probe, `run_command` kills and reaps its child | `_container_name`, `_remove_container`, `_terminate_as_exit`, `_run_probe`, `dind_sidecar`, `runner.run_command` | `::test_each_started_container_is_stopped_then_removed_by_exact_name`, `::test_probe_timeout_removes_that_probe_by_exact_name`, `::test_sigterm_stops_and_removes_the_gate_container_by_name` (a real child process, real SIGTERM, exit 143, stop before rm -f), `::test_run_command_kills_and_reaps_its_child_on_keyboard_interrupt` (kill(pid,0) fails, which also proves the zombie was reaped) |
| BG-07: required DinD memory/CPU/pids, readiness probe timeout, M04, SPEC decision | `DIND_TESTER_ENV`, `resolve_dind_*`, `_dind_ready`, `_dind_start_argv`; SPEC tester-gate row | `::test_dind_ready_requires_exit_zero_and_a_version` (kills M04), `::test_dind_sidecar_never_ready_when_exec_fails_with_output`, `::test_dind_ready_probe_has_a_timeout_and_a_hang_counts_as_not_ready`, `::test_dind_limits_are_required`, `::test_dind_limits_are_validated`, `test_standards.py::test_standards_requires_every_dind_limit_for_a_docker_enabled_gate` |
| BG-12: mount tie picks the visible last entry (`>=`) | `tester_gate._physical_path`, `handlers._host_bind_source` | `::test_physical_path_tie_picks_the_visible_last_mount`, `::test_handlers_host_bind_source_tie_picks_the_visible_last_mount` (kill M09) |
| BG-13: image references validated, `-` prefix refused | `validate_image_reference`, `resolve_image`, `build_docker_command` | `::test_image_references_that_docker_would_parse_as_options_are_refused`, `::test_dash_prefixed_image_is_refused_before_any_container_starts` (no container started) |
| BG-06: privileged images digest-pinned, `--pull=never`, pins in estate config, SPEC aligned | `require_digest_pinned`, `_run_probe`, `_dind_start_argv`; `cmru.orchestration.toml`, `modern-debian-tools-python-debug/cmru.toml` | `::test_privileged_images_must_be_digest_pinned`, the argv-policy test (asserts `--pull=never` on probe and DinD), `::test_estate_configs_declare_the_pids_limit_and_pinned_helper_images` |
| Backlog | KI-52 marked fixed (b assay-bypassed), KI-42 live-probe checklist added, assay **B147** filed | n/a |

## Decisions and deviations

- **`bundle.py` (`pip wheel .`)**: `--no-deps` would be wrong. `pip wheel .` also collects the
  project's dependency wheels into `client_dir`; that is the bundle's purpose, so `--no-deps` would
  change the artifact. Correct fix: when `[wheel].find_links` is declared, add `--no-index` (the
  declared wheelhouse becomes the only source, so a public index cannot supply an estate-internal
  name). Without `find_links`, behaviour is unchanged (default index) and documented as such. Consequence,
  documented in SPEC: the wheelhouse must also hold the build requirements (pip's isolated build env
  honours `--no-index`). No project in the repo declares `find_links`.
- **Dockerfile uses a throwaway build venv** instead of `pip install --no-index` straight into the tester
  venv: cmru/cli-extended pin `setuptools==82.0.1` but assay's build requires pin `84.0.0`, so putting both in
  one venv would be a pip conflict. The internal wheels are built with `--no-index --no-deps
  --no-build-isolation` in `/tmp/internal-build-venv` (only pinned third-party backends, from the refusing generator's
  `--build-requires` mode), then installed with `--no-index --no-deps`. Version `0.0.0+tester.unified`
  (a local segment no PyPI release can have) is asserted at build time.
- **DinD container name** kept as the existing unique `cmru-tester-dind-<12 hex>` (task: keep if already unique); gate
  `cmru-tester-<uuid8>`, probes `cmru-probe-<uuid8>`.
- **mdt DinD limits** chosen by me: `2g` / `1.5` CPUs / `2048` pids (the task named no values). Review.
- **Probe image digests** (both images were local, read with `docker image inspect`, no pull):
  `debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132`,
  `docker@sha256:5efed980cba3fc126cf54e21a5a6ff8849d05b6e0623d6e7612f48e9cd6cd17e`. Nothing left TODO. The
  templates/samples carry a `debian@sha256:<digest>` placeholder that the resolver refuses until replaced; the
  `cmru-project-template-revision` marker was NOT bumped (W0-RETIRE also edits that file; the controller decides).
- **Probe `--init`**: not added (probes run fixed commands cmru controls; inventory said "fine"). Wheel-build
  (#5), buildx bake (#6): out of scope as instructed.
- **B-id**: assay B147 (B145 is the last on the integration branch; the parallel W0-GATE branch already used B146).
  Renumber on merge if needed.
- **`.cmru/`** added to the repo root `.gitignore` (the events file lives there for the duration of a run). Other
  consumers' repos are not covered; CONSUMERS.md says so.
- `docs/SPEC.md` open decision (DinD sizing) is now "decided"; the tester-gate grammar row lists the new options
  (`test_cli_spec_inventory` enforces it).
- Existing tests that passed placeholder images (`debian:test`) or `cpus=` only were updated for the new required
  inputs; many `subprocess.run` fakes now also write a clean events file (a mocked launch with no events file is,
  correctly, exit 3).
- **Not verified live**: the image build, `docker run --init` with the cgroup-v2 nesting script of the DinD image,
  the real `/sys/fs/cgroup/*.events` format inside a container, and that the container uid can write
  `.cmru/` in the bind-mounted worktree (the wrapper redirect failing would surface as a missing file, i.e. exit 3,
  not a pass). The KI-42 live probe (Wave 3) must cover these.

## Plant / revert table

Each plant applied to a scratch copy of `cmru/src` (or of the image files), the relevant test file run, result recorded.

| # | Plant | Killed by |
|---|---|---|
| P1 | gate loses `--init` | argv-policy test, static `--init` count test |
| P2 | DinD loses `--init` | same two |
| P3 | `gate_exit_code` ignores the events file | 9 `gate_exit_code` cases + 4 `main` cases |
| P4 | `_physical_path` tie `>=` -> `>` | `test_physical_path_tie_...` |
| P5 | `handlers._host_bind_source` tie `>=` -> `>` | `test_handlers_host_bind_source_tie_...` |
| P6 | `_dind_ready` ignores the exit code (M04) | `test_dind_ready_requires_exit_zero...`, `test_dind_sidecar_never_ready...` |
| P7 | probe never removed after a timeout | `test_probe_timeout_removes...` |
| P8 | `run_command` does not kill/wait on interrupt | keyboard-interrupt test |
| P9 | no SIGTERM/SIGHUP handler | real-signal test |
| P10 | digest check disabled | `test_privileged_images_must_be_digest_pinned` (6) |
| P11 | `--pids-limit` not passed | argv-policy test |
| P12 | image-reference validation disabled | 8 tests |
| P13 | DinD readiness probe without timeout | timeout test |
| P14 | `docker rm -f` replaced | exact-name cleanup, probe-timeout, SIGTERM tests |
| I1 | generator internal-name refusal off | 10+ generator tests |
| I2 | `gc.autoDetach` line dropped from Dockerfile | Dockerfile git-config test |
| I3 | wheel build without `--no-index` | Dockerfile offline-install test |
| I4 / I4b | import-location / version assertion removed | Dockerfile assertion test (first version was HOLLOW: a comment kept the substring; I4 SURVIVED, test tightened to match the `assert` statement, then both killed) |
| I5 | `.dockerignore` re-include dropped | dockerignore admits test |
| I6 | direct-URL refusal off | direct-URL test |

## Test and gate results

- Full suite (`cmru/tests`, no `--maxfail`, under the shared lock, PSI checked): **2978 passed, 10 skipped, 2 failed**; the
  two failures are exactly the known KI-54 tests in `test_cli_release_snapshot_boundaries.py`
  (`test_release_rejects_internal_handoff_on_dry_run`, `test_release_rejects_an_internal_snapshot_spanning_multiple_git_families`).
- Gate lanes `coverage` and `canary`: see the section below.
