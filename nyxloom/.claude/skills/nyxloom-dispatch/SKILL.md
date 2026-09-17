---
name: nyxloom-dispatch
description: Dispatch-prompt templates for nyxloom pipeline roles (implementer, adversarial code reviewer, fix-verification) — fresh-session rules, checkpoint clause, LOG/REPORT contract. Use when spawning any pipeline agent, in any nyxloom-registered project.
---

> **Tool versions as of last verified update (2026-09-03):** nyxloom
> `0.3.1.dev1263+gf3b89f46`, run-gate `23.5.0` (pip-installed), ciu `7.11.0`.
> `assay` pip package is `5.0.0` (v9→v10 verdict schema hard cut — see
> `assay/docs/CONSUMERS.md` "Migration notes (v9 → v10)"); a consuming
> project's own gate lanes may still pin an older frozen `.pyz`, not yet
> migrated. This skill is mostly role/prompt-shape guidance (not tied to
> specific CLI argv), so it drifts slower than nyxloom-carve/nyxloom-merge-p —
> but the checkpoint sizing tracks each project's own CLAUDE.md-equivalent
> "long-running agent context discipline" figure, which is explicitly subject
> to re-measurement; check that hasn't moved past what's quoted below before
> trusting the numbers verbatim.
>
> **A per-project concurrency directive may override the defaults below** —
> e.g. dstdns's 2026-09-03 "single agent, single gate only, until reversed"
> (see its own standing-directives memory). Check the target project's own
> operating contract before assuming full parallel fan-out is authorized.

> **Canonical, repo-agnostic skill.** The pipeline SHAPE is universal to any
> nyxloom-registered project; substitute the target repo's own trove paths
> and gate argv from its own CLAUDE.md/AGENTS.md. Examples below use dstdns's
> conventions — read them as illustrations, never run a dstdns gate line
> against another repo.

# Dispatch prompts (nyxloom pipeline)

Role rules: implementer = FRESH, no base session, one per package (Sonnet by default for
mechanically well-specified packages, Opus where the package carries real design judgment —
the controller's call, per package). Carve/code reviewers = spawned FRESH the first time,
NEVER `subagent_type: "fork"` (a fork inherits the carver's blind spots, which is the one
thing the fresh-spawn rule exists to break) — but then kept PERSISTENT and resumed across
subsequent packages (2026-08-21: a cost/context optimization once several controllers
converged on it independently). The two properties are independent: "fresh vs. persistent"
is about the SESSION's lifetime; "blind first look" is about each individual REVIEW starting
from the diff/handoff/tree before any narrative, every single time, regardless of whether the
session itself is new or resumed. Never skip the blind pass to save a spawn. Fix-verification
= RESUME the ORIGINAL reviewer (SendMessage — it holds the review context) — this was already
true before the persistence change and doesn't need re-deriving. Repair successor = FRESH when
remaining work is large relative to the agent's context (a remaining-work call, not a
size threshold), seeded with the continuation brief.

## Fork/resume semantics — the actual test (2026-08-21)

"Never fork" was too blunt a rule; different fork/resume choices in this pipeline have very
different risk, and the real test is what the prior session's content actually carries: FACTS
(measurements, sweep tables, file contents, standing decisions) are safe to inherit — the new
task still has to act on them itself. JUDGMENT (a conclusion that a design is correct, that an
implementation satisfies its oracles, that a review found everything) is not — inheriting it
means the new task never independently forms the view it exists to form. The check: *if the
prior session's own conclusion on THIS specific question turned out wrong, would inheriting it
notice, or would it carry forward unexamined?* Concretely:

- **A reviewer forking (or resuming) the carver/implementer it is about to judge** — unsafe. It
  would inherit the exact judgment ("this design is right", "this diff is correct") the role
  exists to independently re-examine. This is what "never fork the carver" was protecting and
  still must.
- **A reviewer resuming ITS OWN session across different packages** — safe. Package A's facts
  don't contaminate judgment of package B's diff; each review still starts blind from that
  package's diff/handoff/tree. This is the "fresh-then-persistent" pattern above, not really a
  fork of anyone else's judgment.
- **A frozen, facts-only orientation snapshot** (one session that only read files and standing
  docs, rendered no design decision) forked by several role-agents — safe. There is no
  judgment trail to inherit, only assembled context, structurally the same thing a pack.md
  provides pre-loaded into a cache instead of freshly read.
- **Forking a previous implementer's live checkpoint session for a DIFFERENT-but-related
  successor package** — usually the wrong tool even when tempting: it drags in package A's
  full judgment trail (including whatever it decided that doesn't apply to B), and
  re-litigating which parts still hold costs more than starting clean. Prefer having the
  successor READ package A's REPORT.md/LOG.md (facts, distilled, already separated from the
  live reasoning that produced them) over forking the live session.
- **Model/effort/role must match for a fork/resume to be coherent at all** — it continues one
  conversation's actual state, and crossing model families defeats the cache-reuse point outright
  (a different model can't use another's cache) while crossing effort levels mid-conversation
  produces an incoherent reasoning trace, not a clean inheritance. If the new task needs a
  different model, that is itself a strong signal it needs a fresh session, not a fork.

## Resume / hand-over prompt should contain (checkpoint → resume cycle)

- **A pointer, not an inline dump**: "re-read `<BRIEF path>` first" — the resumed agent has full
  tool access and re-reads the file itself; embedding its content in the resume prompt defeats
  the point of writing it to a file. A one-line topic index after the pointer (the brief's own
  section headers) helps the agent navigate it without a second full read.
- **What changed since the checkpoint that the agent could not know**: any controller decision,
  ratification, ledger entry, or merge that landed while it was compacted — named explicitly, not
  left for the agent to discover by diffing.
- **Explicit confirmation of what does NOT need re-litigating**: "no new controller decisions" or
  the specific decision-ledger ids that still stand, so the resumed agent doesn't spend calls
  re-deriving something already settled.
- **A closing instruction scoped to exactly what's left**, not a restatement of the whole
  contract — the handoff+brief already carry that; repeating it here just adds tokens the agent
  will read twice.
- **Chain topology — validated for short chains, open for long ones.** Linear resume (compact the
  SAME session in place, repeatedly) showed zero measurable degradation across three chained
  compactions on one implementer (dstdns P116, vbpub `design-context-lifecycle-experiments.md`
  V1 addendum 4: flat resume floor, stable ratio all three times) — keep using it as the
  default up to that scale. Whether a package needing many more checkpoints should instead fork
  fresh from checkpoint 1 and feed it the CONCATENATION of every subsequent brief (bounded growth,
  no summary-of-a-summary compounding) rather than keep re-summarizing an already-summarized
  transcript is untested — treat it as an open experiment past ~4–5 checkpoints on one session,
  not a settled alternative.

## Implementer prompt must contain
- The handoff path + input_revision + "read BOTH halves in full".
- Worktree creation line verbatim (via `ciu worktree add`, never a raw
  `git worktree add` unless `ciu worktree` is genuinely unavailable); "work only
  inside it; scope.touch only".
- Orientation order: ledger D-records → sweep-tables → pack (with stamp-reconcile
  note; slices of edit targets are comprehension-only); "run your OWN tabulated
  sweep before deleting".
- Gate argv + "verdict in a SEPARATE step" + worktree-control baseline requirement.
- LOG/REPORT contract: `<trove>/reports/<repo>-P<NNN>-{LOG,REPORT}.md`; LOG
  per commit (self-hash rule); REPORT per-oracle evidence incl. mutation-check
  transcripts, pruned-assertion itemization, docs disposition table.
- BLOCKED protocol + escalate_if reference; commit trailer.
- **Checkpoint clause**: ARM at the project's own measured context/call threshold
  (whichever first), CUT at the next coherent boundary (green gate > commit >
  LOG/REPORT write > edit-cluster end; never on a red gate), repeat, stop when
  little budget remains. At the cut: continuation brief to a durable file + a
  self-authored `/compact` retention prompt → commit → return. Reviewers: the
  designated boundary is end-of-phase (blind → reconcile); name it in the prompt.
  Never "one emergency checkpoint near the ceiling" — that shape fires on nobody.
- Closing line: "claim only what you ran — a fresh adversarial reviewer verifies".

## Code-reviewer prompt must contain
- FRESH, adversarial, "your job is to BREAK this before merge"; own-sweep mandate
  (the pack biases coverage, not conclusions).
- The recurring-species checklist: consumer dimension (tabulated, all file types),
  flat-shim blast radius, oracle satisfiability + evasion probes (plant the
  violation, watch the guard), environment-specific claims (re-run gates itself,
  own control worktree), hollow tests (mutate the subject, watch the test),
  frontmatter-body agreement, forbid-list integrity across `main...<tip>`.
- Verdict format: ACCEPT / ACCEPT-conditional / REJECT with numbered blockers,
  file:line evidence, concrete prescriptions; product calls named as decision asks,
  never improvised.
- Blind phase first (no LOG/REPORT), then reconcile against the implementer's
  claims.

## Fix-verification message (to the SAME reviewer) must contain
- The repair commit hash; "re-run YOUR OWN probes verbatim"; per-blocker checklist;
  pre-adjudicated residues named as non-blocking; closing: "On ACCEPT state it
  unambiguously — the controller merges on your word."
