# zswap shrinker fill-watermark governor — feasibility report

> Companion to the feature request in [`TODO.md`](TODO.md) ("Feature request: a
> zswap fill-watermark governor") and the live incident record in
> `scripts/gstammtisch-guide/OBSERVATION.md` §1. This is a research/feasibility
> document only — no kernel code or patches were written as part of it.

Research date: 2026-09-08. Kernel source read: `mm/zswap.c` at tag `v7.1`
(matches the host's `7.1.8+deb13-bpo13+1` exactly for this file, verified by
diff against the affected functions) and at `master`/`7.3-rc2-dev` (current
mainline tip as of this date), plus tags `v6.1`, `v6.6`, `v6.8`, `v6.9`,
`v6.10`, `v6.11`, `v6.12`, `v6.18`, `v7.0`, `v7.2` to bracket when things
changed. LKML/lore.kernel.org, bugzilla.kernel.org, and several out-of-tree
kernel patch collections were searched for prior art (methodology and
citations in §3).

---

## 1. Problem statement

The host (`gstammtisch`, Debian, kernel `7.1.8+deb13-bpo13+1`, 16GB RAM,
zswap+zstd, two memory-constrained game-server cgroups) exposes exactly three
runtime-writable zswap tuning knobs under `/sys/module/zswap/parameters/`:

| Knob | What it actually controls |
|---|---|
| `max_pool_percent` | A hard **ceiling** on total pool size (% of RAM). Not a reservation, not a target — only stops the pool from growing further. |
| `accept_threshold_percent` | Hysteresis that only activates **after** the ceiling has already been hit: decides when the pool resumes accepting new pages. Inert before the ceiling is reached. |
| `shrinker_enabled` | Turns on a generic, memory-pressure-driven, per-memcg shrinker that proactively writes compressed pages back to real disk swap *before* the pool fills. |

None of these express "keep the pool around X% full, evict the excess
gradually, then stop." Confirmed live, twice, on this host: enabling
`shrinker_enabled` under genuine memory pressure does not trim any cgroup's
share of the pool proportionally or stop at a partial level. It fully
evacuates whichever cgroup's pool the kernel's reclaim walk currently selects,
all the way to 0%, with no per-cgroup floor — while a sibling, busier
cgroup's pool in the same window is left completely untouched — and it does
not self-correct once disabled (pages already pushed to disk stay there).

- **Incident 1:** one instance's pool (~650–680MB) drained to 0 over ~7
  minutes; a second, busier instance's pool was untouched in the same window.
- **Incident 2** (same session, re-enabled to see if the untouched instance
  would eventually be selected too): that instance's much larger ~2.7GB pool
  drained to 0 in **~2.5 minutes**, with directly observed collateral damage —
  in-game FPS monitoring crashed from ~25 to 8.3, `workingset_refault_anon −
  zswpin` (real disk reads replacing pages that used to be in zswap) peaked
  past 45,000 events/sec, and host-wide disk-backed swap usage rose ~5.5GB in
  the same window.

This raises two separable questions, both addressed below: (1) is there a
**design gap** — no target-fill-level knob exists at all — and (2) is the
**~2GB-in-10-to-150-seconds drain rate itself a pacing defect**, independent
of the missing target-level feature.

---

## 2. Current kernel state: what `shrinker_enabled` actually does

### 2.1 There are two independent shrink paths — this matters

Reading `mm/zswap.c` shows the pool is trimmed by **two structurally
different mechanisms**, and it is easy to conflate them:

**Path A — ceiling-triggered global reclaim (`shrink_worker`)**, always
active, independent of `shrinker_enabled`:

- `zswap_check_limits()` (v7.1 L509) sets `zswap_pool_reached_full` once the
  pool hits `max_pool_percent`; `zswap_store_page()` then does
  `queue_work(shrink_wq, &zswap_shrink_work)`.
- `shrink_worker()` (v7.1 L1311) loops `mem_cgroup_iter()` **round-robin**
  across *all* online memcgs with zswap content, calling `shrink_memcg()`
  (v7.1 L1283) on each — which walks `SWAP_CLUSTER_MAX` (32; `nr_to_walk = 1`
  per node as of v7.1, changed to the fuller `SWAP_CLUSTER_MAX` per node by
  the current mainline tip) entries per node via `list_lru_walk_one()`, then
  `cond_resched()`s — until pool-wide usage falls back to
  `zswap_accept_thr_pages()`.
- Because this is round-robin across *every* memcg with content, it is the
  closer of the two mechanisms to "fair," and it stops at a pool-wide
  threshold (`accept_threshold_percent` of the ceiling) — but that threshold
  is pool-wide, not per-cgroup, so a memcg with a disproportionately large or
  disproportionately cold share can still be driven far down while this loop
  chases the *global* target.

**Path B — memory-pressure-driven per-memcg shrinker
(`zswap_shrinker_scan`/`zswap_shrinker_count`)**, entirely gated by
`shrinker_enabled` — **this is the knob the incident toggled**:

- Registered via `shrinker_alloc(SHRINKER_NUMA_AWARE | SHRINKER_MEMCG_AWARE,
  "mm-zswap")` in `zswap_alloc_shrinker()` (v7.1 L1267), with
  `scan_objects = zswap_shrinker_scan`, `count_objects = zswap_shrinker_count`.
  This plugs directly into the **generic MM shrinker/reclaim framework**
  (`shrink_slab()` / `do_shrink_slab()` in `mm/vmscan.c`) — the same machinery
  used for dentry/inode cache reclaim, not a zswap-specific scheduler.
- `zswap_shrinker_count()` (v7.1 L1195) returns "how many zswap entries in
  *this* memcg, on *this* node, look freeable right now," and
  `zswap_shrinker_scan()` (v7.1 L1174) walks that many via
  `list_lru_shrink_walk()` → `shrink_memcg_cb()` (v7.1 L1093) →
  `zswap_writeback_entry()`.
- **Critically, kswapd/direct reclaim invoke this shrinker per-memcg based on
  that memcg's own reclaim pressure/priority**, not in a fair round-robin
  across all memcgs. The generic memcg reclaim walk in `mm/vmscan.c` decides
  *which* memcg needs reclaiming (based on its own usage vs. `memory.high`,
  PSI, and reclaim priority decay) and keeps revisiting the same memcg — at
  progressively larger scan targets as reclaim priority decays from
  `DEF_PRIORITY` toward 0 — for as long as *that memcg's own* pressure signal
  hasn't resolved. A sibling memcg that isn't showing pressure by the walk's
  own criteria is simply never selected in the same window.

**This directly explains the incident shape.** It is not that the kernel
picked "whichever pool is coldest" by some cross-cgroup fairness rule and
then arbitrarily kept going — it's that the *generic* per-memcg reclaim walk
selected one memcg as needing reclaim, and once selected, nothing in Path B's
per-call accounting establishes a lower bound before that memcg's zswap LRU
count hits zero. Incident 1's memcg was selected while its sibling wasn't;
incident 2's re-enable let the previously-unselected, busier memcg get
selected on its own turn, and by construction it drained faster (a larger
pool means a larger `zswap_shrinker_count()` return value, hence a larger
`sc->nr_to_scan` from `do_shrink_slab()`'s freeable/priority scaling — more
work per shrinker invocation, not less).

### 2.2 The "protection" heuristic exists, and has already been revised once — but it has never been a floor

Path B is not naive; `zswap_shrinker_count()`/`shrink_memcg_cb()` carry a
built-in "don't overshrink" heuristic that has visibly evolved:

- **v6.8** (introduced by commit
  [`b5ba474f3f51`](https://github.com/torvalds/linux/commit/b5ba474f3f518701249598b35c581b92a3c95b48)
  "zswap: shrink zswap pool based on memory pressure", Nhat Pham, acked by
  Johannes Weiner — merged for Linux 6.8, first release ~2024-03): a
  per-lruvec `nr_zswap_protected` counter, incremented on every new zswap
  store and on every folio swap-in, decayed by half whenever it exceeds
  `lru_size / 4`. `zswap_shrinker_scan()` **hard-stops** (`SHRINK_STOP`) once
  shrinking would eat into that protected region: `if (nr_protected >=
  lru_size - sc->nr_to_scan) return SHRINK_STOP;`. Also a `referenced`-bit
  "second chance" (rotate once before eviction) and a `mult_frac(nr_freeable,
  nr_backing, nr_stored)` compression-ratio scaling factor (evict fewer pages
  when compression is already paying off well).
- **v6.12 onward** (present unchanged through the current mainline tip):
  `nr_zswap_protected` was replaced by `nr_disk_swapins` — a counter of
  *recent real disk swap-ins*, consumed as a one-time credit subtracted from
  `nr_freeable` each scan (`zswap_shrinker_count()`, v7.1 L1195–1261). The
  idea, per the in-tree comment: "had we protected the zswap LRU by this
  amount of pages, these disk swapins would not have happened." This is
  **reactive** (it only brakes *after* real disk reads have already started
  happening) and **consumed**, not a standing floor.
- Separately, in the LRU-walk callback itself, `shrink_memcg_cb()` stops the
  *current* scan pass (`LRU_STOP`) if it "encounters a page already in swap
  cache" — read as a signal that shrinking has reached the warm region for
  this pass — but this resets per invocation; it is not sticky.

None of these — old or new — express a percentage, and none establish a
floor below which the memcg's pool will not be pushed. They only change *how
fast* the shrinker concludes a given memcg looks reclaimable; a
sufficiently-sustained pressure signal (exactly what a real, sustained
memory-constrained incident produces) will still walk `nr_freeable` down to
zero for the selected memcg. **The maintainers have iterated on getting this
signal right at least once already (v6.8 → v6.12) and are still tuning it as
of 2024** (§3.1) — this is a live area of upstream attention, not a solved
or abandoned one, but "attention" so far means better *selection of which
pages are safe to evict first*, never a target-level or floor guarantee.

### 2.3 The closest existing per-cgroup control is binary, not a percentage

`Documentation/admin-guide/cgroup-v2.rst` (and mirrored in
`Documentation/admin-guide/mm/zswap.rst`) documents `memory.zswap.writeback`
(default `1`) — set to `0` and Path A and Path B **both** skip that cgroup
entirely (`mem_cgroup_zswap_writeback_enabled(memcg)` is checked at the top
of `zswap_shrinker_scan`, `zswap_shrinker_count`, and `shrink_memcg`). There
is also `memory.zswap.max` — a per-cgroup **ceiling**, the per-cgroup analog
of `max_pool_percent`. So the existing knob set is structurally: a ceiling at
both the global scope (`max_pool_percent`) and the per-cgroup scope
(`memory.zswap.max`), and a binary exemption at the per-cgroup scope
(`memory.zswap.writeback`) — but **no floor or target at either scope**.
This host already runs `memory.zswap.writeback=1` deliberately on the game
slices (per `OBSERVATION.md` — to allow genuinely-cold pages to spill, by
design), which is precisely why it is exposed to the all-or-nothing behavior
rather than being protected from it; the only currently-available protection
would be the coarser `writeback=0`, which forfeits the entire design goal of
letting cold pages spill at all.

### 2.4 Mainline vs. LTS vs. distro/out-of-tree

- **Mainline (current, `7.3-rc2` dev tip) and every LTS still receiving
  updates (`6.18`, `6.12`) as well as `7.2`, `7.1`, `7.0`**: identical
  mechanism as described above (verified by grepping each tag for
  `shrink_memcg_cb`/`zswap_shrinker_count`/`nr_disk_swapins`/
  `writeback_time_threshold` — all present and structurally unchanged from
  v6.12 through the current tip; only the `nr_disk_swapins` refinement and
  minor internal API churn, e.g. the swap-cache folio-allocation call
  signature, separate the earliest (`v6.8`–`v6.11`) shrinker from the current
  one).
- **`6.6` and earlier LTS (`6.1`, `5.15`, `5.10`)**: the memory-pressure
  shrinker **does not exist at all** — `shrinker_enabled` as a concept
  post-dates these; older kernels effectively only have Path A (pool-full
  reclaim) plus the `max_pool_percent`/`accept_threshold_percent` pair. So
  the exact failure mode reported here is specific to kernels ≥6.8.
- **Debian `7.1.8+deb13-bpo13+1`**: matches the vanilla `v7.1` tag for this
  file exactly on every function relevant to this investigation (confirmed
  by direct diff). Debian's backport kernels are not known to carry
  zswap-shrinker-specific behavioral patches, and none were found in this
  research; the host's behavior is stock upstream v7.1 behavior.
- **Out-of-tree/downstream kernels** (CachyOS, Zen, XanMod, Liquorix, TKG,
  ClearLinux, SteamOS/Valve's Neptune kernel): checked directly against each
  project's own patch sets/commit history (not just marketing pages) — **none
  carry a zswap-shrinker target-level, hysteresis, or pacing patch.**
  CachyOS's/TKG's gaming-latency work is scheduler-focused (BORE/PDS-PRJC),
  not MM; Zen and XanMod's `mm/zswap.c` commit history is 100% upstream
  authors (Ahmed/Pham/Weiner/Song/Zhou) with no local patches; Valve's
  memory-latency tuning for Steam Deck is concentrated on zram, not zswap.
  **This is a genuine, unaddressed gap across the entire kernel ecosystem
  surveyed, not something already solved somewhere and merely un-adopted
  here.**

---

## 3. Prior art / existing discussion

Searched: lore.kernel.org / LKML archive mirrors, bugzilla.kernel.org, and
public writing by the active zswap/memcg maintainers (Yosry Ahmed, Nhat Pham,
Johannes Weiner, Chris Down), plus the out-of-tree collections in §2.4.

### 3.1 (a) Target fill percentage + hysteresis for the shrinker

**No dedicated proposal found.** The closest existing precedent is
`accept_threshold_percent` itself, which already establishes the *pattern*
of a percentage-of-ceiling hysteresis pair in this exact file — but it's
scoped to the accept/reject decision after the ceiling is hit, not to the
shrinker's target. The closest *shrinker-adjacent* proposal is Takero
Funaki's "mm: zswap: global shrinker fix and proactive shrink" series (v1,
2024-06-08, [spinics.net/lists/linux-doc/msg160009.html](https://www.spinics.net/lists/linux-doc/msg160009.html);
v2 rebased on mainline 6.10-rc6, covered by LWN at
[Articles/981052](https://lwn.net/Articles/981052/) and archived at
[lkml.rescloud.iu.edu/2407.2/02263.html](https://lkml.rescloud.iu.edu/2407.2/02263.html)),
which makes `zswap_store()` proactively trigger **Path A** (the pool-full
`shrink_worker`, not Path B / `shrinker_enabled`) once usage crosses
`accept_thr_percent + 1%`, so that reclaim work happens ahead of actually
hitting the ceiling. This is target-like in spirit but (a) targets the wrong
mechanism for this incident, (b) ties the target to the existing
ceiling-relative threshold rather than an independent band, and (c) was not
merged — confirmed absent from current mainline (`master` has no
`ZSWAP_GLOBAL_SHRINK_DELAY`, no proactive-trigger-before-full logic; the
store path is unchanged from the v7.1 behavior).

### 3.2 (b) Minimum page age before shrinker-writeback eligibility

**A near-exact prior proposal exists, and it stalled.** Zhongkun He's RFC
"[RFC PATCH] zswap: add writeback_time_threshold interface to shrink zswap
pool" (2023-10-10,
[lkml.rescloud.iu.edu/2310.1/06890.html](https://lkml.rescloud.iu.edu/2310.1/06890.html))
proposed exactly this: a new `/sys/module/zswap/parameters/writeback_time_threshold`
sysfs knob, entries older than N seconds since store become writeback-eligible
— conceptually identical to MGLRU's `min_ttl_ms` but scoped to zswap. Yosry
Ahmed's reply (2023-10-12) did not reject the idea outright but pushed back
on it as premature: pointed at `memory.reclaim` and the memcg-aware shrinker
work that was in flight at the time (which shipped a few months later as
v6.8's Path B) as likely to cover the underlying need, questioned how a fixed
time threshold would be chosen per-workload, and cited zram's idle-marking
precedent as a caution about static thresholds. **Status: not merged,
effectively superseded** — the shipped `nr_disk_swapins`/`referenced`-bit
recency-based heuristic (§2.2) is what the tree got instead of an explicit
absolute-age knob. Any fresh proposal for (b) will need to answer this exact
objection directly: what does an absolute-age knob buy over the existing
recency-ordered LRU + protection heuristic, and how is the threshold chosen
without becoming another unmaintainable magic number.

### 3.3 (c) Reports of the shrinker being too aggressive/bursty/latency-causing

**Confirmed as a known, previously-raised problem class — for the sibling
mechanism, not exactly this one.** The Funaki series above explicitly
describes "severe responsiveness issues" once naive proactive shrinking was
tried, attributing it to shrinker writeback I/O contending with live
reclaim, and proposed a **`ZSWAP_GLOBAL_SHRINK_DELAY`** — a flat ~500ms
pause after a rejected store/failed page-in, chosen to roughly track
mq-deadline's target read latency — as an explicit pacing/backoff mechanism.
This is direct precedent that (i) maintainers recognize unpaced zswap
writeback as a real latency hazard, and (ii) a simple constant-delay pacing
knob is a plausible, previously-attempted shape for a fix. However: this
series targets Path A (`shrink_worker`, the pool-full path) via changes to
`zswap_store()`'s trigger condition and writeback cadence, not Path B
(`shrinker_enabled`, the memory-pressure path actually exercised in this
incident) — and it was not merged (confirmed absent from current mainline
source). **No LKML thread or kernel Bugzilla entry was found describing the
specific failure mode observed here** — a single memcg's zswap pool being
driven from a healthy working level to literally 0% within seconds-to-minutes
by the memory-pressure shrinker, with a sibling memcg untouched. Search
terms used to confirm this negative: `"zswap shrinker" aggressive`, `zswap
shrinker latency`, `zswap shrinker regression`, `zswap drains cgroup`,
`zswap shrinker "0%"`, plus a bugzilla.kernel.org search for "zswap
shrinker" (the only hit was an unrelated NULL-deref regression report,
[lkml.iu.edu/hypermail/linux/kernel/2404.2/04099.html](https://lkml.iu.edu/hypermail/linux/kernel/2404.2/04099.html)).
This appears to be a genuinely under-reported failure mode, not a known and
dismissed one — which argues for filing it upstream rather than assuming
it's already been triaged and rejected.

### 3.4 The protection-heuristic-correctness thread

Yosry Ahmed's "[PATCH 1/2] mm: zswap: increase shrinking protection for
zswap swapins only" (2024-03,
[lkml.iu.edu/hypermail/linux/kernel/2403.2/04271.html](https://lkml.iu.edu/hypermail/linux/kernel/2403.2/04271.html))
is the clearest evidence maintainers know the anti-overshrink signal isn't
perfect: the original `nr_zswap_protected` counter incremented on *any*
swap-in, including fast zswap-decompression swap-ins, which Ahmed argued was
self-reinforcing in the wrong direction (protecting stale entries and
pushing fresher ones to disk, causing more of the disk swap-ins it was
supposed to prevent). This led toward the `nr_disk_swapins`-only refinement
that shipped in v6.12 (§2.2). Ahmed's own framing suggests this was
somewhat exploratory ("might be premature"). **This thread is about signal
correctness, not about adding a floor** — it does not discuss, and does not
fix, full-drain-to-0% as a named failure mode.

### 3.5 Informal/expert commentary

Chris Down (memcg/PSI maintainer at Meta) published "Debunking zswap and
zram myths" (2026-03-24,
[chrisdown.name/2026/03/24/zswap-vs-zram-when-to-use-what.html](https://chrisdown.name/2026/03/24/zswap-vs-zram-when-to-use-what.html)),
which frames zswap's dynamic shrinker as an advantage over zram's manual,
static idle-time writeback ("there is no magic number, and it dynamically
balances the LRU based on pressure") — but the concrete recommendation for
latency-sensitive workloads in that same post is exactly the coarse existing
knob from §2.3: disable writeback per-cgroup entirely
(`memory.zswap.writeback=0`). No middle-ground target-band or pacing
mechanism is mentioned or implied to exist. This is useful as current
(2026) expert framing but does not change the prior-art conclusion: the
target-band gap is real, current, and not addressed even in the community's
own latest public guidance on this exact tension.

### 3.6 Summary of the prior-art search

| Ask | Found? | Disposition |
|---|---|---|
| (a) target% + hysteresis for the shrinker | No dedicated proposal | Closest relative (Funaki, proactive-to-ceiling) targets the other shrink path, unmerged |
| (b) minimum page age / min_ttl-style knob | Yes — proposed and discussed | RFC (He, 2023), pushed back on, unmerged, effectively superseded by the LRU+protection heuristic |
| (c) shrinker too aggressive/bursty, causing stalls | Yes — for Path A | Fix proposed (Funaki, incl. explicit pacing), unmerged; **no report found for Path B's exact full-drain-to-0% behavior specifically** |
| Out-of-tree kernels already fixed this | No | Checked CachyOS, Zen, XanMod, Liquorix, TKG, ClearLinux, SteamOS directly — nothing found in any |

---

## 4. Is the drain rate itself a separate pacing bug?

Reasoning from the source read in §2.1–2.2 (this is inference from the code,
not an external citation — flagged as such):

`zswap_shrinker_scan()`/`shrink_memcg_cb()` (Path B) contain **no explicit
rate limit**: no token bucket, no minimum inter-writeback delay, no
pages-per-second cap, and (unlike Path A's `shrink_worker()`) no
`cond_resched()` of its own — it relies entirely on the generic
`do_shrink_slab()` reclaim-priority machinery in `mm/vmscan.c` to decide how
many objects to ask for per call (`sc->nr_to_scan`, derived from
`freeable >> sc->priority`, batched in chunks — the `list_lru_shrink_walk()`
batch is effectively `SWAP_CLUSTER_MAX`-scale per call), and on the standard
kernel scheduler for any yielding. The only per-item throttles are the
accounting-level heuristics already covered in §2.2 (referenced-bit second
chance, `nr_disk_swapins` credit, compression-ratio scaling, and the
in-pass `LRU_STOP` on hitting a warm page) — none of which bound *wall-clock*
throughput. As long as the generic reclaim walk keeps selecting the same
memcg as needing reclaim (which sustained real pressure will do, by
definition), it will call back into this shrinker repeatedly with no
enforced pause between bursts, and each call's writeback I/O is a normal
synchronous swap-out — fast on this host's thin-provisioned, low-latency
backing storage (`r_await ≈ 0.3ms` per `OBSERVATION.md` §5), which is
exactly the condition under which "unthrottled" becomes "very fast" rather
than merely "not explicitly slow." A 2.7GB drain in ~150 seconds (~18MB/s
sustained, with real burstiness implied by the 45,000/s peak refault rate)
is consistent with this being CPU/IO-bound rather than policy-bound — i.e.
it runs as fast as the device and CPU allow, constrained only by generic
reclaim bookkeeping, not by any zswap-specific pacing decision.

**Conclusion: yes, this is fairly characterized as a distinct pacing defect,
separate from the missing-target-level design gap** — even a hypothetical
kernel with a perfect target-percentage-with-floor mechanism could still, in
principle, blow past the target abruptly on the way down if nothing paces
the *rate* at which it approaches that target. The two problems compound
(no floor + no pacing = fast, complete, collateral-damage-causing drains) but
are independently worth fixing, and §3.3 shows the pacing half already has
one (unmerged, different-path) precedent to draw on.

---

## 5. Proposed approaches

### (a) `target_pool_percent` + hysteresis for the shrinker

**Shape:** New module params, e.g. `target_pool_percent` (say, default
unset/disabled) and reuse the existing hysteresis pattern
(`accept_threshold_percent` already establishes exactly this idiom in this
file) for a resume threshold. Cheapest version: **global, pool-wide** — gate
`zswap_shrinker_count()` to return 0 once `zswap_total_pages()` (or the
pool-wide backing size) has fallen to `target_pool_percent` of the ceiling,
mirroring `zswap_check_limits()`'s existing pattern almost line-for-line.
Correct-but-harder version: **per-cgroup** — since the actual complaint is
about individual memcgs being driven to zero, a global pool-wide target
would not by itself stop one memcg from still being fully drained while
others hold the pool-wide average up; a real fix needs a per-cgroup floor,
most naturally exposed as a new cgroup v2 file analogous to the existing
`memory.zswap.max` (call it e.g. `memory.zswap.min` or reuse the "protection"
framing), checked inside `zswap_shrinker_count()`/`shrink_memcg_cb()`
alongside the existing `nr_disk_swapins` subtraction.

**Complexity:** Global/pool-wide variant — **low**, closely mirrors
`zswap_check_limits()`/`accept_threshold_percent`, self-contained within
`mm/zswap.c`, no new cgroup ABI. Per-cgroup variant — **moderate-to-high**:
touches `mm/memcontrol.c`'s cgroup v2 file table (new user-visible interface
file, which upstream reviews heavily and slowly — cgroup interface additions
routinely take multiple review rounds and LSF/MM discussion), needs careful
interaction with existing `memory.zswap.max`/`.writeback`, and needs a
decision on default (unset = current behavior, to avoid changing behavior
for the many deployments that don't set it).

**Lands in:** `zswap_shrinker_count()` (v7.1 L1195), `zswap_check_limits()`
(v7.1 L509) for the pool-wide variant; `mm/memcontrol.c` cgroup file table
(new file) for the per-cgroup variant.

### (b) Minimum page age before shrinker-writeback eligibility

**Shape:** A per-entry timestamp (or a coarser generation counter) set in
`zswap_lru_add()`/on store, checked in `shrink_memcg_cb()` before writeback,
with a `/sys/module/zswap/parameters/min_writeback_age_ms`-style knob —
structurally the MGLRU `min_ttl_ms` analog requested in the research brief.

**Complexity: highest of the three, with documented headwinds.** Adding a
timestamp (or even a compact epoch counter) to `struct zswap_entry` is a
per-object memory-footprint cost across a structure that can have millions
of live instances on a loaded host — exactly the kind of overhead upstream
scrutinizes hard for this file. More importantly, §3.2 shows this exact
shape was already proposed (Zhongkun He, 2023) and met with "we're already
building a recency-based heuristic for this" pushback that then shipped as
the LRU-order + `referenced`-bit + `nr_disk_swapins` machinery in §2.2. A
fresh (b) proposal needs a concrete answer for why an *absolute* age bound
adds something the *relative*-recency heuristic doesn't (e.g.: the existing
heuristic can still drain a memcg to zero given sustained pressure, no
matter how recent the youngest entry is, whereas an absolute floor like "no
page younger than 30s is eligible" would at least bound the *rate* by
bounding how much of the LRU is ever eligible at once — this is a real,
articulable difference from the 2023 proposal's framing, but it must be made
explicitly, since the maintainers already have a specific reason on record
for preferring the relative approach).

**Lands in:** `struct zswap_entry` (new field), `zswap_lru_add()`,
`shrink_memcg_cb()` (v7.1 L1093), new module_param.

### (c) Rate-limiting/pacing the shrinker's per-invocation work

**Shape:** A simple time-window budget (token bucket or even a flat
minimum-delay-since-last-writeback check, directly analogous to the
already-proposed-but-unmerged `ZSWAP_GLOBAL_SHRINK_DELAY` from §3.3) gating
either `shrink_memcg_cb()` before it calls `zswap_writeback_entry()`, or
`zswap_shrinker_scan()` before it calls `list_lru_shrink_walk()` at all —
returning `SHRINK_STOP`/skipping the pass once the current window's budget
is spent, and letting the next invocation (there will be one, since pressure
persists) pick up where it left off.

**Complexity: lowest of the three.** Self-contained within `mm/zswap.c`
(no new per-entry memory cost, no new cgroup ABI file — a module param or
two suffices, e.g. `shrinker_max_pages_per_sec` or a millisecond delay
constant), and it has the closest existing precedent to build from (§3.3),
even though that precedent targeted Path A. It directly addresses the more
operationally dangerous half of the problem (the observed application-level
stall), independent of whether a target-percentage knob ever lands, and it
composes cleanly with (a) if that is pursued later (a target% tells it
*when* to stop; pacing tells it *how fast* to get there).

**Lands in:** `zswap_shrinker_scan()` (v7.1 L1174) and/or `shrink_memcg_cb()`
(v7.1 L1093).

---

## 6. Recommendation: both, in sequence — ship the workaround now, pursue upstream in parallel, lead with (c)

**Recommendation: pursue (iii) — ship a userspace watcher now, and open an
upstream RFC in parallel — leading the upstream effort with approach (c)
(pacing) rather than (a) or (b).**

Reasoning:

1. **The userspace workaround is available today with zero new kernel code**,
   using exactly the sysfs surface already confirmed live on this host: poll
   `/sys/kernel/debug/zswap/pool_total_size` (pool-wide) or per-cgroup
   `memory.zswap.current` (the specific game slices that matter), and toggle
   `shrinker_enabled` off once inside a configured band (e.g. 70–85% of
   `max_pool_percent`), back on above it. This is genuinely useful and
   low-risk *today*, so there is no reason to wait on any kernel-side outcome
   before shipping it — this matches the TODO.md ask directly and should
   proceed regardless of the upstream track's outcome or timeline.

   **But it has a real, documented limitation worth stating plainly**: given
   the measured drain rate (~2.7GB in ~150s, with bursts implied well above
   the ~18MB/s average), a coarse poll interval (say, 30s+) can let a
   substantial overshoot happen *within one poll cycle* before the watcher
   even notices and disables the knob — the watcher mitigates the "runs
   forever, drains to zero, never recovers" failure but does not, by itself,
   fully solve the "collateral damage happens in a burst faster than
   userspace can react" half of the problem. A tight poll interval (1–5s)
   narrows this window substantially but cannot eliminate it the way a
   kernel-side pacing fix (approach (c)) would, because userspace polling has
   inherent latency the kernel's own reclaim loop doesn't.

2. **On the upstream track, lead with (c), not (a) or (b), for cost/benefit
   reasons visible directly in the code and the prior-art record**: (c) is
   the cheapest to implement (§5), has the most directly applicable existing
   precedent to cite in an RFC (§3.3 — maintainers have already engaged with
   "zswap writeback needs pacing" as a real category of problem, even if that
   specific series didn't land and targeted the other shrink path), and
   addresses the half of this incident with actual, measured, player-facing
   damage (the FPS crash and refault storm), independent of whether a
   target-percentage knob ever gets agreement. (a)'s pool-wide variant is a
   reasonable, cheap follow-on (also has a close precedent — the
   `accept_threshold_percent` idiom already lives in this exact file) but its
   *correct* (per-cgroup) form is a slower, heavier lift through cgroup v2
   ABI review, and doesn't by itself solve the burst-rate problem the
   incident actually showed the most direct harm from. (b) has documented,
   specific maintainer pushback on record from a near-identical 2023
   proposal and should be treated as the lowest-priority/highest-risk-of-
   rejection of the three unless a fresh RFC can directly out-argue that
   specific objection.

3. **This is not a case of "someone already solved this and we should just
   adopt it"** — §2.4 and §3 together show a genuine, current gap across
   mainline, every actively-maintained LTS, Debian's backport, and every
   out-of-tree/downstream kernel checked. That argues for treating this as
   worth an actual upstream submission rather than only a local workaround:
   the problem is real, current, reproducible, and (per §3.3's negative
   result specifically for Path B's full-drain-to-0% shape) apparently not
   yet reported in this exact form anywhere searched.

---

## 7. Recommended next steps

1. **Ship the userspace watcher now** (host-tuning scope, per TODO.md — lives
   in `debian_install_v2`, not `gstammtisch-guide`). Design points worth
   carrying into that separate carve/design pass: poll interval tight enough
   (1–5s) to bound overshoot given the measured drain rate; per-cgroup
   awareness (poll the specific game slices' `memory.zswap.current`, not just
   the pool-wide debugfs total, since the incident shows pool-wide and
   per-cgroup behavior can diverge sharply); explicit logging of every
   toggle event (for exactly the kind of post-incident reconstruction this
   report relies on); and a documented acknowledgment (in the watcher's own
   docs) of the burst-overshoot limitation from §6.1, so operators don't
   over-trust it as a complete fix.
2. **Write and circulate an upstream RFC for approach (c)** (pacing/rate-
   limiting Path B specifically — `zswap_shrinker_scan`/`shrink_memcg_cb`),
   citing: this incident's concrete measurements (2.7GB/~150s,
   45,000 refaults/sec peak, FPS 25→8.3) as motivating real-world evidence
   that Path B specifically (not just Path A, which is what §3.3's prior
   RFC covered) needs pacing; the Funaki `ZSWAP_GLOBAL_SHRINK_DELAY`
   precedent as prior art to build on and explicitly differentiate from
   (different path, same underlying insight); and cc the maintainers already
   active in this exact code (Yosry Ahmed, Nhat Pham, Johannes Weiner) since
   all three have authored the very functions in question within the last
   ~2.5 years.
3. **Treat (a)'s pool-wide variant as a plausible fast-follow RFC**, and (a)'s
   per-cgroup variant plus (b) as longer-horizon/lower-priority — pursue only
   if (c) lands well and there's appetite for more surface area, and for (b)
   specifically, only with a concrete rebuttal of Ahmed's 2023 objection in
   hand before drafting.
4. **Do not block the userspace workaround on any of the above** — it is
   independently useful today and should ship on its own timeline.
