# `srdm` — shared-ramdisk-depot-manager

One host service that turns game (or any) content into **immutable,
hash-verified generations**, holds them resident in a shared tmpfs so N
containers share one copy of the pages instead of N page-cache copies, and
exposes them read-only to the containers that consume them.

- **Design**: [`../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md`](../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md)
  — the master plan. Read §Exposure drivers, §Publication topology,
  §Generation slices, §Worker contract, §Acceptance oracles.
- **Resources companion**: [`../wings-cgroups/shared-ramdisk-update-lifecycle-cgroups-2-fable.md`](../wings-cgroups/shared-ramdisk-update-lifecycle-cgroups-2-fable.md)
- **Wings patches**: [`../wings-patchstack/`](../wings-patchstack/)
- **Operating guide** (gates, cgroup placement, what not to touch):
  [`nyxloom-trove/GUIDE.md`](nyxloom-trove/GUIDE.md)
- **Store format**: [`docs/store-format.md`](docs/store-format.md)

## Status — P01 landed

The release store, the journal and the offline half of `doctor` are
implemented and gated. The rest of Phase 1 is carved but not started; see
[`nyxloom-trove/roadmap.md`](nyxloom-trove/roadmap.md).

| | |
|---|---|
| **Works now** | transactional release store, per-file SHA-256 manifests, profile classification and probes, crash recovery, journal (durable records + JSONL + journald), `doctor` offline subset, CLI |
| **Not yet** | publication topology and hold services (P02), the `host-bind` exposure driver (P03), `harvest` (P04), retention/GC and the daemon (P05), SteamCMD driver, everything `provider` (v2) |

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
(Finding A, `../wings-cgroups/STRATEGY.md`). `srdm.slice` sits at cgroup root;
`srdm-gen-<g8>.slice` and `srdm-hold-<g8>-<class>.service` nest correctly
beneath it. `config.Validate` refuses a hyphenated slice name for this reason.

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
│   ├── journal/              durable records, JSONL, journald
│   ├── doctor/               diagnostics; offline subset            ← P01
│   ├── fsx/                  durability primitives (atomic write, fsync)
│   ├── publish/              publication topology                     P02
│   ├── expose/               exposure drivers                         P03
│   ├── source/steam/         SteamCMD driver (off the MVP path)
│   ├── providerapi/          v2 only
│   └── adminapi/             the operator socket
├── gate/                     srdm's own gate container (unit + e2e)
├── tools/                    gate runner, canaries, cgroup verifier
├── systemd/                  srdm.slice — a REFERENCE unit, not installed
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
