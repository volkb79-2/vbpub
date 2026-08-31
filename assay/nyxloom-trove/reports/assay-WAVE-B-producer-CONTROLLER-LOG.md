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

- **2026-08-31 (review round 1 — complete verdict received)** — Reviewer's
  full text confirms round 1 is DONE (ACCEPT-conditional, not REJECT; no
  separate "must-fix 1", the blocker IS item 1). Precise findings, all
  independently reproduced by the reviewer through the real CLI or direct
  probes: **BLOCKER 1** — `cwd`(B043) + `link_paths`(B041b) compose into a
  real snapshot escape (`runner.py:1782`'s `is_dir()` follows the symlink
  `isolation.py:730` plants, so an untracked directory reads as
  commit-bound; the lane then runs against and writes into the consumer's
  actual working tree — reproduced twice via `assay run`, with a clean
  control proving it's the composition, not either key alone). **MUST-FIX
  2** — three shipped v9 schema descriptions (`schema.json:2683/1500/1515`)
  falsely claim a raw-verifier check that doesn't exist; W5 is byte-frozen
  so this needs a real fix + W5 regeneration, not just prose. **MUST-FIX
  3** — `judgment.r2.producer` has no closed raw vocabulary
  (`verify.py:624,735`); any spelling but exact `"ingested"` silently
  routes to the native branch (schema-layer catches it end-to-end, so not
  exploitable, but a layer-independence violation). **MUST-FIX 4** —
  `SUPPORTED_REPORT_SCHEMA_MAJORS` (`mutation_report_json.py:65`) admits
  major `"2"` with zero witness, contradicting its own docstring.
  **MUST-FIX 5** — REPORT §14 records a gate PASS typed before the run
  existed (moot as fact, gate genuinely green — the reviewer independently
  re-ran it — but the report must say what was observed); two more §11
  citation errors. **MUST-FIX 6** — a stale `adapters/javascript.py`
  docstring still calls B037 open. Reviewer verified extensively what does
  NOT need re-litigating (full list in its transcript) — the fix round
  should not re-touch any of it.
  **Controller decisions, resolved (routine, not paused on):** (1) note 1
  — `judgment.r2.discarded` unwitnessed (accepts a materially misleading
  value) — file as **B052**, don't build, same file-don't-build pattern as
  B049/B050/B051; (2) note 2 — the 5th `cwd` join at `runner.py:2945-2948`
  bypassing `resolve_run_cwd` — fold into the BLOCKER 1 fix since it's the
  same seam and A-367 already establishes "one path grammar, nothing else
  re-roots"; (3) note 4 — no ingested-R2 fixture in W5's acceptance corpus
  — add one while W5 is being regenerated for MUST-FIX 2 anyway; (4) note
  6 — report citation errors — bundle into MUST-FIX 5's report rewrite;
  (5) notes 3 and 5 (lexer `type (x)` fail-open, no `maxLength` on the
  ingested operator pattern) — both explicitly "cosmetic"/"pathological"
  per the reviewer, acknowledge in the REPORT, do not chase a fix. Next
  free decisions.md row: A-384. Dispatching a fresh fix-round implementer
  now, seeded with the complete verdict text.

- **2026-08-31 (fix round 1 — 6/8 committed, mid-gate)** — BLOCKER 1
  (`52b1f86b`: manifest lookup replaces the symlink-following `is_dir()`,
  symlink `cwd` refused, load-time pair refusal, 5th `cwd` join routed
  through `resolve_run_cwd`, reproduction test verified to fail against the
  OLD check), MUST-FIX 2/3 + the bundled W5 ingested-R2 fixture
  (`4780c4ba`: real raw ORDER checks built rather than prose walked back,
  closed `_R2_PRODUCERS` vocabulary, W5 regenerated with a real generated
  ingested verdict, gate phase 6 now 47 passed vs 44), MUST-FIX 4/6
  (`9848d5ca`: major-2 dropped, B037 docstring corrected) all committed.
  Correct judgment call, endorsed: dropped the `!` from the blocker commit
  so the wave keeps exactly one breaking-change marker (`af14021f` alone).
  MUST-FIX 5 (report's honest gate transcript) is PENDING — needs the
  currently-running gate's real output, correctly not faked.
  **Self-caught bookkeeping gap, my own miss**: the reviewer's round-1
  addendum separately recommended filing a NEW backlog item for "no
  `assay verify` re-read of the ingested report's per-file `source` against
  the snapshot's committed bytes" as **B051** — I endorsed that
  recommendation in chat and the prior log entry, but then wrote a
  DIFFERENT finding (the unwitnessed `judgment.r2.discarded`, from the
  verdict's own Notes §1) into the fix-round dispatch and mislabeled it
  "B052" without checking whether B051 was already taken. **It was not** —
  the fix-round implementer correctly noticed `4-backlog.md` had no B051
  yet and filed the discarded-count item there instead of leaving a
  phantom gap, which was the right call. **Net effect: the reviewer's
  ORIGINAL B051 recommendation (ingested source-byte verification) is
  still unfiled.** Will instruct the fix-round agent to file it as B052
  (the next real free number) once it resumes with the gate result — not
  urgent enough to interrupt its wait, which it explicitly said not to
  poll (using its own monitor).

- **2026-08-31 (fix round 1 — complete and green)** — Final tip `c1176bd0`.
  Registered gate green with a CAPTURED exit code 0 (not just the receipt
  marker — that gap was itself MUST-FIX 5, now closed three times over
  across `4780c4ba`/`05947625`/`c1176bd0`), `ASSAY_REGISTERED_GATE_
  COMPLETE=1` literal last line, 11 phase markers. Full suite 3801
  passed/13 skipped/0 failed (+22 nodes over the wave's pre-fix baseline).
  All 8 review items done + both backlog items filed (B051 = the
  `discarded`-unwitnessed gap, B052 = the reviewer's original
  ingested-source-verification recommendation — no actual displacement,
  just my own confusion in relaying it, resolved last entry). Exactly one
  `!` commit in the whole wave (`af14021f` only — the blocker fix was
  deliberately kept as `fix(assay):`). Three things the fix-round
  implementer itself flags for the reviewer to check first: the B051/B052
  numbering; dropping the `!` from the blocker commit; and the judgment
  that tracked+linked `cwd` is not a legitimate combination (given up by
  the load-time refusal, backed by a passing end-to-end node proving a
  link BENEATH the cwd still works) — this is the load-bearing design call
  under A-384. Also: the escape-reproduction test was confirmed to
  genuinely require BOTH parts of the fix reverted to fail (not just one),
  a real regression-test quality signal. **Resuming the ORIGINAL reviewer
  now for fix-verification** (round 2 — same session, `SendMessage`, per
  the dispatch skill's fix-verification role rule), not a fresh reviewer.

- **2026-08-31 (fix-verification round 2 — interim, gate pending)** —
  Reviewer re-ran every probe itself (not diff-reading): BLOCKER 1 closed
  and mutation-tested more thoroughly than claimed (all 3 defense layers
  individually necessary, only defeating all 3 reopens the escape; the
  real B041(b) use case — tracked `cwd` + untracked `link_paths` target —
  still works); A-384's design call endorsed independently; MUST-FIX 2-6
  all confirmed via the reviewer's own probes, not trust. **Two small
  non-blocking carry-overs found**: MUST-FIX 6 only half-done (a SECOND
  stale B037 reference at `adapters/javascript.py:525-532`, 380 lines from
  the one already fixed — the file now self-contradicts); REPORT §15
  makes a checkable diff-scope claim that was true when written but
  invalidated by the very next commit (`c1176bd0`'s B052 filing touches
  `4-backlog.md`, which §15 claims nothing outside `nyxloom-trove/reports/`
  touches). Reviewer explicit: "Neither blocks merge on my reading."
  Waiting on the reviewer's own gate run (in progress, phase 6+, 47 nodes)
  before the formal verdict.

- **2026-08-31 (fix-verification round 2 — ACCEPT)** — Formal verdict:
  **ACCEPT, unambiguous.** Round 2 of 3; round 3 not needed. Gate
  independently re-run green by the reviewer itself on `c1176bd0` (exit 0,
  marker, all 11 phases). BLOCKER 1 mutation-tested by the reviewer across
  all 3 independent defense layers (each alone sufficient; only defeating
  all 3 reopens the escape) — AND confirmed the fix did NOT regress
  B041(b)'s real legitimate use case (tracked `cwd` + untracked
  `link_paths` target still works end-to-end with a live canary). A-384's
  design call independently endorsed. All must-fixes re-verified via the
  reviewer's own fresh probes (unprefixed raw-layer failures now fire with
  distinct wording from the model layer; spelling-variant vocabulary
  probes refused; major "2" refused; W5 ingested fixture independently
  cross-checked against the real Stryker artifact). Two non-blocking
  documentation carry-overs, explicitly NOT gates on the ACCEPT: (1)
  `adapters/javascript.py:525-532` still has a second, now-contradictory
  stale B037 reference (reviewer recommends landing this one-line fix
  before release since it ships in the wheel, but leaves it to the
  controller); (2) REPORT §15's diff-scope claim went stale one commit
  later (the substance still holds, purely a citation staleness).
  **Controller decision**: fix carry-over 1 (one line, zero behavioral
  reach, reviewer-endorsed) via the fix-round implementer (not a fresh
  agent, not a controller-direct edit — preserves the
  controller-doesn't-write-code role split) with a final gate re-run for
  discipline; leave carry-over 2 as-is (truly cosmetic, substance intact).
  No further reviewer round needed for this trivial addendum — the ACCEPT
  already stands unconditionally. Then: merge --no-ff, real `cmru
  release`, deploy, dstdns notify.

- **2026-08-31 (merged to main)** — Final trivial fix landed clean:
  `7263716f`, gate green with a real captured exit code (0), literal-last-
  line marker confirmed, the corrected docstring verified present in the
  gated blob itself (not just the working tree). `git merge --no-ff
  feature/assay-wave-b-producer` into `main` — clean, zero conflicts (127
  files, +23708/-433), pushed as `5692ad37`. Starting the real
  `cmru release --project assay` next; expect 4.0.0 (the range's one and
  only `!` commit is `af14021f`).
