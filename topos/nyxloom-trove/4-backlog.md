---
kind: backlog
schema_version: 1
items:
  - id: B-001
    title: "[Done in P85 frontier review] Further fixed-iteration UI waits"
    type: bugfix
    component: ui-tests
    context_estimate: small
  - id: B-002
    title: "_wait_for_frame still has a fixed-iteration wait, but has never exhibited a flake"
    type: bugfix
    component: ui-tests
    context_estimate: small
  - id: B-003
    title: "Outside the declared [dev] gate, P75 subprocess smoke tests should explain/skip a missing MCP extra cleanly"
    type: bugfix
    component: mcp-tests
    folds_into: F022
    context_estimate: small
  - id: B-004
    title: "[Carved -> P86] Drive the ciu-grouped TUI mode through the real app and prove synthetic rows are inert"
    type: bugfix
    component: tui
    folds_into: F026
    context_estimate: medium
  - id: B-005
    title: "[Folded -> B-033] Configurable width/profile overrides"
    type: feature
    component: ui-config
    context_estimate: medium
  - id: B-006
    title: "[Done in CONTRACTS.md S4] Document the additive EntityFrame.damon shape"
    type: bugfix
    component: damon
    context_estimate: small
  - id: B-007
    title: "[Folded -> P94] Preserve mixed-entity vaddr DAMON as explicit unattributed provider detail rather than dropping it"
    type: bugfix
    component: damon
    folds_into: F034
    context_estimate: medium
  - id: B-008
    title: "[Folded -> P94] Refresh provider status after DAMON start/stop"
    type: bugfix
    component: damon
    folds_into: F034
    context_estimate: small
  - id: B-009
    title: "[Merged with B-016; retained pending named scenario] Measured daemon-owned BPF attach/pin/recovery/detach lifecycle"
    type: feature
    component: bpf
    folds_into: F043
    context_estimate: large
  - id: B-010
    title: "[Superseded by the F032 query source/persistent-history export] Record attached/daemon history"
    type: feature
    component: daemon-history
    folds_into: F032
    context_estimate: medium
  - id: B-011
    title: "[Declined for the first trusted-operator milestone] Multi-user daemon authorization beyond socket permissions"
    type: feature
    component: daemon-security
    context_estimate: large
  - id: B-012
    title: "Retain per-device cgroup I/O rates/cap saturation and device attribution (current collection sums devices too early)"
    type: bugfix
    component: diagnostics
    folds_into: F007
    context_estimate: medium
  - id: B-013
    title: "[Carved -> P89] Configurable socket discovery, explicit source modes, timeouts and safe retry"
    type: feature
    component: daemon-client
    folds_into: F030
    context_estimate: medium
  - id: B-014
    title: "[Folded -> B-033] Configure banner network/disk exclusions instead of hard-coded tuples"
    type: bugfix
    component: ui-banner
    context_estimate: small
  - id: B-015
    title: "[Folds into the D-014 mpstat-class provider] Per-CPU and aggregate CPU trend"
    type: feature
    component: diagnostics
    folds_into: F031
    context_estimate: medium
  - id: B-016
    title: "[Merged with B-009] BPF bridge backoff/failure handling"
    type: bugfix
    component: bpf
    folds_into: F043
    context_estimate: medium
  - id: B-017
    title: "[Declined; use generic configured provider activation/lease status instead] Special --paddr-enabled daemon flag"
    type: bugfix
    component: damon
    context_estimate: small
  - id: B-018
    title: "[Rewritten and carved -> P94] Make bounded file/FD/log evidence available during drill-down"
    type: feature
    component: inspect-files
    folds_into: F034
    context_estimate: medium
  - id: B-019
    title: "[Folds into P93] Preserve planned current value across preview/execute so stale plans refuse"
    type: bugfix
    component: actions
    folds_into: F033
    context_estimate: medium
  - id: B-020
    title: "[Carved -> P91] Simultaneously age- and byte-capped persistent daemon history"
    type: feature
    component: daemon-history
    folds_into: F032
    context_estimate: large
  - id: B-021
    title: "Optional pre-walk subtree pruning for very large trees (benchmark-triggered)"
    type: feature
    component: query
    folds_into: F028
    context_estimate: medium
  - id: B-022
    title: "[Folds into P93] Reuse action gate primitives across squeeze and owner adapters"
    type: bugfix
    component: actions
    folds_into: F033
    context_estimate: medium
  - id: B-023
    title: "Give live TUI/recording selector resolution the same typed clean exit as --once"
    type: bugfix
    component: cli-selectors
    folds_into: F014
    context_estimate: small
  - id: B-024
    title: "Accept 'network' as an alias for 'net' (may instead be a configured query/profile vocabulary alias)"
    type: feature
    component: cli-selectors
    folds_into: F014
    context_estimate: small
  - id: B-025
    title: "[Folds into P88 query-engine performance gates] Bound the rare adversarial superlinear compatibility path"
    type: bugfix
    component: query
    folds_into: F028
    context_estimate: medium
  - id: B-026
    title: "Correlate ZFS ARC pressure with workload pressure without claiming causality (explicitly non-causal)"
    type: feature
    component: zfs
    folds_into: F039
    context_estimate: medium
  - id: B-027
    title: "[Carved -> P87] A full 64-hex Docker ID can bypass protected-service matching; owner-managed workloads must also refuse raw-runtime mutation"
    type: bugfix
    component: actions
    folds_into: F024
    context_estimate: medium
  - id: B-028
    title: "[Done in tests/test_acceptance.py] Mid-run MCP child teardown oracle"
    type: bugfix
    component: mcp
    context_estimate: small
  - id: B-029
    title: "[Superseded as a direct CIU special case; implement a CIU owner adapter only after P93] CIU-aware actions"
    type: feature
    component: actions
    folds_into: F033
    context_estimate: medium
  - id: B-030
    title: "[Folds into P88 projected/byte-bounded queries and P92 frontend routes] Oversized responses should remain useful"
    type: bugfix
    component: query
    folds_into: F028
    context_estimate: medium
  - id: B-031
    title: "[Folds into the shared query/frontend contract] MCP limit schema should be integer while bool remains rejected"
    type: bugfix
    component: mcp
    folds_into: F028
    context_estimate: small
  - id: B-032
    title: "[Carved -> P93] Freeze and fixture-test the owner-chain adapter protocol before CIU/Wings/Compose actions or pull/recreate"
    type: feature
    component: lifecycle
    folds_into: F033
    context_estimate: large
  - id: B-033
    title: "Converge strict config for source discovery, provider modes/leases, process candidate/history caps, policy tags, storage caps and existing UI/device exclusions (partially assigned to P89/P90/P91/P92; remaining convergence stays Open)"
    type: feature
    component: config
    context_estimate: large
  - id: B-034
    title: "[Carved -> P91] The implementation default is four hours; the accepted fast in-memory tier is five minutes at five-second samples"
    type: bugfix
    component: daemon-history
    folds_into: F032
    context_estimate: medium
  - id: B-035
    title: "[Carved -> P92] The current trusted-principal header is not the accepted random per-start capability-token loopback boundary"
    type: bugfix
    component: web
    folds_into: F035
    context_estimate: large
  - id: B-036
    title: "[Stopgap -> P87; full migration -> P93] Existing Docker/systemd mutations predate owner discovery and typed refusal"
    type: bugfix
    component: actions
    folds_into: F033
    context_estimate: large
  - id: B-037
    title: "[Carved -> P90] CPU-hot and I/O-hot candidate sets must be one bounded union with identity-safe /proc baselines and explicit coverage"
    type: feature
    component: process
    folds_into: F031
    context_estimate: large
  - id: B-038
    title: "[Carved -> P95] Stable workload/incarnation identity, lifecycle facts, tombstones and Previous instance/Recent exit links must share the capped store without polluting current totals"
    type: feature
    component: lifecycle
    folds_into: F036
    context_estimate: large
  - id: B-039
    title: "The P87 owner gate engages only when a caller passes owner_inspect (default None); flip the seam fail-closed-by-default once any non-CLI caller appears"
    type: bugfix
    component: actions
    folds_into: F033
    context_estimate: medium
  - id: B-040
    title: "Docker verbs still execute by the raw accepted name/ID string, so a name reassigned between authorizing inspect and runner still races; P93 should decide the canonical-ID argv contract"
    type: bugfix
    component: actions
    folds_into: F033
    context_estimate: medium
  - id: B-041
    title: "Ranking a large hierarchy by a child_sum metric recomputes subtree sums per node (worst-case super-linear); a memoized single subtree-sum pass would harden the rare case"
    type: bugfix
    component: query
    folds_into: F028
    context_estimate: medium
  - id: B-042
    title: "topos query offers no flat slice-grain rollup (group_by slice); a future consumer can add it on the existing subtree_aggregate primitive"
    type: feature
    component: query
    folds_into: F028
    context_estimate: small
  - id: B-043
    title: "Report whether KSM is active with drilldown stats/aggressiveness; show flags for ZRAM/ZSWAP/BFQ; report PR_SET_MEMORY_MERGE and per-process/container memory-saving amounts"
    type: feature
    component: host-providers
    context_estimate: large
  - id: B-044
    title: "Tiered slice display folding Docker/major processes into columns with per-cgroup min/low/high/cpu values a plain cgls tree can't show"
    type: feature
    component: tui
    context_estimate: large
  - id: B-045
    title: "Evaluate zswapmon (https://github.com/VHSgunzo/zswapmon) as a competitor reference and adopt applicable features/display ideas"
    type: feature
    component: research
    context_estimate: medium
---

# Backlog

Migrated from `docs/BACKLOG.md`, which remains the fuller narrative ledger
(`[refs] backlog_product`) grouped by component (B-PORTAL-style buckets:
B-UI/B-DEAD/B-DATA/B-CONFIG/B-QUALITY/B-TESTS analogues here are B-DAMON/
B-BPF/B-ACTIONS/B-QUERY/B-LIFECYCLE/etc.) with full `file:line` citations.
IDs are preserved verbatim (`B-001`..`B-045`) because they are referenced
by name throughout `docs/ROADMAP.md`, `docs/STATUS.md`, and merged package
reports; renumbering would break that cross-referencing.

All 45 entries from the source ledger are preserved here, per its own stated
policy ("stays in the table ... rather than deleted, until a periodic
prune"). Entries already resolved — struck through, carved into a merged
package, folded into another entry, superseded, or declined in the source —
carry a bracketed disposition tag in their `title` (`[Done ...]`,
`[Carved -> Pxx]`, `[Folded -> B-0xx]`, `[Superseded ...]`, `[Declined ...]`)
since the backlog schema has no dedicated status field; only genuinely open
items are left untagged.

`folds_into` is set only where the source text names one clear, resolvable
target that exists as a `2-product-definition.md` feature id (per S2). Where
the source instead points at another backlog entry (a `B-0xx` chain, e.g.
B-005/B-014 -> B-033) or names a diffuse cross-cutting convergence with no
single owning feature (B-033 itself), `folds_into` is left unset and the
relationship is described in the title/here instead, to avoid a false S2
resolution.

B-043, B-044, and B-045 are fresh user feature requests (KSM/ZRAM/ZSWAP/BFQ
flag visibility and per-process memory-saving reporting; a tiered
slice/cgroup column display; a `zswapmon` competitive review) not yet folded
into any scoped roadmap feature.
