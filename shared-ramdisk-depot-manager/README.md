# `srdm` — shared-ramdisk-depot-manager

One host service that turns game (or any) content into **immutable,
hash-verified generations**, holds them resident in a shared tmpfs so N
containers share one copy of the pages instead of N page-cache copies, and
exposes them read-only to the containers that consume them.

- **The plan** — authoritative, and self-contained:
  [`nyxloom-trove/PLAN.md`](nyxloom-trove/PLAN.md). Start here. It carries the
  product definition, the invariants, the architecture as built, **the
  measured ground** (every place the kernel corrected the design), the
  direction, and the acceptance oracles.
- **Decisions**: [`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) —
  every `D-NNN`. **Roadmap**: [`nyxloom-trove/roadmap.md`](nyxloom-trove/roadmap.md).
- **Wings patches**: [`../wings-patchstack/`](../wings-patchstack/)
- **Historical**: `../wings-cgroups/` — the superseded master plan and its
  companions. Read-only history; PLAN.md §Historical documents says what each
  is still good for.
- **Operating guide** (gates, cgroup placement, what not to touch):
  [`nyxloom-trove/GUIDE.md`](nyxloom-trove/GUIDE.md)
- **Store format**: [`docs/store-format.md`](docs/store-format.md)

## What it actually does

The problem: N game servers on one host each want their own copy of a
multi-GB content tree. That means N copies in page cache, N independent
updates, and no way to say "these two servers are running the same bytes".

srdm makes the content a **release** — hashed per file, classified, probed,
frozen — and then a **generation**: that release resident in tmpfs, held by
per-class systemd units that carry its memory policy, sealed read-only, and
bind-mounted into each consuming server's volume. One copy of the pages,
shared, provably identical to the release, and impossible for a server to
corrupt.

```
content on disk
  → srdm store promote    hash every file, classify, probe → an immutable release
  → srdm activate         publish into tmpfs, hold it, expose it to every assigned server
  → [servers run against the shared read-only content]
  → srdm attach/detach    add or remove a consumer
  → srdm harvest          fold an in-place update back into a NEW release
  → srdm rollback         swap back to the previous one
  → srdm gc               drop releases nothing points at
```

Three ideas carry the design:

- **Intent and reality are separate, and reconciliation is the comparison.**
  What an operator asked for is recorded (`internal/assign`); who is actually
  holding a generation is a kernel fact resolved fresh from
  `/proc/*/mountinfo` every time it is asked (`internal/consumer`). Neither
  is derived from the other — see D-018 and D-024.
- **The charge and the policy cannot separate.** Each class's pages are
  faulted by that class's own hold-unit worker, *inside* the cgroup carrying
  that class's `memory.min` / zswap policy. Not applied afterwards to pages
  charged somewhere else.
- **Nothing becomes visible until it is whole.** Population happens in an
  operation-private tmpfs; a generation appears only as a read-only bind of
  an already-verified, already-sealed tree. Nothing is renamed into
  visibility and nothing visible was ever writable.

Everything that changes state is one root process under an `flock` — **there
is no daemon** (D-025). `srdm-restore.service` rebuilds every assignment at
boot; `srdm reconcile` repairs drift on demand.

## Status — P01–P08b landed, v1 MVP complete

The v1 pipeline is a loop and it survives everything short of losing the
disk: store → verified release → publication → hold units → exposure into a
Wings server's volume → update in place → **harvest back into a release** →
activate, and now boot restore and reconciliation-repair as well. See
[`nyxloom-trove/roadmap.md`](nyxloom-trove/roadmap.md); next on the critical
path is P09 (Soulmask profile, migration rehearsal), not v2.

| | |
|---|---|
| **Works now** | everything below, plus: `srdm-restore.service` republishes and re-exposes every assignment at boot with no operator present; `srdm reconcile` / `doctor --repair` resolve crashed operations, remount drifted binds read-only, and clear broken generations nothing is assigned to |
| **Also works** | transactional release store, per-file SHA-256 manifests, profile classification and probes, crash recovery, journal (durable records + JSONL + journald), `doctor`; publication topology (op tmpfs → hold unit → read-only bind), per-class hold units carrying the class memory policy, consumer resolution and teardown that refuses while anything holds, the `host-bind` exposure driver with `ro`/`rw`, `harvest`, and the operator surface: assignments, `activate`/`rollback`, `attach`/`detach`, retention/`gc`, `status` |
| **Not yet (v2, deliberately after v1 proves itself)** | SteamCMD driver, everything `provider`-exposure (needs the socket D-025 confirmed v1 does not have) |

```
srdm store promote --profile examples/soulmask.profile.json \
                   --release rel-2026w31 --from /path/to/content
srdm activate --profile examples/soulmask.profile.json --release rel-2026w31
srdm attach   --profile examples/soulmask.profile.json --server <uuid> --access ro
srdm status
srdm reconcile
srdm rollback --profile examples/soulmask.profile.json
srdm gc       --profile examples/soulmask.profile.json --dry-run
```

Every operation is one root process under an `flock` on `/run/srdm/srdm.lock`
— there is no daemon in v1, and D-025 says why. `srdm-restore.service`
(`systemd/srdm-restore.service`, a reference unit like `srdm.slice` — see
D-003) is what makes state survive a reboot; it is the one thing srdm runs
on itself with no operator watching.

`access: rw` needs `wings.write_owner` — the uid:gid Wings runs its server
containers as (`system.user.uid` / `system.user.gid` in its config). srdm
unseals the class trees and hands them to that owner, because publication
seals them read-only for everyone and an updater running as anything but root
would otherwise report success and write nothing. Undeclared, `rw` is refused
(D-022). A generation written through is repaired by republishing it, or kept
by harvesting it.

```
srdm store promote  --profile examples/soulmask.profile.json \
                    --release rel-2026w31 --from /path/to/content --channel stable
srdm store verify
srdm store recover
srdm doctor --profile examples/soulmask.profile.json
```

`srdm` is a single static Go binary with no third-party dependencies.

## Naming (decision 9)

`shared-ramdisk-depot-manager` is the product name and this directory.
**`srdm` is every identifier**: binary, CLI, provider id, slice root, unit
prefix, `/run/srdm`, `/var/lib/srdm`.

The single token is required, not cosmetic. systemd's `-` is the slice
hierarchy separator, so `shared-ramdisk-depot-manager.slice` would nest under
auto-created `shared.slice` / `shared-ramdisk.slice` / … each with
`MemoryMin=0`, and every class floor beneath it would be arithmetically dead
(Finding A, `../wings-cgroups/STRATEGY.md`). `srdm.slice` sits at cgroup root
and `config.Validate` refuses a hyphenated slice name for this reason.

The same rule decides the per-generation aggregate, and it is **not** the
`srdm-gen-<g8>.slice` the master plan draws: that name nests under an
auto-created `srdm-gen.slice` carrying `memory.min=0`, which kills every class
floor beneath it exactly as a hyphenated root would. Measured, and corrected
to `srdm-<g8>.slice` — one level under `srdm.slice`, nothing interposed
(decision D-015). Service names are unaffected, since only slices nest, so
`srdm-hold-<g8>-<class>.service` keeps the plan's shape.

## The one architectural idea: exposure drivers (decision 10)

```
store → transaction → verified immutable release
      → publication (op tmpfs → hold services → verify → RO bind)
      → EXPOSURE DRIVER                    ← the only fork
           ├─ host-bind  (stock Wings)   bind into the volume path      ← v1
           └─ provider   (L1 + L1b)      Docker mounts + leases          ← v2
```

**v1 requires no Wings patch.** That is deliberate: adoption must not be
hostage to upstream review, and it means the risky half of the program (a
transactional content store, tmpfs publication, cgroup charging, teardown
correctness) is proven in production before any patch-review risk is taken.

| Exposure | Wings patches required |
|---|---|
| `host-bind`, `access: rw` | none — stock Wings |
| `host-bind`, `access: ro` | **F1** (or node sets `system.check_permissions_on_boot: false`) |
| `provider` | L1 + L1b |

## Layout

```
shared-ramdisk-depot-manager/
├── cmd/srdm/                 daemon + CLI (one binary)
├── internal/
│   ├── config/               on-disk layout, ownership policy
│   ├── profile/              classification, probes, class memory policy
│   ├── store/                the transactional release store        ← P01
│   ├── journal/              durable records, JSONL, journald      ← P01
│   ├── doctor/               diagnostics; drift now acted on, not ← P01,
│   │                         only reported                          P08b
│   ├── fsx/                  durability primitives (atomic write, fsync)
│   ├── cgroupfs/             cgroup v2 attribute reader            ← P02
│   ├── systemdx/             transient units, via the systemd CLI  ← P02
│   ├── mountinfo/            /proc/*/mountinfo, with propagation   ← P03
│   ├── publish/              publication topology, reconciliation ← P03,
│   │                         and repair                              P08b
│   ├── hold/                 hold units, class policy, the worker  ← P04
│   ├── consumer/             who is still holding a generation     ← P05
│   ├── expose/               exposure drivers: host-bind, ro|rw    ← P06
│   ├── wings/                the node's propagation and chown walk ← P06
│   ├── harvest/               adopt an in-place update as a release ← P07
│   ├── assign/                declared intent: release + servers    ← P08
│   ├── opctl/                 activate/rollback/attach/…, restore  ← P08,
│   │                         and reconciliation                     P08b
│   ├── source/steam/         SteamCMD driver (off the MVP path)
│   ├── providerapi/          v2 only
│   └── adminapi/             the operator socket
├── gate/                     srdm's own gate container (unit + e2e)
├── tools/                    gate runner, canaries, cgroup verifier
├── systemd/                  srdm.slice, srdm-restore.service — REFERENCE
│                              units, not installed                    ← P08b
├── examples/                 a working profile document
├── docs/
└── nyxloom-trove/            handoffs, decisions, roadmap, guide
```

## Testing

```bash
tools/gate.sh          # build, vet, and the O1–O5 oracles, in srdm-gate
tools/canary-run.sh    # prove each oracle REJECTS a break of its contract
```

Never the devcontainer — and in srdm's case the devcontainer could not
pretend, having no Go toolchain. Both scripts refuse to launch until the
host cgroup tier is *verified*, because a slice name systemd does not know
fails open into an unlimited transient slice rather than erroring. See
[`nyxloom-trove/GUIDE.md`](nyxloom-trove/GUIDE.md).

## Why not inside `wings-patchstack/`

The design insists `srdm` ships on its own cadence and that its slices are
ordinary host units. v1 has **no Wings dependency at all**. Nesting it under
the patch stack would structurally contradict the claim the whole reshape
rests on.
