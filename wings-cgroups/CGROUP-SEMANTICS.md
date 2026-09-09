# cgroup-v2 memory semantics down a slice chain

Every value this project sets — node slice, per-server property, egg variable —
lands on a cgroup-v2 knob whose behaviour depends on the *chain* it sits in, not
on the value alone. This file is the reference for that behaviour: what each
knob means, how nesting changes it, and who arbitrates when the numbers don't
add up. Deployment steps are in [`SETUP.md`](SETUP.md); design rationale is in
[`STRATEGY.md`](STRATEGY.md).

Examples use the shape this project produces:

```
-.slice                                    (root)
└─ wings.slice                             the node tier — a real unit file
   ├─ wings-mgmt.slice                     Wings itself
   └─ docker-<id>.scope                    one per server — Docker-made; Wings
                                           sets its properties. Pages are
                                           charged here.
```

Since 2026-09-08 there is no per-server slice between the tier and the
container: Wings sets the per-server floors, ceilings and weights directly on
the container's own scope. Two levels, not three. Everything below still holds —
the rules are about the chain, and the chain just got shorter.

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
└─ docker-A.scope    min=6G      6G ≤ 8G → honoured in full ✓
```

Broken case, which reports no error anywhere:

```
wings.slice          min=8G
└─ docker-A.scope    min=10G     → effective 8G. The extra 2G is fiction.
```

This is why the node slice's `MemoryMin` must be ≥ the sum of every per-server
floor you intend to grant. Wings does not enforce that sum — it applies what it
was told and logs when the total exceeds the tier's live `MemoryMin`, nothing
more (see "Overcommit, and who resolves it" below).

## Rule 2 — limits are the minimum along the whole path

Ceilings don't distribute, they stack. The effective limit is the tightest value
between the cgroup and the root:

```
wings.slice          high=14G
└─ docker-A.scope    high=7G     → server throttles at 7G
```

A child limit looser than its parent's is legal and inert — `high=20G` on the
scope under a 14G tier still throttles at 14G. So is any limit above physical
RAM: `WINGS_CG_MEMORY_MAX=20G` on a 15.6Gi host can never be reached and does
nothing.

One consequence of the flat shape: the scope is now the *only* place below the
tier where a per-server limit can live, and it is also where Docker puts the
Panel's own memory limit. Two writers, one unit — the last one to set the
property wins. That is a change from the three-level shape, where a Wings value
on the slice and a Docker value on the scope composed by this rule instead of
overwriting.

Wings makes sure it is the last writer at every point where Docker writes:
just after `ContainerStart()`, when re-attaching to a container that outlived
the Wings process, and after the on-the-fly resource update a Panel-side
settings save triggers, whose `ContainerUpdate` otherwise lands the Panel's
`Memory`/`MemoryReservation`/`CpuShares`/`BlkioWeight` on the scope *after*
Wings' own set. Until 2026-09-09 that last one was missing, so a routine
settings save silently replaced `MemoryLow`, `MemoryMax`, `CPUWeight` and the
BFQ weight with the Panel's values — and a Panel memory limit is usually
*looser* than a deliberate `WINGS_CG_MEMORY_MAX`, so the ceiling did not just
move, it disappeared. Measured, then fixed (adversarial review 2026-09-09, F1).

Two things about that last re-assertion are load-bearing, and both were got
wrong on the first attempt (fix-verification 2026-09-09, V1):

- **It uses the band the server is actually in**, which is the *slice phase*
  (`server/slice_phase.go`), not the environment's process state. A server with
  an explicit `WINGS_CG_STEADY_MATCH` — which `SETUP.md` tells you to set for a
  world-streaming game, because the egg's "done" line fires before loading
  finishes — is deliberately still in its startup band for the whole remaining
  world load, long after Docker reports the container running. Re-asserting the
  *steady* band there drops the ceiling onto a server at its load-time peak,
  which is the eviction of Rule 4 below, self-inflicted. The re-assertion is
  therefore made from `Server.reassertSliceProps`, in the layer that holds the
  phase, and never from `environment/docker`.
- **It never writes `memory.high`.** Docker cannot have damaged it —
  `container.Resources` has no such field, so runc never sets it — and writing
  it is the one thing a repair could do harm with. Everything Docker's write
  *can* reach is re-asserted; the ceiling is left where the phase machinery put
  it.

So the rule to hold on to is not "whoever writes last wins" in the abstract:
**for the properties Wings manages, Wings' value is the one that persists,
because it re-applies on every path where the other writer acts.** What that
does *not* give you is composition — the two settings no longer stack, they
replace, and the Panel's value for a Wings-managed property is simply
overwritten a moment later.

## Rule 3 — a cgroup is never protected from itself

`min`/`low` only fend off reclaim driven from **outside** the cgroup. They are
ignored by reclaim that the cgroup's own `high`/`max` triggers:

```
docker-A.scope   min=6G  low=12G  high=7G     current=8.5G
```

At 8.5G the server is over its own `high`, so the kernel reclaims ~1.5G into
zswap continuously — `min=6G` does not object, because that pressure is
self-inflicted. What `min=6G` *does* mean: when the host is short on memory,
nothing else may take this server's first 6G.

Corollary: any `low` above the same cgroup's `high` is decorative — the cgroup
is never allowed to hold that much in the first place. `low=12G` with `high=7G`
behaves exactly like `low=7G`.

## Rule 4 — protection declared on a *parent* needs `memory_recursiveprot`

Pages are charged to the **leaf** (`docker-*.scope`), never to the slices above
it. Without the `memory_recursiveprot` mount flag, `wings.slice min=8G` protects
only pages charged directly to `wings.slice` — approximately none — and the
container inherits nothing. Such a floor becomes a no-op, silently.

With the flag, a parent's protection covers its whole subtree, and children
without their own `min` share it. This is why a node can be usefully protected
at the tier level alone: with `wings.slice min=8G` and no per-server values at
all, a server still gets the tier's protection — it just gets no *individual*
guarantee, no per-server `high`, and no weights. Check with
`grep cgroup2 /proc/mounts` ([`SETUP.md`](SETUP.md) §1b).

**Since 2026-09-08 this is no longer what makes or breaks a Wings-set floor.**
Wings writes `memory.min`/`memory.low` onto the container's own scope — the leaf
that owns the pages — so there is no level to recurse through and no mount flag
in the way. The flag stays a real prerequisite for two things, and they are not
small ones:

- the **node tier** itself. `wings.slice`'s `MemoryMin` is a parent protection,
  and it is now the *entire* aggregate guarantee, since Wings does no budget
  arithmetic. Strip the flag and the tier reservation stops covering anything.
- any **admin-managed slice** a server is pinned to via `WINGS_CGROUP_PARENT`.
  That path is untouched by the redesign: the floors live on the slice and the
  container is a scope beneath it, exactly as before.

The historical failure mode — a stripped flag silently voiding every per-server
floor on the node, with `systemctl show` still reporting the configured number —
is recorded in `TODO.md` and was the finding that motivated moving the
properties down to the scope in the first place.

## Rule 5 — overcommitted protection is distributed by usage, not by weight

**This is now the only thing that arbitrates overcommit.** Wings applies every
floor exactly as configured and corrects nothing; when the sum exceeds the
tier's protection, the mechanism below is what decides who actually gets it. No
code of ours is involved, and there is no configuration for it.

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
time each gets everything it actually touches. `memory.low` overcommits the same
way.

This behaviour is why the project stopped doing floor arithmetic at all: the
kernel already resolves the case, continuously and by live load, and any
Wings-side correction could only be worse — a floor shrunk at start time stays
shrunk however busy that server later becomes.

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
└─ docker-<server>.scope             cpu.weight=1000  → 1000/1200 = 83% of the tier = 39% of host
```

Add a second server at the node default `cpu_weight: 200` and the same tier
share is redivided — 1000/1400, 200/1400, 200/1400 — without touching anyone's
configuration. That redivision is the point: you tune the *ratio between
siblings*, and the tier's total is defended one level up.

## Rule 7 — `IOWeight` is rescaled for BFQ, and the scale is brutally compressive

This one is invisible and changes what your numbers mean. **The table below is
the reference you consult when picking a number by hand** — nothing in this
project converts for you, at either tier: not the `wings.slice` unit file's
`IOWeight=`, and, since patch 0005 was retired on 2026-09-08, not
`WINGS_CG_IO_WEIGHT` either. You state a systemd `IOWeight`; you read the
`io.bfq.weight` column to know what you actually bought.

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
already has, the unit needs `IOWeight=7800` (→ bfq 800). That is not a worked
hypothetical: it is the live value in this node's `wings.slice` unit file,
picked by hand from the table above for exactly that parity.

### Picking the number by hand — at both tiers

The arithmetic is the same wherever the property is written, so do it the same
way in both places:

- **node tier** — `IOWeight=` in the `wings.slice` unit file. Always was by
  hand.
- **per server** — `docker.per_server_slices.defaults.io_weight`, or
  `WINGS_CG_IO_WEIGHT` on the egg. Now also by hand.

The inverse, if you would rather start from the BFQ number you want:

```
  IOWeight  =  100 + 11 × (io.bfq.weight − 100)      for bfq > 100
```

so a real 5.4:1 (`io.bfq.weight` 540) is `IOWeight=4950`, and 8:1 (bfq 800) is
`IOWeight=7800`. There used to be a convenience knob that did this conversion
inside Wings — `io_bfq_weight` / `WINGS_CG_IO_BFQ_WEIGHT`, patch 0005. It was
retired on 2026-09-08 in favour of one spelling on systemd's own scale; see
[`patchstack/README.md`](v1-legacy/patchstack/README.md).

Why not write `io.bfq.weight` directly and skip the arithmetic? Because systemd
re-derives that file from `IOWeight` every time it re-applies the unit's IO
settings, silently clobbering a raw write — the same trap as Finding D, and the
reason this project never writes cgroupfs directly.

Check what the kernel actually holds rather than what you set:

```bash
cat /sys/fs/cgroup/<path>/io.bfq.weight    # what BFQ schedules on
cat /sys/fs/cgroup/<path>/io.weight        # what you set (iocost's file; inert without io.cost.*)
cat /sys/block/<dev>/queue/scheduler       # [bfq] or the weights do nothing at all
```

Under `none`/`mq-deadline`, neither file does anything: no proportional IO
control exists, and only `io.max` hard caps still bite.

### The panel's "Block IO Weight" is a second writer on the same file

Stock Wings already carries a per-server IO weight of its own — the panel field
that reaches Docker as `--blkio-weight`. It is easy to assume it is redundant
with the weights above, or that it suffers the same compression. The second is
not true. Measured on a cgroup-v2 + BFQ host, `docker run --blkio-weight 700`
produces, on the container's own scope:

```
io.bfq.weight = 700          # runc writes BFQ's file directly, uncompressed
io.weight     = 100          # untouched
```

So it lands on BFQ's own 10..1000 scale, on the **scope** — which, since the
per-server slice was retired, is the very cgroup `WINGS_CG_IO_WEIGHT` now
targets:

```
wings.slice                 IOWeight        share of the disk
└─ docker-….scope           io.bfq.weight   share of the tier
                                            ← WINGS_CG_IO_WEIGHT (via IOWeight,
                                              compressed) AND the panel's
                                              "Block IO Weight" (raw)
```

Before 2026-09-08 these were two levels apart and composed by Rule 6. They no
longer do: both write `io.bfq.weight` on the same unit, by different routes, and
the later write wins.

Do **not** assume systemd re-derives the file on its own. This document
previously claimed "systemd re-derives that file from `IOWeight` whenever it
re-applies the unit's IO settings, so the systemd-side value is the one that
survives". That was measured false (adversarial review 2026-09-09): after a
`docker update --blkio-weight 700` on a scope where Wings had set
`IOWeight=4950`, the kernel file held the panel's raw 700 while `systemctl show`
still reported `IOWeight=4950`. The audit surface reported a number that was no
longer real — the exact failure this redesign exists to remove.

The accurate statement is narrower and is what the code relies on: systemd
**does** derive `io.bfq.weight` from `IOWeight`, and re-derives it every time a
property is set on the unit — but only then. It never does so spontaneously, so
it does not undo a foreign write on its own. Measured on the project's e2e
harness (systemd 257, cgroup v2, BFQ):

| step | `systemctl show … IOWeight` | `io.bfq.weight` |
|---|---|---|
| container created `--blkio-weight 300` | *(not set)* | 300 |
| Wings sets `IOWeight=4950` | 4950 | **540** |
| `docker update --blkio-weight 700` | 4950 | **700** ← audit surface diverges |
| Wings re-asserts `IOWeight=4950` | 4950 | **540** ← repaired |
| Wings sets `IOWeight=100` | 100 | 100 |

What keeps the systemd-side value in force is therefore Wings re-applying it:
since 2026-09-09 a panel-side settings save is followed by a re-assertion of the
scope properties (`Server.reassertSliceProps`), so the last write on that file
is systemd's derivation from Wings' `IOWeight` again rather than the panel's raw
number. That is a repair, not composition.

Note what the repaired row does **not** say: `systemctl show` and
`io.bfq.weight` never agree numerically, because they are different scales —
4950 compressed is 540 raw (Rule 7's own arithmetic). "Repaired" means the file
is once again systemd's derivation from `IOWeight` instead of the panel's raw
value, not that the two numbers match. **Use one or the other.** If you set
`WINGS_CG_IO_WEIGHT`, leave the panel field at its default and read the result
back off `io.bfq.weight` rather than trusting either number.

The remaining trap is nomenclature: the panel's `io_weight` and this project's
`defaults.io_weight` share a name while meaning different scales — 10..1000 raw
BFQ versus 1..10000 systemd-compressed. With `io_bfq_weight` retired there is no
longer a spelling that disambiguates itself, so read the table above before
typing a number into either field.

## Overcommit, and who resolves it

Nothing in Wings. There is no floor ledger and no policy knob: `memory_min_budget`
and `budget_policy` (`clamp`/`refuse`/`distribute`) were removed on 2026-09-08
along with the per-server slice. Wings applies the `memory.min`/`memory.low` it
was configured with, as stated, and corrects nothing.

The tier's own `MemoryMin` on the `wings.slice` unit file is therefore the whole
aggregate guarantee, and Rule 5 is the whole arbitration: when the scopes' floors
sum above what the tier can back, each scope claims `min(usage, floor)` and the
kernel shares the tier's protection out in proportion to those claims,
continuously.

Worked example — two instances of the same game on a 15.6Gi host,
`wings.slice min=10G`, each server's egg asking `WINGS_CG_MEMORY_MIN=6G`: both
get `min=6G`. Whichever server is actually resident keeps its memory; when both
are hot the tier's 10G splits 5G/5G by usage. Nothing is stranded in an idle
server, and no server's floor is frozen by the order it happened to start in —
which is what a start-time clamp would have done, permanently.

What Wings still does is tell you. If the floors it currently has applied across
live scopes would sum above `wings.slice`'s own `MemoryMin` — read live from
systemd, not from configuration — it logs that the node is oversubscribed. One
informational line, no behaviour attached:

```
INFO cgroups: per-server memory.min floors exceed the node slice's MemoryMin;
     applied as requested — the kernel shares the parent's protection in
     proportion to each server's usage
     scope=docker-<id>.scope applied_floors=… node_memory_min=10737418240
```

Read it as a tripwire on the tier's sizing, not as a warning about the server
that happened to trigger it. The fix, when you want one, is on the `wings.slice`
unit file.

## Sizing checklist

- `memory.min` — the working set that must survive host-wide pressure. Sum of
  all of them ≤ the node slice's `MemoryMin`, or accept Rule 5's proportional
  split.
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
