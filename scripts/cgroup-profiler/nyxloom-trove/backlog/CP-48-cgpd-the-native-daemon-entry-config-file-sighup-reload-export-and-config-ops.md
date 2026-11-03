---
kind: backlog-entry
schema_version: 1
id: CP-48
title: "cgpd: the native daemon entry, /etc/cgprofile/cgpd.toml with SIGHUP reload, ctl contract 2 ops, session ownership, unit renderer (plan package 3)"
status: open
type: "feature"
component: "daemon"
context_estimate: "large"
provenance: "Operator acceptance 2026-11-04 of the cgpctl plan defaults (CGPCTL-PLAN-2026-11-04 sections 1-4, Q1-Q11); contract in docs/CGPCTL-SPEC.md, docs/PROTOCOL.md section 10, docs/NATIVE-DEPLOYMENT.md (package 0)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-04"
---

## Problem

The daemon is configured only by flags and environment, cannot reload, renders reports in a subprocess that shares its handler pool, has no session
ownership, and has no native unit. It also carries container-shaped checks (`CGPROFILE_PID_NAMESPACE_MODE`, `CGPROFILE_PROC_ROOT=/hostproc`).

## Design

Contract: `docs/CGPCTL-SPEC.md` section 3, `docs/PROTOCOL.md` section 10, `docs/NATIVE-DEPLOYMENT.md` sections 3 to 5. Branch
`cgp2-3-cgpd-20261104`; adopts CP-17 for the daemon half and implements CP-42.

- `cgpd` registry: `run` (default verb, service entrypoint rules), `unit print|check`, `config check`.
- Config loader for the 15 keys of the spec (fixed versus reloadable), unknown keys refused with the nearest valid key, no env fallbacks, stale
  `CGPROFILE_*` daemon variables refused naming the replacing key; SIGHUP atomic swap of reloadable keys, invalid file keeps old settings, fixed
  key change reported as restart-required.
- Native preflight: root, pid-namespace identity (always on, default `/proc`), bounded slice, writable sessions dir, single-daemon guard.
- Contract 2: serve contracts 1 and 2 on one socket; `export` (streaming, capped, owner-checked, 4 concurrent), `config`, `status --state`,
  `cgroup:`/`slice:` observe targets, `owner_uid` in the manifest, ownership rules, `report-removed` at contract 1; remove the report op, the
  report subprocess and the 2-job bound; `version` gains `contracts`, `daemon.version`, `config`.
- `cgpd unit print|check` with the templates in package data and the CP-42 limits as parameters; golden unit; `systemd-analyze verify` lane.
- CP-42 measurements (resident set, threads, CPU for 1/8/24 sessions; runaway containment) on a scratch host, results into
  `docs/NATIVE-DEPLOYMENT.md` section 4.3; decide `OOMScoreAdjust` (section 4.4).

## Acceptance criteria

- New goldens per `PROTOCOL.md` section 10.7; existing contract-1 goldens byte-identical (compat oracle).
- A 1.x client request (including `report`) against the new daemon gets the documented contract-1 answer.
- Reload, preflight, single-daemon and group-adoption tests (gids 2751, 999, 0, arbitrary) pass; `cgroup.kill` rule test unchanged and green.
- The daemon venv imports no pandas, plotly or matplotlib (test).

## Dependencies and order

After CP-46. Parallel with CP-47. Blocks the release and the host-setup package (MDT-055).
