<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/e2-REVIEW.md -->
<!-- relation: e2 review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# E2 independent review: topos 0.8.0 endpoints

Reviewed: branch e2-20261108, head 0cc7c84f2 (6 commits on 2caf58d1f). Fetched origin/main is 63a5cf3e0.
Method: scratch `git archive` copies under <controller-scratch>/rv-e2/ (`src` = E2 head, `merged` = the merge-tree result with the conflicts hand-resolved). Worktree untouched. One pytest file at a time under `nice -n 10`. Anon memory stayed at or below 3.8 GB. No gate container started (the implementer's logs were fresh, see 6).

## Verdict: FIX-FIRST

The REN-B' bug class is NOT present and the installer, doctor, resolver and tests are sound. But the topos code can neither be released nor be merged to a green estate: two blocking integration/release-path defects and one mechanical merge conflict. Both defects were measured, not inferred, and the E2 gates could not see them (they run on the old tester-unified image and only the cmru level test).

## Blocking

**B1. After merge, tester-unified's requirements generator refuses topos' new `augeaswrap` dependency, and three cmru tests fail.**
- `topos/pyproject.toml:14` now requires `augeaswrap>=0.1.0`. `tester-unified/gen-requirements.py` runs over `topos/pyproject.toml` (PROJECTS) and refuses every estate-internal name that is not source-backed. On origin/main the exemption is only `SOURCE_BACKED_RUNTIME = {"cgprofile": frozenset({"augeaswrap"})}` (`gen-requirements.py:87`, added by the cgprofile REN-B' merge and the tu-dockerignore merge, both after E2's base).
- Measured on the merge-tree result: `python3 tester-unified/gen-requirements.py --root .` exits 1: "topos/pyproject.toml [project] requires estate-internal distribution 'augeaswrap' ... Refusing to hand it to a PyPI-default pip". The tester-unified image cannot be rebuilt.
- In `cmru/tests/test_tester_unified_image.py` (run on the merged tree, 548 passed, 3 failed): `test_real_estate_trees_have_no_internal_requirement_in_the_pypi_bound_closure`, `test_cgprofile_augeaswrap_dependency_is_never_forwarded_to_pip`, `test_the_age_window_never_applies_to_a_first_party_package`.
- Fix: `SOURCE_BACKED_RUNTIME = {"cgprofile": frozenset({"augeaswrap"}), "topos": frozenset({"augeaswrap"})}` and update its comment (it names only cgprofile's gate). I applied exactly this in the scratch merged tree: the file then gives 551 passed. Add a topos twin of `test_cgprofile_augeaswrap_dependency_is_never_forwarded_to_pip` (`cmru/tests/test_tester_unified_image.py:4494`). topos-suite's lane already puts `augeaswrap/src` on PYTHONPATH (`topos/run-gate.toml:16`), so no Dockerfile change is needed. Merge or rebase origin/main first, because the code to edit does not exist on the branch's base. Also run `cmru/tests/test_tester_unified_image.py` once as part of the E2 verification.

**B2. toposd cannot get `augeaswrap` onto a host: the release lock step fails, and an install from a release URL could not resolve it either.**
- `augeaswrap` is not on any index (the estate treats it as GitHub-Releases-only; `pip index versions augeaswrap` finds nothing). E2 adds it to `[project].dependencies` and the docs say it travels "in the daemon lock" (`docs/DAEMON.md:66`, `docs/CONSUMERS.md`), but only `cli-extended` is carved out of the lock path.
- `topos/tools/build_daemon_lock.py:66-75` (`_dependency_requirements`) drops only `cli-extended`, so `augeaswrap>=0.1.0` goes to `pip download --only-binary :all: --uploaded-prior-to P14D` (`:78-105`). Measured: `LockBuildError: pip download failed with exit 1: ERROR: Could not find a version that satisfies the requirement augeaswrap>=0.1.0 (from versions: none)`. This is the cmru release step "topos: build daemon dependency lock" (`topos/cmru.toml:36`), so topos 0.8.0 cannot be released. On any index that did serve a same-named project, the lock would record that foreign wheel's hash (dependency confusion, the reason the estate keeps first-party names off index-bound pip).
- Install side: `install.py:1030-1037` passes `--no-index --find-links` only when `--wheel-source` is a directory. With the documented release URL (`docs/CONSUMERS.md:41`) pip would query the default index for the hash-locked `augeaswrap`.
- The tests hide it: `tests/test_build_daemon_lock.py` fakes the downloader and even asserts the lock rows are `["alpha","augeaswrap","zeta"]`; `tests/test_wheel_install.py` builds the augeaswrap zip itself; the install tests monkeypatch `_endpoints_api` or run on a fake root. The VM script (`tools/vm-socket-acceptance.sh:70-83`) puts a local augeaswrap wheel in `$WHEELS`, which is also the only shape that could work.
- Fix, as cgprofile did in REN-B' (cgprofile `installer.py` `--augeaswrap-wheel <ver>@sha256:<h>`): treat augeaswrap like cli-extended. Concretely: (a) `build_daemon_lock.py` excludes `augeaswrap` in `_dependency_requirements` and in the row filter at `:134`; (b) `toposd install` gets `--augeaswrap-wheel` (pin form, floor 0.1.0, sha256 checked before use), fetched from the wheel source like the cli-extended wheel, installed `--no-deps` next to it in the venv, and its pin/sha recorded in the MANIFEST; (c) flip the lock test, add install-argument/pin tests; (d) docs (`CONSUMERS.md`, `DAEMON.md`, `OPERATIONS.md`) and the VM script pass the new pin; (e) the bootstrap invocation must put the augeaswrap wheel on PYTHONPATH (the script already does). If the operator prefers a different delivery, it must still end with augeaswrap hash-pinned outside the index-resolved lock.

**B3. `git merge-tree --write-tree origin/main 0cc7c84f2` conflicts (mechanical).**
- Conflicts in `cmru.orchestration.toml` (LEVELS comment block, lines 41-57) and `docs/BOOTSTRAP-FROM-ZERO.md` (the `project-order` block). The branch computed levels without cgprofile's own augeaswrap edge. `cmru/tests/test_dependency_levels_pyproject.py` auto-merges.
- Correct resolution (verified in the scratch merged tree): `L1: augeaswrap, tnt, ciu, nyxloom, pwmcp, iobench, rigor-record`, `L2: cgprofile, topos`, `L3: run-gate-project`, `L4: cmru`, `L5: mdt-cockpit`. With it, `cmru dependencies` (no `--write`) prints the same blocks and `PREFLIGHT: PASS`, and `tests/test_dependency_levels_pyproject.py` gives 18 passed. Resolve by merging, not by picking a side (the generated blocks must come from `cmru dependencies --write`).

## Nits (non-blocking)

- N1. Survivor: dropping `or os.path.lexists(paths.endpoint_fragment)` from the uninstall `owned` check (`install.py` `_uninstall_targets`, about line 1350-1354) survives all of `test_service_install.py` (155 passed). Same survivor REN-B' had. Add a fragment-only uninstall test (a host with only `etc/host-endpoints.d/topos.env`; I confirmed manually that uninstall removes it).
- N2. The no-literal guard is thin. `tests/test_observe_socket.py:78-88` greps four modules for the regex `/run/topos/(observe/)?topos.sock`. A split literal in `cli.py` (`Path("/run/topos") / "observe" / "topos.sock"`) and a full literal in `daemon/client.py` both survive. Scan every module under `src/topos` except an allow-list (`service/config.py`, `service/install.py` refusal text, `assets/`, `config.py`/`actions/squeeze.py` state paths) for `/run/topos` and `.sock`, or at least add `daemon/client.py` and `daemon/http_gateway.py` to the list.
- N3. `install.py:1379-1381` still carries literal runtime-tree names (`{"observe","state"}`, `{"topos.sock"}`), the REN-B' S1 class. Unreachable today because install pins the config to the `DEFAULTS` path (`install.py:1333-1342`), so derive them from `DEFAULTS` for hygiene.
- N4. Publish failure after apply: behaviour is good and I verified it with the real `augeaswrap` on a fake root. `publish` raising leaves PENDING and the full install record, no fragment; rerun publishes and clears PENDING; uninstall after the failure removes everything. The error is wrapped as `InstallError("cannot publish topos host endpoint record: ...")`, which is better than cgprofile's raw `CliFailure`. It is unit-tested (`test_install_preserves_publish_errors_and_wraps_unexpected_ones`) but the recovery is not stated in the docs (`docs/DAEMON.md:200-212` describes only the readiness case). Add one sentence and a rerun-after-publish-failure test.
- N5. Client behaviour not documented: a relative `TOPOS_OBSERVE_SOCKET` is accepted and resolved against the cwd (connect error then names it); under `--json` the unset refusal is plain text exit 2, no machine code like cgprofile's `endpoint-unset`. Acceptable, but say so in `docs/OPERATIONS.md`.
- N6. The MCP registration in `README.md:63`, `:154` and `docs/DAEMON.md:428` (`claude mcp add topos -- topos mcp serve`) does not mention that the registering shell must carry `TOPOS_OBSERVE_SOCKET` (or `-- topos mcp serve --socket PATH`); otherwise the server exits 2.
- N7. `tests/test_mcp_server.py` is skipped locally (no `mcp` extra in the devcontainer: 1 skipped); it is covered by the topos-suite gate (3672 passed).

## Checks, by brief item

**REN-B' class (first).** Not present.
- `toposd install` makes no call to any topos client. Readiness is `Installer._wait_ready(self.paths.at(config.sockets.observe.path...))` (`install.py:1268`) over the module's own raw `_hello(path)` (`:833`); `service/doctor.py:386, :428` also use `_hello(_configured_socket_path(config, paths))`. None reads `TOPOS_OBSERVE_SOCKET`.
- Real code, empty environment: `Installer._wait_ready` against a real unix socket answering `hello` with `TOPOS_*` removed returned OK. Every real client entry point under `env -i` (with `PYTHONPATH` to the sources): `topos daemon current|status|health`, `topos mcp serve`, `topos --attach --once --json` all exit 2 with "TOPOS_OBSERVE_SOCKET is unset: log in again, or `set -a; . /etc/host-endpoints.env`, or run `toposd install`". `build_daemon_status()` and `run_server()` called directly give `CliFailure` with the same text.
- No other caller in the repo (mdt-cockpit, run-gate, docs, scripts) invokes a topos client without the variable; run-gate reads no `TOPOS_*` (grep).

**(1) Scope.** `git diff origin/main...0cc7c84f2 --stat`: 33 files. topos/ (src, tests, docs, `run-gate.toml`, `cmru.toml`, `pyproject.toml`, `tools/vm-socket-acceptance.sh`), `cmru.orchestration.toml`, `cmru/tests/test_dependency_levels_pyproject.py`, `docs/BOOTSTRAP-FROM-ZERO.md`. No TUI files, nothing else. One small extra: `daemon/http_gateway.py` runs `_validate_startup` in `GatewayConfig.__post_init__` so gateway config errors precede the endpoint lookup (accepted; tested).

**(2) Fragment and installer.** Keys and values `TOPOS_OBSERVE_SOCKET`, `_SOCKET_DIR`, `_GID` match design 2.2 and the ownership table (`DESIGN.md:188`). `augeaswrap/docs/CONSUMERS.md` only documents the cgprofile example and no consumer reads `TOPOS_*` yet. Real-API fake-root run wrote `# owner=topos`, `# writer=toposd install 0.7.3`, GID resolved to 9101, and the aggregate. Order verified: preflight `plan` before any change; dry-run reports `endpoint_fragment` with the GID unresolved as `unresolved_groups`; `publish` after the final record write and before the PENDING unlink; uninstall withdraws first, after the dry-run return, root check and confirmation, and after the ownership/tree refusal (so a refused uninstall does not withdraw). Re-install is idempotent, fragment-only host uninstalls. Paths come from `DEFAULTS` (`InstallPaths.observe_dir` etc.); the fragment path literal `/etc/host-endpoints.d/topos.env` is built in `InstallPaths.endpoint_fragment` because `augeaswrap.endpoints` exposes no public path helper (accepted, tested).

**(3) Resolver.** `socket_path.py`: explicit path wins (`--socket`, `--attach PATH`, `--daemon-socket`), env otherwise via `augeaswrap.endpoints.require_env`, refusal re-raised with exit 2 naming the variable. Measured: unset exit 2; empty env exit 2; `--socket ''` and `--attach ''` exit 2 "observe socket path cannot be empty"; relative path accepted (N5); nonexistent path gives a connection error naming the path (exit 2) and `daemon status` reports `DEGRADED`; env decoy plus `--socket` uses the flag. Help text of `topos --help` and `topos daemon --help` contains no path (the Overall guard test also covers the five parsers). Literal grep over `topos/src`: `/run/topos` remains only in `service/config.py` (defaults), `service/install.py` refusal text and old-layout checks, `assets/systemd`, `config.py:166` and `actions/squeeze.py:123` (daemon state, not sockets). No client default left.

**(4) Dependency.** `topos/pyproject.toml`: `cli-extended>=0.5.3` and `augeaswrap>=0.1.0`, each with a reason comment (the augeaswrap reason names only the publisher and client helper). `cmru.orchestration.toml` edge, graph, levels (topos L2 on the branch) consistent; `cmru dependencies` without `--write` on the branch copy: PREFLIGHT PASS. Merged state: see B3. The dependency delivery problem is B2.

**(5) Hand mutants (16 tried; relevant test files only).**

| Mutant | Result |
|---|---|
| M1 publish before the final record write | killed (`test_install_publishes_endpoint_after_the_final_record_write`) |
| M2 withdraw after the service stop | killed (`test_uninstall_withdraws_endpoint_before_stopping_or_removing`) |
| M3 unset refusal replaced by a default-path fallback | killed (`test_resolver_requires_the_named_environment_variable`) |
| M4 environment beats the explicit `--socket` | killed (`test_resolver_reads_environment_and_an_explicit_path_overrides_it`) |
| M5 doctor ignores `verify` problems | killed (`test_endpoint_record_check_matches_config_and_rejects_stale_fragment`) |
| M6 preflight `plan` problems ignored | killed (`test_endpoint_plan_collision_refuses_before_changes`) |
| M7 full socket literal re-added in `mcp/server.py` | killed (no-literal guard) |
| M7b split literal in `cli.py` | **SURVIVED** (N2) |
| M7c full literal in `daemon/client.py` | **SURVIVED** (N2) |
| M8 fragment key typo in `install._endpoint_entries` | killed (dry-run test) |
| M9 key typo in doctor expected map | killed |
| M10 publish call removed | killed |
| M11 withdraw call removed | killed |
| M12 uninstall `owned` ignores the fragment | **SURVIVED** (N1) |
| M13 `endpoint-record` check unregistered | killed (`test_doctor_registers_the_contract_check_names_in_order`) |
| M14 dry-run drops `endpoint_fragment` | killed |
| M15 withdraw errors swallowed | killed |
| M16 unresolved GID accepted at publish | killed |

**(6) Test runs, once each on the branch copy.** test_observe_socket 9, test_endpoint_docs 3, test_service_install 155, test_service_doctor 46, test_attach_cli 13, test_cli_command_parsers 10, test_daemon_status 11, test_service_lock 11, test_daemon_component_health 49, test_build_daemon_lock 12, test_packaging_metadata 5, test_wheel_install 1: all passed. test_mcp_server 1 skipped (N7). cmru `test_dependency_levels_pyproject.py` 18 passed (branch and resolved merge; needs `--cli-case-partial` when run alone). On the merged tree also: `test_bootstrap_from_zero_sequence.py` 50 passed; `test_tester_unified_image.py` 548 passed, 3 failed (B1), 551 passed with the fix.
- Implementer gate logs, all PASS, all for container names carrying `0cc7c84f` and all started after the newest source edit (commit 15:51:48Z): `e2-endpoints-5.log` 250 passed (session 15:52:01Z); `e2-topos-suite-3.log` 3672 passed, diff coverage 2157/2157 100.0% (15:53); `e2-py-compile-2.log` OK (15:58:36); `e2-cmru-levels.log` 18 passed (15:55). Good evidence for what they ran, but none covers B1/B2.

**(7) merge-tree.** Conflicts in `cmru.orchestration.toml` and `docs/BOOTSTRAP-FROM-ZERO.md` (B3). No other file conflicts; B1 is a semantic conflict that git does not report.

## Summary of required work
1. Merge origin/main into the branch; resolve the two level blocks via `cmru dependencies --write` (B3).
2. Exempt topos in `tester-unified/gen-requirements.py` `SOURCE_BACKED_RUNTIME` with a test (B1); run `cmru/tests/test_tester_unified_image.py`.
3. Deliver augeaswrap through an explicit hash-pinned wheel (`--augeaswrap-wheel`), keep it out of the daemon lock, fix tests and docs (B2).
4. Same round: N1 test, N2 guard widening, N4 sentence.
