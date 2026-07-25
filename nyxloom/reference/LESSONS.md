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
