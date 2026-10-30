---
kind: backlog-entry
schema_version: 1
id: CP-36
title: "ctl host hardcodes dev.slice as the observed root and reads only memory/cpu per slice; make the observed set configuration with dev.slice as a default, and make the per-slice block the full sample_cgroup groups"
status: open
type: "feature"
component: "daemon"
context_estimate: "small"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027); lib/serve.py _host_snapshot/_slice_snapshot; overlaps CP-29 item 4"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler daemon"
priority: 3
filed_date: "2026-10-27"
---

## Observed (code reading)

`SessionServer._host_snapshot` always starts its slice list with the literal `"dev.slice"` and its direct `.slice`
children, then appends `--observe-slices`. `_slice_snapshot` reads `memory.current/max/high`, `memory.swap.current`
and memory+cpu PSI only. For the host-monitoring reference case (CP-28: two production game-server scopes under a
non-dev slice, `system.slice`, the root, io as the contended resource) this means:

- a host whose interesting workloads are not under `dev.slice` always carries a `dev.slice` entry of nulls (the
  cgroup does not exist there) and cannot observe the root (`-.slice`) or a `.scope` at all (only slice NAMES are
  accepted; `slice_to_path` now refuses anything else);
- io PSI and io.stat totals, `swapev` (new in 1.1.0) and `memev`/`memev_h` are not in the block although
  `metrics.sample_cgroup` already reads them in 0.36 ms per cgroup.

## Required contract (additive under contract 1, RG55 §8.5 versioning rules)

- `--observe-slices` becomes `--observe` accepting the same target grammar as the collector (`slice:`, `cgroup:`,
  `container:NAME` resolved to `containerid:` by the deploying side, root `cgroup:/`), with `slice:dev.slice@follow`
  (direct children) as the default when nothing is given; the old flag stays as an alias for one release.
- Each observed entry's block gains `pressure.io`, `io` (per-device `io.stat` as `sample_cgroup` returns it),
  `memev`, `memev_h`, `swapev`. Existing keys keep their names and meaning.
- `_validate_ctl_response.host_at` and the PROTOCOL/CONSUMERS docs describe the additive keys; the mirrored
  run-gate contract copy is updated in the same change.

## Oracles

A fixture host with no `dev.slice` and `--observe cgroup:/,slice:game.slice`: `ctl host` reports exactly those two
entries, each with `pressure.io` and `io` present, and the response still validates against the 1.0.0 client
validator (keys are only added).

## Folds / depends

Absorbs CP-29 item 4 (io PSI and io.stat in `ctl host`); CP-29 items 3, 5 and 6 remain there.
