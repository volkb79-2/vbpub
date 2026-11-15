<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/h1g-REPORT.md -->
<!-- relation: H1g implementation evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# H1g' implementation report

Worktree: `<worktree>`  
Branch: `h1g-20261108`  
Final HEAD: `58ac28389f9d0013918d32f629e07ea27b153745`

## Status by item

1. **Retired host-setup provisioning — complete.** Removed the six specified unit/script sources and their install/check wiring, retired `--observer-only` and `--cgprofile-only`, removed old cgprofile/topos-only environment keys, and replaced README provisioning sections with pointers to `cgprofiled install` and `toposd install`.
2. **Old-file refusal — complete.** Before mutating install modes, `install.sh` refuses when any retired tmpfiles, sysusers, topos drop-in, installer copy, BuildKit profile script, or old `/etc/environment` marker remains. The refusal prints manual cleanup steps. Fake-root cases cover every retired file and the old environment block.
3. **Endpoint publication — complete.** Removed the `/etc/environment` mdt-buildkit writer. The `mdt-host-setup` fragment publishes `BUILDX_BUILDER`, `BUILDKIT_HOST`, Docker GID, BuildKit socket/path/GID, and Docker API socket/path through `augeaswrap endpoints publish` after BuildKit readiness and the builder probe. `AUGEASWRAP_BIN` is configurable; missing augeaswrap refuses with an install instruction. `--dry-run-endpoints` prints the fragment, uninstall guidance withdraws it first, and `check.sh` emits one endpoint status line including a clear missing-command failure.
4. **Tests — complete.** Updated the requested host-setup and renderer tests, added fake-root refusal/publication/withdraw/dry-run/missing-command/check-line coverage, and updated affected template/runtime-lane assertions. New endpoint tests use temporary roots and stub commands; they do not call the real installer or touch host `/etc` or Docker.
5. **Docs — complete.** Updated mdt-cockpit README, CHANGES, DESIGN-GUIDE, CONSUMERS, and host-setup README, including retired options/files, refusal and fragment behavior, consumer endpoint values, and the `AUGEASWRAP_BIN` override.
6. **Commit and worktree — complete.** Three commits on the task branch carry the requested coauthor trailer. The worktree is clean; no push, merge, tag, or release was performed.

## Mutation checks

- **Refusal mutant: killed.** Changed the refusal path to return success with retired artifacts present. `host-setup-admission` reported 8 failures covering the seven retired-file cases and the old environment block; 231 other tests passed, with 92 subtests. Log: `<controller-program-dir>/logs/h1g-refusal-mutant.log`.
- **Publication-order mutant: killed.** Moved publication before BuildKit readiness/probe. `EndpointInstallPreflight.test_endpoint_publication_follows_buildkit_readiness_and_probe` failed; 231 other tests passed, with 99 subtests. Log: `<controller-program-dir>/logs/h1g-publish-order-mutant.log`.

## Gate and verification

Final registered gate: `run-gate smoke`, run under `nice -n 10` through `tester-unified:local`. **PASS, exit 0.** `test-render.sh` reported `ALL OK`; pytest reported **1,442 passed, 1 skipped, 7 deselected, 230 subtests passed**. The lane also completed `py_compile`, `bash -n`, and ShellCheck checks. Log: `<controller-program-dir>/logs/h1g-smoke-final.log`.

An earlier smoke attempt found stale template/test expectations and the missing Buildx shim install line; those were corrected before the final passing run. Static `git diff --check` and shell syntax checks pass. At final resource inspection there was no active Run-Gate container and anonymous memory was 3.69 GiB.

## Review fix round (2026-11-08, after h1g-REVIEW.md FIX-FIRST)

Branch `h1g-20261108`, new head: see "Head" below (one commit on top of `58ac28389`, message "Split H1g': ..."). Note for the merge: the earlier commits still contain the consumer pieces; the new commit reverts them, so the NET diff to main is host-setup only. Squash-merge (or merge as-is) both give the same tree.

### B2 split (controller decision): reverted to origin/main state
Saved first as `<controller-program-dir>/reports/h1g-cutover-pieces.patch` (26 files; applies cleanly with `git apply` on this branch's new head; the mixed doc files are relative to the post-split state, so apply it after this branch is merged. `cmru.orchestration.toml` and every pure consumer file also apply on origin/main). Reverted pieces:
- `.devcontainer/devcontainer.json`, `mdt-cockpit/templates/devcontainer.json`, `mdt-cockpit/templates/host-folder/devcontainer.json` (`${localEnv:...}` mounts, `--group-add`, containerEnv incl. `CGPROFILE_*`/`TOPOS_*`).
- `cmru.orchestration.toml`: `BUILDX_BUILDER = "${BUILDX_BUILDER}"` / `BUILDKIT_HOST = "${BUILDKIT_HOST}"` back to the `:-mdt-managed` / `:-unix:///run/mdt-buildkitd/buildkitd.sock` defaults (an unset variable is a cmru load error; main's unrelated changes to that file are untouched).
- `mdt-cockpit/run-gate.toml`: the 15 added `required_env` names on lanes `image-runtime-built` and `image-runtime` (the `host-setup-admission` lane edit stays, it is host-setup).
- Same class, found in the third/first commit: `mdt-cockpit/scripts/ensure-release-builder.sh` (required `MDT_BUILDKIT_SOCKET`), `scripts/finalize_container_environment.py` (required `MDT_BUILDKIT_SOCKET`, inconsistency error for the old env) and `scripts/mdt_buildkit_builder.py` (removed `MANAGED_ENDPOINT` default / required endpoint argument).
- Tests that assert those pieces, reverted to main: `scripts/test_image_runtime_lane.py`, `test_runtime_lane.py`, `test_release_flow.py`, `test_local_dev.py`, `tests/runtime/harness.py`, `tests/runtime/test_fresh_home.py`; partly reverted and adapted so nothing reads a deleted host-setup file: `scripts/test_devcontainer_audit.py` (the three tests that read `units/mdt-cgprofile.conf` / `mdt-topos*.conf` now assert the literal `/run/cgprofile` and `/run/topos` mounts and that host-setup no longer owns them), `host-setup/tests/test_buildkit_governance.py` (the profile.d assertion became "no profile.d file; the fragment writer carries the values"; template/cmru assertions are main's), `host-setup/tests/test-render.sh` (Docker API bind-mount greps are main's).
- Docs describing the consumer side: `USAGE.md`, `TODO.md`, `docs/DEVCONTAINER-MECHANISMS.md`, `docs/DOCKER-IMAGE-STORE.md`, `docs/OCI-IMAGE-TOOLING.md` fully reverted; hunks reverted in `README.md`, `docs/CONSUMERS.md` (consumer devcontainer JSON, group text, release `[env]`), `docs/BUILD-ARCHITECTURE.md` (cmru `[env]` bullet), `docs/DESIGN-GUIDE.md` (mount-ownership paragraph, BuildKit socket paragraph; the "consumers read the facts" paragraph now says moving consumers is a later phase-2 cutover). Also restored by accident-deleted text in CONSUMERS.md (wizard memory-policy paragraph) and the cockpit-image `cgprofile` clients sentence is still absent (listed under backlog below).
Kept (host-setup only): the shim without socket default (documented), the fragment publisher, the refusal, check.sh, install.sh, host-setup tests/docs, `host-setup-admission` lane list.

### B1 fixed
`check.sh` `check_buildx_default_builder` now reads `BUILDX_BUILDER`, `BUILDKIT_HOST`, `MDT_BUILDKIT_SOCKET` from `/etc/host-endpoints.d/mdt-host-setup.env` (helper `_host_setup_endpoint_fact`) and passes them into the `env -i` probe exactly like install.sh does; a record lacking any of them FAILs with "run host-setup/install.sh" instead of probing; the FAIL text no longer claims an empty environment. New tests (`FreshUserBuilderProbe`) execute the real extracted function with a shim-like fake docker (exit 2 "MDT_BUILDKIT_SOCKET is unset" without the socket): facts reach the probe and nothing else leaks through `env -i`; each missing fact fails without running docker; a non-managed builder fails.

### Survivors killed, messages, docs
- N9/m5: `CheckScriptWiring` requires bare call lines for `check_host_setup_endpoint_record` and `check_buildx_default_builder`.
- N2/m1c: dangling-symlink refusal case for every retired path.
- N3: refusal now prints the safe order (service files, `systemctl daemon-reload`, BuildKit variables last), the reboot/tmpfiles caveat (`/run/cgprofile`, `/run/topos` not recreated at boot until the service installers run), the cutover reference and the log-in-again note; test asserts content and order.
- N1: "log in again / restart long-lived sessions (VS Code Remote-SSH server, systemd units)" note added to CHANGES, host-setup README (section "After the cutover, log in again"), BUILD-ARCHITECTURE and DESIGN-GUIDE.
- N10: MDT-055 design block marked SUPERSEDED. N4: README says `sudo` resets `AUGEASWRAP_BIN` (use `sudo env ...`), version not enforced. N5: install.sh prints a re-run hint when publication fails. N6: README states the refusal precedes every mode. N7: usage line and README say `--dry-run-endpoints` needs root, systemd and a live BuildKit socket.
- Not done (backlog): N8 writer string without a version; optional shim fallback deriving the socket from `BUILDKIT_HOST` (design choice); README sentence about the cockpit image carrying the `cgpctl`/`cgprofile` clients was dropped by the first round from CONSUMERS.md (restore when convenient); gap 1 (new sessions lack the variables between block removal and the end of install.sh) is documented in the removal order, not engineered away.

### Hand mutations of every new guard (focused file `test_cgprofile_install.py`, each KILLED, file restored after each)
m1 drop whole probe injection, m1b drop only `MDT_BUILDKIT_SOCKET` injection, m2 drop the `check_host_setup_endpoint_record` call, m2b drop the probe call, m3 drop the `-L` branch, m4 drop `daemon-reload` text, m4b drop the reboot caveat, m4c drop the log-in-again note, m5 drop the missing-facts guard.

### Verification
Focused files (one at a time, `nice -n 10`): `test_cgprofile_install.py` 20 passed; `test_install_runtime_blocks.py` 24; `test_docker_buildx_shim.py` 19; `test_hs02.py` 30; `scripts/test_devcontainer_audit.py` 13; `test_image_runtime_lane.py` 5; `test_runtime_lane.py` 37; `test_release_flow.py` 32; `test_local_dev.py` 56; `test_docs_in_sync.py` 26. `test_buildkit_governance.py` and `test-render.sh` need `hypothesis` (absent in the devcontainer venv): covered by the smoke gate.
