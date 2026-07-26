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
- **Before** writing a new trove lesson, grep this file + `DOCTRINE.md` for an existing
  canonical rule it duplicates or extends. If one exists, write the trove entry as
  *incident evidence* tagged `upstream: integrated (ref: L#)` — not as a fresh
  `proposed` rule. Skipping this check produces `proposed` trove entries that merely
  restate an already-shipped rule, and leaves stale `proposed` markers on lessons the
  reference file has since absorbed.
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

## L12 — Session reuse is an optimization; the router must own health-based rotation

**Rule.** A persistent agent session is valuable only while its retained context
improves the next bounded package. Do not ask either the implementer or an
independent reviewer to decide this from narrative. The orchestration layer
must collect session-health telemetry, apply explicit rotation tripwires, and
produce a factual fresh-session handoff when it rotates.

**Evidence (Topos global coverage healing, P97–P112, 2026-07-25).** A persistent
DeepSeek Flash implementation session was initially highly cache-efficient and
retained useful fixture/domain context. It later compacted a very large
transcript, repeatedly rewrote its prefix at substantial uncached cost, and
then exhibited stronger quality signals: repeated fixture/edit-context failures,
stale-worktree reads, an invented host virtualenv/runner rather than the declared
tester, and weakened exact assertions. A fresh Flash session removed neither
worktree nor runner drift by itself. The independent DeepSeek Pro reviewer was
excellent at finding incomplete coverage and weak tests, but it could not see
the implementer's cache cost or complete interaction history and itself made
line/arc interpretation errors. The controller caught the combination because
it owned the runner, immutable evidence, and task transcript metadata.

**How to apply.** Add a per-attempt session-health record and let the
dispatcher—not a model—transition it through `HEALTHY`, `DEGRADED`, and
`ROTATE_REQUIRED`. Record at least:

- cumulative and per-turn cached/uncached input, compaction/prefix-rewrite
  events, and estimated restart cost;
- consecutive failed attempts against the same mechanical oracle and whether the
  literal residual set actually shrank;
- runner/worktree contract violations, stale-path reads, and disposable side
  effects created outside the declared tester;
- repeated edits to the same test file, test weakening, empty/assertion-free
  bodies, or other hollow-test scanner findings;
- review disagreements whose exact-commit measurement disproves agent prose.

Rotate immediately on a safety violation (wrong worktree/runner, test
weakening, or unapproved side effect). Rotate after two attempts that miss the
same oracle without material residual reduction, or when the one-time
compaction/rewrite cost exceeds the measured cost of a concise fresh handoff.
The independent reviewer may emit a `SESSION_HEALTH` finding when it observes
surface symptoms, but it is a corroborator, not the owner: it cannot reliably
observe cache economics or every implementer interaction, and its own review
needs controller verification (L2, L7).

Provider-supported compaction or trimming is useful only as a controlled
package-boundary optimization. Preserve the exact base/HEAD, current literal
residual, accepted/deferred scope, runner command, oracle results, and clearly
labeled hypotheses; discard repetitive tool output and superseded prose. Never
trim merely by age inside an active causal package: old constraints may still
explain a current failure, and compaction can turn a tentative diagnosis into a
false fact. A resumed or compacted session must run the relocation preflight and
reproduce any claimed failure before editing (L8). If those checks fail, start a
fresh session from the generated handoff rather than compacting again.

## L13 — Base-guard every merge against a concurrently-advancing main; it is what makes disjoint-file parallel implementation safe

**Rule.** When more than one branch merges into a `main` that is *also* advancing
underneath you — a parallel implementation stream, a second controller, the daemon
dispatching concurrent tasks — a merge is safe only if the incoming branch and
everything `main` gained since that branch's merge-base touch **disjoint files**.
Verify it mechanically *before every merge*: the intersection of {files `main`
advanced since the merge-base} ∩ {files the branch touched} must be **empty**; then
`--no-ff` merge and post-merge re-verify. The *implementation* can be massively
parallel; the *merge* stays serial on one `main` regardless.

**Evidence (gate-adoption + F018 batch, 2026-07-25/26).** ~5 parallel implementation
agents merged package-by-package while `main` advanced ~7 times underneath them via a
disjoint Topos coverage stream. Each merge was preceded by a base-guard check (`comm
-12` of the two sorted `--name-only` diffs); every intersection came back empty, so
each `--no-ff` merge applied cleanly and its post-merge re-gate stayed green. The
base-guard — not luck, and not file-locking — is the *only* reason independent agents
could implement in parallel against a moving target without collision.

**How to apply.** Structurally the daemon must own this on its concurrent-merge path:
compute the merge-base intersection before publishing and refuse/re-queue on a
non-empty result rather than racing an `update-ref`. Any manual or controller-driven
merge reproduces it as a distinct pre-merge step — exactly as L4 makes gate→verdict→
merge distinct acts. Scope parallel packages onto disjoint files *up front* (the
carver's `scope.touch`) so the base-guard is expected-empty by construction; a
non-empty intersection is the signal to **serialize those two packages**, not to
merge-and-hope. This is the merge-discipline complement to the verification rules:
L7 says *re-verify the tree*, L13 says *verify the tree still composes with where
`main` went*.

## L14 — Evaluate an implementation route with sequential, oracle-bound packages; a benchmark is only a prior

**Rule.** Do not select a cheap implementation model from a public leaderboard, a
single impressive patch, or a parallel shoot-out. For a defined task class, test
one `(provider, model, effort, tool-wrapper)` route on **two sequential, bounded
packages** with the same controller contract: immutable base and literal coverage
residual, declared worktree and isolated runner, focused plus full gate, exact
coverage evidence, and independent review. The controller owns the oracle and
records the result. A route that violates the runner/worktree contract receives
no implementation credit even if it has not yet edited a file.

**Evidence (Topos global coverage healing, P97–P113, 2026-07-25/26).** A
persistent DeepSeek Flash Max implementation session could produce useful
test-only packages early, but degraded with stale-worktree reads, weak
assertions, and incorrect runners. A newly started Flash Max session was given a
much smaller, highly literal P113 handoff, preflighted the stated files, then
used host `python3` instead of the mandated `tester-unified` container; the
controller stopped it before an edit. This establishes that more descriptive
prompting and a fresh context alone do not make the route safe as an unattended
owner. A DeepSeek Pro Max reviewer independently found incomplete coverage and
test-quality problems, but required complete receipts and controller
re-measurement to avoid line/arc interpretation mistakes. The useful result is
not “Flash bad” or “Pro sufficient”: it is a measurable route profile.

**How to apply.** Record, per package: the exact handoff/base/residual; time and
cached/uncached token use; runner/worktree/scoping violations; focused and full
gate verdicts; literal residual closure; controller repair or takeover; reviewer
findings; and rework count. Rate a route as:

- **UNFIT** — any safety/runner/worktree breach, hollow-test attempt, or
  controller takeover needed to obtain a valid package;
- **CONDITIONAL** — two packages gate cleanly, but a bounded controller repair
  or reviewer finding remains; use only behind hard tool policy and close
  supervision;
- **QUALIFIED** — two clean packages, complete self-review/evidence, and only
  non-blocking independent-review findings; and
- **PREFERRED** — retains QUALIFIED behaviour across at least four packages and
  has the lowest measured total cost (model + controller + review + retries),
  not merely the lowest token price.

DeepSWE supplies a useful *per-effort coding prior*; Terminal-Bench measures
terminal/tool operations; neither can certify project-local testing discipline.
Use them to reject implausible routes before spending, then promote only routes
that pass this two-package, real-gate experiment. An independent reviewer is a
corroborator and adversary, never the route-health owner (L12) or the final gate
authority (L7).

## L15 — A prompt-only scope is not a tool boundary; capability-capsule an untrusted route before measuring it

**Rule.** A model route has not honored a frozen scope merely because its prompt
says so. Enforce that scope with the tool harness: default-deny its reads,
edits, shell, network, delegation, and external-directory access, then allow
only the exact context and output paths needed for that package. Keep gate
execution controller-owned until the runner can be allowlisted as one bounded,
auditable capability. A route that escapes the declared context before editing
is an implementation-route failure, not a harmless exploration.

**Evidence (Topos low-cost route trial, 2026-07-26).** Poolside Laguna XS read
an unlisted stale global coverage report immediately after a no-edit probe.
Laguna M.1 subsequently read an entire 1,186-line test module despite being
given a narrow test subsection; it also encountered a free-tier limit. Neither
worktree was changed, but both events invalidate a claim that the prompt alone
contained the task. OpenCode's `--auto` mode approves requests unless an
explicit deny overrides them, and its client retried the transient provider
refusal rapidly. The controller stopped both exact session processes and
recorded them as UNFIT rather than converting unsafe exploration into model
credit.

**How to apply.** Build a small controller-attested context capsule for a
bounded test package (literal residual, relevant source excerpt, fixture
contract, and expected assertions). Run a per-worktree permission config with
an ordered default-deny rule and a short path allowlist; leave its configuration
untracked and remove it with the disposable worktree. For a cheap drafting
probe, allow only the nominated test-file edit and let the controller execute
the isolated test/gate. That can reject a weak route cheaply, but cannot qualify
an autonomous implementer: promotion still requires a second package with
correct self-review and auditable runner evidence. Treat provider retries as a
controller concern—bound the client process, preserve its session id, and
apply an explicit 30/60/120-second resume policy rather than accepting hidden
rapid retries.

The capsule is itself part of the oracle and must be preflighted by the
controller before a route is scored: execute its proposed seam/fixture matrix
against the isolated target, list every required constructor field, and verify
that a mocked boundary cannot accidentally intercept a downstream dependency.
If that preflight is wrong, invalidate the package rather than charging the
model for faithfully implementing an impossible instruction.

## L16 — A project gate is a composition of four layers; nyxloom requires only the interface layer, never mandates the infra layer

**Rule.** "The gate catches real bugs" is not one property of one component — it is
the sum of four separable layers: (1) **infra**, a runtime-faithful *isolated*
environment, project-owned, expressed entirely inside the gate's `argv`; (2)
**toolkit**, an opt-in, ecosystem-specific completeness floor (changed-line
coverage, mutation) that nyxloom may ship but never requires; (3) **content**, the
project's own invariant/behavioral tests, which no amount of infra or toolkit can
substitute for; (4) **discipline**, GATE→VERDICT→MERGE as separate acts (L4) with
SOLO serialization across concurrent gates. nyxloom's contract with a consumer
project is layer 1 only: a `[gates.*]` command that runs isolated at a commit and
exits non-zero on failure with nothing masking it — the `{worktree}` placeholder is
the sole integration seam. Layers 2-4 are the daemon's own orchestration and an
optional menu, never a requirement on the project's infra choice.

**Evidence (factory-hardening A/F/G, gate-adoption GA1-GA4, 2026-07 through
2026-07-26).** The two real catches from package F were a config-schema invariant
test and a coverage floor — neither is infra. dstdns (docker `test-runner`, no
coverage floor) and nyxloom (docker `tester-unified`, with a coverage floor) run
under one daemon today with wholly unrelated gate commands and no shared
infrastructure assumption; onboarding's gate-scaffold (GA3) offers the toolkit
without ever hardcoding docker, pytest, or any specific runner.

**How to apply.** When onboarding or reviewing a new consumer project's gate,
verify only that `[gates.*].argv` is isolated, deterministic, and honestly
non-zero-on-failure — never require a specific test runner, container base, or
language toolchain. Offer `coverage_gate`/`mutation_gate`-equivalent tooling as an
opt-in menu item (the *interface* generalizes past Python — e.g. `cargo
llvm-cov`/`nyc` for other ecosystems), gated behind the project's own choice to
declare `asserts=[...]` rigor. A project that declares no toolkit assert is not
broken; a project whose gate argv is untrustworthy (`["true"]`) is what the
coverage-canary/gate-verify machinery (GA1/GA2b/GA4) exists to catch — that is a
*content* problem to surface, not a reason to mandate infra.

## L17 — A parallel gate's coverage that drops fork-child lines is exposing hollow tests, not miscounting

**Rule.** When a coverage-floor gate moves from serial execution to `pytest -n
auto`/xdist, the coverage TOOL must also move from `coverage run -m pytest` to
`pytest-cov` (the only way to measure xdist's execnet worker processes — `coverage
run` traces only the parent, so under `-n auto` it measures ~nothing and a
changed-line floor would false-FAIL every package). That tool switch can then
surface lines that were serial-covered but are xdist-missed. The correct reading of
that gap is never "the parallel measurement is broken, reconfigure it to recapture
the coverage" — it is "those lines had no deterministic test; an integration test
happened to fork a child that incidentally ran them." xdist-`pytest-cov` is *more*
honest, not less; the fix is writing the missing deterministic unit test (L1), never
re-widening the measurement to paper over hollow coverage.

**Evidence (factory-hardening G, 2026-07).** Moving nyxloom's own gate to `pytest -n
auto` + `pytest-cov` surfaced 6 `render.py` liveness lines that a pre-ship
coverage-parity check (serial `coverage run` vs. xdist `pytest-cov`, per-file
executed-line superset) flagged as serial-covered-but-xdist-missed. Mechanism:
`coverage run` follows its tracer into a test's real `os.fork()` child and writes
the child's data to the shared coverage file; `pytest-cov` under xdist combines only
per-WORKER data and drops a worker's forked grandchild's coverage entirely. The
lines were real gaps in deterministic test coverage, not a measurement regression.

**How to apply.** Before trusting any newly-parallelized coverage gate (in nyxloom
itself or a consumer project adopting one via the onboarding toolkit), run a
pre-ship parity check: per-file executed-line superset, serial vs. parallel. The
only dangerous direction is serial-covered-but-parallel-missed (a future false-FAIL
once the floor tightens); parallel-covers-more is harmless and needs no action.
Separate intrinsic suite nondeterminism from a real parallel gap by running the
SERIAL gate TWICE first — lines that flake serial-vs-serial (timing/poll races) are
not the parallel runner's fault; only serial-STABLE-but-parallel-missed lines are a
genuine gap. Put parallelism in the gate's own `argv`, not a global pytest
`addopts`, so single-file tool invocations (e.g. a mutation gate's per-mutant runs)
don't pay xdist startup overhead they don't need.

**CLI-path corollary (Claude Code, Topos P121–P123).** Capability grammar is
part of the route, so preflight it too. On this host, `--bare` bypassed the
normal authenticated credential integration; a normal `dontAsk` invocation
was authenticated. Path-scoped `Read` worked only against the real absolute
worktree path (not a convenient virtual alias), while path-scoped `Edit(...)`
was rejected by the CLI. The safe fallback was not broad shell access: retain
the `Read Edit` tool ceiling, permit `Edit` only after the handoff limits it to
one file, and let the controller audit the exact diff. A clean BLOCKED caused
by a stale resumed handoff name or a denied capability is a session/harness
signal; repair the capsule or rotate the session before scoring model quality.
