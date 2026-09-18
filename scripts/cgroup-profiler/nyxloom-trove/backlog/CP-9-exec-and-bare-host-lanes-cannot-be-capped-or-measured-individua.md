---
kind: backlog-entry
schema_version: 1
id: CP-9
title: "exec and bare-host lanes cannot be capped or measured individually -- the daemon observes a shared cgroup and has no leaf of its own to place a lane into"
status: fixed
type: "feature"
severity: "medium"
provenance: "RG-55 wave, RW-30/RW-35a (design A1/D-20/D-25) + RW-34 (contract v1.1 §8.3), 2026-09-12"
filed_date: "2026-09-12"
closed_date: "2026-09-12"
closed_reason: "placement landed (leaf under the gates slice, migrate/release, write guard), a654bd5d"
---

## Observed mechanism and reproduction

A daemon session names ONE cgroup (`_create_session_locked`'s `abs_target`)
and only ever reads it. For an *ephemeral* lane that is enough: docker made
a scope for the container at create time, so the cgroup the daemon samples
is the lane and nothing else, and `--memory` on the container is a real cap.
The estate's other two lane kinds have neither property:

* an **exec-mode** lane runs `docker exec` into a long-lived devcontainer.
  Its cgroup is the devcontainer's scope, shared with the IDE, every agent
  session and the caller that asked for the lane. `memory.peak` for that
  cgroup is the devcontainer's peak, `memory.pressure` is the
  devcontainer's pressure, and there is no per-lane number anywhere
  (RG-57's report says "devcontainer-wide" for exactly this reason). CP-8's
  token subtree fixes ATTRIBUTION of pids and CPU, but not the cgroup-level
  readings (`memory.peak`, `memory.pressure`, `io.stat`), which are per
  cgroup and cannot be split by pid after the fact.
* a **bare-host** lane has no container at all; its cgroup is whatever the
  caller happened to be in.

Nothing can be capped either. docker caps a container at create time and
the daemon deliberately owns no `--cap` flag (D-15: `serve` never imports
`lib.caps.TempCaps`), so a lane that names `resources.memory` in its own
declaration gets that number recorded and never enforced — the one number
a consumer most wants honoured is advisory. A lane that then runs away
takes the whole devcontainer's memory with it, and the only lever left is
SIGKILL after the damage (CP-8's `--on-stall kill`), never a throttle
before it.

## Why cgroup-profiler owns it

D-20: *lanes name what they need; the daemon places them.* The daemon is
the only process in the estate that runs `--pid=host --cgroupns=host`
against the host's cgroupfs, already resolves the lane's pid subtree on a
fixed cadence (§4.3), and outlives the consumer that asked (D-28). It can
therefore create a sibling leaf under the gates slice, move the lane's pids
into it as it discovers them, and read that leaf — which makes
`memory.peak`, `memory.pressure` and `io.stat` per-LANE for exec and
bare-host lanes, and makes `memory.high` a real throttle (CP-8's
`throttled` state becomes reachable at all only once a leaf exists).

*Why a sibling leaf and never a child of the container's own scope* (D-20's
own note): enabling a controller in the container's cgroup needs
`cgroup.subtree_control`, after which cgroup v2's "no internal processes"
rule forbids that scope from holding processes itself, and every later
`docker exec` into the devcontainer would fail with `EBUSY`. Moving a pid
OUT of a container's scope is safe: it keeps the container's pid and mount
namespaces (pid-1 death still kills it), only its accounting moves.

## Proposed contract

Contract v1.1 §8.3 (RW-34), with RW-35(a) and D-25 as the safety envelope:

1. `start` gains `--place` plus `--memory-high <bytes>`, `--memory-max
   <bytes>`, `--cpu-weight <1..10000>` (all ignored without `--place`).
2. `--place` creates `<gates slice>/rg-<token>` (the gates slice is
   `serve --gates-slice`, default `dev-gates.slice`), applies the caps,
   READS THEM BACK into `placement.applied` (never the requested numbers —
   the S13.3.2 discipline), and migrates every pid the token resolver
   discovers into the leaf's `cgroup.procs`, at start and on every later
   discovery tick.
3. Refusals never fail `start`: `place-refused:no-token`,
   `place-refused:no-gates-slice`, `place-refused:over-slice` (a
   `--memory-max` above the slice's own `memory.max`),
   `place-refused:parent-not-gates-slice` (D-25),
   `place-refused:write-failed:<file>` — each is `placement.error` with
   `leaf: null` and a session that started unplaced.
4. `stop` moves survivors back to the original scope's `cgroup.procs` and
   `rmdir`s the leaf (retried 3x over 3s); a leaf it cannot remove is
   reported, never left silently.
5. D-15's write boundary stays a WHITELIST, extended by exactly one
   non-leaf write (RW-35a): `+memory +cpu +pids` into the gates slice's
   `cgroup.subtree_control` (`+` only, never `-`), because a hand-created
   leaf cannot take `memory.high` unless its parent delegates the
   controller. Everything else writable is on `<gates slice>/rg-*`
   (`cgroup.procs`, `memory.high`, `memory.max`, `cpu.weight`,
   `cgroup.kill`, and `rmdir` of the leaf) plus the original scope's
   `cgroup.procs` for the move-back. Every other cgroup path is refused by
   the guard, and every cgroup write is an `events.jsonl` row (D-25).
6. `placement` (§8.3) is carried by `start`, `status`, `stop`, every
   `watch` reading and the Summary (§8.7); a placed session is killed by
   one write of `1` to the leaf's `cgroup.kill` (atomic — it cannot miss a
   pid that forked during the resolver's walk), which is what CP-8's
   enforcement path was built to delegate to.

Consumer side (run-gate, P5) is §8.9 obligation 3: placement is requested
for exec and bare-host lanes only; ephemeral lanes stay docker-capped.
