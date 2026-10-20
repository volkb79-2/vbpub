# gstammtisch-guide — backlog

## GG-1 — the nsenter test has no VM lane that would ever run it

**Status:** OPEN (filed by mdt-cockpit MDTC-02 T7, 2026-10-18).

**Observed.** `tests/test_soulmask_monitor_r1.py::test_nsenter_binary_exists_but_real_netns_join_needs_a_real_host`
executes a real low-level OS binary (`nsenter`). It is now marked `os_integration` and the gate lanes
(`run-gate.toml` r0-r1 and `assay.toml` r2) deselect it explicitly (`-m "not os_integration"`),
because the minimal `tester-base` image carries no `nsenter`-class binaries and a quiet skip proves
nothing. The test body documents a known limitation rather than asserting nsenter behaviour, and
this project has no QEMU VM lane, so nothing ever runs it.

**Fix direction.** Either build a small `r1-vm-real-nsenter` lane on the shared `testing/vm`
harness (see `scripts/debian-install-v2/testing/vm/`; run the marked test with
`VBPUB_REQUIRE_OS_INTEGRATION=1`, where a skip is a failure) and make the test assert a real netns
join between two guest namespaces, or delete the placeholder test if the deployment-host smoke test
in README.md remains the only authority.

**Oracles.** In the VM lane the test passes with `nsenter` present and FAILS (not skips) when the
binary is absent; the gate containers report it as deselected, never as passed or skipped.

## GG-2 — per-instance tmpfs: host-side facts still to verify live

**Status:** OPEN (filed by SMT-01, 2026-10-20).

**Measured (not deferred).** `systemd-tmpfiles-clean.timer` on the host is static and active,
firing 15 min after boot and then every 1 d (read-only check, 2026-10-20, gstammtisch). It is
no longer relied on: pruning is done by `soulmask_logs-prune.sh` + `soulmask-logs-prune.timer`
(review SMT-01 R-02: an age rule cannot bound the tmpfs against ~66 MB logs).

**Round 2 (review SMT-01).** Exit 75 + `SuccessExitStatus=75` was chosen over a separate
per-instance unit (see SOULMASK-TMPFS.md "Failure policy"); its visibility (journal `-p err`,
failing prune service, toggle warning) has only been exercised against shims — confirm on the
live host with a deliberately refused target in a test instance, and that the prune service
actually shows as failed. The policy single source (`/etc/gstammtisch/soulmask_tmpfs.env`) must
be installed together with the units (a missing file fails both loudly).

**R3-01 (backlog).** The per-instance alarm is pull-only: nothing pushes it. The only standing
indicators are `systemctl is-failed soulmask-logs-prune.service`, the toggle status warning and
`journalctl -p err -u soulmask_tmpfs`, and the unit that owns the problem looks healthy
(deliberate trade-off, see SOULMASK-TMPFS.md "Failure policy"). Fix direction: let
gstammtisch's own monitoring (`soulmask-monitor`, or an `mdt` check) surface
`is-failed soulmask-logs-prune.service`, or add an `OnFailure=` notify unit already used on the
host; plus the live acceptance step above (provoke one refused target on a test instance).

**Deferred.** (1) The `noswap` tmpfs option on the host kernel (7.2.6 here, `noswap` needs
>= 6.4; the code fails loudly rather than fall back, but no live mount of the
`[per-instance-noswap]` section has happened because none is configured). (2) The prune
policy numbers (`KEEP_BACKUPS=3`, `HIGH_WATER_PCT=75`, size 512M, as shipped in `soulmask_tmpfs.env`) rest on a ~66 MB log; confirm
with `df` on the live mounts after the first restart cycle and re-tune in BOTH units if the log
grows. (3) The container-running check in teardown and the mount-namespace behaviour it guards
against (a host `umount` succeeding while the game keeps its own copy) were not exercised against
the live host. (4) The remaining mount-to-recheck race (microseconds, between the final
containment check and the `mount` syscall) cannot be closed in shell.

**Fix direction.** The controller runs the verification steps listed under "Deployment" in
SOULMASK-TMPFS.md after install; promote any surprise to a test in
`tests/test_soulmask_tmpfs_per_instance.py`.

## GG-3 — per-instance log tmpfs: operator responsibilities (not engineered)

**Status:** ACCEPTED, operator responsibility (decided 2026-10-20, SMT-02).

The operator chose not to engineer further: (1) the tmpfs size and prune policy must fit the actual log
volume (the sizes issue is ignored on purpose); (2) sibling logs are not covered: Steam logs, other
files under `WS/Saved` (the SQLite databases above all), crash dumps. Both are documented in
SOULMASK-TMPFS.md "Operator responsibilities and known gaps". Nothing to implement; reopen only if a log
volume change or an ENOSPC on a live mount shows up.

## GG-4 — host IO bounds: applied as an interim, not yet owned by host-setup

**Status:** OPEN (filed by SMT-02, 2026-10-20).

`HOST-IO-INTERFERENCE.md` §5 recommends bounded `vm.dirty_*`, `nr_requests` and `max_sectors_kb`, and dev
`io.max` caps derived from the ~10 MB/s steady write rate. The 64/128/256 KB matrix is
in (§4.4), the live B0/B1 run and the read-throughput check are in (§4.6: 64 KB accepted). **Applied
2026-10-20 as an interim** on gstammtisch (§4.7): `/etc/sysctl.d/90-mdt-io-writeback-interim.conf` (dirty
16M/4M) and `/etc/udev/rules.d/61-mdt-io-queue-interim.rules` (vda: `max_sectors_kb` 64, `nr_requests` 16,
sorted after the `60-*bfq*` rules). Needs: MDT-HS-05 to replace them (host-setup wizard keys plus a central
benchmark; udev rule keyed on the device behind `/`, values derived not literal); the dev `io.max` caps
derived from the ~10 MB/s steady rate are still open.
The instrument scripts are in `tools/io-stall/` but carry gstammtisch literals (`/dev/vda`, slice names, constant restore caps, dated file names); parameterising them (derive the disk, read the pre-test caps) is part of this item.

## GG-5 — burst zswap writeback stall: mitigations proposed, none applied

**Status:** OPEN (filed by SMT-04, 2026-10-20), each option awaiting the operator.

Mechanism, evidence and upstream status: MEMORY-ARCHITECTURE.md "Burst writeback stall: the zswap shrinker has
no rate governor"; data: MEASUREMENTS.md M8. Options: **(a)** raise the game scope's steady `memory.low` through the per-server panel value of
`WINGS_CG_MEMORY_LOW` (egg default 7G; panel database, not in the repo; a runtime change is undone by the next
start; details in MEMORY-ARCHITECTURE.md); **(b)** [I] raise `vm.watermark_scale_factor` (cost: kswapd CPU, less usable free memory);
**(c)** [I] lower `dev.slice` `memory.high` (cost: dev builds slower); **(d)** a userspace chunked governor once
`memory.reclaim source=zswap` exists (posted upstream, not known to be merged; kernel 7.2.6 evidence is only the
missing kallsyms symbols; the `memory.reclaim` key probe was inconclusive). Not applied: the operator decision
that `writeback=1` and the shrinker are required stands.
