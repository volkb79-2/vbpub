# assay Wave B (producer wave) — controller log

Running log of decisions the controller (this session, operating in `/loop`
autonomous dynamic-pacing mode) makes without pausing for the operator, per
the 2026-08-31 clarification below. One entry per decision, newest last.

## Operating rule (operator ruling, 2026-08-31)

"Surface to operator" means **report/log it, keep going** — not stop-and-wait.
The loop only actually pauses for:
- an extreme blocker (nothing left to make progress on without a ruling —
  e.g. the reviewer 3-round cap hit without ACCEPT, standing doctrine),
- a genuinely breaking/irreversible product decision not already answered by
  the wave plan's own rulings, prior `decisions.md` rows, or standing project
  doctrine (A-334/A-335-style principles).

Everything else — routine BLOCKED items on one scope sub-step, REJECT verdicts
mid-review (dispatch the fix round), ACCEPT verdicts (proceed), a release
actually publishing, the next wave's dispatch — gets logged here and in chat,
and the loop keeps moving. Full memory: `autonomous-loop-report-vs-pause`.

## Log

- **2026-08-31** — Wave B implementer generation 1 dispatched (fresh Opus,
  worktree `.worktrees/assay-wave-b-producer`, branch
  `feature/assay-wave-b-producer` off `main`). Scope: B045 -> B046 -> B043 ->
  B041(b), schema v8->v9 hard cut on its own commit (`feat(assay)!:`), W5
  frozen drift-guard. Checkpoint clause: ARM ~120k ctx/~60 calls, CUT at next
  coherent boundary, write a numbered brief + commit + end turn; controller
  dispatches a fresh successor seeded with the brief (external compaction,
  never resume/fork for this step — E-008 + operator instruction).

- **2026-08-31 (checkpoint 1)** — Generation 1 checkpointed clean after 2
  commits (`384f3c0f` real StrykerJS fixture for B046, `fac1b73b` B045's
  config half) + the brief commit. ~1/8 of the wave done: B045 split at the
  schema boundary, its verdict-field/arcs/lexer half deferred to ride the v9
  cut with B046/B043/B041(b), none of which have started except B046's
  Stryker fixture + vocabulary constants. **No blocking decision asks** — two
  items flagged for the REVIEWER only, not a controller call: (a) whether
  `go-cover`'s unshipped producers (`go-test`/`covdata`) should ship now
  under B045's contract text, (b) the coverage-parser protocol widening
  needed for producer-aware branch arcs, deliberately left undecided rather
  than pre-decided. One housekeeping defect self-reported: one batch of
  fixture-lane migrations used a Python `write_text` script instead of
  Edit/apply_patch (durable rule violation, memory
  `repo-edit-with-apply-patch`) — edits are correct but flagged for the
  successor and reviewer. Dispatching generation 2, fresh, seeded with
  `assay-WAVE-B-producer-BRIEF-1.md`. Continuing without pausing per the
  goal's report-and-continue rule.

- **2026-08-31 (checkpoint 2)** — Generation 2 checkpointed clean after one
  commit (`cc4e955f`, B045's non-schema half: real istanbul branch arcs +
  the type-only lexer, closes B038(a)/(b)); full `pytest tests/` green at
  that commit (3668 passed / 13 skipped / 0 failed). B046/B043/B041(b)
  untouched beyond brief 1's state. **Controller decision, resolved (not
  paused on)**: brief 2 §6 flagged one real schema fork — `judgment_r2`'s
  `required` list (`jobs`/`max_mutants`/`operators`/...) can't hold for an
  ingested R2 lane, since B046 refuses those very fields on that path. The
  implementer's own recommendation, option (i) — make them conditional on
  `producer` via an `allOf if/then` (the schema's own existing pattern at
  1501-1576) rather than backfilling them from the report — is endorsed:
  it follows A-230a's precedent directly (assay's own policy fields must
  stay honestly empty for an ingested lane, not filled from what the
  external tool decided, the same declared-vs-verified line `helpers[]`
  already draws). Not a breaking/irreversible call — it's resolved by an
  existing rule, so no operator pause; instructing generation 3 to proceed
  with it and record the A-row, flagged for the reviewer as the brief
  already planned. Also endorsed: the implementer's re-ordering of the
  remaining work (schema cut ONCE with the full v9 field set, BEFORE the
  B046/B043/B041(b) feature commits — brief 1's original order would leave
  every intermediate B046 commit red until the schema catches up, and the
  W5 freeze must be byte-identical to the shipped schema so it has to be
  the last schema-touching commit regardless). Housekeeping: the
  Edit/apply_patch-only rule was violated a second time (a LOG append via
  heredoc, self-corrected) — reinforced again, explicitly, in generation
  3's dispatch. Dispatching generation 3, fresh, seeded with
  `assay-WAVE-B-producer-BRIEF-2.md`.

- **2026-08-31 (checkpoint 3 — MAJOR milestone)** — Generation 3 landed the
  schema cut: `af14021f` (`feat(assay)!:`, the wave's ONE AND ONLY `!` commit
  — `VERDICT_SCHEMA_VERSION = 9`, full v9 field set for all four items
  registered in schema+dataclass+verify.py, native defaults wired, 48
  fixtures + 7 test modules migrated) and `1577fa45` (W5 frozen drift-guard
  generation, `cmp`-verified byte-identical, gate wiring, W4 demoted).
  B045 now fully complete. Full `pytest tests/` green at tip (3668/13/0,
  328.17s, same node count as pre-cut). The controller-endorsed fork
  (checkpoint 2) landed as A-360 with two extensions the endorsement didn't
  explicitly cover (native->ingested forbidding mirrored both directions;
  `equivalence_artifact` joined the wire's forbidden set) — both follow
  directly from the endorsed reasoning, **not re-litigated, just flagged for
  the reviewer** per the implementer's own brief 3 §6. **No blocking decision
  asks.** Housekeeping win: the Edit/apply_patch-only rule held cleanly this
  entire session after two prior slips (the only Bash-driven file ops were
  two byte-identical `cp` duplications the implementer explicitly justified
  as the one thing Write can't do honestly). Remaining scope: B046 (raw
  verify.py currently checks NOTHING about an ingested payload — this is
  B046's gap to close, not a new problem), B043, B041(b), REPORT, gate.
  Dispatching generation 4, fresh, seeded with all three briefs.

- **2026-08-31 (implementation complete)** — Generation 4 finished the full
  scope: B046 (`d0aab6fd`, R2 by evidence ingestion + javascript at R2),
  the 601-line REPORT (`1a783f3e`), gate transcript (`a4bf1bc3` — final
  tip). Registered gate PASS on the exact tip (`exit 0`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, wheel `assay-3.2.1.dev17+ga4bf1bc3`,
  all 11 phase markers incl. `verdict-v9-successors-verified`). Full suite
  3779 passed/13 skipped/0 failed. **Correction logged**: mid-checkpoint I
  mistakenly called `Agent` with a text `to:` prefix instead of
  `SendMessage` to resume generation 4 while it waited on its own gate run
  — spawned a duplicate context-free agent, caught immediately (new
  agentId in the result), killed it via `TaskStop` before it touched
  anything, then properly resumed generation 4 via `SendMessage`. No
  worktree impact; memory `agent-resume-via-sendmessage-not-agent-tool`
  updated with this second occurrence. Five items generation 4 flags for
  the reviewer (not controller decisions): A-360's two unendorsed
  extensions; A-354 (go-cover producers); **B050 filed** (ingested
  `fail_under` only accepted at exactly 100.0 -- a real gap, needs a v10+
  schema field, not fixable under the frozen v9); gate run 1's exit code
  was never captured (nohup, receipt-marker only) -- run 2's evidence
  (which judges the actual tip) is what's being claimed on; A-335 restated
  (gate-green != release-green, release not run). **Dispatching a FRESH
  Opus reviewer now** (different session from every implementer
  generation), per the wave plan's role rules.

- **2026-08-31 (review round 1 — ACCEPT-conditional)** — Fresh reviewer
  completed its blind pass + its own independent gate run (green, exit 0 +
  marker, on `a4bf1bc3`) + a reconciliation sweep via its own sub-agent.
  Verdict: **ACCEPT-conditional**, not REJECT — 1 code blocker (`cwd` +
  `link_paths` compose into a snapshot escape: the lane runs against and
  writes into the consumer's working tree; full file:line detail
  requested, not yet received) + 5 must-fix-before-merge (three false
  raw-verifier claims baked into the byte-frozen v9 schema text, requiring
  a W5 regeneration before merge; a misspelled `judgment.r2.producer` that
  disarms raw ingested checks; `SUPPORTED_REPORT_SCHEMA_MAJORS` admitting
  an unmeasured major; the REPORT recording a gate PASS it could not have
  observed — a documentation-honesty fix, not a code bug; a stale
  docstring in `adapters/javascript.py` still claiming B037 is open). All
  ten push-on items and all five implementer-flagged items were
  independently verified hands-on (not rubber-stamped): A-354's refusal of
  go-cover confirmed correct (and undersold in its own defense), B050
  confirmed real and correctly deferred, A-360's two extensions confirmed
  sound. **Controller decision, resolved (not paused on)**: reviewer's own
  recommendation to file a NEW B051 (ingested report's per-file `source`
  never verified against the snapshot's committed bytes) rather than build
  it this wave — endorsed, same "file, don't build" pattern as B050, no
  operator pause needed. This is a routine ACCEPT-conditional requiring a
  fix round, exactly the goal's report-and-continue case (not an extreme
  blocker or a breaking product decision) — resumed the reviewer via
  `SendMessage` (correctly this time) to get the complete merged verdict
  with full file:line evidence before dispatching the fix-round
  implementer, since this notification only gave one-line summaries of the
  blocker and two of the must-fixes.
