# Kernel analysis: the zswap shrinker in Linux v7.2

Scope: what the kernel source says about the burst writeback stall measured on
gstammtisch (kernel `7.2.6+deb13-amd64`) on 2026-10-20. Companion documents:
[README](../README.md), [DESIGN](DESIGN.md), [UPSTREAM-REPLY-DRAFT](UPSTREAM-REPLY-DRAFT.md),
[synthetic/README](../synthetic/README.md).

## 0. Source basis and how to read this document

- **Tag read: `v7.2`** (Linus's tree), fetched read-only on 2026-10-20 from
  `raw.githubusercontent.com/torvalds/linux/v7.2/...` and, for two files that
  GitHub rate-limited,
  `git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/<path>?h=v7.2`.
  Line numbers below are of those files at that tag.
- **The host runs `7.2.6`, a stable/Debian build of 7.2.** The 7.2.y stable
  tags are not in the Linus tree and were not fetched; stable point releases
  normally carry bug fixes only, but "the 7.2.6 source equals v7.2 in these
  functions" is **[I]** (inferred), not checked.
- Files and what they proved:

  | File (at v7.2) | Lines of interest |
  |---|---|
  | `mm/zswap.c` (1832 lines) | store 1449-1560, writeback 987-1062, shrinker 1064-1275, `shrink_memcg` 1278-1304, `shrink_worker` 1306-1385 |
  | `mm/shrinker.c` (822) | `do_shrink_slab` 374-474, `shrink_slab_memcg` 476-596 |
  | `mm/vmscan.c` (8036) | `flush_reclaim_state` 283-320, `get_scan_count` protection scaling ~2420-2475, `shrink_node_memcgs` 6067-6144, `do_try_to_free_pages` 6454-6550, MGLRU `shrink_one` 5013-5030 |
  | `mm/memcontrol.c` (6068) | `reclaim_high` 2328-2350, `high_work_func` 2352-2357, `__mem_cgroup_handle_over_high` 2505-2600, zswap charge 5837-5930 |
  | `include/linux/memcontrol.h` (1991) | `mem_cgroup_unprotected` / `below_low` / `below_min` 604-634, `mem_cgroup_protection` 549-600 |
  | `mm/page_io.c` (721) | `swap_writepage_bdev_async` 449-464, `__swap_writepage` 466-487, swap-in hook 689-693 |
  | `include/linux/shrinker.h` | `DEFAULT_SEEKS` = 2 (line 119); `DEF_PRIORITY` = 12 (`include/linux/mmzone.h:1381`) |

- Labels: **[S]** proven by the quoted source; **[M]** measured on the host
  (repo files cited); **[I]** inferred; **[Q]** only the synthetic (or a
  traced real run) can decide. Section 9 collects them in one table.

A v7-specific correction first: the older "protected" accounting
(`nr_zswap_protected` with a time/shift decay in `zswap_lruvec_state`) **does
not exist in v7.2**. The per-lruvec state is now a single counter,
`nr_disk_swapins` (`zswap.c:703-706`). Anything written about the shrinker from
6.8-era sources (including the 6.12 kernel Debian trixie ships, which the VM
harness guest runs by default) describes a different dampening mechanism.

## 1. When the memcg shrinker runs

**[S]** `zswap_shrinker` is a normal `SHRINKER_NUMA_AWARE | SHRINKER_MEMCG_AWARE`
shrinker (`zswap.c:1262-1275`, name `mm-zswap`, `batch = 0`,
`seeks = DEFAULT_SEEKS`). It runs only when generic reclaim calls
`shrink_slab()` for a memcg. There is no timer, no workqueue and no periodic
scan that drives it. The callers of `shrink_slab` for memcgs are:

- `shrink_node_memcgs()` (`vmscan.c:6129`), used by kswapd (full walk,
  `vmscan.c:6083-6084`), by global direct reclaim (`try_to_free_pages` ->
  `do_try_to_free_pages` -> `shrink_node`), and by memcg reclaim
  (`try_to_free_mem_cgroup_pages`, including memory.high reclaim, section 6);
- the MGLRU variant `shrink_one()` (`vmscan.c:5026`) with the same semantics.

So "the shrinker runs" means "some task or kswapd is in reclaim and visits this
memcg". Whose thread that is decides who pays for the writeback (section 3).

**What `zswap_shrinker_count()` returns** (`zswap.c:1190-1260`), per memcg and
node, in this order:

1. 0 if `shrinker_enabled` is off or `memory.zswap.writeback` is 0 on the
   memcg or any ancestor (`1200`, `mem_cgroup_zswap_writeback_enabled`,
   `memcontrol.c:5931-5942`);
2. 0 if the reclaim `gfp_mask` lacks `__GFP_IO|__GFP_FS` (`1210`);
3. `nr_stored` (`MEMCG_ZSWAPPED`) and `nr_backing` (`MEMCG_ZSWAP_B >> PAGE_SHIFT`)
   from the memcg's stats; 0 if nothing is stored (`1228`);
4. `nr_freeable` = number of entries on this memcg's zswap LRU (`1231`);
5. minus the **disk-swapin counter**: `nr_disk_swapins` is incremented every
   time a page is swapped in from the actual swap device after missing zswap
   (`zswap_folio_swapin`, called from `swap_read_folio`, `page_io.c:693`,
   defined `zswap.c:708-717`). In the count function the counter is **consumed**:
   `nr_freeable` is reduced by `min(counter, nr_freeable)` and the counter is
   reduced by the same amount (`1236-1250`);
6. scaled by `nr_backing / nr_stored` (`1259`), so a well-compressing pool
   offers proportionally fewer objects. At the measured ratio ~2.6x the count
   is about 0.38 of the stored page count.

**Decay of the protection over time: there is none [S].** The v7.2 dampener
is event-driven only: disk swap-ins push the count down, and the next count call
that sees a large enough LRU consumes them in one step. Nothing decays with
wall-clock time or with the number of reclaim passes. A burst of disk swap-ins
(the monitor shows `rfd/s` of 1227-1403 between 11:16:49 and 11:17:05, i.e.
roughly 20k pages, **[M]**, `measurements/2026-10-20-soulmask-startup-monitor.txt`)
protects at most that many objects, once.

## 2. The second-chance `referenced` flag

**[S]** `struct zswap_entry` has a `referenced` bool (`zswap.c:193`), set to
`true` for every newly stored entry (`1461`). In `shrink_memcg_cb()`
(`1088-1167`):

```c
if (entry->referenced) {          /* 1102 */
        entry->referenced = false;
        return LRU_ROTATE;        /* only clear the bit and rotate */
}
...                               /* second visit: unlock LRU, write back */
```

Therefore: **yes, an entry survives its first shrinker visit and is written
back on its next visit** (the comment block at `1066-1077` states exactly this
intent). Two properties follow, both **[S]**:

- the clearing visit costs a scan unit but no I/O, so it is cheap; a single pass
  that merely has to rotate the whole list can "arm" the entire pool
  (every entry `referenced == false`) without writing a page;
- there is no age: the only clock is *how many shrinker visits the entry has
  seen*, and visits happen only inside reclaim (section 1).

**Candidate mechanism for "reproducible a few minutes after the server
settles" [I]:** the squeeze at 11:11:38-11:11:52 pushed ~780 MB into zswap
(zpool 37M -> 755M in 15 s, **[M]**). Every one of those entries was stored with
`referenced = true`. Light reclaim visits since then (the pool even grew
slowly 773M -> 789M until 11:15:27, **[M]**, during the controller's load test B2 (11:12:55-11:17:02 local), which means reclaim kept swapping
the game's pages out) would have cleared the flags a few hundred entries at a
time; once the flags are clear, the next reclaim visit **with a large scan
budget** writes the cleared entries back at full device rate. The delay to the
burst is then "how long until a high-budget visit arrives", not an age.
That a flag-clearing phase exists is **[S]**; that it is what happened on the
host is **[I]**; its duration depends on reclaim visit frequency and budget and
only a traced run or the synthetic (H1) can decide it **[Q]**.

## 3. In whose context writeback runs, and whether it blocks

**[S]** `shrink_memcg_cb()` (called from `list_lru_shrink_walk()` inside
`zswap_shrinker_scan()`, `1181`) calls `zswap_writeback_entry()` (`987`)
**synchronously in the reclaiming task**: it allocates a swap-cache folio
(`GFP_KERNEL`, `1018`), decompresses into it (`1043`), frees the entry
(`1056`), and calls `__swap_writepage()` (`1052`). On this host's virtio-blk
swap devices (`vda6`, `vda7`; not `SWP_SYNCHRONOUS_IO`) that is
`swap_writepage_bdev_async()` (`page_io.c:449-464`): `bio_alloc`,
`folio_start_writeback`, `submit_bio()` and return; it does **not** wait for
completion. The only blocking point is inside `submit_bio()`: when the request
queue has no free tag, `blk_mq_get_tag()` sleeps. That is exactly the recorded
frame `blk_mq_get_tag <- ... <- submit_bio_noacct_nocheck <- shrink_memcg_cb`
(**[M]**, controller's D-state log). With `nr_requests = 16` (set 2026-10-20,
IO-STALL-TESTING-GUIDE.md section 10 B1) the queue depth, hence the pacing, is
whatever 16 in-flight requests drain at, i.e. the 14 MiB/s device cap
(**[M]**, iostat). So: **the shrinker is paced by the block layer's tag
exhaustion, and the pacing cost is paid by whichever task is running the
shrinker.** No other throttle appears in the path **[S]**.

Which task: whatever thread invoked reclaim. The recorded stacks were captured
with `kstack(10)` and are cut off at
`submit_bio_noacct_nocheck < shrink_memcg_cb < __list_lru_walk_one <
list_lru_walk_one` **[M]**, so **the caller above `list_lru_walk_one` is not
recorded**. From source the candidates are (each reaches `shrink_slab` for the
game memcg):

| Candidate caller | Chain | Runs in |
|---|---|---|
| Global direct reclaim | `__alloc_pages_slowpath` -> `try_to_free_pages` -> `do_try_to_free_pages` -> `shrink_node` -> `shrink_node_memcgs` -> `shrink_slab` | the allocating game thread |
| Memcg `memory.high` reclaim on return to user mode | `mem_cgroup_handle_over_high` -> `reclaim_high` -> `try_to_free_mem_cgroup_pages` -> ... -> `shrink_slab` (`memcontrol.c:2505-2539`, `2328-2350`) | the charging game thread |
| Memcg limit reclaim at charge | `try_charge_memcg` -> `try_to_free_mem_cgroup_pages` (`memcontrol.c:2655`) | the charging game thread |
| kswapd | `balance_pgdat` -> `shrink_node` -> same | kswapd (not a game thread) |
| `memory.zswap.max` hit | `zswap_store` -> `shrink_memcg` (`zswap.c:1493-1497`), one entry per call | the swapping-out thread; not applicable here, `zswap.max = max` ([M], `obj_cgroup_may_zswap` returns true at `memcontrol.c:5851-5852`) |
| `shrink_worker` | pool limit, kworker | a kworker; not applicable, see section 4 |

Because the stacks name `shrink_memcg_cb` inside **game** threads, kswapd and
the kworker are excluded **[M+S]**; the remaining candidates are the first
three. Both the 21 s of `try_to_free_pages` (global direct reclaim) and the
`memory.high` hypothesis are alive. **The synthetic therefore records full
stacks and names the caller as a measurement** (section 8, and
[synthetic/README](../synthetic/README.md)); this document does not assume one.

## 4. Pool-limit path versus memcg shrinker path

**[S]** Two different drivers call the same `shrink_memcg_cb`:

- `shrink_worker()` (`1306-1385`): a kworker on `zswap-shrink`, queued only when
  `zswap_store()` fails while `zswap_pool_reached_full` (`1536-1537`, set by
  `zswap_check_limits()` `509-522`). It walks memcgs round-robin, one entry per
  memcg visit (`nr_to_walk = 1`, `1286-1296`), down to
  `accept_threshold_percent` of the pool maximum (`1313`). Kworker context, so
  never in a game thread.
- the memcg shrinker (sections 1-3), in the reclaiming task.

Host numbers: `max_pool_percent = 25` of 16 GB = ~4 GB maximum **[M]**; the
largest pool ever seen was 789M (monitor log). The pool limit was never close,
so `shrink_worker` was not the actor; **the stacks in game threads show the
memcg shrinker path [M+S]**.

## 5. Why the whole pool drained: any per-pass budget or rate limit?

**[S]** There is no byte, page or time budget, and no rate limit, in
`zswap_shrinker_scan`/`shrink_memcg_cb`. The only bound is the scan target
computed by the generic code in `do_shrink_slab()` (`shrinker.c:376-474`):

```
freeable   = count_objects()                       /* ~0.38 x stored pages */
delta      = (freeable >> priority) * 4 / seeks    /* seeks = 2: 2*freeable/2^prio */
total_scan = min((deferred >> priority) + delta, 2*freeable)
while (total_scan >= batch || total_scan >= freeable)   /* batch = 128 */
        scan_objects(min(128, total_scan))         /* each call = up to 128 LRU entries */
```

Consequences, all **[S]** from the arithmetic and `DEF_PRIORITY = 12`:

- At the first reclaim priority (12) `delta` is tiny: `2*freeable/4096`, i.e.
  ~100 objects for a ~200k-object pool. Below `batch`, so **no scanning at all**;
  the amount is added to the memcg's deferred counter (`nr_deferred`, capped
  at `2*freeable`, `shrinker.c:462-470`) and tried again later.
- `total_scan` doubles for every priority step down. Around priority 6 it is
  ~6k objects per memcg visit, around priority 1 it is ~`freeable`. At
  priority 0 `delta = 2*freeable` and the clause `total_scan >= freeable`
  makes the loop run until the LRU is exhausted. So **a single reclaim
  episode that drives the priority down can legitimately drain the entire
  pool in one `do_shrink_slab` call**: the 128-entry batches are the only
  granularity, and `cond_resched()` between batches (`shrinker.c:453`) is the
  only pause. There is no "give up after N pages" and no check of how much the
  device can absorb.
- Deferral carries unmet work across reclaim episodes (`nr >> priority` is added
  next time), so a long run of light passes accumulates scan debt that a later
  pass spends at once **[S]** (cap `2*freeable`).
- A visit ends early only on `LRU_STOP` (the shrinker met a page that is already
  in the swap cache, `1158-1161`, "shrinking into the warmer region"), or when
  the walk finds no entries.

**Why reclaim goes deep, a source-grounded chain [I for the conclusion]:**

1. The shrinker's work is not credited as reclaim progress. `shrink_node_memcgs`
   discards the return value of `shrink_slab` (`vmscan.c:6129`), and zswap
   writeback never touches `current->reclaim_state` (`grep` of `zswap.c` finds
   no `reclaim_state`); even slab pages credited via `flush_reclaim_state()` count
   only for root (global) reclaim (`vmscan.c:316`).
2. `do_try_to_free_pages` keeps lowering `sc->priority` while
   `nr_reclaimed < nr_to_reclaim` (`6467-6478`).
3. Hence pool writeback executed in a direct-reclaim episode does not make that
   episode finish sooner; if the LRU scan alone cannot reclaim enough (protected
   cgroup, mostly cold anon already compressed), the episode descends to deeper
   priorities, where the shrinker budget is large. This explains the *shape*
   (one episode eats everything) but whether it happened on the host is **[I]**.

## 6. memory.min / memory.low: what the source says

Protection acts at two places, and the shrinker only sees one of them.

**(a) Whole-or-nothing gate, per memcg [S].** In `shrink_node_memcgs()`
(`vmscan.c:6102-6120`): after `mem_cgroup_calculate_protection()`, a memcg
whose usage is `<=` its effective `memory.min` is skipped entirely (no
`shrink_lruvec`, no `shrink_slab`); one `<=` effective `memory.low` is skipped
on the first pass unless `memcg_low_reclaim` was set by a retry
(`do_try_to_free_pages`, `6540-6545`, when the first pass reclaimed nothing).
So a game scope **below** its effective low is not visited by the zswap
shrinker in the normal first pass.

**(b) Proportional scaling of the LRU scan [S].** `get_scan_count()` scales the
LRU scan by `1 - protection/usage` (`vmscan.c:2429-2468`) for a cgroup above
its protection. **This scaling is applied only to `shrink_lruvec`. `shrink_slab`
gets no equivalent**: `shrink_slab(sc->gfp_mask, nid, memcg, sc->priority)`
(`6129`), and `zswap_shrinker_count()` never consults `memory.min/low`. So for
a memcg that is above its low, the shrinker's scan target is unscaled by
protection; only the priority (pressure) moves it.

Using the live numbers: a game scope using ~7.5 G with `memory.low` 5000M is
above its low, so it is not skipped (a), its LRU scan is scaled to ~33% (b),
and its zswap shrinker runs at full strength. Lowering `memory.min/low` makes
no difference to that decision; **raising `memory.low` above the usage makes
the memcg skipped in the first pass (a)**, which is what mitigation (a) in
MEMORY-ARCHITECTURE.md relies on (rejected by the operator 2026-10-20, see DESIGN) (and the cgroup then only gets reached on the
`memcg_low_reclaim` retry, when other cgroups have nothing to give).

**Target-memcg reclaim ignores protection entirely [S].** For reclaim whose
target is the memcg itself, `mem_cgroup_unprotected(target, memcg)` is true when
`memcg == target` (`memcontrol.h:604-614`), and `mem_cgroup_protection()`
returns 0 for the target (`memcontrol.h:561-600`). `memory.high` and
`memory.max` reclaim use the game scope as target, so **its own `memory.min` and
`memory.low` do not restrain memory.high reclaim at all**. That makes the memory.high
caller (section 3) a distinct hypothesis (H4) with a distinct mitigation:
protection changes do nothing for it; only `memory.high` placement or the
shrinker pacing do.

**Other facts relevant to the stall [S]:** zswap'd bytes are charged to the memcg
(`obj_cgroup_charge_zswap`, `memcontrol.c:5878-5902`, `obj_cgroup_charge` in the
store path), so `memory.current` includes the compressed pool and moving an
anon page into zswap lowers charge only by the saved fraction. A page written
back leaves the memcg when it is freed after the I/O completes. The relation
between the monitor's `RAM` column and `memory.current` is **not verified** in
this repo; if `RAM` is `memory.current`, the game sat ~500M below `memory.high`
8000M, which would not by itself trigger memory.high reclaim without growth
(transient excursions are not visible at 5 s sampling).

## 7. Reconciling with the operator's claim

Operator claim: "no other load; a few minutes after the server settles, the
shrinker evicts everything deemed cold at once; reproducible."

| Statement | Verdict |
|---|---|
| The eviction is the memcg zswap shrinker, in the game's own threads | **proven [M+S]**: `shrink_memcg_cb` frames in game D-state stacks; 455.8 s total (measurement file + IO-STALL-TESTING-GUIDE.md section 10) |
| It writes back "everything cold at once" with no governor | **proven [S] for the code, [M] for the effect**: no per-pass budget (section 5), pacing only by tag exhaustion (section 3) |
| It is triggered by pages becoming "cold enough" over time | **not supported by source [S]**: there is no age or timer; the only per-entry state is the 1-bit `referenced` and the only clock is reclaim visits. "Cold enough" can only be "visited twice" |
| It needs no other load | **the operator reports the pattern after every settle without load; for this instance (11:19:34) a preceding load cannot be excluded**: the controller's load test B2 (11:12:55-11:17:02 local) ended 2.5 min earlier, and the pool growth 773M -> 789M (11:13:44-11:15:27) lies inside it. Source side: reclaim is required **[S]**; the host is overcommitted by design (game ~7.5 G, dev ~4.4 G on 16 G) and 21 s of `try_to_free_pages` direct reclaim were recorded in the game's threads **[M]**, so baseline reclaim without any test load is plausible **[I]**. No log shows the pressure at 11:19:34 (the recorder units had expired). H1 tests this **[Q]** |
| Reproducible a few minutes after settle | **[M] observed once at 11:19:34 (7.7 min after the squeeze, 5.7 min after fps normalised, 2.5 min after load test B2 ended at 11:17:02); a second, smaller instance in B3 (09:34:00-09:34:36 UTC, 16.03 s of D in `shrink_memcg_cb`)**. A statistical statement ("reproducible") needs the synthetic or repeated traced runs **[Q]** |

So the honest synthesis: the operator's mechanism ("the shrinker, unpaced,
in our threads") is source-proven. The *trigger* is reclaim activity combined
with the second-chance arming, not time. "No other load" is compatible with
the source if the baseline overcommit pressure counts as the reclaim
source, which the 21 s direct-reclaim stacks support **[I]**; for the 11:19:34
instance the load test B2 that ended 2.5 min earlier could additionally have
left aged reclaim debt (section 5, deferral) **[I]**. The operator's general
observation and this instance are independent recorded facts; the synthetic
decides.

## 8. What only the synthetic (or a deeper trace) can decide [Q]

1. The **caller** of the burst: global direct reclaim, memory.high reclaim at
   user-mode return (`__mem_cgroup_handle_over_high`), memcg-charge reclaim, or
   kswapd (excluded on the host by the stacks). Needs stacks deeper than
   `kstack(10)`: use `kstack(32)` or more in bpftrace, or `/proc/<tid>/stack`
   sampling as the synthetic does.
2. Whether the arming (flag clearing, section 2) followed by a deep-priority
   episode produces the observed delay distribution, and whether a squeeze
   (`memory.min/low/high` step-down, H2) moves it.
3. Whether `watermark_scale_factor` 200 moves the work off the game's threads
   (only the global path, H3) or is irrelevant because the caller is
   memory.high (H4).
4. The 7.2.6-vs-v7.2 source difference (assumed none); the guest kernel must
   report `>= 7.x` for results to transfer (synthetic setup enforces it).
5. Whether the burst is two phases (arming sweep then writeback) in the real
   host: needs per-second `zswpwb`, `pgscan_*` and `allocstall_*` from the
   host, not available from the recorded files.

## 9. Source-proven versus inferred

| Claim | Label | Basis |
|---|---|---|
| zswap shrinker runs only from reclaim (`shrink_slab`), never on a timer | S | `zswap.c:1262-1275`, callers in `vmscan.c`, `shrinker.c` |
| v7.2 has no time-decayed protected counter; only `nr_disk_swapins`, consumed in `count` | S | `zswap.c:703-717`, `1236-1250` |
| An entry stored `referenced=true` is rotated once, written back at its second visit | S | `zswap.c:1102-1105`, `1461` |
| Writeback runs synchronously in the reclaiming task and blocks only on `blk_mq_get_tag` | S | `zswap.c:987-1062`, `page_io.c:449-464` |
| The game threads' stacks show the memcg shrinker, not `shrink_worker` | M+S | D-state log frames + `shrink_worker` is a kworker |
| No per-pass byte/page/time budget or rate limit | S | `shrink_memcg_cb`, `do_shrink_slab` |
| A deep reclaim priority can drain the whole pool in one episode | S (arithmetic) | `shrinker.c:376-474` |
| Writeback is not credited as reclaim progress, so episodes can go deeper | S for "not credited"; I for "so they went deeper" | `vmscan.c:6129`, no `reclaim_state` in `zswap.c` |
| memory.min/low skip a below-protection memcg entirely; shrinker count ignores protection otherwise | S | `vmscan.c:6102-6120`, `zswap.c:1190-1260` |
| Target-memcg (memory.high/max) reclaim ignores that memcg's own min/low | S | `memcontrol.h:561-614` |
| The burst's reclaim caller on the host | unknown | stacks cut at `kstack(10)` [M]; candidates in section 3 |
| 7.2.6 behaves as v7.2 here | I | stable-tag source not read |
| The pool had been "armed" by earlier flag-clearing passes | I | consistent with pool growth under light reclaim; untraced |
| The delay to the burst is "next high-budget visit", not age | I | follows from S above; [Q] |
| Burst is reproducible and its inter-arrival | Q | one full instance + one smaller (B3) |

## 10. Pointers into upstream (for the reply draft)

The reply thread and series are listed in the [README](../README.md#upstream-references).
Relevant to this analysis: the proactive-writeback series is what would give a
userspace governor a budgeted entry point (DESIGN option 1); the "Fixes and
improves shrinker" series (batch writeback) touches `shrink_memcg_cb` and may
change section 3 and 5 on a future kernel and should be re-read against this
document before any conclusion is carried forward. Neither was read by the
author of this document (links come from the controller's web search); verify
them on lore.kernel.org.
