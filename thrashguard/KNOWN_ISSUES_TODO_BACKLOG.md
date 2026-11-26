<!-- backlog-format: 1; prefix: TG- -->

# thrashguard backlog

Normative behaviour lives in [`docs/design/thrashguard/DESIGN.md`](../docs/design/thrashguard/DESIGN.md);
current behaviour only in the README, docs and SPEC-like files. Views:
[`INDEX.md`](INDEX.md), closed items in [`archive/`](archive/).

## TG-1 — P1 `observe` is the only cut; every act path is P2

**State:** in-progress · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md

**Problem.** The design's MVP splits into P1 `observe`, P2 `act`, P3 `victims` and
P4 `estate` (design §5). P1 compiles no act path, so `mode = enforce` must be
refused with a one-line reason and a test must assert no cgroup write and no
signal exists in the crate. **Wanted.** P1 finishes with config, PSI poll,
hysteresis, clock, the two sockets, the candidate store and container→scope
resolution, `events.jsonl`, `mode=report`, the CLI and static units, all green in
the registered lane; P2 lands freeze/thaw only with the VM acceptance of §5.

## TG-2 — the deferred P2/P3/P4 behaviour, as one pointer each

**State:** parked · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md, TG-1

**Problem.** P2 (freeze/thaw, `memory.reclaim`, read-back, death sweep,
`enforce`, the M1 thaw queue), P3 (`newest`/`refault`/`smallest-enough`, aging,
invariants) and P4 (run-gate interim registrant, thrashguard→hostobs emission,
`host-facts/2`, fleetd place-and-register, agctl snapshot, mdt-cockpit deploy
offer) are out of scope for the P1 wave and must not appear as code or as a lane
that pretends to judge them. **Wanted.** Revive each by opening its own wave with
its own exit criteria from design §5; never as "finish P1".

## TG-3 — The frozen-time consumer path does not exist in hostobs yet

**State:** blocked · **Severity:** medium · **Owner:** hostobs · **Links:** blocked-by hostobs M3, docs/design/thrashguard/DESIGN.md

**Problem.** Design §4a lists exactly three missing hostobs parts: the `annotate`
verb is `served: false`, the `events` read verb is planned M3, and freeze state
is not a sampled metric in `crates/model/src/metric.rs`. Until all three land,
every consumer must report the frozen total as `unknown` — never `0`. **Wanted.**
hostobs M3 plus one metric-registration item; thrashguard P4 emits
`thrashguard.freeze` / `thrashguard.thaw` only against those, and its own
`events.jsonl` is a crash record, not the consumer path.

## TG-4 — P4 must state how a cockpit-side run-gate reaches the registrant socket

**State:** open · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md, TG-2

**Problem.** Design §4e requires run-gate on the host to be the interim registrant
and says it "needs the `thrashguard-registrant` group membership (Q5) and nothing
else". That is a statement about the group, not about reachability: the gate
container runs inside the cockpit's Docker, the socket lives at
`/run/thrashguard/registrant/thrashguard.sock` **on the host**, and a container
with `--network none` and no `/run` bind sees neither. The §4e text does not
name the mount, the namespace, or the transport, so an implementer would guess.
**Wanted.** P4's own item states the mechanism explicitly — which process
resolves the host path (`nsenter`/host runner vs a bind-mount of
`/run/thrashguard/registrant`), what that does to the container's containment
argument (root in the container can reach the socket anyway, so the boundary
stays the scope resolution), and what happens on a host where the socket is
absent. Until that is decided P4 does not register from a container.

## TG-5 — No R2 (mutation) lane exists for this crate

**State:** open · **Severity:** medium · **Owner:** core · **Links:** thrashguard/run-gate.toml, assay/docs/CONSUMERS.md

**Problem.** `thrashguard/run-gate.toml` declares `lint` and `r0-r1` only. A
declared-but-never-run R2 lane is not evidence, so none is declared; but the
release rule wants R2 asynchronously on the newest release, and a campaign has
never been designed for a crate this small. **Wanted.** A cargo-mutants lane over
the changed lines of `src/` with a mutant-count estimate from
`cargo mutants --list --in-diff <base>` and a measured per-mutant cost, declared
only when there is a budget figure. P1's release therefore records "R2 pending".

## TG-6 — The R1 floor in `assay.toml` is a placeholder, not a measurement

**State:** in-progress · **Severity:** medium · **Owner:** core · **Links:** thrashguard/assay.toml, thrashguard/tools/check-floors.py

**Problem.** `assay.toml [lanes.r0-r1.judge].fail_under` is `90.0` only so the
lane loads; the design's habit is one full point below the MEASURED value, with
the derivation recorded in the lane header. A placeholder that survives becomes a
floor nobody can explain. **Wanted.** The first full `r0-r1` run's measured
percentage becomes the recorded figure, its derivation is written into the lane
header, and `coverage-floors.toml` carries the same number so
`tools/check-floors.py` can check the declaration both ways (floor above measured
fails; more than four points below fails).
