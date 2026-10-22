# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [source-174351ab708e] - 2026-10-21
<!-- cmru: generated -->
<!-- cmru: source-end=174351ab708e540e0d7781d0646b1899c9feaedf -->

### Changed
- mdt-cockpit: MDTC-05 skopeo temporary pin 601 -> 701 (66ae8550a)
- mdt-cockpit: MDTC-05 review fixes - vendor origins at 700, oldstable wording (66ad84a0c)
- mdt-cockpit: MDTC-05 apt priority policy by archive name (backports pin was inert) (cb92959f4)
- mdt-cockpit: skopeo/testing step after the last other apt update; capture dry-run output (no pipefail SIGPIPE race) (29c152164)
- mdt-cockpit: review fixes (priority-100 docs, testing COPY before skopeo, skopeo fails closed, oldstable pins checked) (8e3433a61)
- mdt-cockpit: docs triad, CHANGES and tests for gdu/dust/testing; pwmcp examples to 1.64.0-r3 (d5c01b6d6)
- mdt-cockpit: Debian testing visible but pinned to 100 (one stanza per archive) (b867e4346)
- mdt-cockpit: add gdu 5.38.0 and dust 1.2.6 (pinned, sha256-verified) (1e7947d2f)
- SWAP-01 round 2: M5 title quote, guide section cite, [I] on the 30-60 suggestion, UTC time (a6d127942)
- SWAP-01: vm.swappiness low (10) on these hosts; resolves GG-6 (a4bc5b211)
- mdt-cockpit backlog: drop the old-project-name mention (rename guard) (43e5e2986)
- DOCS-FOLD-01 round 2: repoint MDT-021 and DESIGN-GUIDE to MEMORY-ARCHITECTURE; drop process remarks (aacb94f24)
- DOCS-FOLD-01 review fixes F-01..F-08: memory.low claim corrected, boost chain labelled, one canonical home per fact, 125 s, stale rows, README:130, MDT-022 citations (fa215e600)
- DOCS-FOLD-01: fold operator-approved facts A-D into the docs (c5b79f9f2)
- mdt-cockpit host-setup: keep the pre-rename project name out of a lib comment (MDT-HS-07) (1b7392713)
- mdt-cockpit host-setup: MDT-HS-07 review round 3 (H-71 daemon chain, H-69 pause reader) (a814ec5af)
- mdt-cockpit host-setup: MDT-HS-07 review round 2 (H-65, H-66, H-67, the re-assert pause) (a6cc60b7b)
- mdt-cockpit host-setup: check.sh sub-ceiling min() and conditional mdt-buildkitd restart (MDT-HS-07 live findings) (7929bae28)
- mdt-cockpit host-setup: MDT-HS-07 review fixes (H-58, H-59 and non-blocking items) (254021d80)
- mdt-cockpit host-setup: IO scheduler and io.cost as host-setup keys; BFQ retired as the weight mechanism (MDT-HS-07) (588cd6ca2)
- mdt-cockpit host-setup: MDT-HS-06 review fixes (H-55, H-56 and nits) (de42b1dd9)
- mdt-cockpit host-setup: MDT-HS-06 docs, backlog incident record, VM spelling-migration step (089bd4546)
- mdt-cockpit host-setup: apply and verify the whole IO batch, reload once for all units (MDT-HS-06) (6a16e2488)
- mdt-cockpit host-setup: MDT-HS-05 review fixes (H-46..H-51) (0210aa4dc)
- mdt-cockpit host-setup: reload udev rules when MDT's queue rule is removed (MDT-HS-05) (8ab2732e2)
- mdt-cockpit host-setup: writeback and root-queue settings as host-setup keys (MDT-HS-05) (bc3dcfbc4)
- mdt-cockpit host-setup: persist the self-heal reload limiter across runs (MDT-HS-04 review H-39..H-41) (d383783f2)
- mdt-cockpit host-setup: allowlist the read-only IO device helpers in the install preflight scan (MDT-HS-04) (113127462)
- mdt-cockpit host-setup: match the IO_SD_DEV derivation inside install.sh's if (MDT-HS-04) (e8748de55)
- mdt-cockpit host-setup: test-render exempts the install-derived IO_SD_DEV from the env.example rule (MDT-HS-04) (9c3818cc7)
- mdt-cockpit host-setup: self-heal stale per-spelling IO limit entries with one daemon-reload (MDT-HS-04 live evidence) (561c161c1)
- mdt-cockpit host-setup: systemd gets /dev/<DEVNAME> of the whole disk; split install messages; MDT-020 (MDT-HS-04 review H-33..H-35) (f4e1f5ab2)
- mdt-cockpit host-setup: IO caps use /dev/block/MAJ:MIN and reset-then-set in one call (MDT-HS-04) (79ed3c7d2)
- mdt-cockpit host-setup: check.sh reports guard state distinctly, unreadable journal, rm race (MDT-HS-03 review H-29..H-31) (2ee58ffea)
- mdt-cockpit host-setup: guard survives vanished containers, rolling malformed window, throttled sweep (MDT-HS-03 review H-25..H-28) (a0ec58bcb)
- mdt-cockpit host-setup: guard reads Actor.ID and survives one malformed event (MDT-HS-03) (ecc7ee741)
- mdt-cockpit host-setup: MDT-HS-02 review fixes (H-18..H-20, H-22..H-24) (f872df62c)
- mdt-cockpit host-setup: buildkitd socket wait 60 s; upgrade acceptance shows the install output on failure (cab998e80)
- mdt-cockpit: MDT-HS-02 item 4 (devcontainer template audit) and VM upgrade acceptance (48ddc9218)
- mdt-cockpit host-setup: MDT-HS-02 items 1-3 (optional keys single source, DAMON default yes, guard re-confirmation) (8ff509d82)
- mdt-cockpit host-setup: README wording matches the doc test (inactive) (1cb2fde93)
- mdt-cockpit host-setup: final round (H-15, H-16, H-17) (2471120d4)
- mdt-cockpit host-setup tests: order check uses the destroy command, not a comment (e38aae5fa)
- mdt-cockpit host-setup: review fix round 2 (H-11..H-14) (e8e4dd0b9)
- mdt-cockpit host-setup tests: guest install wrapper runs ./install.sh from host-setup/ (815f94a35)
- mdt-cockpit host-setup tests: shared-definition comparison uses the same labels (43ab605f4)
- mdt-cockpit host-setup: review fix round 1 (H-01..H-10, buildkitd timeout, VM diagnostics) (6bc43bc35)
- mdt-cockpit host-setup tests: fixture fixes from the first smoke run (206976032)
- mdt-cockpit host-setup tests: watcher fixture uses schema 3 and an explicit baseline checker (862871eda)
- mdt-cockpit host-setup: per-tier IO caps, VM governance acceptance, plausibility in both consumers (MDT-HS-01 A1) (946d60f73)
- mdt-cockpit host-setup: dev.slice IO governance that is really in effect (MDT-016) (a823bda37)
- backlog: file A-06 (Calls defaults), A-11/A-12 (tester-base TB-01/TB-02), B-06 (MDT-015) from INT-01 review (9eaf6f960)
- mdt-cockpit: credsStore message and comment, aider window clock (review B-04, B-05) (3f3b62701)
- mdt-cockpit README: resolve committed conflict markers; add a repo-wide marker guard (review A-01) (236cd57fe)
- Merge mdt-home-persistence-20261018 (daf47a2e9) (INT-01 step 4) (5e2dff706)
- mdt-cockpit: aider deps_before is held to the estate window; an override released inside the window is shown as waiving it; the doctor never echoes an invalid credsStore name (MDTC-03 Z1-Z3) (daf47a2e9)
- mdt-cockpit: dependencies obey the 14-day window (npm --before / pip --uploaded-prior-to), record cross-checks, override reasons, exact 28-day boundary, manifest cutoffs; advisory docs and test (MDTC-03 Y1-Y3, Y5) (feff2d485)
- mdt-cockpit: label the eight OCI version records advisory (not consumed by the build, MDT-009) (MDTC-03 Y1) (a989b6d55)
- mdt doctor: a credsStore is queried with the helper's list verb, never get; absent helper reads 'cannot confirm' (MDTC-03 Y4) (a96c1e968)
- mdt-cockpit: the venv installer layer loads tool-versions.env (aider's commit pin was unset at build time; -18 build failed there) (574c1c925)
- mdt-cockpit: Docker Hub login docs + non-fatal mdt doctor notice, AI CLI version docs, cmru gaps G6/G7 and backlog (MDTC-03 X2/X3) (ff2f4185a)
- mdt-cockpit: stage copilot/openclaw/claudelink from the committed cmru-versions record; aider pinned to an exact main commit (MDTC-03 X1) (8e88ca315)
- mdt-cockpit: make cmru versions resolvable - literal patch tags for the OCI targets, npm targets for copilot/openclaw/claudelink, committed resolved state (MDTC-03 X1) (561b9df44)
- mdt-cockpit: the oci-push generated-path allowance mirrors cmru's changelog rule (W3) (78447b4fe)
- mdt-cockpit: the oci-push layout is bound to the current build; the target is explicit; the private tag is always removed (V4) (48bb2b44e)
- tester-base: the release version is derived late from VERSION, the push logs in explicitly and pushes the version tag last (V1, V2); cmru gaps recorded (908ca11e3)
- mdt-cockpit: the smoke lane also deselects the OCI-layout push module explicitly (R6 completion) (9288d680e)
- mdt-cockpit: zsh cases fail when required (D-003), oci-push image derived from the release (D-006), explicit bake-test deselection (R6) (d5140dabd)
- mdt-cockpit: the buildx-dependent bake-file test moves to the oci-push lane (T8 skip-diff) (cd119a5f8)
- mdt-cockpit: oci-push lane runs the OCI-layout push test inside the mdt-cockpit image (MDTC-02 T6) (3cd0b0131)
- mdt-cockpit: step 0 (M1-M3) exact recorded versions for codex/reasonix/pi/opencode, published npm pins, one install-enabled rule (b8aca9fc1)
- mdt-cockpit: handoff MDTC-02 (tester-base) with controller amendments A1-A6 (317a37d55)
- mdt-cockpit: history parser passes non-RUN base layers (debian.sh) but still fails unrecognised RUN forms (cfafccf32)
- mdt-cockpit: R16 version policy for user-updatable tools; N7 history parser for bash; N8 skip Claude resolution; N9 /etc/profile assertion (92da43cdb)
- mdt-cockpit: the login hook de-duplicates PATH, keeping the first occurrence (N6) (5a85bd9a3)
- mdt-cockpit: record the measured 00-restore-env growth in login shells (dc0ab38e8)
- mdt-cockpit: cleared-environment PATH expectation names the shell's own default (env -i bash) (876cbabc9)
- mdt-cockpit: login shells keep runtime PATH additions; the hook merges the managed entries (N1, operator decision) (532b03d8e)
- mdt-cockpit: document the measured login-shell PATH limit (N1) (836dbb3af)
- mdt-cockpit: round 3 - Claude version is required and age-checked, local-dev purges same-distribution wheels, history parser ignores the ARG blob (N2-N5) (5b97af1d0)
- mdt-cockpit: zsh login shells get the image PATH too (found by the lane on the -10 images) (ca89e30b1)
- mdt-cockpit: ignore the generated pip/wheels/RESOLVED.json report (79d271696)
- mdt-cockpit: lane scenarios drive tools by what they show; nvim starts the tree once; finalizer runs in the template environment (R3, R4, R5) (1f94cbd65)
- mdt-cockpit: Claude Code is an npm-global install under /opt/mdt/npm; update policy documented (R14, R15) (f9ac5abd0)
- mdt-cockpit: permission-layer detector catches find -exec and metadata-only rewrites (R13); ZSH_COMPDUMP doc corrected (R12) (e71efa9f4)
- mdt-cockpit: the rename guard scans every text file for both spellings and its retired-name list can match (R11) (ffabf0808)
- mdt-cockpit: the D1 notice prints a full GitHub URL (R7) (d39c2c4d0)
- mdt-cockpit: publishing requires the exact release label (R8) (b2e41be16)
- mdt-cockpit: mdt-perms requires g+rw, pins its group, and fails closed (R6, R8) (47d84b952)
- mdt-cockpit: PATH hook reads a build-time file; provenance compares wheel content (R2, R10) (9bd51ca65)
- mdt-cockpit: local-dev wheels travel in their own context; the wheel set must equal the resolver report (R1, R9) (685a1752a)
- mdt-cockpit: mdt-perms wrong-group check no longer needs a second group membership (C7) (baacee61d)
- mdt-cockpit: smoke lane reports skip reasons (-rs) (2f68fe938)
- mdt-cockpit: an empty or incomplete first-party wheel set fails the build in every mode (C6) (5e68efc1d)
- mdt-cockpit: build-mode label is asserted present; local-dev provenance cannot skip (C4, C5) (51eba89c5)
- mdt-cockpit: retire the old display name from image labels and templates; drop stale console logs (C1-C3) (1810c90e0)
- mdt-cockpit: stop the ordering test leaking MDT_BUILD_MODE into other tests (6c5af0240)
- mdt-cockpit: close MDT-006, document /srv claim and measured AI CLI update paths (9743ad0bc)
- mdt-cockpit: image-runtime lane, runtime scenarios, and fixes from the first real build (3427dd734)
- mdt-cockpit: local-dev build sets the build mode before evaluating the tag plan (1eeee8e38)
- mdt-cockpit: fresh-home redesign, mdt group, system-wide defaults, local-dev build (MDTC-01 phase 1) (d41ee1a40)
- mdt-cockpit: handoff MDTC-01 (fresh-home redesign + runtime tests) (d834ea8eb)
- mdt-cockpit: baseline fork with full-home persistence (pre-review) (ad46d4fa1)

## UNRELEASED

### Fixed (MDT-HS-08)
- host-setup: every build on the host now reaches `mdt-managed` whoever starts it. Live incident 2026-10-22: VS Code Remote-SSH "Reopen in Container" failed with `No such container: buildx_buildkit_keen_mestorf0` because the Dev Containers CLI (a child of VS Code's non-login SSH server, user shell zsh) never read `/etc/profile.d`, fell back to a stale per-user `docker-container` builder, and `mdt-buildkit-guard` removed its worker. New `docker-buildx` Docker CLI plugin shim (`host-setup/scripts/docker-buildx-shim.sh` -> `/usr/local/lib/docker/cli-plugins/docker-buildx`, ahead of the packaged plugin in Docker's search order) selects `mdt-managed` when `BUILDX_BUILDER` is unset or the CLI-injected `default`, registers the `remote` node (`default-load=true`) in the caller's own Buildx state on first use, never falls back to the built-in builder, and leaves a named builder, a non-`unix://` `DOCKER_HOST`, the metadata handshake and `MDT_BUILDX_PASSTHROUGH=1` alone. A marked `BUILDX_BUILDER`/`BUILDKIT_HOST` block in `/etc/environment` covers PAM sessions; `install.sh` proves a fresh user with an empty environment resolves `mdt-managed` (exit 2 otherwise). Design, measurements and rejected alternatives: `docs/BUILD-ARCHITECTURE.md` "Making every build use mdt-managed". Oracle `host-setup/tests/test_docker_buildx_shim.py`
- host-setup: `/etc/profile.d/mdt-buildkit.sh` no longer exports `BUILDX_CONFIG=/etc/mdt/buildx`. Measured: that root-owned directory fails every non-root build (`permission denied` on `activity/`, then `refs/`) because Buildx writes build state there on each build; users build from their own `~/.docker/buildx`. A stale export from an older shell is dropped by the shim; `/etc/mdt/buildx` remains the installer's root-owned reference and reachability proof. `mdt_buildkit_builder.py` runs Buildx with `MDT_BUILDX_PASSTHROUGH=1` (it manages builders explicitly) and registers with `--driver-opt default-load=true`
- host-setup: warnings and errors name the variable or file that decides them and say what to change. New shim/installer/check messages carry the cause, the path or variable involved (`BUILDX_CONFIG`/`DOCKER_CONFIG`/`HOME`, the socket, the plugin directories) and the way out; the shim registers under the account's passwd home when `HOME` is unset (as the Docker CLI itself does), and shows Buildx's own output when registration fails. Existing install-time warnings now name their variables: `WARN: DEV_IO_CAP_PCT=30 is outside the recommended 60-80 band ...`, and the memory `REVIEW WARNING`s name the `DEV_*_MEMORY_*` keys to change. Bootstrap errors for conflicting spellings, a source mounted as both home and `/tmp`, an unresolvable cgroup slice and the fallback set say which `devcontainer.json` entry or host step to fix
- host-setup: the guard's TERMINATED/REPORT-ONLY line for an unapproved worker names the builder (from the container name) and the remedy (`docker buildx rm <builder>`, build via the shim or `--builder mdt-managed`); `mdt-host-check.sh` reports the shim, the `/etc/environment` block and a fresh-user probe
- host-setup: every variable in `host-setup.env.example` states its allowed values on a `# Allowed KEY[, KEY...]: <grammar>` line (brace groups allowed); `host-setup/tests/test_env_example_allowed_values.py` fails for a variable without one or a line naming a variable that is not set
- devcontainer bootstrap (`templates/initialize_container_environment.py`): the home/`/tmp` role of a `$HOME` source comes from the mount **target** (`/home/<remoteUser>`, `/tmp`), not from the names `mdt-cockpit-home`/`mdt-cockpit-tmp`; a consumer that renames its sources (for example `mdt-cockpit-vbpub-home`) now gets `0700`/`1777` instead of `0755`, and the old-state notice names the source actually mounted. One source mounted as both home and `/tmp` is refused. The `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` error names the variable, `/etc/mdt/host-setup.env`, the allowed values and the exact `mkdir -p`/`touch` for the missing path; the start-up banner lists the allowed values. `test_loaded_host_cgroup_unit_must_have_an_installed_file` no longer depends on whether the machine running it has the real unit installed

### Fixed (MDTC-05)
- apt priorities: the old `backports.pref.in` rendered `Pin: release a=trixie-backports`, which matches no archive (`a=` compares the Release `Suite:`, here `stable-backports`; `trixie-backports` is the `Codename:` for `n=`). Measured with a synthetic index: backports stayed at its built-in 100 (`NotAutomatic`), so the image never preferred backports. Replaced by the operator's `apt/debian-priorities.pref` (`/etc/apt/preferences.d/debian-priorities`: `a=stable` 500, `a=stable-backports` 600), copied as is; the `@SUITE@` template and `/etc/apt/preferences.d/backports` are gone. The backports source stays codename-based; rebuild when trixie becomes oldstable (DESIGN-GUIDE). The system-package install now takes backports versions where offered. Vendor repositories (sury PHP, pgdg, redis.io, docker.com) are pinned to 700 by origin in `apt/vendor-origins.pref` so backports (600) cannot replace their packages; the oracle requires a pin for every vendor host the Dockerfile adds. The temporary skopeo pin is 701 (was 501) so neither a vendor (700) nor a backported (600) skopeo can beat testing's. Oracle `scripts/test_apt_preview_archives.py`; image check `tests/runtime/test_apt_preview.py`

### Added (MDTC-04)
- Tools: `gdu` 5.38.0 (released 2026-10-05; sha256 from upstream `sha256sums.txt`) and `dust` 1.2.6 (released 2026-09-16; upstream publishes no checksum, sha256 pinned in `stage_tool_artifacts.py`), static linux/amd64, pinned to the newest release at least 14 days old
- Debian `testing` as a permanent apt source (`apt/testing.sources`) pinned with `oldstable` and `oldstable-backports` to priority 100 (`apt/preview-archives.pref`, one stanza per archive: a comma list in one `Pin: release` line is an AND and matches nothing); install a package with `apt install -t testing <pkg>`. The skopeo step uses it with a temporary skopeo-only pin removed in the same layer. Oracle `scripts/test_apt_preview_archives.py`; image check `tests/runtime/test_apt_preview.py`
- pwmcp examples re-pointed at 1.64.0-r3 (the build derives the pinned release and `get.py` sha256 from `pwmcp/pwmcp.contract.json` and `pwmcp/get.py` at the source commit; the published `get.py.sha256` f91c7018... matches)

### Added (MDT-HS-07)
- host-setup (review round 3): buildkitd is idle only when the container scope holds exactly the measured daemon chain `rootlesskit` -> `exe` -> `buildkitd` (comm and parent chain); any `buildkit-runc` or other process is busy; the pause reader enforces the 240-minute bound itself and refuses a symlink, a non-regular file, a file or directory not owned by the reconcile's user, or a group/world-writable one
- host-setup (review round 2): the re-assert is split by mechanism (udev trigger / `sysctl -p` / `_mdt_apply_iocost`) with a 30-minute back-off after a failed re-apply, and an explicit time-bounded pause (`mdt-io-reassert-pause <minutes> <reason>`, shown by `mdt-host-check.sh`); the running mdt-buildkitd records what it was started from, so a pending restart is reported by install.sh (exit 2), check.sh and the reconcile and never as "unchanged"; busy detection reads the buildkitd container scope; `--restart-buildkitd` documented
- host-setup (live findings): `mdt-host-check.sh` computes the same `min(sub-ceiling %, absolute pin)` as the reconcile for dev-gates/dev-buildkitd IOPS, so a correct pinned state is no longer a WARN; plain `install.sh` restarts `mdt-buildkitd.service` only when its rendered unit or config changed, and not while a build may be active unless `--restart-buildkitd` is given (WARN, old service kept, non-zero exit)
- host-setup (review fixes): a scheduler is accepted when listed OR available as a module; the minute reconcile verifies and re-applies the scheduler, queue values and io.cost on drift (logged); the legacy path notices a live scheduler that differs from what its rule re-applies; a kept non-identical BFQ rule is reported also under `IO_ROOT_SCHEDULER=kernel`; the VM io.cost acceptance proves the live apply with mq-deadline or `none` (no refusal-only pass); VM budget margin raised to 15 minutes
- host-setup: `IO_ROOT_SCHEDULER` (mq-deadline | none | bfq | kernel, checked against the root disk's scheduler list), `IO_COST_MODEL` (`kernel` = ctrl=auto, or six coefficients) and `IO_COST_QOS` (`off` = enable=0, or six numbers within the kernel's bounds), runtime-optional (empty/absent = not managed, the legacy BFQ rule is kept), asked by the wizard (model from an identity-verified IOB-02 result > existing > `kernel`; QoS existing > `off`). install.sh renders `60-mdt-io-scheduler.rules` for the derived disk (replacing the BFQ rule; a non-MDT BFQ rule only when byte-identical to a known repo copy), writes `io.cost.model` and `io.cost.qos` for the whole disk's MAJ:MIN and reads both back field by field; `mdt-io-cost.service` re-applies them at boot from the env. BFQ is retired as the weight mechanism: IOWeight comments are scheduler-conditional. `mdt-host-check.sh` reports the live weight mechanism (io.cost / bfq / none) from the live files and the scheduler, model and QoS states, and no longer claims "IOWeight is inert" under io.cost

### Fixed (MDT-HS-06)
- host-setup: the IO reconcile applies every managed unit (dev.slice and each capped tier), verifies ALL, reloads systemd ONCE if any disagrees while `systemctl show` reports the intended values, re-applies EVERY unit and re-verifies all; the persisted reload limiter stays. Live incident 2026-10-20: healing one unit reloaded, the next unit then failed as "reload suppressed" and was left uncapped. The VM lane gained a two-spelling migration step; MDT-020 (device-mapper root) stays open with the incident recorded

### Added (MDT-HS-05)
- host-setup (review fixes): interim files are retired only when MDT's own file carries every key they set (else kept with a WARN); the wizard derives the root disk (`_mdt_root_disk_node`) instead of `IO_DEV_PATH`; benchmark proposals are defaulted only with a verified identity beyond the device number; values reject leading zeros, `dirty_bytes` below two pages and an unaligned or sub-page `max_sectors_kb`; `mdt-host-check.sh` reports "live only, not persisted" and notices later overrides
- host-setup: four keys `IO_WRITEBACK_DIRTY_BYTES`, `IO_WRITEBACK_DIRTY_BACKGROUND_BYTES`, `IO_ROOT_QUEUE_NR_REQUESTS`, `IO_ROOT_QUEUE_MAX_SECTORS_KB` (positive integer or `kernel`, runtime-optional), asked by the wizard (benchmark proposal > existing > `kernel`, measured values shown only as a labelled example), validated by one shared validator, rendered by install.sh as `/etc/sysctl.d/90-mdt-io-writeback.conf` and `/etc/udev/rules.d/61-mdt-io-queue.rules` (derived whole disk, sorted after the BFQ rule), applied and read back (non-zero exit on mismatch), the interim files retired only after a verified apply; `mdt-host-check.sh` reports OK / MISMATCH / not managed / could not read per key

### Fixed (MDT-HS-04)
- host-setup: IO caps are given to systemd as `/dev/<DEVNAME>` of the derived whole disk (from sysfs uevent; survives dynamic major renumbering) (reconcile, per-container watcher, rendered `dev.slice` static lines) because `/dev/mapper/<lv>` left the kernel `io.max` empty on systemd 257; every IO `set-property` resets then sets each key in one call so a stale spelling cannot shadow the cap; log lines name the path given to systemd; when the kernel io.max disagrees with `systemctl show` (stale per-spelling all-max entry inside systemd), the reconcile warns, runs one `systemctl daemon-reload`, re-applies and re-verifies, at most once per 30 minutes across runs (stamp file `/run/mdt/io-selfheal-reload.stamp`, cleared by a clean run) and only when show reports all intended values; refusals say what was tried and list the other causes

### Fixed (MDT-HS-03)
- host-setup: `mdt-buildkit-guard` reads the container id from `Actor.ID` (Docker 29 no longer sends top-level `id`) and no longer crash-loops; disagreeing ids are refused, non-container events ignored, and one malformed event triggers a throttled full reconcile instead of an exit (five in a row, or 20 in 60 s, exit); a container vanishing before inspect is skipped (daemon errors still fail); full 64-hex ids required and the sweep uses `--no-trunc`; `check.sh` reports the guard's state distinctly (not installed / not running / active with N restarts) and a journal it could not read as such, not as zero malformed events; `docker rm -f` of a container already gone is treated as gone

### Fixed (MDT-HS-02)
- host-setup: `install.sh` on an existing env file no longer refuses the runtime-optional keys: ONE definition (`host-setup/runtime-optional-keys.re`) is read by both the strict Python validator and the shell helper; present optional keys are still validated, render-time keys still refuse
- host-setup: a non-wizard re-install with an already-confirmed `terminate` guard policy keeps the running guard; the confirmation is recorded in `/var/lib/mdt/guard-terminate.confirmed`; a refusal now happens before any mutation (pre-flight / COMMIT POINT split)

### Changed (MDT-HS-02)
- host-setup: `DAMON_STAT_DISABLE_DEFAULT` now defaults to `yes` (operator decision 2026-10-20)

### Fixed
- host-setup: the dev.slice IO reconcile no longer resets the runtime IO list to empty (which removed the unit-file cap as well); it re-asserts explicit values, reads the kernel `io.max` back after every apply and exits non-zero on a mismatch (MDT-016)

### Added
- host-setup: operator-pinned `DEV_IO_RBPS_MAX`, `DEV_IO_WBPS_MAX`, `DEV_IO_RIOPS_MAX`, `DEV_IO_WIOPS_MAX` runtime keys; baseline schema 3 with a plausibility refusal (old results are stale); `UNCAPPED SWAP` warning; `mdt-cgroup-io-limits.sh`. The 20 new pin keys and `IO_BASELINE_ROTATIONAL_FLAG` are runtime-optional: absent from an existing `/etc/mdt/host-setup.env` they mean "not set", a plain `install.sh` works and prints a NOTICE naming the effective cap source; run `install.sh --wizard` to review them

## [source-998a43552c36] - 2026-10-02
<!-- cmru: generated -->
<!-- cmru: source-end=998a43552c36a51bc4a87932bd40098f227fd93d -->

### Fixed
- fix(mdt): make host resource policies explicit (df874d003)

### Changed
- mdt: set fs.inotify.max_user_watches = 524288 (10a344e2f)

### Documentation
- docs(mdt): clarify shared Codex session storage (82176d26a)

## [source-ec95f86b1edd] - 2026-09-30
<!-- cmru: generated -->
<!-- cmru: source-end=ec95f86b1edd51315be821b5b3f966423b96452d -->

### Added
- feat(mdt): persist codex profiles across disconnects (69ae760b)
- feat(mdt): classify missing bind sources by spelling (5e2c95d9)

### Fixed
- fix(mdt): seal BuildKit socket path before permission change (0af1b2d7)
- fix(mdt): escape systemd stat format specifiers (0d7f8df4)
- fix(mdt): grant docker group access to BuildKit socket (51248fdc)
- fix(mdt): expose parser to user image layer (7d6757f8)
- fix(mdt): make build inputs readable under release umask (6981838f)
- fix(mdt): expose staged version inputs to image user (133dd1d6)

### Changed
- merge: integrate origin main with local main (9f3158f0)
- doc(mdt): plan do not use user home (c4dbc4f8)

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
