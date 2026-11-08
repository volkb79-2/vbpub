# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Added
- feat(toposd): publish the configured observe socket through `augeaswrap.endpoints`, preflight collisions, verify `endpoint-record` in doctor, and withdraw the fragment first during uninstall (E2, 0.8.0)
- feat(toposd): `toposd install --augeaswrap-wheel VERSION@sha256:...` (required for 0.8.0 and later) installs the digest-pinned augeaswrap wheel `--no-deps` beside topos; augeaswrap is on no package index, so the daemon lock excludes it and the lock builder never sends it to `pip download` (E2, 0.8.0)

### Changed
- feat(topos): make daemon clients environment-only through `TOPOS_OBSERVE_SOCKET`; remove default client socket paths and return a named usage error when the endpoint is unset (E2, 0.8.0)
- fix(topos): a relative `TOPOS_OBSERVE_SOCKET` or `--socket` is refused with exit 2 naming its source (E2, 0.8.0)

<!-- cmru: release history -->

## [0.7.0] - 2026-11-07
<!-- cmru: generated -->
<!-- cmru: source-end=f59fc964efa25b723d5fbb81a86974f6cccf6d05 -->

### Added
- feat(toposd): topos.service runs toposd -t as ExecStartPre (c246f4471)
- feat(toposd): P-5 group checks, --add-user and toposd -t (eb1d5461c)
- feat(toposd): refuse docker as the observe group (2b40ef1d1)
- feat(toposd): verify socket activation in service doctor (00f5b6ce5)
- feat(toposd): install, uninstall and doctor; hash-pinned daemon lock; old-install refusal (P-7, P-9, P-11) (641f7e05c)
- feat(topos): add socket-activated host service (4ca769e94)

### Fixed
- fix(toposd): bound a failing ExecStartPre with a start limit (6ef646471)
- fix(toposd): changelog, forgotten-user dedup and comment from review (408cc2f03)
- fix(toposd): review fixes for P-5, --add-user and -t (f9bbed6d9)
- fix(topos): docker-group follow-ups from review (8a44e9e16)
- fix(toposd): dangling config symlink, uninstall advice and docs (64fc4e31e)
- fix(toposd): review fixes for the cgprofile alignment (982ad56e9)
- fix(toposd): recover interrupted install and uninstall (00b396a75)
- fix(toposd): defer removal of the running version (0d31136f8)
- fix(toposd): apply explicit observe group to config (031415f2b)
- fix(toposd): require observe group on noninteractive install (3b5304a59)
- fix(toposd): a re-apply after a failed upgrade reloads and restarts (509fb0dd7)
- fix(toposd): review round 2 (failed-upgrade rerun, rerunnable uninstall, fixed layout) (dbd6ae80e)
- fix(toposd): install, uninstall and doctor review fixes; complete test coverage (3c2029a53)
- fix(toposd): repair fix-round regressions; runtime errors exit 1 (32a155d96)
- fix(toposd): address host socket review findings (5ab5b956f)
- fix(topos): keep the topos-suite lane parallel (f3ec8ab6d)
- fix(toposd): report state directory overlap (2ea2eeb68)
- fix(toposd): name unsupported socket level (f2b88c15e)
- fix(topos): clarify sysusers plan step (ce7ca1dc6)

### Changed
- Merge remote-tracking branch 'origin/main' into host-sockets-topos-20261105 (6447ee65d)

### Documentation
- docs(topos): TOP-SQUEEZE-THREADS: the restore guard's signal mask is per thread (e2c1ae768)
- docs(topos): name the capture behind each reply size (81a1d318d)
- docs(topos): reply sizes per view list, decode figures labelled, protocol item states what each cut needs (f5c20cf59)
- docs(topos): correct the current-reply size, add decode cost and the protocol direction (bb3f3a4c8)
- docs(topos): plan evidence rows use the recorded sampler and yardstick runs (75539c382)
- docs(topos): plan drops the settled Rust decision and restates sampler ratios (e733e90ef)
- docs(topos): plan states what is done and where the branches stand (827f87e8c)

### Testing
- test(toposd): in-process coverage for owned-node and self-bind refusal paths (cf961c797)
- test(toposd): cover service boundary failures (00b176e44)
- test(toposd): pass strings in argv lists (a2be443f4)
- test(topos): follow daemon helper extraction (5175150d9)
- test(topos): model systemd metadata in status CLI (1573ed224)
- test(topos): model systemd-owned status socket (9f6fc3bad)
- test(topos): accept daemon socket mode in fake (fbeec9537)
- test(topos): update daemon listener fake (09ecbdce4)
- test(topos): note that the signal test relies on running in the main thread (172d909a3)
- test(topos): the restore-under-signal test signals its own thread; None handlers are not restored (9ddc91379)
- test(topos): the squeeze guard test restores every guarded signal, not only SIGTERM (055957074)

<!-- cmru: pending release notes begin -->
- feat(toposd): add pinned `install`, `uninstall`, and `doctor` host-service workflows for topos 0.7.0 and later: immutable versioned venvs, recoverable install records, targeted observe-group config edits, activation-aware service checks, deferred removal of the running version, and safe partial cleanup when group removal fails
- feat(toposd): refuse `docker`, GID 0 and the docker GID as the observe group on every input path (flag, install record, `toposd.toml`, prompt), exit 2, no override; `--add-user observe=USER` (repeatable, `gpasswd -a`, members never removed) replaces `--observe-users`; `toposd config check` and `-t` refuse such a group where it exists; the doctor `groups` check fails on those GIDs and lists members; a recorded user who no longer exists is dropped from the record and reported instead of blocking the install
- feat(toposd): `toposd -t [--config PATH]` tests the config (same as `config check`, exit 0 or 2); `topos.service` runs it as its `ExecStartPre`
- feat(topos/release): generate and publish the sha256-pinned daemon dependency lock beside each topos wheel
- fix(topos): remove `topos daemon preflight` and `install-plan` without alias (use `toposd doctor` and `toposd install --dry-run`); `topos daemon status` stays as the client check, sends hello, and accepts any socket group unless `--group` names one
- feat(topos/daemon): systemd owns the `observe` socket at `/run/topos/observe/topos.sock`; daemon state moves under `/run/topos/state/`; the old `/run/topos/topos.sock` is no longer served, so an upgrading host needs a fresh install
- feat(toposd): add `run`, `unit print|check`, and `config check`, with explicit `--self-bind` for tests and development
- fix(topos): point client defaults at the socket unit and remove `topos daemon serve`
- fix(topos/deploy): render socket, service, slice, and sysusers files from `/etc/topos/toposd.toml`; remove the tmpfiles asset
- fix(topos): route daemon BPF snapshots and squeeze recovery records under service `state_dir`
- fix(toposd): preserve an existing or replacement socket node when self-bind fails or closes
- fix(toposd): reject unsafe service-config paths before rendering systemd unit text
- fix(toposd): reject `--self-bind` with an activated listener and report the bound address
- fix(toposd): prevent systemd restart loops for exit status 2; a runtime OSError at startup exits 1 so systemd still restarts
<!-- cmru: pending release notes end -->

## [0.6.0] - 2026-11-05
<!-- cmru: generated -->
<!-- cmru: source-end=66da61266884e688d6276515155191f81a020599 -->

### Added
- feat(topos/poc): side-by-side terminal drawing PoC for the redesign (6bbf77701)

### Fixed
- fix(topos): restore bookkeeping before unmask; a real producer crash stays a failure (H-01, H-02, H-03) (94eb46d84)
- fix(topos): replay jump epoch timestamps, busy reply off the accept thread, tolerant owned_markers (G-08, G-09, G-10) (9cbfbd6a3)
- fix(topos): docker inspect negative cache, sweep budget, positive TTL (G-07) (50dd52828)
- fix(topos): record append never truncates a non-recording file (G-06) (4202c90a0)
- fix(topos): squeeze restore survives second signal, exclusive recovery record, restore original on clean exit (G-04, G-05) (131ef015a)
- fix(topos): operator interrupt is not a producer failure; refresh stale gate tests (A-02 totals, derived DAMON, strict compression) (c0be18046)
- fix(topos): first column tier always shown, BPF staleness from interval, more power units refused (G-01, G-02, G-03) (54f07e389)
- fix(topos): snapshot window, redaction and mode, socket liveness probe before unlink (B-19) (59e7a749c)
- fix(topos): recording append cuts a torn plain tail and refuses truncated zstd (B-18) (e481087c6)
- fix(topos): gateway handler timeout and concurrency cap, proc_* sensitive, source-less findings sensitive (B-16, B-17) (6be9fdd2d)
- fix(topos): query coverage vs requested window, no bridging of holes, slice grouping once, compare span check, MCP history honesty (B-10, B-11, B-12, B-13, B-15) (ceea6bba3)
- fix(topos): SIGTERM shutdown path, producer-death exit, stale cursor/epoch, total deadlines, legacy hook/audit/cap, health causes (B-01, D-01, B-02, B-04, B-05, B-06, D-18) (97bc9acff)
- fix(topos): DAMON teardown/markers, docker log tail, bounded journald stream, drill stop confirmation (C-10, C-11, C-13, C-14, D-08) (6c9f20f5f)
- fix(topos): squeeze validation, signals, recovery file, confinement; container selector; kill preview parity (C-02, C-03, C-04, C-06, C-08, C-15, C-16) (485ba164c)
- fix(topos): UI survives source failures, resize, replay history, Footer, drop dead k key (D-02, D-09, D-10, D-12) (234c136fc)
- fix(topos): damon false zeros, bpf staleness and single pass, idle procs and incarnation pins, diag bands (A-10, A-12, B-14, A-14, A-16) (c3ae7ee6a)
- fix(topos): missing metrics sort last, top-pressure dedupes ancestors, drill uses table formatting, name column capped at 40 (D-03, UI truths) (16110813e)
- fix(topos): systemd gate maps container scopes, protected+power targets, expect-current, hardened owner inspect (C-01, C-07, C-09, C-12, C-18) (f7b0ba4c7)
- fix(topos): io.stat stacked-device totals, io.cost metrics, docker/unit caches and timeouts, shared net filter, baselines, registry scopes (A-01, A-02, A-04, A-05, A-06, A-07, A-09, A-15, A-17, A-18, A-19) (a3ac817c3)
- fix(topos): ConfigError for invalid config, strict values, unknown-key warnings, reject ignored CLI flags (D-04, D-05, D-06) (5c66060fc)
- fix(topos): headless record progress reports real frame count and final summary (9c917d441)

### Changed
- chore(topos/poc): tidy the drawing PoC (5095db731)
- merge(topos-fix-ui-20261105): topos deep-review fix round (fe588f4f7)
- merge(topos-fix-data-20261105): topos deep-review fix round (329972ff8)

### Documentation
- docs(topos): single view at 260 columns, D-033 reversed, D-034 host boundary (c971dfe30)
- docs(topos): documentation truth pass (README, architecture, readiness, trove pointers) (d59db6ca4)
- docs(topos): new console plan, rewritten TUI-SPEC, truthful status and roadmap from the 2026-11-05 interview (0ff8e6b62)
- docs(topos): dependency floor reasons and CHANGES entries for the UI/config fix round (D-17) (0ff51d6ac)

### Testing
- test(topos): fake collectors carry config so the live stream cannot crash mid-test (1f86ab730)
- test(topos): only the first shutdown request sets the reason and exit status (c89027aa4)
- test(topos): cover changed error branches in safety, daemon, collect, record and UI code (diff-coverage floor) (6688bf95f)
- test(topos): BPF daemon loop test expects the configured staleness limit (G-02) (9fabf41a0)
- test(topos): trickling-client test reads the queued reply after the server closes first (4a07541af)
- test(topos): cover negative-cache retain and busy-replier start failure (1802f8644)
- test(topos): convert p113 audit tests to real filesystem; changelog for G fixes (f3f617c35)
- test(topos): restore pinned default_owner_inspect contract and pid-reuse rate check (G-12) (df2d5d7d0)
- test(topos): operator interrupt is not a producer failure (6087686c3)
- test(topos): real no-audit-log test; audit directory checks run on a real filesystem (D-15) (35ce7c8cc)
- test(topos): replace elapsed-time assertions with state assertions; collect thread errors (D-14) (109981fa3)
- test(topos): paddr_enabled string is now rejected (D-05) (f1345a925)

## [0.5.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=090d30d874f287192a434b82a87dc12ec506a0d1 -->

### Fixed
- fix(topos): drop an unreachable fallback in the canonical-ID bind; cover the blank io.max line (B-040, B-012) (86b519394)
- fix(topos): judge io.max saturation per device and keep the device attribution (B-012) (d4b6aac9f)
- fix(topos): owner gate always on and Docker verbs run by the inspected canonical ID (B-039, B-040) (2f8ef27b2)

### Changed
- Merge branch 'fix-topos-2-20261104' (292dfcfbd)
- topos: fix B-047/B-003/B-023, add B-024 network alias and B-031 integer limit schema (0be7631fd)

### Documentation
- docs(topos): backlog B-003/B-023/B-024/B-031/B-047 FIXED (0be7631fd) (64f6cdbd0)
- docs(topos): CHANGES and backlog status for B-039, B-040, B-012; owner_inspect docstrings (9b35721a9)

### Testing
- test(topos): real_docker_inspect opt-out so the production resolver stays testable (B-039) (cadfc34d1)

<!-- cmru: pending release notes begin -->
- fix(topos/daemon): `daemon serve` handles SIGTERM/SIGINT through the normal shutdown path (history flush, paddr stop, socket unlink) (B-01, D-01)
- fix(topos/daemon): the daemon exits non-zero when the frame producer dies instead of serving a frozen frame; `daemon status` reports a frame older than 3 intervals as stale (B-02)
- fix(topos/daemon): a cursor ahead of the daemon's latest sequence is `gap=true`; responses carry a per-start `epoch` and `stream_frames` raises on a restart (B-04)
- fix(topos/daemon): request and response phases have a total deadline (trickling clients no longer hold slots); the busy reply is an envelope error for envelope clients (B-05)
- fix(topos/daemon): legacy (no `v`) requests go through the authorization hook, audit log and response byte cap; deeply nested JSON gets an error reply (B-06, B-19)
- fix(topos/daemon): component health errors carry the exception text (D-18)
- fix(topos/query): `coverage.complete` is false when the data does not span the requested window; `coverage.requested_span_s` added (B-10)
- fix(topos/query): integral, state_duration and derived rates no longer bridge holes wider than 1.5 intervals; integral/state_duration report `covered_s` and `span_s` (B-11)
- fix(topos/report): `--group-by slice` builds the parent map once instead of per entity per frame (B-12)
- fix(topos/compare): totals (`counter_delta`, `event_count`, `integral`) over windows whose spans differ by more than 10% are refused as `incomplete_coverage` (B-13)
- fix(topos/gateway): HTTP handlers time out after 5 s idle and live handler threads are capped at 32 (excess answered 503 `gateway_busy`) (B-16)
- fix(topos/daemon): all `proc_*` metrics are classified `sensitive`; a finding with no `source_metrics` is treated as `sensitive` when redacting (B-17)
- fix(topos/record): appending to a plain recording first cuts a torn final line; appending to a truncated or corrupt zstd recording is refused instead of corrupting it (B-18)
- fix(topos/snapshot): a 1-frame snapshot no longer includes every previous frame; redacted bundles drop docker `Cmd`/`Entrypoint`; bundles are created mode 0600 (B-19)
- fix(topos/daemon): `daemon serve` refuses to start over a socket a live daemon is serving (stale sockets are still replaced); deeply nested request JSON gets an error reply (B-19)
- fix(topos/mcp): `topos_history` reports `gap`, `truncated`, `frames_returned`, `oldest_ts`, `newest_ts` and `requested_since_ts` (B-15)
- fix(topos/tests): B-047 version assertions compare against `topos.__version__` (fallback test reads pyproject `fallback_version`) instead of the literal "0.1.0"
- fix(topos/tests): B-003 MCP-dependent smoke/server tests skip cleanly (`requires_mcp`) when the `topos[mcp]` extra is absent; the P84 gate still fails such a run
- fix(topos/cli): B-023 an unresolvable selector in the live TUI exits 2 with the typed message, as `--once` does
- feat(topos/registry): B-024 `--metrics network` is accepted as an alias for the `net` family
- fix(topos/mcp): B-031 `limit` is advertised as an integer in the `topos_overview`/`topos_history` input schemas; bool is still rejected with a typed error
- fix(actions): the Docker owner / protected-ID gate is always on; omitting `owner_inspect` uses the production `docker inspect` (B-039)
- fix(actions): Docker verbs run by the inspected canonical full ID instead of the raw name, closing the rename race (B-040)
- fix(collect): io_cap_saturation_pct is judged per device against that device's own io.max cap, with the device attributed (B-012)
- fix(actions): systemd verbs on `docker-<id>.scope`/`libpod-<id>.scope` get the same owner/protected decision as the docker verbs; `protected_services` applies to every systemd verb (name and key forms); power, sleep and rescue units are refused from a denylist, which is not a closed set (C-01, G-03, H-03)
- fix(squeeze): `--relax-to` is validated up front; a failed restore is retried once, reported as `restore_failed` (exit 3, loud stderr with the stuck value), never as restored (C-02)
- fix(squeeze): SIGHUP/SIGQUIT/SIGPIPE restore like SIGTERM and unwind (end audit record written, no `os._exit`); a recovery file is written before the first write and `topos squeeze --recover [--target PATH]` restores from it (C-03)
- fix(squeeze): step<=0, floor<0 and start<floor are rejected; the first write is clamped to the floor (C-04)
- fix(squeeze): target must resolve under the cgroup root and own a regular `memory.high`; the writer never creates the file (C-06)
- fix(actions): `action execute --expect-current VALUE` (incl. `max`) on systemd-set-property; systemd `infinity` reads as `max`; the fresh value is always read and recorded in the audit record (C-07)
- fix(cli): `--container` resolves to the container ID for docker kinds (C-08)
- fix(actions): owner inspect uses the absolute docker binary, scrubbed env, `--type container --` and a 10 s timeout that fails closed (C-09)
- fix(actions): owner-gate argv binding replaces only the final argv element (C-12)
- fix(inspect-files): cgroup-files accepts systemd-escaped `\` in cgroup names (C-15)
- fix(actions): an audit append after a torn partial line starts on a fresh line; the new audit file's directory entry is fsynced (C-18)
- fix(damon): teardown writes `nr_contexts=0` instead of `rmtree` on sysfs (C-10); the marker is written before the sysfs writes and carries `boot_id`, markers from another boot are ignored and removed (C-11)
- fix(inspect-files): docker-json-log returns the tail of the log (C-13); journald read streams with a hard byte cap and honours `--max-bytes` (C-14)
- fix(actions): `action preview` for kill applies the same target validation and protected-service refusal as execute (C-16)
- fix(ui): `s` in the drill screen opens the typed DAMON confirmation and reports which sessions were stopped if a later one fails (D-08)
- fix(collect): cgroup io totals (`io_r_bps`/`io_w_bps`/`io_r_iops`/`io_w_iops`/`io_discard_bps`) count only bottom-level block devices (stacked dm/md devices from `/sys/dev/block/<dev>/slaves` are excluded, no double counting); src `exact`, or `derived` when /sys cannot classify; empty io.stat lines skipped
- feat(collect): io.cost visibility: `host_io_cost_vrate_pct`, `host_io_cost_enabled` (root cgroup `io.cost.qos` + `io.stat` `cost.vrate`, typed unavailable when off) and per-cgroup `io_cost_usage_per_s`
- fix(collect): docker inspect cached by 64-hex id and evicted when the id leaves the tree; `docker inspect`, `systemctl show` and `tc` have timeouts that degrade to unavailable; `UnitView` 30 s per-unit TTL cache (A-01, A-04)
- fix(net): one shared interface filter (exact `lo`, `veth*`, `br-*`, `docker*`) for host_meta, the host row and the netns candidate (A-02, A-17)
- fix(collect): counter baselines pruned when an entity leaves, per-device io.stat deltas taken every tick, intervals from a monotonic clock (A-07)
- fix(collect): `swap_disk` without `swapcached`, `ratio`, `headroom_*` and `rf_*` report a typed unavailable state instead of exact/None or "warming"; absent `zswpin` makes every anon refault a device refault (A-05, A-06, A-15)
- fix(registry): `cpu_pct`, `psi_*` and `pids_current` are subtree-scoped; dead `aggregatable` field removed; scope table test (A-09)
- fix(collect): `io.max` parsed once per cgroup; cgroupfs-driver `docker/<id>` cgroups are recognised (A-18, A-19)
- fix(damon): no region data is typed unavailable instead of "0 bytes hot exact"; the hot/warm/cold/idle split is `derived` (A-10)
- fix(net): the BPF provider refuses a snapshot older than 3 intervals (`generated_at`) so a dead producer does not read as idle, and aggregates in a single pass (A-12, B-14)
- fix(procs): idle processes are not "hot" (rate > 0 floor); a pin is bound to a ProcessKey so a reused pid does not inherit it (A-14)
- fix(diag): `protected_disk_refault` honours the warn band, `memory_high_user_visible` is red at crit, `pressure` is unavailable when no input is readable (A-16)
- fix(topos/record): headless record progress reports the real written-frame count and prints a final `done frames=N` line at clean exit (D-02 review round)
- fix(topos/ui): a frame-source failure no longer crashes the TUI; the last frame is marked STALE with `mode=<src> source failed: <msg>`, attach and live sources retry and clear on recovery (D-02)
- fix(topos/ui): rows with a missing, NaN or non-numeric metric sort last in both directions and never raise TypeError (D-03)
- fix(topos/config): one `ConfigError` for syntax errors and invalid values (`topos: config <path>: <msg>`, exit 2); `general.interval` >= 0.1 s; invalid `compression`, non-boolean `paddr_enabled` and invalid `default_view` are rejected instead of coerced; unknown keys warn once each; `[hotkeys]`, `[colors]` and `history.downsample_*` warn that they have no effect yet (D-04, D-05)
- fix(topos/cli): `--duration`/`--frames` outside `--headless --record` and `--speed`/`--step` without `--replay` exit 2; `--interval` now applies to UI, attach and record modes (D-06)
- fix(topos/ui): resize re-chooses columns for the real table width (no 80-column floor); replay stepping no longer duplicates history; the live worker stops on quit; `42.0` in the jump prompt is a frame; the `__empty__` placeholder is never the selection; collapsed keys are pruned (D-09, D-10, D-12)
- fix(topos/ui): TOP PRESSURE drops ancestors of a listed culprit and the root; drill metrics use the table's byte/rate/percent formatting; NAME column capped at 40 characters (middle ellipsis); Footer shows bound keys; the dead `k` binding is removed
- test(topos): elapsed-time assertions replaced by state assertions, thread errors collected, vacuous no-audit-log test made real, audit-directory checks run on a real filesystem (D-14, D-15)
- chore(topos): reason comments for each dependency floor in pyproject.toml (D-17)
- fix(topos/ui): the first column tier (name, ram, refaults, PSI, cpu) is always shown; the narrow table widget no longer drops the metric columns on an 80-column terminal (G-01)
- fix(topos/net): the BPF snapshot staleness limit is three producer intervals (default 90 s) instead of a fixed 15 s that flapped against the 30 s default interval (G-02)
- fix(topos/actions): systemd start/stop/restart also refuse `systemd-{reboot,poweroff,halt,kexec}.service`, `soft-reboot`, `suspend`, `hibernate`, `hybrid-sleep`, `suspend-then-hibernate` and `ctrl-alt-del` targets; protecting `docker.service`/`multi-user.target` by default stays a recorded policy decision (G-03)
- fix(topos/squeeze): the restore write blocks the guarded signals and retries when interrupted; the recovery record is created atomically and refuses a second squeeze; `--recover` validates the recorded value; without `--relax-to` a clean exit restores the pre-squeeze `memory.high` (G-04, G-05)
- fix(topos/record): `--record` refuses (typed `NotARecordingError`) an existing file that does not start with a recording header instead of truncating it (G-06)
- fix(topos/docker): failed `docker inspect` is cached negatively (15 s) with a 15 s per-sweep budget; cached inspect data expires after 60 s so renames show up; last known data is used meanwhile (G-07)
- fix(topos/ui): replay jump treats integer-valued epoch timestamps (>= 1e8) as timestamps, not frame numbers (G-08)
- fix(topos/daemon): the busy reply runs on a short bounded helper thread, not the accept thread (G-09)
- fix(topos/damon): listing owned markers tolerates an undeletable stale marker (G-10)
- fix(topos/daemon): an operator KeyboardInterrupt of the serving loop is a clean stop (exit 0) even if the producer had already ended (gate follow-up to B-02)
<!-- cmru: pending release notes end -->

## [0.4.2] - 2026-10-21
<!-- cmru: generated -->
<!-- cmru: source-end=8f7425bba9a407c4d2522d0a2c429dd95b93585b -->

### Added
- feat(run-gate): make wheel SemVer the only supported interface (9a0e192ab)

### Changed
- DOCS-FOLD-01 review fixes F-01..F-08: memory.low claim corrected, boost chain labelled, one canonical home per fact, 125 s, stale rows, README:130, MDT-022 citations (fa215e600)
- DOCS-FOLD-01: fold operator-approved facts A-D into the docs (c5b79f9f2)
- INT-01 gate fixes: overlap tests, bootstrap oracle, topos lane, mattermost drift (fbe8720e5)
- Merge mdt-home-persistence-20261018 (daf47a2e9) (INT-01 step 4) (5e2dff706)
- topos: a present systemctl that cannot reach systemd is unavailable too (W4) (44f56b06e)
- topos: 'systemctl not available' is a state of its own, not 'unit absent' (V3) (697103a5f)
- tester-unified: no systemctl stub, node from the official image (D-001, D-004), host lanes off the root environment (D-002), R5, R7 (c3bd7f916)
- topos: a missing systemctl degrades instead of crashing the collector (D-001, TP-SYSTEMCTL) (78e4200d3)
- tester-base/tester-unified: explicit systemctl stub (D-001) and pycache guard (D-002) found by the T8 skip-diff; topos backlog TP-SYSTEMCTL (262cb9c30)

## [0.4.1] - 2026-09-26
<!-- cmru: generated -->
<!-- cmru: source-end=d22feaab860607938505380ef9ddacb521307e66 -->

### Testing
- test(topos): synchronize broker history boundary test (8823bfea)

## [0.4.0] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=a2def16c934a888245a0d088ca9e44ddcf31792e -->

### Added
- feat: add estate cli version compatibility (05f373a4)

### Changed
- cli: universalize vbpub parser diagnostics (aa0e69fa)
- chore: land run-gate root and dev-gates migration (41c1cafb)

## [0.3.2] - 2026-09-17
<!-- cmru: generated -->
<!-- cmru: source-end=fba1be96e9ec571e6567800869e0c6cd740e2f02 -->

### Changed
- chore(nyxloom): archive historical handoffs and reports (28801a95)

## [0.3.1] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=54dfca2f0d6e075f23941afbcfe7836fca4bdda0 -->

### Documentation
- docs: propagate MDT governance ownership and names (2e5f6463)

## [0.3.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=94917b55f7274f8c3665f706fd2550cdbfb9435f -->

### Added
- feat(topos): P93 lifecycle owner-chain protocol and action migration (port-forward) (864674d7)

### Changed
- pwmcp,topos: adopt $INSTANCE_ID/$DOCKER_NETWORK_INTERNAL naming (CIU-104 mitigation) (28a70b24)
- merge(nyxloom): make the AGENTS.md pointer block a REQUIRED adoption step (950046d9)
- merge(topos): P93 lifecycle owner-chain protocol — port-forward + independent review APPROVE (3f156379)
- merge(topos): P91 persistent capped history — port-forward + independent review APPROVE (d82c35af)
- topos-P91: close final diff-coverage gaps (2 lines) (c9d70520)
- topos-P91: close remaining diff-coverage gaps (recovery-internals branches) (301c7909)
- topos-P91: close diff-coverage gate on the ported wiring; fix a latent COMPONENT_NAMES gap (c5bd5a1a)
- topos-P91: port forward persistent capped daemon history (groop->topos rescue) (ecd671fe)

### Documentation
- docs(topos): update backlog ideas (59c2dd3f)
- docs(topos-P91): LOG/REPORT/REVIEW for the groop->topos port-forward (5519eb87)
- docs(topos/P93): record official gate results in LOG/REPORT port-forward addenda (995b57d7)
- docs(topos/backlog): B-046/B-047 -- 4 hardcoded-"0.1.0" tests break topos-suite on main (e9f8c4d6)
- docs(topos): update backlog entries (9f8e4929)
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)
- docs(nyxloom): make the AGENTS.md pointer block a REQUIRED adoption step (97324f46)

## [0.2.1] - 2026-08-22
<!-- cmru: generated -->
<!-- cmru: source-end=f330d9a17940787edcfcec957c1268f765aa5af6 -->

### Changed
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- topos: make subprocess tests project-root portable (5681b42b)
- handoff(nyxloom): P90 -- extract the testing mechanics into a standalone library (b22fe999)
- topos: add backlog entry (77e0b63c)

### Testing
- test(topos): py-compile argv meta-tests parse run-gate.toml [lanes.py-compile] — the SSOT since adoption; nyxloom.toml holds only the pointer (b2f01c0c)
