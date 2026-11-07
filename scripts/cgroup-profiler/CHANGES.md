# Changelog — cgroup-profiler

All notable changes to this project are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before
the release gate runs. Normative daemon behavior lives in `DESIGN.md` and
`docs/PROTOCOL.md`; entry-by-entry rationale lives in
`nyxloom-trove/backlog/` and git history.

## [Unreleased]

<!-- cmru: release history -->

## [2.2.0] - 2026-11-07
<!-- cmru: generated -->
<!-- cmru: source-end=e9804bf29ebbf49b238df7f6a789096e0f9f9bf3 -->

### Added
- feat(cgroup-profiler): add socket-activated host service (9a8f65b9a)

### Fixed
- fix(cgroup-profiler): align fix5 tests and docs (138acdbdd)
- fix(cgroup-profiler): address socket round review findings (611747312)
- fix(cgroup-profiler): VM acceptance script refuses anything but a fresh harness guest (R2-3) (7bf66ba9e)
- fix(cgroup-profiler): installer round-2 review (R2-1 R2-2 R2-4 R2-5 R2-6 R2-7 R2-8 R2-9, --no-deps) (98752a4c9)
- fix(iogovmon): track source install versions (66a4d1609)
- fix(cgroup-profiler): cgpd doctor aligned with toposd doctor (24f7447db)
- fix(cgroup-profiler): cgpd install and uninstall safety (H2 H3 H4 M1 M4 M5 M6 M7 L2 L3 L4 L5) (a8d712269)
- fix(cgroup-profiler): cgpd.service does not restart a refusal exit (M3) (95b4652fe)
- fix(cgroup-profiler): repair the red suite and the self-bind observer regression (673115e5b)

### Changed
- cgroup-profiler: enforce dedicated socket groups (9e34b4143)
- Merge origin/main into sockets-cgp-20261105 (673b6a85e)
- refactor(cgroup-profiler): delete prepare_registry_lock_file (L7) (98ef319eb)
- Merge remote-tracking branch 'origin/main' into sockets-cgp-20261105 (f94910104)
- chore: retire modern-debian-tools-python-debug; mdt-cockpit is its successor (2015c4e00)

### Documentation
- docs: reconcile RG55 socket contract text (98c0a860c)
- docs: reconcile RG55 socket contract mirrors (651d0c39f)
- docs(cgroup-profiler): VM acceptance guard wording (18a4ca89a)
- docs(cgroup-profiler): installer and uninstall contract after the fix round (b0c2a4150)
- docs(cgroup-profiler): show installed cgpd path (985c0c017)

### Testing
- test: cover duplicate missing installer users (3a22e0e77)
- test(cgroup-profiler): add round 3 review guards (dc4a8c985)
- test(cgroup-profiler): cover the remaining new socket-activation code (100 percent line and branch) (5a0d92cf7)
- test(cgroup-profiler): guest-side VM acceptance script for the review's 10-case list (41d097d91)
- test(cgroup-profiler): an activated daemon never unlinks its sockets; observer-not-docker guard (6609db393)
- test(cgprofile): kill the four 2.1.0 R2 survivors (machine.py 57/257/263, metrics.py 235); CP-60 (8a3028c8f)

<!-- cmru: pending release notes begin -->
### Added
- feat(cgprofile): systemd-owned per-level control and observer sockets, `cgpd install` / `uninstall` / `doctor`, and explicit `--self-bind` for tests and development (phase 1, plan 5.1)
- feat(cgprofile): repeatable `cgpd install --add-user LEVEL=USER`; existing users are added with `gpasswd -a`, recorded for reruns and never individually removed on uninstall
- feat(cgprofile): `cgpd -t` config-test alias for systemd `ExecStartPre`

### Fixed
- fix(cgprofile): monitor gaps are unknown and paused, and stall kills require fresh sustained evidence plus exact counter confirmation (CP-62)

### Changed
- breaking(cgprofile): socket defaults move to `/run/cgprofile/control/cgprofile.sock` and `/run/cgprofile/observer/cgprofile.sock`; old config keys are refused and clients have no legacy-path fallback
- docs(cgprofile): document P-11 manual cleanup, phase-1 rollout boundary, and systemd socket ownership
- fix(cgprofile): `cgpd install` reruns with a saved `install.toml` that lists users, keeps an existing `cgpd.toml` (merging only socket groups and flag keys), refuses a foreign `/usr/local/bin/cgpctl`, and confirms unless `--yes`; `PENDING` is written first and removed last
- fix(cgprofile): `cgpd uninstall` needs `install.toml` or `PENDING` and refuses unknown or old-layout paths before any change, stops `cgprofile.slice`, reloads systemd right after the unit files, skips groups that no longer resolve, and removes `PENDING` then `install.toml` last so an interrupted run can be rerun
- fix(cgprofile): `cgpd.service` has `RestartPreventExitStatus=2`, so a refusal exit is not restarted; a failed self-bind observer setup no longer leaves a closed socket assigned
- fix(cgprofile): `cgpd doctor` skips unreadable paths for non-root callers and `foreign-rules` runs the whole old-installation scan
- fix(cgprofile): `cgpd install --force-restart` proceeds with a warning when the session list is unavailable; groups created by an interrupted first install are named in `PENDING` so `uninstall --remove-groups` removes them; with no flag a hand-edited socket group in `cgpd.toml` wins over the install record; a dry run needs no `--enable`; the wheels install with `--no-deps`
- fix(cgprofile): `cgpd uninstall` keeps `current` until the running tree is removed, continues past a `groupdel` failure and reports it, only ever removes the two default groups, and `install` never prunes the tree it runs from
- fix(cgprofile): both socket groups reject Docker by name or GID, GID 0, and same-service duplicate GIDs on every install/config input path; `cgpd doctor groups` reports the group IDs and members, including recorded users
- fix(cgprofile): reinstall drops and reports recorded users whose accounts are gone, while a user named with `--add-user` must still exist; `cgpd doctor groups` warns with a rerun remedy
- fix(cgprofile): restrict `cgpd -t` to the standalone config check forms and describe P-5 refusals by group, root-equivalent cause, and dedicated-group remedy
- fix(cgprofile): deferred uninstall failures give manual tree, `groupdel`, and install-record cleanup steps instead of asking operators to rerun a cgpd binary that may have been removed
- refactor(cgprofile): remove the unused `damon.prepare_registry_lock_file`

### Tests
- tests(cgprofile): kill the four 2.1.0 R2 survivors in machine.py/metrics.py (CP-60)
<!-- cmru: pending release notes end -->

## [2.1.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=a12b0504d436db2a4c3ef268b53629259d1afb90 -->

### Added
- feat(assay): R2 lanes declare jobs_max and run-gate jobs=auto (assay-jobs audit) (fe53428a8)
- feat(cgprofile): machine/1 facts, persisted host CPU benchmark reader, score table fallback, steal time (rigor CP-1, CP-58) (8fb865420)

### Changed
- Merge branch 'cgpd-rollout-20261105' (cd62d0299)
- backlog: CP-57 (doctor caller-group fails for root), MDT-062 (cgprofile 1.x container cutover) (bff860d9b)
- cgroup-profiler: retire the eb3af770 R2 base, document per-release request base (0f9ef3181)

### Testing
- test(cgprofile): host-facts isolation fixture must not share the monkeypatch instance (CP-58) (215f4518a)

<!-- cmru: pending release notes begin -->
### Added
- Machine facts (`machine/1`, rigor-evidence CP-1, CP-58): `host.machine`, `host.steal_seconds`, `host.cpu_seconds_total` in the `host` verb, the
  `status` host block and the Summary host block (additive; Summary v1.2). The CPU speed is read from the persisted
  `/var/lib/mdt/host-facts/cpu-benchmark.json` at start and on reload (accepted only while CPU model and CPU count match; never measured, the claim
  file is never opened), else looked up in the packaged `cgprofile-scores/1` table by CPU model and Geekbench major, else `null` with a reason.
  Per-major baselines (6 and 7: 2500). The table ships empty (CP-59); refresh procedure in `docs/SCORES-TABLE.md`; `docs/PROTOCOL.md` section 11.
- `metrics.sample_host` cpu block gains `steal_jiffies` and `accounted_jiffies`.

### Fixed
- R2 lane base: the recorded pre-wave base `eb3af770` exceeds Assay's 64 MiB git output bound (GIT_FAILED). `docs/CONSUMERS.md` now names the
  previous release tag as the request base, to be refreshed per release (`cgprofile-v1.2.0` plans 557 candidates).
<!-- cmru: pending release notes end -->

## [2.0.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=4892657ea01c60031735fecb8f2bcb4751091926 -->

### Added
- feat(cgprofile): package 1 of 2.0, src/cgprofile wheel layout (CP-46) (1913c055d)

### Fixed
- fix(run-gate): RG-154 probe cowtree from the pinned pyz, name cheap_clone_reason; RG-155 assay lane budget in header; CP-56 canary tar race (vbpub canary-run.sh) (fc0ac39c9)
- fix(cgprofile): serve reaches _limits_snapshot via cli.legacy, not the package (CP-46) (6edeb3c50)

### Changed
- Merge branch 'fix-cgp-rel-20261104' (4892657ea)
- cgprofile: 2.0.0 ships the wheel only; retire the OCI image from the release contract (CP-44, CP-54) (4ac559f9b)
- Merge branch 'fix-rg-154-20261104' (cf087adc8)
- cgprofile tests: image-recipe doc pins follow the native docs (df34b22d7)
- cgprofile: CHANGES [Unreleased] and backlog statuses for package 5 (CP-45, CP-49) (6e86c5c79)
- cgprofile: CP-45 docs-sync test and removed-name grep (CP-49) (b75ba12d6)
- cgprofile docs: DESIGN and PROTOCOL residual daemon wording (35a04df67)
- cgprofile docs: CONSUMERS to the native service, migration table (CP-49) (146e9f18a)
- cgprofile docs: README product overview (CP-45), cgpd run explicit in CGPCTL-SPEC 3.1 (9e56cd6c8)
- cgprofile docs: DESIGN-GUIDE slice-proof section native (9362fa446)
- cgprofile docs: DESIGN-GUIDE to the native service (11ecea78e)
- cgprofile docs: DESIGN DAMON notes; package 5 continuation brief (6279e4ad7)
- cgprofile docs: DESIGN to the native model (55ccc2eb5)
- cgprofile docs: ATTACH-GUIDE and PROTOCOL to the native cgpctl/cgpd model (6924a2fb0)
- cgprofile: cover the bare-uid peer fallback in _handle_connection (be7b29391)
- cgprofile: CHANGES [Unreleased] and backlog statuses for package 3, file CP-54 and CP-55 (0e44dbaf6)
- cgprofile: retire tests/_ctl_reference.py and the legacy serve/report shims, Dockerfile entrypoint to cgpd, report-removed goldens (CP-48) (3f0e314b4)
- cgprofile: cgpd unit print/check, the unit renderer, template restart steps and venv parameter, golden units (CP-48) (5f8dd91f2)
- cgprofile: cgpd cli-extended entry (run with the seven preflight checks, config check, reload, SIGHUP reload, single-daemon guard) (CP-48, CP-50) (bef71678a)
- cgprofile: the export streaming verb (PROTOCOL 10.3), contract-2 hand-off, own slot class, retention skip, goldens (CP-48) (837194e67)
- cgprofile: tests and contract-2 goldens for the cgpd serve plumbing (negotiation, ownership, gc, status states, config verbs, settings apply) (d9347fe21)
- cgprofile: ctl contract 2 plumbing in cgpd serve (negotiation, ownership, config verbs, gc args, status states), report removed, exec carrier false (CP-48, CP-50, CP-52) (1c7df3d53)
- cgprofile: fix the CP-53 flake, the accept-loop test keys its crash on the request and waits for both handler threads (a6b354d4a)
- cgprofile: daemon_config, the cgpd settings schema, file loader, reload and runtime overrides (CP-48, CP-50) (994ecb972)
- cgprofile: keep the wheel-install test clear of the audit's no-path-hacks heuristic (AC-25) (345eb363e)
- cgprofile: the wheel-install test builds cli-extended from the checkout offline so the clean venv can import it (89fe40601)
- cgprofile: CHANGES [Unreleased] for cgpctl and the 1.x client removal; CP-47 progress note, INDEX regenerated (5be5f584d)
- cgprofile docs: README, ATTACH-GUIDE sections 0-3 and 8, CONSUMERS front door and target resolution describe cgpctl; the 1.x collector sections stay marked for the package-4 docs sweep (48bda4fd6)
- cgpctl: cli-extended surface (pyproject config, CLI-SPEC.md, manifest, 171 reviewed cases against the fake daemon, contract and audit tests); watch prints the end line's reason and the fake daemon no longer sends an ack line the real one never sends (9a3ed21f5)
- cgprofile: delete the 1.x client surface (run/attach/mark/targets/doctor/ctl, helper container, log tailers, bash shim, setup.sh); legacy.py keeps only _collect, serve and the report subprocess; frozen 1.x ctl client kept as tests/_ctl_reference.py for the contract-1 daemon goldens (package 3 retires it) (4f5aa66d1)
- cgprofile: tests for direct mode (100 percent) (1ce82ba96)
- cgprofile: cgpctl tests against a fake daemon (100 percent of cgpctl.py); show falls back from live to stored; probe cached on the Namespace; stop takes --yes (61e2fb966)
- cgprofile: tests for ctlwire (100 percent) (31fa96441)
- cgprofile: cgpctl show reads finished summaries via export; file CP-52 (gc args) and CP-53 (flaky accept-loop test) (ae4ad0908)
- wip(cgprofile): package 2 checkpoint 2, cgpctl registry, direct run, skill move (untested) (73a9e4fbb)
- wip(cgprofile): package 2 checkpoint, ctlwire client module (untested) (b1da96ca9)
- Merge branch 'feat-cgp2-pkg1-20261104' (53885072c)
- refactor(cgprofile): remove lib/ and cgprofile.py, superseded by src/cgprofile (CP-46) (0cb4b8eeb)

### Documentation
- docs(cgprofile): runtime config-set/config-reset with a persisted overrides layer (CP-50) (7dc73f794)

### Testing
- test(cgprofile): layout, DAMON-sync and clean-venv wheel-install tests (CP-46) (850b668c5)
- test(cgprofile): convert the suite to the cgprofile package, add layout, DAMON-sync and clean-venv wheel tests (CP-46) (1f890d756)

<!-- cmru: pending release notes begin -->
### Changed
- Release contract (CP-44, CP-54): cgprofile 2.0.0 ships the wheel only. `cmru.toml` now declares `artifacts = ["wheel"]`; the
  daemon-image build, the image push and the `oci-image-release` handler are removed (the handler would have overwritten the
  `wheel-publish` GitHub Release body, so CMRU refused the plan). The OCI image is no longer published from 2.0.0; the 1.x
  container stack cannot start under the `cgpd` entrypoint. The Dockerfile and compose files stay in the tree for package 9 to delete.

### Added
- `cgpctl`, the cli-extended client (CP-47, cgprofile 2.0 package 2): `status`, `host`, `sessions`, `show`, `start`, `stop`, `watch`, `report`,
  `prune`, `run`, `mark`, `config show|set|reset`, `doctor`, `skills`. Socket `--socket` > `$CGPCTL_SOCKET` > `/run/cgprofile/ctl.sock`; contract
  negotiation through `version` (contract 2 verbs answer `contract-unsupported` from a contract-1 daemon, so `sessions`, `show`, `start`, `prune`,
  `config` work only once the 2.x daemon of package 3 is deployed); `report` exports the stored session from the daemon, verifies the digests and
  renders locally; `run` is the daemonless direct mode (`src/cgprofile/direct.py`, `self@follow`); `src/cgprofile/ctlwire.py` is the wire layer.
- The reviewed command surface: `[tool.cli-extended]` in `pyproject.toml`, `docs/CLI-SPEC.md` (generated region), `docs/cli-surface.json`,
  `docs/cli-review.toml` (171 cases run against a fake daemon by `tests/test_cli_review_cases.py`), `tests/test_cgpctl_contract.py`
  (`assert_cli_contract`, `surface check`, `cli-extended audit` with no `fail`).
- The agent skill is `src/cgprofile/skills/cgpctl/SKILL.md`, installed with `cgpctl skills install`.
- `cgpd`, the native daemon entry on cli-extended (CP-48, CP-50, cgprofile 2.0 package 3): `cgpd run` with seven preflight checks, `config check
  [--file]`, `reload [--reset-overrides]` and SIGHUP reload, a single-daemon guard (`probe_daemon`: version and pid from SO_PEERCRED).
  `run` is an explicit verb: cli-extended has no no-argument default, so the unit's ExecStart is `cgpd run` (CGPCTL-SPEC 3.1 says "default
  verb"; the spec sweep is package 4).
- `src/cgprofile/daemon_config.py`: the `cgpd.toml` settings schema, loader, reload and the persisted runtime overrides layer.
- Ctl contract 2 in the daemon (PROTOCOL section 10): negotiation through `version`, session ownership per peer uid, `config`, `config-set` and
  `config-reset`, `gc` with `dry_run`, `keep` and `keep_days` (CP-52), `status --state`, and the streaming `export` verb (own slot class,
  `O_NOFOLLOW`, retention skips an exporting session). Contract-1 goldens stay byte-identical except the changes listed under Removed and Changed.
- `cgpd unit print [--unit --install-root --wheel-venv]` and `cgpd unit check [--installed PATH ...]` (`src/cgprofile/unitfiles.py`); golden
  units `tests/fixtures/units/{cgprofile.slice,cgpd.service}`; the service template gains the venv ExecStart token, RestartSteps,
  RestartMaxDelaySec and StartLimit*.
- Contract-2 goldens (`config*`, `config-set*`, `config-reset*`, `version-c2*`, `status-c2*`, `export-*`) under `tests/fixtures/rg55/socket/`.

- Docs for the native model (CP-45, CP-49, cgprofile 2.0 package 5): README "Product overview" (what it is, commands, config keys, sockets,
  consumers, access, protection, install), CONSUMERS.md with the 1.x-to-`cgpctl` migration table and the native-service chapter, ATTACH-GUIDE,
  PROTOCOL, DESIGN and DESIGN-GUIDE rewritten for `cgpctl`/`cgpd`; estate docs (mdt host-setup and templates, ciu v8 examples, debian-install-v2
  usage guide) name `cgpctl` and the native service. `tests/test_docs_sync.py` keeps the README tables in step with the cli-extended registries,
  the `daemon_config` schema, the sockets and the group names, and keeps the removed 1.x names out of the live documents.

### Fixed
- `tests/test_serve.py` accept-loop flake: the crash injection is keyed on the request (CP-53).
- CGPCTL-SPEC 3.1: `cgpd run` is an explicit verb, not a default.

### Removed
- The 1.x client surface (CP-47): `cgprofile run/attach/mark/targets/doctor/ctl`, the bash shim `./cgprofile`, `setup.sh`, helper-container mode,
  the placement probe and the `docker logs` tailers (`logtail.py`). `src/cgprofile/cli/legacy.py` keeps only `_collect`.
- Package 3 (CP-48): `tests/_ctl_reference.py` (users moved to `ctlwire` and a wire oracle in the tests), the legacy `serve` and `report`
  entries, `serve.parse_allow_uids` and the `CGPROFILE_ALLOW_UIDS` variable (a stale value is refused at start; the access list is `allow_uids`
  in `cgpd.toml`), and the 1.x `serve` flags.
- The contract-2 `report` verb: a daemon at contract 2 answers `report-removed` (render with `cgpctl report`, which exports and renders locally).
  The contract-1 golden is renamed `report-removed-{request,response}.json`.
- The `exec` carrier: `transports.exec` is `false` at contract 1 and absent at contract 2 (PROTOCOL 10.1 controller decision; a deviation from
  the 1.x literal `true`).

### Changed
- The `peer-refused` text is now "peer uid N is not in allow_uids". Refusals and busy lines sent before the request parses are stamped contract 1.
- The 1.x ciu container stack (`ciu.compose.yml.j2`, `ciu.defaults.toml.j2`, `CGPROFILE_PID_NAMESPACE_MODE` / `CGPROFILE_ALLOW_UIDS`, `--sessions`
  flags) no longer starts: the Dockerfile entrypoint is `python3 -m cgprofile.cli.cgpd` with `CMD ["run"]`. Migration is CP-54; the cgroup/slice
  observe targets of PROTOCOL 10.4 are not served yet (CP-55).
- (Superseded by the cgpd entrypoint above.) The image entrypoint is `python3 /opt/cgprofile/src/cgprofile/cli/legacy.py serve` (the `cgprofile` symlink and `COPY cgprofile` are gone), so
  `docker exec cgprofile-host-daemon cgprofile ctl ...` no longer works: consumers use `cgpctl` over the socket (CONSUMERS.md migration table).
  The 6th canary of `canary-run.sh` replaced the logtail canary (7 to 6).
- `cgpctl watch` prints the terminal `end` line with the daemon's `reason`.
- Gate: `tools/gate.sh` puts `libraries/cli-extended/src` on `PYTHONPATH` because the gate image carries cli-extended 0.5.0 while the pin is `>=0.5.1`.
- Notes: `cgpctl show` falls back from live to stored; `stop` asks for confirmation only when uid 0 stops a session owned by another uid (`--yes` skips it); `prune`
  previews with a dry-run `gc` and refuses (exit 2) to delete without `--yes` when stdin is not a terminal (the `gc` dry-run arguments are CP-52).
<!-- cmru: pending release notes end -->

## [1.2.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=088c923dbf11d1b749eb3c254ec107a4ce6bdaba -->

### Added
- feat(run-gate)!: reach the cgprofile daemon over the control socket only (RG-154) (c14b897f9)

### Fixed
- fix(cgprofile): fork-race foreign count, own report cap, shutdown inside the stop grace (delta review M1-M3) (dbd2f4740)
- fix(cgprofile): control socket safety and concurrency (CP-34, CP-31, CP-33) and finalize failure handling (CP-27 N9-N11) (05ea66bda)
- fix(cgprofile): lock pins coverage; contract test for extras vs lock (CP-32) (b23a13f0a)
- fix(cgprofile): stop says session lock busy, not worker did not exit; pin one lock acquire (CP-27 N5 N6 N8) (51d42dad3)
- fix(cgprofile): refuse an unknown @option early (CP-38) (84666766d)
- fix(cgprofile): strict kernel-integer parsing of cgroup files (CP-37) (2d639675d)

### Changed
- Merge main into feat-rg-socket-only-20261104; renumber RG-154 to RG-156 (main's RG-154/155 are the dstdns pilot findings); move CHANGES entry to Unreleased (088c923db)
- Merge branch 'backlog-platform-20261104' (e69a27940)
- cgprofile backlog: wheel release, host-unit with protective limits, socket access model, ciu retirement, product docs (CP-41..45) (a96a0c046)
- backlog(cgprofile): CP-39 and CP-40 implemented in branch fan-cgp-20261103, note on CP-23 (1437903f7)
- cgprofile: observer contract 3 measurement windows and ancestor memory events (CP-39, CP-40) (2d1f0a6cf)
- Merge branch 'req-renames-20261103' (1eaaf51c3)
- chore(cgroup-profiler): rename report requirements lock (53723783e)
- chore(cgprofile): pin deployed daemon image to published 1.1.0 (49f272a04)

### Documentation
- docs(cgprofile): package 0 of cgprofile 2.0, cgpctl/cgpd CLI spec, ctl contract 2, native deployment design (a615cc6c0)
- docs(cgprofile): shutdown grace, report cap, 1.2.0 release notes, CP-33/39/40 backlog statuses (844855546)
- docs: close CP-27, CP-31, CP-33, CP-34 in the cgprofile backlog (fix-cgp-1) (7ec70ae66)
- docs(cgprofile): close CP-37/38/32, CP-27 N5/N6/N8, and stale CP-18/19/20/21 (c6598df4e)
- docs(cgprofile): observer contract 3 windows and the ancestor-events Summary key (CP-39, CP-40) (dcb916001)
- docs: every dependency manifest carries a use-case suffix; document .[test] origin of requirements-report.txt (c579bd3c6)
- docs(cgprofile): file CP-39 (observer contract 3 windows) and CP-40 (ancestor memory events) (f35cde799)

<!-- cmru: pending release notes begin -->
- Package layout (CP-46, cgprofile 2.0 package 1): the project is now the `cgprofile` wheel. Modules moved from `lib/` to `src/cgprofile/` (flat), the 1.x entry `cgprofile.py` to `src/cgprofile/cli/legacy.py` (the `cgprofile` shell shim keeps working), console scripts `cgpctl` and `cgpd` are interim stubs over the 1.x code path (`cgpctl <verb>` = `ctl <verb>`, `cgpd run` = `serve`). The version comes from the `cgprofile-v` tag via setuptools-scm; an uninstalled source tree reports `0.0.0-dev`. The only runtime dependency is `cli-extended>=0.5.1`. The DAMON analysis module is vendored at `cgprofile/_damon/damon_analysis.py` (GPL-2.0-only header kept; see `NOTICE` and CP-51). `cmru.toml` now builds and publishes the wheel next to the OCI image.

### Added
- Observer contract 3 (CP-39): `window-open` and `window-close` on the observer socket return per-subject measurement windows (peak RSS, CPU, I/O bytes and operations, memory and swap peaks, PSI) in `process-tree` mode (works in a shared container) or `cgroup` mode (exact while the leaf holds nothing foreign; otherwise `SHARED_CGROUP`). Every null metric carries a reason code. `version.resource_observer` gains `"contracts": [2, 3]` while `"contract"` stays 2; contract 2 and `resource-limits` are unchanged. New error codes `window-refused` (reason `limit`) and `window-lost`. Fixtures: `tests/fixtures/rg55/observer-window-v3-*.json`. Release as 1.2.0 together with CP-40.
- Summary key `ancestors_memory_events_local` (CP-40): the increase in local `high`, `max`, `oom` and `oom_kill` of each ancestor up to `dev.slice` over the session, and the same list in every window's `context`, so a consumer can tell reclaim driven from above the lane from the lane's own limit. Optional key; the Summary schema stays v1.1. Fixture: `tests/fixtures/rg55/summary-v1.1-ancestors.json`.

### Fixed
- CP-37 (2d639675d): `read_int`, `read_kv` and `read_flat_keyed` parse the kernel's decimal grammar only (ASCII digits, a leading `-` where signed); `1_000`, `+5` and Unicode digits are skipped, and an `io.stat` line that repeats a key is dropped instead of last-wins.
- CP-38: an unknown word after `@` is refused early: `slice:` rejects `@` in the unit name at parse time, and a `cgroup:` or bare target that names no cgroup and ends in an option-looking `@word` is refused with the suspect segment named and the trailing-`@` escape mentioned.
- CP-32: `requirements-report.txt` pins `coverage` (7.16.2, the newest release at least 14 days old), and a deployment-contract test fails when any distribution of the `test` or `report` extra is missing from the lock.
- CP-27 (N5, N6, N8): a test pins exactly one `sess.lock` acquire per finalize; the own-thread finalize comments now say the return is ignored and the failure is logged; `stop` says "session lock was busy" instead of "worker did not exit" when the worker has exited and the lock stayed busy. N7 is recorded as accepted.
- CP-27 N9: a finalization that raises (placement release, summary) restores `closed` and any forced mark, releases the session lock and is reported as not finalized (`stop-incomplete` naming the error); the session keeps its resources and a retry works.
- CP-27 N10: the DAMON runtime-failure close runs under the session lock, so a concurrent forced finalization either closes DAMON itself or reads the finished disposition; the Summary no longer omits `damon_cleanup` and the manifest is no longer written before it.
- CP-27 N11: daemon shutdown joins the sampling workers outside the daemon-wide lock (one shared 10 s budget) before finalizing.
- CP-34: `serve` no longer unlinks whatever sits at the control socket path. A non-socket node (file, directory, symlink) or a socket another daemon still answers on makes `serve` exit 2 naming the path; only a dead socket node (connection refused) is removed, with a log line. Shutdown unlinks only the node this process bound.
- CP-31: the daemon checks every 5 s that the control socket path still holds its own listener (same inode) and rebinds when it was removed or replaced; when it cannot rebind it exits 3 so the restart policy recreates it. `ctl` failures to connect end in `[cause: socket-not-mounted | socket-missing | daemon-refusing | socket-permission]`, and `docs/CONSUMERS.md` documents the causes.
- CP-33: the control socket is served concurrently, one thread per connection, so a slow peer or a long verb (`report`, a wedged `stop`) no longer blocks other verbs. Admission is bounded (32 connections reading a request line, 16 running verbs, 2 running `report` verbs in their own pool, 32 `watch` streams) and over-limit requests get the additive `busy` error (contract stays 1; documented in `docs/PROTOCOL.md` and both copies of the run-gate contract). Shutdown waits for in-flight verbs before finalizing sessions.
- Contract 3 cgroup mode: a child forked after the round's process scan is no longer counted as a foreign process (which nulled every cgroup metric for the window); a cgroup member not in the scanned tree is the tree's own when its live parent chain reaches the tree. A member that already exited, or whose chain cannot be read, still counts as foreign (fail-safe).
- Shutdown fits docker's default stop grace: in-flight drain 4 s, worker join 4 s, forced finalize waits 1 s for a session lock (9 s on the normal path), and the ciu project sets `stop_grace_period: 30s` (use `docker stop --time 30` elsewhere). Live sessions are aborted (`daemon-stopped`) on exit, including exit 3 after a lost control socket. See `docs/CONSUMERS.md` "Stopping the daemon".
### Changed
- Rename the report dependency lock to `requirements-report.txt`; keep the same pinned packages in `setup.sh` and the image build.
- Release notes for 1.2.0: release with `cmru release --set-version 1.2.0` (the conventional bump would compute 1.1.1; check the dry-run version). `ciu.toml.j2` still pins image `1.1.0`: bump it to 1.2.0 after the image is published, then deploy (checklist: `docs/DESIGN-GUIDE.md`, "Release checklist for 1.2.0"). R2 mutation did not run for this delta (CP-30 open, waived).
<!-- cmru: pending release notes end -->

## [1.1.0] - 2026-10-27
<!-- cmru: generated -->
<!-- cmru: source-end=2562ebfbaf982205030d72fe6452aa0b21c639b6 -->

### Fixed
- fix(cgprofile): review follow-ups to 3e50df9cc: last-@ target option grammar (user@1000.service), swap.events fail = swap-out allocation failures, serve fail-fast documented, docker inspect -- (753d2fa6b)
- fix(cgprofile): pre-release review of 1.1.0: swapev/memev_h collector groups, swap_fail event, canonical cgroup: paths, slice-name validation, exact-type docker inspect, finite PSI (3e50df9cc)
- fix(cgprofile): join the sampler outside the daemon-wide lock in stop; cover the two uncovered CGP-110 branches; file review NITs N9-N12 (wave review r1) (420ac1554)
- fix(cgprofile): bounded finalize lock; guard liveness kill and DAMON paths after close (review CGP-110 r3) (6ea5ef2fb)
- fix(cgprofile): close the use-after-release window; document stop_forced in PROTOCOL (review CGP-110 r2) (5d80e02ce)
- fix(cgprofile): CGP-110 review fixes: own-cgroup slice proof, wedged-worker escalation, shutdown force (ac7e17e7b)
- fix(cgprofile): serve refuses to run under an unbounded cgprofile.slice (Sol B4) (353d90c95)
- fix(cgprofile): stop must not claim completion while the sampler is running (Sol B2) (a3a314837)
- fix(cgprofile): report an unconfirmed DAMON teardown to the consumer (Sol B3) (c201d69b6)
- fix(cgprofile): refuse placement of a selected pid absent from its recorded origin (Sol B1) (98d25e37d)

### Changed
- Merge cgp-110-20261021 (6ea5ef2fb): CGP-110 / Sol blockers B1-B6 for cgprofile 1.1.0 (keep CIU-150 and CIU-151) (68762c9ca)
- Merge origin/main into int-02-20261021 (gate the integrated tree) (62497cb0a)
- BL-01: review fixes - label facts vs hypotheses, drop out-of-repo citations (e79dd01aa)
- BL-01: renumber filed entries to free IDs (RG-108->RG-109, CIU-150->CIU-151, CP-24->CP-27) (4d384174c)
- BL-01: file backlog entries RG-108, CIU-150 (+ CIU-134 third reproduction), CP-24 (d5761487d)
- docs/test: INT-02 review fixes I-01..I-07 (f4267688a)
- Merge cmru-candidate-integrated-20261017 (03c2f5bab) (INT-01 step 2) (66bd6c9f9)

### Documentation
- docs(cgprofile): final-tree gate results for 1.1.0 (r0-r1, r3 on 753d2fa6b), CP-38, CP-31 socket-unlink reproduction (fe8c2a6da)
- docs(cgprofile): pre-release review backlog (CP-32..CP-37), review notes on CP-27/28/29, evidence addendum for 3e50df9cc (7a47e42f9)
- docs(cgprofile): final gate table for 1.1.0 evidence (677a56d0c)
- docs(cgprofile): record the 1.1.0 gate results (r0-r1 PASS, r3 PASS, R2 waived) (ab19854ea)
- docs(cgprofile): 1.1.0 release evidence, R2 waiver (CP-30), socket-absence diagnosis (CP-31), CP-24/25/26 backlog entries with the park-ref pointer (d354b5e78)
- docs(cgroup-profiler): file CP-28 monitor mode (standing named-scope sampler) and CP-29 cheap collector gaps; reference iomon vbpub@d1a19a159, iogov vbpub@2db1712aa (05bc30373)
- docs(cgprofile): release-qualification state and stale records (Sol B6) (0ee87b042)

### Testing
- test(cgprofile): round-2 review nits (release in finally, assert already_stopped) (a8e3e9a90)
- test(cgprofile): the serve CLI tests declare a bounded daemon slice (Sol B4 follow-up) (c4c1277ae)
- test(cgprofile): add the bounded live contract-2 observer acceptance probe (Sol B5) (a8bb82b9f)
- test(cgprofile): declare sequential pytest options (6d5852307)
- test(cgprofile): enable selective R2 diagnostics (5e5c6916f)
- test(cgprofile): refuse retirement when cgroup is unknown (8109072e9)

<!-- cmru: pending release notes begin -->
Release scope (CGP-110): the six blockers of the cgprofile-v1.0.0 Sol review.
**No R2 (mutation) pass for 1.1.0**: R2 is deferred by operator policy; the
registered `r0-r1` and `r3` lanes are the qualification. No R2 verdict may be
inferred from the r3 canaries.

- Release qualification and acceptance evidence (2026-10-27 release wave):
  the registered `r0-r1` and `r3` lanes ran on the exact release tree
  (results in `nyxloom-trove/reports/cgprofile-1.1.0-RELEASE-EVIDENCE.md`).
  R2 is waived for this release and tracked as CP-30. Operator decision
  2026-10-27: the live acceptance evidence for 1.1.0 is the read-only
  observation made the same day, in which the collectors (`sample_cgroup`,
  `read_limits`) were run from a root observation container against the real
  host cgroup tree with `host_cgroup_view` true; its capability matrix is
  recorded in CP-28 (Evidence). The separate bounded observer acceptance
  probe (`tools/observer-live-acceptance.py`) was NOT run for 1.1.0; CP-20
  stays `carved` until it is.
- Add (CP-29 items 1 and 2, pre-release review 2026-10-27): two additive
  sample groups, `swapev` (`memory.swap.events`: `high`, `max`, `fail`) and
  `memev_h` (hierarchical `memory.events`; `memev` stays
  `memory.events.local`). Both are in `metrics.GROUPS`, so the one-shot
  collector samples them by default and the daemon records them per tick;
  neither changes the contract-1 Summary. A new counter event kind,
  `swap_fail` (severity `serious`), fires when `fail` increases; `fail`
  counts swap-out ALLOCATION failures (`memory.swap.max` or host swap
  exhausted), never swap-ins. An absent file reads as `{}`, never as zeros.
- Fix (pre-release review): a `cgroup:` target is canonicalised
  (`//`, `/./`, trailing `/` collapse) and a `..` component is refused —
  previously `cgroup:/../..@follow` walked out of the mounted cgroup tree.
  A slice name with an empty hierarchy component (`a--b`, `-a`, `a-`) or a
  `/` is refused instead of expanding to a phantom `a-.slice` level.
- **Changed behaviour (pre-release review):** `serve` validates
  `--observe-slices` and `--gates-slice` at start and refuses (exit 2) an
  unusable unit name. Previously the daemon started and every `ctl host`/
  `ctl status` then failed as a daemon fault (exit 3). Under the compose
  `restart: unless-stopped` policy the refusal is a restart loop until the
  flag is corrected, exactly like the slice guard; fail-fast is intended.
- Fix (pre-release review, pre-existing): the target option grammar split
  at the FIRST `@`, so a real cgroup name containing `@`
  (`/user.slice/user-1000.slice/user@1000.service`) was truncated to
  `.../user` and `1000.service` read as an option. Options are now the text
  after the LAST `@`, and only when every item's key is a known option word
  (`follow`, `nofollow`, `metrics`, `as`, `role`, `subpath`); a trailing
  bare `@` is the escape for a name ending in an option word. `cgprofile
  run`/`attach` role tagging goes through the same grammar.
- Fix (pre-release review): `container:` lookup runs `docker inspect --type
  container -- <ref>` (a same-named image or volume can no longer be
  mistaken for the container, a ref starting with `-` is not a flag) and
  requires a 64-hex `Id`; a hung Docker CLI is a "cannot resolve" refusal,
  not a `TimeoutExpired` traceback.
- Fix (pre-release review): a PSI field whose text parses as a non-finite
  float (`nan`, `inf`, `1e999`) is dropped, so a corrupted file can never put
  a bare `NaN`/`Infinity` token (not JSON) into `samples.jsonl.gz`.
- Docs: README/DESIGN-GUIDE no longer claim "no planned 1.1.0 release".
- Backlog: CP-24/25/26 (bounded sampler-owned stop, evidence-preserving
  recovery, durable DAMON pool provenance) are filed with their design held on
  a park ref and are NOT in this release; CP-28/CP-29 (monitor mode, cheap
  collector gaps), CP-30 (R2), CP-31 (socket-absence diagnosis, incl. a socket unlinked under a running daemon) and CP-32..CP-38 (review follow-ups) are filed.
- Fix (wave review r1): `stop` joins the sampling worker outside the daemon-wide
  lock. Previously a worker that was itself finishing (session error, enforced
  stall kill) needed that lock, so the join timed out and `stop` answered a
  spurious `stop-incomplete` after the full 10 s while holding the lock.
- Docs (Sol B6 / records): the observer mount is described as the dedicated
  directory (not "only that node"), and stale CP-13 is closed. Release
  qualification for 1.1.0 is the registered `r0-r1` and `r3` lanes run from a
  clean exact tree plus the Bake build and post-deploy revision-label checks
  (CP-21); the code side of CP-21, including the dirty-source refusal in
  `cmru/build-initial-standalone.sh`, is already on main. No R2 (mutation)
  verdict exists or is claimed for 1.1.0.
- Add (Sol B5): `tools/observer-live-acceptance.py`, a read-only probe for the
  live contract-2 observer acceptance from a gated peer (contract-1 refusal,
  well-formed local maps, sibling-only versus enforced-at-ancestor deltas),
  with the operator procedure in `docs/CONSUMERS.md`. The live run itself is
  NOT part of this change and is required before CP-20 is closed.
- **BREAKING / operational (Sol B4):** `cgprofile serve` now refuses to start
  (exit 2) unless the slice the daemon is ACTUALLY IN is bounded: its own
  cgroup (derived from the host proc view, not looked up by name) must sit
  under the top-level `cgprofile.slice` and that slice must carry a finite,
  positive `memory.max` (unlimited, zero, empty, unreadable and non-numeric
  values are all refused; zero because a slice that can hold no memory is a
  misconfiguration, not containment). Hosts without the installed unit
  (`sudo cp infra/cgprofile.slice /etc/systemd/system/ && sudo systemctl
  daemon-reload`, then recreate the container, since its cgroup parent is
  chosen at creation) refuse, and with the compose `restart: unless-stopped`
  the container CRASH-LOOPS until the unit is installed. Install the unit
  BEFORE upgrading. A non-container or development `serve` run has no
  `cgprofile.slice` ancestry and is refused too. The pre-creation Docker-host
  `LoadState=loaded` probe the review asked for is NOT delivered in 1.1.0: it
  is CIU-150 (`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`), explicitly outside this
  window; the daemon's own refusal is the interim fail-closed guard and runs
  after container creation. The documentation that said the daemon "still
  serves" without the unit, and a `doctor` message the code never emitted, are
  corrected.
- Fix (Sol B2): `stop` no longer claims completion while the sampling worker is
  still running. If the worker does not exit within 10 s the daemon answers the
  new error `stop-incomplete`, writes no `finished` manifest or summary, and
  keeps the session's placement and DAMON resources; a retry after the worker
  exits finalizes normally. Previously the join result was ignored, so a blocked
  sampler could append records to a finalized session. A permanently wedged
  worker cannot hold resources forever: the third consecutive incomplete `stop`
  (`STOP_FORCE_AFTER_ATTEMPTS`) finalizes anyway, releasing placement and DAMON,
  writing an `aborted` manifest with reason `sampler-still-running` and an
  additive `summary.stop_forced`; a late sample from the worker is discarded.
  Daemon shutdown (SIGTERM) forces the same way instead of stranding resources.
- Fix (Sol S7/S8/S9, closed-session paths): `_observe_liveness` returns when the
  session is closed and re-checks before a stall kill, so a kill can never reach
  a leaf that release is restoring; the DAMON `collect()` runs only while open
  and `_disable_damon_for_session` is a no-op once closed (no second close, the
  cleanup disposition is not overwritten). Finalize takes the session lock ONCE
  with a bounded acquire (`STOP_JOIN_SECONDS`) and keeps it for the whole
  section; if a worker is wedged inside a locked action it returns False
  instead of hanging the daemon-wide lock, and a restart recovers the session
  as an orphan.
- Fix (Sol S6, use-after-release): finalization now sets a `closed` flag under
  the session lock BEFORE it releases placement and closes DAMON, and the
  sample path (migrate, DAMON recommit, summary accumulation, every record
  append) runs only while the flag is clear, under the same lock. A wedged
  worker's sample that arrives between the flag and the release is discarded.
- Fix (Sol B3): an unconfirmed DAMON teardown is no longer indistinguishable
  from "DAMON off". `stop` (and `summary.json`/`manifest.json`) now carry
  `damon_cleanup: {confirmed, slot, quarantined}` whenever a DAMON owner was
  closed, including after a runtime DAMON failure. The workload verdict and the
  `ok: true` stop are unchanged.
- Fix (Sol B1): placement now requires every selected, still-live process to
  be listed in its recorded origin unit's census immediately before transfer.
  A process that left the unit since capture is refused
  (`place-refused:identity-unavailable`) and no scope is created or attached,
  instead of being moved out of an unrelated unit and later restored to a stale
  origin.
<!-- cmru: pending release notes end -->

## [1.0.0] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=f4933a2d5a6d1f1ab460d5ff85caeed269af7a99 -->

### Added
- feat(assay,ciu,cmru,cgroup-profiler,nyxloom,run-gate): complete integrated release wave (1fa737782)
- feat(run-gate): make wheel SemVer the only supported interface (9a0e192ab)
- feat(cgroup-profiler): add contract-2 resource observer (eb3c77b35)
- feat(cmru): pass protected release source identity to artifact steps (b310458e9)
- feat: add contract-2 resource event attribution (90dc9078d)
- feat(rg55): adopt observer contract 2 and local events (2b1bd7f39)
- feat(cgprofile): expose local and hierarchical event evidence (135bcb4da)
- feat(rg-89): observe native mutation resource limits (219997c93)
- feat: add estate cli version compatibility (05f373a40)
- feat(skills): add canonical single-tool skills for ciu, run-gate, assay, cmru, cgprofile (33c0b0c27)
- feat(cgroup-profiler): build the r0-r1/r2/r3/gate lane matrix, restore 100% coverage (d5df216d1)
- feat(cgroup-profiler): log-tail phase source; fix lib/ silently excluded from git (712abdcac)
- feat(cgroup-profiler): container/cgroup profiler with effective limits, phases and reports (4af86d571)

### Fixed
- fix(cgroup-profiler): refuse pids_localevents in the R2 observer; clarify release qualification (bc48c179f)
- fix: bound memory for ephemeral gate lanes (731be2b4e)
- fix: close release-wave review findings (49e4be092)
- fix: bound Assay refusals and harden observer setup (ad173675f)
- fix(cgprofile): make host daemon lifecycle selectable in ciu (e3f13ada4)
- fix(cgprofile): preserve worktree git mounts in gate (1be40b03a)
- fix: bind image builds to exact source identity (a0ad6f40d)
- fix(cgprofile): refuse placement that empties origin scope (465f67d06)
- fix(rg89): serialize shared-leaf R2 and verify peer capability state (cb155eab1)
- fix(cgprofile): serialize R2 attribution and retry peer ESRCH snapshots (5bc86f977)
- fix(cgprofile): retry transient peer snapshot ESRCH (ace790e94)
- fix(cgprofile): consolidate DAMON guidance and harden NSpid guard (aa5edcde6)
- fix(cgprofile): align observer oracle and DAMON guidance (178552d0b)
- fix(cgprofile): close resource-observer mutation gaps (75d286dab)
- fix(cgprofile): fail closed on missing socket bind source (930f0bbab)
- fix(run-gate): support observer-backed command lanes (c9d488fc1)
- fix(cgprofile): wire observer into native R2 lane (cd0e947e5)
- fix(rg55): align daemon PID mode and preserve profile summaries (3c77a9314)
- fix(cgprofile): use host PID namespace for the daemon (50372b71f)
- fix(cgprofile): preserve placement recovery across failed start and retention (753ea04c5)
- fix(cgprofile): report cleanup quarantine truthfully (a8a53fb43)
- fix(cgprofile): tolerate unavailable optional DAMON lock (d8056454e)
- fix(cgprofile): serialize DAMON registry ownership safely (c5d89652f)
- fix(cgprofile): keep DAMON startup optional for one-shot runs (591c258d8)
- fix(cgprofile): reserve DAMON capacity and quarantine uncertain stops (c8f7c70e9)
- fix(cgprofile): skip unchanged token DAMON recommits (5c3b4f0d2)
- fix(cgprofile): isolate DAMON targets and degrade runtime failures (181201f5b)
- fix(cgprofile): start DAMON from configured sysfs inputs (c2db510fc)
- fix(cgprofile): preserve shared proc parser offsets (6733bef5e)
- fix(cgprofile): validate helper container id through shared utility (83ca2f5d1)
- fix(cgprofile): recover safely after leaf removal (4636a9c43)
- fix(cgprofile): pass explicit base through P1 acceptance gates (a7dc7b4c1)
- fix(cgprofile): close P1 proc-identity oracle gaps (18bf438ff)
- fix(cgprofile): verify scope retirement during restore and preserve socket refusals (d48d1ed8e)
- fix(cgprofile): publish only the versioned GHCR target (5d3c108e6)
- fix(cgprofile): publish only the versioned GHCR target (8723f3064)
- fix(cgprofile): release provisional DAMON owners and refuse session ID collisions (aa4d11734)
- fix(cgprofile): stream P1 summary accumulation (2e130da3e)
- fix(cgprofile): bind proc stat to requested pid (e8221c6bc)
- fix(cgprofile): place and recover lanes under delegated scopes (831a789e1)
- fix(cgprofile): reject malformed socket request shapes (753a57881)
- fix(cgprofile): distinguish unreadable cgroup tree from absent target (b88d4f07e)
- fix(cgprofile): accept completed capped helpers after update race (02c92591c)
- fix(cgprofile): preserve omitted ctl options and sync contract mirror (a1a82c991)
- fix(cgprofile): address P6 round-5 blockers (da0662877)
- fix(cgprofile): fail closed on PID migration write errors (bfe6a2e8a)
- fix(cgprofile): restore placed survivors across private PID namespaces (738bf1f5c)
- fix(cgprofile): refuse phantom system bus bind source (e102a62ff)
- fix(cgprofile): move private-namespace PIDs through host systemd (7eb544186)
- fix(cgprofile): refuse explicit empty build version (158488ccb)
- fix(cgprofile): derive daemon identity from release tag (dfef6bad8)
- fix(cgprofile): preserve host observation with private namespaces (d794c2362)
- fix(cgprofile): fail closed without probe parent (5b9d1bc10)
- fix(cgprofile): enforce placed gate container caps (3ce083491)
- fix(cgprofile): restore CMRU release identity (97c055927)
- fix(skills): repair defects found by independent review of new canonical skills (abcb6e274)
- fix(cgroup-profiler): a test's own "known broken" shebang path isn't universally broken (eda33a84a)
- fix(run-gate): each project's own assay.toml also needs the tester-venv python (943db7c82)
- fix(run-gate): estate-wide sweep of environment="host" lanes broken by RG-43 (f62642c69)
- fix(cgroup-profiler): caps.py limit-erasure bug, report_html XSS, unwired resample (95ace544b)
- fix(cgroup-profiler): place the helper outside the tier it profiles (77dff4428)

### Changed
- chore: integrate contract-2 release wave (336336d81)
- chore(cgprofile): pin cockpit daemon release image (9c152110d)
- review(cgprofile): record R2 cap audit (06e7804ea)
- Add oracles for RG-89 input-boundary survivors (42a76af89)
- Retry peer identity scans on procfs disappearance (fa5e348a8)
- Remove dead resource observer test fixture assignment (60303e0db)
- Strengthen resource observer survivor oracles (75d9c4f0e)
- Merge main into rg89-resource-observer (3cd1b1444)
- cgprofile: record P1 round 8 fix verification (eb909889f)
- Merge branch 'main' into rg55-damon-start-commit-fix-main-20261005 (f0cf27517)
- Merge main into cli-extended-unified before release (ce1696562)
- harden(cgprofile): validate proc PID inputs (63e4fe919)
- refactor(cgprofile): share proc and safety helpers (cb22e88ec)
- cgprofile: model busctl subprocess output semantics (c3a0d7865)
- cgprofile: assert rejected slice-name boundaries (4728f2c0f)
- backlog(cgprofile): CP-15, CP-16 reviewed against SPEC-V8 draft.11: count mode is 8.0, byte budget v8.1 (dstdns D-666) (0c4f72e9e)
- backlog(cgprofile): CP-15/CP-16 scoped to v8.1 admission (dstdns D-658) (c8def3121)
- Merge branch 'main' into rg55-p1-final-20261001 (55d44fcd4)
- backlog(cgprofile): CP-16 combined per-sample hot+warm Summary series for ciu v8's warm-set charge (dstdns D-658) (e7a5ed5c0)
- backlog(cgprofile): CP-15 note -- optional after D-655 (Docker-object reservations) (4a46ee439)
- backlog(cgprofile): CP-15 reservation verb for consumer-decided admission (ciu v8 V8-31) (9b4bf835e)
- refactor(cgprofile): remove inert P1 mutation boundaries (4d097c97e)
- Merge branch 'main' into rg55-p1-final-20261001 (e0ce67dc0)
- revert: drop duplicate P1 oracle tests (1a098eb51)
- merge(cgprofile): reconcile P6 with current main (3f8d119ba)
- Refresh P1 checkpoint for latest Nyxloom main update (1063fc5f5)
- Refresh P1 checkpoint for latest main (bd915d7f9)
- Refresh P1 reconciliation and review checkpoint (a617f8763)
- Clarify P1 review artifact gate sequence (6aa6427f4)
- Record current P1 base and gate deferral (bd4cb3453)
- Refresh P1 round-five review checkpoint (27fd457d3)
- Record P1 mutation survivor oracle backport (902a6f5f6)
- Add direct oracles for proc identity survivor gaps (01912a917)
- Clarify exact percentile state memory growth (032b707a3)
- Document exact percentile state growth (c003cd3c2)
- Record focused P6 blocker regressions (28345cecb)
- Point P6 preflight evidence at RW-398 (298e4157e)
- Normalize parallel RG-55 controller rulings (6e8178e75)
- Record RG-55 host preflight for P6 (cd24d73c9)
- Record RG-55 host unit preflight evidence (fb1f1d154)
- perf(cgprofile): share online DAMON rank trees (c81b28372)
- review(cgprofile): record P1 round4 rejection and evidence holds (1a9451553)
- review(cgprofile): fix socket test synchronization and state daemon authority (0c361de93)
- Merge branch 'rg55-p1-release-review-20260930' into rg55-followups-cgprofile-final (80e6d8d2b)
- review(cgprofile): record round-6 host-unit block and safe bus fixes (bfcf69368)
- cgprofile: record P6 final review round 4 rejection (ca8fccc7c)
- cgprofile: fail closed on unreachable watch kills and repair status timestamp (9c0a6e39a)
- cgprofile: record P6 final review and live-probe block (5e37a7a62)
- cgprofile: describe stream identity and private PID namespace accurately (5ef643605)
- cgprofile: align design and release guide with private namespaces (7bcdc37d9)
- cgprofile: reject watch EOF without a terminal end (d206877ad)
- cgprofile: count only complete progress events and cover leaf-creation race (d858852b3)
- cgprofile: bind placement writes to exact token leaf and guard progress streams (fd77a4c44)
- Merge branch 'main' into rg55-followups-cgprofile-final (41c6fba6d)
- Cover helper PID identity resolution (d20153732)
- Preserve helper PID target identity (a2d79242f)
- Clarify shared cgroup profiling attribution (f76f1f015)
- Include runtime project metadata in cgprofile image (e93a40fcc)
- rg55: cap P1 container lanes before execution (285ffa8cb)
- merge: reconcile P6 with private-namespace P1 fixes (d14b90707)
- cgprofile: constrain profiler PID attribution to target cgroup (bc5566b23)
- cgprofile: preserve private-namespace host PID attribution (0f478314e)
- merge cgprofile release and gate infrastructure (35d3732fe)
- merge: reconcile P6 with current main (ae923b88b)
- review(cgprofile): record reconciled P6 controller pass (4392bece3)
- Merge current main for P6 source-backed assay (cc9d13b60)
- review(cgprofile): record controller P6 conditional review (13e394a01)
- Merge current P1 daemon fixes into P6 follow-ups (bb1042a67)
- review(cgprofile): record controller P1 adversarial review round 2 (3455999ae)
- chore(cgprofile): reconcile P1 daemon with current main (ced406593)
- backlog(CP-4): cmd_targets hardcodes repo_dir==out_dir, Docker refuses the duplicate mount (c01451d75)
- chore: land run-gate root and dev-gates migration (41c1cafb3)
- chore(cgprofile): materialize P6 final tree linearly on current main (c362c8dff)
- chore(cgprofile): materialize P1 final tree linearly on current main (9b70a46e9)
- plan+log(rg55): P6 review handoff; RW-45 -- builds under PSI, frozen-golden version string, CP-10 root cause (7d7298522)
- contract+log(rg55): RW-43 -- P4 round 1 rulings (rusage via os.wait4, exec inflight stamp), contract §4.3a bare-host lanes, round file committed (2ec076e18)
- plan(rg55): P6 handoff (cgprofile follow-ups: CP-2 socket, CP-4..CP-7, cgprofile.slice, CP-8 watch, CP-9 placement) + RW-35 (7c8e55738)
- contract(rg55): v1.1 amendment (RW-34) -- two carriers, watch streaming, placement, liveness policy, gates slice (6b719d223)
- contract(rg55): RW-21 scope-container absolute counters + RW-11 file list -- goldens regenerated (3b75e1df1)
- plan(rg55): adversarial review handoffs for P1 (daemon) and P2 (run-gate client) (ead72a735)
- plan(rg55): P1/P2 implementer handoffs (dispatched 2026-09-12 in parallel per RW-1) (6f827c296)
- plan(rg55): P0 -- interface contract frozen, golden fixtures, RG-56/RG-57 + CP-1..3 filed, SPEC-V8 D.6, controller log (63b928da0)

### Documentation
- docs(cgprofile): preserve Sol stop design review (290459db4)
- docs(cgprofile): preserve Opus stop handoff review v2 (b4ef3b2d6)
- docs(cgprofile): preserve sampler handoff review (8dca2089a)
- docs(cgprofile): preserve Opus release review follow-up (a7c512a5f)
- docs(cgprofile): preserve independent release reviews (eae8d0fc1)
- docs(cgprofile): record live placement acceptance (8f5e5eae2)
- docs(cgprofile): document OCI release notes (9943239c2)
- docs(cgprofile): name installed Run-Gate wheel consumer (e5d01bef1)
- docs(rg55): align consumer identity with Run-Gate SemVer (39754e0b0)
- docs(cgprofile): align Run-Gate wheel identity contract (9ba133fe3)
- docs(cgprofile): close CP-22 after gate proof (31f09341b)
- docs(cgprofile): keep consumer identity wording aligned with oracle (9688cd435)
- docs(cgprofile): clarify R2 cap sentinel plan (34417c42e)
- docs(cgprofile): record P1 round-9 adversarial review (3d6df01ff)
- docs(cgprofile): explain DAMON exclusivity and test EOF edge (b5804ee6d)
- docs(cgprofile): fix DAMON_STAT troubleshooting anchors (868ff7c64)
- docs(cgprofile): document DAMON_STAT availability boundary (a56fe0c3a)
- docs(rg55): record bind and observer trust boundaries (ae11d4240)
- docs(cgprofile): correct host PID namespace claims (e89aeff89)
- docs(cgprofile): correct daemon namespace description (b9d41a141)
- docs(cgprofile): record P1 final merge review acceptance (056ffbec5)
- docs(rg55): record P1 fix verification and campaign evidence (7da5f0eb5)
- docs(cgprofile): record P1 survivor review (ca352d8cd)
- docs(rg55): record P1 round-5 provisional acceptance (5becf9893)
- docs(cgprofile): record latest-main reconciliation (e2e077fbd)
- docs(cgprofile): record current-main reconciliation (385526194)
- docs(cgprofile): record DAMON repair gate history (e764f582d)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(cgprofile): link DAMON startup semantics to kernel source (a3735a42e)
- docs(rg55): disposition P1 supplemental Sol review (8bd5d0a8e)
- docs(rg55): record P1 supplemental Sol acceptance (bf7dc95fb)
- docs(cgprofile): preserve P1 base-propagation review finding (8e6b2475d)
- docs(cgprofile): add P1 supplemental review packet (d7078fe51)
- docs(cgprofile): clarify P1 supplemental review scope (9d66a9434)
- docs(cgprofile): checkpoint P1 main reconciliation (d97ecae44)
- docs(cgprofile): identify DAMON stop evidence field (de06f750d)
- docs(cgprofile): align full gate advisory window (2f3689ef3)
- docs(cgprofile): clarify P6 campaign estimate (13fae541e)
- docs(cgprofile): clarify DAMON capability vs readiness (3f1cd1910)
- docs(cgprofile): record P6 review and gate receipts (0bc3f3ddb)
- docs(cgprofile): record P6 survivor oracle review (faed6fe25)
- docs(cgprofile): record round-7 P6 fix verification (2c4c5ad74)
- docs(rg55): record P6 exact-gate failure and repair (175ed5d84)
- docs(rg55): record P6 socket EOF oracle (95fcbe9d0)
- docs(cgprofile): record P6 focused oracle correction (123e182ef)
- docs(cgprofile): record P1 final Sol review (415ce74aa)
- docs(cgprofile): document pinned GHCR deployment (e32cb6179)
- docs(cgprofile): record P1 adversarial review round 5 (c2716520e)
- docs(cgprofile): record P6 blocker regression rerun (dae606f06)
- docs(rg55): checkpoint current P1 review blockers (db044d4d3)
- docs(rg55): refresh P1 review checkpoint and attached-tree policy (148481e4a)
- docs(cgprofile): record P6 round-seven blocker repairs (415bbc798)
- docs(cgprofile): add round 7 live carrier evidence (d1b2963ef)
- docs(cgprofile): record P6 final adversarial review rejection (b526eb5c1)
- docs(cgprofile): record round-6 exact-tree short gates (a98b443f2)
- docs(cgprofile): refresh P6 review contract (477a8e7a8)
- docs(cgprofile): record P6 main reconciliation and host preflight (2d555fe12)
- docs(cgprofile): record P6 exact-tip short-gate results (0eb2686c9)
- docs(cgprofile): record P6 exact-tip coverage failure (3ec4cba0a)
- docs(cgprofile): checkpoint P6 after B107 merge (cc25b517d)
- docs(cgprofile): bound P6 brief inventory in review handoff (14fae51e3)
- docs(cgprofile): checkpoint P6 survivor restoration work (a65d2f37a)
- docs(cgprofile): record P6 replacement R2 outcome (d612dbf41)
- docs(cgprofile): document system bus placement prerequisite (6d1ed8a96)
- docs(cgprofile): prepare P6 provisional review (6859bc3e9)
- docs(cgprofile): clarify P1 evidence limits (7e39857d8)
- docs(cgprofile): record P1 Sol review round three (b0544d50a)
- docs(rg55): reconcile P1 review state to current main (3f3777662)
- docs(rg55): refresh P6 review safety contract (1300fca3a)
- docs(rg55): record fresh P1 short gates (b871cd644)
- docs(rg55): record P1 coverage and CIU findings (43daf53e0)
- docs(cgprofile): clarify helper PID identity (8df969674)
- docs(rg55): refresh P1 reviewer packet rulings (ec2882090)
- docs(rg55): record P1 terminal mutation evidence (d94c58b9e)
- docs(rg55): record P1 main reconciliation (1908316b2)
- docs(rg55): refresh private-daemon review policy (0daa66e26)
- docs(cgprofile): record terminal P1 R2 rerun (199850969)
- docs(cgprofile): close P6 reconciliation report checkpoint (8e52ea083)
- docs(cgprofile): align adopter examples with 1.1.0 (54e0a3645)
- docs(cgprofile): record current R2 survivor disposition (9471a9af7)
- docs(cgprofile): disambiguate post-wave backlog identity (32ec2b3d6)

### Testing
- test(cgprofile): cover observer host permission failures (2dfa8204a)
- test(cgprofile): cover observer refusal and cleanup branches (c9f769691)
- test(cgprofile): cover release identity refusal paths (e00aac9d8)
- test(cgprofile): raise R2 cap for current candidate plan (27ddf251a)
- test(cgprofile): cover unterminated observer overflow (cedfc4598)
- test(cgprofile): normalize deployment contract wording checks (6aa0a8b5a)
- test(cgprofile): match the documented helper cgroup wording (25deff6ce)
- test(cgprofile): synchronize socket readiness without clients (0afa24a6e)
- test(cgprofile): hold token process through PID scan (b2a270ee9)
- test(cgprofile): wait for socket listener readiness (e520b855d)
- test(cgprofile): synchronize token process readiness (e7a6b7594)
- test(cgprofile): wait for listening socket before client connect (1653143f7)
- test(cgprofile): close P1 R2 survivor oracles (c24b0d2b8)
- test(cgprofile): cover DAMON quarantine and restore failures (b9ea60801)
- test(cgprofile): align pool counter fixtures with reconciliation (b30eca821)
- test(cgprofile): exercise scope recovery after leaf removal (b6b2dc659)
- test(cgprofile): close P1 survivor oracles (f13f76db9)
- test(cgprofile): strengthen P1 mutation survivor oracles (99db6d034)
- test(cgprofile): cover P1 R2 summary and proc boundaries (38e383499)
- test(cgprofile): budget the full P6 mutation inventory (87064fb26)
- test(cgprofile): reject empty systemd array replies (58eeac96c)
- test(cgprofile): exercise captured systemd unit output (df4dd1fc5)
- test(cgprofile): cover placement retirement proof paths (45f575bd0)
- test(cgprofile): remove obsolete PID-addressability oracle (de7245aa2)
- test(cgprofile): exercise unterminated requests over real socket (9651d9152)
- test(cgprofile): assert delegated moves use systemd (67f45c28b)
- test(cgprofile): restore P6 mutation survivor oracles (a1d6d545d)
- test(cgprofile): replace probabilistic run ID oracle (7d7171b60)
- test(cgprofile): distinguish proc stat PID guard from helper refusal (d11adc412)
- test: cover malformed proc stat delimiter (80263df66)
- test: cover late placement identity recovery paths (f6a366160)
- test: cover placement identity and retired-scope failures (ae0a17a93)
- test: include placement defaults in ctl request fixture (5b62ef028)
- test(cgroup-profiler): cover orphan placement recovery paths (bb106d724)
- test(cgroup-profiler): cover placement ownership and recovery paths (1e0d5a2cb)
- test(cgprofile): align D-31 fixtures and documentation oracles (d5b524eff)
- test(cgprofile): describe shared peak oracle accurately (3a8327afc)
- test(cgprofile): distinguish persistent helper from completed client (07bc03477)
- test(cgprofile): cover partial request timeout in round-6 gate (e48d1d3f7)
- test(cgprofile): cover incomplete kill without logger (f48726036)
- test(cgprofile): close P6 coverage gaps (d5fd17eb5)
- test(cgprofile): assert PID migration refusal diagnostics (e7bca65e4)
- test(cgprofile): close P6 mutation oracle gaps (6540f8776)
- test(cgprofile): cover silent cgroup kill failure (b3df5602a)
- test(cgprofile): cover placement refusal cleanup branches (93264604a)
- test(cgprofile): cover namespace placement failure paths (b282fe79e)
- test(cgprofile): close P6 mutation survivor oracles (2f3fd2c90)
- test(cgprofile): close P1 mutation survivors (3e6e657e0)
- test(cgprofile): align private-proc placement oracles (aae66356b)
- test(cgprofile): cover CLI usage headline (a2c2501fe)
- test(debian-install-v2): harden isolated VM lane (d472d58d8)

<!-- cmru: pending release notes begin -->
### Added
- First SemVer release of the `cgprofile` CLI and host daemon, including the
  separate read-only contract-2 observer for native Assay R2 lanes. The
  observer reports event counters for the candidate cgroup and its enforcing
  ancestors without exposing the daemon control socket.

### Changed
- Documentation: identify the installed Run-Gate SemVer wheel as the supported
  consumer of the CGProfile interface; copied `run-gate.py` scripts are retired.
- Gate R3 now declares memory and swap ceilings, so Run-Gate admits it against
  the gates-slice budget and Docker enforces its per-container limit.
- The native R2 resource observer refuses a host cgroup2 mount using
  `pids_localevents`: its fork-failure counter records the cgroup where the
  fork was attempted, so it cannot identify which ancestor's `pids.max`
  rejected it. Successful responses always report
  `pids_events_scope = "hierarchical"`; `memory_localevents` remains supported.
- Documentation: the native-R2 lane example declares the required
  `resources.memory` and `resources.pids`.
- Release qualification: CMRU's release gate runs `run-gate r0-r1` and
  `run-gate r3` only. The complete Run-Gate `gate` lane additionally runs the
  24-hour `r2` mutation campaign. The initial 1.0.0 release has no completed
  full-source R2 verdict; the long R2 campaign was deferred under the
  operator's release policy.

### Documentation
- The README, design guide and consumer guide now describe the GitHub Release body CMRU
  publishes for each release (`cmru handler oci-image-release`, the last command of the push
  step, after the image push): its pull instruction and tagged notes, the
  `cmru release --dry-run` preview, and what CMRU verifies before and after writing it.

### Migration
- Native R2 consumers that use CGProfile resource observation must move to
  observer contract 2 and the observer-only endpoint
  `/run/cgprofile/observer/resource-observer.sock`. Contract-1 resource
  observer requests are refused; the separate daemon control protocol remains
  contract 1 on `/run/cgprofile/ctl.sock`.
- Host observer setup must provision the `cgprofile-observer` group and
  `/run/cgprofile/observer` as `0751 root:cgprofile-observer`. On vbpub hosts,
  apply MDT's observer-only host setup before using the endpoint. This is
  required only for lanes configured to use the native R2 observer.
<!-- cmru: pending release notes end -->
