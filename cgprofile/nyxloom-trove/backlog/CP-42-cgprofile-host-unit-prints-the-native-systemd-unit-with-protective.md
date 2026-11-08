---
kind: backlog-entry
schema_version: 1
id: CP-42
title: "cgprofile host-unit: print the native systemd service unit, with protective limits so a misbehaving daemon cannot hurt the host"
status: open
type: "feature"
component: "daemon"
context_estimate: "medium"
provenance: "Operator decision 2026-11-04 (native service from a released wheel; ship and configure a protective cgroup for the daemon); platform design memo section 4"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-04"
---

## Problem

The daemon is a privileged container today. As a native service it needs a unit, and that unit must protect the host from the daemon
(the inverse of `infra/cgprofile.slice`, which protects the watcher from host pressure). The daemon runs as root with `/sys/fs/cgroup`
writable and a serial accept loop (CP-33); a runaway collector, a thread leak or a stuck session must be bounded by the kernel, not by
the daemon's own good behavior.

## Decision record

Operator decision 2026-11-04: ship and configure a protective cgroup for the daemon: own slice and service limits (MemoryMax and
MemoryHigh, CPUQuota, TasksMax, IOWeight, OOMScoreAdjust, a Restart policy with backoff). The daemon keeps writing `cgroup.kill` only
on exact gates-slice container cgroups (PROTOCOL.md section 3, unchanged). The unit template ships INSIDE the wheel (CP-41) so cgprofile
keeps one source; host-setup renders it (MDT-055).

## Design

New subcommand `cgprofile host-unit --print [--gates-slice S] [--wheel-venv P] [--sessions-dir D]` writes the rendered unit to stdout (and
`--check` compares an installed file); host-setup supplies the host tier names, nothing is read from a checkout. Proposed unit
(every number measured before it is final, see acceptance):

- `Slice=cgprofile.slice` (top-level, sibling of `dev.slice`, as today: MemoryMin 128M, MemoryHigh 768M, MemoryMax 1G, CPUWeight 100,
  IOWeight 50). host-setup owns the slice file instead of the hand-installed `infra/cgprofile.slice`. 1.1.0's refusal to start on an
  unbounded slice stays and now has a real partner.
- Service limits inside the slice: `MemoryHigh=512M`, `MemoryMax=768M`, `MemorySwapMax=0` (a sampler that swaps stops observing the
  pressure it exists to see), `CPUQuota=50%`, `TasksMax=256` (measure the real thread count of a busy host first; CP-33 unbounded
  watch threads must be bounded in code too), `IOWeight=50`, `LimitNOFILE=4096`.
- `OOMScoreAdjust=` a moderate positive value (proposal +200): under host-wide OOM the watcher is expendable before the production
  workloads and the lanes. Open question: the slice comment says the daemon must not be the thing that gets killed mid-run; with
  MemoryMax well below the slice cap, a per-service cap is the right guard and a positive adjust is the right cost trade. Decide with
  measurements and record the decision here.
- `Restart=on-failure`, `RestartSec=2s`, `RestartSteps=5`, `RestartMaxDelaySec=60`, `StartLimitIntervalSec=300`, `StartLimitBurst=5`:
  a crash loop backs off and then stops, and run-gate sees a missing socket (CP-31 keeps "missing" distinct from "refusing").
- Hardening: `PrivateNetwork=yes` (the container has `network_mode: none`), `PrivateTmp=yes`, `ProtectHome=yes`,
  `ProtectSystem=strict` with `ReadWritePaths=` for `/run/cgprofile`, the sessions directory and the cgroup tree; `RuntimeDirectory=cgprofile`
  must NOT recreate the directory mode or group: host-setup's tmpfiles rule owns `/run/cgprofile` and its group (CP-43), the daemon only
  re-asserts. `CapabilityBoundingSet` starts from what placement, DAMON sysfs and the D-Bus slice probe need and shrinks by test.
  Access to the system bus socket stays read-only as today.
- The unit sets `CGPROFILE_ALLOW_UIDS` from host config (empty by default) and the gates slice name from `--gates-slice`.

## Acceptance criteria

- `cgprofile host-unit --print` output is stable (golden file), passes `systemd-analyze verify` on a Debian 13 host, and contains no
  checkout path.
- On a scratch host: a deliberate runaway (memory balloon in a test build, fork bomb in a test build) is contained at the service
  limits, the host and gates slice are unaffected, the service restarts with backoff and stops after the start-limit burst.
- The daemon's cgroup.kill rule is unchanged: a test proves no write to any cgroup other than an exact gates-slice container leaf with
  `populated 1` is possible from the unit's writable paths plus the code guard.
- A measured table (resident set, threads, CPU of the daemon under 1, 8 and 24 concurrent sessions) backs every limit.

## Test plan

Golden unit test; `systemd-analyze verify` in a tester lane with systemd available or documented skip; a scratch-host runaway test
(not the production-shared host); existing placement guard tests stay green.

## Dependencies and order

After CP-41. Consumed by mdt-cockpit MDT-055. Independent of CP-43 except that the unit must not own `/run/cgprofile` (tmpfiles does).

## 2.0 plan amendment (2026-11-04, operator-accepted defaults)

- The renderer is `cgpd unit print|check` (not `cgprofile host-unit`); the templates ship in the wheel's package data. Unit design, limits table,
  restart policy and hardening are in `docs/NATIVE-DEPLOYMENT.md` section 4; all numbers remain proposals until the measured table of that file's
  section 4.3 exists.
- `ReadWritePaths` gains `/sys/kernel/mm/damon` (DAMON sysfs writes) beyond the list above; confirm by test.
- `OOMScoreAdjust` conflict recorded in `docs/NATIVE-DEPLOYMENT.md` section 4.4: the slice header says the watcher must not be the OOM victim, this
  entry proposed +200. First 2.0.x release leaves it unset, as a template parameter; decide with the measurements.
- The unit sets no `Environment=`: configuration is `/etc/cgprofile/cgpd.toml` (`CGPCTL-SPEC.md` section 3), `CGPROFILE_ALLOW_UIDS` becomes the
  `allow_uids` key, and a leftover variable is refused.
- Implementation rides CP-48 (package 3).
