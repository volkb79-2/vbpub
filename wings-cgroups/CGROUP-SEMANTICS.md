# cgroup-v2 memory semantics down a slice chain

Every value this project sets — node slice, per-server slice, egg variable —
lands on a cgroup-v2 knob whose behaviour depends on the *chain* it sits in, not
on the value alone. This file is the reference for that behaviour: what each
knob means, how nesting changes it, and the arithmetic behind
`memory_min_budget` / `budget_policy`. Deployment steps are in
[`SETUP.md`](SETUP.md); design rationale is in [`STRATEGY.md`](STRATEGY.md).

Examples use the shape this project produces:

```
-.slice                                    (root)
└─ wings.slice                             the node tier — a real unit file
   ├─ wings-mgmt.slice                     Wings itself
   └─ wings-<32hex>.slice                  one per server — transient, Wings-made
      └─ docker-<id>.scope                 the container: where pages are charged
```

## The four knobs

| Knob | Kind | Meaning |
|---|---|---|
| `memory.min` | protection | Never reclaim below this under **outside** pressure. Hard: the kernel would sooner OOM than reclaim protected pages. |
| `memory.low` | protection | Same, best-effort: yielded if the kernel finds nothing else to reclaim. |
| `memory.high` | limit | Throttle. Past this, reclaim pushes usage back down (into zswap/swap). Allocations still succeed; the workload just gets slow. |
| `memory.max` | limit | Hard wall. Reclaim, then OOM-kill inside the cgroup. |

Protection and limits are different mechanisms and nest by **different rules**.
That is the whole point of this document.

## Rule 1 — protection flows down and is capped by the parent

A cgroup can never be protected more than its parent was granted. Wanted case:

```
wings.slice          min=8G      the tier's total reservation
└─ wings-A.slice     min=6G      6G ≤ 8G → honoured in full ✓
```

Broken case, which reports no error anywhere:

```
wings.slice          min=8G
└─ wings-A.slice     min=10G     → effective 8G. The extra 2G is fiction.
```

This is why the node slice's `MemoryMin` must be ≥ the sum of every per-server
floor you intend to grant, and why Wings checks that sum at slice-creation time
(`memory_min_budget`).

## Rule 2 — limits are the minimum along the whole path

Ceilings don't distribute, they stack. The effective limit is the tightest value
between the cgroup and the root:

```
wings.slice          high=14G
└─ wings-A.slice     high=7G     → server throttles at 7G
   └─ docker-….scope high=max    → still 7G; the parent wins
```

A child limit looser than its parent's is legal and inert. So is any limit above
physical RAM: `WINGS_CG_MEMORY_MAX=20G` on a 15.6Gi host can never be reached
and does nothing.

## Rule 3 — a cgroup is never protected from itself

`min`/`low` only fend off reclaim driven from **outside** the cgroup. They are
ignored by reclaim that the cgroup's own `high`/`max` triggers:

```
wings-A.slice   min=6G  low=12G  high=7G     current=8.5G
```

At 8.5G the server is over its own `high`, so the kernel reclaims ~1.5G into
zswap continuously — `min=6G` does not object, because that pressure is
self-inflicted. What `min=6G` *does* mean: when the host is short on memory,
nothing else may take this server's first 6G.

Corollary: any `low` above the same cgroup's `high` is decorative — the cgroup
is never allowed to hold that much in the first place. `low=12G` with `high=7G`
behaves exactly like `low=7G`.

## Rule 4 — none of it works without `memory_recursiveprot`

Pages are charged to the **leaf** (`docker-*.scope`), never to the slices above
it. Without the `memory_recursiveprot` mount flag, `wings.slice min=8G` protects
only pages charged directly to `wings.slice` — approximately none — and the
container inherits nothing. Every floor on the node becomes a no-op, silently.

With the flag, a parent's protection covers its whole subtree, and children
without their own `min` share it. This is also why a node can be usefully
protected at the tier level alone: with `wings.slice min=8G` and an empty
per-server slice, the server still gets the tier's protection — it just gets no
*individual* guarantee, no per-server `high`, and no weights. Check with
`grep cgroup2 /proc/mounts` ([`SETUP.md`](SETUP.md) §1b).

## Rule 5 — overcommitted protection is distributed by usage, not by weight

When the children's floors add up to more than the parent can back, the kernel
does not fail and does not pick a winner. Each child claims
`min(its usage, its own floor)`, and if those claims exceed the parent's
protection, each child receives:

```
                       claim_i
  effective_i  =  ───────────────── × parent_protection
                     Σ claim_j
```

The claim is **usage below the floor**, so an idle server claims little and a
busy one claims its whole floor. The split is recomputed continuously as usage
moves — there is no memory weight to tune and no LRU input at this level (LRU
decides *which pages* get reclaimed once a cgroup is targeted, never *how much*
each cgroup is protected).

Worked example — two game servers, `wings.slice min=10G`, both asking `min=6G`:

| Server A usage | Server B usage | claim A | claim B | Σ claims | Effective A | Effective B |
|---|---|---|---|---|---|---|
| 6G | 2G | 6G | 2G | 8G ≤ 10G | **6G** (full) | **2G** (all it uses) |
| 6G | 4G | 6G | 4G | 10G ≤ 10G | **6G** | **4G** |
| 8G | 8G | 6G | 6G | 12G > 10G | **5G** | **5G** |
| 8G | 3G | 6G | 3G | 9G ≤ 10G | **6G** | **3G** |

The overcommit only bites when *both* servers are genuinely hot; the rest of the
time each gets everything it actually touches. That is the behaviour
`budget_policy: distribute` exists to allow. `memory.low` overcommits the same
way.

## Rule 6 — weights compose multiplicatively down the tree

`cpu.weight` and `io.weight` (1..10000, default 100) are **relative among
siblings under the same parent**, and nothing else. A weight never expresses
"share of the machine"; it expresses "share of whatever my parent got". So the
share of the whole host is the **product of the ratios along the path to the
root**:

```
              w_self                    w_parent
  share  =  ──────────────  ×  ──────────────────────  ×  … up to the root
             Σ w_siblings          Σ w_parent's siblings
```

Three consequences that catch people out:

- **A big number under a small parent is still small.** Raising a server's
  `cpu.weight` from 100 to 5000 changes nothing about how much CPU the *tier*
  gets — only how the tier's slice of it is divided between that server and its
  siblings.
- **Weights only exist under contention.** They are work-conserving: an idle
  sibling's share is handed to whoever wants it. A weight never caps anything —
  that is what `cpu.max`/`io.max` are for.
- **The share-of-total figure is a floor, not a forecast** — see the worked
  example below, which is the part everyone reads wrong.

Worked example, from a live node (root-level slices, `cpu.weight`):

```
-.slice
├─ wings.slice        800     ├─ interactive.slice  200     ├─ system.slice   100
├─ besteffort.slice    20     ├─ services.slice     100     └─ 5 × others     100 each
```

Σ at the root = 800+20+200+100+100+(5×100) = 1720, so `wings.slice` gets
800/1720 = **47%** of the host's CPU.

**That 47% is the worst case, and it is a guarantee, not a prediction.** It is
what the tier is owed in the single instant when *all ten* root slices are
simultaneously saturating the CPU — a state that essentially never occurs. Three
reasons the real number is far higher:

- **Empty slices contribute nothing.** On that same node three of the ten —
  `besteffort` and two of the `others` — had zero processes. They cannot claim a
  share, so the live denominator was 1500, not 1720 → **53%**.
- **Sleeping processes contribute nothing either.** Of the rest, most are idle
  daemons. If only `wings.slice`, `interactive.slice` and `system.slice` are
  actually runnable, the denominator is 800+200+100 = 1100 → **73%**.
- **Idle time is free.** When nothing else wants the CPU, `wings.slice` gets
  **100%** of it. Weights never cap.

So the intuition "each competitor only has 100 against my 800, so any one of
them loses badly to me" is exactly right, and it is the useful way to read a
weight: **pairwise**, `wings.slice` beats any default sibling 8:1. The Σ-based
percentage only tells you the floor beneath which the tier cannot be pushed no
matter how badly the rest of the host misbehaves. Raising 800 higher would buy
nothing in normal operation — it would only harden that worst-case floor.

Inside the tier:

```
wings.slice (47% of the host)
├─ wings-mgmt.slice                  cpu.weight=200   → 200/1200 = 17% of the tier =  8% of host
└─ wings-<server>.slice              cpu.weight=1000  → 1000/1200 = 83% of the tier = 39% of host
```

Add a second server at the node default `cpu_weight: 200` and the same tier
share is redivided — 1000/1400, 200/1400, 200/1400 — without touching anyone's
configuration. That redivision is the point: you tune the *ratio between
siblings*, and the tier's total is defended one level up.

## Rule 7 — `IOWeight` is rescaled for BFQ, and the scale is brutally compressive

This one is invisible and changes what your numbers mean.

BFQ has its own weight file, `io.bfq.weight`, on a **1..1000** scale (default
100) — it does *not* read `io.weight` (that file belongs to the `iocost`
controller, which is inert unless you configure `io.cost.model`/`io.cost.qos`).
systemd papers over this: `IOWeight=` writes **both** files, converting into
BFQ's range with the default pinned to the default:

```
  io.bfq.weight  =  w                                  for w ≤ 100
  io.bfq.weight  =  100 + (w − 100) × 900 ÷ 9900       for w > 100   (integer division)
```

The whole range 100..10000 is squeezed into 100..1000, so **ratios above the
default shrink by ~11×**. Measured on a live node by stepping `IOWeight` on a
throwaway transient slice and reading both files back — every value matches:

| `IOWeight=` | `io.weight` | `io.bfq.weight` | Effective ratio vs. a default sibling |
|---|---|---|---|
| 10 | 10 | 10 | 0.1× |
| 100 (default) | 100 | 100 | 1× |
| 200 | 200 | **109** | 1.09× — *not* 2× |
| 500 | 500 | **136** | 1.36× — *not* 5× |
| 1000 | 1000 | **181** | 1.81× — *not* 10× |
| 4500 | 4500 | **500** | 5× |
| 4950 | 4950 | **540** | 5.4× |
| 10000 | 10000 | 1000 | 10× |

So on a BFQ node, `io_weight: 1000` next to a default sibling buys **1.8:1**,
not 10:1. A genuine 5:1 IO advantage needs `IOWeight=4500`. This is why an IO
weight near the top of the range is not the overreach it looks like — whereas
CPU weights near the top really are, because `cpu.weight` is converted linearly
(`weight × 1024 ÷ 100`) and its ratios are exactly what you wrote: 800 vs 100
really is 8:1.

Rule 6 still applies on top: these ratios only decide the split *among
siblings*. A server at `io_weight: 4500` inside a tier whose `wings.slice` sits
at `IOWeight=500` (bfq 136) is fighting for the tier's share, not the disk's.

**Watch for accidental asymmetry between CPU and IO.** A node slice carrying
`CPUWeight=800` + `IOWeight=500` looks balanced and is not: 800 is 8:1 on CPU
while 500 is **1.36:1** on IO. To give IO the same 8:1 priority the tier's CPU
already has, the unit needs `IOWeight=7800` (→ bfq 800).

### Stating BFQ weights directly

Rather than reverse-engineering the IOWeight that yields the weight you want,
say it on BFQ's scale and let Wings do the conversion (patch 0005):

```yaml
docker:
  per_server_slices:
    defaults:
      io_bfq_weight: 200      # 1..1000, default 100 — BFQ's own scale
```

…or per server, `WINGS_CG_IO_BFQ_WEIGHT=500`. Wings converts with the exact
inverse (`IOWeight = 100 + 11 × (bfq − 100)`) and applies the result as an
ordinary `IOWeight` property, so the value stays systemd-owned and reload-safe.
`io_weight` and `io_bfq_weight` set the same property and are mutually
exclusive. Setting both in `defaults:` fails config validation and Wings does not
start; setting both as per-server variables is logged and neither is applied —
node configuration is the administrator's own file and gets caught loudly, while
a bad egg variable must never keep a server from booting.

Why not write `io.bfq.weight` directly? Because systemd re-derives that file
from `IOWeight` every time it re-applies the unit's IO settings, silently
clobbering a raw write — the same trap as Finding D, and the reason this
project never writes cgroupfs directly.

The applied weight appears in the Wings log with the derived value spelled out,
so the effective number is visible without knowing any of this:

```
cgroups: ensured per-server slice  properties=… IOWeight=4500(io.bfq.weight=500)
```

Check what the kernel actually holds rather than what you set:

```bash
cat /sys/fs/cgroup/<path>/io.bfq.weight    # what BFQ schedules on
cat /sys/fs/cgroup/<path>/io.weight        # what you set (iocost's file; inert without io.cost.*)
cat /sys/block/<dev>/queue/scheduler       # [bfq] or the weights do nothing at all
```

Under `none`/`mq-deadline`, neither file does anything: no proportional IO
control exists, and only `io.max` hard caps still bite.

### The panel's "Block IO Weight" is a third knob, one level down

Stock Wings already carries a per-server IO weight of its own — the panel field
that reaches Docker as `--blkio-weight`. It is easy to assume it is redundant
with the slice weights above, or that it suffers the same compression. Neither
is true. Measured on a cgroup-v2 + BFQ host, `docker run --blkio-weight 700`
produces, on the container's own scope:

```
io.bfq.weight = 700          # runc writes BFQ's file directly, uncompressed
io.weight     = 100          # untouched
```

So it lands on BFQ's own 10..1000 scale, at the **scope** level. The weights in
this document act on the **slice** above it. By Rule 6 the two compose rather
than compete:

```
wings.slice                 IOWeight        share of the disk
└─ wings-A.slice            io.bfq.weight   share of the tier   ← WINGS_CG_IO_BFQ_WEIGHT
   └─ docker-….scope        io.bfq.weight   share of the slice  ← panel "Block IO Weight"
```

The scope weight only matters when a slice holds more than one container, which
for a per-server slice means it is almost always inert — one container, no
siblings, nothing to settle. Set the slice weight for server-versus-server
priority; leave the panel field alone unless you know a slice has company.

The trap is nomenclature, not arithmetic: the panel's `io_weight` and this
project's `defaults.io_weight` share a name while meaning different scales at
different levels. `io_bfq_weight` is the one that says what it means.

## `memory_min_budget` and `budget_policy`

`memory_min_budget` is Wings' node-level ledger: the sum of `MemoryMin` across
all `wings-*` slices must stay within it. Set it equal to the `wings.slice` unit
file's `MemoryMin` — that is the number the kernel will actually honour (Rule 1).
Empty disables the check.

`budget_policy` decides what happens when a request would exceed the remainder.
All three apply every other property (`low`/`high`/`max`/weights) unchanged;
they differ only over `memory.min`:

| Policy | On overcommit | Use when |
|---|---|---|
| `clamp` (default) | The new floor is reduced to what remains, and the reduction is logged. | You sell per-server guarantees. Every granted floor stays literally true; a server that starts late gets less than its egg asks for — **permanently, however busy it is**. |
| `refuse` | The floor is dropped entirely (logged); the server runs with no floor. | A partial guarantee is worse than none — you would rather see the server unprotected than believe in a number that shrank. |
| `distribute` | The floor is applied as requested; the overcommit is logged, not corrected. Rule 5 takes over. | Servers co-operate rather than compete (e.g. two instances of one game, one operator). Nobody's floor is an individual guarantee any more — the **tier total** is — but protection follows live load instead of start order. |

Worked example — the case `clamp` gets wrong. Two instances of the same game on a
15.6Gi host, `wings.slice min=10G`, each server's egg asking
`WINGS_CG_MEMORY_MIN=6G`:

- **`clamp`:** A starts first and gets 6G. B starts and is clamped to the 4G
  remainder — logged, permanent. If B later becomes the busy one and A idles,
  B is still capped at a 4G guarantee while A sits on an unused 6G reservation.
  The split was frozen by start order.
- **`distribute`:** both get `min=6G`. Whichever server is actually resident
  keeps its memory; when both are hot the tier's 10G splits 5G/5G by usage
  (Rule 5). Nothing is stranded in an idle server.

The log line to expect under `distribute`:

```
INFO cgroups: per-server memory.min floors exceed the node budget; policy
     "distribute" applies them as requested and lets the kernel share the parent
     slice's protection proportionally to each server's usage
     slice=wings-<32hex>.slice requested_floor=6442450944 sibling_floors=6442450944 budget=10737418240
```

Keep `memory_min_budget` set even under `distribute`: the policy changes what
Wings does about overcommit, not whether it tells you. An unset budget means no
ledger and no log line — you lose the tripwire that says the node is now
oversubscribed.

## Sizing checklist

- `memory.min` — the working set that must survive host-wide pressure. Sum of
  all of them ≤ the node slice's `MemoryMin` (or accept Rule 5 via `distribute`).
- `memory.low` — soft protection above `min`, and only meaningful below the same
  cgroup's own `high` (Rule 3).
- `memory.high` — where this server starts getting squeezed into zswap. If it
  sits below the server's real resident set, you are paying reclaim continuously.
- `memory.max` — leave unset unless you want an OOM kill; above RAM it is inert.
- `cpu.weight` — settles contention **between siblings under the same parent**
  only, composes multiplicatively toward the root (Rule 6), and is honoured
  linearly: the ratio you write is the ratio you get.
- `io.weight` — same sibling scoping, but requires BFQ ([`SETUP.md`](SETUP.md)
  §1c) **and** is rescaled into BFQ's 1..1000 range, which compresses every
  ratio above the default by ~11× (Rule 7). Pick the number from the
  `io.bfq.weight` column, not the one that looks right.
