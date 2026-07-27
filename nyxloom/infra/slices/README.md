# nyxloom slice templates

Templates for **D-G1** of `docs/plan-resource-governance.md`. They are
templates, not shipped config: several values must be measured on the target
host before they mean anything, and are deliberately left commented out rather
than guessed.

## The hierarchy is the naming

systemd derives nesting from dash-separated names. `nyxloom-gates.slice` is
automatically a child of `nyxloom.slice`; `nyxloom-gates-P71.slice` is a child
of `nyxloom-gates.slice`. No parent declarations, no ordering directives — four
files give the whole tree, and per-instance leaves are created on demand by
`docker --cgroup-parent=nyxloom-gates-<task>.slice`.

```
nyxloom.slice              whole-factory ceiling
├── nyxloom-daemon.slice   MemoryMin — protected, survives sibling thrash
├── nyxloom-agents.slice   collective cap; leaf per session
└── nyxloom-gates.slice    batch, lowest weight; leaf per gate run
```

## Install (operator, needs root)

nyxloom **cannot** do this itself, by design. Setting cgroup values on a
container it starts is a Docker API call the daemon already has rights for;
writing `/etc/systemd/system` and reloading systemd needs host root, which the
daemon does not and must not have.

```bash
sudo cp nyxloom*.slice /etc/systemd/system/
sudo systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/nyxloom.slice   # optional sanity check
```

Never auto-install from the daemon: besides the root boundary, it would clobber
the runtime IO caps `setup-cgroups.sh` applies from measured
`io-baseline.env` values.

## ⚠️ A missing slice fails OPEN

Verified 2026-07-27:

```bash
docker run --rm --cgroup-parent=nyxloom-does-not-exist.slice alpine true
# → accepted silently; systemd creates a transient slice with NO limits
```

A typo in a slice name does not error — it removes **every** limit. So this is
not a cosmetic check: `nyxloom doctor` must assert each configured slice exists
as a real unit and **fail closed** when it does not. It is the same "error path
aliasing a benign result" shape as canonical **L18**, one layer down in the
infrastructure.

## Values you must supply

| File | Setting | How to derive |
|---|---|---|
| `nyxloom.slice` | `MemoryMax` / `MemoryHigh` / `MemorySwapMax` | total RAM − prod (soulmask) − interactive headroom |
| `nyxloom-daemon.slice` | `MemoryMin` | **measure**: daemon idle, then under a real dispatch wave; read `memory.current` + `memory.stat` anon at both |
| `nyxloom-agents.slice` | `MemoryHigh` / `MemoryMax` | aggregate across concurrent sessions (`policy.max_active_tasks`) |
| `nyxloom-gates.slice` | memory + `IO*IOPSMax` | cap RAM, allow generous swap, IOPS at 60–80% of measured device ceilings from `io-baseline.env` |

Absolute numbers are **host-owned and live here**, never in project config
(D-G4). A project moving hosts then needs no change — which is already how the
estate works: dstdns's ciu governance sets a per-container `mem_limit` as a
first-pass ceiling, and its own comment records that `besteffort.slice`'s
`MemoryMax` is "the real host-safety backstop regardless of any per-container
value".

## Weight reference on this host

`interactive.slice` **200** · `dev-workloads.slice` **50** ·
`besteffort.slice` **20**. Weights bind **only under contention** — on an idle
host a weight-1 cgroup still reaches 100% of every core. Setting agent and gate
weights low therefore costs nothing in idle throughput; it only decides who
yields when things collide.

One measured caveat, recorded in `nyxloom-gates.slice`: starving a gate does
not merely slow it, it **manufactures false reds**. At CPUWeight 20 the suite
produced two novel timing failures that vanished at 50. Those are real test
defects — a correct test asserts on causality, not elapsed time — but until
they are fixed, weighting gates too aggressively costs more in false failures
than it saves in CPU.
