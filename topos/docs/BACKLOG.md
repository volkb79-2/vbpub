# Backlog — identified, not yet carved

Distinct from two other trackers:
- `DECISIONS-INBOX.md` — **product** decisions needing the user's judgment
  (auth posture, framework picks). Not engineering work items.
- `ROADMAP.md` — large, already-scoped feature areas and package history.

This file is for concrete engineering work that someone identified — a
follow-up, a deferred fix, a named-but-unfixed flake, a "worth a package"
note — that didn't get carved into a handoff *this* cycle. Its purpose is to
make sure that insight survives past the session that found it, instead of
sitting undiscoverable in a REPORT/REVIEW/SELFREVIEW file until someone
happens to reread it.

## Who writes here

Any implementer, self-review, or frontier-review session that identifies
follow-up work it is not carving right now appends an entry. Frontier
reviewers are the primary source (post-merge, warm context) but are not the
only one — a self-review that spots a second, out-of-scope instance of a bug
it just fixed should log it here rather than only mentioning it in prose.

## Who reads here

**The carver, every cycle.** Per `docs/controller-workflow-v2.md` §8, the
carver picks its next handoffs by priority across ALL sources — this
backlog, `ROADMAP.md`'s open items, and standing product goals — not by a
fixed quota per source. An item picked up gets its entry marked `Carved` with
the resulting package ID; it stays in the table (struck through or noted) for
audit trail rather than deleted, until a periodic prune.

## Entry schema

| Field | Meaning |
|---|---|
| ID | `B-0XX`, monotonic |
| Source | review-derived / self-review / implementer-report / scan-backfill |
| Origin | file:line or package ID this was found in (e.g. `P85-SELFREVIEW.md`) |
| Finding | one or two sentences: what's wrong or missing |
| Priority | urgency/impact/importance, carver's call each cycle — not fixed at write time |
| Status | Open / Carved (→ package ID) / Declined (with reason) |

## Entries

| ID | Source | Origin | Finding | Priority | Status |
|---|---|---|---|---|---|
| ~~B-001~~ | self-review | `P85-SELFREVIEW.md` | Further fixed-iteration UI waits. | — | **Done** in P85 frontier review. |
| B-002 | review-derived | `P85-REVIEW.md` | `_wait_for_frame` still has a fixed-iteration wait, but has never exhibited a flake. | Low; act on first observed failure | Open |
| B-003 | review-derived | `P84-REVIEW.md` | Outside the declared `[dev]` gate, P75 subprocess smoke tests should explain/skip a missing MCP extra cleanly. | Medium; small contributor-hygiene fix | Open |
| B-004 | review-derived | `P83-REVIEW.md` | Drive the `ciu-grouped` TUI mode through the real app and prove synthetic rows are inert. | Medium | Carved → **P86** (revalidated 2026-07-15). |
| B-005 | scan-backfill | `P5-REPORT.md:28` | Configurable width/profile overrides. | Low | Folded → **B-033** config convergence. |
| ~~B-006~~ | scan-backfill | `P8-REPORT.md:79` | Document the additive `EntityFrame.damon` shape. | — | **Done** in `CONTRACTS.md` §4. |
| B-007 | scan-backfill | `P8-REPORT.md:80` | Preserve mixed-entity vaddr DAMON as explicit unattributed provider detail rather than dropping it. | Medium; truthfulness | Folded → **P94** provider/status contract. |
| B-008 | scan-backfill | `P14-REPORT.md:52` | Refresh provider status after DAMON start/stop. | Low | Folded → **P94** lease/status refresh. |
| B-009 | scan-backfill | `P18-REPORT.md:70`; `P42-REPORT.md:228` | Measured daemon-owned BPF attach/pin/recovery/detach lifecycle. | Later, optional/manual only | Merged with B-016; retain until a named scenario justifies the measured provider package. |
| B-010 | scan-backfill | `P20-REPORT.md:59` | Record attached/daemon history. | Medium | Superseded literally; the P88 query source and P91 persistent store must export the selected canonical window instead of inventing a second attach recorder. |
| B-011 | scan-backfill | `P20-REPORT.md:60` | Multi-user daemon authorization beyond socket permissions. | Later | Declined for the first trusted-operator milestone; reconsider only for an explicitly shared deployment. |
| B-012 | scan-backfill | `P28-REPORT.md:80` | Retain per-device cgroup I/O rates/cap saturation and device attribution. | High; accepted scenario OQ-09 | Open; current collection sums devices too early. |
| B-013 | scan-backfill | P30/P31/P32 reports | Configurable socket discovery, explicit source modes, timeouts and safe retry. | High | Carved → **P89**. |
| B-014 | scan-backfill | P34/P37 reports | Configure banner network/disk exclusions instead of hard-coded tuples. | Low | Folded → **B-033**. |
| B-015 | scan-backfill | `P36-REPORT.md:80` | Per-CPU and aggregate CPU trend. | Medium | Fold into the D-014 `mpstat`-class provider, not a banner-only package. |
| B-016 | scan-backfill | `P42-REPORT.md:241` | BPF bridge backoff/failure handling. | Later, optional/manual only | Merged with B-009. |
| B-017 | scan-backfill | `P44-REPORT.md:155` | Special `--paddr-enabled` daemon flag. | — | Declined; use generic configured provider activation/lease status instead of one flag per provider. |
| B-018 | scan-backfill | P45/P48 reports | Make bounded file/FD/log evidence available during drill-down. | Medium | Rewritten and carved → **P94**: leased/finding-scoped only; no generic browser, arbitrary path or unbounded follow. |
| B-019 | scan-backfill | `P49-REPORT.md:142` | Preserve planned current value across preview/execute so stale plans refuse. | High; action safety | Fold into **P93** owner-plan protocol and migration. |
| B-020 | scan-backfill | `P51-REPORT.md:114` | Simultaneously age- and byte-capped persistent daemon history. | Core | Carved → **P91**. |
| B-021 | scan-backfill | `P55-REPORT.md:102` | Optional pre-walk subtree pruning for very large trees. | Low; benchmark-triggered | Open. |
| B-022 | scan-backfill | `P56-REVIEW.md:14` | Reuse action gate primitives across squeeze and owner adapters. | Medium | Fold into **P93**; authorization remains centralized and owner-neutral. |
| B-023 | scan-backfill | P59 report/review | Give live TUI/recording selector resolution the same typed clean exit as `--once`. | Medium | Open. |
| B-024 | scan-backfill | P60 report/review | Accept `network` as an alias for `net`. | Low | Open; may instead be a configured query/profile vocabulary alias. |
| B-025 | scan-backfill | `P70-REPORT.md:75` | Bound the rare adversarial superlinear compatibility path. | Low; benchmark-triggered | Fold into P88 query-engine performance gates. |
| B-026 | scan-backfill | `P71-REPORT.md:133` | Correlate ZFS ARC pressure with workload pressure without claiming causality. | Low-medium | Open, explicitly non-causal. |
| B-027 | scan-backfill | `P72-REPORT.md:254` | A full 64-hex Docker ID can bypass protected-service matching; owner-managed workloads must also refuse raw-runtime mutation. | Urgent safety | Carved → **P87**. |
| ~~B-028~~ | scan-backfill | `P75-SELFREVIEW.md:51` | Mid-run MCP child teardown oracle. | — | **Done** in `tests/test_acceptance.py`. |
| B-029 | scan-backfill | `P76-REPORT.md:161` | CIU-aware actions. | High after protocol | Superseded as a direct CIU special case; implement a CIU owner adapter only after **P93**, never as inferred authorization. |
| B-030 | scan-backfill | `P58-REVIEW.md:179` | Oversized responses should remain useful. | Core | Fold into P88 projected/byte-bounded queries and P92 frontend routes. |
| B-031 | scan-backfill | `P58-REVIEW.md:184` | MCP `limit` schema should be integer while bool remains rejected. | Low | Fold into the shared query/frontend contract. |
| B-032 | product discussion | D-016 / `docs/LIFECYCLE-ADAPTERS.md` | Freeze and fixture-test the owner-chain adapter protocol before CIU/Wings/Compose actions or pull/recreate. | High safety prerequisite | Carved → **P93**. |
| B-033 | product discussion audit | D-009/D-015/D-019 and config scan | Converge strict config for source discovery, provider modes/leases, process candidate/history caps, policy tags, storage caps and existing UI/device exclusions; reject unknown/invalid combinations. | High; cross-package contract | Partially assigned to P89/P90/P91/P92; remaining convergence stays Open. |
| B-034 | product discussion audit | D-005 vs `HistoryConfig` | The implementation default is four hours; the accepted fast in-memory tier is five minutes at five-second samples. | High; contract drift | Carved → **P91** (migration and compatibility must be explicit). |
| B-035 | product discussion audit | D-002 vs P67 gateway | The current trusted-principal header is not the accepted random per-start capability-token loopback boundary. | High; web blocker | Carved → **P92**. |
| B-036 | product discussion audit | D-016 vs current action verbs | Existing Docker/systemd mutations predate owner discovery and typed refusal. | Urgent safety | Stopgap → **P87**; full migration → **P93**. |
| B-037 | process audit | D-013/D-019 | CPU-hot and I/O-hot candidate sets must be one bounded union with identity-safe `/proc` baselines and explicit coverage. | Core | Carved → **P90**. |
| B-038 | product discussion | D-008/D-010 | Stable workload/incarnation identity, lifecycle facts, tombstones and Previous instance/Recent exit links must share the capped store without polluting current totals. | Core | Carved → **P95**. |
| B-039 | P87 frontier review | `actions/execute.py` owner seam | The P87 owner gate engages only when a caller passes `owner_inspect` (default `None` keeps P46/P72 tests unmodified); production safety currently rests on three CLI kwargs, pinned by wiring tests. When any non-CLI caller appears (P93 owner protocol, TUI actions, daemon RPC), flip the seam fail-closed-by-default and make tests opt out explicitly. | High; safety posture | Open — natural P93 contract item. |
| B-040 | P87 frontier review | `actions/execute.py` docker verbs | Docker verbs still execute by the raw accepted name/ID string, so a name reassigned between the single authorizing inspect and the runner still races (inherent to P46 preview-parity argv). Executing by the resolved canonical full ID would close it but must keep preview/execute argv parity. | Medium; TOCTOU residue | Open — P93 should decide the argv contract. |
| B-041 | implementer-report | `P88-REPORT.md` | Ranking a large hierarchy by a `child_sum` metric (`net_*`) recomputes subtree sums per node → worst-case super-linear; common ranking metrics (`ram`/`psi`/`cpu`) are `kernel_subtree`/`local_only` (O(1)/node) so the measured budget is linear. A memoized single subtree-sum pass would harden the rare case. | Low; benchmark-triggered | Open (folds with B-021/B-025 subtree-pruning work). |
| B-042 | implementer-report | `P88-REPORT.md` | `topos query` offers no flat slice-grain rollup (`group_by slice`); the hierarchy projection surfaces slice structure and `topos report` keeps its rollup. A future consumer wanting flat slice sums can add it on the existing `subtree_aggregate` primitive. | Low | Open. |
| B-043 | feature request | user request | global: report if KSM is active, drilldown shows stats/agressivness. also show flags/labels for other facts like if ZRAM/ZSWAP/BFQ are active (any other important things to highlight?). in table per process/container if PR_SET_MEMORY_MERGE is set and report on memory saving (per container/process), how much memory is shared would be great. | high| open |
| B-044 | feature request | user request | consider this theoretical output, you see tiered slices with docker and (major) processes folded in, in the columns on the right the cgroups values: What `szstemd-cgls` tree can't show you
```
wings.slice                              min=8G  low=12G  high=14G  cpu=800   
└─ wings-b87c0a5b…slice                  min=0   low=0    high=max  cpu=100   
   └─ docker-cfc76dd….scope              min=0   low=0    high=max  cpu=100
``` | high| open |
| B-045 | feature request | user request | add is competitor, consider its features and display, if we should adapt something, `https://github.com/VHSgunzo/zswapmon` | high| open |


