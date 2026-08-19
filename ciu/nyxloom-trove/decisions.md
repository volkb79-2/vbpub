# ciu product decisions

Decisions below were approved by the operator on 2026-08-17 for the
automation-safe worktree lifecycle milestone. They are implementation inputs,
not an open inbox.

## D-001 — one serial feature branch and one final merge

All milestone planning, implementation, tests, documentation, and review fixes
land serially in `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
on `docs/ciu-worktree-automation-backlog`. There are no parallel CIU
implementation branches and no intermediate merges to `main`; the completed,
reviewed milestone is merged once.

## D-002 — canonical issue tracker during the backlog transition

`KNOWN_ISSUES_TODO_BACKLOG.md` remains CIU's sole active issue tracker until
nyxloom's per-entry backlog schema exists. `nyxloom-trove/backlog.md` is unused
for active product work in the interim and must not duplicate issue content.

## D-003 — authoritative worktree instance record

Every CIU-managed linked worktree has a versioned, atomic, non-secret record at
`<target-ciu-root>/ciu.worktree-instance.json`, alongside `ciu.env`. The record
owns durable logical identity and lifecycle state. `ciu.env` remains the
generated runtime environment. The record contains no credentials and no
frozen HEAD value; current Git state is derived during inspection.

## D-004 — identity scope and collision policy

A logical instance name is unique within one Git worktree family. Independent
clones may reuse it. Physical/runtime identities remain collision-safe on the
Docker host. Required identity facts are derived or read; absence and mismatch
refuse rather than inventing a fallback.

## D-005 — generated display, branch, and directory names

CIU-generated names use UTC
`<project-or-component>-<YYYYMMDD_HHMMSS>-<feature-description>`. The caller
supplies the prefix and feature description. The generated branch name and
worktree directory basename are exactly 1:1. Allocation occurs under the Git
family lock; a suffix is added only for an actual same-second collision. The
initial timestamp/name is persisted and never regenerated on resume. Explicit
advanced branch/path overrides remain supported.

## D-006 — create, adopt, ensure, and recovery

Create-new refuses before side effects when its requested identity is occupied.
Adopt is the only mode allowed to take ownership of an unmanaged existing
checkout. Ensure reuses an exact ready match and may complete only a
mechanically recognizable interrupted CIU-owned allocation. Identity mismatch
refuses. Repair is explicit and narrowly scoped; ensure never silently rewrites
conflicting state.

## D-007 — exact worktree control and execution

`ciu worktree up <id>` explicitly starts the selected instance using its own
record and `ciu.env`; execution never implies startup. `ciu worktree exec <id>
-- <argv>` supports an exact local checkout for non-container consumers. `ciu
worktree exec <id> --target <alias> -- <argv>` runs in a declared container
target. Target aliases are the only automation container-selection surface:
no arbitrary service escape hatch. A target resolves one exact Compose project,
service, and network; zero or multiple matches refuse. Worktree-mount
verification is required by default, with only an explicit
`requires_worktree_mount = false` target declaration opting out. Child argv is
passed without a shell and its exact exit code is propagated. Nyxloom policy
requires a container target for cockpit-doctrine projects.

## D-008 — withdraw the speculative S16.2 PostgreSQL provider

CIU-23 was grounded incorrectly: dstdns's already-existing schema gate uses a
disposable PostgreSQL container and explicitly rejects a scratch database on a
shared server. No estate consumer uses `--data-isolation`,
`PostgresProvisioner`, or `CIU_DATA_ISOLATION_*`; the shipped provider also
assumes a local Docker container/admin user and does not apply consumer schema.
Remove the flag, protocol, provider, env fields, tests, and S16.2 contract.
Record CIU-23 as WITHDRAWN, not FIXED. Close CIU-26 as OBSOLETE because the
unproven provider was removed. This is a breaking change for CIU's next major
release. A future general data-slot hook requires a real consumer and a new
grounded issue.

## D-009 — structured automation boundary

Worktree lifecycle, list, inspect, and removal provide versioned JSON with
closed status vocabularies. CIU exposes a versioned machine-readable capability
document; consumers allowlist tested capability identifiers instead of
inferring features from SemVer. CIU owns WHERE and exact identity. It does not
interpret Assay verdicts or own consumer workflow policy.

## D-010 — evidence and review

The CIU gate is modernized to use the pinned released Assay 1.0.0 artifact in
`tester-unified`, never an Assay source import. It resolves and verifies
`$CGROUP_PARENT_DEV_BACKGROUND` rather than hard-coding a slice. Focused tests
run during implementation; the completed series must pass the full
Assay-backed gate. One adversarial code/spec/test review follows implementation;
all accepted findings are repaired before the single final merge.

## D-011 — durable worktree-local configuration layer

`ciu clean` is valid inside a linked worktree and preserves its durable inputs:
`ciu.env`, `ciu.worktree-instance.json`, authored templates, and the checkout.
It removes runtime state and rendered artifacts only. Worktree-specific
configuration must not be appended to `ciu.env`: regeneration overwrites those
lines, and profile/shared-infrastructure choices are configuration rather than
machine identity.

Add a sparse, non-secret, gitignored
`<target-ciu-root>/ciu.global.worktree.toml.j2` layer. The global merge order is
committed defaults, committed project override, worktree-local override, then
the rendered `ciu.global.toml`. The layer is preserved by clean and may carry
legitimate per-worktree global overrides. CIU creates/updates it when lifecycle
options require worktree-local configuration. Raw secret scanning and the
normal template render/expansion rules apply.

Ownership is non-overlapping: committed templates own project policy;
`ciu.global.worktree.toml.j2` owns durable local configuration including the
selected service profiles and shared-infrastructure intent; `ciu.env` owns only
generated machine/runtime facts; `ciu.worktree-instance.json` owns durable
logical/Git identity and lifecycle state. The instance record may report
non-secret feature presence but must not duplicate the overlay as an independent
configuration authority.
