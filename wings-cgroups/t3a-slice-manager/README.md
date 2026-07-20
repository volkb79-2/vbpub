# wings-slice-manager (T3a)

> **Status: fallback / external option.** The in-Wings variant (T3b, patch
> 0004 `docker.per_server_slices`) shipped and is the deployed architecture.
> This daemon is what you reach for when a node's Wings stays at T2 — a build
> not carrying 0004, a policy against giving Wings D-Bus access, or a host
> where the properties axis must live outside the fork. On a 0004 node you do
> **not** want both: two writers on the same slice will fight.

Standalone host daemon that automates the **properties axis** for per-server
Wings slices while keeping the Wings fork frozen at T2 size. It watches Docker,
reads the admin-only `WINGS_CG_*` metadata that the T2 egg variables deliver
into each container's environment, and creates/reconciles the matching
transient `wings-<uuid>.slice` units via the systemd D-Bus API — the
daemon-reload-safe channel (systemd re-applies transient-unit properties;
raw cgroupfs writes get wiped — Finding D in the companion proposal).

Replaces the T0c `set-property` reconciler: same job, but slice-scoped,
event-driven, budget-aware, and with garbage collection.

## How it fits (division of responsibility)

- **Patched Wings (T1/T2)** does *placement only*: `HostConfig.CgroupParent`
  from `docker.cgroup_parent` / `WINGS_CGROUP_PARENT`.
- **This daemon** does *properties only*: floors/ceilings/weights on the
  per-server slices those containers land in.
- **The admin** owns the parent `wings.slice` unit file and its `MemoryMin`
  (the floor budget the children draw from).

## Spec transport (set as admin-only egg/server variables)

| Variable | Meaning |
|---|---|
| `WINGS_CGROUP_PARENT` | placement (consumed by patched Wings; this daemon only checks it against the actual placement) |
| `WINGS_CG_MEMORY_MIN` / `_LOW` / `_HIGH` / `_MAX` | sizes: `6G`, `512M`, `1024K`, or bytes |
| `WINGS_CG_CPU_WEIGHT` / `WINGS_CG_IO_WEIGHT` | 1..10000 |
| `WINGS_CGROUP_JSON` | all-in-one blob, e.g. `{"memory_min":"6G","cpu_weight":800}`; discrete vars win |

These ride in the container environment, so the game process can read them:
**non-secret metadata only** — and they are requests, not authority; the
daemon validates everything.

**Divergence from patch 0004 — the two paths are no longer feature-identical.**
This daemon does not implement `io_bfq_weight` / `WINGS_CG_IO_BFQ_WEIGHT`
(patch 0005's BFQ-native 1..1000 scale; here you must pre-compress by hand
through Rule 7's formula and set `IOWeight` to the systemd-scale number), and
its `budget_policy` has only `clamp` and `refuse` — 0004 adds `distribute`.
Anything written against those two features has to run on a 0004 node.

## Safety rails

- **Namespace guard (hard rule):** only units matching
  `wings-<alnum>[alnum_.-]*.slice` are ever created, modified, or stopped.
  Never the parent slice, never `system.slice`, never anything else — even if
  a container asks. Root-equivalent D-Bus power stays confined to this
  auditable daemon instead of being added to Wings.
- **Floor budget:** `memory_min_budget` caps Σ child `MemoryMin`
  (`clamp` or `refuse` policy, deterministic by slice name). Every reconcile
  also warns when the parent slice's `MemoryMin` is below the child sum —
  floors are zero-sum; an unbacked floor is a silently oversold guarantee.
- **GC with grace:** slices whose containers are gone (running *or* stopped
  containers keep a slice alive) are stopped only after `gc_grace`.
- **`dry_run: true`** logs every action instead of performing it — use it for
  the first rollout.

## Known race (accepted)

A freshly created container can run for a second or two before its slice has
properties: Docker/systemd create the slice at placement time (limit-less),
and this daemon applies properties on the create/start event (debounced 2s)
or at the latest on the next periodic reconcile. For game servers with
multi-second boots this window is cosmetic. It is inherent to the external
position: only the process that creates the container can have the slice ready
first. Patch 0004 (T3b) closes it by ensuring the slice *before* the create
call — one more reason it, not this daemon, is the default path.

## Run

```bash
make static && sudo make install       # binary + unit + config
sudo systemctl enable --now wings-slice-manager
```

Containerized (not recommended; the host service is simpler):

```bash
docker build -t wings-slice-manager .
docker run -d --name wings-slice-manager \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /run/dbus/system_bus_socket:/run/dbus/system_bus_socket \
  wings-slice-manager
```

## Test

```bash
make test vet                 # pure unit tests, no docker/systemd needed
make integration              # REAL systemd+docker (run in ../test/e2e-systemd/)
```
