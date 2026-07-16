---
schema_version: 1
id: groop-P93-lifecycle-owner-protocol
project: groop
title: "Lifecycle owner-chain protocol and action migration"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a standalone Docker container resolves to itself as the sole authoritative owner"
    negative: "a standalone container is misattributed to a non-existent higher-level owner"
    gate: groop-suite
  - id: O2
    observable: "a systemd-owned service resolves systemd as the authoritative owner ahead of the raw container"
    negative: "the raw container is treated as authoritative when systemd owns the unit"
    gate: groop-suite
  - id: O3
    observable: "a Compose-owned container resolves Compose as the authoritative owner in the chain"
    negative: "Compose ownership is not detected and the container is treated as standalone"
    gate: groop-suite
  - id: O4
    observable: "CIU/Wings ownership chains resolve through the fixture adapters with correct precedence"
    negative: "a CIU/Wings-owned target is executed through the wrong owner in the chain"
    gate: groop-suite
  - id: O5
    observable: "conflicting ownership labels surface as a typed conflict rather than an automatic pick"
    negative: "conflicting labels are silently resolved to one owner without surfacing the conflict"
    gate: groop-suite
  - id: O6
    observable: "a disappeared owner causes typed refusal instead of execution through a lower link in the chain"
    negative: "execution falls back down the chain when the selected owner has disappeared"
    gate: groop-suite
  - id: O7
    observable: "a stale incarnation is revalidated before execution and refused if it changed"
    negative: "execution proceeds against a stale incarnation without revalidation"
    gate: groop-suite
  - id: O8
    observable: "an unsupported action for the resolved owner is refused with a typed outcome"
    negative: "an unsupported action is attempted anyway or silently no-ops"
    gate: groop-suite
  - id: O9
    observable: "a verification failure after execution is reported as a typed partial outcome with a durable audit record"
    negative: "a verification failure is swallowed or leaves no audit record"
    gate: groop-suite
  - id: O10
    observable: "existing Docker/systemd action tests pass unchanged through the migrated protocol"
    negative: "an existing action test regresses after migration to the protocol"
    gate: groop-suite
  - id: O11
    observable: "a fake adapter's discovery and plan production are proven side-effect-free with no invocation of any external CLI"
    negative: "discovery or plan generation for the fake adapter invokes a side-effecting call"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["an existing mutation cannot identify a unique authoritative owner", "the mutation cannot verify post-state through the selected authoritative owner"]
advances: []
---

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

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p93-lifecycle-owner-protocol`
  at `.worktrees/groop-p93-lifecycle-owner-protocol` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p93-lifecycle-owner-protocol`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/LIFECYCLE-ADAPTERS.md`; `docs/ROADMAP.md`; and any
  handoff listed as this file's frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.
