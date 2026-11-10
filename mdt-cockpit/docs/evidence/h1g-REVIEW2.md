<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/h1g-REVIEW2.md -->
<!-- relation: H1g re-review; verbatim source text follows -->

# H1g' re-review (fix round), branch h1g-20261108, head 70885e1c9288e502e3ab97975739e6545e8afdda

Reviewer: same fresh session as h1g-REVIEW.md. Scratch archives under <controller-scratch>/rv-h1g/ (new2 = head, base2/chkmain = origin/main 72ae0ddc0, mut2 = mutants). Worktree untouched, install.sh never run, no gate run (log is present and fresh).

## Verdict: MERGE

B1 and B2 are resolved. Remaining items are nits, none blocks.

## (1) Net diff vs origin/main is host-setup only: confirmed
`git diff origin/main...70885e1c9` = 32 files. Outside `mdt-cockpit/host-setup/` only these 8, all judged fine:
- `mdt-cockpit/CHANGES.md`, `README.md`, `docs/{BUILD-ARCHITECTURE,CONSUMERS,DESIGN-GUIDE}.md`, `KNOWN_ISSUES_TODO_BACKLOG.md`: docs about host-setup (retired provisioning, fragment, refusal, log-in-again note, SUPERSEDED markers). I read every net hunk: none claims that the templates, cmru or run-gate read the published facts; DESIGN-GUIDE says explicitly that moving consumers is a later phase-2 cutover and "until then the consumers keep their literal defaults".
- `mdt-cockpit/run-gate.toml`: one line, adds `test_cgprofile_install.py` to the `host-setup-admission` lane (host-setup test file; the 15 `required_env` additions are gone).
- `mdt-cockpit/scripts/test_devcontainer_audit.py`: the three tests that read the deleted `units/mdt-cgprofile.conf` / `mdt-topos*.conf` now assert the literal `/run/cgprofile` and `/run/topos` template mounts and that host-setup no longer owns them. Needed (those files are deleted), consumer behaviour unchanged, 13 pass.
Zero net diff in: `.devcontainer/devcontainer.json`, `mdt-cockpit/templates/**`, `cmru.orchestration.toml`, `mdt-cockpit/tests/**`, `ensure-release-builder.sh`, `finalize_container_environment.py`, `mdt_buildkit_builder.py`, the other consumer tests. I also verified on the actual merge result (`git merge-tree --write-tree origin/main 70885e1c9` tree 9ad82023d413): those paths have the same tree/blob IDs as origin/main.
Live-controller consequence: cmru keeps `${BUILDX_BUILDER:-mdt-managed}` / `${BUILDKIT_HOST:-unix:///run/mdt-buildkitd/buildkitd.sock}` (unset variables still load), the devcontainer template/`.devcontainer` still mount `/run/cgprofile`, `/run/topos`, `/run/mdt-buildkitd` literally (rebuild needs no `CGPROFILE_*`/`TOPOS_*` values), run-gate lanes keep main's `required_env`, the release builder keeps its defaults. The only consumer-visible behaviour change that remains is the intended host-setup one: the host's Buildx shim has no socket default, so a host-side build from a session without `MDT_BUILDKIT_SOCKET` exits 2 with a clear message. The cockpit image installs the packaged plugin, not the shim (no Dockerfile reference), and the netcup runner paths pass `BUILDX_BUILDER=default MDT_BUILDX_PASSTHROUGH=1` or the explicit variables, so I found no in-repo consumer broken by it. This is documented (host-setup README "After the cutover, log in again", CHANGES, BUILD-ARCHITECTURE, refusal text).

## (2) B1 fixed
`check.sh` `check_buildx_default_builder` reads `BUILDX_BUILDER`, `BUILDKIT_HOST`, `MDT_BUILDKIT_SOCKET` with `_host_setup_endpoint_fact` from `/etc/host-endpoints.d/mdt-host-setup.env`, FAILs ("run host-setup/install.sh") before touching docker when any is empty, and passes the three into the `env -i` probe; FAIL text no longer claims an empty environment. Key-prefix safety checked (`^MDT_BUILDKIT_SOCKET=` does not match `..._SOCKET_DIR=`). New `FreshUserBuilderProbe` tests execute the real extracted function with a shim-like fake docker; passed. Same-code question: not literally one function. install.sh derives the facts from live host state before publishing and check.sh reads the published record afterwards (that is the point of a check), but both pass the same three variables into the same `env -i PATH HOME` probe, so the shapes cannot diverge unnoticed (mutants a/j below each kill on their side).

## (3) Cutover patch (reports/h1g-cutover-pieces.patch, 26 files)
- `git apply --check` on head 70885e1c9: all 26 apply.
- On origin/main: 23 apply; three do not: `docs/DESIGN-GUIDE.md`, `host-setup/tests/test_buildkit_governance.py`, `scripts/test_devcontainer_audit.py` (the agent only named DESIGN-GUIDE; the other two are the adapted tests, also post-split state). Apply the patch after the branch merges.
- Inverse check: applying the patch on the head reproduces the pre-revert state byte for byte (sha256 vs 58ac28389) for 24 of 26 files (devcontainer.json, cmru toml, templates, run-gate.toml, builder scripts, all consumer tests and docs). The two that differ are the intentional later edits in the mixed docs: BUILD-ARCHITECTURE.md (log-in-again note, probe wording) and CONSUMERS.md (the wizard memory-policy paragraph that round one had deleted by accident, now restored from main). So the patch is exactly the reverted consumer behaviour. I did not run the consumer lanes after applying it (scope: skip heavy runs).

## (4) Survivors, refusal message, docs
- Check.sh call removal: now killed (`CheckScriptWiring`, mutants b and h). Dangling-symlink branch: killed (mutant c, 7 failures).
- Refusal text (exercised on a fake root with topos tmpfiles, the profile.d script and the old block): says "in one sitting", states the boot-time caveat (tmpfiles/sysusers/drop-in recreate `/run/cgprofile`, `/run/cgprofile/observer`, `/run/topos`; a reboot before `cgprofiled install`/`toposd install` leaves the old daemons without runtime dirs), points to NATIVE-DEPLOYMENT section 3 and plan 5.3, orders service files, then `systemctl daemon-reload` (only printed when a service file is listed), then the profile.d script and `sudoedit /etc/environment` last, then install.sh, then the service installers. Every command is `rm -f -- <exact retired path>` or `sudoedit`; it states and I confirmed nothing touches `/run/*`, `/opt/cgprofile`, `/var/lib/*`, groups, containers or game-server state. Safe to follow.
- Docs: "log in again" note present in CHANGES (line 14), host-setup README ("After the cutover, log in again"), BUILD-ARCHITECTURE; `sudo env AUGEASWRAP_BIN=...` documented; SUPERSEDED markers on backlog entries at lines 583, 1119, 1132, 1245.

## (5) Mutants re-applied (on a scratch copy of the head, files test_cgprofile_install.py + test_install_runtime_blocks.py)
| mutant | result |
|---|---|
| a: drop `MDT_BUILDKIT_SOCKET` from check.sh probe env | KILLED |
| b: remove the `check_host_setup_endpoint_record` call | KILLED |
| c: drop the `-L` dangling-symlink branch | KILLED (7) |
| e: remove the `systemctl daemon-reload` line from the refusal | KILLED |
| f: neutralise the missing-facts guard in check.sh | KILLED (3) |
| g: read facts from the wrong record file | KILLED (2) |
| h: remove the `check_buildx_default_builder` call | KILLED |
| i: reword "do not reboot in between" | survived (phrase not asserted); the asserted phrase "a reboot before" mutant is KILLED |
| j: drop `MDT_BUILDKIT_SOCKET` from install.sh's probe env | KILLED |
8 of 8 behavioural mutants killed; only an unasserted sentence fragment survived (not a guard).

## (6) Tests, gate, merge
Local single-file runs on the head (nice -n 10): test_cgprofile_install 20, test_install_runtime_blocks 24, test_docker_buildx_shim 19, test_hs02 30, scripts/test_devcontainer_audit 13: all pass. test_buildkit_governance and the governance step of test-render.sh need `hypothesis` (absent in the devcontainer venv): covered by the smoke log. `logs/h1g-fix-smoke.log` finished 16:55:34, after the head commit (16:54:10): `test-render: ALL OK`, 1447 passed, 1 skipped, 239 subtests, `verdict PASS; exit_code 0`; worktree clean at 70885e1c9. `git merge-tree --write-tree origin/main 70885e1c9`: clean (rc 0).

## Nits (non-blocking)
- The first-round sentence "The cockpit image carries the cgprofile wheel's clients (`cgprofile`, `cgprofilectl`; MDT-048)" is gone from CONSUMERS.md (the report already lists this); restore when convenient.
- Two host-setup commits (064ca0916 etc.) still carry the consumer pieces in history; net tree is correct, so squash or plain merge are equivalent.
- The `host-setup-admission` lane now runs `test_cgprofile_install.py`, whose name no longer describes its content (retirement and endpoint tests); rename later.
- Carried over: writer string has no version (accepted by augeaswrap), no shim fallback from `BUILDKIT_HOST` (design choice), `--dry-run-endpoints` needs a live BuildKit socket (documented).
