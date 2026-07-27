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

## Starvation does NOT justify raising a weight (RETRACTION, 2026-07-27)

This file previously claimed that starving a gate "manufactures **false reds**",
citing two timing failures at CPUWeight 20 that vanished at 50, and concluded
that gates should not be weighted too low. **That claim is retracted.** It was
wrong in its framing, unsound in its evidence, and is contradicted by
measurement.

**1. The framing was backwards.** A test that fails when the machine is slow is
a **TRUE red**. The race was always in the test; slow hardware only *revealed*
it. Calling such a failure "false" is actively harmful — it teaches an operator
to dismiss a genuine defect and to "fix" it with hardware. **Hardware speed must
never determine the outcome of a test.**

**2. The evidence could not support the conclusion** — and this file said so
itself, two paragraphs up: *"Weights bind only under contention — on an idle host
a weight-1 cgroup still reaches 100% of every core."* By that same mechanism,
changing 20 → 50 cannot change CPU availability unless the host was contended,
in which case the real variable was the **other load**, not the weight. The
experiment was uncontrolled; the observation ("two tests failed that time") may
well have been real, but the attribution to CPUWeight was never falsifiable.

**3. Measured refutation (2026-07-27).** Re-run with a **hard quota**
(`docker run --cpus=N`), which binds whether the host is idle or busy — the
controlled knob a weight is not:

| Condition | Result |
|---|---|
| full CPU, `CPUWeight=25` + this slice's IO caps | green |
| `--cpus=2` — 25% of an 8-core host, 4 xdist workers on 2 cores | green (`PYTEST_EXIT:0`) |
| `--cpus=1` — 12.5% of host, 4 xdist workers on 1 core | green (`PYTEST_EXIT:0`) |

The suite passes at **8× starvation**. There is no measured cost to weighting
gates low, so weight them for the host's priorities, not for the test suite's
comfort.

**The rule.** If a test fails under starvation, **fix the test** — assert on
causality, not on elapsed wall-clock: wait on a real synchronization point
(`join()`, an event), or eliminate the wait entirely by calling an extracted
pure step function directly from the main thread. topos set the precedent
(`topos/nyxloom-trove/reports/P96-SELFREVIEW.md`: a wall-clock failure "fixed
with deterministic oracle"). **Never raise a cgroup weight to make a test pass**
— that hides the defect and leaves it to fire on slower hardware, under load, or
in CI.

**Known latent debt (not currently failing):** 28 fixed-deadline sites
(`deadline = time.monotonic() + N` followed by an assertion) across 8 test
files — `test_daemon` 11, `test_wrapper` 8, `test_crash` 3, `test_carver` 2, and
one each in `test_behavioral`/`test_config_ui`/`test_intake_ui`/`test_integration`.
A fixed budget is a proxy for "eventually" and is hardware-dependent by
construction. They pass at 1 core today; convert them to deterministic oracles
opportunistically, and never "fix" one by enlarging its budget.
