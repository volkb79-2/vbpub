# Design: governing zswap writeback

Why each choice, what was rejected, and what is recommended. WHAT the project
is lives in the [README](../README.md); the kernel facts every claim below leans
on are in [KERNEL-ANALYSIS](KERNEL-ANALYSIS.md) (labels S/M/I/Q as there). A
future `docs/CONSUMERS.md` (HOW to adopt) is deferred until there is something
to adopt; nothing in this folder is adoptable yet.

## 1. Requirements

From the operator (2026-10-20) and the measurements:

- R1. `memory.zswap.writeback=1` and the shrinker stay on. Not negotiable on
  7.2.6: any option below that switches either off is marked **needs operator
  approval**, not recommended by default.
- R2. The latency-sensitive task's own threads must not execute the writeback
  (the failure is `shrink_memcg_cb` -> `blk_mq_get_tag` in game threads,
  455.8 s of D state in the incident, [M]).
- R3. Writeback is chunked: bytes per unit time bounded and derived from the
  device's measured write behaviour (14 MiB/s here, flat, likely a provider
  cap), not a constant typed in.
- R4. Fail loudly: a missing kernel interface must stop the governor, not
  degrade it silently (estate rule "defaults and fallbacks are hazards").
- R5. Do not make the cold set slower to reach than necessary: the pool is a
  tier; draining it to disk spends swap-in latency for pages that are touched
  again.

## 2. Option 1: userspace governor on `memory.reclaim source=zswap` (later)

The upstream series "mm/zswap: Implement proactive writeback" (see README for
links, v5 2026-06 as `memory.reclaim source=zswap`; **posted, not known merged;
absent from the 7.2.6 build [I]**, the earlier `memory.reclaim` probe on an empty
cgroup returned EIO for every key and was inconclusive) gives a userspace
daemon a budgeted entry point.

Design:

- A daemon in its own cgroup (so any blocking is the daemon's, satisfying R2).
- Each tick (1 s) it writes a byte budget to the game cgroup's `memory.reclaim`
  with `source=zswap`. The budget is `rate x tick`; `rate` comes from an AIMD
  controller on **measured** device behaviour: raise while the swap devices'
  `w_await` (from `/proc/diskstats`) stays under a latency ceiling and the
  game's `memory.pressure` and host IO pressure stay under thresholds; halve on
  breach; floor 0 (pause). The initial ceiling is read, not assumed: the largest
  sustained write rate observed on the swap devices (14 MiB/s on gstammtisch),
  with the controller never exceeding a configured fraction of it.
- It runs only when the pool holds entries older than a configured age and the
  host is not in a pressure episode, so it drains ahead of pressure and the
  reactive shrinker finds a small pool.
- **Fail closed:** at start it probes the interface on a throwaway child cgroup
  and refuses to run if the key is rejected (R4); the EIO-for-every-key
  behaviour seen 2026-10-20 must be treated as "interface absent", never as
  success.

Pros: the real fix for proactive draining, rate bounded by bytes, the daemon
pays the stall (R2), policy lives in userspace. Cons: needs a kernel that has it
(not 7.2.6); it does not bound a **reactive** burst by the in-reclaim shrinker
on whatever remains (only shrinks the pool that burst would find); the exact
semantics (does writeback run in the writer's context? is the budget bytes of
compressed or logical?) are from the posted series, **not read by the author of
this document** and must be verified on the target kernel before use.

## 3. Option 2: userspace levers on 7.2.x without it

What exists, honestly, each tied to the source:

| Lever | What the 7.2 source does | Use as a governor |
|---|---|---|
| `memory.zswap.max` | checked only in `zswap_store()` (`obj_cgroup_may_zswap`). If the cgroup is at its max, the storing task calls `shrink_memcg()` which writes back **one** entry per node per call, then proceeds to store (`zswap.c:1493-1497`); if that writeback fails the page is rejected to disk. | Does not drain an existing pool by itself (no stores, no eviction). With the pool near the cap it yields **1:1 eviction per new swap-out in the storing task**: a built-in rate bound equal to the swap-out rate, no bursts. Cost: paid in reclaim tasks, still blocking on `blk_mq_get_tag` if the queue is saturated; shrinker burst remains unless it is off. |
| `shrinker_enabled` (module param, global) | `count`/`scan` return 0/STOP when off (`zswap.c:1175`, `1200`). | Off: no reactive bursts (R1 violated), pool drains only by the 1:1 store path and the pool limit. Pulsed on for short windows: the shrinker is paced by device throughput, so a window `w` bounds the bytes at about `rate x w` (a time-based governor); but it cannot pick which task pays or whether reclaim visits during the window. Global, affects every cgroup. |
| `memory.zswap.writeback` (per cgroup) | 0: shrinker returns 0 for the memcg and `swap_writepage` keeps pages out of disk swap when zswap rejects them (`page_io.c:~290`). | Same pulsing as above, but scoped to the game cgroup. Cost: while 0, pages that zswap rejects (incompressible) cannot go to disk and are activated, raising memory pressure. R1 violated while 0. |
| `memory.reclaim` (7.2, no `source=`) | proactive memcg reclaim in the writer's context, protection of the target ignored (KERNEL-ANALYSIS section 6). Reclaims LRU pages (growing the pool) and calls the shrinker at the loop's priority. | A byte-budgeted proactive reclaim run by a daemon, so the daemon pays. It does **not** target the pool: it pushes more pages into zswap, and the shrinker budget is priority-based, not byte-based. Not a drain. |
| cgroup `io.max` on the game's swap I/O | not analysed: swap bios are associated with the folio's blkg (`bio_associate_blkg_from_page`, `page_io.c`), but how `blk-throttle`/`iocost` treat swap bios was **not read**. | Candidate only. If it throttled asynchronously it would move the sleep from the submitter into queued bios while folios pile up under writeback: unproven, must be read in `block/` and tried in the synthetic before anyone relies on it. |
| `vm.swappiness`, `memory.swap.max` | decide what is swapped out, not how fast the pool is written back. | Not governors. |

Approximations ranked by cost to R1:

1. **Store-time 1:1 eviction** (`memory.zswap.max` set to the settled pool size)
   with the shrinker on: bounded by the swap-out rate where the reactive burst is
   still possible. It limits **pool growth**, not an existing burst. Weak.
2. **Pulsed `shrinker_enabled`/`writeback`** (daemon toggles, duty cycle chosen
   from the device rate): bounds writeback bytes per pulse by construction.
   Needs operator approval (R1), and the stall still lands in whichever task is
   in reclaim during the pulse.
3. Nothing in userspace on 7.2.6 both honours R1 and prevents the stall. State
   this plainly to the operator; do not oversell option 2.

## 4. Option 3: a kernel change

Candidates, ordered by how little they disturb reclaim:

- **(3a) A per-pass budget in the zswap shrinker.** Cap `nr_to_scan` honoured per
  `zswap_shrinker_scan` call (or per `do_shrink_slab` visit) so one reclaim
  episode cannot descend priority 0 and drain the pool
  (KERNEL-ANALYSIS section 5). Smallest patch; keeps behaviour for small pools.
  Con: the deferral mechanism (`nr_deferred`, cap `2 x freeable`) re-adds the
  debt later; the cap must interact with it, or the work resurfaces as a burst.
- **(3b) A token-bucket rate** per memcg (bytes/s, tunable in
  `memory.zswap.*`), with the rate set from userspace from the measured device
  rate. Matches R3 exactly; a new ABI knob, so slower to land upstream.
- **(3c) Do the writeback outside the reclaimer.** Queue entries to the existing
  `zswap-shrink` workqueue and return without waiting, so direct reclaim never
  blocks on the block layer (R2). Con: reclaim sees no progress and memory is
  not freed until the I/O completes, so under real pressure this risks OOM
  or deeper priority; the progress accounting problem (KERNEL-ANALYSIS section 5,
  writeback is not credited) must be solved first.
- **(3d) Non-blocking submission.** Mark shrinker writeback bios `REQ_NOWAIT` so
  `blk_mq_get_tag` fails fast; on `-EAGAIN` stop the walk (`LRU_STOP`). This
  paces at device speed without ever sleeping in the reclaimer. Con: the
  writeback path decompresses and frees the entry **before** submitting
  (`zswap.c:1043-1062`), so a failed submission needs a restore path;
  non-trivial and untried. A sketch **[I]**, not a recommendation to implement.

Any of these first needs the upstream conversation (see
[UPSTREAM-REPLY-DRAFT](UPSTREAM-REPLY-DRAFT.md)): the "Fixes and improves
shrinker" batch-writeback series overlaps (3a) and (3c).

## 5. Option 4: `vm.watermark_scale_factor` and where reclaim runs

Raising it (50 -> 200, live provisionally since ~10:20 UTC 2026-10-20) widens the
gap between the min/low/high watermarks so kswapd starts earlier and the
allocator reaches direct reclaim later. It acts only if the burst caller is
**global direct reclaim** (KERNEL-ANALYSIS section 3): then writeback executes in
kswapd, not in the game's threads. Limits, from source:

- It does nothing for `memory.high` reclaim at user-mode return
  (`__mem_cgroup_handle_over_high`), which is entirely separate from watermarks.
- kswapd is one thread per node. If it sleeps in `blk_mq_get_tag` behind the
  throttled device, it frees nothing meanwhile and allocators fall back to direct
  reclaim [I]; the synthetic's `pgscan_kswapd` vs `pgscan_direct` shows it (H3).
- It does not bound the writeback, it relocates it. The pool still drains at
  device speed in one go, just in another thread.
- Cost: more kswapd CPU and less usable free memory (MEMORY-ARCHITECTURE.md
  mitigation (b)).

## 6. Recommendation

## Operator decisions (2026-10-20, recorded verbatim)

| Item | Decision | Operator's words |
|---|---|---|
| Raise the game's steady `memory.low` (mitigation (a)) | **REJECTED** | "no, far too much, around 4G min is fine as set now. refaults stay low at 5G. ignore your suggestion." |
| Lower `dev.slice` `memory.high` (mitigation (c)) | **REJECTED** | "no, we need it" |
| `vm.watermark_scale_factor` 200 (mitigation (b)) | accepted provisionally; live since ~10:20 UTC, **not persisted** | (no quote recorded; accepted provisionally) |
| Chunked, rate-governed writeback governor | **WANTED later** | "yes, we want that at some time" |

Within those constraints the honest answer for 7.2.6 is: **there is nothing to
do beyond keeping `vm.watermark_scale_factor` 200 (provisional) and measuring
the caller.** No userspace lever on 7.2.6 both keeps R1 and prevents the stall
(section 3); the two other mitigations are rejected. What remains is evidence:
find the reclaim caller (the synthetic's full-stack sampling, or a real-host
trace with `kstack(32)` or deeper), because only that says whether the wsf change
can help at all (it acts only on global direct reclaim). Decision table for what
a measurement would change:

| Measured caller | Meaning | Action |
|---|---|---|
| `global-direct` and H3 PASS | watermark relocation works | keep option 4 and persist it with the operator's approval; plan option 1 for when the kernel has it |
| `global-direct` and H3 FAIL | kswapd blocks too | option 4 is not a fix; pursue option 3 upstream; the 7.2 userspace levers (section 3) need operator approval |
| `memcg-high` | memory.high reclaim in the game's threads; the game's own min/low are ignored for target reclaim | watermarks and memory.low are irrelevant; relaxing the game's `memory.high` is a separate operator decision (not yet asked); otherwise (3a)/(3b) upstream |
| mixed | both | combine the above per share |

**Later: option 1, the userspace governor on `memory.reclaim source=zswap`,
together with option 3 pursued upstream.** Option 1 is the only design that both
bounds bytes by measured device rate and keeps the stall out of the game's
threads, but it governs the proactive path only; a per-pass budget or rate in the
shrinker itself (3a/3b) is what bounds the reactive path. The reply draft asks
the upstream author for both.

Rejected by the operator (above): raising `memory.low`, lowering `dev.slice` `memory.high`.
Not recommended: any option that turns the shrinker or writeback off (R1), and
anything relying on `io.max` before it has been read and tried (section 3).

Harness note: the synthetic throttles the whole virtual disk because `vmctl`
has no second-drive option. A `--extra-drive` pass-through in `vmctl start`
would let swap sit on a separate throttled disk and remove the shared-root-fs
confound; it is a harness change outside this project's scope and not done.
