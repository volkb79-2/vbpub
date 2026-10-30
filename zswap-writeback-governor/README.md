# zswap-writeback-governor

Chunked, rate-governed zswap writeback for latency-sensitive tasks on an
overcommitted host. **Status: analysis, design, a not-yet-run synthetic and an
upstream reply draft. No product code, nothing deployed.** No gate lane,
release or artifact exists for this folder (docs and scripts only), so it is
not in the cmru release set.

## The problem in operator terms

On gstammtisch (16 GB, Soulmask game server in a Wings cgroup, builds and
containers on the same host), a few minutes after the server settles the
kernel's zswap **shrinker** writes the game's whole cold compressed pool to the
swap disk in one go. The disk is capped at 14 MiB/s, the writing is done by the
game's own threads, and the game stalls for minutes (frames per second fall
from 30 to about 6). Nothing paces it; the operator requires
`memory.zswap.writeback=1` and the shrinker, so switching them off is not an
answer.

## Measured evidence (all in this repo)

| What | Where |
|---|---|
| Burst 11:19:34-11:22:00: pool 769M to 0, swap +2.1 G, fps 29.9 to 5.9, 455.8 s of game D state in `blk_mq_get_tag` under `shrink_memcg_cb`, plus 12.9 s + 8.1 s in `try_to_free_pages` | [`scripts/gstammtisch-guide/MEASUREMENTS.md` M8](../scripts/gstammtisch-guide/MEASUREMENTS.md#m8-soulmask-startup-to-steady-state-final-configuration-tmpfs-logs-eatmydata--ksm-bounded-io) |
| Raw monitor log (5 s rows, 136 rows) and `iostat -x` (flat 14 MiB/s on `vda6`/`vda7`) | [`measurements/2026-10-20-soulmask-startup-monitor.txt`](../scripts/gstammtisch-guide/measurements/2026-10-20-soulmask-startup-monitor.txt), [`...-iostat-zswap-writeback.txt`](../scripts/gstammtisch-guide/measurements/2026-10-20-iostat-zswap-writeback.txt) |
| First write-up and mitigation options (a)-(d) | [`MEMORY-ARCHITECTURE.md`, "Burst writeback stall"](../scripts/gstammtisch-guide/MEMORY-ARCHITECTURE.md#burst-writeback-stall-the-zswap-shrinker-has-no-rate-governor) |
| The second episode (B3: 16.03 s of D in `shrink_memcg_cb`), D-state stacks | IO-STALL-TESTING-GUIDE.md section 10 (controller's working copy; the D-state log `/var/log/mdt-stall/dstate-20261020-pid4040733.log` lives on gstammtisch, not in this repo) |
| The Wings steady squeeze (min/low/high step-down at steady-match) | [`SOULMASK-STARTUP-MARKERS.md`](../scripts/gstammtisch-guide/SOULMASK-STARTUP-MARKERS.md) |

One thing the evidence does **not** show: who called reclaim. The stack probe
used `kstack(10)`, so every recorded stack ends at `list_lru_walk_one`. The
caller could be global direct reclaim, `memory.high` reclaim at user-mode return,
or memcg-charge reclaim. This project does not assume one; the synthetic
measures it.

## Goal

A writeback path that is **chunked** (bounded bytes per unit time), **rate-governed
from the device's measured write rate**, and **never executed in the
latency-sensitive task's own threads**, while keeping `writeback=1` and the
shrinker.

## What is here

| Path | What |
|---|---|
| [`docs/KERNEL-ANALYSIS.md`](docs/KERNEL-ANALYSIS.md) | the shrinker mechanism from the v7.2 source, with line citations, what is source-proven vs inferred |
| [`docs/DESIGN.md`](docs/DESIGN.md) | governor options (userspace on `memory.reclaim source=zswap`, 7.2 userspace levers, kernel change, `watermark_scale_factor`), the recommendation for now and later |
| [`synthetic/`](synthetic/README.md) | a VM-only reproduction with four hypotheses (H1 aging, H2 memory.min/low step-down, H3 `watermark_scale_factor`, H4 `memory.high` reclaim alone) and exact pass/fail criteria; **not run yet** |
| [`docs/UPSTREAM-REPLY-DRAFT.md`](docs/UPSTREAM-REPLY-DRAFT.md) | plain-text reply to the proactive-writeback thread, for the operator to send under their own name; To/Cc and message-id are TODOs |

Docs triad: this README is WHAT; `docs/DESIGN.md` is WHY; HOW to adopt
(`docs/CONSUMERS.md`) is deliberately absent until there is something to adopt.

## Findings so far (details and labels in KERNEL-ANALYSIS)

- The memcg shrinker runs only inside reclaim (no timer) and writes back in the
  reclaiming task, blocking on the block layer's tag exhaustion; there is no
  per-pass budget or rate limit. An unarmed (all-`referenced`) batch ends a visit,
  so a fresh pool needs about N/128 light visits to arm; once armed, a visit with
  a large scan target writes back batch after batch. (source; the visit rate on
  the host is unknown)
- v7.2 has no time-decayed "protected" counter; the dampener is the number of
  recent disk swap-ins, consumed once. The `referenced` bit gives each entry one
  second chance and no age. (source)
- `memory.min/low` gate a cgroup out of reclaim only while its usage is below
  them; the shrinker's object count ignores protection otherwise, and
  `memory.high` reclaim on the cgroup ignores its own protection. (source)
- "A few minutes after settle": the source has no clock, so the trigger must be
  reclaim activity, not time. For this instance (11:19:34 local) the controller's
  load test B2 had ended 2.5 minutes earlier (09:17:02 UTC), so a preceding load
  cannot be excluded; the operator reports the pattern without load after every
  settle. Both are recorded facts, and H1 tests the operator's observation.
  (inferred; to be decided by the synthetic)
- Steady state versus burst: below `memory.low` the shrinker writes back nothing,
  above it at server start nearly everything; `memory.low` alone gives no graded
  split. KERNEL-ANALYSIS section 6.1, DESIGN "Steady state versus burst".
- Recommendation (as recorded; the provisional `vm.watermark_scale_factor` 200 was
  superseded by the operator on 2026-10-20 ~23:16 UTC: wsf 50 and
  `watermark_boost_factor` 0, see DESIGN section 5): on 7.2.6 there is nothing
  beyond measuring the reclaim caller; later the userspace governor on `memory.reclaim source=zswap`
  together with a per-pass budget/rate pursued upstream. See DESIGN section 6.

## Operator decisions (2026-10-20, verbatim)

| Item | Decision | Operator's words (full text; shorter elsewhere in the repo is abridged) |
|---|---|---|
| Raise the game's steady `memory.low` (mitigation (a)) | **REJECTED** | "no, far too much, around 4G min is fine as set now. refaults stay low at 5G. ignore your suggestion." |
| Lower `dev.slice` `memory.high` (mitigation (c)) | **REJECTED** | "no, we need it" |
| `vm.watermark_scale_factor` 200 (mitigation (b)) | accepted provisionally; live since ~10:20 UTC, **not persisted**; **superseded 2026-10-20 ~23:16 UTC: wsf 50 and `watermark_boost_factor` 0, persisted** | (no quote recorded; accepted provisionally) |
| Chunked, rate-governed writeback governor | **WANTED later** | "yes, we want that at some time" |

## Upstream references

Found by the controller's web search of LKML mirrors on 2026-10-20; **mirror
archives, not lore.kernel.org: verify there before citing**. None of them was
read by the authors of these documents.

- "mm/zswap: Implement proactive writeback" (Hao Jia), v5 2026-06:
  <https://lkml.iu.edu/2606.3/08404.html>
- The author on why `memory.zswap.max` is not enough:
  <https://lkml.indiana.edu/2605.1/07304.html> and
  <https://lkml.indiana.edu/2605.1/09790.html>
- v1 (`memory.zswap.proactive_writeback`):
  <https://lkml.indiana.edu/2605.1/04469.html>
- "mm/zswap: Fixes and improves shrinker" v3 (batch writeback):
  <https://ratatoskr.run/lkml/2026/07/17333952/t>

## Safety

Everything in `synthetic/` runs only in the QEMU/TCG VM harness
(`scripts/debian-install-v2/testing/vm`). The guard is a positive marker file
that only `synthetic/run-synthetic.sh` writes into a guest it has just started,
checked by every guest script before it changes anything; "is a VM" is not used
because the production host is itself a KVM guest. See
[`synthetic/README.md`](synthetic/README.md) (Safety) and
`synthetic/check-guards.sh`. No script uses `swapoff -a`. Nothing here should
ever be pointed at the live host (see the cgroupns/loop-device host-hang
incident in project memory).
