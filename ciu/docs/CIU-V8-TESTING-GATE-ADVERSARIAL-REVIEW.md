# Adversarial Review: CIU v8 Proposal

**Status:** *STALE* - was created for the initial unversioned CIU v8 proposal. Kept to check during future reviews if important things still stand out to be folded in.

**Reviewed:** 2026-08-23
**Reviewer:** Independent adversarial agent
**Proposal under review:** CIU-V8-TESTING-GATE-PROPOSAL.md
**Reference specs:** CIU v5 SPEC.md (S3/S7/S13/S14/S16/S17); run-gate-project SPEC.md/CONSUMERS.md; live dstdns configs

---

## Blockers

### B1: Logical services do not model CIU stacks

The proposal replaces `[deploy.phases]` with `[service.*]`, but dstdns deploys *stacks* (`infra/db-core`, `infra/db-init`, `applications/controller`). Each stack may contain multiple containers, init jobs, hooks, hostdirs, configfiles, and secrets. The proposal defines neither how a stack maps to a logical service nor whether multiple logical services can share one stack.

**Resolution (operator ruling):** Logical services address stacks via `location`. Individual containers/init-jobs within a stack are subsections:

```toml
[service.databases]
location = "infra/db-core"

[service.databases.db-init]
# one-shot init job configuration
```

This preserves stacks as deployment units while giving them logical identity. Status: RESOLVED by clarification, proposal needs revision.

### B2: `init_requires` cannot express one-shot readiness

dstdns applications must wait for `db-init` (a one-shot schema initializer that exits successfully). A topological graph of long-running service dependencies does not express "wait until this one-shot exits 0."

Status: OPEN — needs explicit completion/readiness edge type in the dependency model.

### B3: Secrets design conflicts with existing S4 architecture

Section 3.2 refers to `ciu.secrets.toml`, but S4 permits secrets only in stack-scoped tables, supports six directives, and rejects global secret tables. The flat per-service secret names lose producer/consumer semantics (`GEN_TO_VAULT` → `ASK_VAULT`).

**Resolution (operator ruling):** `ciu.secrets.toml` is ONLY for vaultless projects — it replaces `.ciu/secrets/*` storage. When Vault is used, existing S4 directives apply unchanged. `GEN_TO_VAULT` naturally requires Vault. Status: RESOLVED by clarification, proposal needs revision.

**Superseded 2026-08-27:** the proposal's own §10.3 now carries the full, revised resolution (addressing scheme, delivery modes, materialization relocation+lifecycle, Vault-bootstrap and AppRole disposition) — this entry is kept for audit history only; read §10.3, not this line, for the current design. One correction to the ruling above: `ciu.secrets.toml` is not restricted to vaultless projects in the finalized design — it coexists with Vault as a second SSoT in any project (§10.3's "Mixed" mode), and it still materializes to on-disk files (relocated, not eliminated) rather than truly replacing `.ciu/secrets/*` storage.

### B4: No worktree/instance selection contract for gates

How does the gate know which tree supplies test files, changed-file diffs, assay pins, and provenance?

**Resolution (operator ruling):** A worktree IS a fork off a commit. All repo files including test files are present. They can be changed and committed there. Any test attests to the commit it was tested against (no dirty tree). Gate execution happens inside the worktree against its own config and code. Instance targeting uses the existing S16 registry (`--environment <name>` resolves via worktree-instance record). Status: RESOLVED — existing S16 mechanism suffices; proposal should state this explicitly.

---

## Major

### M1: Provenance disappears from gate semantics

S17 treats running-image mismatch as fail-closed test-time concern. V8's gate composes five axes but never requires or records a provenance verdict. A passing R0/R1/R2 result could describe stale images.

### M2: Rigor levels are vocabulary, not enforceable contracts

R0–R6 meanings are assigned but no provider contract defines how evidence is attached to verdicts or how each level is proven.

### M3: Default skip-on-realness-mismatch creates false green risk

Skip-with-warning default allows partial evidence reported as success. Inverts run-gate/assay fail-safe posture.

### M4: Changed-file scope underspecified for monorepos

No diff base, merge-base policy, renamed/deleted files, generated files, nested package roots. Single global selection table centralizes monorepo ownership incorrectly. "No match → full scope" turns docs-only changes into full gate runs.

### M5: Profiles/groups split discards composition semantics

Existing profiles support ordered stacks, phases, compose profiles, env/topology overrides, layouts. V8 groups are unordered sets without these capabilities.

### M6: Multi-host model regresses from layouts

S7.5c has explicit ordered host plans, durable environments, bundle assignment, failure abort. Proposed topology is static without deployment order or per-host commands.

### M7: Shared infrastructure absent

S16.1 shared-infra join for worktrees is ignored. Common multi-stack optimization lost.

### M8: Concurrency budget and locking incomplete

S16.3 coordinates family-wide cold starts through shared locks. V8 doesn't explain interaction between named environments, locks, budgets, shared networks, parallel CI.

### M9: Migration operationally brittle

Hard cutover spans nyxloom pointers, copied scripts, standalone adopters, CI pipelines, AGENTS instructions. No transition tooling, validation command, or rollback plan.

### M10: Run-gate invocation mechanics lost without replacement

Cgroup placement, mounts, Docker socket access, memory limits, artifact paths, clean-tree policy, exit-code discipline — currently normatively owned by run-gate SPEC. V8 moves both into CIU without specifying replacements.

### M11: Assay pinning weakened

Current lanes verify pinned assay artifacts by version+SHA. V8 says "de-vendor" but provides no artifact-lock format or verification mechanism.

---

## Minor

- Realness taxonomy misses levels: external-emulator, recorded, snapshot, shadow, degraded-live, disabled
- Variant-level fields inconsistently scoped (some invariant across variants, some variant-specific)
- Mock implementation semantics undefined (executable? import? sidecar?)
- Health semantics conflate container health with dependency readiness
- Remote gate execution lacks security/isolation rules
- Interactive-only clean confirmation hostile to agents/CI
- Config sample inconsistencies (transport vs mode key naming)
- Intent names closed/product-specific; allow consumer-defined intents

---

## Enhancements suggested

- Keep logical services as projection over stacks rather than replacing stacks
- Explicit dependency edge types: requires-ready, requires-completed, provides-capability, soft-depends, data-seeded-by
- Preserve S13 typed references; add refs for initialized schema, completed job, healthy service, available realness variant
- Structured gate verdict: commit/tree state, image provenance, resolved instance, realness map, selections, skipped scopes, rigor evidence paths, artifact hashes
- Per-package/project gate configs for monorepos
- First-class test targets separate from deployment services
- Dry-run/plan output before any mutation
- Realness as environment capability declarations; tests declare minimum requirements
- Retain run-gate properties: stdlib independence, lane listing, visible resolution sources, advisory budget, artifact pinning, worktree override

---

## Bottom line

The proposal correctly identifies integration friction. But v8 as drafted replaces a stack-oriented orchestration system with an under-specified abstraction. Largest dangers: silent partial gates, unproven rigor claims, lost provenance, broken one-shot initialization semantics, disruptive migration without operational detail. Revise around CIU's existing stack/S13/S16/S17/secrets/layout models rather than replacing wholesale.
