# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

<!-- cmru: release history -->

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
- fix(actions): systemd verbs on `docker-<id>.scope`/`libpod-<id>.scope` get the same owner/protected decision as the docker verbs; `protected_services` applies to every systemd verb (name and key forms); poweroff/reboot/halt/kexec/rescue/emergency/shutdown/default `.target` are refused (C-01)
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
