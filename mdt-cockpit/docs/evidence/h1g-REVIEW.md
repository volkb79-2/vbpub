<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/h1g-REVIEW.md -->
<!-- relation: H1g review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# H1g' independent review (branch h1g-20261108, head 58ac28389)

Reviewer: fresh session, read-only. Scratch git-archive copy at <controller-scratch>/rv-h1g/ (new = 58ac28389, old = 2caf58d1f, mut/ = mutants). Worktree untouched, nothing run against the host, install.sh never executed.

## Verdict: FIX-FIRST

One reproducible functional bug (B1) and one sequencing/scope decision (B2). Everything else is a nit. After B1 is fixed (small) and B2 is decided, this is MERGE.

## Blocking

### B1. `check.sh` fresh-user probe now always FAILs on a healthy host
`host-setup/scripts/check.sh:761` still runs `env -i PATH="$PATH" HOME="$probe_home" docker buildx inspect --bootstrap` (empty environment). The shim no longer carries a socket default (`docker-buildx-shim.sh`: `MDT_SOCKET=${MDT_BUILDKIT_SOCKET:-}`), so with an empty environment it exits 2 ("MDT_BUILDKIT_SOCKET is unset"). Reproduced on the scratch copy: `env -i PATH=... HOME=... sh docker-buildx-shim.sh buildx inspect --bootstrap` prints that message, exit 2. Result: `mdt-host-check.sh` reports `FAIL a fresh user with an empty environment does NOT resolve mdt-managed` on every correctly installed host, and the message text ("builds started from VS Code's SSH server, cron or systemd leave dev-buildkitd.slice") is now wrong as a diagnosis. `install.sh` was fixed (probe injects `BUILDX_BUILDER/BUILDKIT_HOST/MDT_BUILDKIT_SOCKET`, :1403), `check.sh` was missed, and no test covers the check.sh probe (the smoke gate cannot see it).
Fix: have the check probe take the three facts from the published record (read `/etc/host-endpoints.d/mdt-host-setup.env` or `augeaswrap endpoints show`) and pass them like install.sh does; reword the FAIL; add a test that extracts `check_buildx_default_builder` with a stub docker and asserts the three variables reach it. Also update the docs sentence "a user with an EMPTY environment must resolve the managed remote builder" (check.sh comment, BUILD-ARCHITECTURE.md).

### B2. Scope/sequencing: commit 58ac28389 + `cmru.orchestration.toml` go beyond the brief and hard-require service endpoints that the controller does not have yet
The brief scoped H1g' to `mdt-cockpit/host-setup` deletions + the `mdt-host-setup` fragment. The branch also:
- rewrites `.devcontainer/devcontainer.json` (the controller's own devcontainer) and both cockpit templates to `${localEnv:...}` for mounts, `--group-add` and containerEnv, including `CGPROFILE_CONTROL_SOCKET_DIR`, `CGPROFILE_OBSERVER_*`, `TOPOS_OBSERVE_*`, `CGPROFILE_CONTROL_GID`, `TOPOS_OBSERVE_GID`. On the live host none of these exist until `cgprofiled install` and `toposd install` have published (code is on origin/main, nothing installed). A Docker `--mount` with an empty source fails, so the next rebuild of the controller devcontainer cannot start.
- changes root `cmru.orchestration.toml` to `BUILDX_BUILDER = "${BUILDX_BUILDER}"` / `BUILDKIT_HOST = "${BUILDKIT_HOST}"`. Per `cmru/src/cmru/config.py:1025-1051` an unset `${NAME}` without a default is a load error, so every cmru invocation (estate-wide: all tools' release/tester-gate, nyxloom workers, timers) in a context without those two variables now fails to load. The controller session has them set today (verified `env`), a systemd timer or a CI/worker context may not.
- adds 15 `required_env` names (CGPROFILE_*, TOPOS_*) to two mdt-cockpit run-gate lanes.
This is consistent with the "nothing hard-coded in consumers" ruling and the sockets plan phase 2, but it makes this branch not independently mergeable. Decision for the controller: either (a) merge it only as part of the phase-2 cutover (operator order below), or (b) split commit 3 and the cmru/run-gate edits into the cutover branch and merge only host-setup now. Not a code defect in itself; flagged because the dependency is invisible in the report ("Docs"/"Tests" only) and the host is production-shared.

## Answers to the requested checks

### (1) Refusal list vs the old code
Old install/uninstall wrote exactly (origin base 2caf58d1f): `/etc/tmpfiles.d/mdt-cgprofile.conf` (install.sh:1368), `/etc/tmpfiles.d/mdt-cgprofile-observer.conf` and `/etc/sysusers.d/mdt-cgprofile.conf` (`provision_cgprofile_observer` :263-264), `/etc/tmpfiles.d/mdt-topos.conf` (:1377), `/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf` (:1381-1383), `/usr/local/sbin/mdt-cgprofile-install.sh` (:1476), `/etc/profile.d/mdt-buildkit.sh` (:1481), the `# BEGIN mdt-buildkit` block (:1487). All seven files plus the block are in `legacy_host_setup_paths` / the block grep; none missed, none extra (the old uninstall README section never listed any of them). The list equals the sockets plan section 5.3 item 3 plus the profile.d script and the env block (the latter two follow rev2 R-7, which is right: a surviving block would duplicate or contradict the aggregate; the new aggregate uses a different marker `host-endpoints`, so the old block would not be replaced). Old-code leftovers that are NOT refused, correctly: `/opt/cgprofile`, `/etc/cgprofile/cgpd.toml`, `cgpd.service`, `cgprofile.slice`, `/usr/local/bin/cgpctl`, `/run/cgprofile`, groups, stale `CGPROFILE_*` keys in `/etc/mdt/host-setup.env` (the validator ignores keys it does not know; checked `validate_host_setup_values`). Those belong to `cgprofiled install`'s own refusals (cgprofile/docs/NATIVE-DEPLOYMENT.md section 3, plan P-11).
The live controller host was provisioned by these paths, so the new install.sh WILL refuse there until the operator removes them; that is the intended protection. The refusal also precedes `--admission-only`, `cpu-benchmark` and `--apply-protection` (brief: "before any change"; the plan text said unrelated roles stay unblocked; note for the operator, not a defect). `--dry-run-endpoints` and `--help` run before the refusal.
Operator removal on the live host (all non-destructive: `rm -f` of config files and one `sudoedit`; nothing touches `/run/*`, `/opt/cgprofile`, `/var/lib/*`, groups, containers or game-server state; safe to follow):
1. Do it in one sitting right before `install.sh`, and not across a reboot: `/etc/tmpfiles.d/mdt-cgprofile*.conf`, `mdt-topos.conf` and the topos drop-in are what recreate `/run/cgprofile`, `/run/cgprofile/observer`, `/run/topos` and the topos socket group at boot; after removal the running old cgpd/topos survive but a reboot before `cgprofiled install`/`toposd install` leaves them without runtime dirs. (Nit N3: the message says none of this.)
2. `sudo rm -f -- /etc/tmpfiles.d/mdt-cgprofile.conf /etc/tmpfiles.d/mdt-cgprofile-observer.conf /etc/tmpfiles.d/mdt-topos.conf /etc/sysusers.d/mdt-cgprofile.conf /etc/systemd/system/topos.service.d/50-mdt-socket-group.conf /usr/local/sbin/mdt-cgprofile-install.sh`, then `sudo systemctl daemon-reload` (not printed by the refusal; needed for the drop-in).
3. Last, immediately before install: `sudo rm -f -- /etc/profile.d/mdt-buildkit.sh` and `sudoedit /etc/environment` (remove only the `# BEGIN mdt-buildkit ... # END mdt-buildkit` lines). Then run `install.sh`, which republishes the same BUILDX_BUILDER/BUILDKIT_HOST values (same bytes as the old block) through the fragment.
4. Then `cgprofiled install` / `toposd install` per the plan cutover (they refuse on `/opt/cgprofile` leftovers etc.).

### (2) `/etc/environment` block removal, consumers, failure story
- Removal is by refusal, not by the installer: no stale duplicate is possible (the block is refused, not left beside the aggregate). Verified with the real augeaswrap library into a fake root: the aggregate block is `# BEGIN host-endpoints (generated ...)` with the eight keys, distinct from the old marker.
- Gap 1 (small): between the operator's block removal and the end of `install.sh` (publish at :1414, after BuildKit readiness + guard + probe) new PAM sessions have no `BUILDX_BUILDER/BUILDKIT_HOST`; the shim then selects mdt-managed but hard-fails for lack of `MDT_BUILDKIT_SOCKET` (never falls back to the built-in builder, so no ungoverned build, just a failed one).
- Gap 2 (real, undocumented, N1 below): processes that started before the cutover keep the old environment (`BUILDX_BUILDER`, `BUILDKIT_HOST` but no `MDT_BUILDKIT_SOCKET`). After the new shim is installed, `docker build` from them exits 2 with the clear message. That includes a VS Code Remote-SSH server started earlier, i.e. the exact 2026-10-22 incident path, and long-lived systemd units/services that never read `/etc/environment` (the old shim hard-coded the socket and served them; the new one fails them by design). Neither CHANGES nor the README says "log in again / restart the VS Code server / set the variables in unit files". Needs a CHANGES + README line (and optionally the shim could derive the socket from `BUILDKIT_HOST` when only that is set; that is a design choice, the doc line is the minimum).
- Publish failure after BuildKit changes: host ends with BuildKit running, guard enabled, new shim installed, fragment absent (fresh host) or the previous fragment (upgrade); builds fail with the shim's message; `install.sh` exits with augeaswrap's code and the later steps (memory/IO watchers, admission) are not run; re-running install is idempotent and heals. No line from host-setup tells the operator this (N5).
- Missing augeaswrap is refused before anything changes (`require_augeaswrap` at :252, ahead of the commit point and the optional apt installs; test-covered and mutant-killed). The probe at :1403 injects the three facts, so the fresh-user proof does not depend on the aggregate.

### (3) Fragment keys/values
`BUILDX_BUILDER=mdt-managed`, `BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock`, `MDT_HOST_DOCKER_GID`, `MDT_BUILDKIT_SOCKET`, `MDT_BUILDKIT_SOCKET_DIR`, `MDT_BUILDKIT_GID`, `MDT_DOCKER_API_SOCKET` (`/run/docker-api/docker.sock`), `MDT_DOCKER_API_SOCKET_DIR`. Names match phase-2 design 2.x table, the sockets plan (D7 and the GID section) and what `scripts/netcup/runner/lib.sh` on origin/main requires from the record; the BuildKit values are byte-identical to the old block. All values pass augeaswrap's value alphabet; I published the exact set through the real `augeaswrap.endpoints.publish(..., writer="mdt host-setup install", root=<fakeroot>)` and it was accepted and aggregated; the `check --json` status field the check.sh line reads is `clean`/`problems`, as used. `MDT_BUILDKIT_GID` is read from the live socket (design said `getent group mdt-buildkit`: that group does not exist yet, fine for this step). `DOCKER_GID` is not exported any more (templates now use `MDT_HOST_DOCKER_GID`); no other consumer in the repo needs it from host-setup.

### (4) `AUGEASWRAP_BIN`
No absolute path anywhere; `${AUGEASWRAP_BIN:-augeaswrap}` resolved with `command -v` in publish, withdraw, require and check.sh. Missing command is a refusal (exit 2) naming "install the augeaswrap wheel ... available on PATH before running host-setup/install.sh". Caveat (N4): the README says "Set AUGEASWRAP_BIN" but not that `sudo` resets it and root's PATH is the `secure_path`; and "augeaswrap 0.1.0" is named but no version is checked.

### (5) Tests and mutants (my own, on the scratch copy, relevant files only: test_install_runtime_blocks.py, test_docker_buildx_shim.py, test_cgprofile_install.py = 57 passed on the unmodified copy; plus test-render.sh)
Deleted tests: all deleted cases are about deleted behaviour (cgprofile installer/pin/drain, observer, cgprofile validators and check sections; test-render deleted only the (7) tmpfiles block and the five observer-only `pass` sections; the Docker-API line was adapted, not dropped). New tests drive the real helper under bash with a fake root and a recording `augeaswrap` stub (argv, stdin, env checked); the check.sh tests execute the extracted function. Order tests are source-text index comparisons (same style as the repo's existing ones).

| # | Mutation | Result |
|---|---|---|
| m1 | drop sysusers path from the refusal list | KILLED (1 fail) |
| m1b | drop topos drop-in path | KILLED |
| m1c | drop the `-L` (dangling symlink) test in the refusal | SURVIVED (symlink branch untested) |
| m2 | publish before BuildKit readiness/probe | KILLED |
| m3 | README uninstall: withdraw moved after the removals | KILLED |
| m4 | `require_augeaswrap` returns 0 when the command is missing | KILLED |
| m5 | delete the `check_host_setup_endpoint_record` call in check.sh | SURVIVED (test-render greps the function NAME, which the definition satisfies) |
| m5b | check.sh missing-command line silent | KILLED |
| m6 | dry-run no longer prints / takes the publish path | KILLED |
| m6b | dry-run key value corrupted | KILLED (2) |
| m7 | old-block detection regex broken | KILLED |
| m8 | `AUGEASWRAP_BIN` ignored in the helper | KILLED |
| m8b | `AUGEASWRAP_BIN` ignored in check.sh | KILLED (2) |
| m9 | refusal call removed from install.sh | KILLED |
| m10/m11 | BUILDX_BUILDER value / docker GID field wrong | KILLED |
| m12/m13/m14 | require call removed / publish call removed / wrong owner | KILLED |
Survivors are nits N2/N3 (no wiring test of the check.sh call; the symlink branch). The report's two mutants (refusal, order) reproduce as killed.

### (6) Gate and test runs
Final smoke log `<controller-program-dir>/logs/h1g-smoke-final.log` finished 16:02:42, after the last source commit 58ac28389 (15:59:08): `test-render: ALL OK`, 1442 passed, 1 skipped, 230 subtests, `verdict PASS; exit_code 0`, worktree clean at head. I did not re-run the gate. Locally I ran host-setup/tests/{test_cgprofile_install,test_install_runtime_blocks,test_docker_buildx_shim,test_hs02}.py and scripts/{test_devcontainer_audit,test_image_runtime_lane,test_runtime_lane,test_release_flow}.py: all pass. `test_buildkit_governance.py`/the governance step of test-render need `hypothesis` (absent in the devcontainer venv), and `test_cpu_benchmark_hostsetup.py`/one `test_local_dev.py` test need sibling trees missing in the scratch archive (same failures on the old tree, environmental); the gate log covers them. Note the smoke lane cannot catch B1 (no test runs check.sh's probe).

### (7) Remaining references
`git grep` of the deleted names: only history (assay review reports, sockets plan, backlog entries already marked SUPERSEDED, cgprofile's own refusal docs/installer that name the old files on purpose) and the refusal list/README cleanup commands. Nit N10: `KNOWN_ISSUES_TODO_BACKLOG.md` ~1125-1144 still describes `CGPROFILE_WHEEL`/`CGPROFILE_ENABLE`/`--force-cgprofile-restart` as current design text (no SUPERSEDED marker on that block). No `CGPROFILE_*`/`TOPOS_*` key left in host-setup.env.example, wizard, install.sh or check.sh (only the new `*_SOCKET*_GID` names in templates/run-gate.toml).

### (8) Merge
`git merge-tree --write-tree origin/main 58ac28389` (origin/main = 63a5cf3e0): clean, rc 0, tree fe63484f7e82; 50 files, +1135/-2836.

## Nits (non-blocking)
- N1 doc: add the "log in again, restart VS Code server, systemd units need the variables" cutover note (see (2) gap 2).
- N2 test: add a dangling-symlink case for the refusal (m1c).
- N3 message: the refusal should add `systemctl daemon-reload`, say that removal also drops the boot-time recreation of `/run/cgprofile`/`/run/topos`, and point to the cutover list (cgprofile/docs/NATIVE-DEPLOYMENT.md section 3, plan 5.3) so the operator removes these only as part of the cutover.
- N4 doc: `sudo` resets `AUGEASWRAP_BIN` (use `sudo env AUGEASWRAP_BIN=... ./install.sh`) and root's PATH; "0.1.0" is not enforced.
- N5 install: publish sits mid-install (:1414); on failure print one line "BuildKit is running, endpoint record not published, re-run install.sh" (or publish last).
- N6 note: the refusal blocks `--admission-only`/`cpu-benchmark`/`--apply-protection` too (consistent with the brief; the plan text suggested otherwise).
- N7 `--dry-run-endpoints` needs root, systemd and a live BuildKit socket, so it cannot preview on a fresh host; say so in the usage line.
- N8 writer string lacks a version (`mdt host-setup install`; augeaswrap documents "verb and version"; it is accepted).
- N9 test: add a test that check.sh actually calls `check_host_setup_endpoint_record` (m5).
- N10 backlog block ~1125-1144 mark SUPERSEDED.
