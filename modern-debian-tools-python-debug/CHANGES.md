# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [source-f10a9ada3ccd] - 2026-09-22
<!-- cmru: generated -->
<!-- cmru: source-end=f10a9ada3ccd8234bff1376b879ef2329f973552 -->

### Added
- feat: add estate cli version compatibility (05f373a4)

### Fixed
- fix(gates): correct release and VM lane contexts (a4bba60d)
- fix(mdt): stage CLI parser with AI installer (90a37794)
- fix(mdt): preserve Docker pull diagnostics (a4420c42)

### Changed
- cli: universalize vbpub parser diagnostics (aa0e69fa)
- chore: land run-gate root and dev-gates migration (41c1cafb)

### Documentation
- docs(mdt): explain VM and Assay test boundaries (5885cd03)
- docs: record cmru and isolated gate lane plans (6091cfef)

### Testing
- test(mdt): add governed VM and assay lanes (79a0d5ee)
- test(mdt): make assay source imports explicit (46744b07)

## [source-b0210c0e25a2] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=b0210c0e25a2dab723abcd50eb456bc1cc2d9abc -->

### Changed
- Release metadata prepared by CMRU.

## [source-3bdb473105fe] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=3bdb473105febf0a995610b7b085b7b4134d1a97 -->

### Fixed
- fix(mdt): keep release cache on managed BuildKit remote (98015f29)
- fix(mdt): avoid forced recompression during large OCI exports (0c338a87)

### Documentation
- docs(mdt): align cache example with non-forced compression policy (2063f1a1)

## [source-32e006235d05] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=32e006235d051df3e865a73c827dfc40840e4ed9 -->

### Changed
- Release metadata prepared by CMRU.

## [source-69c5fd50af56] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=69c5fd50af5655f99a08af760faab15d3052a81a -->

### Added
- feat(mdt): add headless VM tooling (fcce2d80)
- feat(mdt): harden host setup wizard and io baseline (5ed374f7)
- feat(mdt): persist Pi and ClaudeLink state (80d393f5)
- feat(mdt): add BuildKit governance (51245d69)

### Fixed
- fix(mdt): isolate BuildKit caches by compression policy (c2deb87f)
- fix(mdt): reconcile shared venv after aider install (0ce89ad2)
- fix(mdt): close offline Nyxloom wheel dependencies (880b98c9)
- fix(mdt): make wizard auto controls and watcher fail safe (9a2f1275)
- fix(mdt): harden wizard validation and explain watcher policy (f706ce87)
- fix(mdt): clear stale caps and validate wizard candidates (422f526f)
- fix(mdt): disable unmeasured per-container IO caps (d4074266)
- fix(mdt): use tester coverage report syntax (6d83f3e3)
- fix(mdt): initialize assay state and declare gate resources (697f2e85)
- fix(mdt): repair IO watchers and memory wizard UX (7f182139)
- fix(mdt): close host setup wizard review findings (d93492b7)
- fix(mdt): make host setup wizard host-only (c594c185)
- fix(mdt): document dynamic host config source (85142e9f)
- fix(mdt): validate host config before mutation (2c4a670e)
- fix(mdt): align release docs and host-scaled wizard defaults (aec4df70)
- fix(mdt): align wizard and managed-builder guidance (df3f514b)
- fix(mdt): close BuildKit governance correctness gaps (1ebe42d5)
- fix(mdt): publish load-flow OCI layouts after gate (dd370563)

### Changed
- chore(nyxloom): archive historical handoffs and reports (28801a95)
- Merge remote-tracking branch 'origin/main' into mdt-origin-merge-probe-20260916 (05900455)
- WIP: modernize MDT host setup and IO governance (d1bd75db)

### Documentation
- docs(mdt): require VMs for host-kernel tests (950ba935)
- docs(mdt): clarify derived wizard values (d10671b9)
- docs(mdt): align wizard proposal rationale (2bb110f2)
- docs(mdt): migrate container persistence safely (ea0529cf)
- docs(mdt): document source-first release flow (dd6c95e6)

### Testing
- test(mdt): cover finalizer builder wiring (e9ff4f29)
- test(mdt): make assay baseline process-tree safe (50e45ce4)
- test(mdt): clarify wizard hierarchy and harden coverage (7ddf3859)
- test(mdt): bound native mutation candidates (3d637cca)
- test(mdt): clarify wizard and run full tester gate (1b47f3ee)
- test(mdt): cover fail-closed host config staging (a0293524)

## [source-652a118cb83f] - 2026-09-14
<!-- cmru: generated -->
<!-- cmru: source-end=652a118cb83f7440ff2c79488f757d580093279c -->

### Changed
- Release metadata prepared by CMRU.

## [source-9a08f1260f1c] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=9a08f1260f1cd1e97cb8dc2a883ba8f554e8ced2 -->

### Added
- feat(mdt-host-setup): round-1 repairs C9 -- devcontainer.json /run/cgprofile mount (RW-31/A2, D-30, M5) (d3aa5e6a)
- feat(mdt-host-setup): export CGROUP_PARENT_DEV_INFRA/_GATES to devcontainers (M3) (1de4c93c)
- feat(mdt-host-setup): render/install dev-infra+dev-gates, placement report, checks (M2) (22049949)
- feat(mdt-host-setup): add dev-infra.slice and dev-gates.slice units (M1) (16f3a476)
- feat(nyxloom): adopt cmru release orchestration, ship the wheel via mdt (928b7fc2)
- feat(host-setup): interactive host-setup.env wizard (install.sh --wizard) (f9c1623f)
- feat(host-setup): add dev-memory_min_guaranteed.slice (not yet applied to any host) (c4caaee9)
- feat(mdt): mount host debugfs read-only in the devcontainer template (659d94ec)
- feat(mdt,host-setup): self-heal cgroup2 flags by default, inotify-driven per-container MemoryMax, docker.json ownership trim, device-class + kernel-build docs (1195026c)
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
- feat(cmru)!: enforce strict release framework (8dd0e416)
- feat(mdt-host-setup): add dev-buildkitd.slice tier for a shared BuildKit worker (c0f84816)
- feat(host-governance): rename+merge dev-tier cgroup slices, own daemon.json, close preflight/fallback gaps (7e32949c)
- feat(cmru): release projects one after another, not in a shared batch (bc29f43b)
- feat(cmru): release from isolated source-first worktrees (bde0bd11)
- feat(mdt): repair the docker socket relay's half-close timeout at finalize (da0841ed)
- feat(mdt): option for KSM opt-in, add `lnav` for JSONL (074e9f09)
- feat(ciu,mdt): share one io-baseline, and make its provenance explicit (8e87c12c)
- feat(mdt): KSM opt-in by default; chore(ciu): carve physical-root fix (ce70ef73)
- feat: adopt evidence-driven image delivery and handoff design (bfab1abd)
- feat(mdt): add verified OCI registry tooling (773d10b2)
- feat: harden shared browser testing and diagnostics (c7cd3a62)
- feat(mdt): migrate to cmru built-in oci-image handler with repack (d5797c91)
- feat: add PHP 8.5 support and related tooling (acf4acce)
- feat(mdt): in-container finalize_container_environment.py with consumer hooks; modernize templates (c47d1764)
- feat(mdt): add hadolint/grype/cdebug (pre-staged + verified); docker-repack trial script (a02b0de9)
- feat(mdt): container-inspection + IO/swap diagnostics tools; pre-staged + checksum-verified installs (dd168ccd)
- feat(mdt): devcontainer template + initialize_container_environment.py host-bootstrap (55725732)
- feat(mdt): add AI CLI tools (reasonix, deepcode, openclaw) + GHCR visibility sync (4e688faa)
- feat(mdt): unify ciu into wheels.list + ship docker CLI in the image (eee2e27e)
- feat(mdt): externalize first-party wheels (ship cmru in the image) (829e99fa)
- feat(cmru): delegated version strategy + rename build-push.toml → cmru.build.toml (P2 core) (57177aae)
- feat(cmru)!: unify on one cmru.toml schema + cmru.* naming (P1, S-CLI/S2) (6521da28)

### Fixed
- fix(mdt-host-setup): round-2 repairs RC1 -- B7 byte-size parser, mdt-cgprofile.conf rename, S20(a)/(b) vacuous-assertion fixes (2922928c)
- fix(mdt-host-setup): round-1 repairs C10 -- wizard earmark-sum fourth tier (S2) (e6af30df)
- fix(mdt-host-setup): round-1 repairs C6 -- check.sh B3 fail mechanism + M5 (S9, S5 leftover) (6d9da520)
- fix(mdt-host-setup): round-1 repairs C5 -- install.sh B3 WARN + M5 tmpfiles (83521c56)
- fix(mdt-host-setup): round-1 repairs C4 -- D2 gates ManagedOOMSwap, S5/S12 stale phrasing (21ca5e3f)
- fix(mdt-host-setup): round-1 repairs C3 -- cap-watcher covers dev-gates.slice (B1/D1) (b1ec4f2e)
- fix(mdt-host-setup): round-1 repairs C2 -- env.example B5 + D1 cap knob (f0601a17)
- fix(nyxloom): cmru adoption -- pin structlog<27 in mdt toolkit, note GHCR image drift in NL-15 (834d1803)
- fix(host-setup): wizard round-2 review fixes F1/F2/F3 (088e22ae)
- fix(host-setup): wizard round 2 -- resolve_default() blank-value bug, memory-min invariant check, UX pass (7a02c8a9)
- fix(host-setup): wizard review fixes — small-value size formatting, root permission guard (ecc6b943)
- fix(host-setup): render DEV_MEMORY_MIN_GUARANTEED_CEILING into the slice units (27a0fcc1)
- fix(mdt-host-setup): rootless buildkitd needs --oci-worker-no-process-sandbox (ce40537f)
- fix(mdt-host-setup): directory-mount the Docker API socket, not the file (12fb4629)
- fix(mdt-host-setup): grant the rootless buildkitd its socket dir write access (9f5da2f2)
- fix(mdt): push each OCI-layout tag by its own ocidir ref, not a bare dir (f5ccbf16)
- fix(mdt): raise governed builder memory ceiling to stop content-store stalls (a78c6754)
- fix(mdt): serialize bake group targets to avoid content-store lock races (de8578d4)
- fix(cmru): resolve_versions_from_git crash under per-project sequential release (083158cb)
- fix(cmru): eliminate silent version fallback in containerized wheel builds (308ab737)
- fix(mdt): keep release discovery live (025985c6)
- fix(mdt): configure cache through bake targets (35126ca6)
- fix(mdt): extract private build manifest before publication (c404e857)
- fix(mdt): preserve successful release bake status (f24007cf)
- fix(mdt): normalize regctl manifest version (7149e44a)
- fix(mdt): fail closed on malformed repack output (0a57f5dd)
- fix(mdt): govern repack by cgroup pressure (6888c039)
- fix(mdt): extract manifests from repacked OCI layouts (c3165bd3)
- fix(mdt): preserve explicit retry coordinates (96e9ec40)
- fix(mdt): govern OCI-native repack releases (cebdeaa0)
- fix(mdt): install OpenCode platform package directly (69f3c84c)
- fix(mdt): clean staged tool metadata as root (1145097d)
- fix(mdt): polish AI CLI packaging review (c538461a)
- fix(skopeo): install from Debian testing with fallback to trixie (edb27443)
- fix(mdt): correct trial-docker-repack.sh for docker-repack v0.5.0 CLI (deb15724)
- fix(build): set uid/gid on the vscode pip cache mount so caching works as non-root (c99bdf33)
- fix(build): use valid default devcontainers base image (e42e5411)
- fix(mdt): purge stale wheels before staging new version (bd13d456)
- fix(mdt): export staged tool-versions.env so install_ai_cli_tools.py sees resolved tool versions (2d3bb899)
- fix(mdt): allow-list ai-cli-tools.list + install_ai_cli_tools.py in .dockerignore (75f3568c)
- fix(mdt): generate package manifests dynamically per built target only (c70ec667)

### Changed
- modern-debian-tools: finish cgroup governance redesign (18716556)
- revert(mdt-host-setup): withdraw dev-infra.slice (RW-30) -- the daemon ships its own cgprofile.slice (b9e628f5)
- merge(nyxloom): adopt cmru release orchestration, ship the wheel via mdt (41761951)
- merge(assay): B068 + quick-wins -- linked-worktree gap named, 3 RecursionError sites closed, tests/ pyflakes-clean, honest skips outside a repo, crash diagnostics kept (B068/B072/B062/B063/B071/B074) (309bedca)
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- cmru: exclude offline nyxloom from estate builds (d5fe1a19)
- mdt: safe docker DOCKER_SCOPE_BACKSTOP_MEMORY_MAX (97ceaeb6)
- mdt: fix docker socket fix `/run/docker-api` (1eddd1df)
- cmru: quiet flag for output, log reference stage_tool_artifacts.py: use combined `checksums.sha256` first (a812690b)
- perf(mdt): reuse verified release build caches (7c9c61c7)
- build(cmru): register nyxloom as a wheel project; ship wheel into mdt devcontainer (21de72bc)
- build(cmru,mdt): containerize wheel-build, retire ad hoc repo venv, add host-escape (7a7d9b8c)
- wings-cgroups: fix remaining review findings; allow disk paging on every tier (bee7b40f)
- refactor(mdt,gstammtisch): own dev-tier cgroups in mdt host-setup (738e3779)
- refactor: rename groop -> topos (deep, vbpub) (2860d3f5)
- fix mdt env (7e8397d5)
- perf(mdt): keep volatile labels out of build cache (dd44dc1e)
- template devcontainer.json update for opencode (f5ccd009)
- MDT-P01: Codex companion host and OpenCode integration (0a4c999b)
- mdt: update devcontainer template (7048be38)
- mdt: update devcontainer template (1cd68b81)
- Dockerfile: fix skopeo apt source format for Debian trixie (7d321add)
- │ modern-debian-tools-python-debug: overhaul manifest generation — single source of truth, Node/PHP sections, source annotations │ │ Architecture change: │ - Manifest is now generated ONCE inside the Dockerfile from live probes │   (pip freeze, dpkg-query, php -m, tool versions) via manifest_sections.py │ - Post-build extraction (build-push.py extract_manifests) copies the in-image │   manifest into package-manifests-versioned/ so committed and in-image always match │ - Pre-build resolver (resolve-devcontainers-release.py) writes placeholder stubs; │   real manifests come from the image │ - All rendering consolidated into manifest_sections.py — the single shared module │   called both at build time (in Dockerfile) and post-build (extraction) │ │ New manifest content: │ - Dedicated ## Node Runtime section (source: nodesource apt repo) │   with node + npm versions │ - Enriched ## Python & PHP Runtime section with php -m extension listing, │   composer version, and sury.org source URL (conditional on INSTALL_PHP) │ - Installation-source annotations on every section heading, e.g. │   "## AI CLI Tools (source: npm / PyPI / GitHub Releases)" │ - node/npm removed from Custom Tooling table into their own section │ │ Dockerfile: │ - NODE_MAJOR default 24 → 26 │ - PHP_VERSION and NODE_MAJOR now have no defaults in Dockerfile — │   they are architectural decisions set in docker-bake.hcl only │ - Added PHP runtime probes (php -v, php -m, composer --version) │ - Added TARGET ARG for bake target metadata │ - Fixed php_system_packages cross-RUN-layer bug by inlining PHP debs │ │ docker-bake.hcl: │ - Added NODE_MAJOR variable (default 26), passed through base target args │ │ cmru.build.toml: │ - Added NODE_MAJOR to bake_set_vars for both build-images and push-images │ │ package-manifests-versioned: │ - Stale pre-build manifests replaced with placeholder stubs │   (regenerated from image on next build) (c2630a94)
- mdt: cleanup, refactor manifest generator/layout, add copilot CLI (7f8679d2)
- mdt: parallel repack workers + compression/concurrency knobs (dda71702)
- mdt: php8.5 is a tag variant, not a package family (1d965afd)
- `.ghcr-auth.json` - is a runtime fallback for skopeo-based release steps that cannot reuse ~/.docker/config.json. - If we wanted to centralize anything, the right place would be cmru/release tooling generating or passing REGISTRY_AUTH_FILE, not storing the     auth blob itself. (19955664)
- Drop unavailable php8.5 opcache package (2feab4f9)
- Copy staged tool artifacts before Neovim install (17d13b90)
- Use staged tool versions for Neovim install (9b5de1df)
- Export Neovim release pins to build env (c5063d24)
- Pass Neovim build args through release bake (8faa7aeb)
- Add Neovim bake vars to release env (6bca38ed)
- Declare Neovim bake variables (4c5850dc)
- Fix devcontainer cgroup caps and release retries (e5c11978)
- chore(modern-debian-tools-python-debug): repo-backed customization and manifest cleanup (4d0c378c)
- modern-debian-tools-python-debug: drop /root/.cache from cleanup rm list (7161c7c6)
- modern-debian-tools-python-debug: fix cdebug --version invocation (b1f1a4be)
- modern-debian-tools-python-debug: remove deepcode from image build plans (6b55d0c3)
- Fix modern-debian-tools-python-debug release staging (10d741d5)
- perf(build): add BuildKit cache mounts for pip/apt/npm (8da7ad90)
- chore(release): ciu SPEC v4.0.0/Active, minisign in mdt toolchain, signing fast-follow spec (c7e157ec)
- Remove outdated package manifests for modern-debian-tools-python-debug (trixie-py3.14-20260616-4 and -5). Add new toolkit requirements for the development environment. Update manifest_sections.py to include grpcurl in the custom tool order. Modify resolve-devcontainers-release.py to include w3m in system package names. Enhance stage_tool_artifacts.py to stage grpcurl as a new tool artifact. (e5d99b38)
- rename ciu-forge → cmru; ciu v3 flat CLI dispatcher (c411f5f7)
- ciu-forge P6: migrate all consumers → ciu_forge, delete release_manager shim (cb84e7f2)
- release: unified versioned-release architecture + reproducible checksums (10c834db)
- modern-debian-tools-python-debug: release 20260616-5 (ciu 2.0.1.dev12+g55d43da) (80065a15)
- modern-debian-tools-python-debug: unify devcontainer manifests into single file (55d43da4)
- modern-debian-tools-python-debug-vsc-devcontainer: multi version python (46e29cbe)
- add note (17461c98)
- modern-debian: document browser-free policy + multi-Python workflow (cc7dfffc)
- modern-debian image: track new ciu tag scheme + add packaging tooling (2c3133fc)
- modern-debian-tools-python-debug: move to build-push.py (0b6f8ee1)
- modern-debian-tools-python-debug: include ai cli tools (1ae04efb)
-  modern-debian-tools-python-debug: add  CODEX_VERSION, CLAUDE_CODE_VERSION, ANTIGRAVITY_VERSION, AIDER_VERSION, and INSTALL toggles (8346fd28)
- Shared manifest rendering, Project-owned env / release defaults (719810f3)
- Add stable GHCR docs links (6fe3ec95)
- Add versioned GHCR package manifests (cb9d787b)
- update modern-debian-tools-python-debug creation (afbf7bc5)
- unified modern-debian-tools-python-debug /modern-debian-tools-python-debug-vsc-devcontainer (77dc34e2)

### Documentation
- docs(mdt): handoff for cgroup CPU/swap/zswap redesign review fixes (873d99fa)
- docs(mdt-host-setup): round-2 repairs RC2 -- B8 docker-fail-open correction, S16 worst-case share, S19 ordering sentence (f3ef7b80)
- docs(mdt-host-setup): round-1 repairs C7 -- README B4 sequence rewrite, D1/D2/D3/S1/S4/S10/S11 docs, D5 Changes (20d52736)
- docs(mdt-host-setup): dev-gates docs, operator upgrade sequence (M4) (4ec6dcfa)
- docs(host-setup): plan io.cost integration -- device-wide latency-QoS layer (5c383801)
- docs(host-setup): wizard round 2 -- LOG + REPORT (6c94aa01)
- docs(host-setup): plan wizard round 2 -- UX fixes + a real resolve_default() bug (c23967f1)
- docs(host-setup): correct the unit file's own stale "CIU-94 not yet implemented" comment (817cd8d2)
- docs(host-setup): re-land CGROUP-NOTES.md's stale-header fix (lost in a concurrent merge) (ff3d5303)
- docs(host-setup): correct CGROUP-NOTES.md's stale "not built" framing (51427d60)
- docs(host-setup): mdt-host-setup-wizard LOG/REPORT (6e95ab77)
- docs(host-setup): plan an interactive host-setup.env wizard (7bd8ba67)
- docs(host-setup): document vm.swappiness settings + the tool/metric to validate one (099e33c1)
- docs(host-setup): design the dev-memory_min_guaranteed tier (not built) (f248d981)
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)
- docs: zswap writeback verification + toggle procedure; record pool drift (9e120d83)
- docs(mdt): rewrite README for the matured layout (d888f128)
- docs(mdt): refine build load attribution (d535ce3e)
- docs(mdt): map release load and cgroup boundaries (7a692d35)
- docs(mdt): explain and harden governed release builds (3fa08133)
- docs(mdt): carve AI CLI packaging handoff (4bb777e1)
- docs(readme): add notes on docker buildx/bake and docker-repack usage (f5d9eebe)
- docs(mdt): correct the ciu-dependency TODO — ship+encourage ciu, never enforce it (e765c449)
- docs(ciu+mdt): config-architecture note (env stays for secrets; HOST_MDT_TMP); mdt no-ciu-dependency TODO (0a217364)
- docs(mdt+ciu): canonical devcontainer-lifecycle doc; awesome-docker post-launch TODO for ciu (a7106d95)
- docs(bake): keep release group print stable (7beeff0b)
- docs(bake): self-documenting groups + per-variable comments (8b81ec8f)
- docs(mdt): pristine Container Doctrine (lean devcontainer; deps in their own containers) (080195ba)

### Testing
- test(mdt-host-setup): round-1 repairs C1 -- test-render.sh B2/B6/S7/S8/S14 (244728c0)
- test(mdt-host-setup): add renderer test, wire into the registered gate (62927f1f)
