# thrashguard design guide

Why the code in this project is shaped the way it is. The **contract** is
[`docs/design/thrashguard/DESIGN.md`](../../docs/design/thrashguard/DESIGN.md);
this file does not re-argue it, and it records the choices made *inside* the
implementation where the design left a gap or contradicted itself.

## 1. The problem, and why a freeze instead of a kill

Overcommit is intended on a dev host. When pressure rises the choice is not
"which job dies" but "which job waits". Killing loses all invested work; freezing
keeps it and gives the memory back (design §1). So this service parks jobs; OOM
stays with memcg/systemd-oomd, which is aimed at the tiers on purpose.

The cost of that choice is honest: a parked job takes longer, a memcg OOM can
still reach a parked unit, and the guarantee is "some registered work always
runs", never "every job finishes in bounded time". Those are documented limits,
not bugs (design §2 D4).

## 2. Two guards, two counters, no shared state

`mdt-psi-guard` protects the host *from* the dev tier: it re-weights, then lowers
`memory.high`. thrashguard parks dev-tier work. They read different counters
(tier `some` vs host `full`) and neither waits for the other. Re-weighting is the
first, reversible act; a freeze is what "heavy swapping" justifies, which is why
thrashguard's trigger (3) is deliberately *lower* than psi-guard's escalate
point (5). On a `dedicated-dev` host psi-guard runs stage 1 only, so there
thrashguard is the only actuator.

## 3. Report first, because an untested freeze is a silent wrong answer

`mode` is `off | report | enforce`. P1 ships `report` as its observe-only mode and
`mode = enforce` is refused with one line naming P2. The no-act check is lexical:
`tests/no_act_path.rs` and `tools/check-lints.py` strip comments while preserving
ordinary and raw strings, then look for known cgroup-control names,
process/signal calls and file-writing APIs. It is a guardrail, not a Rust parser
or proof: aliases, macro expansion, generated code, indirect APIs, FFI and
unlisted spellings can evade the patterns. The release writer allowance is the
journal; the fixture helper is checked separately and compiled only under
`cfg(test)`.

## 4. `unknown` is not zero

Missing, unparseable or reset PSI counters set `unknown`: freeze nothing, thaw
what this guard froze, report `unknown`. A reset is judged against the last
*successful* reading, so a transient garbage read cannot hide one. A synthetic
`0.00` would read as "calm host" and leave work parked forever; an `unknown`
thaws.

## 5. The client names a container, the guard resolves the scope

A client inside its own private cgroup namespace reads `0::/` and nothing else,
so it cannot know a host cgroup path, and if it could, any registrant could reach
any scope. So the wire carries a docker container id or scope name, and the guard
resolves it under `allowed_ancestors`, taking the shallowest match (the docker
scope, not the placement leaf that holds only migrated pids), and refuses a
registration that resolves to none, to several, or to a `protected` ancestor.
That resolution, re-checked on every act in P2, is the real containment; the
socket group is advisory only.

## 6. Lifetime is heartbeat liveness, never a job bound

`ttl_s` is derived as `missed_heartbeats x heartbeat_s` from config keys. No
client sends a job duration: a job that takes long is not a job that failed. Two
deregistration cases are deliberately different (design §3, D14):

- explicit `deregister` **thaws**: the registrant is about to `rm`, and a frozen
  scope may hang `docker stop`;
- silent expiry **does not thaw**: the registrant vanished, and thawing into a
  still-full host only re-parks. The unit keeps its place until pressure release,
  aging or a guard restart.

## 7. Monotonic time, one journal

A frozen interval is measured on `CLOCK_MONOTONIC`, which keeps running while the
cgroup is frozen; wall time only stamps a line for a human. Every interval
carries `boot_id` so two readings can never be joined across a reboot. The journal
(`events.jsonl`) is thrashguard's own crash record, rotated at 1 MiB because
`/run` is tmpfs. It is **not** the consumer path: consumers read `hostobs` (P4).

## 8. Injectable roots, and the one place a real path is spelled

Every unit test takes `--cgroup-root` and `--pressure-path` and works in a temp
tree. That is the only way to test hysteresis, expiry, thaw ordering and the death
sweep without freezing the cockpit's own work. The production defaults are the
real paths and are the *only* place this crate spells them.
`tools/check-lints.py` checks that literal count and applies the lexical no-act
patterns described in §3; those patterns cannot prove safety against aliases,
macro expansion, generated code, indirect APIs, FFI or unlisted spellings.

## 9. One crate, no third-party dependency

The Rust gate image's crate cache identity is the registry set of
`hostobs/Cargo.lock`, and its verifier compares both directions. thrashguard
declares no dependency, and `tools/cargo-offline.sh` verifies that fact itself: a
lock that names a registry crate is refused, because it would need the image
rebuilt and a stated reason. JSON, TOML-shaped config reading and socket framing
are therefore implemented here, small and strict, rather than pulled in.

## 10. Where the design left a gap, and what this crate chose

Each of these is a deliberate smallest-safe choice, recorded so a later cut can
revisit it with a reason rather than by accident:

1. **PSI `avg10` is carried as integer hundredths.** The design names a decimal
   threshold; comparing floats across a hold window invites a `0.9999999` bug, so
   `3.00` is `300` and the factor-of-two release is exact integer arithmetic.
2. **A missing `some` row is `unknown`, not a number.** The design makes `full`
   the trigger and `some` reportable; a file with one row is not a measurement of
   this host.
3. **A registration refusal stores nothing.** The design says so; this crate also
   makes the refusal reply name the reason (`none`, `several`, `protected`,
   `malformed`), because a registrant that gets "no" needs to know which.
4. **The scope resolution takes the shallowest match.** Two scopes with the same
   name at different depths would otherwise make the choice depend on walk order.
5. **Two scopes with the same name at one depth is a refusal (`several`), not a
   coin flip.** A deeper match of the same name loses to the shallower one and is
   recorded in `Scope::shadowed` for the journal, so "the docker scope, not the
   placement leaf" (design §2 D1) is a decision and not a sort order.
6. **A `[[static_unit]]` path is exempt from `allowed_ancestors`, never from
   `protected`.** A standalone host has no docker scopes at all, so holding an
   operator's `/etc` path to the docker ancestors would make the only
   standalone candidate source unusable; a path under a protected ancestor is
   still refused, so nobody parks the cockpit by accident.
7. **`refault` and `smallest-enough` report `rule-unknown` and order by
   `newest`.** Neither has a readable input in P1 (no `memory.stat` reader, no
   reclaim estimate). Design §8 requires the guard to *say* unknown rather than
   use a zero rate, so the reply carries `rule_reason: "rule-unknown"` beside the
   rule it could honour. Rejected: refusing to run with such a rule, which would
   turn a config typo into a dead guard.
8. **Every P1 journal row says `simulated: true` and `seconds: 0`.** No interval
   can exist because nothing is frozen. Writing `seconds: 0` alone would read as
   a real, instantly-thawed freeze; the marker is what stops that misreading.
9. **The journal records transitions, not ticks.** A `would-freeze` row every 5 s
   for the same unit is noise in a 1 MiB rotating file, and it hides the rows that
   matter. The live decision — including its reason word — is served by `status`
   and `candidates` instead.
10. **The daemon adopts systemd's descriptors instead of binding paths.** A
    process that binds its own socket cannot set the `root:thrashguard-{registrant,
    reader}` group (`std` has no `chown`), and it leaves a stale socket file after
    every restart. `LISTEN_FDS`/`LISTEN_FDNAMES` are validated as a pure plan and
    one audited `unsafe` block wraps the raw descriptor — the crate's only
    `unsafe`, asserted by `tools/check-lints.py` in both directions.
11. **`serve` refuses `enforce` and exits 0 on `off`.** `enforce` is valid
    vocabulary and `status` must be able to report it, but this build cannot
    honour it, so the refusal happens at the run boundary with one line.
