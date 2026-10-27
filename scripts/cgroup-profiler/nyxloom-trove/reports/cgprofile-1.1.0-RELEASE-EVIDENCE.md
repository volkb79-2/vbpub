# cgprofile 1.1.0 release evidence (wave 2026-10-27)

Branch `wave-cgprofile-20261027`. Contents of 1.1.0: CGP-110 (Sol blockers B1-B6 of 1.0.0, ten commits merged from
`cgp-110-20261021`, tip `6ea5ef2fb`) plus the CP-28/CP-29 backlog entries (`efded4f97`).

## Gate policy for this release

- R2 (assay mutation) is WAIVED by the operator for every release of the 2026-10-27 wave. No R2 verdict exists for 1.1.0 and none may
  be inferred from the r3 canaries. Tracked as CP-30. (1.0.0 also had no completed full-source R2 verdict.)
- `r0-r1` (unit + coverage, 100% line and branch required by `tools/gate.sh coverage`) and `r3` (7 canaries) are mandatory and run
  from the exact tree. `--skip-gates` is not used.

## Acceptance evidence

Operator decision 2026-10-27: the live acceptance evidence is the read-only observation made that day, not a separate acceptance run.
A short-lived root observation container bind-mounted the real host cgroup tree read-only and the repository read-only, and called
the collectors directly (`lib.metrics.sample_cgroup`, `lib.limits.read_limits`) with `host_cgroup_view` true. Recorded in the
CP-28 entry, section "Evidence": all of `mem memstat memev psi cpu io pids cgstat` returned for a container scope, a slice,
`system.slice` and the root (full per-device `io.stat` incl. `cost.usage`, three PSI files, `memory.stat`, `memory.events.local`,
swap, `cpu.stat` throttling, `pids`), 0.36 ms per call for all groups; `read_limits` returned `io.max`, `io.weight`,
`memory.high/max/swap.max`, `pids.max` with binding-level attribution; gaps found (`memory.swap.events`, hierarchical
`memory.events`, `io.cost.*`, `/proc/diskstats`, io PSI in `ctl host`) were filed as CP-29/CP-28.

What this evidence covers: collectors and limit readers against the real tree. What it does not cover: the contract-2 observer
acceptance deltas (`tools/observer-live-acceptance.py`, Sol B5), the end-to-end `serve` refusal path of B4 on a host without the
slice unit, and any CGP-110 stop/finalize race (unit-tested with fakes only). CP-20 therefore stays `carved`.

## Deploy-side facts measured 2026-10-27

- The host slice unit that B4 requires is installed and bounded (`cgprofile.slice` loaded, `MemoryMax=1073741824`), so the 1.1.0
  daemon passes its own start guard on this host. The running host daemon is still the pre-1.1.0 image.
- Run-gate client compatibility (installed run-gate 24.0.1): `stop-incomplete` is surfaced as a generic profile warning
  ("refused (stop-incomplete)"), the stop is not retried, and the unknown additive Summary keys `damon_cleanup`/`stop_forced` are
  ignored. Nothing breaks; a wedged-worker session stays reserved until the third incomplete stop or daemon shutdown. cgprofile 1.1.0
  may therefore ship before run-gate 24.x; a run-gate follow-up (retry `stop-incomplete`, surface `damon_cleanup.confirmed`) is
  recommended, not required.

## Gate results

See the sections below, appended after the runs.
