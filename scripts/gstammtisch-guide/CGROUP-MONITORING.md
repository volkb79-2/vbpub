# Cgroup v2 Monitoring — gstammtisch / Soulmask

Reference for every metric exposed by the cgroup v2 memory, CPU, and IO controllers,
explained through the actual running Soulmask container and pak slice.

Host: Debian 13, kernel 7.0.10, 15.6G RAM, zswap+zstd, two 35G swap partitions.

---

## Quick-reference formulas

| Want to know | Formula | Example |
|---|---|---|
| True compression ratio | `zswapped / zswap` (from memory.stat) | 5742 / 1806 = **3.18×** |
| Pages actually on disk (per cgroup) | `memory.swap.current − zswapped` (from memory.stat) | 9518 − 5742 = 3776M (game: 0 because writeback=0) |
| Total unique anon footprint | `anon + zswapped` | 3872 + 5742 = **9614M** |
| Uncompressed zswap content | `zswapped` (memory.stat) | **5742M** |
| Compressed zswap size | `zswap` (memory.stat) = `memory.zswap.current` | **1806M** |
| System-wide pages on disk | `/proc/swaps Used − stored_pages × 4 KiB` | 9517 − 5742 = **3775M** |
| Is writeback=0 enforced? | `zswpwb == 0 && pswpout == 0` | Game: both 0, confirmed |
| Zswap pressure rate | `workingset_refault_anon` delta per second | 1/s = healthy |

**Do not use** `memory.swap.current / memory.zswap.current` as a compression ratio. It is inflated by swapcached and produces a meaningless number (9518 / 1806 = 5.27× here — wrong).

---

## §1 — The memory map

### 1.1 Physical RAM layout (live snapshot)

```
Physical RAM: 15,600M total
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 ┌─ GAME cgroup ─────────────────────────────────────────┐  5,919M
 │  anon pages (heap/stack/mmap)              3,872M     │
 │  ├─ swapcached subset (still hot, hold      3,730M    │  ← counted in anon
 │  │   swap slot — NOT double-counted in RAM)            │
 │  file cache (shared libs, mmap'd)            204M     │
 │  kernel overhead                           1,834M     │
 │  ├─ zswap compressed pool        1,806M               │  ← INSIDE memory.current!
 │  ├─ page tables                     25M               │
 │  └─ slab / misc                      3M               │
 └────────────────────────────────────────────────────────┘

 ┌─ PAK slice ────────────────────────────────────────────┐  3,423M
 │  shmem (tmpfs ramdisk content)            1,708M      │  ← safe from silent eviction
 │  file cache (source .pak files from cp)   1,711M      │  ← silently evictable
 │  kernel / slab                                4M      │
 └────────────────────────────────────────────────────────┘

 ┌─ Other (OS, Docker, dev containers) ──────────────────┐  ~2,800M
 └────────────────────────────────────────────────────────┘

 ┌─ Free ─────────────────────────────────────────────────┐  3,419M
 └────────────────────────────────────────────────────────┘
```

### 1.2 The zswap pool (inside RAM, inside the game's kernel memory)

```
 zswap pool = 1,806M compressed bytes, sitting in kernel memory
 ┌──────────────────────────────────────────────────────────────────┐
 │  stored_pages = 1,470,020 pages                                  │
 │  each page = 4,096 bytes uncompressed = 5,742M total uncompressed │
 │  compressed with zstd to 1,806M → ratio 5742/1806 = 3.18×       │
 └──────────────────────────────────────────────────────────────────┘

 These are the game's COLD pages — actor graph regions not currently
 in play, old zone data, cold code paths. When a player enters a
 new zone, zswap decompresses those pages back to RAM.
```

### 1.3 Page lifecycle: RAM → zswap → disk

```
 Allocation (malloc, mmap)
         │
         ▼
 ┌──────────────┐   LRU aging    ┌──────────────────────┐
 │  ACTIVE ANON │ ─────────────► │  INACTIVE ANON        │
 │  (hot pages) │                │  (cold, candidate for │
 │   1,726M     │                │   reclaim)  2,152M   │
 └──────────────┘                └──────────┬───────────┘
        ▲                                   │ kswapd or direct reclaim
        │                                   │ (memory.high triggers direct)
        │ workingset_refault                 ▼
        │ (+1 to refault counter)   ┌────────────────────┐
        │                           │  ZSWAP POOL        │
        └───────────────────────────│  (compressed RAM)  │
          decompress on next access │  zstd, 3.18×       │
          = major fault + zswpin+1  │  1,806M compressed │
                                    │  = 5,742M logical  │
                                    └─────────┬──────────┘
                                              │ writeback=1 only
                                              │ (game has writeback=0
                                              │  so this never happens)
                                              ▼
                                    ┌────────────────────┐
                                    │  SWAP DISK         │
                                    │  /dev/vda6+vda7    │
                                    │  3,775M on disk    │
                                    │  (dev containers)  │
                                    └────────────────────┘

 Game cgroup: writeback=0 → arrow from zswap to disk is CUT.
              zswpwb=0 and pswpout=0 confirm this never happened.
```

### 1.4 Swapcached: the double-entry trap

```
 The swapcached phenomenon: a page can appear in BOTH memory.current
 and memory.swap.current simultaneously.

 Timeline of a page:
   1. Allocated → in RAM → no swap slot
   2. Evicted to zswap → swap slot assigned → leaves RAM
      memory.current ▼, memory.swap.current ▲
   3. Re-accessed (refault) → decompressed back to RAM
      memory.current ▲, BUT swap slot kept (lazy free)
      → page now in BOTH counters → this is "swapcached"
   4. Eventually swap slot freed → memory.swap.current ▼

 Current snapshot:
   ├── zswapped   = 5,742M  pages OUT of RAM (in zswap, compressed)
   └── swapcached = 3,730M  pages IN RAM that still hold a swap slot

   memory.swap.current = 5,742 + 3,730 = 9,472M  (≈ 9,518M actual)

 Visualised as number lines:

   0M ────────────────────────────────────────────── 9,614M
                                                 ▲ total unique anon
   ┌─────── memory.swap.current 9,518M ──────────┐
   │ [zswapped 5,742M] │ [swapcached 3,730M]     │
   └─────────────────────────────────────────────┘
                        │
                        │ swapcached also counted here:
                        ▼
   ┌─────── memory.current 5,919M ───────────────┐
   │ anon 3,872M (incl swapcached) │ file │ kern │
   └─────────────────────────────────────────────┘
```

---

## §2 — memory.current: what is in physical RAM

```
cat memory.current → 6,205,104,128 bytes = 5,919M
```

This is the total physical RAM consumed by the cgroup right now. It counts every page
frame currently mapped into the cgroup's address space, including kernel structures
that the cgroup caused to be allocated.

### Decomposition from memory.stat

```
memory.current = anon + file + kernel

anon   = 4,059,840,512 = 3,872M   process pages: heap, stack, private mmap
file   =   214,147,072 =   204M   file cache: shared libs, read-ahead buffers
                                   note: includes shmem (but shmem = 4,096 ≈ 0M here)
kernel = 1,922,711,552 = 1,834M   kernel structures allocated on behalf of cgroup

Sum    =                 5,910M   ≈ 5,919M ✓ (small rounding + transient entries)
```

### The kernel surprise: your zswap pool is inside memory.current

The `kernel` component includes:

```
kernel = page tables + zswap compressed pool + slab

pagetables  =    25,792,512 =    25M   (PTEs mapping 5,919M of anon+file pages)
zswap pool  = 1,893,198,891 = 1,806M   (compressed cold pages, living in kernel RAM)
slab        =     3,720,149 =     4M   (dentry/inode cache, etc.)
─────────────────────────────────────
total kernel                = 1,835M   ≈ 1,834M ✓
```

**This means**: when you see `memory.current = 5,919M` for the game, that includes 1,806M of
compressed cold data. If you reduce memory.min/high to squeeze out cold pages, `memory.current`
drops because fewer compressed pages fit in the pool. It is NOT pure "live process RAM".

The actual live process pages in RAM are:
```
anon (active + inactive) = 1,726 + 2,152 = 3,878M  ≈ 3,872M ✓
file (active + inactive) =    22 +   181 =   203M  ≈   204M ✓
```

---

## §3 — memory.swap.current and memory.zswap.current: the swap pair

These two files are the most misread metrics in cgroup v2 memory monitoring.

### What each file actually measures

```
memory.zswap.current = 1,893,198,891 = 1,806M
```
The number of bytes the zswap compressor is using to store compressed pages on behalf
of this cgroup. This is the **physical RAM consumed by the compressed pool**. It is a
subset of `kernel` in memory.stat, counted separately here for visibility.

```
memory.swap.current = 9,979,514,880 = 9,518M
```
The total uncompressed size of all pages that have a **swap slot assigned**. A swap slot
is a reservation in the swap partition address space. Pages get slots when evicted; slots
are freed lazily. This counter includes:
- Pages **in zswap** (cold, not in RAM): 5,742M
- Pages **in RAM with a slot still assigned** (swapcached): 3,730M

```
memory.swap.current  ≠  "pages on disk"
memory.swap.current  ≠  "total process size"
memory.swap.current  =  zswapped + swapcached  =  5,742 + 3,730  =  9,472M  ≈  9,518M
```

### Why out / z_pool is the wrong compression ratio

Our monitor shows `out=9,527M` and `z_pool=1,824M`. The naïve ratio is:

```
9,527 / 1,824 = 5.22×  ← WRONG
```

This is inflated because `out` (= memory.swap.current = 9,527M) includes 3,730M of
swapcached pages that are already in RAM — they are not compressed. The compressed pool
only holds the 5,742M that are genuinely out of RAM.

### Correct compression ratio

Use `memory.stat` fields `zswap` and `zswapped`:

```
zswap    = 1,893,198,891 = 1,806M   compressed bytes in pool
zswapped = 6,021,201,920 = 5,742M   uncompressed size of what IS compressed

compression ratio = zswapped / zswap = 5,742 / 1,806 = 3.18×
```

Interpretation: zstd achieved 3.18× on the game's cold data. With 5,742M of cold pages
needing to exist somewhere, zswap uses only 1,806M of RAM instead of 5,742M on disk.
Savings vs disk access: at 3.18× compression, decompression (~2–5µs per page with zstd)
replaces disk I/O (~100–1000µs per page on SSD). Net: ~100–200× latency reduction.

### True total Soulmask footprint

```
Pages in RAM (anon only)     = 3,872M   (active + inactive anon)
Pages in zswap (cold)        = 5,742M   (zswapped)
─────────────────────────────────────
Total unique anon footprint  = 9,614M   (≈ 9.4G)

swapcached (3,730M) is NOT additive — those pages are already counted
in the 3,872M anon.
```

The game's world state — actor graph, physics state, zone data for all loaded areas —
occupies ~9.4G of virtual memory. At any given moment, ~3.9G of the hot set is
decompressed in RAM; the remaining ~5.7G sits cold in the zswap pool.

---

## §4 — memory.stat: every field explained

`memory.stat` is a flat key-value file with cumulative and instantaneous counters.
All byte values are in bytes. All page counts are pages (4,096 bytes each).

### A. Current page inventory

**`anon = 4,059,840,512` (3,872M)**
Anonymous pages in physical RAM right now. Anonymous means "not backed by a file":
heap allocations (malloc), stack frames, private `mmap(MAP_ANONYMOUS)`, and shmem
(tmpfs-backed, treated as anon for LRU purposes). Does NOT include pages in zswap —
those are gone from RAM.

**`file = 214,147,072` (204M)**
File-backed pages in physical RAM: shared libraries mapped read-only, read-ahead data
from disk files, memory-mapped regular files. Includes `shmem` (see below). File pages
are "clean" if they match disk content and can be silently dropped under pressure with no
swap needed — they are just re-read from disk on next access.

**`shmem = 4,096` (≈ 0M)**
Shared memory and tmpfs pages charged to this cgroup. A subset of `file`. The game
cgroup has almost none — Soulmask uses private anonymous memory, not shared memory. The
pak slice has 1,708M of shmem (the tmpfs ramdisk content).

The critical difference between shmem and regular file cache: shmem pages **cannot** be
silently dropped. They must go through the swap path (→ zswap, then optionally → disk)
because there is no on-disk file to re-read them from. This is why the pak ramdisk
protects game data: converting pak pages from file cache (silently droppable) to shmem
(must go to zswap first) eliminates silent page loss.

**`kernel = 1,922,711,552` (1,834M)**
All kernel memory allocated on behalf of this cgroup. This is the broadest category and
includes:

| Sub-component | Value | Notes |
|---|---|---|
| `pagetables` | 25M | Page table entries (PTEs) for mapping virtual → physical addresses |
| `zswap` (= memory.zswap.current) | 1,806M | The compressed pool living in kernel RAM |
| `slab` | 3M | Kernel object caches (dentry, inode, etc.) |
| Other (stack, percpu, vmalloc, sock) | <1M | Various kernel per-process structures |

**`zswap = 1,893,198,891` (1,806M)**
The compressed bytes in the zswap pool attributed to this cgroup. Identical to
`memory.zswap.current`. This is inside `kernel`. It is physical RAM consumed by the
compressor's backing store.

**`zswapped = 6,021,201,920` (5,742M)**
The **uncompressed** size of all pages currently residing in the zswap pool. This is the
"logical" RAM that has been displaced: 5,742M of pages that were in RAM, got evicted by
the reclaimer, were compressed, and now live in the 1,806M pool.

```
zswap    = 1,806M   ← physical cost (compressed)
zswapped = 5,742M   ← logical size (uncompressed)
ratio    = 3.18×
```

**`swapcached = 3,911,290,880` (3,730M)**
Pages that are currently in physical RAM AND still hold a swap slot. They were previously
evicted (went to zswap), were brought back to RAM on a refault, but the kernel has not
yet freed their swap slot. The slot is freed lazily to avoid re-evicting and re-compressing
the same page if it goes cold again quickly.

Swapcached pages are a subset of `anon`. They appear in both `memory.current` (because
they are in RAM) and `memory.swap.current` (because they have a swap slot). This double-
counting is what makes the `out / z_pool` ratio incorrect.

**`inactive_anon = 2,257,334,272` (2,152M)**
**`active_anon = 1,810,022,400` (1,726M)**
The LRU split of anonymous pages in RAM. Active = recently accessed. Inactive = not
recently accessed, candidate for eviction. Reclaim picks from inactive first.

```
active_anon + inactive_anon = 1,726 + 2,152 = 3,878M ≈ anon (3,872M) ✓
```

**`inactive_file = 190,582,784` (181M)**
**`active_file = 23,560,192` (22M)**
The LRU split of file-backed pages in RAM. File pages are evicted before anon pages under
the default heuristic: file pages are re-readable from disk for free, so evicting them
costs nothing but a future disk read. Anon pages must be compressed/swapped.

LRU eviction order (cheapest first):
```
1. inactive_file  →  silently drop (re-read from disk next access)
2. inactive_anon  →  compress to zswap (or disk if writeback=1)
3. active_file    →  demote to inactive, then drop
4. active_anon    →  demote to inactive, then compress
```

**`unevictable = 0`**
Pages locked into RAM via `mlock()`. The game does not use mlock. Zero here.

**`file_mapped = 70,164,480` (67M)**
File pages that are actively mapped into a process's page table. A file page can be in
the page cache without being mapped (prefetched), or mapped (mmap'd library in use).
Only mapped pages cause a page fault on access; unmapped cache pages are invisible to
the process until mapped.

**`file_dirty = 0` / `file_writeback = 0`**
No dirty file data (pending writes to disk) and no writeback in progress. Expected:
the game does not write to disk frequently; DB saves happen through explicit OS calls,
not dirty-page writeback of mapped files.

**`anon_thp = 0`**
Transparent huge pages (2MB) backing anonymous allocations. Zero because `THP=madvise`
is set: THP only activates when the process explicitly requests it via `madvise()`, which
Unreal Engine does not.

**`file_thp = 54,525,952` (52M) [game] / 1,757,413,376 (1,675M) [pak]**
Transparent huge pages in file cache. These are 2MB collapsed pages in the page cache
allocated automatically by the kernel's khugepaged daemon. No `madvise()` needed for
file cache THP. The pak slice has 1,675M of file_thp because the pak files' read patterns
are sequential and collapse-friendly. This reduces TLB pressure and speeds up bulk pak reads.

**`slab_reclaimable = 2,470,448` / `slab_unreclaimable = 664,200` / `slab = 3,134,648`**
Kernel slab allocator memory. `reclaimable` = can be freed under pressure (dentry cache,
inode cache). `unreclaimable` = cannot be freed without the owning process dying (task
structs, socket buffers, etc.). Total slab for game = ~3M.

---

### B. Workingset tracking

The workingset subsystem tracks whether evicted pages were "worth evicting". When a page
is evicted, the kernel leaves a **shadow entry** (a 4-byte ghost) at the page's position
in the radix tree. If the page is accessed again while the shadow exists, it is a
**refault** — the eviction was a mistake.

**`workingset_refault_anon = 4,520,496`** — PRIMARY ZSWAP PRESSURE METRIC

Cumulative count of anonymous pages that were evicted to zswap and then accessed again
(requiring decompression). Each increment is one decompression event: one game thread
blocked for ~2–5µs while zstd decompressed 4KB.

The rate of change (delta per second) is what `soulmask-zswap-monitor.sh` shows as
`rflt/s`. At 1/s in steady state: healthy. At 40,000/s during area load: normal spike.
At sustained 500+/s at rest: memory.min is too low.

```
workingset_refault_anon ≈ zswpin  (4,510,649)
```
These two should be approximately equal. Slight divergence is from timing: the shadow
entry that causes the refault count may be recorded at a different moment than the
actual zswap decompress (`zswpin`).

**`workingset_refault_file = 367,051`**
Same concept but for file-backed pages. A file page refault means the page was dropped
from cache and then read from disk again. 367K file refaults since startup vs 4.5M anon
refaults: the game's working set is dominated by anonymous pages, not file I/O.

**`workingset_activate_anon = 1,261,724`**
When a page refaults, the kernel checks if it refaulted "fast enough" (the shadow entry
is recent enough to indicate the page was still useful). If so, it is promoted directly
to the active LRU list rather than the inactive list. This counter tracks such
"justified" promotions. 1.26M out of 4.52M refaults (28%) were promoted → the kernel
thinks these pages should have stayed in RAM.

**`workingset_restore_anon = 163,694`**
Pages that refaulted while still in the process of being evicted (the eviction and the
access raced). These pages never fully left RAM. Relatively rare (163K vs 4.52M total
refaults).

**`workingset_nodereclaim = 0`**
NUMA-specific: pages that were reclaimed from a non-local NUMA node. Zero because this
is a single-NUMA-node VM.

---

### C. Page reclaim

**`pgscan = 9,248,260`**
Total pages examined by the memory reclaimer. The reclaimer sweeps the inactive LRU
list looking for pages to evict. Not every scanned page gets evicted.

**`pgsteal = 6,510,966`**
Pages actually reclaimed (evicted). For this cgroup: evicted = compressed to zswap
(since pswpout=0). Reclaim efficiency = 6,510,966 / 9,248,260 = **70.4%**. Pages that
were scanned but not stolen were either dirty (would need writeback) or active (promoted
back to active list).

**`pgscan_kswapd = 0` / `pgscan_direct = 9,248,260`**

This is the most revealing pair in the entire stat block.

`kswapd` is the background reclaim daemon. It wakes when free memory falls below a
watermark and reclaims pages in the background, before any allocation blocks. If
`pgscan_kswapd > 0`, the system proactively freed memory.

`pgscan_direct` is **synchronous direct reclaim**: the allocating thread itself — a game
process thread calling `malloc()` or `mmap()` — is paused to run the reclaimer before
the allocation can succeed.

In this data: `pgscan_direct = 9,248,260` and `pgscan_kswapd = 0`. This means ALL
9.25M pages of reclaim happened synchronously, blocking game threads. This is the
mechanism by which `memory.high` causes latency: when the cgroup exceeds `memory.high`,
the kernel does not use background reclaim — it forces the allocating thread to pay the
reclaim cost directly.

```
memory.events.high = 31,882  ← number of times the game thread was paused to reclaim
pgscan_direct = 9,248,260    ← pages inspected during those pauses
```

This data came from the calibration squeeze tests (soulmask-mempress.sh stepping
memory.high down). In normal production with memory.high=6G and the game's hot set at
5.8–6G, `memory.events.high` should increment rarely and `pgscan_direct` should grow
slowly.

**`pgdemote_kswapd = pgdemote_direct = pgdemote_khugepaged = pgdemote_proactive = 0`**
NUMA memory demotion counters (moving pages from near NUMA node to far NUMA node).
All zero — single NUMA node VM.

**`pgpromote_success = 0`**
Pages promoted from cold NUMA node to hot NUMA node. Zero for same reason.

---

### D. Fault counters

**`pgfault = 141,951,629`** (141M)
Every page fault, including minor faults. Minor faults do not require I/O: COW
(copy-on-write), demand paging of pre-faulted pages, or mapping a newly allocated
anonymous page. This is the total virtual memory activity of the game engine since
container start. 141M faults / 6.7h of runtime ≈ 5,880 minor faults per second on
average.

**`pgmajfault = 4,521,374`**
Major faults: the page was not in RAM and required some form of I/O or decompression.
Compare with refault counters:

```
workingset_refault_anon = 4,520,496
workingset_refault_file =   367,051
sum                     = 4,887,547

pgmajfault              = 4,521,374

difference              =   366,173  ←  file refaults that did NOT leave a shadow entry
                                         (e.g., first-time disk reads before shadow was set)
```

The near-equality of `pgmajfault` and `workingset_refault_anon` confirms:
- Almost every major fault in the game cgroup is a zswap decompression, not a disk read.
- `pswpin = 0` independently confirms zero disk reads.

**`pgrefill = 901,616`**
Pages moved from the active LRU to the inactive LRU (aged down). This is normal churn.

**`pgactivate = 2,040,191`**
Pages moved from inactive LRU to active LRU (promoted due to access). Active LRU pages
are protected from immediate eviction.

**`pgdeactivate = 0`**
Pages forcibly moved from active to inactive. Zero is unusual but can happen if the
reclaimer never needed to demote active pages (inactive list was sufficient supply).

---

### E. Swap flow counters

**`pswpin = 0` / `pswpout = 0`**
Pages read from / written to the swap partition on disk. Both zero for the game cgroup.
This confirms that `memory.zswap.writeback = 0` is working: no game pages have ever
touched the disk swap partition.

**`zswpin = 4,510,649`**
Pages decompressed from the zswap pool back into RAM. Each is a zstd decompress
operation (~2–5µs). Closely tracks `workingset_refault_anon` (4,520,496).

**`zswpout = 6,076,057`**
Pages compressed from RAM into the zswap pool. Net flow:
```
zswpout - zswpin = 6,076,057 - 4,510,649 = 1,565,408 pages net accumulated in zswap
1,565,408 pages × 4,096 bytes = 6,412M uncompressed content added net
```
More pages went out than came in because the game's cold set grew over the session
as new zones were loaded and older zones cooled.

**`zswpwb = 0`**
Pages written from the zswap pool to the disk swap partition (writeback). Zero, confirming
`memory.zswap.writeback = 0` is enforced. If this were non-zero, game pages would be
competing with dev container pages for disk swap bandwidth.

**`swpin_zero = 9,847` / `swpout_zero = 20,660`**
Zero-filled pages handled through the swap path. The kernel recognises pages containing
all zeros and stores them specially without running the compressor (a zero page compresses
trivially but even better: the kernel can just discard it and return a fresh zero page on
fault). 20,660 zero pages were "swapped out" (discarded) and 9,847 were "swapped in"
(a fresh zero page was returned). This is an optimisation that bypasses the compressor
entirely.

---

### F. THP counters

**`thp_fault_alloc = 0` / `thp_collapse_alloc = 0` / `thp_swpout = 0`**
Transparent huge pages for anonymous memory. All zero because `THP=madvise` means THP
only activates when the application explicitly requests it. Unreal Engine does not call
`madvise(MADV_HUGEPAGE)`.

**`file_thp = 54,525,952` (52M) in game**
File-backed huge pages collapsed by khugepaged. Does not require application cooperation.
The game's 52M of file_thp comes from shared library regions (.so files) that khugepaged
collapsed into 2MB pages.

---

## §5 — memory.min, low, high, max: the four knobs

```
memory.min  = 5,368,709,120 = 5,120M = 5G
memory.low  = 12,884,901,888 = 12,288M = 12G
memory.high = 6,442,450,944 = 6,144M = 6G
memory.max  = max
```

### Semantics table

| Knob | Threshold type | What happens when breached | OOM risk | Reclaim type |
|---|---|---|---|---|
| `memory.min` | Hard floor — cannot drop below | Kernel protects this cgroup from global reclaim | No | N/A (protects, not limits) |
| `memory.low` | Soft floor — best-effort | Kernel prefers reclaiming from OTHER cgroups first | No | Diverts global reclaim |
| `memory.high` | Soft ceiling — throttle | Allocating thread runs direct reclaim; process slows but survives | No | Direct reclaim (synchronous) |
| `memory.max` | Hard ceiling — OOM | Cgroup OOM killer fires; process(es) killed | YES | OOM kill |

### Detailed mechanism

**`memory.min = 5G`**
The kernel treats this cgroup's first 5G of pages as globally protected. When the system
is under pressure and kswapd/direct-reclaim needs to free memory, it will not take pages
from this cgroup until it has exhausted reclaim from all other cgroups. The game's 5G floor
survives even a full Docker build storm running in parallel.

Note: `memory.min` applies only to the charge level of the cgroup at that moment. If the
cgroup currently has only 4G (e.g., early in startup), the protection is 4G, not 5G.

**`memory.low = 12G`**
A best-effort hint that this cgroup should be treated as having a 12G preference. Since
the game never reaches 12G uncompressed in RAM (natural hot set ≈ 6G), this knob is
effectively inactive in practice. It serves as a "never prefer to reclaim from us if we
have less than 12G" signal to the global reclaimer.

The ordering `memory.low (12G) > memory.high (6G)` looks contradictory but is valid:
`memory.low` is a global-reclaim hint, while `memory.high` is a per-cgroup active
throttle. They operate at different layers of the memory hierarchy.

**`memory.high = 6G`**
The soft ceiling. When `memory.current` tries to exceed 6G:
1. The allocating thread is intercepted by the kernel before `memory.current` is incremented.
2. The thread runs `pgscan_direct` (reclaim) to free some pages.
3. For this cgroup (writeback=0), freed pages go to zswap.
4. `memory.events.high` is incremented.
5. The original allocation proceeds once enough pages are freed.

From the data: `memory.events.high = 31,882` — the game was throttled 31,882 times
during calibration squeezes. Each throttle event compressed pages to zswap while blocking
a game thread. This is audible as stutter when the ceiling is too tight.

**`memory.max = max`**
No hard ceiling. If `memory.max` were set (e.g., `memory.max = 8G`) and the cgroup
exceeded it despite `memory.high` throttling, the kernel OOM killer would select and kill
a process inside the cgroup. Setting `memory.max` without understanding the game's peak
usage risks server crashes during area loads. Always use `memory.high` for soft limits;
leave `memory.max = max`.

### The reclaim cascade

When global memory pressure occurs:
```
Step 1: Reclaim from cgroups with usage > memory.high (they're over limit anyway)
Step 2: Reclaim from cgroups with usage > memory.low  (soft floor honoured)
Step 3: Reclaim from cgroups with usage > memory.min  (min floor honoured)
Step 4: Emergency: reclaim from all, but protected cgroups are last resort
```

In our configuration:
- Game: min=5G, low=12G, high=6G → protected from reclaim below 5G; throttled above 6G
- Dev containers: in dev-workloads.slice with no min/low set → reclaimed first

---

## §6 — memory.events and memory.pressure (PSI)

### memory.events

```
low            0       no global-pressure soft-floor breach
high           31,882  throttle events — allocating thread ran direct reclaim 31,882 times
max            0       no OOM ceiling breach
oom            0       no OOM events
oom_kill       0       no processes killed by OOM
oom_group_kill 0       no group OOM kills
sock_throttled 8       8 socket operations throttled by memory pressure
```

**`high = 31,882`**: This is the memory calibration history. During `soulmask-mempress.sh`
squeeze tests (stepping `memory.high` from 8G down to 5G), every 64M step caused multiple
high events as the game's allocations were intercepted and pages were compressed.

In production with `memory.high = 6G` and the game comfortably at 5.8G, this counter
should grow very slowly. A growth rate above ~100/s indicates the ceiling is too tight.

**`sock_throttled = 8`**: Eight socket operations were delayed due to memory pressure.
Likely during the same calibration period. In normal operation this should be 0 or very
small.

### memory.pressure (PSI — Pressure Stall Information)

```
some avg10=0.00 avg60=0.00 avg300=0.00 total=75,944,491
full avg10=0.00 avg60=0.00 avg300=0.00 total=75,941,757
```

PSI measures the fraction of time tasks were stalled waiting for memory.

| Metric | Meaning |
|---|---|
| `some` | At least one task was stalled; other tasks continued running |
| `full` | ALL runnable tasks were stalled (system completely blocked on memory) |
| `avg10` | Percentage of time stalled in the last 10 seconds (exponential moving average) |
| `avg60` | Same, last 60 seconds |
| `avg300` | Same, last 5 minutes |
| `total` | Cumulative microseconds of stall since cgroup creation |

**Current state**: `avg10=0.00`, `avg60=0.00`, `avg300=0.00` — no memory pressure at all
in the last 5 minutes. The game is running cleanly.

**Historical**: `total = 75,944,491 µs = 75.9 seconds` of cumulative stall since the
container started. This is the time-integral of the calibration squeeze tests. When you
stepped `memory.high` down to 5G and the game was forced to compress 1G of pages, the
allocating threads stalled for those compressions. 75.9 seconds across the entire session.

In production monitoring:
- `some avg10 > 5%` = noticeable latency; investigate
- `full avg10 > 1%` = severe; the game engine is stuck

---

## §7 — CPU and IO

### CPU

```
cpu.weight = 800
cpu.max    = max 100000
```

**`cpu.weight = 800`**: The CFS (Completely Fair Scheduler) weight. Default is 100. At 800,
Soulmask gets 8× the CPU share of a default-weight process when CPUs are contested. On a
mostly-idle host, this has no effect — the game gets all the CPU it needs regardless. Under
a heavy Docker build (which runs at cpu.weight=100 by default), the game retains 800/(800+100)
= 89% of CPU time.

**`cpu.max = max 100000`**: No CPU bandwidth cap. Format is `quota period` in microseconds.
`max` quota = unlimited. The 100,000µs period (100ms) is the scheduling window. Setting
`max = 50000 100000` would cap the cgroup at 50% of one CPU core.

```
usage_usec   = 24,174,816,779 µs = 24,175s = 6.72 hours of CPU time
user_usec    = 23,270,540,950 µs = 96.3% in user space
system_usec  =    904,275,829 µs =  3.7% in kernel space
nr_throttled = 0
throttled_usec = 0
```

6.72 hours of CPU consumed (since container start). The 96.3%/3.7% user/system split is
typical for a game engine: most work is CPU-bound game logic (physics, AI, rendering prep),
with minimal kernel calls. Zero throttle events confirms `cpu.max=max` is not limiting
execution.

### IO

```
io.weight     = default 4950
io.bfq.weight = default 1000
io.max        = (none — no hard IOPS or bandwidth cap)
io.stat: 254:0 rbytes=1,896,308,736 wbytes=4,634,025,984 rios=10,580 wios=6,899
```

**`io.weight = 4950`**: The blkio proportional weight (range 1–10000, default 500). At 4950,
the game gets ~10× the I/O bandwidth of default-weight cgroups when the block device is
contested.

**`io.bfq.weight = 1000`**: The BFQ (Budget Fair Queueing) scheduler weight (range 1–1000,
default 100). At 1000 (maximum), BFQ prioritises the game's I/O requests over all other
cgroups. Requires BFQ scheduler on the block device (`/sys/block/vda/queue/scheduler = bfq`).
Without BFQ, this file exists but has no effect.

**`io.stat`**: Since container start:
```
read:   1,896,308,736 bytes = 1.77G   (10,580 read I/Os)
write:  4,634,025,984 bytes = 4.32G   (6,899 write I/Os)
```

Writes (4.32G) massively exceed reads (1.77G). This pattern is typical: the game loaded
from disk at startup (reads), then generates continuous small writes for DB saves, logs,
and world-state persistence. The read I/O count is low (10,580 over hours) because
`pswpin=0` — the game is NOT reading from swap, only from its initial pak file load and
database reads.

---

## §8 — The PAK slice: a different story

```
Path: /sys/fs/cgroup/soulmask.slice/soulmask-paks.slice
```

The pak slice holds pages charged during `soulmask-pak-ramdisk.service` execution.
The `cp` process that populated the ramdisk ran inside this slice (`Slice=soulmask-paks.slice`
in the service unit). Pages it created are charged here and remain as long as the service
has `RemainAfterExit=yes`.

### The live values

```
memory.current        = 3,589,718,016 = 3,423M
memory.min            =   157,286,400 =   150M
memory.low            = 0
memory.high           = max
memory.max            = max
memory.swap.current   = 0    ← nothing out of RAM
memory.zswap.current  = 0    ← nothing in zswap
memory.zswap.writeback= 1    ← writeback to disk ENABLED
```

### Memory decomposition

```
file   = 3,585,241,088 = 3,419M   total file-backed pages in RAM
shmem  = 1,791,352,832 = 1,708M   tmpfs ramdisk content (subset of file)
────────────────────────────────
non-shmem file = 3,419 - 1,708 = 1,711M   source pak files read during cp

kernel = 4,476,928 = 4M   slab

Total = 1,708 + 1,711 + 4 = 3,423M ✓
```

### The 1,711M mystery: source file cache

When `cp /source/paks /mnt/ramdisk/paks` ran, it:
1. Read source .pak files → created 1,711M of regular file cache (charged to the slice)
2. Wrote to tmpfs → created 1,708M of shmem pages (charged to the slice)

The source file cache (1,711M) is still resident because there was no memory pressure
to evict it. These pages are classified as `inactive_file = 1,709M` — cold, silently
evictable. The kernel can drop them instantly with no swap needed (they are just
re-readable from disk). They are redundant data: the game reads from the ramdisk now,
not from the source .pak path.

### LRU tells the eviction story

```
active_anon   = 1,708M   = shmem (ramdisk)      ← evicted LAST
inactive_file = 1,709M   = source file cache     ← evicted FIRST
active_file   =     2M   = recently read files
```

Under memory pressure, the kernel will:
1. Drop `inactive_file` (source cache) — free, silent, no swap needed
2. Eventually compress `active_anon` (shmem ramdisk) — must go to zswap first

This ordering is exactly what we want. The redundant source cache acts as a pressure
buffer: it will be evicted before any ramdisk content is touched.

### THP in the pak slice

```
file_thp = 1,757,413,376 = 1,675M
```

1,675M of the 3,423M pak memory is in 2MB huge pages. khugepaged scanned the pak file
cache and collapsed adjacent 4KB pages into 2MB pages. Benefits: 512× fewer TLB entries
needed, faster bulk reads (single TLB miss covers 2MB instead of 4KB). This happened
automatically without any application changes.

### Zero refaults, zero zswap

```
workingset_refault_anon = 0
zswpout = 0
pgmajfault = 8   (from the initial cp setup, not gameplay)
```

The pak slice has never evicted a single page since the ramdisk was created. All 3,423M
remains in RAM. Zero zswap pressure from paks. This validates the ramdisk strategy:
without the ramdisk, pak pages would be regular file cache in the game's cgroup (or the
root cgroup), vulnerable to silent eviction during Docker builds.

### Writeback=1 and the pak disk path

`memory.zswap.writeback = 1` means: if the pak shmem pages were ever evicted from RAM,
they would be compressed into zswap, and could then be written through to disk (when the
zswap pool is under pressure from other cgroups).

The monitor's signal for this happening: `p_z > 0` (pak pages in zswap) or `p_out > 0`
(pak has swap slots). Currently both are 0 — pak is fully in RAM with zero eviction.

If `p_z = 0` and `p_out > 0` simultaneously, it means pak pages bypassed zswap and went
directly to disk — possible if the zswap pool is full and incoming pages are rejected.
See `reject_compress_poor` in the zswap debug stats.

---

## §9 — The system-wide picture

### How cgroup numbers map to /proc/meminfo

```
/proc/meminfo                    Relationship to cgroup data
─────────────────────────────────────────────────────────────────────
MemTotal    = 15,600M            Fixed: physical RAM
MemFree     =  3,419M            RAM with no pages at all
MemAvailable=  6,366M            Kernel's estimate of reclaimable free
                                  = MemFree + reclaimable_file_cache + ...

Cached      =  4,692M            System-wide page cache (file + shmem)
                                  Includes pak's 1,711M source cache
                                  Includes pak's 1,708M shmem (counted separately below too)

Shmem       =  1,714M            All tmpfs system-wide ≈ pak ramdisk 1,708M (+6M other)

SwapCached  =  3,632M            System-wide swapcached
                                  ≈ game's swapcached (3,730M) — slightly different by timing

Zswap       =  2,380M            Total compressed zswap pool (ALL cgroups)
                                  vs game cgroup alone: 1,806M
Zswapped    =  5,880M            Total uncompressed content in zswap (system)
                                  vs game cgroup alone: 5,742M
                                  → game dominates zswap; other cgroups tiny
```

### /proc/swaps accounting

```
/dev/vda6: 4,872,648 KiB used
/dev/vda7: 4,872,972 KiB used
─────────────────────────────
total used: 9,745,620 KiB = 9,517M
```

Where do those 9,517M sit?

```
In zswap (compressed in RAM):
  stored_pages = 1,470,020 pages × 4,096 bytes = 6,021,201,920 bytes = 5,742M
  compressed to pool_total_size = 2,437,136,384 bytes = 2,325M

On disk (written through):
  9,517M − 5,742M = 3,775M on disk
  ← from dev containers with memory.zswap.writeback=1

Game cgroup contribution to disk: 0M
  Confirmed by: zswpwb=0, pswpout=0
```

```
                           ┌─ SWAP ADDRESS SPACE: 9,517M used of 69G ─────────┐
                           │                                                    │
                           │  ┌─ In zswap pool (RAM) ──────────────────────┐  │
                           │  │  5,742M uncompressed → 2,325M compressed   │  │
                           │  │  ┌─ Game cgroup ─────────────────────────┐ │  │
                           │  │  │  5,742M → 1,806M  (3.18×)            │ │  │
                           │  │  └──────────────────────────────────────┘ │  │
                           │  │  Other cgroups: ~0M → 519M remnant        │  │
                           │  └────────────────────────────────────────────┘  │
                           │                                                    │
                           │  ┌─ On disk (written through) ─────────────────┐  │
                           │  │  3,775M                                     │  │
                           │  │  Dev containers (writeback=1) — game=0M    │  │
                           │  └────────────────────────────────────────────┘  │
                           └────────────────────────────────────────────────────┘
```

### zswap debug counters

Read from `/sys/kernel/debug/zswap/`:

| Counter | Value | Meaning |
|---|---|---|
| `stored_pages` | 1,470,020 | Pages currently in zswap pool |
| `pool_total_size` | 2,437,136,384 (2,325M) | Compressed pool size in RAM |
| `written_back_pages` | (non-zero) | Pages flushed from zswap to disk — dev containers |
| `reject_compress_poor` | (non-zero) | Pages too incompressible; sent to disk directly |
| `reject_alloc_fail` | (from listing) | Pool allocation failures |
| `stored_incompressible_pages` | (from listing) | Pages stored despite poor compression |

`reject_compress_poor`: when zstd cannot achieve better than a threshold ratio (default:
pages that would expand), the page is rejected from zswap and goes directly to disk. A
non-zero value here means some pages are too random/encrypted to compress — they bypass
zswap entirely.

---

## §10 — Reading the monitor output correctly

### Monitor line anatomy

```
time     | RAM    z_pool  out     rflt/s    mflt/s    | p_RAM  p_z    p_out  p_rf/s | disk_sw
04:02:04 | 5873M  1824M   9527M   1/s       1/s       | 3423M  0M     0M     0/s    | 3724M
```

Column by column:

| Column | Source | Value | Meaning |
|---|---|---|---|
| `RAM` | `memory.current` | 5,873M | All pages in physical RAM (anon + file + kernel incl. zswap pool) |
| `z_pool` | `memory.zswap.current` | 1,824M | Compressed bytes in zswap (denominator for true compression ratio) |
| `out` | `memory.swap.current` | 9,527M | Pages with swap slots = zswapped(5,742M) + swapcached(3,730M). **NOT "on disk"** |
| `rflt/s` | `workingset_refault_anon` delta | 1/s | Decompress events per second — PRIMARY pressure indicator |
| `mflt/s` | `pgmajfault` delta | 1/s | Major faults per second (≈ rflt/s confirms no disk I/O) |
| `p_RAM` | pak `memory.current` | 3,423M | Pak pages in RAM (1,708M safe shmem + 1,711M evictable file cache) |
| `p_z` | pak `memory.zswap.current` | 0M | Pak compressed in zswap (0 = pak fully in RAM) |
| `p_out` | pak `memory.swap.current` | 0M | Pak with swap slots (0 = nothing evicted from pak) |
| `p_rf/s` | pak `workingset_refault_anon` delta | 0/s | Pak decompress events (0 = zero pak eviction) |
| `disk_sw` | `/proc/swaps Used − stored_pages×4K` | 3,724M | System-wide pages on disk (dev containers; game=0M) |

### Common misreadings

**`out` is not the process size.** `out = 9,527M` looks like "Soulmask is using 9.5G".
It is not. It is the uncompressed size of pages with swap slots = zswapped + swapcached.
The total unique anon footprint is `anon + zswapped = 3,872 + 5,742 = 9,614M`.

**`out / z_pool` is not the compression ratio.** `9,527 / 1,824 = 5.22×` is wrong.
The true ratio is `zswapped / zswap = 5,742 / 1,806 = 3.18×` (from memory.stat).

**`RAM` includes the compressed pool.** When `RAM = 5,873M`, that includes 1,806M of
compressed data inside the kernel. The "live process" RAM is closer to `anon + file =
3,872 + 204 = 4,076M`. The rest is kernel overhead and zswap.

**`p_RAM = 3,423M` is not all ramdisk.** It is 1,708M of actual ramdisk (shmem) plus
1,711M of source pak file cache (silently evictable). The ramdisk is only 1,708M
currently resident.

**`disk_sw` is system-wide.** When `disk_sw = 3,724M`, that does NOT mean the game
has 3,724M on disk. The game has 0M on disk (`zswpwb=0`, `pswpout=0`). The 3,724M
is from dev containers with `writeback=1`.

### What values are suspicious

| Signal | Observation | What it means |
|---|---|---|
| `rflt/s > 500` sustained | Steady background refault | memory.min too low; hot set being pushed to zswap |
| `rflt/s > 5000` for >60s | Extended area-load spike | Floor may be too low or new zone is very large; watch if it decays |
| `mflt/s >> rflt/s` | Major faults exceed refaults | Disk reads in game cgroup — check pswpin in memory.stat |
| `out` growing without bound | memory.swap.current rising | Game's cold set growing (new zones) or swapcached growing (normal churn) |
| `z_pool` approaching RAM budget | Compressed pool very large | zswap under pressure; may start writing through to disk |
| `p_z > 0` | Pak pages in zswap | Pak is being evicted — Docker build pressure hit the pak slice |
| `p_out > 0 && p_z = 0` | Pak has swap slots, no zswap | Pak went directly to disk (zswap pool full at eviction time) |
| `disk_sw` growing fast | Dev containers filling disk swap | Docker build under memory pressure; zswap saturated |
| `memory.events.high` growing fast | Game hitting ceiling often | memory.high too tight for current player count |
| `pgscan_direct >> pgscan_kswapd` | All reclaim is synchronous | Normal when memory.high is active; causes game thread stalls |

### Normal operation baseline (3 players, steady state)

```
rflt/s:  0–30       healthy; hot set comfortably in RAM
         30–100     acceptable; light background activity
         100–500    sustained → raise memory.min
         5k–40k     area load event → normal, watch for decay within 5 min

memory.events.high:  growing < 10/s → ceiling has headroom
                     growing > 100/s → ceiling too tight

PSI some avg10:  0.00–0.10%   → no stall
                 0.10–1.00%   → mild stall
                 > 1.00%      → players noticing latency

p_z:  0M   → pak fully in RAM (ideal)
p_z > 0M   → pak being evicted; adjust pak memory.min upward
```
