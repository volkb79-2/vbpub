# migration to handoffctl

Status: **design / pilot**. This plan preserves existing workflows while a
read-only importer and shadow control plane earn authority.

## 1. Scope and safety rules

- Existing root, groop, pwmcp, and dstdns workflow documents remain authoritative
  until a migration phase explicitly replaces them.
- Initial tools are read-only and MUST NOT dispatch, merge, rewrite handoffs, or
  delete worktrees.
- Imported facts retain their source file, line/section when available,
  repository commit, and whether the value was explicit or inferred.
- Ambiguity produces a drift finding, never a guessed value.
- Existing dirty worktrees and user changes are preserved.

## 2. Inventory sources

The first importer should index:

- generic controller workflow and CLI guidance;
- project adaptations such as dstdns `docs/ai-dev`;
- `AGENTS.md`, `CLAUDE.md`, and other tool instruction files;
- handoff headers and bodies;
- LOG, REPORT, SELFREVIEW, and REVIEW artifacts;
- roadmap, status, backlog, and decisions inbox records;
- dated dispatch documents and slot tables where available;
- Git branches, commits, merges, worktrees, and dirty state;
- captured agent output/metrics when locally available; and
- declared project gates and resource locks.

Chat transcripts may be an optional evidence source, but must never be required
to reconstruct workflow truth.

## 3. Drift audit

The importer emits machine-readable findings and a human report. Finding classes
include:

- conflicting canonical sources or precedence;
- stale/nonexistent paths and absolute sibling-workspace references;
- volatile model/provider routes duplicated in durable policy;
- malformed/unknown/missing handoff fields;
- dependency references that cannot be resolved;
- stale source revisions or base branches;
- task/report status disagreement;
- evidence claims not bound to a commit or gate environment;
- missing, duplicate, abandoned, or dirty worktrees;
- plain lock files with unverifiable ownership;
- open decisions that block queued tasks;
- roadmap items without observable acceptance;
- repeated review-derived work without milestone progress;
- missing cost/usage or mixed actual/estimated values; and
- instruction conflicts among agent-specific files.

Every finding has severity, project, source references, explanation, and a
suggested owner. The audit must be useful without invoking an LLM.

## 4. Mapping legacy records

Suggested mappings:

| Legacy artifact | Pilot representation |
| --- | --- |
| Handoff header/body | Handoff record plus source Markdown reference |
| Planned/queued row | Task projection with inferred status marker |
| LOG | Attempt events and referenced raw artifact |
| REPORT | Completion receipt/evidence candidate |
| SELFREVIEW | Advisory review attempt |
| Frontier REVIEW | Merge-gating review attempt/findings |
| `.STACK_LOCK` / `.CARVE_LOCK` | Imported unverifiable legacy lease; never auto-renewed |
| Dispatch document | Historical run/route snapshot |
| Decisions inbox | Typed decision records and dependency holds |
| Git worktree | Worktree inventory linked when identity is provable |

Inferred status is visibly marked and cannot authorize a dispatch or merge.

## 5. Shadow operation

After import, wrappers observe the existing manual/controller workflow:

1. Operator launches work as before.
2. Adapter captures process/session and emits normalized events.
3. `handoffd` computes what it would have scheduled or transitioned.
4. Dashboard shows both observed/manual truth and shadow decision.
5. Any disagreement becomes a drift/adapter issue; no automated action occurs.

Shadow comparison should cover success, contract BLOCKED, provider limit,
process interruption/resume, stalled gate, review rejection, post-merge failure,
open product decision, and resource contention.

## 6. Consumer layout after adoption

Generic protocol, adapter guidance, schemas, security, and benchmark methodology
move into this project. Consumers retain only project truth and a small adapter:

```text
AGENTS.md                         # hard rules and canonical pointers
.handoffctl/project.json          # machine policy
.handoffctl/handoffs/*.json       # typed metadata
docs/ROADMAP.md                   # product-owned
docs/DECISIONS-INBOX.md           # product-owned
docs/handoff/*.md                 # human contract, if retained
```

Agent-specific files should reference `AGENTS.md` and contain only tool-specific
deltas. Route matrices live in active configuration and are recorded per attempt;
dated dispatch files become archived evidence.

## 7. Adoption sequence

1. Ratify design and schemas.
2. Import/audit vbpub and dstdns read-only.
3. Resolve critical instruction and evidence conflicts manually.
4. Run dashboard from imported state.
5. Observe existing runs in shadow mode.
6. Pilot deterministic dispatch on bounded groop work with no live stack.
7. Pilot guarded review orchestration, still with manual merge.
8. Implement and pilot the dstdns adapter/resource leases.
9. Replace generic duplicated docs with short consumer pointers only after both
   projects have clean audits.
10. Apply a user-approved worktree/log retention policy; do not bulk-delete as
    part of migration.

## 8. Rollback

At every pre-merge phase, disabling `handoffd` leaves repositories and manual
workflows usable. Runtime state is additive and external to Git. A failed pilot
stops new automated dispatch, preserves worktrees and session handles, exports
events, and returns authority to the existing manual workflow.

Migration completion requires explicit approval; it is not inferred from a
green dashboard or a single successful wave.
