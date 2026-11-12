---
kind: backlog-entry
schema_version: 1
id: CP-58
title: "cgpd reads the persisted host CPU benchmark and exports machine facts and steal time (rigor-evidence CP-1)"
status: fixed
type: "feature"
component: "daemon"
priority: 2
provenance: "rigor-evidence design memo 2026-11-05 sections 4.4, 4.6, 7.4 row CP-1; depends on HB-1 (cpu-benchmark.json schema)"
filed_by: "rigor-cp1"
filed_date: "2026-11-05"
---

## Delivered (branch rigor-cp1-20261105, not merged)

- `cgprofile.machine`: `machine/1` (CPU model, `cpus_online`, `cpus_available` = affinity intersected with the tightest `cpu.max` quota, hypervisor,
  smt, `speed`, `speed_reason`).
- The speed comes from `/var/lib/mdt/host-facts/cpu-benchmark.json`, read at start and on reload, accepted only while `cpu_model` and `cpus_online`
  equal the live values and `single_core` is a valid score. Otherwise the packaged table `cgprofile-scores/1` (keyed by CPU model and Geekbench
  major) is used, with the reason `NO_HOST_MEASUREMENT`, `HOST_MEASUREMENT_STALE`, `HOST_MEASUREMENT_INCOMPLETE` or `HOST_MEASUREMENT_INVALID`.
  With neither source: `speed: null` and `<reason>_AND_MODEL_NOT_IN_TABLE`. No default score. The claim file is never opened.
- Per-major baselines `{6: 2500, 7: 2500}`; a major without a baseline is no basis.
- `host` verb (and the `host` block of `status`) and the Summary `host` block gain `machine`, `steal_seconds`, `cpu_seconds_total` (Summary: session
  deltas; `host` verb: since boot).
- `docs/PROTOCOL.md` section 11, `docs/SCORES-TABLE.md` (refresh procedure), goldens updated and vendored into `run-gate-project`.

## Not done here

Observer contract 3 (`window-close` context) is CP-2. The table content is CP-59.
