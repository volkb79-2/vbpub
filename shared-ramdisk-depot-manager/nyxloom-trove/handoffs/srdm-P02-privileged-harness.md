---
schema_version: 1
id: srdm-P02-privileged-harness
project: srdm
title: "Privileged systemd harness + the hold-unit charging probe"
tier: sonnet5-high
input_revision: "c93c0d57"
depends_on: [D-003]
session: fresh
source: {kind: roadmap, ref: nyxloom-trove/roadmap.md}
scope:
  touch:
    - "gate/Dockerfile"
    - "tools/gate.sh"
    - "internal/cgroupfs/**"
    - "internal/systemdx/**"
    - "nyxloom-trove/decisions.md"
  forbid:
    - "internal/store"
    - "internal/journal"
    - "cmd/srdm"
oracles:
  - id: O6
    observable: "A Type=oneshot RemainAfterExit=yes transient unit whose ExecStart faults N bytes into a tmpfs and exits is still ActiveState=active afterwards, its cgroup still exists, and that cgroup's memory.stat shmem accounts for ~N bytes."
    negative: "The cgroup is reaped when ExecStart exits, or the pages are charged somewhere else — either of which invalidates the populate-and-hold-are-one-unit design."
    gate: privileged-e2e
  - id: O7
    observable: "Unmounting the tmpfs drops the hold unit's charge to ~0 and leaves cgroup.stat nr_dying_descendants unchanged."
    negative: "Memory stays charged after the unmount with nothing holding it, or dying descendants accumulate — a teardown leak."
    gate: privileged-e2e
  - id: O8
    observable: "With another process holding an open fd on a file in the tmpfs, unmounting does NOT drop the charge; it drops only once that fd closes."
    negative: "The charge drops while a reference is still held, which would make teardown look safe when it is not."
    gate: privileged-e2e
  - id: O9
    observable: "Declared unit properties (MemoryMin, MemoryZSwapMax) read back from systemctl show on the live unit."
    negative: "A property is silently ignored, so a class floor that was never applied reads as configured."
    gate: privileged-e2e
gates: [unit, privileged-e2e, canary]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
  - "privileged containers are refused by this Docker daemon"
---

# srdm-P02 — privileged systemd harness + the hold-unit charging probe

## Why this is first, and not publication topology

Every load-bearing claim in the publication layer is a claim about the
kernel and systemd, not about Go. The master plan states the central one as
an **open branch** rather than a fact:

> **Privileged e2e oracle, not an assumption** (review): after the worker
> exits, the unit is active, `memory.current` of its cgroup ≈ class size,
> and the properties read back; **if any systemd version fails to keep an
> active-but-empty service's cgroup alive, the fallback is an explicit
> minimal hold process — the oracle decides, the spec allows both.**

Writing the hold layer before running that probe means guessing and then
discovering. This package runs it.

It also closes **D-004**: `privileged-e2e` is currently declared with an
empty case set, which is a gate that cannot fail.

## Context to read first

Paths below are from the **vbpub repo root**, one level above this project.

1. `shared-ramdisk-depot-manager/nyxloom-trove/roadmap.md` — the wave plan
   and where this sits.
2. `wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md`,
   **§Generation slices and charging** only. That section is the contract;
   the rest is later phases.
3. `wings-cgroups/v1-legacy/test/e2e-systemd/` — its `Dockerfile` and
   `run-e2e.sh` are a **working** systemd-in-Docker recipe on this host.
   Steal the mechanics: `--privileged --cgroupns=private`, tmpfs on `/run`,
   `/run/lock` and `/tmp`, `CMD ["/sbin/init"]`, `STOPSIGNAL SIGRTMIN+3`,
   the `systemctl is-system-running` readiness loop, and the
   privileged-refused SKIP. **Read only — that whole tree is off limits to
   this project, as stated in `nyxloom-trove/GUIDE.md`.**
4. `shared-ramdisk-depot-manager/nyxloom-trove/GUIDE.md` — gate invocation
   and cgroup placement.
5. `nyxloom/reference/AUTHORING.md` §3b — the test anti-patterns, which
   apply in full.

The `scope.forbid` list above names only in-project paths, because that is
what nyxloom resolves it against. The out-of-project prohibition —
`wings-cgroups/`, `wings-patchstack/`, `scripts/gstammtisch-guide/`,
`tester-unified/` — is repo doctrine and lives in `GUIDE.md`; it applies
here in full.

## Work

1. **`gate/Dockerfile`, `e2e` target.** Add `systemd-sysv` and `dbus`, trim
   the units that make no sense in a container, `CMD ["/sbin/init"]`. It
   already inherits the Go toolchain from the `unit` stage, so the suite
   compiles and runs inside.
2. **`tools/gate.sh`, `e2e` path.** Boot detached, wait for
   `systemctl is-system-running` to report `running` or `degraded`, then
   `docker exec` the suite. **Read the verdict in a step separate from the
   run** — the exec writes its status to a file and a second exec reads it —
   so a truncating transport cannot forge a pass.
3. **`internal/cgroupfs`.** Read a cgroup v2 attribute set: `memory.current`,
   `memory.stat` (`shmem` is the number that matters — tmpfs pages are
   shmem), `cgroup.stat`'s `nr_dying_descendants`, and existence. Root is
   injectable so the parsers are unit-tested against fixtures.
4. **`internal/systemdx`.** Start and inspect transient units. The command
   runner is injected, so argv construction and output parsing are unit
   -tested without systemd.
5. **The e2e suite** behind a `//go:build e2e` tag: O6–O9.

**Out of scope** — do not start: the publication topology itself, per-class
sizing, the RO bind, exposure drivers, retention.

## Oracles

Paste of `nyxloom/reference/AUTHORING.md` §3b applies. For this package in
particular:

- **No wall-clock deadline may decide a verdict.** Waiting for a unit to
  reach `SubState=exited` needs a poll; make its budget generous (60s) and
  treat expiry as a real failure ("the unit never reached exited"), never as
  a tuning knob. If shrinking it could flip a result, it is an oracle and
  must be replaced by a synchronization point.
- **No process-global state left mutated.** Every transient unit is stopped
  and `reset-failed` in a `t.Cleanup`, and every tmpfs is unmounted, even on
  failure — this suite runs as root in a shared container.
- **Fresh temp root per test**, and a unit name unique per test, so two
  cases cannot collide on a leftover unit from a previous run.
- **No hollow tests.** Asserting "systemd-run exited 0" proves nothing; the
  assertions are on `memory.stat`, unit state, and property read-back.

O6, O7, O8, O9 as in the frontmatter.

## Gate

`tools/gate.sh <worktree> e2e`, i.e. the `privileged-e2e` gate id. `unit`
and `canary` must stay green. Placement is verified by
`tools/cgroup-parent.sh` as for every other srdm container.

If this Docker daemon refuses privileged containers, the harness **SKIPs
with exit 0 and says so** — degrading loudly is right, but a host that
cannot run the harness is not a failing change.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a
forbidden file, **STOP** — write `BLOCKED: <reason>` to the LOG, commit, and
exit. Do not improvise a workaround. A BLOCKED exit is a success mode.

Product gaps are **decisions**: file a `D-<NNN>` and keep working.

## Decisions this package is expected to produce

- Whether an active-but-empty `RemainAfterExit=yes` unit holds its charge on
  this systemd — and if not, the minimal-hold-process fallback the spec
  already permits.
- How srdm drives systemd: the `systemd-run`/`systemctl` CLI (zero
  dependencies) versus the D-Bus API the master plan's privilege table
  names.
