---
kind: backlog-entry
schema_version: 1
id: CP-57
title: "cgpctl doctor: the caller-group check FAILs for uid 0, so mdt-host-check.sh (run as root) always reports a failure on a healthy host"
status: open
type: "bugfix"
severity: "medium"
component: "cli"
priority: 2
provenance: "native rollout on v1001 and r1002, 2026-11-05 (reports/cgpd-rollout-REPORT.md): cgpd 2.0.0 healthy, `cgpctl sessions`/`status` work as root, doctor still FAIL"
filed_by: "cgpd-rollout"
filed_date: "2026-11-05"
---

## Observed mechanism

`_check_caller_group` (`src/cgprofile/cli/cgpctl.py`) fails when the caller is not in the socket's group (`docker`, gid 990). Root is not in that
group, but root reaches the socket by DAC and the daemon accepts uid 0, so every verb works. `mdt-host-check.sh` runs `cgpctl doctor --json` as root
(MDT-055 `check_cgprofile_service`), so the host check ends `FAIL cgpctl doctor reports failing checks: caller-group` on both rollout hosts although the
service is fully healthy (doctor: 8 ok, 2 warn, 1 fail).

## Fix

For effective uid 0 report `ok` ("caller is root"); also `ok` when the caller's uid is in the daemon's `allow_uids` (the remedy text already names that
route; the daemon exposes the list through `status`). A test pins both branches. Until then the MDT-055 check should treat `caller-group` as non-failing
when `id -u` is 0 (MDT-062).
