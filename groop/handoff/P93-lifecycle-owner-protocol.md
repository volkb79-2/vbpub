# P93 - Lifecycle owner-chain protocol and action migration

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P87
> **Base:** main after P87
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** an existing mutation cannot identify a unique authoritative owner or cannot verify post-state through that owner. The required outcome is typed refusal, not raw-runtime fallback.

## Goal

Freeze and fixture-test D-016's lifecycle protocol before adding Compose, CIU,
Wings, Podman/Quadlet or future orchestrator actions. Migrate existing Docker and
systemd planning onto the protocol without adding pull/recreate yet.

## Required contracts

Implement the types in `docs/LIFECYCLE-ADAPTERS.md`:

1. Side-effect-free discovery returns an ordered owner chain, provenance,
   confidence and conflicts. Precedence is explicit; labels alone never confer
   authorization.
2. Each adapter advertises closed capabilities and produces a side-effect-free,
   immutable plan containing observed incarnation/state, exact argv/API intent,
   reversibility, persistence mode and verification predicate.
3. The shared P46/P78 authorization kernel gates every plan: root/admin/policy,
   typed confirmation, stale-plan revalidation, durable pre/post audit, bounded
   execution and typed partial outcome.
4. Execution occurs only through the selected authoritative owner, followed by
   owner-level and observed-runtime verification. Conflict, unsupported verb,
   disappeared owner or failed revalidation refuses without falling down the
   chain.
5. Migrate current Docker/systemd actions and `memory.high` governance to the
   protocol while preserving public behavior for demonstrably standalone
   targets. Preserve `planned_current_value` across preview/execute.
6. Define fixture adapters for Compose, CIU and Wings discovery/capability
   contracts, but do not invoke their CLIs. Document future candidates:
   Podman/Quadlet after scenario evidence; Nomad, Kubernetes and other
   orchestrators only through separately versioned adapters, never heuristic
   generic execution.

## Acceptance oracles

Fixture-test standalone Docker, systemd-owned service, Compose-owned container,
CIU/Wings chains, conflicting labels, owner disappearance, stale incarnation,
unsupported action, verification failure and partial audit. Mutation-test the
no-fallback rule. Existing action tests must pass through the migrated protocol,
and a fake adapter must prove discovery/plan are side-effect-free.

## Out of scope

Actual Compose/CIU/Wings invocation, pull/recreate/rollback, web actions,
Kubernetes/Nomad support and daemon installation.

## Gates

Focused lifecycle/action tests, zero-skip full suite, compile checks and
`git diff --check`. Write P93-LOG.md and P93-REPORT.md; update
`docs/LIFECYCLE-ADAPTERS.md` only where executable contracts require precision.
