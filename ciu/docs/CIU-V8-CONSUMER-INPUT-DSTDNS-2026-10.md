# CIU v8 — dstdns consumer input (2026-10)

**Status:** input to the v8 planning set (proposal rev 3.4 / SPEC-V8 draft.7). It is not normative. Fold it the same way review rounds are folded.
**Source:** dstdns tooling-boundary pass, 2026-10-03. The full reasoning is in `dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md`, which is committed by the dstdns controller, so cite it by path.
**Why now:** the operator's current direction is that v8 *unifies* ciu and run-gate, and that dstdns's real use cases should shape it. The proposal (§4.1.10, R-01) still says "run-gate stays standalone". See open decision 1 below.

## Filed entries (search these before adding anything)

| Concern | Entry | v8 tag | v8 home |
|---|---|---|---|
| Which infra a worktree reuses must be declared in committed config, not passed as four CLI flags | ciu **CIU-116** | absorb | S9.5 joins → project-level join presets |
| A linked worktree's bake overwrites the shared project-built image tag | ciu **CIU-117** | absorb | S6.2 / S17.6.1 (no instance-scoping rule today) |
| No `resolve`/`exec` for a service of any instance (primary included) | ciu **CIU-118** | absorb | S4.4 identities-as-data covers names; generic exec still missing (S14.6.3 covers gate environments only) |
| Instance-id derivation: 7.15 ships base36, SPEC-V8 S4.1.1 says 6-hex SHA-256 | ciu **CIU-119** | absorb (spec correction) | S4.1.1, S4.1.2, examples |
| Per-environment concurrency, and a count fallback where the slice is invisible | run-gate **RG-67** (note 2026-10-03) | absorb the count fallback; exec N>1 stays v7-only | S16.6.1, S16.5.7 |
| Exec a command in a worktree's runner | run-gate **RG-70** (note 2026-10-03) | absorb | S14.6.3 |
| Ephemeral environment parity with a per-worktree runner (`image_from`, worktree mounted at the image's root) | run-gate **RG-73** | absorb | S16.4, S16.4.3 |
| Trunk-merge first-parent base and sequence-lane base propagation | run-gate **RG-74** | absorb | S16.5.4, S16.5.5 |
| Lane-scoped throwaway service (database) | run-gate **RG-75** | absorb | S16.4 (ephemeral `binds` / a lane-scoped Realization) |
| Judge command/pin/lane list restated per lane (dstdns: 118 identical pin blocks) | run-gate **RG-76** | absorb | S16.3 judge, S16.5/S16.7 lane import from `assay lanes --json` |
| Agent-concurrency cap has no config SSOT for manual controllers | nyxloom **NL-29** | n/a (references `[ciu.instances] max_concurrent`) | — |
| The image does not ship run-gate | mdt `TODO.md` (2026-10-03 entry) | n/a | — |

## Assumptions in the v8 set that today's facts contradict

1. **Identity (S4.1.1, proposal §4.1.4, demo).** v7.15 (vbpub@d1eb98770) moved dstdns main from `98535c` to `hox0ju`. A v8 that re-derives with SHA-256 hex changes every id a second time. Named volumes then orphan, as S4.1.2 itself states, and every literal goes stale. The D-645 fallout (32 containers and 3 volumes removed with raw `docker rm`) is the measured cost of one such change. → CIU-119.
2. **Admission (S16.6.1) assumes the slice's cgroup directory is visible and lockable where the gate runs.** The live probe that proposal §4.10 gap 5 still owes, taken from the dstdns devcontainer on 2026-10-03: `/proc/self/cgroup` = `0::/`, and `/sys/fs/cgroup/dev.slice` is absent. Every gate on this host is launched from there, so S16.6.1 as written admits nothing and blocks nothing. It needs the profiler-daemon path (run-gate RG-56), with a declared count as the fallback (RG-67 note).
3. **Exec parallelism (S16.5.7, §4.10 gap 18 "revisit only with a real consumer").** dstdns is that consumer: two concurrent gates on one runner, operator-authorised (dstdns D-619/D-636), held together by a hand-rolled two-slot flock (`scripts/gate-slot.sh`). **Recommendation: keep S16.5.7.** The right answer is for dstdns's hermetic lanes to leave the exec runner for `ephemeral` environments. They get per-lane cgroups, real profiling and no shared state. v8 must make that move possible, which is RG-73's parity.
4. **The demo's testing config (`v8-dstdns-demo/ciu.toml:525-528`) puts dstdns's `unit` lane in the `tester` exec environment.** Under S16.5.7 that serializes every hermetic lane of an instance behind one container. The demo should move `unit`, the assay R0-R2 lanes and `ui-unit` to the `clean` (ephemeral) environment. It should keep `tester` (exec) for live lanes that need the deployed stack.
5. **"Every checkout is an instance" (§4.1.9) and per-instance joins (S9.5) assume each worktree runs its own tester plus joins.** That is the right default, and the operator's direction for dstdns. But with joins authored per instance (`ciu instance add --join`), the project has no place to say "worktrees borrow vault/consul/redis from primary". → CIU-116.
6. **Proposal §4.9 "Open product decisions: None."** No longer true. The decisions below are open.

## Open product decisions raised by this input

1. **Unify or keep two gate implementations?** The proposal's R-01 posture is that run-gate's code is *lifted* into `ciu/gate/` and run-gate is maintained in parallel. The operator now frames v8 as the unification. dstdns's interest is a single implementation of lane execution, admission and history, with run-gate reduced to a compatibility front-end (or retired) after the cutover. Every RG entry above is tagged so it can be built once in run-gate now and lifted.
2. **Where a concurrency count lives in v8.** Recommended: in `[testing.environments.<e>]` (count fallback for ephemeral, S16.6.1) and `[ciu.instances] max_concurrent` for stacks. Agent counts stay in nyxloom `[policy]` (NL-29).
3. **Identity derivation, decided once** (CIU-119).
