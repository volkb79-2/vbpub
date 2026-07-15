# P87 - Close Docker action owner and protected-ID bypasses

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P72 (merged), P76 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none (P93 depends on this package)
> **Escalate-if:** refusing an owner-managed container requires running an owner CLI, or full-ID/name normalization cannot be proven from one inspected Docker object. Do not weaken the refusal.

## Goal

Close the current privileged-action gap before expanding the product: a protected
container addressed by its full 64-hex ID must not bypass matching, and a
container known to be owned by Compose, CIU, Wings or another higher-level owner
must not be mutated through Groop's raw Docker verb.

This is a narrow fail-closed stopgap under D-016, not the owner-adapter system.

## Required contracts

1. Resolve an accepted Docker ID/name once and compare protection rules against
   the canonical full ID, short ID and inspected name. No second `docker inspect`
   race between authorization and execution.
2. Treat positive owner metadata as a refusal for raw Docker `start`, `stop`,
   `restart`, `kill` and durable `update`. Recognize at least Docker Compose and
   the already-defined CIU/Wings labels. Unknown metadata is not permission.
3. A standalone container remains actionable through the existing P46/P72 gate.
   Runtime-only updates may remain available only when the inspected object is
   demonstrably standalone and the existing stale/current-value checks pass.
4. Preserve root, `--admin`, typed confirmation, validation and durable audit.
   Refusals are typed and audited, and name the detected owner plus the safe next
   step; Groop must not invoke that owner's CLI in this package.
5. Detection is identity/provenance only. Inferred CIU grouping never grants
   authorization and never proves ownership.

## Acceptance oracles

1. The same protected container is refused when targeted by name, short ID and
   full 64-hex ID; mutation-test removal of canonicalization turns the test red.
2. Compose-, CIU- and Wings-labelled fixtures refuse every raw mutation before
   the runner is called, with one pre/post audit outcome and no secret label
   values in the message.
3. A standalone fixture still executes each existing verb through the unchanged
   gate chain; existing P46/P72 tests pass unmodified.
4. Conflicting/partial owner labels fail closed with `owner-ambiguous` rather
   than falling back to Docker.
5. Docker inspect failure or identity change produces a typed refusal, not a
   name-only fallback.

## Out of scope

Owner CLI invocation, pull/recreate, adapter discovery beyond existing labels,
daemon install execution, and browser actions. P93 owns the full protocol.

## Gates

Run focused action tests, the dependency-complete zero-skip full suite, Python
compile checks and `git diff --check`. Write P87-LOG.md and P87-REPORT.md.
