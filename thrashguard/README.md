# thrashguard

The actuator for "freeze" on a dev-tier host: a small Rust service that watches
the host's memory PSI and parks one registered unit at a time, so an
overcommitted host loses time to one job instead of all of them.

**Status: TG-1 P1 `observe` — the monitoring cut, complete.** What exists here
runs the tick loop (`thrashguard serve`), resolves a registered container to its
cgroup scope, journals what it *would* do, and reports it. **Nothing in this crate writes a cgroup file or sends
a signal**, and `tests/no_act_path.rs` fails if that ever changes. `mode =
enforce` is refused with one line until P2 (`act`) is reviewed, because an
untested freeze is exactly the kind of quiet wrong answer the estate's default
rules forbid.

The design is normative and lives outside this project:
[`docs/design/thrashguard/DESIGN.md`](../docs/design/thrashguard/DESIGN.md).

## What it does, in one paragraph

Dev-tier work is allowed to overcommit RAM on purpose, and the only signal that
says "stop adding work" is memory pressure. thrashguard reads the host's
`memory` PSI `full` row; when `full avg10` stays at or above the configured
trigger for the configured hold time it picks **one** unit from the set that
registered itself over its UNIX socket, and in P2 it would freeze that unit's
docker scope and reclaim memory. When pressure releases — or the park ages out,
or the guard stops, or the unit's owner deregisters — nothing is left frozen.
Every frozen interval is written to a journal and, in P4, published through
`hostobs` so a consumer can correct wall time without reading a host socket.

Each planned cut, and what it needs to be usable:

| Cut | Content | State |
|---|---|---|
| P1 `observe` | config, PSI poll, hysteresis, the tick loop, the two sockets, candidate store + container→scope resolution, `events.jsonl`, `mode=report`, CLI, static units | complete (this crate) |
| P2 `act` | freeze/thaw of one scope, reclaim, read-back, death sweep, `enforce`, the thaw queue | not built |
| P3 `victims` | `newest`, `refault`, `smallest-enough`, aging, invariants | not built |
| P4 `estate` | run-gate interim registrant, hostobs emission, `host-facts/2`, fleetd, agctl, mdt-cockpit deploy offer | not built |

## What it deliberately does not do

- **It never kills.** OOM stays with memcg and systemd-oomd. thrashguard parks
  work; it does not end it.
- **It never re-weights.** `cpu.weight`, `io.weight` and `memory.high` belong to
  `mdt-psi-guard`. The two guards read different counters and never wait for each
  other (design §2, L1).
- **It never scans `dev.slice`.** Only a registered unit, or an operator's
  `[[static_unit]]`, is a candidate: an unregistered cgroup cannot be ranked, and
  a scan would reach the cockpit.
- **It never trusts a client's path.** A registrant names a *container*; the guard
  resolves the cgroup scope itself under `allowed_ancestors` and refuses a
  registration that resolves to nothing, to several scopes, or to a protected
  ancestor.

## Sockets and groups

| Path | Owner | Purpose |
|---|---|---|
| `/run/thrashguard/registrant/thrashguard.sock` | `root:thrashguard-registrant` 0660 | register, update, heartbeat, deregister |
| `/run/thrashguard/reader/thrashguard.sock` | `root:thrashguard-reader` 0660 | status, candidates, events, version |

The units live in [`deploy/systemd/`](deploy/systemd) with the socket groups'
directories from [`deploy/tmpfiles.d/thrashguard.conf`](deploy/tmpfiles.d/thrashguard.conf);
[docs/CONSUMERS.md](docs/CONSUMERS.md) §2 installs them.

Both are systemd-owned `.socket` units with `Accept=no`, so a daemon restart does
not refuse a client. Group gating is **advisory** — container root bypasses socket
DAC — and the real boundary is the scope resolution above.

## CLI

```
thrashguard serve                 # the daemon: tick loop + both sockets
thrashguard status                # mode, PSI, candidates, next decision
thrashguard candidates            # the next tick's order and the victim it implies
thrashguard events [--since-seq N]
thrashguard version
```

The three reader verbs answer **locally** from `--config` when `--socket` is
absent, so a standalone host is complete with no daemon; with `--socket` they
ask a running one. `serve` adopts the two systemd descriptors when
`LISTEN_FDS`/`LISTEN_FDNAMES` are set and binds `--registrant-socket`/
`--reader-socket` only for a foreground run, `mode = off` exits 0 without
binding anything, and `mode = enforce` is refused with one line. Every verb
takes `--config`, `--cgroup-root` and `--pressure-path` (design §5 D9);
`freeze`/`thaw` are **P2** verbs and do not exist in this build.

See [docs/CONSUMERS.md](docs/CONSUMERS.md) for worked examples, and
[docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) for why the parts are shaped this
way.

## Build and test

This project is its own single-crate workspace with **no third-party
dependency**: the Rust gate image caches only `hostobs`'s registry set, so a new
crate needs an image rebuild and a stated reason. `tools/cargo-offline.sh` proves
the lock names no registry crate before it runs cargo offline and locked.

```bash
# through the remote dev tool (not a gate, and not a host actuator)
scripts/controller/dev -p thrashguard test
scripts/controller/dev -p thrashguard clippy
scripts/controller/dev -p thrashguard fmt

# the registered lane (gate evidence)
cd thrashguard && run-gate --worktree <tree> r0-r1
```

Tests never touch the real host: every cgroup read takes an injected
`--cgroup-root`, every pressure read an injected `--pressure-path`, both pointing
into a temp tree the test creates and removes.
