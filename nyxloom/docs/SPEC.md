# nyxloom specification

Status: **draft design for pilot validation**. MUST/SHOULD/MAY describe a
future conforming implementation. Where a section says *inherited*, draft 1's
[SPEC](../../nyxloom/docs/SPEC.md) text applies with draft-2 storage
substituted (files for SQLite, tick for daemon, flock for managed lease).

## 1. Terms

Inherited: Project, Handoff, Task, Attempt, Gate, Lease, Evidence, Milestone.
Added:

- **Tick**: one bounded, idempotent reconciler invocation.
- **Wrapper**: the per-attempt supervisor process that runs a CLI leg and
  writes its receipt.
- **Receipt**: the typed completion record an attempt's wrapper writes
  (`done | blocked | limit | error`, exit code, per-oracle results, usage).
- **Wave**: a set of ≤N tasks reviewed by one frontier session.
- **Progress unit**: a roadmap/milestone acceptance criterion newly satisfied,
  recorded at merge.
- **Decision (D-ID)**: a DECISIONS-INBOX entry; tasks may depend on it.

## 2. Sources of truth

1. Product behavior: project specs + recorded decisions (inherited).
2. One handoff = **one Markdown file**; its frontmatter is the only machine
   metadata. A second machine representation of the same contract MUST NOT
   exist (no JSON sidecars).
3. Runtime truth: `events.jsonl` (append-only) is authoritative; statefiles
   are projections and MUST be reproducible by replay. Any database is a
   rebuildable index, never an authority.
4. Route selection is snapshotted onto the attempt at dispatch; it MUST NOT be
   inferred later from the then-current `routes.toml`.
5. Chat history is never required to reconstruct workflow truth (inherited).

## 3. Handoff frontmatter

Schema: [`handoff-frontmatter.schema.json`](../schemas/handoff-frontmatter.schema.json).
Requirements beyond schema validity:

- `id` unique per project; `input_revision` = commit at carve time.
- `tier` is carver-stamped (v2 §4); handoffs never name a CLI or provider.
- `session` is `fresh` or `resume:<affinity-key>` (an area label, not a raw
  session id — ids are runtime state on the statefile).
- `mutexes` are named groups from project.toml; `stack: exclusive` is sugar
  for the project's stack mutex. Pairwise serialize-with lists are not used.
- `depends_on` may name tasks and decisions (`D-0XX`); a decision hold blocks
  dispatch exactly like an unmerged dependency.
- Every `oracle` names: an observable, a negative case, and the project gate
  id it runs under. Every gate referenced MUST be declared in project.toml —
  free-text shell in a handoff is rejected (inherited security rule).

## 4. Task and attempt state machines

Inherited from draft 1 SPEC §4–§5 unchanged (states, terminality, typed
blockers, orthogonality of task/attempt/gate/decision/lease status; retries
preserve prior attempt evidence; provider limits pause the provider rather
than consuming retry budget; multi-signal liveness). Additions:

- `PAUSED` is **not** a task state: pause is a project/task flag the tick
  honors at dispatch time; in-flight attempts continue (brake, not kill).
- Every attempt carries `wave_id: null` until adopted into a review wave.
- Mapping from the legacy v2 §9 machine, for the transition period:
  CARVED→`CARVED/QUEUED`, DISPATCHED/IMPLEMENTING→`ACTIVE`,
  SELF_REVIEW→`ACTIVE` (attempt role=self-review),
  FRONTIER_REVIEW→`AWAITING_REVIEW`, MERGED→`MERGED`,
  VALIDATED→`COMPLETED`, BLOCKED→`BLOCKED|NEEDS_DECISION` by blocker type.

## 5. Tick requirements

1. A tick MUST be idempotent: given identical on-disk state, a rerun performs
   no new action. Every action is guarded by projected state, and mutations
   take the per-task flock.
2. A tick MUST be bounded: fixed scan, at most the configured number of
   dispatches, then exit. Long work (agents, gates) always runs detached
   under a wrapper.
3. A tick MUST NOT invoke a model, directly or transitively. Ambiguity
   resolves to `NEEDS_OPERATOR`, never to a guess.
4. Failure classification MUST use the typed ladder: receipt result → v2 §5.2
   limit-phrase match → exit-code class → `NEEDS_OPERATOR`.
5. Stall handling MUST implement the tiered check (log mtime → process state →
   gate-container activity) with the declared long-gate exception, and prefer
   resume over cold restart (v2 §5.4, normative here).
6. Each mutation = event append + statefile rewrite (temp + rename), in that
   order; a crash between the two is healed by replay (event wins).
7. Dispatch preflight MUST include: route probe, gate reachability (project
   gate probe command), lease availability, budget remaining, input_revision
   still current (else the task returns to re-carve — inherited rule).

## 6. Lint rules (carve gate)

`nyxloom lint` MUST pass before a handoff may be committed/queued. Rules
L1–L8 machine-checked; L9–L12 checklist-prompted where heuristics are weak.
The incident corpus (P51, P69, P78, P84, P85 — v2 deciding log) forms the
golden test set; **every future incident of this class MUST land as a lint
rule or template change, not only as prose**.

| # | Rule | Evidence |
| --- | --- | --- |
| L1 | Frontmatter validates against schema; ids/deps/decisions resolve; dates are real and current | review checklist "dates" |
| L2 | Every quoted gate command maps to a declared project gate id — a bare `pytest`/venv command is rejected | cockpit-vs-gate false greens |
| L3 | ≥1 oracle; each names observable + negative case + gate id ("verify the mechanism, not its constant") | standing contracts |
| L4 | **No enumerated subset under a universal contract**: a contract saying "every/all X" whose oracle lists specific instances is flagged; the oracle must name the comparison, not the columns | P78 |
| L5 | **No reviewer-only deliverables in implementer scope**: DECISIONS-INBOX writes, STATUS edits, merge/validate steps in an implementer handoff are rejected | P69 |
| L6 | **No oracle deferral**: acceptance steps phrased as another role's duty ("the controller/reviewer will validate…") are rejected; every oracle must be executable in the implementer's declared environment, and if that needs an environment build, the build is in scope | P84 |
| L7 | All referenced paths resolve from the repo root; cross-repo references must be vendored or explicitly marked non-resolvable-context | P69 |
| L8 | `escalate_if` triggers are mechanical (named contract unmet, forbidden file needed, threshold exceeded) — introspective phrasing ("reflect whether…") is rejected | P51 §7 |
| L9 | Scope touching declared infra globs forces the stack mutex + deploy-validation gate | dstdns §4 |
| L10 | Handoff token size within project budget (warn, then block at 2×) — carve investment is real but bounded | v2 §11 |
| L11 | Body includes: worktree path, branch, exact gate commands, out-of-scope list, context-to-read-first, in-repo exemplar | dstdns §7 |
| L12 | BLOCKED rule present verbatim; no instruction contradicts a project hard rule (frontmatter cannot override policy — inherited) | v2 §7 |

## 7. Review, findings, waves

Inherited (draft 1 SPEC §7): independent merge-gating review, packet binds
revision + commits + diff digest, findings distinguish contract / implementation
/ test-oracle / environment defects and product decisions, review fixes require
revalidation, merges serialized, auto-merge disabled pending the separate
decision. Additions:

- Findings are typed records with `flagged_by_pass_1: acted | mentioned | no`.
  **Only `acted` counts toward the pass-#1 trial metric** — a finding raised
  and then dismissed does not reduce the frontier pass's work (P85).
- A review session's cost is recorded at wave level; attribution to tasks is
  proportional to diff size and labelled `basis: estimated`.
- The reviewer MAY carve in-session post-merge (`carve_affinity` hint, v2 §2);
  carve outputs are still individually lint-gated.

## 8. Stop policy: outcomes, admission, ratchet

Carver outcomes inherited verbatim (draft 1 SPEC §10): `CANDIDATES_READY`,
`MILESTONE_COMPLETE`, `ROADMAP_EXHAUSTED`, `SPEC_GAP`, `DECISION_REQUIRED`,
`EXTERNAL_BLOCKER`, `BUDGET_EXHAUSTED`. Queue depth is a target only while an
active milestone admits useful work; carvers are never dispatched to satisfy a
numeric floor.

**Admission control** (the tick's, not the reviewer's): review findings and
implementer ideas enter the backlog as *proposals*. A proposal becomes
dispatchable work only through a carve that the tick admits, requiring: active
user-approved milestone, budget remaining, queue below target, and the
**progress ratchet** below.

**Progress ratchet** (makes v2 §8's "audit signal" computable): each merge
records its progress units (roadmap/milestone criteria newly satisfied —
declared in frontmatter `advances:`, verified by the reviewer). If
`max_consecutive_zero_progress_merges` (default 3) consecutive merges record
zero units while the queue is ≥ target and predominantly review-derived, the
tick MUST stop requesting carves and open `SPEC_ATTENTION` ("queue is orbiting
review descendants; milestone not advancing") — the mechanical form of the
observed carve-drift failure mode.

## 9. Spec/roadmap health (when do ROADMAP or specs need work?)

Computable triggers, each emitting `SPEC_ATTENTION` + notification + dashboard
badge; resolution is always a human/frontier act (a spec package, a decision),
never an automated edit:

1. Carver returns `SPEC_GAP` or `DECISION_REQUIRED` (direct).
2. ≥2 review rejections in one area with findings of class contract-defect.
3. BLOCKED rate with cause `underspecified` above threshold over a window.
4. A top-3 roadmap item not selected by the carver for N consecutive cycles
   (must be explained or the scoring is wrong — v2 §8 rule, computed).
5. Gap-analysis staleness: merges since last analysis > threshold (~10).
6. Repeated decisions pointing at the same spec file (≥2 open/decided in a
   window) — the spec is deciding-by-inbox instead of by text.
7. Progress ratchet trip (§8).
8. Carve lint failure rate trending up (carver quality signal).

## 10. Costs and budgets

Inherited: actuals preserved when supplied; estimates labelled; currencies
never silently combined. Additions: `basis` is mandatory on every usage
record; price-table revision recorded when pricing tokens; budget enforcement
at dispatch (task, project, milestone, daily caps); `BUDGET_WARNING` at
thresholds; `BUDGET_EXHAUSTED` stops dispatch, preserves resumable state,
notifies.

## 11. Leases

- All mutual exclusion uses flock(2) on files under the shared lease root.
  Content (owner, purpose, since) is dashboard metadata only.
- Exclusive: single flock. Counted: slot files. Acquisition is non-blocking at
  dispatch (unavailable → task stays QUEUED).
- Kernel release on process death is the recovery mechanism; the tick reports
  (never auto-breaks) a lock held by a live but stalled process.
- Legacy `.STACK_LOCK` / `.CARVE_LOCK` are honored read-only during evolution
  (their freshness heuristics apply) and retired at M4.

## 12. Dashboard

Inherited requirements (read-only, no AI, loopback default, restricted
artifacts) plus: the web root contains only renderer-produced files; raw logs
and transcripts stay outside it at stricter permission; log excerpts are
bounded and pass project redaction rules at render time; every state-changing
operation remains an audited CLI command.

## 13. Notifications and decisions

- Notification payloads are built exclusively from typed event fields; raw
  agent/log text MUST NOT reach a notification channel.
- Delivery failure never mutates workflow truth; persistent failure is
  surfaced on the dashboard (inherited).
- `decide` MUST: record decision + authority, append `DECISION_RESOLVED`,
  release dependent holds, and leave the inbox entry as the durable record.
- Editing the inbox file directly is equivalent; the tick reconciles it.

## 14. Self-acceptance (the tool must pass its own review bar)

Draft-2 may take over a duty only with, for that duty:

1. Unit/property tests for schema + transition validation; event-replay
   determinism test (`doctor --rebuild` byte-identical statefiles).
2. Lint golden tests: the P69/P78/P84/P85 incident corpus red, current good
   handoffs green.
3. Adapter fixture tests from captured CLI transcripts before any live
   dispatch; usage extractors verified against a real run per CLI.
4. Crash drills: kill -9 the wrapper mid-attempt and mid-gate → next tick
   classifies INTERRUPTED, resume works, leases are free; kill the tick
   mid-mutation → replay heals.
5. One shadow milestone (M1): dashboard + doctor run read-only against live
   manual/controller operation with no unexplained disagreement.
6. Dogfood rule: nyxloom's own implementation packages are carved, linted,
   dispatched, and reviewed under the protocol it automates, as soon as each
   capability exists.

Automatic merging remains a separately approved, later decision (inherited).
