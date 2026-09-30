---
kind: backlog-entry
schema_version: 1
id: CP-9
title: "exec and bare-host lanes gain identity-scoped placement and resource attribution"
status: fixed
type: "feature"
severity: "medium"
provenance: "RG-55 wave, RW-30/RW-35a (design A1/D-20/D-25) + RW-34 (contract v1.1 §8.3), 2026-09-12"
filed_date: "2026-09-12"
closed_date: "2026-09-30"
closed_reason: "D-31 delegated-scope placement, verified restore/recovery, and explicit charge-based memory semantics; see current contract §8.3 and D-31"
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

## Final resolution — contract §8.3, D-31 and D-32

D-20 still says that lanes declare their needs and the daemon attributes and
places them. The daemon keeps private PID and cgroup namespaces; host `/proc`
is read-only, while host systemd is reached through the daemon-only system bus.
It does not use `--pid=host` or `--cgroupns=host`. The original direct-child
layout was rejected after the live systemd manager refused process migration
to non-delegated `dev-gates.slice`: a slice remains systemd-owned and is not a
supported delegation boundary. D-31 therefore creates one transient,
systemd-owned delegated scope directly beneath the verified gates slice and
one cgprofile-owned `rg-<token>` leaf below the scope. The actual scope
`ControlGroup` and delegation properties are read back from systemd; they are
not inferred from the token or unit name.

1. `start` accepts `--place` plus `--memory-high <bytes>`, `--memory-max
   <bytes>`, and `--cpu-weight <1..10000>` (ignored without `--place`). Before
   moving a PID, the daemon journals its stable start identity, original
   cgroup/unit, and exact intended transient scope. Initial PIDs are given to
   systemd as part of scope creation; later descendants are identity-checked
   before systemd attaches them to the leaf.
2. systemd retains ownership of the gates slice and scope. cgprofile enables
   only required controllers at that delegated scope root, creates and manages
   only its exact leaf, reads caps back, and samples the verified leaf while
   retaining the original cgroup as the session's logical key. Stop restores
   only matching survivors to their recorded origins, verifies leaf/scope
   emptiness, removes the leaf, and stops only the exact empty scope.
3. Restart recovery consumes the durable journal, not a token-shaped path.
   It recovers journal-only crashes and finished manifests with incomplete
   cleanup. An unknown PID, path, unit, or membership is never guessed or
   killed: preserve the scope, leaf, and journal; publish a failure for
   operator repair. Completed journals are not replayed for finished sessions.
4. Memory readings remain cgroup-native and are explicitly charge-based.
   Moving an already-running process does not transfer page charges faulted
   before placement. Leaf `memory.current`, `memory.peak`, and its
   `memory.high`/`memory.max` therefore are not total process RSS and do not
   cap all memory already resident in the lane. This limitation is part of the
   contract and adopter docs; a separately named RSS/PSS estimate would need
   its own contract.
5. D-15's cgroup path whitelist is enforced by the daemon's application code:
   only the delegated scope's controller file, the exact token leaf's files,
   and recorded origins needed for verified restore are permitted. The host
   cgroupfs mount itself is broadly writable to the privileged daemon. D-32
   keeps this implementation broker-free for the current single-operator,
   rootful-Docker estate; these checks constrain ordinary requests but do not
   contain arbitrary code execution in cgprofile. The host system bus is not
   mounted into the cockpit. The accepted tradeoff and a future broker's
   honest security benefit/limitations are documented in A4 of the run-gate
   placement design and `docs/DESIGN-GUIDE.md`.

Consumer side (run-gate, P5) is contract §8.9 obligation 3: placement is
requested only when the existing resource plan requests it; ephemeral lanes
remain Docker-capped. Placement refusal is disclosed without changing the
test verdict (R-36h).
