# nyxloom — LESSONS (canonical, product-level)

> **What this is.** The accumulated, *general* lessons that improve nyxloom for
> every consumer. This file ships **with the product** and is maintainer-curated,
> exactly like `AUTHORING.md` / `STANDARD.md` / `DOCTRINE.md`. It is the graduation
> target for project-local lessons, not a scratchpad.
>
> **Relationship to DOCTRINE.md.** `DOCTRINE.md` is the *stable operating rules*
> distilled from experience; `LESSONS.md` is the *accumulated lessons* those rules
> come from. A lesson that becomes a load-bearing, always-applicable rule graduates
> into DOCTRINE.md; LESSONS.md keeps the fuller "why", with the concrete incident.

## Placement & promotion model (how a lesson flows)

Three surfaces, following the same canonical/trove ownership model as the rest of
the doc set (`STANDARD.md`):

1. **`nyxloom-trove/LESSONS.md`** (per-project, in the consumer repo) — the
   **writable** surface. The factory (daemon) and any working agent append a
   discovered lesson here. Each entry is tagged `scope: project | product`.
2. **`nyxloom/reference/LESSONS.md`** (this file, canonical, shipped) — the
   **general** lessons. **Never written directly by the factory.** It is the
   integration target for `scope: product` lessons a maintainer has accepted.
3. **`nyxloom/reference/DOCTRINE.md`** — where a matured general lesson graduates
   into a stable rule.

**Promotion process (the answer to "write direct or local, and send upstream?"):**
- A discovered lesson is **always written project-local first** (`nyxloom-trove/LESSONS.md`).
  The factory NEVER auto-mutates a shipped `reference/` surface — that would be the
  daemon committing to the product's public docs.
- A `scope: product` lesson **additionally emits an upstream proposal** (the
  system→system lessons channel — see `docs/plan-factory-hardening.md`, reusing the
  findings-channel plumbing: a `LESSON_DISCOVERED` finding with `scope=product`).
  Until accepted it stays in the trove flagged `upstream: proposed`.
- A **maintainer** (or nyxloom reviewing itself under a stricter, human-gated
  review) integrates accepted product-lessons into this file and marks the trove
  entry `upstream: integrated (ref: <commit/link>)`.
- Net: **write-local-first, propose-upstream-in-parallel, maintainer-integrates.**
  Even when nyxloom is dogfooding itself, edits to this shipped surface pass a
  maintainer-review gate; the factory only *proposes*.

Each lesson entry carries: a one-line rule, the concrete incident that produced
it, and "how to apply" (ideally: which carve/review/gate mechanism should enforce
it so it stops being advice and becomes structure).

---

## L1 — Prefer the structural fix, never the band-aid

**Rule.** When a defect has a root cause, fix the root cause; do not paper over a
symptom. A guard/shim that makes a symptom pass while leaving the underlying
duplication, dead code, or missing invariant in place is debt, not a fix.

**Incident (2026-07-24).** A JSON schema existed as two hand-maintained copies
(`schemas/` + `src/nyxloom/schemas/`) that silently diverged twice. The reflex fix
was a byte-identity *guard test* — a band-aid that keeps two sources of truth and
merely alarms on drift. The structural fix is to have **one** source of truth
(de-duplicate; generate or symlink any second copy). The guard is acceptable only
as a temporary bridge while the de-dup lands, never as the destination.

**How to apply.** In carve oracles and review, ask "is this a symptom-guard or a
root-cause fix?" A change that adds a check to tolerate a bad state, rather than
removing the bad state, must justify why the structural fix is out of scope and
file the structural fix as a backlog item. This aligns with the greenfield
policy (`AGENTS.md` §4.1: no dual naming / dual behavior paths).

## L2 — The three correctness layers are non-redundant; do not collapse them to save cost

**Rule.** GATE (deterministic reach/coverage floor) ⊕ REVIEWER (semantic/design)
⊕ CONTROLLER (spec authorship + harness) each catch a class the others
*structurally cannot*. "Tests pass + 100% coverage → merge, skip review" ships
production incidents.

**Incident (2026-07-24).** A package with 1600+ passing tests and a 100%
changed-line coverage gate carried a real bug: an unguarded call could raise
`AttributeError` on a type-legal `None` field **after** `git update-ref` already
published, leaving git advanced while the projection stayed `MERGE_READY`. No test
constructed the `None` case, so the gate was green; the **reviewer** caught it.

**How to apply.** Spend correctness where the blast radius is largest and cheapen
it elsewhere: additive/leaf work → gate + one cheap review; **frozen-core** work
(`reconcile.py`, `daemon.py`, `storage.py`, `types.py`) → full stack + perspective-
diverse review + (where affordable) mutation testing. Drive the switch off the
carver's complexity-band prediction, not a flat policy.

## L3 — A green gate proves lines RAN, not that decisions are RIGHT

**Rule.** Coverage/tests green ≠ correct. Deterministic gates prove *reach* and
*mechanical* properties; semantic correctness (a pure state machine emitting the
right action for an event history, a defense-in-depth invariant, parity that
cannot drift) is the reviewer's floor, and for the frozen core, property/invariant
tests — not line coverage.

**How to apply.** For frozen-core changes require behavioral oracles with
negatives and property tests, and instruct the reviewer to adversarially attack
the specific invariant (determinism, exception-safety, parity). Encode "attack
this" as `review_focus` on the handoff so it is mechanical, not the reviewer's
luck (see `docs/plan-factory-hardening.md` D).

## L4 — Merge only on a gate verdict you actually read; gate → verdict → merge are separate acts

**Rule.** Never make a publish conditional on a signal you have not read. The
daemon mechanizes this (`_execute_auto_merge` runs the gate on the scratch merge-
tree and publishes ONLY on `exit_code == 0`, else `REVIEW_REJECTED`). Any *manual*
merge path (an operator, a manual controller, `cli.cmd_merge`) must reproduce it:
run the gate, **read** the real exit/verdict in a distinct step, merge in a later
step — never in the same action.

**Incident (2026-07-24).** A manual controller read a gate log and issued the
merge *in the same step*, so the merge did not depend on the verdict; a wrapper's
trailing no-op made the job report "exit 0" while the gate had failed. Reverted
without harm because main was unpushed.

**How to apply.** Structurally: the daemon path is already safe; close the manual
gap by gating `cli.cmd_merge` (unless `--force`) and by having post-merge
validation **auto-revert** (CAS `update-ref` back) rather than only BLOCK on a
failing published tree (see `docs/plan-factory-hardening.md` F). Trust no
wrapper's exit code — read the tool's own verdict line.

## L5 — Hardening the bar does not harden the existing house

**Rule.** Changed-lines gates (diff coverage, changed-lines mutation) only examine
*new* diffs. Code written before a bar was raised is never retroactively examined —
its hollow tests and dead branches persist invisibly.

**How to apply.** Schedule a deliberate audit of the pre-existing frozen core
(mutation + dead-code sweep of `reconcile.py`/`daemon.py`/`storage.py`/`types.py`),
prioritized by the strategic test-health trigger (D-065). Do not assume a raised
bar cleaned the baseline (see `docs/plan-factory-hardening.md` H).

## L6 — When a new protocol subsumes an old responsibility, DELETE the old path — do not carry it forward

**Rule.** The most dangerous refactor is a faithful one. When a package builds a new
mechanism that takes over an old code path's job, copying the old code forward *verbatim*
silently re-introduces the exact bug the new mechanism exists to eliminate — and the
happy-path test (which exercises the new mechanism) sails right past it.

**Evidence (F018 P3c, 2026-07-25).** The carve-normalize path copied the legacy *launch-time*
re-scope supersede verbatim. But §4.2's whole point was to move supersession to *admission*
(only after a valid replacement exists). With both firing, the proposal protocol's admission-
supersede became a dead no-op, and a re-scope that produced an empty proposal deleted the
origin with nothing to replace it — the precise B7 data-loss bug §4.2 was written to fix.
Same shape in P3b: an "all-artifacts-in-states" structural cursor (carried-forward intuition)
let the ordinary scan pre-empt the new admission marker.

**How to apply.** When authoring a package that introduces a protocol/event/cursor that owns
a responsibility an existing path also performs, the handoff must name the old path and say
"DELETE it — the new owner is X." Reviewers: for every "preserved verbatim from legacy"
comment in a diff, ask *does the new protocol now own this?* A green gate + 100% coverage
cannot catch it (the bug lives in a path the tests are structured to avoid); only reading the
spec's *intent* against the code's action-ordering does (reinforces L2/L3).

## L7 — A headless implementer's "done / ready to merge" is never the merge signal — the controller's independent re-gate is

**Rule.** Headless/`-p` implementer agents routinely PARK: they launch their gate in the
background, arm a monitor, and end their turn *before the verdict lands* — then report "done."
Their committed tree is frequently RED (coverage-short, or a failing test they never saw). A
self-reported green is, at best, a hint about where to look.

**Evidence (F018 P3, 2026-07-25).** Three consecutive parked reports across P3b/P3c whose
commits were RED on independent re-gate (80.6% coverage; an xdist-only test-isolation failure;
a branch-authority worktree assertion). Every one was caught only because the controller
re-ran the real gate unconditionally.

**How to apply.** The controller re-runs the containerized gate on the committed branch for
*every* package, regardless of the agent's report — this is the gate, not belt-and-braces.
Read the actual `DOCKER_EXIT` + the `diff-coverage OK/FAIL` line yourself (L4). Handoffs
should still say "gate synchronously, don't park," but do not rely on it — the harness
behavior wins. (Extends `nyxloom-trove/LESSONS.md` PL4.)

## L8 — A handoff states the symptom and a *candidate* cause; the log outranks the controller's hypothesis

**Rule.** When sending a fix, describe the observed *symptom* precisely and offer a *candidate*
cause — but leave the implementer free to find the real one. A controller hypothesis dictated
as fact can send an implementer chasing a bug that isn't there.

**Evidence (F018 P3c, 2026-07-25).** The controller diagnosed a failing byte-identical test as
an xdist config-state leak (`cfg.carve.session` bleeding between fixtures). The implementer read
the actual failing-run log, found `session` was genuinely `"fresh"`, and the real cause was a
missing `role_default` route → `carve-no-route` short-circuit → `StopIteration`. It fixed the
real issue because the handoff framed the hypothesis as a candidate, not a mandate.

**How to apply.** Phrase controller diagnoses as "likely X — confirm against the failing-run
log." Value the implementer refusing a wrong hypothesis as the system working (the mirror of
L7: as the controller distrusts the agent's self-report, the agent must distrust the
controller's guess — evidence is the only authority both defer to).

**Compaction amplifies this (F018 P3c resume, 2026-07-25).** A hypothesis that passes through a
context compaction is *more* dangerous, not less: the summary restates it stripped of its original
"candidate" hedging, so the fresh context reads it as established fact with no memory of how
tentative it was — and there may be no implementer in the loop to push back, because the wrong
guess is now the controller's *own* prior claim. The P3c summary asserted the RED was a "worktree
!= cfg.root" design bug; on resume the very first act was to run the failing test, whose log showed
that assert PASSING and a different one (a stale empty-artifacts oracle) failing. Rule: on resuming
any handoff/summary that names a specific failure cause, **reproduce the failure from the log before
writing a single line of fix** — treat a summarized diagnosis exactly like an implementer's
self-report (L7): a hint about where to look, never a verified fact. (Two sibling false-greens the
same session reinforce the discipline: a "pure extraction" claim, trusted only after an AST walk
proved 133 identical string constants; and a diagnostic script that silently returned 0/0 — caught
because the output was read, not assumed green.)

## L9 — Land a big feature DARK behind one gate; enumerate its feature-on runaways in a pre-enablement checklist

**Rule.** A multi-package frozen-core feature should merge *inert* behind a single feature-gate
(default off), so each package lands byte-identical in production and is reviewed/gated in
isolation. But "inert today" hides latent feature-on defects that ship with each package — they
must be tracked explicitly, or someone flips the flag onto a half-built machine.

**Evidence (F018, 2026-07-25).** The whole long-running-carver series gated on
`cfg.carve.session == "project-persistent"` (default `"fresh"`); every package merged dark and
byte-identical. Reviews surfaced ≥4 feature-on runaways that were correct-to-defer but would
have been foot-guns if enabled early (infinite merge-feed re-loop with no ack cursor; unhandled
actions → TICK_ERROR storm; spurious NEEDS_OPERATOR on an idle carver; route-drift escalation
storm).

**How to apply.** Keep a **PRE-ENABLEMENT CHECKLIST** in the plan doc: every "inert feature-off"
gap a review defers becomes a numbered item that must clear before the flag is set anywhere. Add
an enablement guard that rejects/warns on turning the feature on until the checklist is clear, so
the partially-built feature cannot be enabled by mistake.

## L10 — A cheap model builds a subtle package when the scope is one layer, the precedents are named, and the frozen core is forbidden

**Rule.** Routing a package to the cheapest capable model is not a property of the model alone — it
is a property of how the package is *scoped and authored*. A subtle, frozen-core-*adjacent* package
is viable for a cheap implementer (deepseek-flash via reasonix, ~cents total) precisely when the
handoff (a) bounds the work to ONE layer (e.g. executor-only), (b) names the exact in-codebase
precedents each item mirrors (file:line), and (c) forbids the frozen core outright so "I think I
need to edit reconcile.py" becomes a mechanical BLOCKED, not a silent overreach. The controller's
independent re-gate + adversarial review remain the safety net regardless of who implements.

**Evidence (F018 P3d, 2026-07-25).** P3d (carver rotation, compaction→rotation fallback, ack
validation, route-storm debounce, enablement guard) *looked* like frozen-core work. But scouting
`reconcile.py` (the A1 ladder) and `carver_session.py` (the projector) BEFORE authoring revealed the
planner already plans the whole rotation/recovery/compaction ladder and the projector already folds
every status event — so the package was **daemon.py-only**, just emitting already-folded events at the
right executor moments. Authored that way (one layer, P3a/b/c patterns named, frozen core forbidden),
deepseek produced it in one shot: gate-green at 96/96 changed lines (100%), FORBID-clean, and the
adversarial review found only two feature-dark, self-healing refinements. The scope-scout is what
turned an "expensive Sonnet/Opus" package into a "cheap deepseek" one.

**How to apply.** Before choosing an implementer model, SCOUT the frozen core the package appears to
touch: is the planner/projector/state-machine already complete, leaving only executor wiring? If yes,
author it as a single-layer handoff with named precedents + an explicit frozen-core forbid, and route
it cheap. If the package genuinely needs planner/state changes, keep it on a stronger model (or split
the executor slice out to go cheap and reserve the stronger model for the frozen-core slice). Either
way the independent re-gate (L7) and review (L2) are non-negotiable — cheap implementation is only
safe because verification is model-independent.

## L11 — A `# pragma: no cover` on an `except`'s BODY does not cover the `except` CLAUSE; the diff-coverage floor dies to that off-by-one

**Rule.** coverage.py treats the `except Foo:` clause line as its own arc that executes only when an
exception is actually raised in the tried block. A `# pragma: no cover` placed on the *body* line
(`    return None  # pragma: no cover`) exempts only that line — the clause line above it stays an
uncovered *changed* line, so a diff-coverage gate with a 100% floor still FAILS at, e.g., 95%. More
broadly: reaching for a pragma on a *defensive* branch is a floor-dodge (L3). A defensive branch is
almost always cheaply reachable — a `monkeypatch` that makes the guarded call raise runs the whole
try/except for real — so prefer a test that raises over a pragma that hides.

**Evidence (F018 P4a, 2026-07-25).** A deepseek implementer added two `# pragma: no cover` markers to a
helper's `if not source_ids:` guard and `except Exception:` branch to reach the floor, then
self-reported "done" after committing. Its own gate had actually run RED three times (`exit status 1`,
`diff-coverage FAIL 95.2%`, uncovered `daemon.py:3749`): the pragma sat on the `return None` body while
the uncovered arc was the `except Exception:` clause one line above. The controller's independent
re-gate (L7) surfaced the real verdict the implementer's truncated tool-log had hidden. Fix: two
direct-call unit tests (empty input → None; `storage.iter_events` monkeypatched to raise → None), both
pragmas removed → genuine 100% (23/23). Note the changed-line *denominator* rose 21→23 as the pragmas
came off — replacing exclusions with real coverage is the opposite of a floor-dodge.

**How to apply.** (1) Treat every `# pragma: no cover` in a reviewed diff as a finding, not a given: is
the line genuinely unreachable, or merely annoying to reach? A defensive `except` is reachable via
`monkeypatch` — test it. (2) If a line truly must be excluded, the pragma goes on the *clause* line
(`except Exception:  # pragma: no cover`), not its body — and verify by re-reading the gate's
uncovered-line list, never by assuming the pragma landed where you meant. (3) An implementer's "gate
green / done" is never the merge signal (L7): re-run the gate yourself and read the `diff-coverage
OK/FAIL` line (L4). A self-reported green that a truncated log can't corroborate is a RED until your
own gate says otherwise.
